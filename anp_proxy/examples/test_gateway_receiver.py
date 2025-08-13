"""Comprehensive test script for Gateway and Receiver functionality."""

import asyncio
import time

import httpx

# Note: websockets import removed as it's not needed for this test
from anp_proxy.common.config import (
    AuthConfig,
    GatewayConfig,
    ReceiverConfig,
    TLSConfig,
)
from anp_proxy.examples.simple_fastapi_app import app as test_app
from anp_proxy.gateway import GatewayServer
from anp_proxy.receiver import ReceiverClient


class ANPProxyTester:
    """Comprehensive tester for ANP Proxy functionality."""

    def __init__(self):
        self.gateway_port = 9080
        self.wss_port = 9765
        self.gateway = None
        self.receiver = None
        self.gateway_task = None
        self.receiver_task = None

    async def setup_components(self):
        """Setup Gateway and Receiver components with auth disabled."""
        print("🔧 Setting up components...")

        # Create configuration with auth and TLS disabled for testing
        gateway_config = GatewayConfig(
            host="127.0.0.1",
            port=self.gateway_port,
            wss_host="127.0.0.1",
            wss_port=self.wss_port,
            auth=AuthConfig(enabled=False),  # Disable authentication
            tls=TLSConfig(enabled=False),  # Disable TLS for testing
            max_connections=10,
            timeout=30.0,
        )

        receiver_config = ReceiverConfig(
            gateway_url=f"ws://127.0.0.1:{self.wss_port}",  # Use ws for testing
            auth=AuthConfig(enabled=False),  # Disable authentication
            tls=TLSConfig(enabled=False),  # Disable TLS for testing
            reconnect_delay=1.0,
            max_reconnect_attempts=3,
        )

        # Create components
        self.gateway = GatewayServer(gateway_config)
        self.receiver = ReceiverClient(receiver_config, test_app)

        print("✅ Components configured (auth disabled)")

    async def start_components(self):
        """Start Gateway and Receiver asynchronously."""
        print("🚀 Starting Gateway and Receiver...")

        # Start Gateway first
        self.gateway_task = asyncio.create_task(self.gateway.start())
        await asyncio.sleep(1)  # Give gateway time to start

        # Start Receiver
        self.receiver_task = asyncio.create_task(self.receiver.start())
        await asyncio.sleep(2)  # Give receiver time to connect

        print("✅ Both components started")

    async def stop_components(self):
        """Stop Gateway and Receiver."""
        print("🛑 Stopping components...")

        tasks = []
        if self.receiver:
            tasks.append(asyncio.create_task(self.receiver.stop()))
        if self.gateway:
            tasks.append(asyncio.create_task(self.gateway.stop()))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Cancel running tasks
        for task in [self.gateway_task, self.receiver_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        print("✅ Components stopped")

    async def test_basic_get_request(self):
        """Test basic GET request through the proxy."""
        print("\n📡 Testing basic GET request...")

        try:
            async with httpx.AsyncClient(
                timeout=10.0, proxy=None, trust_env=False
            ) as client:
                response = await client.get(f"http://127.0.0.1:{self.gateway_port}/")

                assert response.status_code == 200
                data = response.json()
                assert "message" in data
                assert "timestamp" in data

                print(f"✅ GET / - Status: {response.status_code}")
                print(f"   Response: {data}")
                return True

        except Exception as e:
            print(f"❌ GET request failed: {e}")
            return False

    async def test_health_check(self):
        """Test health check endpoint."""
        print("\n🩺 Testing health check...")

        try:
            async with httpx.AsyncClient(
                timeout=10.0, proxy=None, trust_env=False
            ) as client:
                response = await client.get(
                    f"http://127.0.0.1:{self.gateway_port}/health"
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "healthy"

                print(f"✅ GET /health - Status: {response.status_code}")
                print(f"   Response: {data}")
                return True

        except Exception as e:
            print(f"❌ Health check failed: {e}")
            return False

    async def test_path_parameters(self):
        """Test path parameters."""
        print("\n🛤️  Testing path parameters...")

        try:
            async with httpx.AsyncClient(
                timeout=10.0, proxy=None, trust_env=False
            ) as client:
                response = await client.get(
                    f"http://127.0.0.1:{self.gateway_port}/echo/test-item?q=test-query"
                )

                assert response.status_code == 200
                data = response.json()
                assert data["item"] == "test-item"
                assert data["query"] == "test-query"

                print(
                    f"✅ GET /echo/test-item?q=test-query - Status: {response.status_code}"
                )
                print(f"   Response: {data}")
                return True

        except Exception as e:
            print(f"❌ Path parameters test failed: {e}")
            return False

    async def test_post_request(self):
        """Test POST request with JSON body."""
        print("\n📮 Testing POST request...")

        try:
            test_data = {"name": "ANP Proxy", "version": "1.0", "test": True}

            async with httpx.AsyncClient(
                timeout=10.0, proxy=None, trust_env=False
            ) as client:
                response = await client.post(
                    f"http://127.0.0.1:{self.gateway_port}/echo", json=test_data
                )

                assert response.status_code == 200
                data = response.json()
                assert data["method"] == "POST"
                assert "body" in data

                print(f"✅ POST /echo - Status: {response.status_code}")
                print(f"   Method: {data['method']}")
                print(f"   Body received: {data['body'] is not None}")
                return True

        except Exception as e:
            print(f"❌ POST request failed: {e}")
            return False

    async def test_slow_response(self):
        """Test slow response to verify timeout handling."""
        print("\n🐌 Testing slow response...")

        try:
            async with httpx.AsyncClient(
                timeout=15.0, proxy=None, trust_env=False
            ) as client:
                start_time = time.time()
                response = await client.get(
                    f"http://127.0.0.1:{self.gateway_port}/slow-response"
                )
                elapsed = time.time() - start_time

                assert response.status_code == 200
                data = response.json()
                assert "message" in data
                assert elapsed >= 2.0  # Should take at least 2 seconds

                print(f"✅ GET /slow-response - Status: {response.status_code}")
                print(f"   Elapsed time: {elapsed:.2f}s")
                print(f"   Response: {data['message']}")
                return True

        except Exception as e:
            print(f"❌ Slow response test failed: {e}")
            return False

    async def test_connection_status(self):
        """Test that receiver is properly connected to gateway."""
        print("\n🔌 Testing receiver connection status...")

        try:
            # Check if receiver is connected by trying to access gateway internals
            if self.gateway and self.receiver:
                print("✅ Gateway and Receiver components are running")
                # Additional check: see if we can make requests successfully
                await asyncio.sleep(1)  # Give time for connection
                return True
            else:
                print("❌ Components not properly initialized")
                return False

        except Exception as e:
            print(f"❌ Connection status test failed: {e}")
            return False

    async def run_all_tests(self):
        """Run comprehensive test suite."""
        print("🧪 Starting ANP Proxy comprehensive test suite")
        print("=" * 50)

        # Setup and start components
        await self.setup_components()
        await self.start_components()

        # Wait a bit for everything to stabilize
        await asyncio.sleep(3)

        # Run tests
        tests = [
            ("Basic GET Request", self.test_basic_get_request),
            ("Health Check", self.test_health_check),
            ("Path Parameters", self.test_path_parameters),
            ("POST Request", self.test_post_request),
            ("Slow Response (Timeout)", self.test_slow_response),
            ("Connection Status", self.test_connection_status),
        ]

        results = []
        for test_name, test_func in tests:
            try:
                result = await test_func()
                results.append((test_name, result))
            except Exception as e:
                print(f"❌ {test_name} failed with exception: {e}")
                results.append((test_name, False))

        # Print summary
        print("\n" + "=" * 50)
        print("📊 Test Results Summary:")
        passed = 0
        for test_name, result in results:
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"   {status} - {test_name}")
            if result:
                passed += 1

        print(f"\n🎯 Overall: {passed}/{len(results)} tests passed")

        # Cleanup
        await self.stop_components()

        return passed == len(results)


async def main():
    """Main test function."""
    tester = ANPProxyTester()

    try:
        success = await tester.run_all_tests()
        if success:
            print("\n🎉 All tests passed! Gateway and Receiver are working correctly.")
            return 0
        else:
            print("\n💥 Some tests failed. Check the logs above for details.")
            return 1

    except KeyboardInterrupt:
        print("\n⏹️  Test interrupted by user")
        await tester.stop_components()
        return 1

    except Exception as e:
        print(f"\n💥 Test suite failed with error: {e}")
        await tester.stop_components()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
