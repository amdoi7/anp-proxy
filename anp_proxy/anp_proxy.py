"""ANP Proxy library entry point."""

from .common.config import ANPConfig
from .common.log_base import get_logger, setup_logging
from .gateway import GatewayServer
from .receiver import ReceiverClient

logger = get_logger(__name__)


class ANPProxy:
    """ANP Proxy library interface."""

    def __init__(self, config: ANPConfig) -> None:
        """
        Initialize ANP Proxy.

        Args:
            config: ANP configuration
        """
        self.config = config

        # Setup logging
        setup_logging(config.logging)

        logger.info("ANP Proxy library initialized", mode=config.mode)

    def create_gateway_server(self) -> GatewayServer:
        """Create Gateway server instance."""
        return GatewayServer(self.config.gateway)

    def create_receiver_client(self, app=None) -> ReceiverClient:
        """Create Receiver client instance."""
        return ReceiverClient(self.config.receiver, app)


# For backwards compatibility
def main():
    """Main entry point - delegates to CLI script."""
    import sys
    from pathlib import Path

    # Add the parent directory to path so we can import the CLI script
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from anp_proxy import main as cli_main
    cli_main()


__all__ = ["ANPProxy", "ANPConfig", "GatewayServer", "ReceiverClient"]
