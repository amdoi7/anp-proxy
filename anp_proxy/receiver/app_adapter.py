"""ASGI application adapter for Receiver."""

from collections.abc import Callable
from typing import Any

from ..common.log_base import get_logger
from ..protocol import ANPXMessage, HTTPMeta, ResponseMeta

logger = get_logger(__name__)


class MockResponse:
    """Mock response object to replace httpx.Response."""

    def __init__(self, status_code: int, headers: dict[str, str], content: bytes):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.reason_phrase = self._get_reason_phrase(status_code)

    def _get_reason_phrase(self, status_code: int) -> str:
        """Get HTTP reason phrase for status code."""
        reasons = {
            200: "OK",
            201: "Created",
            400: "Bad Request",
            404: "Not Found",
            500: "Internal Server Error",
        }
        return reasons.get(status_code, "Unknown")


class ASGIAdapter:
    """Adapter for calling ASGI applications from ANPX messages."""

    def __init__(self, app: Any, base_url: str = "http://localhost") -> None:
        """
        Initialize ASGI adapter.

        Args:
            app: ASGI application instance
            base_url: Base URL for internal requests
        """
        self.app = app
        self.base_url = base_url.rstrip("/")
        # No need for httpx client as we call ASGI directly

        logger.info("ASGI adapter initialized", base_url=base_url)

    async def process_request(self, message: ANPXMessage) -> ANPXMessage:
        """
        Process ANPX request message through ASGI app.

        Args:
            message: ANPX request message

        Returns:
            ANPX response message
        """
        try:
            # Extract request components
            request_id = message.get_request_id()
            http_meta = message.get_http_meta()
            body = message.get_http_body()

            if not request_id:
                raise ValueError("Request missing request_id")
            if not http_meta:
                raise ValueError("Request missing http_meta")

            logger.debug(
                "Processing ASGI request",
                request_id=request_id,
                method=http_meta.method,
                path=http_meta.path,
            )

            # Make internal ASGI request
            response = await self._make_internal_request(http_meta, body)

            # Convert to ANPX response
            anpx_response = await self._convert_to_anpx_response(request_id, response)

            logger.debug(
                "ASGI request processed",
                request_id=request_id,
                status=response.status_code,
                response_size=len(response.content),
            )

            return anpx_response

        except Exception as e:
            logger.error("Failed to process ASGI request", error=str(e))
            return self._create_error_response(
                message.get_request_id() or "unknown", str(e)
            )

    async def _make_internal_request(
        self, http_meta: HTTPMeta, body: bytes | None
    ) -> "MockResponse":
        """
        Make internal ASGI request to app.

        Args:
            http_meta: HTTP request metadata
            body: Request body

        Returns:
            Mock response object with ASGI app response
        """
        from urllib.parse import quote

        # Construct ASGI scope
        scope = {
            "type": "http",
            "method": http_meta.method,
            "path": http_meta.path,
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("localhost", 8000),
        }

        # Add query string
        if http_meta.query:
            query_pairs = []
            for key, value in http_meta.query.items():
                query_pairs.append(f"{quote(key)}={quote(value)}")
            scope["query_string"] = "&".join(query_pairs).encode()

        # Add headers
        for key, value in http_meta.headers.items():
            scope["headers"].append((key.lower().encode(), value.encode()))

        # Prepare receive callable
        async def receive():
            return {"type": "http.request", "body": body or b"", "more_body": False}

        # Prepare send callable to capture response
        response_data = {"status": 500, "headers": [], "body": b""}

        async def send(message):
            if message["type"] == "http.response.start":
                response_data["status"] = message["status"]
                response_data["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                response_data["body"] += message.get("body", b"")

        # Call ASGI app
        await self.app(scope, receive, send)

        # Create mock response
        return MockResponse(
            status_code=response_data["status"],
            headers=dict((k.decode(), v.decode()) for k, v in response_data["headers"]),
            content=response_data["body"],
        )

    async def _convert_to_anpx_response(
        self, request_id: str, response: MockResponse
    ) -> ANPXMessage:
        """
        Convert mock response to ANPX response message.

        Args:
            request_id: Original request ID
            response: Mock response from ASGI app

        Returns:
            ANPX response message
        """
        from ..protocol import ANPXHeader, MessageType, TLVTag

        # Create response metadata
        resp_meta = ResponseMeta(
            status=response.status_code,
            reason=response.reason_phrase,
            headers=dict(response.headers),
        )

        # Create ANPX response message
        header = ANPXHeader(message_type=MessageType.HTTP_RESPONSE)
        anpx_response = ANPXMessage(header=header)

        # Add TLV fields
        anpx_response.add_tlv_field(TLVTag.REQUEST_ID, request_id)
        anpx_response.add_tlv_field(TLVTag.RESP_META, resp_meta.to_json())

        # Add response body if present
        if response.content:
            anpx_response.add_tlv_field(TLVTag.HTTP_BODY, response.content)

        return anpx_response

    def _create_error_response(
        self, request_id: str, error_message: str
    ) -> ANPXMessage:
        """
        Create error response message.

        Args:
            request_id: Request ID
            error_message: Error description

        Returns:
            ANPX error response message
        """
        from ..protocol import ANPXHeader, MessageType, TLVTag

        # Create error response metadata
        resp_meta = ResponseMeta(
            status=500,
            reason="Internal Server Error",
            headers={"content-type": "text/plain"},
        )

        # Create ANPX response message
        header = ANPXHeader(message_type=MessageType.HTTP_RESPONSE)
        anpx_response = ANPXMessage(header=header)

        # Add TLV fields
        anpx_response.add_tlv_field(TLVTag.REQUEST_ID, request_id)
        anpx_response.add_tlv_field(TLVTag.RESP_META, resp_meta.to_json())
        anpx_response.add_tlv_field(TLVTag.HTTP_BODY, error_message.encode("utf-8"))

        return anpx_response

    async def close(self) -> None:
        """Close the ASGI adapter and cleanup resources."""
        # No HTTP client to close since we call ASGI directly
        logger.info("ASGI adapter closed")


class MockASGIApp:
    """Mock ASGI application for testing."""

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        """ASGI application interface."""
        if scope["type"] == "http":
            await self._handle_http(scope, receive, send)
        else:
            # Unsupported scope type
            await send({
                "type": "http.response.start",
                "status": 404,
                "headers": [(b"content-type", b"text/plain")],
            })
            await send({"type": "http.response.body", "body": b"Not Found"})

    async def _handle_http(
        self, scope: dict, receive: Callable, send: Callable
    ) -> None:
        """Handle HTTP request."""
        method = scope["method"]
        path = scope["path"]

        # Simple echo response
        response_body = f"Echo: {method} {path}".encode()

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", str(len(response_body)).encode()),
            ],
        })

        await send({"type": "http.response.body", "body": response_body})
