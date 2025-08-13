"""DID-WBA end-to-end test for Gateway and Receiver.

This example uses the DID document and private key under docs/did_info/ to
perform a WebSocket handshake with DID-WBA authentication, then runs basic
HTTP tests through the gateway.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx

from anp_proxy.common.config import (
    AuthConfig,
    GatewayConfig,
    LogConfig,
    ReceiverConfig,
    TLSConfig,
)
from anp_proxy.common.log_base import setup_logging
from anp_proxy.examples.simple_fastapi_app import app as test_app
from anp_proxy.gateway import GatewayServer
from anp_proxy.receiver import ReceiverClient

DID = "did:wba:didhost.cc:anpproxy"
DID_DOC = Path("docs/did_info/did-wba-didhost.cc-anpproxy-did-doc.json")
DID_PRIVATE_KEY = Path("docs/did_info/did-wba-didhost.cc-anpproxy-private-key.pem")

setup_logging(LogConfig(level="INFO"))


class DidWbaTester:
    """End-to-end tester for DID-WBA handshake and proxy path."""

    def __init__(self, gateway_port: int = 9081, wss_port: int = 9766) -> None:
        self.gateway_port = gateway_port
        self.wss_port = wss_port
        self.gateway: GatewayServer | None = None
        self.receiver: ReceiverClient | None = None
        self.gateway_task: asyncio.Task | None = None
        self.receiver_task: asyncio.Task | None = None

    async def setup_components(self) -> None:
        print("🔧 Setting up components (DID-WBA enabled)...")

        if not DID_DOC.exists():
            raise FileNotFoundError(f"DID document not found: {DID_DOC}")
        if not DID_PRIVATE_KEY.exists():
            raise FileNotFoundError(f"DID private key not found: {DID_PRIVATE_KEY}")

        # Gateway config: enable DID-WBA; TLS disabled for local test
        gateway_config = GatewayConfig(
            host="127.0.0.1",
            port=self.gateway_port,
            wss_host="127.0.0.1",
            wss_port=self.wss_port,
            auth=AuthConfig(
                enabled=True,
                did_wba_enabled=True,
                allowed_dids=[DID],
                jwt_private_key_path=Path("docs/jwt_rs256/private.pem"),
                jwt_public_key_path=Path("docs/jwt_rs256/public.pem"),
            ),
            tls=TLSConfig(enabled=False),
            max_connections=10,
            timeout=30.0,
        )

        # Receiver config: enable DID-WBA and point to local DID assets; TLS disabled
        receiver_config = ReceiverConfig(
            gateway_url=f"ws://127.0.0.1:{self.wss_port}",
            auth=AuthConfig(
                enabled=True,  # JSON auth will be skipped because did_wba_enabled=True
                did_wba_enabled=True,
                did=DID,
                did_document_path=DID_DOC,
                private_key_path=DID_PRIVATE_KEY,
                jwt_private_key_path=Path("docs/jwt_rs256/private.pem"),
                jwt_public_key_path=Path("docs/jwt_rs256/public.pem"),
            ),
            tls=TLSConfig(enabled=False),
            reconnect_delay=1.0,
            max_reconnect_attempts=3,
        )

        self.gateway = GatewayServer(gateway_config)
        self.receiver = ReceiverClient(receiver_config, test_app)
        print("✅ Components configured (DID-WBA)")

    async def start_components(self) -> None:
        print("🚀 Starting Gateway and Receiver...")
        assert self.gateway is not None and self.receiver is not None
        self.gateway_task = asyncio.create_task(self.gateway.start())
        await asyncio.sleep(1.5)
        self.receiver_task = asyncio.create_task(self.receiver.start())
        await asyncio.sleep(2.0)
        print("✅ Both components started")

    async def stop_components(self) -> None:
        print("🛑 Stopping components...")
        tasks = []
        if self.receiver:
            tasks.append(asyncio.create_task(self.receiver.stop()))
        if self.gateway:
            tasks.append(asyncio.create_task(self.gateway.stop()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in [self.gateway_task, self.receiver_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        print("✅ Components stopped")

    async def test_did_handshake(self) -> bool:
        print("\n🔐 Testing DID-WBA handshake...")
        try:
            assert self.gateway is not None
            stats = await self.gateway.websocket_manager.get_connection_stats()
            # Expect at least one authenticated connection
            ok = stats.get("authenticated_connections", 0) >= 1
            if ok:
                print("✅ DID-WBA handshake authenticated")
            else:
                print(f"❌ Handshake not authenticated, stats={stats}")
            return ok
        except Exception as e:
            print(f"❌ DID-WBA handshake check failed: {e}")
            return False

    async def test_basic_request(self) -> bool:
        print("\n📡 Testing basic GET request through gateway...")
        try:
            async with httpx.AsyncClient(
                timeout=10.0, proxy=None, trust_env=False
            ) as client:
                r = await client.get(f"http://127.0.0.1:{self.gateway_port}/")
                if r.status_code != 200:
                    print(f"❌ Unexpected status: {r.status_code}, body={r.text}")
                    return False
                data = r.json()
                ok = "message" in data and "timestamp" in data
                if ok:
                    print("✅ GET / passed")
                else:
                    print(f"❌ Unexpected payload: {data}")
                return ok
        except Exception as e:
            print(f"❌ Request failed: {e}")
            return False

    async def run(self) -> bool:
        await self.setup_components()
        await self.start_components()
        try:
            await asyncio.sleep(1.0)
            ok1 = await self.test_did_handshake()
            ok2 = await self.test_basic_request()
            return ok1 and ok2
        finally:
            await self.stop_components()


async def main() -> int:
    # Ensure predictable environment (disable proxies)
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    tester = DidWbaTester()
    try:
        success = await tester.run()
        print("\n=== RESULT ===")
        print("PASS" if success else "FAIL")
        return 0 if success else 1
    except KeyboardInterrupt:
        await tester.stop_components()
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
