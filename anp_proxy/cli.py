#!/usr/bin/env python3
"""
ANP Proxy - HTTP over WebSocket tunneling for private networks

Backend service that provides HTTP to WebSocket tunneling capabilities.
Supports gateway mode for network proxy functionality.

Usage:
    python -m anp_proxy [options]
    uv run python -m anp_proxy --mode both --gateway-port 8089 --wss-port 8789
"""

import asyncio
import sys
from pathlib import Path

import click

# Add the parent directory to the Python path for development
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from anp_proxy.common.config import ANPConfig
    from anp_proxy.common.log_base import get_logger, setup_enhanced_logging
    from anp_proxy.gateway import GatewayServer
except ImportError:
    # Fallback for when package is not installed
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from anp_proxy.common.config import ANPConfig
    from anp_proxy.common.log_base import get_logger, setup_enhanced_logging
    from anp_proxy.gateway import GatewayServer

logger = get_logger(__name__)


class ANPProxyApp:
    """Main ANP Proxy application with CLI integration."""

    def __init__(self, config: ANPConfig) -> None:
        """Initialize ANP Proxy application."""
        self.config = config
        self.gateway_server: GatewayServer | None = None

        # Setup enhanced logging
        setup_enhanced_logging(
            level=config.logging.level,
            include_location=True,
            enable_console_colors=True,
        )

        logger.info("🚀 ANP Proxy initialized", mode=config.mode)

    async def run_gateway(self) -> None:
        """Run as Gateway only."""
        logger.info("🌐 Starting Gateway mode")
        self.gateway_server = GatewayServer(self.config.gateway)
        await self.gateway_server.run()

    async def run_gateway_only(self) -> None:
        """Run as Gateway only."""
        logger.info("🌐 Starting Gateway mode")
        self.gateway_server = GatewayServer(self.config.gateway)
        await self.gateway_server.run()

    async def run(self) -> None:
        """Run in configured mode."""
        try:
            if self.config.mode == "gateway":
                await self.run_gateway_only()
            else:
                logger.error(f"❌ Unknown mode: {self.config.mode}")
                raise ValueError(f"Unknown mode: {self.config.mode}")
        except Exception as e:
            logger.error(f"❌ Failed to run ANP Proxy: {e}")
            raise


@click.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Configuration file path",
)
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["gateway"]),
    default="gateway",
    help="Operating mode (gateway only)",
)
@click.option("--gateway-host", help="Gateway HTTP host")
@click.option("--gateway-port", type=int, help="Gateway HTTP port")
@click.option("--wss-host", help="WebSocket server host")
@click.option("--wss-port", type=int, help="WebSocket server port")
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
    help="Log level",
)
@click.option("--debug", is_flag=True, help="Enable debug mode")
def main(
    config: Path | None = None,
    mode: str | None = None,
    gateway_host: str | None = None,
    gateway_port: int | None = None,
    wss_host: str | None = None,
    wss_port: int | None = None,
    log_level: str | None = None,
    debug: bool = False,
) -> None:
    """
    ANP Proxy - HTTP over WebSocket tunneling for private networks.

    Examples:
        # Run as Gateway
        python -m anp_proxy --mode gateway --gateway-port 8089 --wss-port 8789

        # Run as Gateway only
        python -m anp_proxy --mode gateway --gateway-port 8080 --wss-port 8765

        # With config file
        python -m anp_proxy --config config.toml
    """
    try:
        # Load configuration
        if config:
            anp_config = ANPConfig.from_file(config)
        else:
            # Try to load default config.toml if it exists
            default_config = Path("config.toml")
            if default_config.exists():
                logger.info(f"Loading default configuration from {default_config}")
                anp_config = ANPConfig.from_file(default_config)
            else:
                anp_config = ANPConfig()

        # Apply CLI overrides
        if mode:
            anp_config.mode = mode
        if gateway_host:
            anp_config.gateway.host = gateway_host
        if gateway_port:
            anp_config.gateway.port = gateway_port
        if wss_host:
            anp_config.gateway.wss_host = wss_host
        if wss_port:
            anp_config.gateway.wss_port = wss_port
        if log_level:
            anp_config.logging.level = log_level
        if debug:
            anp_config.debug = True
            anp_config.logging.level = "DEBUG"

        # Create and run application
        app = ANPProxyApp(anp_config)

        # Run with asyncio
        if sys.platform == "win32":
            # Windows doesn't support signal handlers in asyncio
            asyncio.run(app.run())
        else:
            try:
                # Use uvloop on Unix-like systems for better performance
                import uvloop

                uvloop.install()
            except ImportError:
                pass

            asyncio.run(app.run())

    except KeyboardInterrupt:
        print("\n🛑 Received interrupt signal, shutting down")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
