"""WebSocket connection management for Gateway."""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.server import WebSocketServerProtocol

from ..common.config import GatewayConfig
from ..common.db_base import DatabaseAdapter
from ..common.did_wba import DidWbaVerifier
from ..common.log_base import get_logger
from ..common.service_registry import ServiceRegistry
from ..protocol import ANPXDecoder, ANPXEncoder, ANPXMessage, MessageType

logger = get_logger(__name__)


@dataclass
class ConnectionInfo:
    """
    Enhanced WebSocket connection information with pool management.

    Attributes:
        connection_id: Unique connection identifier
        websocket: WebSocket server protocol instance
        authenticated: Whether the connection is authenticated
        user_id: User DID if authenticated
        created_at: Timestamp when connection was created
        last_ping: Timestamp of last ping
        pending_requests: Set of request IDs that are pending
        advertised_services: List of service URLs advertised by the connection
        request_count: Total number of requests processed
        last_activity: Timestamp of last activity for connection reuse
        is_idle: Whether connection is currently idle and available for reuse
        connection_weight: Weight for load balancing (based on performance)
    """

    connection_id: str
    websocket: WebSocketServerProtocol
    authenticated: bool = False
    user_id: str | None = None
    created_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    last_ping: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    pending_requests: set[str] = field(default_factory=set)
    advertised_services: list[str] = field(default_factory=list)
    # Connection pool management fields
    request_count: int = 0
    last_activity: float = field(
        default_factory=lambda: asyncio.get_event_loop().time()
    )
    is_idle: bool = True
    connection_weight: float = 1.0  # Higher weight = better performance


