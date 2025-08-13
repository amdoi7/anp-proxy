"""WebSocket client for Receiver."""

import asyncio
import json
from typing import Any

import websockets
from websockets.client import WebSocketClientProtocol

from ..common.config import ReceiverConfig
from ..common.did_wba import build_auth_headers
from ..common.log_base import get_logger
from ..common.utils import GracefulShutdown, import_app
from .app_adapter import ASGIAdapter, MockASGIApp
from .message_handler import MessageHandler
from .reconnect import ConnectionState, ReconnectManager

logger = get_logger(__name__)


class ReceiverClient:
    """WebSocket client that connects to Gateway and serves local ASGI app."""

    def __init__(self, config: ReceiverConfig, app: Any | None = None) -> None:
        """
        Initialize receiver client.

        Args:
            config: Receiver configuration
            app: Optional ASGI app (will load from config if None)
        """
        self.config = config

        # WebSocket connection
        self.websocket: WebSocketClientProtocol | None = None
        self.connected = False

        # Application adapter
        self.app = app
        self.asgi_adapter: ASGIAdapter | None = None

        # Message handling
        self.message_handler: MessageHandler | None = None

        # Reconnection management
        self.reconnect_manager = ReconnectManager(config)
        self.reconnect_manager.set_connect_callback(self._connect_websocket)
        self.reconnect_manager.set_state_change_callback(self._on_state_change)

        # Tasks
        self._message_task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None

        logger.info("Receiver client initialized", gateway_url=config.gateway_url)

    async def start(self) -> None:
        """Start the receiver client."""
        try:
            # Load ASGI app if not provided
            if self.app is None:
                await self._load_app()

            # Initialize ASGI adapter
            base_url = f"http://{self.config.local_host}:{self.config.local_port}"
            self.asgi_adapter = ASGIAdapter(self.app, base_url)

            # Initialize message handler
            self.message_handler = MessageHandler(
                self.asgi_adapter, self.config.chunk_size
            )
            self.message_handler.set_send_callback(self._send_message)

            # Connect to gateway
            success = await self.reconnect_manager.connect()
            if not success:
                raise RuntimeError("Failed to connect to gateway")

            logger.info("Receiver client started successfully")

        except Exception as e:
            logger.error("Failed to start receiver client", error=str(e))
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop the receiver client."""
        logger.info("Stopping receiver client")

        # Stop reconnection
        await self.reconnect_manager.disconnect()

        # Cancel tasks
        for task in [self._message_task, self._ping_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close WebSocket
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        # Close ASGI adapter
        if self.asgi_adapter:
            await self.asgi_adapter.close()

        self.connected = False
        logger.info("Receiver client stopped")

    async def run(self) -> None:
        """Run the receiver client with graceful shutdown."""
        with GracefulShutdown() as shutdown:
            try:
                await self.start()

                # Wait for shutdown signal
                await shutdown.wait_for_shutdown()

            finally:
                await self.stop()

    async def _load_app(self) -> None:
        """Load ASGI application from configuration."""
        if self.config.local_app_module:
            try:
                self.app = await import_app(self.config.local_app_module)
                logger.info("ASGI app loaded", module=self.config.local_app_module)
            except Exception as e:
                logger.error("Failed to load ASGI app", error=str(e))
                raise
        else:
            # Use mock app for testing
            self.app = MockASGIApp()
            logger.warning("Using mock ASGI app - set local_app_module for production")

    async def _connect_websocket(self) -> bool:
        """
        Establish WebSocket connection to gateway.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info("Connecting to gateway", url=self.config.gateway_url)

            # Optional DID-WBA headers on handshake using agent_connect
            extra_headers: dict[str, str] = build_auth_headers(
                self.config.auth, self.config.gateway_url
            )
            if extra_headers:
                # websockets expects a list of tuples or a CIMultiDict-like as HeadersLike
                # Convert dict to list of (key, value)
                extra_headers = {k: v for k, v in extra_headers.items()}

            # Connect to gateway
            connect_kwargs = dict(
                ping_interval=self.config.ping_interval,
                ping_timeout=self.config.timeout,
                close_timeout=self.config.timeout,
                max_size=None,  # No size limit for large transfers
                compression=None,  # Disable compression for binary protocol
            )
            if extra_headers:
                # websockets.HeadersLike supports dict, list of tuples, or CIMultiDict
                # Pass dict directly as extra_headers
                self.websocket = await websockets.connect(
                    self.config.gateway_url,
                    extra_headers=extra_headers,
                    **connect_kwargs,
                )
            else:
                self.websocket = await websockets.connect(
                    self.config.gateway_url,
                    **connect_kwargs,
                )

            self.connected = True

            # Send service registration message if advertised services are configured
            if self.config.advertised_services:
                await self._send_service_registration()

            # Start message handling tasks
            self._message_task = asyncio.create_task(self._message_loop())
            self._ping_task = asyncio.create_task(self._ping_loop())

            logger.info("Connected to gateway successfully")
            return True

        except Exception as e:
            logger.error("Failed to connect to gateway", error=str(e))
            if self.websocket:
                await self.websocket.close()
                self.websocket = None
            return False

    async def _message_loop(self) -> None:
        """Main message processing loop."""
        try:
            while self.connected and self.websocket:
                try:
                    # Receive message
                    message = await self.websocket.recv()

                    logger.debug(
                        "Received message",
                        message_type=type(message).__name__,
                        size=len(message) if hasattr(message, "__len__") else "unknown",
                    )

                    if isinstance(message, str):
                        # Control message
                        await self._handle_control_message(message)
                    elif isinstance(message, bytes):
                        # Binary ANPX message
                        if self.message_handler:
                            await self.message_handler.handle_message(message)
                        else:
                            logger.warning(
                                "Received binary message but no message handler available"
                            )
                    else:
                        logger.warning(
                            "Received unknown message type", message_type=type(message)
                        )

                except websockets.ConnectionClosed:
                    logger.info("WebSocket connection closed")
                    break
                except Exception as msg_error:
                    logger.error(
                        "Error processing individual message",
                        error=str(msg_error),
                        error_type=type(msg_error).__name__,
                    )
                    # Continue processing other messages

        except Exception as e:
            logger.error(
                "Error in message loop", error=str(e), error_type=type(e).__name__
            )
        finally:
            self.connected = False
            self.reconnect_manager.on_connection_lost()

    async def _handle_control_message(self, message: str) -> None:
        """Handle control messages (JSON format)."""
        try:
            data = json.loads(message)

            if data.get("type") == "pong":
                # Pong response - just log for debugging
                logger.debug("Received pong from gateway")

        except Exception as e:
            logger.warning("Invalid control message", error=str(e))

    async def _ping_loop(self) -> None:
        """Send periodic ping messages to gateway."""
        try:
            while self.connected and self.websocket:
                await asyncio.sleep(self.config.ping_interval)

                if self.connected and self.websocket:
                    try:
                        ping_data = {
                            "type": "ping",
                            "timestamp": asyncio.get_event_loop().time(),
                        }
                        await self.websocket.send(json.dumps(ping_data))

                    except websockets.ConnectionClosed:
                        break
                    except Exception as e:
                        logger.warning("Failed to send ping", error=str(e))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error in ping loop", error=str(e))

    async def _send_message(self, message: Any) -> None:
        """Send ANPX message to gateway."""
        if not self.connected or not self.websocket:
            raise RuntimeError("Not connected to gateway")

        try:
            message_data = message.encode()
            await self.websocket.send(message_data)

        except Exception as e:
            logger.error("Failed to send message", error=str(e))
            raise

    async def _send_service_registration(self) -> None:
        """Send service registration message to gateway."""
        try:
            registration_message = {
                "type": "service_registration",
                "advertised_services": self.config.advertised_services,
            }

            await self.websocket.send(json.dumps(registration_message))

            logger.info(
                "Sent service registration", services=self.config.advertised_services
            )

        except Exception as e:
            logger.error("Failed to send service registration", error=str(e))

    def _on_state_change(self, new_state: ConnectionState) -> None:
        """Handle connection state changes."""
        if new_state == ConnectionState.CONNECTED:
            logger.info("Connection established")
        elif new_state == ConnectionState.DISCONNECTED:
            logger.warning("Connection lost")
            self.connected = False
        elif new_state == ConnectionState.FAILED:
            logger.error("Connection failed permanently")
            self.connected = False

    def get_stats(self) -> dict[str, Any]:
        """Get client statistics."""
        stats = {
            "connected": self.connected,
            "gateway_url": self.config.gateway_url,
            "reconnect": self.reconnect_manager.get_stats(),
        }

        if self.message_handler:
            stats["message_handler"] = self.message_handler.get_stats()

        return stats
