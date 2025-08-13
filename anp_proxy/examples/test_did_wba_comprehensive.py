"""Comprehensive DID-WBA authentication test suite.

This test demonstrates the complete DID-WBA workflow:
1. Gateway starts with DID-WBA authentication enabled
2. Receiver connects using DID document and private key
3. WebSocket handshake performs DID-WBA authentication
4. HTTP requests are successfully routed through the authenticated tunnel
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx

from anp_proxy.common.config import AuthConfig, GatewayConfig, ReceiverConfig, TLSConfig
from anp_proxy.examples.simple_fastapi_app import app as test_app
from anp_proxy.gateway import GatewayServer
from anp_proxy.receiver import ReceiverClient

# DID configuration from docs/did_info/
DID = "did:wba:didhost.cc:anpproxy"
DID_DOC_PATH = Path("docs/did_info/did-wba-didhost.cc-anpproxy-did-doc.json")
DID_PRIVATE_KEY_PATH = Path("docs/did_info/did-wba-didhost.cc-anpproxy-private-key.pem")


class DidWbaComprehensiveTest:
    """Comprehensive DID-WBA test suite."""

    def __init__(self):
        self.gateway_port = 9090
        self.wss_port = 9790
        self.gateway = None
        self.receiver = None

    async def setup_components(self):
        """Setup Gateway and Receiver with DID-WBA authentication."""
        print("🔧 Setting up DID-WBA components...")

        # Verify DID files exist
        if not DID_DOC_PATH.exists():
            raise FileNotFoundError(f"DID document not found: {DID_DOC_PATH}")
        if not DID_PRIVATE_KEY_PATH.exists():
            raise FileNotFoundError(
                f"DID private key not found: {DID_PRIVATE_KEY_PATH}"
            )

        # Gateway configuration with DID-WBA enabled
        gateway_config = GatewayConfig(
            host="127.0.0.1",
            port=self.gateway_port,
            wss_host="127.0.0.1",
            wss_port=self.wss_port,
            auth=AuthConfig(
                enabled=True,
                did_wba_enabled=True,
                allowed_dids=[DID],  # Only allow our test DID
                did_document_path=DID_DOC_PATH,  # Use local DID doc for verification
                jwt_private_key_path=Path("docs/jwt_rs256/private.pem"),
                jwt_public_key_path=Path("docs/jwt_rs256/public.pem"),
            ),
            tls=TLSConfig(enabled=False),  # Disable TLS for local testing
            max_connections=10,
            timeout=30.0,
        )

        # Receiver configuration with DID-WBA client credentials
        receiver_config = ReceiverConfig(
            gateway_url=f"ws://127.0.0.1:{self.wss_port}",
            auth=AuthConfig(
                enabled=True,
                did_wba_enabled=True,
                did=DID,
                did_document_path=DID_DOC_PATH,
                private_key_path=DID_PRIVATE_KEY_PATH,
                jwt_private_key_path=Path("docs/jwt_rs256/private.pem"),
                jwt_public_key_path=Path("docs/jwt_rs256/public.pem"),
            ),
            tls=TLSConfig(enabled=False),
            reconnect_delay=1.0,
            max_reconnect_attempts=3,
        )

        self.gateway = GatewayServer(gateway_config)
        self.receiver = ReceiverClient(receiver_config, test_app)
        print("✅ Components configured with DID-WBA")

    async def start_and_authenticate(self):
        """Start components and verify DID-WBA handshake."""
        print("🚀 Starting Gateway...")
        gateway_task = asyncio.create_task(self.gateway.start())
        await asyncio.sleep(1)

        print("🔐 Starting Receiver with DID-WBA authentication...")
        receiver_task = asyncio.create_task(self.receiver.start())
        # Poll up to 8 times (approx 4s) for authenticated connection
        authenticated = False
        for _ in range(8):
            await asyncio.sleep(0.5)
            stats = await self.gateway.websocket_manager.get_connection_stats()
            if stats.get("authenticated_connections", 0) > 0:
                authenticated = True
                break
        print(
            f"📊 Connection stats: {await self.gateway.websocket_manager.get_connection_stats()}"
        )

        if not authenticated:
            raise RuntimeError(
                "DID-WBA handshake failed - no authenticated connections"
            )

        print("✅ DID-WBA handshake successful!")
        return gateway_task, receiver_task

    async def test_http_requests(self):
        """Test various HTTP requests through the DID-authenticated tunnel."""
        print("\n📡 Testing HTTP requests through DID-authenticated tunnel...")

        base_url = f"http://127.0.0.1:{self.gateway_port}"

        test_cases = [
            ("GET /", "Root endpoint"),
            ("GET /health", "Health check"),
            ("GET /echo/test-item?q=test-query", "Path and query parameters"),
            ("POST /echo", "POST with JSON data"),
        ]

        results = []

        async with httpx.AsyncClient(
            timeout=10.0,
            proxy=None,
            trust_env=False,
        ) as client:
            for method_path, description in test_cases:
                try:
                    method, path = method_path.split(" ", 1)
                    url = f"{base_url}{path}"

                    if method == "GET":
                        response = await client.get(url)
                    elif method == "POST":
                        test_data = {"test": True, "timestamp": time.time()}
                        response = await client.post(url, json=test_data)

                    if response.status_code == 200:
                        print(f"✅ {description}: {response.status_code}")
                        results.append(True)
                    else:
                        print(f"❌ {description}: {response.status_code}")
                        results.append(False)

                except Exception as e:
                    print(f"❌ {description}: {e}")
                    results.append(False)

        return all(results)

    async def test_unauthorized_connection(self):
        """Test that connections without DID-WBA are rejected."""
        print("\n🚫 Testing unauthorized connection (should be rejected)...")

        # Create a receiver without DID-WBA credentials
        unauthorized_config = ReceiverConfig(
            gateway_url=f"ws://127.0.0.1:{self.wss_port}",
            auth=AuthConfig(enabled=False),  # No DID-WBA
            tls=TLSConfig(enabled=False),
        )

        unauthorized_receiver = ReceiverClient(unauthorized_config, test_app)

        try:
            # This should fail to connect
            await unauthorized_receiver.start()
            await asyncio.sleep(1)

            # Check if it really connected
            stats = await self.gateway.websocket_manager.get_connection_stats()
            if stats.get("total_connections", 0) > 1:
                print("❌ Unauthorized connection was accepted (should be rejected)")
                return False
            else:
                print("✅ Unauthorized connection properly rejected")
                return True

        except Exception:
            print("✅ Unauthorized connection properly rejected (exception)")
            return True
        finally:
            try:
                await unauthorized_receiver.stop()
            except Exception:
                pass

    async def stop_components(self):
        """Stop all components."""
        print("\n🛑 Stopping components...")

        tasks = []
        if self.receiver:
            tasks.append(asyncio.create_task(self.receiver.stop()))
        if self.gateway:
            tasks.append(asyncio.create_task(self.gateway.stop()))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        print("✅ Components stopped")

    async def run_comprehensive_test(self):
        """Run the complete DID-WBA test suite."""
        print("🧪 Starting Comprehensive DID-WBA Test Suite")
        print("=" * 60)

        try:
            # Setup
            await self.setup_components()

            # Start and authenticate
            gateway_task, receiver_task = await self.start_and_authenticate()

            # Test HTTP requests
            http_success = await self.test_http_requests()

            # Test unauthorized access
            unauth_success = await self.test_unauthorized_connection()

            # Results
            print("\n" + "=" * 60)
            print("📊 Test Results:")
            print("   DID-WBA Handshake: ✅ PASS")
            print(f"   HTTP Requests: {'✅ PASS' if http_success else '❌ FAIL'}")
            print(
                f"   Unauthorized Rejection: {'✅ PASS' if unauth_success else '❌ FAIL'}"
            )

            overall_success = http_success and unauth_success
            print(f"\n🎯 Overall Result: {'✅ PASS' if overall_success else '❌ FAIL'}")

            return overall_success

        except Exception as e:
            print(f"\n💥 Test suite failed: {e}")
            return False

        finally:
            await self.stop_components()


async def main():
    """Main test function."""
    test_suite = DidWbaComprehensiveTest()

    try:
        success = await test_suite.run_comprehensive_test()

        if success:
            print("\n🎉 All DID-WBA tests passed!")
            print("   WebSocket supports DID identity authentication successfully!")
            return 0
        else:
            print("\n💥 Some DID-WBA tests failed.")
            return 1

    except KeyboardInterrupt:
        print("\n⏹️  Test interrupted by user")
        await test_suite.stop_components()
        return 1

    except Exception as e:
        print(f"\n💥 Test suite error: {e}")
        await test_suite.stop_components()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
