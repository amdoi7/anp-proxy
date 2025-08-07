"""HTTP response handling and reconstruction for Gateway."""

import asyncio
from typing import Any

from starlette.responses import Response

from ..common.log_base import get_logger
from ..protocol import ANPXMessage

logger = get_logger(__name__)


class PendingRequest:
    """Represents a pending HTTP request."""

    def __init__(self, request_id: str, timeout: float = 300.0) -> None:
        """
        Initialize pending request.

        Args:
            request_id: Unique request identifier
            timeout: Request timeout in seconds
        """
        self.request_id = request_id
        self.future: asyncio.Future[Response] = asyncio.Future()
        self.timeout = timeout
        self.timeout_handle: asyncio.Handle | None = None

        # Set timeout
        loop = asyncio.get_event_loop()
        self.timeout_handle = loop.call_later(timeout, self._timeout_callback)

    def _timeout_callback(self) -> None:
        """Handle request timeout."""
        if not self.future.done():
            self.future.set_exception(TimeoutError(f"Request {self.request_id} timed out"))

    def complete(self, response: Response) -> None:
        """Complete the request with a response."""
        if self.timeout_handle:
            self.timeout_handle.cancel()

        if not self.future.done():
            self.future.set_result(response)

    def error(self, exception: Exception) -> None:
        """Complete the request with an error."""
        if self.timeout_handle:
            self.timeout_handle.cancel()

        if not self.future.done():
            self.future.set_exception(exception)

    async def wait(self) -> Response:
        """Wait for request completion."""
        return await self.future


class ResponseHandler:
    """Handles HTTP response reconstruction from ANPX messages."""

    def __init__(self, timeout: float = 300.0) -> None:
        """
        Initialize response handler.

        Args:
            timeout: Default request timeout in seconds
        """
        self.pending_requests: dict[str, PendingRequest] = {}
        self.default_timeout = timeout

        logger.info("Response handler initialized", timeout=timeout)

    async def create_pending_request(
        self,
        request_id: str,
        timeout: float | None = None
    ) -> PendingRequest:
        """
        Create a new pending request.

        Args:
            request_id: Unique request identifier
            timeout: Request timeout (uses default if None)

        Returns:
            PendingRequest object
        """
        if request_id in self.pending_requests:
            logger.warning("Request ID already exists", request_id=request_id)
            raise ValueError(f"Request ID {request_id} already exists")

        timeout = timeout or self.default_timeout
        pending_request = PendingRequest(request_id, timeout)
        self.pending_requests[request_id] = pending_request

        logger.debug("Created pending request", request_id=request_id, timeout=timeout)
        return pending_request

    async def handle_response(self, request_id: str, message: ANPXMessage) -> None:
        """
        Handle response message from receiver.

        Args:
            request_id: Request identifier
            message: ANPX response message
        """
        pending_request = self.pending_requests.pop(request_id, None)
        if not pending_request:
            logger.warning("Received response for unknown request", request_id=request_id)
            return

        try:
            response = await self._convert_to_http_response(message)
            pending_request.complete(response)

            logger.debug(
                "Response handled successfully",
                request_id=request_id,
                status=response.status_code
            )

        except Exception as e:
            logger.error("Failed to handle response", request_id=request_id, error=str(e))
            pending_request.error(e)

    async def handle_error(self, request_id: str, message: ANPXMessage) -> None:
        """
        Handle error message from receiver.

        Args:
            request_id: Request identifier
            message: ANPX error message
        """
        pending_request = self.pending_requests.pop(request_id, None)
        if not pending_request:
            logger.warning("Received error for unknown request", request_id=request_id)
            return

        try:
            # Extract error message from body
            error_body = message.get_http_body()
            error_text = error_body.decode('utf-8') if error_body else "Unknown error"

            # Create error response
            response = Response(
                content=error_text,
                status_code=500,
                headers={"content-type": "text/plain"}
            )

            pending_request.complete(response)

            logger.debug("Error handled", request_id=request_id, error=error_text)

        except Exception as e:
            logger.error("Failed to handle error", request_id=request_id, error=str(e))
            pending_request.error(e)

    async def handle_timeout(self, request_id: str, error_message: str) -> None:
        """
        Handle request timeout or connection loss.

        Args:
            request_id: Request identifier
            error_message: Error description
        """
        pending_request = self.pending_requests.pop(request_id, None)
        if not pending_request:
            return

        # Create timeout response
        response = Response(
            content=f"Gateway Error: {error_message}",
            status_code=504,
            headers={"content-type": "text/plain"}
        )

        pending_request.complete(response)
        logger.warning("Request timed out", request_id=request_id, error=error_message)

    async def _convert_to_http_response(self, message: ANPXMessage) -> Response:
        """
        Convert ANPX response message to HTTP response.

        Args:
            message: ANPX response message

        Returns:
            Starlette Response object
        """
        # Extract response metadata
        resp_meta = message.get_resp_meta()
        if not resp_meta:
            raise ValueError("Response message missing metadata")

        # Extract response body
        body = message.get_http_body()

        # Create response
        response = Response(
            content=body,
            status_code=resp_meta.status,
            headers=resp_meta.headers
        )

        return response

    def cleanup_stale_requests(self, max_age: float = 600.0) -> int:
        """
        Clean up stale pending requests.

        Args:
            max_age: Maximum age for pending requests in seconds

        Returns:
            Number of requests cleaned up
        """
        import time
        time.time()
        stale_requests = []

        for request_id, pending_request in self.pending_requests.items():
            # Check if request is stale (this is a simple check)
            if not pending_request.future.done():
                # For a more accurate check, we'd need to track creation time
                # For now, just clean up requests with cancelled timeout handles
                if pending_request.timeout_handle and pending_request.timeout_handle.cancelled():
                    stale_requests.append(request_id)

        # Clean up stale requests
        for request_id in stale_requests:
            pending_request = self.pending_requests.pop(request_id, None)
            if pending_request and not pending_request.future.done():
                pending_request.error(Exception("Request cleaned up as stale"))

        if stale_requests:
            logger.info("Cleaned up stale requests", count=len(stale_requests))

        return len(stale_requests)

    def get_stats(self) -> dict[str, Any]:
        """Get response handler statistics."""
        return {
            "pending_requests": len(self.pending_requests),
            "default_timeout": self.default_timeout
        }
