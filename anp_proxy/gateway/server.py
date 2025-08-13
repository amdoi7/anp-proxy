"""Gateway HTTP server implementation."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ..common.config import GatewayConfig
from ..common.log_base import get_logger
from ..common.utils import GracefulShutdown
from .middleware import (
    CORSMiddleware,
    LoggingMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from .request_mapper import RequestMapper
from .response_handler import ResponseHandler
from .smart_router import SmartRouter
from .websocket_manager import WebSocketManager

logger = get_logger(__name__)


class MessageBatcher:
    """
    Message batcher for optimizing WebSocket message sending with batching.
    Reduces system calls by combining multiple messages into batches.
    """

    def __init__(self, max_batch_size: int = 10, batch_timeout: float = 0.01):
        """
        Initialize message batcher.

        Args:
            max_batch_size: Maximum messages per batch
            batch_timeout: Maximum time to wait before sending partial batch (seconds)
        """
        self.max_batch_size = max_batch_size
        self.batch_timeout = batch_timeout
        self.pending_batches: dict[str, list] = {}  # connection_id -> [messages]
        self.batch_timers: dict[str, asyncio.Handle] = {}  # connection_id -> timer

    async def add_message(self, connection_id: str, message, send_callback):
        """
        Add message to batch for a connection.

        Args:
            connection_id: Target connection ID
            message: Message to send
            send_callback: Function to call when sending batch
        """
        if connection_id not in self.pending_batches:
            self.pending_batches[connection_id] = []

        batch = self.pending_batches[connection_id]
        batch.append((message, send_callback))

        # Cancel existing timer
        if connection_id in self.batch_timers:
            self.batch_timers[connection_id].cancel()

        # Send immediately if batch is full
        if len(batch) >= self.max_batch_size:
            await self._send_batch(connection_id)
        else:
            # Set timer for partial batch
            loop = asyncio.get_event_loop()
            timer = loop.call_later(
                self.batch_timeout,
                lambda: asyncio.create_task(self._send_batch(connection_id)),
            )
            self.batch_timers[connection_id] = timer

    async def _send_batch(self, connection_id: str):
        """
        Send all pending messages for a connection.
        """
        if connection_id not in self.pending_batches:
            return

        batch = self.pending_batches.pop(connection_id, [])
        if connection_id in self.batch_timers:
            self.batch_timers[connection_id].cancel()
            del self.batch_timers[connection_id]

        if not batch:
            return

        # Send all messages in batch
        for message, send_callback in batch:
            try:
                await send_callback(message)
            except Exception as e:
                logger.error(
                    "Failed to send message in batch",
                    connection_id=connection_id,
                    error=str(e),
                )

    async def flush_all_batches(self):
        """
        Send all pending batches immediately.
        """
        connection_ids = list(self.pending_batches.keys())
        for connection_id in connection_ids:
            await self._send_batch(connection_id)

    def get_batch_stats(self) -> dict:
        """
        Get batching statistics.
        """
        total_pending = sum(len(batch) for batch in self.pending_batches.values())
        return {
            "pending_batches": len(self.pending_batches),
            "total_pending_messages": total_pending,
            "active_timers": len(self.batch_timers),
        }


class GatewayServer:
    """HTTP Gateway server that forwards requests to receivers via WebSocket."""

    def __init__(self, config: GatewayConfig) -> None:
        """
        Initialize Gateway server.

        Args:
            config: Gateway configuration
        """
        self.config = config

        # Initialize components
        self.websocket_manager = WebSocketManager(config)
        self.request_mapper = RequestMapper(config.chunk_size)
        self.response_handler = ResponseHandler(config.timeout)

        # Smart Router
        self.smart_router: SmartRouter | None = None

        # Create FastAPI app
        self.app = self._create_app()

        # Message batching for performance optimization
        self.message_batcher = MessageBatcher(
            max_batch_size=getattr(config, "message_batch_size", 10),
            batch_timeout=getattr(config, "batch_timeout", 0.01),
        )

        # Setup callbacks
        self.websocket_manager.set_response_callback(self._handle_websocket_response)
        self.websocket_manager.set_error_callback(self._handle_websocket_error)

        self._server_task: asyncio.Task | None = None

        logger.info(
            "Gateway server initialized", smart_routing=config.enable_smart_routing
        )

        # Suppress uvicorn error logs for CancelledError
        uvicorn_error_logger = logging.getLogger("uvicorn.error")
        uvicorn_error_logger.addFilter(self._filter_cancelled_errors)

    def _filter_cancelled_errors(self, record: logging.LogRecord) -> bool:
        """Filter out CancelledError tracebacks from uvicorn logs."""
        if record.exc_info and record.exc_info[0] is not None:
            exc_type = record.exc_info[0]
            if exc_type is asyncio.CancelledError:
                return False
            # Also filter out records containing "CancelledError"
            if "CancelledError" in str(record.exc_info[1]):
                return False
        # Filter out messages containing CancelledError text
        if hasattr(record, "getMessage"):
            message = record.getMessage()
            if "CancelledError" in message or "generator didn't stop" in message:
                return False
        return True

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        """Application lifespan manager."""
        startup_success = False
        try:
            # Startup
            await self._startup()
            startup_success = True
            yield
        except asyncio.CancelledError:
            logger.debug("Application lifespan cancelled")
            if startup_success:
                # If startup was successful, we need to clean shutdown
                try:
                    await self._shutdown()
                except Exception as e:
                    logger.error("Error during emergency shutdown", error=str(e))
            raise  # Re-raise CancelledError to stop the generator properly
        except Exception as e:
            logger.error("Error during application startup", error=str(e))
            # Don't yield if startup failed
            raise
        finally:
            # Normal shutdown only if not cancelled
            if startup_success:
                try:
                    await self._shutdown()
                except asyncio.CancelledError:
                    logger.debug("Application shutdown cancelled")
                except Exception as e:
                    logger.error("Error during shutdown", error=str(e))

    def _create_app(self) -> FastAPI:
        """Create and configure FastAPI application."""
        app = FastAPI(
            title="ANP Proxy Gateway",
            description="HTTP Gateway for Agent Network Proxy",
            version="1.0.0",
            docs_url="/docs",
            redoc_url="/redoc",
            lifespan=self._lifespan,
        )

        # Add middleware (order matters - last added runs first)
        app.add_middleware(SecurityHeadersMiddleware)
        app.add_middleware(CORSMiddleware)
        app.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)
        app.add_middleware(LoggingMiddleware)

        # Add routes
        self._add_routes(app)

        return app

    def _add_routes(self, app: FastAPI) -> None:
        """Add HTTP routes to the application."""

        @app.get("/health")
        async def health_check():
            """Health check endpoint."""
            stats = await self.get_stats()
            return {"status": "healthy", "stats": stats}

        @app.get("/stats")
        async def get_statistics():
            """Get server statistics."""
            return await self.get_stats()

        # Catch-all route for proxying
        @app.api_route(
            "/{path:path}",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
            include_in_schema=False,
        )
        async def proxy_request(request: Request, path: str) -> Response:
            """
            Proxy HTTP request to receiver.

            Args:
                request: HTTP request to proxy
                path: Request path

            Returns:
                HTTP response from receiver
            """
            return await self._handle_http_request(request)

    async def _handle_http_request(self, request: Request) -> Response:
        """
        HTTP request handler with smart routing.

        Args:
            request: HTTP request to handle

        Returns:
            HTTP response
        """
        request_start_time = asyncio.get_event_loop().time()

        try:
            # Map HTTP request to ANPX message(s)
            request_id, anpx_messages = await self.request_mapper.map_request(request)

            # Create pending request for response tracking
            pending_request = await self.response_handler.create_pending_request(
                request_id, self.config.timeout
            )

            # Route request using smart router or fallback to basic routing
            success, selected_connection = await self._route_and_send_request(
                request, request_id, anpx_messages
            )

            if not success:
                # No receiver available
                error_messages = self.request_mapper.create_error_response_message(
                    request_id, status=503, message="No receiver available"
                )
                return await self._convert_anpx_to_response(error_messages[0])

            # Wait for response
            try:
                response = await pending_request.wait()

                # Calculate response time
                response_time = asyncio.get_event_loop().time() - request_start_time

                logger.debug(
                    "Request completed successfully",
                    request_id=request_id,
                    method=request.method,
                    path=str(request.url.path),
                    status=response.status_code,
                    response_time=f"{response_time:.3f}s",
                    connection=selected_connection,
                )

                return response

            except TimeoutError:
                logger.warning("Request timed out", request_id=request_id)
                return Response(
                    content="Request timeout",
                    status_code=504,
                    headers={"content-type": "text/plain"},
                )

        except Exception as e:
            logger.error("Failed to handle HTTP request", error=str(e))
            return Response(
                content=f"Internal server error: {str(e)}",
                status_code=500,
                headers={"content-type": "text/plain"},
            )

    async def _route_and_send_request(
        self, request: Request, request_id: str, anpx_messages: list
    ) -> tuple[bool, str | None]:
        """
        Route request and send to selected connection.

        Returns:
            Tuple of (success, selected_connection_id)
        """
        selected_connection = None

        # Try enterprise-grade universal smart routing first
        if self.smart_router:
            try:
                # Use the new universal fallback routing strategy
                selected_connection = (
                    await self.smart_router.route_request_with_universal_fallback(
                        request
                    )
                )

                if selected_connection:
                    # Send to specific connection using batching for efficiency
                    success = True
                    for message in anpx_messages:
                        if not await self._send_to_specific_connection_batched(
                            selected_connection, request_id, message
                        ):
                            success = False
                            break

                    if success:
                        logger.debug(
                            "Universal smart routing successful",
                            request_id=request_id,
                            connection=selected_connection,
                            host=request.headers.get("host"),
                            routing_type="universal",
                        )
                        return True, selected_connection

            except Exception as e:
                logger.error(
                    "Universal smart routing failed, falling back to basic routing",
                    error=str(e),
                    host=request.headers.get("host"),
                    path=str(request.url.path),
                )

        # No fallback routing - database-driven only
        logger.warning(
            "Smart routing failed and fallback routing is disabled",
            request_id=request_id,
            host=request.headers.get("host"),
            path=str(request.url.path),
        )
        return False, selected_connection

    async def _send_to_specific_connection_batched(
        self, connection_id: str, request_id: str, message
    ) -> bool:
        """Send ANPX message to a specific connection using batching for optimization."""
        conn_info = self.websocket_manager.connections.get(connection_id)

        if not conn_info or not conn_info.authenticated:
            logger.warning(
                "Target connection not available",
                connection_id=connection_id,
                request_id=request_id,
            )
            return False

        try:
            # Define the actual send callback
            async def send_callback(msg):
                await self.websocket_manager._send_message(conn_info, msg)

            # Add message to batch for this connection
            await self.message_batcher.add_message(
                connection_id, message, send_callback
            )

            # Track routing with timestamp for cleanup
            self.websocket_manager.request_routing[request_id] = connection_id
            self.websocket_manager.request_timestamps[request_id] = (
                asyncio.get_event_loop().time()
            )
            conn_info.pending_requests.add(request_id)

            return True

        except Exception as e:
            logger.error(
                "Failed to batch message to specific connection",
                connection_id=connection_id,
                request_id=request_id,
                error=str(e),
            )
            return False

    async def _send_to_specific_connection(
        self, connection_id: str, request_id: str, message
    ) -> bool:
        """Send ANPX message to a specific connection."""
        conn_info = self.websocket_manager.connections.get(connection_id)

        if not conn_info or not conn_info.authenticated:
            logger.warning(
                "Target connection not available",
                connection_id=connection_id,
                request_id=request_id,
            )
            return False

        try:
            # Send message
            await self.websocket_manager._send_message(conn_info, message)

            # Track routing with timestamp for cleanup
            self.websocket_manager.request_routing[request_id] = connection_id
            self.websocket_manager.request_timestamps[request_id] = (
                asyncio.get_event_loop().time()
            )
            conn_info.pending_requests.add(request_id)

            return True

        except Exception as e:
            logger.error(
                "Failed to send to specific connection",
                connection_id=connection_id,
                request_id=request_id,
                error=str(e),
            )
            return False

    async def _handle_websocket_response(self, request_id: str, message) -> None:
        """Handle response received from WebSocket."""
        await self.response_handler.handle_response(request_id, message)

    async def _handle_websocket_error(self, request_id: str, message) -> None:
        """Handle error received from WebSocket."""
        await self.response_handler.handle_error(request_id, message)

    async def _convert_anpx_to_response(self, message) -> Response:
        """Convert ANPX message to HTTP response."""
        resp_meta = message.get_resp_meta()
        body = message.get_http_body()

        return Response(
            content=body,
            status_code=resp_meta.status if resp_meta else 500,
            headers=resp_meta.headers if resp_meta else {},
        )

    async def start(self) -> None:
        """Start the gateway server with smart routing."""
        try:
            # Start HTTP server (lifespan will handle WebSocket startup)
            config = uvicorn.Config(
                app=self.app,
                host=self.config.host,
                port=self.config.port,
                log_config=None,  # We handle logging ourselves
                access_log=False,
                server_header=False,
                date_header=False,
                lifespan="on",  # Enable lifespan events
            )

            server = uvicorn.Server(config)
            self._server_task = asyncio.create_task(server.serve())

            logger.info(
                "Gateway server started",
                http_host=self.config.host,
                http_port=self.config.port,
                wss_host=self.config.wss_host,
                wss_port=self.config.wss_port,
                smart_routing=self.config.enable_smart_routing,
            )

        except Exception as e:
            logger.error("Failed to start gateway server", error=str(e))
            await self.stop()
            raise

    async def _initialize_smart_router(self) -> None:
        """Initialize smart router."""
        try:
            if not self.websocket_manager.service_registry:
                logger.warning("Service registry not available, smart routing disabled")
                return

            # Initialize smart router
            self.smart_router = SmartRouter(self.websocket_manager.service_registry)

            logger.info("Smart router initialized")

        except Exception as e:
            logger.error("Failed to initialize smart router", error=str(e))
            self.smart_router = None

    async def stop(self) -> None:
        """Stop the gateway server."""
        logger.info("Stopping gateway server")

        # Stop HTTP server (lifespan will handle WebSocket shutdown)
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                logger.debug("Server task cancelled during stop")

        logger.info("Gateway server stopped")

    async def run(self) -> None:
        """Run the gateway server with graceful shutdown."""
        with GracefulShutdown() as shutdown:
            try:
                await self.start()

                # Wait for shutdown signal
                await shutdown.wait_for_shutdown()

            finally:
                await self.stop()

    async def get_stats(self) -> dict[str, Any]:
        """Get server statistics."""
        ws_stats = await self.websocket_manager.get_connection_stats()
        response_stats = self.response_handler.get_stats()

        base_stats = {
            "gateway": {
                "host": self.config.host,
                "port": self.config.port,
                "wss_host": self.config.wss_host,
                "wss_port": self.config.wss_port,
            },
            "websocket": ws_stats,
            "response_handler": response_stats,
            "auth_enabled": self.config.auth.enabled,
            "tls_enabled": self.config.tls.enabled,
        }

        # Add smart routing statistics
        if self.config.enable_smart_routing:
            base_stats["smart_routing"] = {
                "enabled": True,
                "router_initialized": self.smart_router is not None,
            }
        else:
            base_stats["smart_routing"] = {"enabled": False}

        # Add message batching statistics
        base_stats["message_batching"] = self.message_batcher.get_batch_stats()

        return base_stats

    async def _startup(self) -> None:
        """Application startup tasks."""
        try:
            # Start WebSocket server
            await self.websocket_manager.start_server()

            # Initialize smart router if enabled
            if self.config.enable_smart_routing:
                await self._initialize_smart_router()

            logger.info("Application started successfully")
        except Exception as e:
            logger.error("Failed to start application", error=str(e))
            raise

    async def _shutdown(self) -> None:
        """Application shutdown tasks."""
        if hasattr(self, "_shutdown_called") and self._shutdown_called:
            logger.debug("Shutdown already called, skipping")
            return

        self._shutdown_called = True
        try:
            # Stop WebSocket server
            if self.websocket_manager:
                await self.websocket_manager.stop_server()

            # Cancel any running tasks
            if self._server_task:
                self._server_task.cancel()
                try:
                    await self._server_task
                except asyncio.CancelledError:
                    logger.debug("Server task cancelled during shutdown")

            logger.info("Application shutdown completed")
        except Exception as e:
            logger.error("Error during application shutdown", error=str(e))
