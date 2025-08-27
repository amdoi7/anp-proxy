"""
网关中间件 - 横切关注点的统一处理
- 安全检查
- 请求日志
- 错误处理
- 性能监控
"""

import re
import time
from collections.abc import Callable

from fastapi import Request, Response
from starlette.responses import JSONResponse

try:
    from ..common.log_base import get_logger
except ImportError:
    from anp_proxy.common.log_base import get_logger

logger = get_logger(__name__)


class SecurityMiddleware:
    """安全中间件 - 恶意请求检测"""

    def __init__(self):
        # 恶意路径模式 - 基于真实攻击数据
        self._malicious_patterns = {
            # WordPress 攻击
            "/wp-admin/",
            "/wp-includes/",
            "/wordpress/",
            "/xmlrpc.php",
            "/wp-config.php",
            "/wp-content/",
            "/wp-json/",
            # 管理面板攻击
            "/admin/",
            "/administrator/",
            "/phpmyadmin/",
            "/mysql/",
            "/cpanel/",
            "/webmail/",
            "/mail/",
            # 系统攻击
            "/ftp/",
            "/ssh/",
            "/telnet/",
            "/shell/",
            "/cmd/",
            "/exec/",
            "/system/",
            "/eval/",
            "/assert/",
            # 文件包含攻击
            "/include/",
            "/require/",
        }

        # 协议包装器攻击 - 需要正则匹配
        self._protocol_patterns = [
            r"/(file|data|php|expect|input|filter|zip|phar)://",
            r"/convert\.(base64|quoted-printable|uuencode)-(decode|encode)",
        ]

        self._protocol_regex = re.compile(
            "|".join(self._protocol_patterns), re.IGNORECASE
        )

        logger.info("SecurityMiddleware initialized")

    async def __call__(self, request: Request, call_next: Callable) -> Response:
        """安全检查中间件"""
        path = str(request.url.path)

        # 检查恶意模式
        if self._is_malicious_request(path):
            logger.warning(
                f"Blocked malicious request: {path} from {request.client.host if request.client else 'unknown'}"
            )
            return JSONResponse(
                {"error": "Forbidden", "message": "Request blocked by security policy"},
                status_code=403,
            )

        return await call_next(request)

    def _is_malicious_request(self, path: str) -> bool:
        """检测恶意请求 - 高性能版本"""
        if not path:
            return False

        path_lower = path.lower()

        # 快速字符串匹配 (O(1) 平均复杂度)
        for pattern in self._malicious_patterns:
            if pattern in path_lower:
                return True

        # 正则匹配 (较慢，但必要)
        if self._protocol_regex.search(path_lower):
            return True

        return False


class LoggingMiddleware:
    """日志中间件 - 请求/响应日志"""

    def __init__(self, log_requests: bool = True, log_responses: bool = False):
        self.log_requests = log_requests
        self.log_responses = log_responses

        logger.info("LoggingMiddleware initialized")

    async def __call__(self, request: Request, call_next: Callable) -> Response:
        """请求日志中间件"""
        start_time = time.time()

        # 记录请求
        if self.log_requests:
            client_ip = request.client.host if request.client else "unknown"
            logger.info(
                f"Request: {request.method} {request.url.path} from {client_ip}"
            )

        # 处理请求
        try:
            response = await call_next(request)

            # 记录响应
            duration = time.time() - start_time
            if self.log_responses:
                logger.info(f"Response: {response.status_code} in {duration:.3f}s")

            return response

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Request failed: {e} in {duration:.3f}s")
            raise


class MetricsMiddleware:
    """性能监控中间件 - 请求指标收集"""

    def __init__(self):
        # 简单的内存指标存储
        self._request_count = 0
        self._total_duration = 0.0
        self._error_count = 0
        self._status_codes = {}

        logger.info("MetricsMiddleware initialized")

    async def __call__(self, request: Request, call_next: Callable) -> Response:
        """性能监控中间件"""
        start_time = time.time()

        try:
            response = await call_next(request)

            # 记录成功指标
            duration = time.time() - start_time
            self._request_count += 1
            self._total_duration += duration

            status_code = response.status_code
            self._status_codes[status_code] = self._status_codes.get(status_code, 0) + 1

            # 添加性能头
            response.headers["X-Response-Time"] = f"{duration:.3f}s"

            return response

        except Exception:
            # 记录错误指标
            self._error_count += 1
            raise

    def get_metrics(self) -> dict:
        """获取性能指标"""
        avg_duration = (
            self._total_duration / self._request_count if self._request_count > 0 else 0
        )

        return {
            "requests_total": self._request_count,
            "requests_duration_avg": avg_duration,
            "errors_total": self._error_count,
            "status_codes": self._status_codes.copy(),
            "error_rate": self._error_count / self._request_count
            if self._request_count > 0
            else 0,
        }

    def reset_metrics(self):
        """重置指标"""
        self._request_count = 0
        self._total_duration = 0.0
        self._error_count = 0
        self._status_codes.clear()