class ConnectionPool:
    """
    Connection pool for managing WebSocket connection reuse and load balancing.
    """

    def __init__(self, max_connections_per_service: int = 10):
        """Initialize connection pool.

        Args:
            max_connections_per_service: Maximum connections per service for pooling
        """
        self.max_connections_per_service = max_connections_per_service
        self.service_connections: dict[
            str, list[ConnectionInfo]
        ] = {}  # service -> connections
        self.connection_metrics: dict[str, dict] = {}  # connection_id -> metrics

    def add_connection(self, service_id: str, conn_info: ConnectionInfo) -> None:
        """Add connection to pool for a specific service."""
        if service_id not in self.service_connections:
            self.service_connections[service_id] = []

        connections = self.service_connections[service_id]

        # Remove if already exists (update)
        connections = [
            c for c in connections if c.connection_id != conn_info.connection_id
        ]

        # Add new connection
        connections.append(conn_info)

        # Keep only the best connections (by weight/performance)
        if len(connections) > self.max_connections_per_service:
            connections.sort(key=lambda c: c.connection_weight, reverse=True)
            connections = connections[: self.max_connections_per_service]

        self.service_connections[service_id] = connections

        # Initialize metrics
        self.connection_metrics[conn_info.connection_id] = {
            "requests_handled": 0,
            "avg_response_time": 0.0,
            "error_rate": 0.0,
        }

    def get_best_connection(self, service_id: str) -> ConnectionInfo | None:
        """Get the best available connection for a service using weighted least connections."""
        connections = self.service_connections.get(service_id, [])
        if not connections:
            return None

        # Filter healthy and authenticated connections
        healthy_connections = [
            conn
            for conn in connections
            if conn.authenticated and conn.websocket and not conn.websocket.closed
        ]

        if not healthy_connections:
            return None

        # Calculate load score for each connection (lower is better)
        def calculate_load_score(conn: ConnectionInfo) -> float:
            pending_weight = len(conn.pending_requests) * 1.0
            activity_weight = (
                asyncio.get_event_loop().time() - conn.last_activity
            ) * 0.1
            inverse_weight = 1.0 / max(conn.connection_weight, 0.1)
            return pending_weight + activity_weight + inverse_weight

        # Select connection with lowest load score
        best_connection = min(healthy_connections, key=calculate_load_score)

        # Update connection state
        best_connection.last_activity = asyncio.get_event_loop().time()
        best_connection.is_idle = False

        return best_connection

    def update_connection_metrics(
        self, connection_id: str, response_time: float, success: bool
    ) -> None:
        """Update connection performance metrics."""
        if connection_id not in self.connection_metrics:
            return

        metrics = self.connection_metrics[connection_id]
        metrics["requests_handled"] += 1

        # Update average response time (exponential moving average)
        alpha = 0.1  # Smoothing factor
        current_avg = metrics["avg_response_time"]
        metrics["avg_response_time"] = alpha * response_time + (1 - alpha) * current_avg

        # Update error rate
        if not success:
            current_errors = metrics.get("errors", 0)
            metrics["errors"] = current_errors + 1

        total_requests = metrics["requests_handled"]
        total_errors = metrics.get("errors", 0)
        metrics["error_rate"] = total_errors / max(total_requests, 1)

        # Update connection weight based on performance
        # Better performance = higher weight
        base_weight = 1.0
        response_factor = max(
            0.1, 1.0 - (response_time / 1000.0)
        )  # Penalize slow responses
        error_factor = max(
            0.1, 1.0 - metrics["error_rate"]
        )  # Penalize high error rates

        new_weight = base_weight * response_factor * error_factor

        # Find and update connection weight
        for service_connections in self.service_connections.values():
            for conn in service_connections:
                if conn.connection_id == connection_id:
                    conn.connection_weight = new_weight
                    break

    def remove_connection(self, connection_id: str) -> None:
        """Remove connection from all service pools."""
        for service_id, connections in self.service_connections.items():
            self.service_connections[service_id] = [
                c for c in connections if c.connection_id != connection_id
            ]
        self.connection_metrics.pop(connection_id, None)

    def get_pool_stats(self) -> dict:
        """Get connection pool statistics."""
        total_connections = sum(
            len(conns) for conns in self.service_connections.values()
        )
        active_connections = 0
        idle_connections = 0

        for connections in self.service_connections.values():
            for conn in connections:
                if conn.is_idle:
                    idle_connections += 1
                else:
                    active_connections += 1

        return {
            "total_pooled_connections": total_connections,
            "active_connections": active_connections,
            "idle_connections": idle_connections,
            "services_with_pools": len(self.service_connections),
            "average_connections_per_service": total_connections
            / max(len(self.service_connections), 1),
        }


