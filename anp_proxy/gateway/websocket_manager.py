"""WebSocket connection management for Gateway."""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.server import WebSocketServerProtocol

from ..common.auth import AuthManager
from ..common.config import GatewayConfig
from ..common.log_base import get_logger
from ..protocol import ANPXDecoder, ANPXEncoder, ANPXMessage, MessageType

logger = get_logger(__name__)


@dataclass
class ConnectionInfo:
    """Information about a WebSocket connection."""

    connection_id: str
    websocket: WebSocketServerProtocol
    authenticated: bool = False
    user_id: str | None = None
    created_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    last_ping: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    pending_requests: set[str] = field(default_factory=set)


class WebSocketManager:
    """Manages WebSocket connections from receivers."""

    def __init__(self, config: GatewayConfig, auth_manager: AuthManager) -> None:
        """
        Initialize WebSocket manager.

        Args:
            config: Gateway configuration
            auth_manager: Authentication manager
        """
        self.config = config
        self.auth_manager = auth_manager
        self.connections: dict[str, ConnectionInfo] = {}
        self.decoder = ANPXDecoder()
        self.encoder = ANPXEncoder(config.chunk_size)
        self.request_routing: dict[str, str] = {}  # request_id -> connection_id
        self._server = None
        self._cleanup_task = None

        logger.info("WebSocket manager initialized")

    async def start_server(self) -> None:
        """Start the WebSocket server."""
        try:
            self._server = await websockets.serve(
                self._handle_connection,
                self.config.wss_host,
                self.config.wss_port,
                ssl=self._create_ssl_context() if self.config.tls.enabled else None,
                ping_interval=self.config.ping_interval,
                ping_timeout=self.config.timeout,
                max_size=None,  # No size limit for large file transfers
                compression=None  # Disable compression for binary protocol
            )

            # Start cleanup task
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

            logger.info(
                "WebSocket server started",
                host=self.config.wss_host,
                port=self.config.wss_port,
                tls_enabled=self.config.tls.enabled
            )

        except Exception as e:
            logger.error("Failed to start WebSocket server", error=str(e))
            raise

    async def stop_server(self) -> None:
        """Stop the WebSocket server."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Close all connections
        for conn_info in list(self.connections.values()):
            await conn_info.websocket.close()

        self.connections.clear()
        self.request_routing.clear()

        logger.info("WebSocket server stopped")

    def _create_ssl_context(self):
        """Create SSL context for secure WebSocket connections."""
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

        if self.config.tls.cert_file and self.config.tls.key_file:
            context.load_cert_chain(self.config.tls.cert_file, self.config.tls.key_file)

        if self.config.tls.ca_file:
            context.load_verify_locations(self.config.tls.ca_file)

        # Set verification mode
        if self.config.tls.verify_mode == "required":
            context.verify_mode = ssl.CERT_REQUIRED
        elif self.config.tls.verify_mode == "optional":
            context.verify_mode = ssl.CERT_OPTIONAL
        else:
            context.verify_mode = ssl.CERT_NONE

        return context

    async def _handle_connection(self, websocket: WebSocketServerProtocol) -> None:
        """Handle a new WebSocket connection."""
        connection_id = str(uuid.uuid4())
        client_info = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"

        logger.info("New WebSocket connection", connection_id=connection_id, client=client_info)

        try:
            # Create connection info
            conn_info = ConnectionInfo(
                connection_id=connection_id,
                websocket=websocket
            )

            # Authenticate connection
            if not await self._authenticate_connection(conn_info):
                await websocket.close(code=4001, reason="Authentication failed")
                return

            # Add to connections
            self.connections[connection_id] = conn_info

            # Handle messages
            await self._message_loop(conn_info)

        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket connection closed", connection_id=connection_id)
        except Exception as e:
            logger.error("WebSocket connection error", connection_id=connection_id, error=str(e))
        finally:
            # Cleanup connection
            await self._cleanup_connection(connection_id)

    async def _authenticate_connection(self, conn_info: ConnectionInfo) -> bool:
        """Authenticate a WebSocket connection."""
        if not self.config.auth.enabled:
            conn_info.authenticated = True
            conn_info.user_id = "anonymous"

            # Still need to handle the auth message from receiver and send response
            try:
                # Wait for auth message from receiver
                auth_message = await asyncio.wait_for(
                    conn_info.websocket.recv(),
                    timeout=self.config.timeout
                )

                # Send success response even though auth is disabled
                import json
                response = json.dumps({"status": "authenticated", "auth_disabled": True})
                await conn_info.websocket.send(response)

                logger.info(
                    "Connection authenticated (auth disabled)",
                    connection_id=conn_info.connection_id,
                    user_id=conn_info.user_id
                )
                return True

            except Exception as e:
                logger.error("Failed to handle auth handshake", connection_id=conn_info.connection_id, error=str(e))
                return False

        try:
            # Wait for authentication message
            auth_message = await asyncio.wait_for(
                conn_info.websocket.recv(),
                timeout=self.config.timeout
            )

            # TODO: Parse authentication credentials from message
            # For now, use simple token-based auth
            import json
            auth_data = json.loads(auth_message)

            client_id = f"{conn_info.websocket.remote_address[0]}"
            token = self.auth_manager.authenticate_connection(client_id, auth_data)

            if token:
                token_data = self.auth_manager.verify_token(token)
                if token_data:
                    conn_info.authenticated = True
                    conn_info.user_id = token_data.user_id

                    # Send success response
                    response = json.dumps({"status": "authenticated", "token": token})
                    await conn_info.websocket.send(response)

                    logger.info(
                        "Connection authenticated",
                        connection_id=conn_info.connection_id,
                        user_id=conn_info.user_id
                    )
                    return True

            # Send failure response
            response = json.dumps({"status": "authentication_failed"})
            await conn_info.websocket.send(response)
            return False

        except Exception as e:
            logger.error("Authentication failed", connection_id=conn_info.connection_id, error=str(e))
            return False

    async def _message_loop(self, conn_info: ConnectionInfo) -> None:
        """Handle messages from a WebSocket connection."""
        while True:
            try:
                # Receive binary message
                message_data = await conn_info.websocket.recv()

                if isinstance(message_data, str):
                    # Control message (ping/pong, etc.)
                    await self._handle_control_message(conn_info, message_data)
                    continue

                # Decode ANPX message
                message = self.decoder.decode_message(message_data)
                if message:
                    await self._handle_anpx_message(conn_info, message)

            except websockets.exceptions.ConnectionClosed:
                break
            except Exception as e:
                logger.error(
                    "Error processing message",
                    connection_id=conn_info.connection_id,
                    error=str(e)
                )
                # Send error response
                error_msg = self.encoder.encode_error(str(e))
                await self._send_message(conn_info, error_msg)

    async def _handle_control_message(self, conn_info: ConnectionInfo, message: str) -> None:
        """Handle control messages (JSON format)."""
        try:
            import json
            data = json.loads(message)

            if data.get("type") == "ping":
                conn_info.last_ping = asyncio.get_event_loop().time()
                pong_data = json.dumps({"type": "pong", "timestamp": data.get("timestamp")})
                await conn_info.websocket.send(pong_data)

        except Exception as e:
            logger.warning("Invalid control message", error=str(e))

    async def _handle_anpx_message(self, conn_info: ConnectionInfo, message: ANPXMessage) -> None:
        """Handle ANPX protocol message."""
        request_id = message.get_request_id()

        if message.header.message_type == MessageType.HTTP_RESPONSE:
            # Response from receiver - route back to HTTP client
            if request_id in self.request_routing:
                # Remove routing entry
                del self.request_routing[request_id]
                conn_info.pending_requests.discard(request_id)

                # Notify response handler
                await self._notify_response_received(request_id, message)
            else:
                logger.warning(
                    "Received response for unknown request",
                    request_id=request_id,
                    connection_id=conn_info.connection_id
                )

        elif message.header.message_type == MessageType.ERROR:
            # Error from receiver
            if request_id in self.request_routing:
                del self.request_routing[request_id]
                conn_info.pending_requests.discard(request_id)

                await self._notify_error_received(request_id, message)

        else:
            logger.warning(
                "Unexpected message type from receiver",
                message_type=message.header.message_type,
                connection_id=conn_info.connection_id
            )

    async def send_request(self, request_id: str, message: ANPXMessage) -> bool:
        """
        Send request to a receiver.

        Args:
            request_id: Unique request ID
            message: ANPX request message

        Returns:
            True if sent successfully, False otherwise
        """
        # Find available connection (simple round-robin for now)
        available_connections = [
            conn for conn in self.connections.values()
            if conn.authenticated and len(conn.pending_requests) < 100
        ]

        if not available_connections:
            logger.warning("No available connections for request", request_id=request_id)
            return False

        # Select connection (round-robin)
        conn_info = min(available_connections, key=lambda c: len(c.pending_requests))

        try:
            # Send message
            await self._send_message(conn_info, message)

            # Track routing
            self.request_routing[request_id] = conn_info.connection_id
            conn_info.pending_requests.add(request_id)

            logger.debug(
                "Request sent to receiver",
                request_id=request_id,
                connection_id=conn_info.connection_id
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to send request",
                request_id=request_id,
                connection_id=conn_info.connection_id,
                error=str(e)
            )
            return False

    async def _send_message(self, conn_info: ConnectionInfo, message: ANPXMessage) -> None:
        """Send ANPX message to connection."""
        message_data = message.encode()
        await conn_info.websocket.send(message_data)

    async def _notify_response_received(self, request_id: str, message: ANPXMessage) -> None:
        """Notify that a response was received."""
        # This will be connected to the response handler
        if hasattr(self, '_response_callback'):
            await self._response_callback(request_id, message)

    async def _notify_error_received(self, request_id: str, message: ANPXMessage) -> None:
        """Notify that an error was received."""
        if hasattr(self, '_error_callback'):
            await self._error_callback(request_id, message)

    def set_response_callback(self, callback) -> None:
        """Set callback for response messages."""
        self._response_callback = callback

    def set_error_callback(self, callback) -> None:
        """Set callback for error messages."""
        self._error_callback = callback

    async def _cleanup_connection(self, connection_id: str) -> None:
        """Clean up a connection."""
        conn_info = self.connections.pop(connection_id, None)
        if not conn_info:
            return

        # Clean up pending requests
        for request_id in list(conn_info.pending_requests):
            self.request_routing.pop(request_id, None)
            # Notify that request failed
            if hasattr(self, '_request_failed_callback'):
                await self._request_failed_callback(request_id, "Connection lost")

        logger.info(
            "Connection cleaned up",
            connection_id=connection_id,
            pending_requests=len(conn_info.pending_requests)
        )

    async def _cleanup_loop(self) -> None:
        """Periodic cleanup of stale connections and requests."""
        while True:
            try:
                await asyncio.sleep(60)  # Cleanup every minute

                current_time = asyncio.get_event_loop().time()
                stale_connections = []

                for conn_id, conn_info in self.connections.items():
                    # Check if connection is stale (no ping for 2x ping interval)
                    if current_time - conn_info.last_ping > self.config.ping_interval * 2:
                        stale_connections.append(conn_id)

                # Close stale connections
                for conn_id in stale_connections:
                    conn_info = self.connections.get(conn_id)
                    if conn_info:
                        logger.warning("Closing stale connection", connection_id=conn_id)
                        await conn_info.websocket.close()

                # Cleanup decoder chunks
                self.decoder.cleanup_stale_chunks()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in cleanup loop", error=str(e))

    def get_connection_stats(self) -> dict[str, Any]:
        """Get connection statistics."""
        total_connections = len(self.connections)
        authenticated_connections = sum(1 for c in self.connections.values() if c.authenticated)
        total_pending_requests = sum(len(c.pending_requests) for c in self.connections.values())

        return {
            "total_connections": total_connections,
            "authenticated_connections": authenticated_connections,
            "total_pending_requests": total_pending_requests,
            "request_routing_entries": len(self.request_routing)
        }
