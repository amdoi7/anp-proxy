"""HTTP request mapping and packaging for Gateway."""

import uuid

from starlette.requests import Request

from ..common.log_base import get_logger
from ..protocol import ANPXEncoder, ANPXMessage

logger = get_logger(__name__)


class RequestMapper:
    """Maps HTTP requests to ANPX protocol messages."""

    def __init__(self, chunk_size: int = 64 * 1024) -> None:
        """
        Initialize request mapper.

        Args:
            chunk_size: Maximum chunk size for large requests
        """
        self.encoder = ANPXEncoder(chunk_size)
        logger.info("Request mapper initialized", chunk_size=chunk_size)

    async def map_request(self, request: Request) -> tuple[str, list[ANPXMessage]]:
        """
        Map HTTP request to ANPX message(s).

        Args:
            request: Starlette HTTP request

        Returns:
            Tuple of (request_id, list of ANPX messages)
        """
        try:
            # Generate unique request ID
            request_id = str(uuid.uuid4())

            # Extract request components
            method = request.method
            path = self._extract_path(request)
            headers = self._extract_headers(request)
            query = self._extract_query(request)
            body = await self._extract_body(request)

            logger.debug(
                "Mapping HTTP request",
                request_id=request_id,
                method=method,
                path=path,
                body_size=len(body) if body else 0,
            )

            # Encode to ANPX messages
            messages = self.encoder.encode_http_request(
                method=method,
                path=path,
                headers=headers,
                query=query,
                body=body,
                request_id=request_id,
            )

            logger.debug(
                "HTTP request mapped to ANPX",
                request_id=request_id,
                message_count=len(messages),
                is_chunked=len(messages) > 1,
            )

            return request_id, messages

        except Exception as e:
            logger.error("Failed to map HTTP request", error=str(e))
            raise

    def _extract_path(self, request: Request) -> str:
        """Extract request path with query string."""
        path = request.url.path
        if request.url.fragment:
            path += f"#{request.url.fragment}"
        return path

    def _extract_headers(self, request: Request) -> dict[str, str]:
        """Extract HTTP headers."""
        headers = {}

        for name, value in request.headers.items():
            # Convert header names to lowercase for consistency
            headers[name.lower()] = value

        # Add client information if available
        if request.client:
            headers["x-forwarded-for"] = request.client.host
            if hasattr(request.client, "port"):
                headers["x-forwarded-port"] = str(request.client.port)

        return headers

    def _extract_query(self, request: Request) -> dict[str, str]:
        """Extract query parameters."""
        query = {}

        for key, value in request.query_params.items():
            query[key] = value

        return query

    async def _extract_body(self, request: Request) -> bytes | None:
        """Extract request body."""
        try:
            # Check if request has body
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) == 0:
                return None

            if request.method in ["GET", "HEAD", "DELETE"]:
                # These methods typically don't have bodies
                return None

            # Read body
            body = await request.body()
            return body if body else None

        except Exception as e:
            logger.warning("Failed to extract request body", error=str(e))
            return None

    def create_error_response_message(
        self,
        request_id: str,
        status: int,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> list[ANPXMessage]:
        """
        Create an error response message.

        Args:
            request_id: Request ID to respond to
            status: HTTP status code
            message: Error message
            headers: Optional response headers

        Returns:
            List containing single error response message
        """
        try:
            response_headers = headers or {}
            response_headers["content-type"] = "application/json"

            error_body = {"error": message, "status": status, "request_id": request_id}

            import json

            body_bytes = json.dumps(error_body).encode("utf-8")

            messages = self.encoder.encode_http_response(
                status=status,
                reason=self._get_status_reason(status),
                headers=response_headers,
                body=body_bytes,
                request_id=request_id,
            )

            return messages

        except Exception as e:
            logger.error("Failed to create error response", error=str(e))
            # Return basic error message
            basic_message = self.encoder.encode_error(
                f"Internal error: {e}", request_id
            )
            return [basic_message]

    def _get_status_reason(self, status: int) -> str:
        """Get HTTP status reason phrase."""
        status_reasons = {
            200: "OK",
            201: "Created",
            202: "Accepted",
            204: "No Content",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            405: "Method Not Allowed",
            408: "Request Timeout",
            413: "Payload Too Large",
            429: "Too Many Requests",
            500: "Internal Server Error",
            502: "Bad Gateway",
            503: "Service Unavailable",
            504: "Gateway Timeout",
        }

        return status_reasons.get(status, "Unknown")
