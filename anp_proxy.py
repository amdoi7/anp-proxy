"""Main entry point for ANP Proxy."""

import asyncio
import sys
from pathlib import Path

import click

from anp_proxy.common.config import ANPConfig
from anp_proxy.common.log_base import get_logger, setup_logging
from anp_proxy.gateway import GatewayServer
from anp_proxy.receiver import ReceiverClient

logger = get_logger(__name__)


class ANPProxy:
    """Main ANP Proxy application."""

    def __init__(self, config: ANPConfig) -> None:
        """
        Initialize ANP Proxy.

        Args:
            config: ANP configuration
        """
        self.config = config
        self.gateway_server: GatewayServer | None = None
        self.receiver_client: ReceiverClient | None = None

        # Setup logging
        setup_logging(config.logging)

        logger.info("ANP Proxy initialized", mode=config.mode)

    async def run_gateway(self) -> None:
        """Run as Gateway only."""
        logger.info("Starting ANP Proxy in Gateway mode")

        self.gateway_server = GatewayServer(self.config.gateway)
        await self.gateway_server.run()

    async def run_receiver(self) -> None:
        """Run as Receiver only."""
        logger.info("Starting ANP Proxy in Receiver mode")

        self.receiver_client = ReceiverClient(self.config.receiver)
        await self.receiver_client.run()

    async def run_both(self) -> None:
        """Run both Gateway and Receiver."""
        logger.info("Starting ANP Proxy in dual mode (Gateway + Receiver)")

        # Create components
        self.gateway_server = GatewayServer(self.config.gateway)
        self.receiver_client = ReceiverClient(self.config.receiver)

        # Start both components concurrently
        try:
            await asyncio.gather(
                self.gateway_server.run(),
                self.receiver_client.run()
            )
        except Exception as e:
            logger.error("Error running dual mode", error=str(e))
            raise

    async def run(self) -> None:
        """Run in configured mode."""
        if self.config.mode == "gateway":
            await self.run_gateway()
        elif self.config.mode == "receiver":
            await self.run_receiver()
        elif self.config.mode == "both":
            await self.run_both()
        else:
            raise ValueError(f"Invalid mode: {self.config.mode}")


@click.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Configuration file path"
)
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["gateway", "receiver", "both"]),
    help="Operating mode"
)
@click.option(
    "--gateway-host",
    help="Gateway HTTP host"
)
@click.option(
    "--gateway-port",
    type=int,
    help="Gateway HTTP port"
)
@click.option(
    "--wss-host",
    help="WebSocket server host"
)
@click.option(
    "--wss-port",
    type=int,
    help="WebSocket server port"
)
@click.option(
    "--gateway-url",
    help="Gateway WebSocket URL for receiver"
)
@click.option(
    "--local-app",
    help="Local ASGI app module (e.g., 'myapp:app')"
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
    help="Log level"
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug mode"
)
def main(
    config: Path | None = None,
    mode: str | None = None,
    gateway_host: str | None = None,
    gateway_port: int | None = None,
    wss_host: str | None = None,
    wss_port: int | None = None,
    gateway_url: str | None = None,
    local_app: str | None = None,
    log_level: str | None = None,
    debug: bool = False
) -> None:
    """
    ANP Proxy - HTTP over WebSocket tunneling for private networks.

    Examples:
        # Run as Gateway
        anp-proxy --mode gateway --gateway-port 8080 --wss-port 8765

        # Run as Receiver
        anp-proxy --mode receiver --gateway-url wss://gateway.example.com:8765 --local-app myapp:app

        # Run both (development)
        anp-proxy --mode both --local-app myapp:app

        # With config file
        anp-proxy --config config.toml
    """
    try:
        # Load configuration
        if config:
            anp_config = ANPConfig.from_file(config)
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

        if gateway_url:
            anp_config.receiver.gateway_url = gateway_url

        if local_app:
            anp_config.receiver.local_app_module = local_app

        if log_level:
            anp_config.logging.level = log_level

        if debug:
            anp_config.debug = True
            anp_config.logging.level = "DEBUG"

        # Create and run ANP Proxy
        proxy = ANPProxy(anp_config)

        # Run with asyncio
        if sys.platform == "win32":
            # Windows doesn't support signal handlers in asyncio
            asyncio.run(proxy.run())
        else:
            try:
                # Use uvloop on Unix-like systems for better performance
                import uvloop
                uvloop.install()
            except ImportError:
                pass

            asyncio.run(proxy.run())

    except KeyboardInterrupt:
        print("Received interrupt signal, shutting down")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