class WebSocketManager:
    """
    Enhanced WebSocket manager with connection pooling, service discovery and smart routing.
    """

    def __init__(self, config: GatewayConfig) -> None:
        """
        Initialize WebSocket manager.

        Args:
            config: Gateway configuration

        Attributes:
            connections: Dictionary of connection IDs to ConnectionInfo objects
            decoder: ANPX decoder instance
            encoder: ANPX encoder instance
            request_routing: Dictionary of request IDs to connection IDs
            _server: WebSocket server instance
            _cleanup_task: Task for periodic cleanup
            _did_wba_verifier: DID-WBA verifier instance
            db_adapter: Database adapter instance
            service_registry: Service registry instance
        """
        self.config = config
        self.connections: dict[str, ConnectionInfo] = {}
        self.decoder = ANPXDecoder()
        self.encoder = ANPXEncoder(config.chunk_size)
        self.request_routing: dict[str, str] = {}  # request_id -> connection_id
        self.request_timestamps: dict[
            str, float
        ] = {}  # request_id -> timestamp for cleanup
        self._server = None
        self._cleanup_task = None
        self._did_wba_verifier = DidWbaVerifier(config.auth)

        # Connection Pool Management
        self.connection_pool = ConnectionPool(
            max_connections_per_service=config.max_connections_per_service
            if hasattr(config, "max_connections_per_service")
            else 10
        )

        #  Service Discovery
        self.db_adapter: DatabaseAdapter | None = None
        self.service_registry: ServiceRegistry | None = None

        logger.info(
            "WebSocket manager initialized", smart_routing=config.enable_smart_routing
        )

    async def start_server(self) -> None:
        """Start the WebSocket server with service discovery."""
        try:
            # Initialize service discovery components if enabled
            if self.config.enable_smart_routing:
                await self._initialize_service_discovery()

            self._server = await websockets.serve(
                self._handle_connection,
                self.config.wss_host,
                self.config.wss_port,
                ping_interval=self.config.ping_interval,
                ping_timeout=self.config.timeout,
                max_size=None,  # No size limit for large file transfers
                compression=None,  # Disable compression for binary protocol
            )

            # Start cleanup task
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

            logger.info(
                "WebSocket server started",
                host=self.config.wss_host,
                port=self.config.wss_port,
                tls_enabled=self.config.tls.enabled,
                smart_routing=self.config.enable_smart_routing,
            )

        except Exception as e:
            logger.error("Failed to start WebSocket server", error=str(e))
            raise

    async def _initialize_service_discovery(self) -> None:
        """Initialize database adapter and service registry."""
        try:
            # Initialize database adapter
            self.db_adapter = DatabaseAdapter(self.config.database)
            self.db_adapter.initialize()

            # Initialize service registry
            self.service_registry = ServiceRegistry(self.db_adapter)
            await self.service_registry.start()

            logger.info("Service discovery components initialized")

        except Exception as e:
            logger.error("Failed to initialize service discovery", error=str(e))
            # Continue without service discovery on failure
            self.db_adapter = None
            self.service_registry = None

    async def stop_server(self) -> None:
        """Stop the WebSocket server and cleanup resources."""
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
        self.request_timestamps.clear()

        # Stop service discovery components
        if self.service_registry:
            await self.service_registry.stop()

        if self.db_adapter:
            self.db_adapter.close()

        logger.info("WebSocket server stopped")

    async def _handle_connection(self, websocket: WebSocketServerProtocol) -> None:
        """WebSocket connection handler with service registration."""
        connection_id = str(uuid.uuid4())
        client_info = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"

        logger.info(
            "New WebSocket connection", connection_id=connection_id, client=client_info
        )

        try:
            # Create connection info
            conn_info = ConnectionInfo(connection_id=connection_id, websocket=websocket)

            # Phase 1: Authentication
            user_did = await self._authenticate_connection(
                websocket, conn_info, connection_id
            )
            if not user_did:
                return

            # Add to active connections early
            self.connections[connection_id] = conn_info

            # Phase 2: Service Registration (if smart routing enabled)
            service_urls = await self._register_connection_services(
                connection_id, user_did
            )

            # Phase 3: Add to connection pool for efficient reuse
            if service_urls:
                for service_url in service_urls:
                    self.connection_pool.add_connection(service_url, conn_info)

            logger.info(
                "Connection established successfully",
                connection_id=connection_id,
                did=user_did,
                services_count=len(service_urls) if service_urls else 0,
            )

            # Handle messages
            await self._message_loop(conn_info)

        except websockets.ConnectionClosed:
            logger.info("WebSocket connection closed", connection_id=connection_id)
        except Exception as e:
            logger.error(
                "WebSocket connection error", connection_id=connection_id, error=str(e)
            )
        finally:
            # cleanup
            await self._cleanup_connection(connection_id)

    async def _authenticate_connection(
        self,
        websocket: WebSocketServerProtocol,
        conn_info: ConnectionInfo,
        connection_id: str,
    ) -> str | None:
        """Authenticate WebSocket connection and return user DID."""
        if self.config.auth.did_wba_enabled:
            did_result = await self._verify_did_headers(websocket)
            if not did_result.success:
                await websocket.close(code=4003, reason="DID authentication failed")
                logger.warning(
                    "DID authentication failed",
                    connection_id=connection_id,
                    error=did_result.error,
                )
                return None

            conn_info.authenticated = True
            conn_info.user_id = did_result.did or "did-user"
            return did_result.did or "anonymous"
        else:
            # DID-WBA is disabled; allow connection as anonymous
            conn_info.authenticated = True
            conn_info.user_id = "anonymous"
            return "anonymous"

    async def _register_connection_services(
        self, connection_id: str, did: str
    ) -> list[str]:
        """Register connection using DID->ConnectionInfo mapping."""
        if not self.service_registry:
            return []

        try:
            # Get ConnectionInfo object from connections dict
            conn_info = self.connections.get(connection_id)
            if not conn_info:
                logger.error(
                    "ConnectionInfo not found for registration",
                    connection_id=connection_id,
                )
                return []

            # Register DID -> ConnectionInfo in service registry
            service_urls = await self.service_registry.register_connection(
                did, conn_info, conn_info.advertised_services
            )

            if service_urls:
                logger.info(
                    "Connection registered with DID->ConnectionInfo mapping",
                    connection_id=connection_id,
                    did=did,
                    services=service_urls,
                )

            return service_urls

        except Exception as e:
            logger.error(
                "Failed to register connection",
                connection_id=connection_id,
                did=did,
                error=str(e),
            )
            return []

    async def _verify_did_headers(self, websocket: WebSocketServerProtocol):
        """Verify DID-WBA headers during WS handshake."""
        try:
            # Extract domain from websocket host
            domain = websocket.host or self.config.wss_host
            logger.info(
                f"Verifying DID-WBA headers {websocket.host} {self.config.wss_host}"
            )

            result = await self._did_wba_verifier.verify(
                websocket.request_headers, domain
            )
            if result.success:
                logger.info("DID-WBA authenticated", did=result.did)
            else:
                logger.warning("DID-WBA auth failed", error=result.error)
            return result
        except Exception as e:
            logger.error("DID-WBA verification error", error=str(e))
            from ..common.did_wba import DidAuthResult

            return DidAuthResult(success=False, error=str(e))

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

            except websockets.ConnectionClosed:
                break
            except Exception as e:
                logger.error(
                    "Error processing message",
                    connection_id=conn_info.connection_id,
                    error=str(e),
                )
                # Send error response
                error_msg = self.encoder.encode_error(str(e))
                await self._send_message(conn_info, error_msg)

    async def _handle_control_message(
        self, conn_info: ConnectionInfo, message: str
    ) -> None:
        """Handle control messages (JSON format)."""
        try:
            import json

            data = json.loads(message)

            if data.get("type") == "ping":
                conn_info.last_ping = asyncio.get_event_loop().time()
                pong_data = json.dumps({
                    "type": "pong",
                    "timestamp": data.get("timestamp"),
                })
                await conn_info.websocket.send(pong_data)

            elif data.get("type") == "service_registration":
                # Service registration is now database-driven, ignore advertised services
                advertised_services = data.get("advertised_services", [])

                logger.info(
                    "Received service registration (ignored - using database-driven services)",
                    connection_id=conn_info.connection_id,
                    advertised_services=advertised_services,
                    did=conn_info.user_id,
                )

                # Services are determined by database lookup during connection establishment
                # No action needed here

        except Exception as e:
            logger.warning("Invalid control message", error=str(e))

    async def _handle_anpx_message(
        self, conn_info: ConnectionInfo, message: ANPXMessage
    ) -> None:
        """Handle ANPX protocol message."""
        request_id = message.get_request_id()

        if message.header.message_type == MessageType.HTTP_RESPONSE:
            # Response from receiver - route back to HTTP client
            if request_id in self.request_routing:
                # Calculate response time for connection pool metrics
                request_start_time = self.request_timestamps.get(request_id)
                current_time = asyncio.get_event_loop().time()
                response_time = (
                    (current_time - request_start_time) * 1000
                    if request_start_time
                    else 0
                )

                # Update connection pool metrics
                self.connection_pool.update_connection_metrics(
                    conn_info.connection_id, response_time, True
                )

                # Update connection state
                conn_info.request_count += 1
                conn_info.is_idle = len(conn_info.pending_requests) <= 1

                # Remove routing entry and timestamp
                del self.request_routing[request_id]
                self.request_timestamps.pop(request_id, None)
                if request_id is not None:
                    conn_info.pending_requests.discard(request_id)

                # Notify response handler
                await self._notify_response_received(request_id, message)
            else:
                logger.warning(
                    "Received response for unknown request",
                    request_id=request_id,
                    connection_id=conn_info.connection_id,
                )

        elif message.header.message_type == MessageType.ERROR:
            # Error from receiver
            if request_id in self.request_routing:
                # Calculate response time and update metrics as failed
                request_start_time = self.request_timestamps.get(request_id)
                current_time = asyncio.get_event_loop().time()
                response_time = (
                    (current_time - request_start_time) * 1000
                    if request_start_time
                    else 0
                )

                # Update connection pool metrics with failure
                self.connection_pool.update_connection_metrics(
                    conn_info.connection_id, response_time, False
                )

                # Update connection state
                conn_info.is_idle = len(conn_info.pending_requests) <= 1

                del self.request_routing[request_id]
                self.request_timestamps.pop(request_id, None)
                if request_id is not None:
                    conn_info.pending_requests.discard(request_id)

                await self._notify_error_received(request_id, message)
        else:
            logger.warning(
                "Unexpected message type from receiver",
                message_type=message.header.message_type,
                connection_id=conn_info.connection_id,
            )

    async def send_request(self, request_id: str, message: ANPXMessage) -> bool:
        """
        Send request to a receiver
        - DISABLED: Only database-driven routing allowed.

        This method is now disabled to enforce database-only routing.
        All requests must go through smart router with database service mapping.

        Args:
            request_id: Unique request ID
            message: ANPX request message

        Returns:
            False - fallback routing is disabled
        """
        logger.warning(
            "Fallback round-robin routing disabled - use database-driven smart routing only",
            request_id=request_id,
        )
        return False

    async def _send_message(
        self, conn_info: ConnectionInfo, message: ANPXMessage
    ) -> None:
        """Send ANPX message to connection."""
        message_data = message.encode()
        await conn_info.websocket.send(message_data)

    async def _notify_response_received(
        self, request_id: str, message: ANPXMessage
    ) -> None:
        """Notify that a response was received."""
        # This will be connected to the response handler
        if hasattr(self, "_response_callback"):
            await self._response_callback(request_id, message)

    async def _notify_error_received(
        self, request_id: str, message: ANPXMessage
    ) -> None:
        """Notify that an error was received."""
        if hasattr(self, "_error_callback"):
            await self._error_callback(request_id, message)

    def set_response_callback(self, callback) -> None:
        """Set callback for response messages."""
        self._response_callback = callback

    def set_error_callback(self, callback) -> None:
        """Set callback for error messages."""
        self._error_callback = callback

    async def _cleanup_connection(self, connection_id: str) -> None:
        """connection cleanup with DID->ConnectionInfo mapping."""
        # Get connection info before removing it
        conn_info = self.connections.get(connection_id)
        did = None

        if conn_info:
            did = conn_info.user_id  # The DID is stored in user_id

            # Clean up pending requests
            for request_id in list(conn_info.pending_requests):
                self.request_routing.pop(request_id, None)
                self.request_timestamps.pop(request_id, None)
                # Notify that request failed
                if hasattr(self, "_request_failed_callback"):
                    await self._request_failed_callback(request_id, "Connection lost")

        # Remove from connections dict
        self.connections.pop(connection_id, None)

        # Remove from connection pool
        self.connection_pool.remove_connection(connection_id)

        # Remove from service registry by DID
        if self.service_registry and did:
            await self.service_registry.remove_connection(did)

        logger.info(
            "connection cleanup completed",
            connection_id=connection_id,
            did=did,
            pending_requests=len(conn_info.pending_requests) if conn_info else 0,
        )

    async def _cleanup_loop(self) -> None:
        """Periodic cleanup of stale connections and requests with enhanced memory leak prevention."""
        while True:
            try:
                await asyncio.sleep(60)  # Cleanup every minute

                current_time = asyncio.get_event_loop().time()
                stale_connections = []
                orphaned_requests = []

                # Find stale connections
                for conn_id, conn_info in self.connections.items():
                    # Check if connection is stale (no ping for 2x ping interval)
                    if (
                        current_time - conn_info.last_ping
                        > self.config.ping_interval * 2
                    ):
                        stale_connections.append(conn_id)

                # Find orphaned request mappings (requests without valid connections)
                # Also find old requests (older than 10 minutes)
                request_timeout = 600.0  # 10 minutes
                for request_id, connection_id in list(self.request_routing.items()):
                    is_orphaned = connection_id not in self.connections
                    is_expired = False

                    # Check if request has expired based on timestamp
                    request_time = self.request_timestamps.get(request_id)
                    if request_time:
                        is_expired = (current_time - request_time) > request_timeout

                    if is_orphaned or is_expired:
                        orphaned_requests.append(request_id)
                        reason = "orphaned" if is_orphaned else "expired"
                        logger.warning(
                            f"Found {reason} request mapping",
                            request_id=request_id,
                            connection_id=connection_id,
                            age_seconds=current_time - request_time
                            if request_time
                            else "unknown",
                        )

                # Clean up orphaned requests
                cleanup_count = 0
                for request_id in orphaned_requests:
                    self.request_routing.pop(request_id, None)
                    self.request_timestamps.pop(request_id, None)
                    cleanup_count += 1

                    # Also clean from pending requests in all connections
                    for conn_info in self.connections.values():
                        conn_info.pending_requests.discard(request_id)

                # Close stale connections
                for conn_id in stale_connections:
                    conn_info = self.connections.get(conn_id)
                    if conn_info:
                        logger.warning(
                            "Closing stale connection",
                            connection_id=conn_id,
                            pending_requests=len(conn_info.pending_requests),
                        )
                        await conn_info.websocket.close()

                # Cleanup decoder chunks
                decoder_cleanup_count = self.decoder.cleanup_stale_chunks()

                # Log cleanup statistics
                if cleanup_count > 0 or decoder_cleanup_count > 0 or stale_connections:
                    logger.info(
                        "Cleanup completed",
                        orphaned_requests=cleanup_count,
                        stale_connections=len(stale_connections),
                        decoder_chunks_cleaned=decoder_cleanup_count,
                        active_connections=len(self.connections),
                        active_request_mappings=len(self.request_routing),
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in cleanup loop", error=str(e))

    async def get_connection_stats(self) -> dict[str, Any]:
        """Get connection statistics."""
        total_connections = len(self.connections)
        authenticated_connections = sum(
            1 for c in self.connections.values() if c.authenticated
        )
        total_pending_requests = sum(
            len(c.pending_requests) for c in self.connections.values()
        )

        base_stats = {
            "total_connections": total_connections,
            "authenticated_connections": authenticated_connections,
            "total_pending_requests": total_pending_requests,
            "request_routing_entries": len(self.request_routing),
            "smart_routing_enabled": self.config.enable_smart_routing,
            "connection_pool": self.connection_pool.get_pool_stats(),
        }

        # Add service registry stats if available
        if self.service_registry:
            service_stats = self.service_registry.get_stats()
            # Return service mappings as the main service_registry field for compatibility
            base_stats["service_registry"] = service_stats.get("service_mappings", {})
            # Add detailed stats as separate field
            base_stats["service_registry_stats"] = service_stats

        # Add database stats if available
        if self.db_adapter:
            try:
                db_stats = self.db_adapter.get_connection_stats()
                base_stats["database"] = db_stats
            except Exception as e:
                base_stats["database"] = {"error": str(e)}

        return base_stats
