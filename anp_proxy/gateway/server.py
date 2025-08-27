"""
网关核心 - 集成连接管理，提供统一接口
"""

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from starlette.responses import JSONResponse, Response

from ..anp_sdk.anp_auth.did_wba_verifier import DidWbaVerifier, DidWbaVerifierConfig
from ..common.log_base import get_logger
from ..common.utils import get_advertised_services
from .middleware import create_default_middleware_stack
from .request_mapper import RequestMapper
from .response_handler import ResponseHandler
from .routing import PathRouter
from .websocket_handler import WebSocketHandler

logger = get_logger(__name__)


class ConnectionState(Enum):
    """连接状态"""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    DISCONNECTED = "disconnected"


@dataclass
class ConnectInfo:
    """连接信息"""

    connection_id: str
    websocket: WebSocket | None = None
    state: ConnectionState = ConnectionState.CONNECTING
    authenticated: bool = False
    did: str | None = None
    path: str | None = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    last_websocket_activity: float = field(
        default_factory=time.time
    )  # 区分WebSocket活动
    _cleaning_up: bool = False  # 添加清理状态标志
    _websocket_closed: bool = False  # 添加WebSocket关闭状态标志

    @property
    def is_healthy(self) -> bool:
        """连接是否健康"""
        from ..common.log_base import get_logger

        logger = get_logger(__name__)

        if self.state != ConnectionState.AUTHENTICATED:
            logger.info(
                f"Connection {self.connection_id} not healthy: state={self.state.value}"
            )
            return False

        if self.websocket is None:
            logger.info(
                f"Connection {self.connection_id} not healthy: websocket is None"
            )
            return False

        logger.info(f"Connection {self.connection_id} is healthy (simplified check)")
        return True

    def update_activity(self) -> None:
        """更新活动时间（通用）"""
        self.last_activity = time.time()

    def update_websocket_activity(self) -> None:
        """更新WebSocket活动时间（只有WebSocket消息才调用）"""
        current_time = time.time()
        self.last_activity = current_time
        self.last_websocket_activity = current_time

    def update_ping(self) -> None:
        """更新心跳时间"""
        self.update_websocket_activity()

    def start_cleanup(self) -> bool:
        """开始清理，返回是否成功获取清理锁"""
        if self._cleaning_up:
            return False
        self._cleaning_up = True
        return True

    @property
    def is_cleaning_up(self) -> bool:
        """是否正在清理"""
        return self._cleaning_up

    def mark_websocket_closed(self) -> None:
        """标记WebSocket已关闭"""
        self._websocket_closed = True

    @property
    def is_websocket_closed(self) -> bool:
        """WebSocket是否已关闭"""
        return self._websocket_closed


