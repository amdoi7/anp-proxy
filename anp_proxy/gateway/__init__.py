"""
ANP Gateway - 统一网关接口
"""

from .connection import ConnectInfo
from .routing import PathRouter
from .server import ANPGateway, create_app, create_gateway


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

        import uvicorn

        from anp_proxy.common.config import get_default_bind_host
        from anp_proxy.common.log_base import logger

        # 从配置中获取服务器参数
        http_host = getattr(self.config, "host", get_default_bind_host())
        http_port = getattr(self.config, "port", 8089)

        logger.info(f"Starting ANP Gateway server on {http_host}:{http_port}")
        logger.info("WebSocket support is integrated via FastAPI /ws endpoint")

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
            # 启动 FastAPI 服务器（包含 WebSocket 支持）
            await http_server.serve()
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
