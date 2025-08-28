#!/usr/bin/env python3
"""
ANP Proxy Application Core

This module contains the main application logic for ANP Proxy,
providing HTTP over WebSocket tunneling capabilities.
"""

from typing import Any

from anp_proxy.common.config import ANPProxyConfig
from anp_proxy.common.log_base import logger, setup_logging
from anp_proxy.gateway import GatewayServer


class ANPProxyApp:
    """ANP Proxy Application - Core application class."""

    def __init__(self, config: ANPProxyConfig) -> None:
        """Initialize ANP Proxy application."""
        self.config = config
        self.gateway_server: GatewayServer | None = None

        # Setup logging with simplified config
        setup_logging(
            level=config.logging.level,
            log_dir=config.logging.log_dir,
            environment=config.logging.environment,
        )

        logger.info("🚀 ANP Proxy initialized")

    async def run_gateway(self) -> None:
        """Run as Gateway only."""
        logger.info("🌐 Starting Gateway mode")
        self.gateway_server = GatewayServer(self.config.gateway)
        await self.gateway_server.run()

    async def run(self) -> None:
        """Run in configured mode."""
        try:
            await self.run_gateway()
        except Exception as e:
            logger.error(f"❌ Application failed: {e}")
            raise
        finally:
            await self.cleanup()

    async def cleanup(self) -> None:
        """Cleanup application resources."""
        logger.info("🔄 Cleaning up application resources...")

        if self.gateway_server:
            try:
                await self.gateway_server.stop()
                logger.info("✅ Gateway server stopped")
            except Exception as e:
                logger.error(f"❌ Error stopping gateway server: {e}")

        logger.info("✅ Application cleanup completed")


def create_app(config: ANPProxyConfig) -> ANPProxyApp:
    """Create and configure ANP Proxy application."""
    return ANPProxyApp(config)


async def run_app(config: ANPProxyConfig) -> None:
    """Run ANP Proxy application with the given configuration."""
    app = create_app(config)
    await app.run()


def get_version() -> str:
    """Get application version."""
    try:
        from anp_proxy import __version__

        return __version__
    except ImportError:
        return "development"


def get_app_info() -> dict[str, Any]:
    """Get application information."""
    return {
        "name": "ANP Proxy",
        "version": get_version(),
        "description": "HTTP over WebSocket tunneling for private networks",
    }
