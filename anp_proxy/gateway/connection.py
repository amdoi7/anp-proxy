"""
连接信息模块 - 避免循环导入
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket


class ConnectionState(Enum):
    """Connection state enumeration."""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    DISCONNECTED = "disconnected"


@dataclass
class ConnectInfo:
    """连接信息"""

    connection_id: str
    websocket: "WebSocket | None" = None
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
        from ..common.log_base import logger

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
