"""Gateway component for ANP Proxy - Refactored with Single Responsibility Principle."""

from .request_mapper import RequestMapper
from .response_handler import ResponseHandler
from .routing import PathRouter
from .server import ANPGateway, ConnectInfo, create_app, create_gateway


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
                # WebSocket连接必须通过认证，不再支持原始连接
                logger.warning(f"Raw WebSocket connection rejected: {path}")
                await websocket.close(code=4001, reason="Authentication required")
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
    "GatewayServer",  # 向后兼容
    "RequestMapper",
    "ResponseHandler",
    "PathRouter",
    "create_gateway",
    "create_app",
]
