"""Gateway HTTP server implementation."""

import asyncio
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ..common.auth import AuthManager
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
from .websocket_manager import WebSocketManager

logger = get_logger(__name__)


class GatewayServer:
    """HTTP Gateway server that forwards requests to receivers via WebSocket."""

    def __init__(self, config: GatewayConfig) -> None:
        """
        Initialize Gateway server.

        Args:
            config: Gateway configuration
        """
        self.config = config
        self.auth_manager = AuthManager(config.auth)

        # Initialize components
        self.websocket_manager = WebSocketManager(config, self.auth_manager)
        self.request_mapper = RequestMapper(config.chunk_size)
        self.response_handler = ResponseHandler(config.timeout)

        # Create FastAPI app
        self.app = self._create_app()

        # Setup callbacks
        self.websocket_manager.set_response_callback(self._handle_websocket_response)
        self.websocket_manager.set_error_callback(self._handle_websocket_error)

        self._server_task: asyncio.Task | None = None

        logger.info("Gateway server initialized")

    def _create_app(self) -> FastAPI:
        """Create and configure FastAPI application."""
        app = FastAPI(
            title="ANP Proxy Gateway",
            description="HTTP Gateway for Agent Network Proxy",
            version="1.0.0",
            docs_url="/docs",
            redoc_url="/redoc"
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
            stats = self.get_stats()
            return {
                "status": "healthy",
                "stats": stats
            }

        @app.get("/stats")
        async def get_statistics():
            """Get server statistics."""
            return self.get_stats()

        # Catch-all route for proxying
        @app.api_route(
            "/{path:path}",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
            include_in_schema=False
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
        Handle incoming HTTP request.

        Args:
            request: HTTP request to handle

        Returns:
            HTTP response
        """
        try:
            # Map HTTP request to ANPX message(s)
            request_id, anpx_messages = await self.request_mapper.map_request(request)

            # Create pending request for response tracking
            pending_request = await self.response_handler.create_pending_request(
                request_id, self.config.timeout
            )

            # Send request to receiver via WebSocket
            success = False
            for message in anpx_messages:
                if await self.websocket_manager.send_request(request_id, message):
                    success = True
                else:
                    break

            if not success:
                # No receiver available
                error_messages = self.request_mapper.create_error_response_message(
                    request_id,
                    status=503,
                    message="No receiver available"
                )
                return await self._convert_anpx_to_response(error_messages[0])

            # Wait for response
            try:
                response = await pending_request.wait()

                logger.debug(
                    "Request completed successfully",
                    request_id=request_id,
                    method=request.method,
                    path=str(request.url.path),
                    status=response.status_code
                )

                return response

            except TimeoutError:
                logger.warning("Request timed out", request_id=request_id)
                return Response(
                    content="Request timeout",
                    status_code=504,
                    headers={"content-type": "text/plain"}
                )

        except Exception as e:
            logger.error("Failed to handle HTTP request", error=str(e))
            return Response(
                content=f"Internal server error: {str(e)}",
                status_code=500,
                headers={"content-type": "text/plain"}
            )

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
            headers=resp_meta.headers if resp_meta else {}
        )

    async def start(self) -> None:
        """Start the gateway server."""
        try:
            # Start WebSocket server first
            await self.websocket_manager.start_server()

            # Start HTTP server
            config = uvicorn.Config(
                app=self.app,
                host=self.config.host,
                port=self.config.port,
                log_config=None,  # We handle logging ourselves
                access_log=False,
                server_header=False,
                date_header=False
            )

            server = uvicorn.Server(config)
            self._server_task = asyncio.create_task(server.serve())

            logger.info(
                "Gateway server started",
                http_host=self.config.host,
                http_port=self.config.port,
                wss_host=self.config.wss_host,
                wss_port=self.config.wss_port
            )

        except Exception as e:
            logger.error("Failed to start gateway server", error=str(e))
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop the gateway server."""
        logger.info("Stopping gateway server")

        # Stop HTTP server
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass

        # Stop WebSocket server
        await self.websocket_manager.stop_server()

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

    def get_stats(self) -> dict[str, Any]:
        """Get server statistics."""
        ws_stats = self.websocket_manager.get_connection_stats()
        response_stats = self.response_handler.get_stats()

        return {
            "gateway": {
                "host": self.config.host,
                "port": self.config.port,
                "wss_host": self.config.wss_host,
                "wss_port": self.config.wss_port
            },
            "websocket": ws_stats,
            "response_handler": response_stats,
            "auth_enabled": self.config.auth.enabled,
            "tls_enabled": self.config.tls.enabled
        }
