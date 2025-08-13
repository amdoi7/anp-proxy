"""Middleware for Gateway server."""

import time
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..common.log_base import get_logger
from ..common.utils import RateLimiter

logger = get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for request/response logging."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and log details."""
        start_time = time.time()
        client_ip = request.client.host if request.client else "unknown"

        logger.info(
            "Request started",
            method=request.method,
            url=str(request.url),
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent", "unknown"),
        )

        try:
            response = await call_next(request)

            process_time = time.time() - start_time
            logger.info(
                "Request completed",
                method=request.method,
                url=str(request.url),
                status_code=response.status_code,
                process_time=f"{process_time:.3f}s",
                client_ip=client_ip,
            )

            # Add processing time header
            response.headers["X-Process-Time"] = f"{process_time:.3f}"

            return response

        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                "Request failed",
                method=request.method,
                url=str(request.url),
                error=str(e),
                process_time=f"{process_time:.3f}s",
                client_ip=client_ip,
            )
            raise


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for rate limiting."""

    def __init__(self, app, max_requests: int = 100, window_seconds: float = 60.0):
        """
        Initialize rate limiting middleware.

        Args:
            app: ASGI application
            max_requests: Maximum requests per window
            window_seconds: Time window in seconds
        """
        super().__init__(app)
        self.rate_limiter = RateLimiter(max_requests, window_seconds)
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with rate limiting."""
        client_ip = request.client.host if request.client else "unknown"

        if not self.rate_limiter.is_allowed(client_ip):
            logger.warning(
                "Rate limit exceeded",
                client_ip=client_ip,
                max_requests=self.max_requests,
                window_seconds=self.window_seconds,
            )

            from starlette.responses import JSONResponse

            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "max_requests": self.max_requests,
                    "window_seconds": self.window_seconds,
                },
                headers={
                    "Retry-After": str(int(self.window_seconds)),
                    "X-RateLimit-Limit": str(self.max_requests),
                    "X-RateLimit-Window": str(self.window_seconds),
                },
            )

        return await call_next(request)


class CORSMiddleware(BaseHTTPMiddleware):
    """Middleware for CORS handling."""

    def __init__(
        self,
        app,
        allow_origins: list = None,
        allow_methods: list = None,
        allow_headers: list = None,
        allow_credentials: bool = True,
    ):
        """
        Initialize CORS middleware.

        Args:
            app: ASGI application
            allow_origins: Allowed origins (default: ["*"])
            allow_methods: Allowed methods (default: all)
            allow_headers: Allowed headers (default: common headers)
            allow_credentials: Whether to allow credentials
        """
        super().__init__(app)
        self.allow_origins = allow_origins or ["*"]
        self.allow_methods = allow_methods or [
            "GET",
            "POST",
            "PUT",
            "DELETE",
            "OPTIONS",
            "HEAD",
            "PATCH",
        ]
        self.allow_headers = allow_headers or [
            "accept",
            "accept-encoding",
            "authorization",
            "content-type",
            "dnt",
            "origin",
            "user-agent",
            "x-csrftoken",
            "x-requested-with",
        ]
        self.allow_credentials = allow_credentials

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with CORS headers."""
        origin = request.headers.get("origin")

        # Handle preflight requests
        if request.method == "OPTIONS":
            response = Response()
            response.status_code = 200
        else:
            response = await call_next(request)

        # Add CORS headers
        if origin and (self.allow_origins == ["*"] or origin in self.allow_origins):
            response.headers["Access-Control-Allow-Origin"] = origin
        elif self.allow_origins == ["*"]:
            response.headers["Access-Control-Allow-Origin"] = "*"

        response.headers["Access-Control-Allow-Methods"] = ", ".join(self.allow_methods)
        response.headers["Access-Control-Allow-Headers"] = ", ".join(self.allow_headers)

        if self.allow_credentials:
            response.headers["Access-Control-Allow-Credentials"] = "true"

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware for security headers."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Add security headers to response."""
        response = await call_next(request)

        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Add server header
        response.headers["Server"] = "ANP-Proxy/1.0"

        return response
