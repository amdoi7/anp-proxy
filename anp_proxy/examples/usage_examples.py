"""Usage examples for ANP Proxy."""

import asyncio
from pathlib import Path

from ..common.config import ANPConfig, GatewayConfig, ReceiverConfig
from ..gateway import GatewayServer
from ..receiver import ReceiverClient
from .simple_fastapi_app import app as fastapi_app


async def example_gateway_only():
    """Example: Run Gateway only."""
    print("Running Gateway only example...")

    config = GatewayConfig(
        host="127.0.0.1", port=8080, wss_host="127.0.0.1", wss_port=8765
    )

    gateway = GatewayServer(config)

    try:
        await gateway.start()
        print("Gateway started. Connect receivers to ws://127.0.0.1:8765")
        print("HTTP requests: http://127.0.0.1:8080")

        # Run for 30 seconds
        await asyncio.sleep(30)

    finally:
        await gateway.stop()
        print("Gateway stopped")


async def example_receiver_only():
    """Example: Run Receiver only."""
    print("Running Receiver only example...")

    config = ReceiverConfig(
        gateway_url="ws://127.0.0.1:8765",
        local_app_module=None,  # Will use provided app
    )

    receiver = ReceiverClient(config, fastapi_app)

    try:
        await receiver.start()
        print("Receiver started. Connected to ws://127.0.0.1:8765")
        print("Serving FastAPI app")

        # Run for 30 seconds
        await asyncio.sleep(30)

    finally:
        await receiver.stop()
        print("Receiver stopped")


async def example_both_modes():
    """Example: Run both Gateway and Receiver."""
    print("Running both Gateway and Receiver example...")

    # Create full ANP config
    config = ANPConfig(mode="both")
    config.gateway.port = 8080
    config.gateway.wss_port = 8765
    config.receiver.gateway_url = "ws://127.0.0.1:8765"

    # Create components
    gateway = GatewayServer(config.gateway)
    receiver = ReceiverClient(config.receiver, fastapi_app)

    try:
        # Start both components
        print("Starting Gateway and Receiver...")

        await asyncio.gather(gateway.start(), receiver.start())

        print("Both components started!")
        print("Test with: curl http://127.0.0.1:8080/")

        # Run for 60 seconds
        await asyncio.sleep(60)

    finally:
        print("Stopping components...")
        await asyncio.gather(gateway.stop(), receiver.stop(), return_exceptions=True)
        print("All components stopped")


async def example_with_config_file():
    """Example: Run with configuration file."""
    print("Running with config file example...")

    # Create a temporary config file
    config_content = """
mode = "both"

[gateway]
host = "127.0.0.1"
port = 8080
wss_host = "127.0.0.1"
wss_port = 8765

[gateway.auth]
enabled = false

[receiver]
gateway_url = "ws://127.0.0.1:8765"

[receiver.auth]
enabled = false

[logging]
level = "INFO"
"""

    config_file = Path("temp_config.toml")
    config_file.write_text(config_content)

    try:
        # Load config from file
        config = ANPConfig.from_file(config_file)

        # Create and run proxy
        from .. import ANPProxy

        proxy = ANPProxy(config)

        # For this example, just run gateway
        gateway = proxy.create_gateway_server()

        await gateway.start()
        print("Proxy started from config file")

        # Run for 30 seconds
        await asyncio.sleep(30)

        await gateway.stop()

    finally:
        # Clean up temp file
        if config_file.exists():
            config_file.unlink()
        print("Config file example completed")


def main():
    """Run all examples."""
    print("ANP Proxy Usage Examples")
    print("=" * 40)

    examples = [
        ("Gateway Only", example_gateway_only),
        ("Receiver Only", example_receiver_only),
        ("Both Modes", example_both_modes),
        ("Config File", example_with_config_file),
    ]

    for name, example_func in examples:
        print(f"\n--- {name} Example ---")
        try:
            asyncio.run(example_func())
        except KeyboardInterrupt:
            print(f"{name} example interrupted")
        except Exception as e:
            print(f"{name} example failed: {e}")
        print(f"{name} example completed\n")


if __name__ == "__main__":
    main()
