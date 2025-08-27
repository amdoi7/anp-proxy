"""Utility functions for ANP Proxy."""

import asyncio
import signal
import socket
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from .db_base import execute_query
from .log_base import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def retry_async(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """
    Decorator for async functions with retry logic.

    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Backoff multiplier for delay
        exceptions: Tuple of exceptions to catch and retry
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception = None
            current_delay = delay

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"Attempt {attempt + 1} failed, retrying in {current_delay}s",
                            function=func.__name__,
                            error=str(e),
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"All {max_attempts} attempts failed",
                            function=func.__name__,
                            error=str(e),
                        )

            raise last_exception

        return wrapper

    return decorator


def timeout_async(seconds: float) -> Callable:
    """
    Decorator to add timeout to async functions.

    Args:
        seconds: Timeout in seconds
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)
            except TimeoutError:
                logger.error(
                    f"Function {getattr(func, '__name__', repr(func))} timed out after {seconds}s"
                )
                raise

        return wrapper

    return decorator


def find_free_port(host: str = "127.0.0.1", start_port: int = 8000) -> int:
    """
    Find a free port starting from start_port.

    Args:
        host: Host to bind to
        start_port: Starting port number

    Returns:
        Available port number
    """
    port = start_port
    while port < 65535:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
                return port
        except OSError:
            port += 1

    raise RuntimeError(f"No free port found starting from {start_port}")


def parse_module_attr(module_string: str) -> tuple[str, str]:
    """
    Parse module:attribute string.

    Args:
        module_string: String like "myapp.main:app"

    Returns:
        Tuple of (module_name, attribute_name)
    """
    if ":" not in module_string:
        raise ValueError("Module string must be in format 'module:attribute'")

    module_name, attr_name = module_string.split(":", 1)
    return module_name.strip(), attr_name.strip()


async def import_app(module_string: str) -> Any:
    """
    Dynamically import an ASGI application.

    Args:
        module_string: Module string like "myapp.main:app"

    Returns:
        Imported ASGI application
    """
    import importlib

    module_name, attr_name = parse_module_attr(module_string)

    try:
        module = importlib.import_module(module_name)
        app = getattr(module, attr_name)

        logger.info(f"Successfully imported app from {module_string}")
        return app

    except ImportError as e:
        logger.error(f"Failed to import module {module_name}: {e}")
        raise
    except AttributeError as e:
        logger.error(f"Failed to get attribute {attr_name} from {module_name}: {e}")
        raise


class GracefulShutdown:
    """Context manager for graceful shutdown handling."""

    def __init__(self) -> None:
        """Initialize shutdown handler."""
        self.shutdown_event = asyncio.Event()
        self.tasks: list[asyncio.Task] = []

    def __enter__(self) -> "GracefulShutdown":
        """Enter context manager."""
        # Register signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._signal_handler)

        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager."""
        # Restore default signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, signal.SIG_DFL)

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.shutdown_event.set()

    def add_task(self, task: asyncio.Task) -> None:
        """Add a task to be cancelled on shutdown."""
        self.tasks.append(task)

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        await self.shutdown_event.wait()

    async def cleanup(self) -> None:
        """Cancel all tracked tasks."""
        if self.tasks:
            logger.info(f"Cancelling {len(self.tasks)} tasks")

            for task in self.tasks:
                if not task.done():
                    task.cancel()

            # Wait for tasks to complete cancellation
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)


class RateLimiter:
    """Simple rate limiter implementation."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        """
        Initialize rate limiter.

        Args:
            max_requests: Maximum requests per window
            window_seconds: Time window in seconds
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = {}

    def is_allowed(self, key: str) -> bool:
        """
        Check if request is allowed for the given key.

        Args:
            key: Rate limiting key (e.g., client IP)

        Returns:
            True if request is allowed, False otherwise
        """
        now = time.time()
        window_start = now - self.window_seconds

        # Initialize or clean up old requests
        if key not in self.requests:
            self.requests[key] = []
        else:
            # Remove requests outside current window
            self.requests[key] = [
                req_time for req_time in self.requests[key] if req_time > window_start
            ]

        # Check if under limit
        if len(self.requests[key]) < self.max_requests:
            self.requests[key].append(now)
            return True

        return False

    def cleanup(self) -> None:
        """Clean up old entries."""
        now = time.time()
        window_start = now - self.window_seconds

        for key in list(self.requests.keys()):
            self.requests[key] = [
                req_time for req_time in self.requests[key] if req_time > window_start
            ]

            # Remove empty entries
            if not self.requests[key]:
                del self.requests[key]


def get_advertised_services(did: str) -> list[str]:
    """Get ordered list of proxy paths for the given DID.

    Args:
        did: DID identifier

    Returns:
        List of proxy_path strings
    """
    try:
        sql = "SELECT proxy_path FROM did_proxy_path WHERE did = %s ORDER BY created_at"
        rows = execute_query(sql, (did,))

        proxy_paths = []
        for row in rows:
            proxy_path = row["proxy_path"]

            # 从路径中提取服务前缀 - 去掉 /ad.json 后缀
            if proxy_path.endswith("/ad.json"):
                # 如果路径以 /ad.json 结尾，去掉后缀获取服务前缀
                service_prefix = proxy_path[:-8]  # 去掉 "/ad.json" (8个字符)
                proxy_paths.append(service_prefix)
            else:
                # 如果路径不以 /ad.json 结尾，直接使用
                proxy_paths.append(proxy_path)

        logger.info(
            f"Loaded service prefixes from database for DID {did}: {proxy_paths}"
        )
        return proxy_paths
    except Exception as e:
        logger.error(f"Failed to query proxy paths for DID: {e}")
        return []  # Return empty list on failure to avoid crash
