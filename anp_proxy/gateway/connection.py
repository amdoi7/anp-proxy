"""
WebSocket 连接管理模块 - 单一职责：管理与 Octopus Receiver 的连接
- WebSocket 连接的建立、维护、断开
- 连接健康状态监控
- 连接池管理
"""

import asyncio
import time
import weakref
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from websockets.server import WebSocketServerProtocol

try:
    from ..common.log_base import get_logger
except ImportError:
    from anp_proxy.common.log_base import get_logger

logger = get_logger(__name__)


class ConnectionState(Enum):
    """连接状态枚举"""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass(slots=True)
class ConnectInfo:
    """简化的连接信息 - 专注于连接管理，使用 slots 优化内存"""

    connection_id: str
    websocket: WebSocketServerProtocol | None = None
    state: ConnectionState = ConnectionState.CONNECTING

    # 认证信息
    authenticated: bool = False
    did: str | None = None

    # 时间戳
    created_at: float = field(default_factory=time.time)
    last_ping: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    # 服务信息
    advertised_paths: set[str] = field(default_factory=set)

    def __post_init__(self):
        """初始化后处理"""
        if isinstance(self.advertised_paths, list):
            self.advertised_paths = set(self.advertised_paths)

    @property
    def is_healthy(self) -> bool:
        """连接是否健康"""
        return (
            self.state == ConnectionState.AUTHENTICATED
            and self.websocket is not None
            and not self.websocket.closed
        )

    @property
    def age(self) -> float:
        """连接存活时间"""
        return time.time() - self.created_at

    def update_activity(self) -> None:
        """更新活动时间"""
        self.last_activity = time.time()

    def update_ping(self) -> None:
        """更新心跳时间"""
        self.last_ping = time.time()
        self.update_activity()

    def __str__(self) -> str:
        return f"Connection(id={self.connection_id}, state={self.state.value}, healthy={self.is_healthy})"


class ConnectionLimitExceeded(Exception):
    """连接数超过限制异常"""

    pass


