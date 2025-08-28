#!/usr/bin/env python3
"""
ANP Proxy - HTTP over WebSocket tunneling for private networks

Backend service that provides HTTP to WebSocket tunneling capabilities.
Configuration is loaded from config.toml file.

Usage:
    uv run anp-proxy
"""

import asyncio
import sys
from pathlib import Path

# Add the parent directory to the Python path for development
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from anp_proxy.app import run_app
    from anp_proxy.common.config import ANPProxyConfig
    from anp_proxy.common.log_base import logger
except ImportError:
    # Fallback for when package is not installed
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from anp_proxy.app import run_app
    from anp_proxy.common.config import ANPProxyConfig
    from anp_proxy.common.log_base import logger


def main() -> None:
    """
    ANP Proxy - HTTP over WebSocket tunneling for private networks.
    All configuration is loaded from config.toml file.
    """
    try:
        # Load configuration from config.toml
        config_path = Path("config.toml")
        if config_path.exists():
            logger.info(f"Loading configuration from {config_path}")
            anp_config = ANPProxyConfig.from_file(config_path)
        else:
            logger.error("config.toml not found! Please create a configuration file.")
            print("❌ config.toml not found! Please create a configuration file.")
            sys.exit(1)

        # Run application using app.py
        if sys.platform == "win32":
            # Windows doesn't support signal handlers in asyncio
            asyncio.run(run_app(anp_config))
        else:
            try:
                # Use uvloop on Unix-like systems for better performance
                import uvloop

                uvloop.install()
            except ImportError:
                pass

            asyncio.run(run_app(anp_config))

    except KeyboardInterrupt:
        print("\n🛑 Received interrupt signal, shutting down")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