class ANPGateway:
    """ANP网关核心 - 集成连接管理功能"""

    def __init__(
        self,
        ping_interval: float = 30.0,
        connection_timeout: float = 300.0,
        response_timeout: float = 30.0,
        heartbeat_interval: float = 60.0,
        auth_config=None,
    ):
        # 连接管理相关参数
        self.ping_interval = ping_interval
        self.connection_timeout = connection_timeout

        # 连接存储
        self.connections: dict[str, ConnectInfo] = {}
        self._path_conn_info_dict: dict[
            str, ConnectInfo
        ] = {}  # path -> ConnectInfo mapping

        # 认证配置
        self.auth_config = auth_config

        # 初始化DID验证器
        try:
            # 从 auth_config 中获取配置参数
            verifier_config = DidWbaVerifierConfig()
            if auth_config:
                if (
                    hasattr(auth_config, "resolver_base_url")
                    and auth_config.resolver_base_url
                ):
                    # 设置 DID 解析器的基础 URL
                    import os

                    os.environ["DID_RESOLVER_BASE_URL"] = auth_config.resolver_base_url
                    logger.info(
                        f"Set DID resolver base URL: {auth_config.resolver_base_url}"
                    )

                if hasattr(auth_config, "nonce_window_seconds"):
                    verifier_config.nonce_expiration_minutes = (
                        auth_config.nonce_window_seconds // 60
                    )
                    verifier_config.timestamp_expiration_minutes = (
                        auth_config.nonce_window_seconds - 60
                    ) // 60
                    logger.info(
                        f"Set nonce window: {auth_config.nonce_window_seconds}s"
                    )

            self.did_verifier = DidWbaVerifier(verifier_config)
            logger.info("DID verifier initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize DID verifier: {e}")
            raise RuntimeError(f"DID verifier initialization failed: {e}")

        # 初始化各个管理器
        self.request_router = PathRouter()
        self.request_mapper = RequestMapper()
        self.response_handler = ResponseHandler(response_timeout)
        self.websocket_handler = WebSocketHandler(self)

        self._running = False
        self._health_check_task: asyncio.Task | None = None

        logger.info("ANPGateway initialized with integrated connection management")

    async def start(self) -> None:
        """启动网关"""
        if self._running:
            return

        self._running = True
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        await self.response_handler.start()

        logger.info("ANPGateway started")

    async def stop(self) -> None:
        """停止网关"""
        if not self._running:
            return

        self._running = False

        if self._health_check_task:
            self._health_check_task.cancel()

        await self._close_all_connections()
        await self.response_handler.stop()

        # 清理服务注册相关数据
        self._path_conn_info_dict.clear()

        logger.info("ANPGateway stopped")

    # === 连接管理方法 ===

    async def register_and_add_connection(
        self, connection_id: str, websocket, did: str
    ) -> ConnectInfo | None:
        """注册并添加连接 - 直接使用已认证的 DID"""
        try:
            # 1. 验证 DID 格式
            if not did or not did.startswith("did:"):
                logger.warning(
                    f"Service registration failed - Invalid DID format: {did}"
                )
                return None

            logger.info(f"Registering connection with authenticated DID: {did}")

            # 2. 通过DID查询服务路径
            services = get_advertised_services(did)

            # 3. 创建连接信息
            connection = ConnectInfo(
                connection_id=connection_id,
                websocket=websocket,
                state=ConnectionState.AUTHENTICATED,
                authenticated=True,
                did=did,
            )
            self.connections[connection_id] = connection

            # 4. 更新路径映射
            for path in services:
                self._path_conn_info_dict[path] = connection
                # 同时添加到 PathRouter 中
                self.request_router.add_route(path, connection)
                logger.debug(f"Mapped path {path} to connection {connection_id}")

            logger.info(
                "Service registered successfully",
                connection_id=connection_id,
                did=did,
                paths=services,
            )

            return connection

        except Exception as e:
            logger.error(
                f"Service registration error: {e}", connection_id=connection_id
            )
            return None

    def update_heartbeat(self, connection_id: str) -> None:
        """更新心跳"""
        connection = self.connections.get(connection_id)
        if connection:
            connection.update_activity()

    async def remove_connection(self, connection_id: str) -> None:
        """移除连接并清理服务注册（原子性操作）"""
        # 获取连接对象
        connection = self.connections.get(connection_id)
        if not connection:
            logger.debug(f"Connection {connection_id} already removed")
            return

        # 尝试获取清理锁，防止重复清理
        if not connection.start_cleanup():
            logger.debug(f"Connection {connection_id} cleanup already in progress")
            return

        try:
            # 1. 更新连接状态为断开
            connection.state = ConnectionState.DISCONNECTED

            # 2. 从连接池中移除（防止新的消息处理）
            self.connections.pop(connection_id, None)

            # 3. 清理服务注册和路由
            self.unregister_service(connection_id)

            # 4. 安全关闭WebSocket连接
            await self._safe_close_websocket(connection)

            logger.info(f"Connection removed: {connection_id}")

        except Exception as e:
            logger.error(f"Error during connection cleanup for {connection_id}: {e}")
            # 确保连接被移除，即使清理过程中出现错误
            self.connections.pop(connection_id, None)

    async def _safe_close_websocket(self, connection: ConnectInfo) -> None:
        """安全关闭WebSocket连接"""
        if connection.is_websocket_closed or connection.websocket is None:
            logger.debug(
                f"WebSocket for {connection.connection_id} already closed or None"
            )
            return

        try:
            # 标记WebSocket已关闭
            connection.mark_websocket_closed()

            # 尝试关闭WebSocket
            await connection.websocket.close()
            logger.debug(
                f"WebSocket closed successfully for {connection.connection_id}"
            )

        except Exception as e:
            error_msg = str(e).lower()
            # 检查是否为预期的关闭错误
            if any(
                expected_error in error_msg
                for expected_error in [
                    "websocket.close",
                    "connection is closed",
                    "disconnect message has been received",
                    "unexpected asgi message",
                ]
            ):
                logger.debug(
                    f"Expected WebSocket close error for {connection.connection_id}: {e}"
                )
            else:
                logger.warning(
                    f"Unexpected error closing websocket for {connection.connection_id}: {e}"
                )

    def get_connection_for_path(self, path: str) -> ConnectInfo | None:
        """根据路径获取连接信息"""
        # 使用路由器的前缀匹配逻辑而不是精确匹配
        return self.request_router.find_route(path)

    def get_connection_paths(self, connection_id: str) -> list[str]:
        """获取连接的所有服务路径"""
        service_paths = []
        for path, conn_info in self._path_conn_info_dict.items():
            if conn_info.connection_id == connection_id:
                service_paths.append(path)
        return service_paths

    def unregister_service(self, connection_id: str) -> None:
        """注销服务"""
        # 获取该连接的所有服务路径
        service_paths = self.get_connection_paths(connection_id)

        # 清理路径映射
        for path in service_paths:
            self._path_conn_info_dict.pop(path, None)
            # 同时从 PathRouter 中移除
            self.request_router.remove_route(path)
            logger.debug(f"Unmapped path {path} from connection {connection_id}")

        logger.info(f"Service unregistered: {connection_id}, paths: {service_paths}")

    async def _close_all_connections(self) -> None:
        """关闭所有连接"""
        for connection_id in list(self.connections.keys()):
            await self.remove_connection(connection_id)

    async def _health_check_loop(self) -> None:
        """健康检查循环"""
        while self._running:
            try:
                await asyncio.sleep(self.ping_interval)
                await self._check_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")

    async def _check_connections(self) -> None:
        """检查连接健康状态"""
        current_time = time.time()
        stale_connections = []

        for connection_id, connection in self.connections.items():
            # 跳过正在清理的连接
            if connection.is_cleaning_up:
                continue

            # 跳过已关闭的WebSocket连接
            if connection.is_websocket_closed:
                stale_connections.append(connection_id)
                continue

            # 检查已认证连接的WebSocket活动超时
            if (
                connection.state == ConnectionState.AUTHENTICATED
                and connection.websocket is not None
                and current_time - connection.last_websocket_activity
                > self.connection_timeout
            ):
                stale_connections.append(connection_id)
                logger.info(
                    f"Connection {connection_id} timed out - last activity: {current_time - connection.last_websocket_activity:.1f}s ago"
                )

        # 移除陈旧连接
        for connection_id in stale_connections:
            logger.warning(f"Removing stale connection: {connection_id}")
            await self.remove_connection(connection_id)

    def get_connection_stats(self) -> dict:
        """获取连接统计信息"""
        stats = {
            "total_connections": len(self.connections),
            "authenticated_connections": 0,
            "websocket_closed_connections": 0,
            "healthy_connections": 0,
        }

        for connection in self.connections.values():
            if connection.authenticated:
                stats["authenticated_connections"] += 1
            if connection.is_websocket_closed:
                stats["websocket_closed_connections"] += 1
            if connection.is_healthy:
                stats["healthy_connections"] += 1

        return stats

    # === WebSocket处理方法 ===

    async def handle_websocket_connection(
        self,
        websocket: WebSocket,
        connection_id: str | None = None,
        auth_header: str = "",
    ) -> None:
        """处理WebSocket连接"""
        await self.websocket_handler.handle_connection(
            websocket, connection_id, auth_header
        )

    # === HTTP请求处理方法 ===

    async def handle_http_request(self, request: Request) -> Response:
        """处理HTTP请求"""
        if not self._running:
            return JSONResponse({"error": "Gateway not running"}, status_code=503)

        # 从中间件获取连接信息
        websocket = getattr(request.state, "websocket", None)
        conn_info = getattr(request.state, "conn_info", None)

        if not websocket or not conn_info:
            return JSONResponse({"error": "No route found"}, status_code=404)

        # 检查连接是否正在清理中，如果是则拒绝请求
        if conn_info.is_cleaning_up:
            return JSONResponse(
                {"error": "Service unavailable - connection cleaning up"},
                status_code=503,
            )

        try:
            # 1. 处理HTTP请求并创建ANPX消息
            http_message = await self.request_mapper.process_http_request(request)
            anpx_message = self.request_mapper.create_anpx_message(http_message)

            # 2. 转发ANPX消息到WebSocket
            forward_success = await self.response_handler.forward_to_websocket(
                websocket, anpx_message
            )
            if not forward_success:
                return self.response_handler.create_error_response(
                    http_message.message_id, "Failed to forward request", 502
                )

            # 3. 等待响应
            try:
                response = await self.response_handler.wait_for_response(
                    http_message.message_id
                )

                # 4. 直接返回响应（现在wait_for_response返回的是Response对象）
                return response

            except TimeoutError:
                return self.response_handler.create_error_response(
                    http_message.message_id, "Response timeout", 504
                )

        except Exception as e:
            logger.error(f"Error handling HTTP request: {e}")
            return JSONResponse({"error": "Internal server error"}, status_code=500)

    # === 统计和监控方法 ===

    def get_gateway_stats(self) -> dict[str, Any]:
        """获取网关统计信息"""
        # 使用新的连接统计方法
        connection_stats = self.get_connection_stats()

        return {
            "running": self._running,
            "connections": connection_stats,
            "routing": self.request_router.get_stats(),
            "responses": self.response_handler.get_handler_stats(),
        }

    async def health_check(self) -> dict[str, Any]:
        """健康检查"""
        stats = self.get_gateway_stats()
        connection_stats = stats["connections"]

        # 检查网关是否运行且有健康的连接
        is_healthy = self._running and connection_stats["healthy_connections"] > 0

        return {
            "status": "healthy" if is_healthy else "degraded",
            "timestamp": asyncio.get_event_loop().time(),
            "details": stats,
        }


def create_gateway(auth_config=None) -> ANPGateway:
    """创建网关实例"""
    return ANPGateway(auth_config=auth_config)


def create_app(gateway: ANPGateway) -> FastAPI:
    """创建FastAPI应用"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await gateway.start()
        yield
        await gateway.stop()

    app = FastAPI(lifespan=lifespan)

    # 添加中间件件
    middleware_stack = create_default_middleware_stack(
        debug=False, connection_manager=gateway
    )

    # 按照正确的顺序添加中间件（从后往前）
    for middleware in reversed(middleware_stack):
        app.middleware("http")(middleware)

    @app.get("/health")
    async def health():
        return await gateway.health_check()

    @app.get("/stats")
    async def stats():
        return gateway.get_gateway_stats()

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        # 提取认证头
        auth_header = websocket.headers.get("authorization", "")
        await gateway.handle_websocket_connection(websocket, auth_header=auth_header)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def proxy_request(request: Request, path: str):
        return await gateway.handle_http_request(request)

    return app