class ConnectionManager:
    """WebSocket 连接管理器 - 单一职责：管理所有连接的生命周期"""

    def __init__(
        self,
        ping_interval: float = 30.0,
        connection_timeout: float = 300.0,
        max_connections: int = 100,
    ):
        self.ping_interval = ping_interval
        self.connection_timeout = connection_timeout
        self.max_connections = max_connections

        # 连接存储
        self._connections: dict[str, ConnectInfo] = {}
        self._websocket_to_id: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

        # 健康检查任务
        self._health_check_task: asyncio.Task | None = None
        self._running = False

        logger.info(
            "ConnectionManager initialized",
            ping_interval=ping_interval,
            connection_timeout=connection_timeout,
            max_connections=max_connections,
        )

    async def start(self) -> None:
        """启动连接管理器"""
        if self._running:
            return

        self._running = True
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        logger.info("ConnectionManager started")

    async def stop(self) -> None:
        """停止连接管理器"""
        self._running = False

        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # 关闭所有连接
        await self._close_all_connections()
        logger.info("ConnectionManager stopped")

    async def add_connection(
        self,
        connection_id: str,
        websocket: WebSocketServerProtocol,
        did: str | None = None,
    ) -> ConnectInfo:
        """添加新连接"""
        # 检查连接数限制
        if len(self._connections) >= self.max_connections:
            logger.warning(
                "Connection limit exceeded",
                current_connections=len(self._connections),
                max_connections=self.max_connections,
                rejected_connection=connection_id,
            )
            raise ConnectionLimitExceeded(
                f"Maximum {self.max_connections} connections exceeded. "
                f"Current: {len(self._connections)}"
            )

        connection = ConnectInfo(
            connection_id=connection_id,
            websocket=websocket,
            state=ConnectionState.CONNECTED,
            did=did,
        )

        self._connections[connection_id] = connection
        self._websocket_to_id[websocket] = connection_id

        logger.info(
            "Connection added",
            connection_id=connection_id,
            did=did,
            total_connections=len(self._connections),
            max_connections=self.max_connections,
        )

        return connection

    async def add_raw_connection(
        self,
        connection_id: str,
        websocket,  # 原始 websocket 对象
        did: str | None = None,
    ) -> ConnectInfo:
        """添加新的原始 WebSocket 连接"""
        # 检查连接数限制
        if len(self._connections) >= self.max_connections:
            logger.warning(
                "Raw connection limit exceeded",
                current_connections=len(self._connections),
                max_connections=self.max_connections,
                rejected_connection=connection_id,
            )
            raise ConnectionLimitExceeded(
                f"Maximum {self.max_connections} connections exceeded. "
                f"Current: {len(self._connections)}"
            )

        connection = ConnectInfo(
            connection_id=connection_id,
            websocket=websocket,  # 直接使用原始 websocket
            state=ConnectionState.CONNECTED,
            did=did,
        )

        self._connections[connection_id] = connection
        # 注意：原始 websocket 对象也可以用作 key
        self._websocket_to_id[websocket] = connection_id

        logger.info(
            "Raw connection added",
            connection_id=connection_id,
            did=did,
            total_connections=len(self._connections),
            max_connections=self.max_connections,
        )

        return connection

    async def remove_connection(self, connection_id: str) -> ConnectInfo | None:
        """移除连接"""
        connection = self._connections.pop(connection_id, None)
        if not connection:
            return None

        # 关闭 WebSocket
        if connection.websocket and not connection.websocket.closed:
            await connection.websocket.close()

        connection.state = ConnectionState.DISCONNECTED

        logger.info(
            "Connection removed",
            connection_id=connection_id,
            total_connections=len(self._connections),
        )

        return connection

    def get_connection(self, connection_id: str) -> ConnectInfo | None:
        """获取连接"""
        return self._connections.get(connection_id)

    def get_connection_by_websocket(
        self, websocket: WebSocketServerProtocol
    ) -> ConnectInfo | None:
        """通过 WebSocket 获取连接"""
        connection_id = self._websocket_to_id.get(websocket)
        if connection_id:
            return self._connections.get(connection_id)
        return None

    def authenticate_connection(
        self, connection_id: str, did: str, advertised_paths: list[str]
    ) -> bool:
        """认证连接"""
        connection = self._connections.get(connection_id)
        if not connection:
            return False

        connection.authenticated = True
        connection.did = did
        connection.state = ConnectionState.AUTHENTICATED
        connection.advertised_paths = set(advertised_paths)
        connection.update_activity()

        logger.info(
            "Connection authenticated",
            connection_id=connection_id,
            did=did,
            paths=len(advertised_paths),
        )

        return True

    def get_healthy_connections(self) -> list[ConnectInfo]:
        """获取所有健康连接"""
        return [conn for conn in self._connections.values() if conn.is_healthy]

    def get_connections_by_path(self, path: str) -> list[ConnectInfo]:
        """根据路径获取连接"""
        return [
            conn
            for conn in self._connections.values()
            if conn.is_healthy and path in conn.advertised_paths
        ]

    async def ping_connection(self, connection_id: str) -> bool:
        """ping 特定连接"""
        connection = self._connections.get(connection_id)
        if not connection or not connection.websocket:
            return False

        try:
            await connection.websocket.ping()
            connection.update_ping()
            return True
        except Exception as e:
            logger.warning(f"Ping failed for connection {connection_id}: {e}")
            connection.state = ConnectionState.ERROR
            return False

    async def _health_check_loop(self) -> None:
        """健康检查主循环"""
        while self._running:
            try:
                await self._perform_health_check()
                await asyncio.sleep(self.ping_interval)
            except Exception as e:
                logger.error(f"Health check error: {e}")
                await asyncio.sleep(5.0)

    async def _perform_health_check(self) -> None:
        """执行健康检查"""
        now = time.time()
        unhealthy_connections = []

        for connection_id, connection in self._connections.items():
            # 检查连接超时
            if now - connection.last_activity > self.connection_timeout:
                unhealthy_connections.append(connection_id)
                continue

            # 检查是否需要 ping
            if now - connection.last_ping > self.ping_interval:
                ping_success = await self.ping_connection(connection_id)
                if not ping_success:
                    unhealthy_connections.append(connection_id)

        # 移除不健康的连接
        for connection_id in unhealthy_connections:
            await self.remove_connection(connection_id)
            logger.warning(f"Removed unhealthy connection: {connection_id}")

    async def _close_all_connections(self) -> None:
        """关闭所有连接"""
        for connection_id in list(self._connections.keys()):
            await self.remove_connection(connection_id)

    def get_stats(self) -> dict[str, Any]:
        """获取连接统计"""
        total = len(self._connections)
        healthy = len(self.get_healthy_connections())

        state_counts = {}
        for connection in self._connections.values():
            state = connection.state.value
            state_counts[state] = state_counts.get(state, 0) + 1

        return {
            "total_connections": total,
            "healthy_connections": healthy,
            "unhealthy_connections": total - healthy,
            "max_connections": self.max_connections,
            "connection_utilization": f"{(total / self.max_connections * 100):.2f}%",
            "state_distribution": state_counts,
            "average_age": sum(conn.age for conn in self._connections.values())
            / max(total, 1),
        }