class CORSMiddleware:
    """跨域中间件 - 处理CORS请求"""

    def __init__(
        self,
        allow_origins: list[str] = ["*"],
        allow_methods: list[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers: list[str] = ["*"],
    ):
        self.allow_origins = allow_origins or ["*"]
        self.allow_methods = allow_methods or [
            "GET",
            "POST",
            "PUT",
            "DELETE",
            "OPTIONS",
        ]
        self.allow_headers = allow_headers or ["*"]

        logger.info("CORSMiddleware initialized")

    async def __call__(self, request: Request, call_next: Callable) -> Response:
        """CORS处理中间件"""
        # 处理预检请求
        if request.method == "OPTIONS":
            response = Response()
        else:
            response = await call_next(request)

        # 添加CORS头
        origin = request.headers.get("origin")
        if origin and (origin in self.allow_origins or "*" in self.allow_origins):
            response.headers["Access-Control-Allow-Origin"] = origin
        elif "*" in self.allow_origins:
            response.headers["Access-Control-Allow-Origin"] = "*"

        response.headers["Access-Control-Allow-Methods"] = ", ".join(self.allow_methods)
        response.headers["Access-Control-Allow-Headers"] = ", ".join(self.allow_headers)
        response.headers["Access-Control-Allow-Credentials"] = "true"

        return response


class ErrorHandlingMiddleware:
    """错误处理中间件 - 统一异常处理"""

    def __init__(self, debug: bool = False):
        self.debug = debug
        logger.info("ErrorHandlingMiddleware initialized")

    async def __call__(self, request: Request, call_next: Callable) -> Response:
        """错误处理中间件"""
        try:
            return await call_next(request)

        except ValueError as e:
            logger.warning(f"Bad request: {e}")
            return JSONResponse(
                {
                    "error": "Bad Request",
                    "message": str(e) if self.debug else "Invalid request",
                },
                status_code=400,
            )

        except PermissionError as e:
            logger.warning(f"Permission denied: {e}")
            return JSONResponse(
                {"error": "Forbidden", "message": "Access denied"}, status_code=403
            )

        except FileNotFoundError as e:
            logger.warning(f"Not found: {e}")
            return JSONResponse(
                {"error": "Not Found", "message": "Resource not found"}, status_code=404
            )

        except TimeoutError as e:
            logger.error(f"Timeout: {e}")
            return JSONResponse(
                {"error": "Gateway Timeout", "message": "Request timeout"},
                status_code=504,
            )

        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return JSONResponse(
                {
                    "error": "Internal Server Error",
                    "message": str(e) if self.debug else "Server error",
                },
                status_code=500,
            )


class RoutingMiddleware:
    """路由中间件 - 在中间件层进行路由匹配"""

    def __init__(self, connection_manager=None):
        self.connection_manager = connection_manager
        logger.info("RoutingMiddleware initialized")

    async def __call__(self, request: Request, call_next: Callable) -> Response:
        """路由匹配中间件"""
        request_path = str(request.url.path)

        # 跳过健康检查、统计信息和静态资源
        if request_path in ["/health", "/stats", "/metrics", "/favicon.ico"]:
            return await call_next(request)

        # 从网关获取连接信息
        if self.connection_manager:
            conn_info = self.connection_manager.get_connection_for_path(request_path)

            if not conn_info:
                logger.warning(f"No route found for path: {request_path}")
                return JSONResponse(
                    {"error": "No route found", "path": request_path}, status_code=404
                )

            # 检查连接是否健康且未在清理中
            if not conn_info or not conn_info.is_healthy or conn_info.is_cleaning_up:
                logger.warning(
                    f"Service unavailable for path: {request_path} - connection unhealthy or cleaning up"
                )
                return JSONResponse({"error": "Service unavailable"}, status_code=503)

            # 将连接信息附加到请求状态中
            request.state.conn_info = conn_info
            request.state.websocket = conn_info.websocket

            logger.debug(f"Route matched: {request_path} -> {conn_info.connection_id}")

        return await call_next(request)


class RateLimitMiddleware:
    """限流中间件 - 基于IP的简单限流"""

    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests = {}  # ip -> [(timestamp, count), ...]

        logger.info(
            f"RateLimitMiddleware initialized: {max_requests} req/{window_seconds}s"
        )

    async def __call__(self, request: Request, call_next: Callable) -> Response:
        """限流中间件"""
        client_ip = request.client.host if request.client else "unknown"
        current_time = time.time()

        # 清理过期记录
        self._cleanup_expired_requests(current_time)

        # 检查当前IP的请求数
        if self._is_rate_limited(client_ip, current_time):
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            return JSONResponse(
                {"error": "Too Many Requests", "message": "Rate limit exceeded"},
                status_code=429,
            )

        # 记录请求
        self._record_request(client_ip, current_time)

        return await call_next(request)

    def _is_rate_limited(self, ip: str, current_time: float) -> bool:
        """检查是否超过限流"""
        if ip not in self._requests:
            return False

        # 统计窗口内的请求数
        window_start = current_time - self.window_seconds
        recent_requests = [t for t in self._requests[ip] if t > window_start]

        return len(recent_requests) >= self.max_requests

    def _record_request(self, ip: str, timestamp: float):
        """记录请求"""
        if ip not in self._requests:
            self._requests[ip] = []

        self._requests[ip].append(timestamp)

    def _cleanup_expired_requests(self, current_time: float):
        """清理过期请求记录"""
        window_start = current_time - self.window_seconds

        for ip in list(self._requests.keys()):
            self._requests[ip] = [t for t in self._requests[ip] if t > window_start]

            # 删除空记录
            if not self._requests[ip]:
                del self._requests[ip]


def create_default_middleware_stack(
    debug: bool = False, connection_manager=None
) -> list:
    """创建默认的中间件栈"""
    middleware_stack = [
        ErrorHandlingMiddleware(debug=debug),
        SecurityMiddleware(),
        RateLimitMiddleware(max_requests=1000, window_seconds=60),
        LoggingMiddleware(log_requests=True, log_responses=False),
        MetricsMiddleware(),
    ]

    # 添加路由中间件（如果提供了连接管理器）
    if connection_manager:
        middleware_stack.append(RoutingMiddleware(connection_manager))

    middleware_stack.append(CORSMiddleware())

    return middleware_stack
