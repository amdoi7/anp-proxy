"""Simple test to verify basic Gateway functionality without Receiver."""

import asyncio

import httpx

from anp_proxy.common.config import AuthConfig, GatewayConfig
from anp_proxy.gateway import GatewayServer


async def test_gateway_only():
    """Test Gateway in standalone mode."""
    print("🧪 Testing Gateway standalone functionality")

    # Configure Gateway with auth disabled
    config = GatewayConfig(
        host="127.0.0.1",
        port=9080,
        wss_host="127.0.0.1",
        wss_port=9765,
        auth=AuthConfig(enabled=False),
        max_connections=10,
        timeout=30.0
    )

    gateway = GatewayServer(config)

    try:
        # Start Gateway
        print("🚀 Starting Gateway...")
        await gateway.start()
        await asyncio.sleep(2)  # Give time to start

        print("✅ Gateway started successfully")

        # Test health endpoint (this should work even without receiver)
        print("📡 Testing Gateway health endpoint...")
        try:
            # Clear any proxy settings
            async with httpx.AsyncClient(
                timeout=10.0,
                proxy=None,  # No proxy
                trust_env=False  # Don't trust environment proxy settings
            ) as client:
                response = await client.get("http://127.0.0.1:9080/health")
                print(f"✅ Health check - Status: {response.status_code}")
                if response.status_code == 200:
                    print(f"   Response: {response.json()}")
                else:
                    print(f"   Response: {response.text}")

        except Exception as e:
            print(f"❌ Health check failed: {e}")

        # Test other endpoint (should timeout since no receiver)
        print("📡 Testing Gateway root endpoint (should timeout)...")
        try:
            async with httpx.AsyncClient(
                timeout=5.0,  # Short timeout
                proxy=None,
                trust_env=False
            ) as client:
                response = await client.get("http://127.0.0.1:9080/")
                print(f"🤔 Unexpected success - Status: {response.status_code}")

        except httpx.TimeoutException:
            print("✅ Expected timeout (no receiver connected)")
        except Exception as e:
            print(f"❌ Unexpected error: {e}")

    finally:
        print("🛑 Stopping Gateway...")
        await gateway.stop()
        print("✅ Gateway stopped")


if __name__ == "__main__":
    asyncio.run(test_gateway_only())
