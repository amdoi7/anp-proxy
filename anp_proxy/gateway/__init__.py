"""Gateway component for ANP Proxy - Refactored with Single Responsibility Principle."""

from .connection import ConnectInfo, ConnectionManager
from .message import MessageHandler
from .registry import ServiceRegistryManager
from .routing import RequestRouter
from .server import ANPGateway, create_app, create_gateway


# 向后兼容 - 为旧代码提供 GatewayServer
class GatewayServer:
    """向后兼容的 Gateway 服务器包装器"""

    def __init__(self, config=None):
        # 传递认证配置给网关
        auth_config = getattr(config, "auth", None) if config else None
        self.gateway = create_gateway(auth_config=auth_config)
        self.app = create_app(self.gateway)
        self.config = config

    async def start(self):
        """启动网关"""
        await self.gateway.start()

    async def stop(self):
        """停止网关"""
        await self.gateway.stop()

    async def run(self):
        """运行网关服务器 - 主要入口点"""
        import asyncio

        import uvicorn
        import websockets

        from anp_proxy.common.config import get_default_bind_host
        from anp_proxy.common.log_base import get_logger

        logger = get_logger(__name__)

        # 从配置中获取服务器参数
        http_host = getattr(self.config, "host", get_default_bind_host())
        http_port = getattr(self.config, "port", 8089)
        wss_host = getattr(self.config, "wss_host", get_default_bind_host())
        wss_port = getattr(self.config, "wss_port", 8789)

        logger.info(f"Starting ANP Gateway HTTP server on {http_host}:{http_port}")
        logger.info(f"Starting ANP Gateway WebSocket server on {wss_host}:{wss_port}")

        # WebSocket 处理函数
        async def websocket_handler(websocket, path):
            try:
                # 直接使用原始 websocket 处理连接
                await self.gateway.handle_raw_websocket_connection(websocket, path)
            except Exception as e:
                logger.error(f"WebSocket handler error: {e}")

        # 创建 HTTP 服务器配置
        http_config = uvicorn.Config(
            app=self.app,
            host=http_host,
            port=http_port,
            log_level="info",
            access_log=True,
        )
        http_server = uvicorn.Server(http_config)

        try:
            # 同时启动 HTTP 和 WebSocket 服务器
            await asyncio.gather(
                http_server.serve(),
                websockets.serve(websocket_handler, wss_host, wss_port),
            )
        except Exception as e:
            logger.error(f"Gateway server error: {e}")
            raise
        finally:
            await self.stop()

    def get_app(self):
        """获取 FastAPI 应用"""
        return self.app


__all__ = [
    "ANPGateway",
    "ConnectInfo",
    "ConnectionManager",
    "GatewayServer",  # 向后兼容
    "MessageHandler",
    "RequestRouter",
    "ServiceRegistryManager",
    "create_gateway",
    "create_app",
]
