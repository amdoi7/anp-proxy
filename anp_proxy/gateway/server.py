"""
核心网关协调器 - 单一职责：协调所有子模块，提供统一的网关接口
- 整合连接管理、路由、消息处理、服务注册
- 提供统一的 API 接口
- 协调各模块间的交互
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from starlette.responses import Response

try:
    from ..common.did_resolver import get_did_service_resolver
    from ..common.did_wba import DidAuthResult, DidWbaVerifier
    from ..common.log_base import get_logger
    from .connection import ConnectInfo, ConnectionManager
    from .message import MessageHandler
    from .registry import ServiceRegistryManager
    from .routing import RequestRouter
except ImportError:
    from anp_proxy.common.did_resolver import get_did_service_resolver
    from anp_proxy.common.did_wba import DidAuthResult, DidWbaVerifier
    from anp_proxy.common.log_base import get_logger
    from anp_proxy.gateway.connection import ConnectInfo, ConnectionManager
    from anp_proxy.gateway.message import MessageHandler
    from anp_proxy.gateway.registry import ServiceRegistryManager
    from anp_proxy.gateway.routing import RequestRouter

logger = get_logger(__name__)


class ANPGateway:
    """
    ANP 网关核心 - 世界级 AI Agent 基础设施网关

    单一职责原则下的模块协调：
    - ConnectionManager: WebSocket 连接管理
    - RequestRouter: HTTP 路径路由
    - MessageHandler: 消息处理和转发
    - ServiceRegistryManager: 服务注册和发现
    """

    def __init__(
        self,
        ping_interval: float = 30.0,
        connection_timeout: float = 300.0,
        response_timeout: float = 30.0,
        heartbeat_interval: float = 60.0,
        auth_config=None,
    ):
        # 初始化各个管理器
        self.connection_manager = ConnectionManager(ping_interval, connection_timeout)
        self.request_router = RequestRouter()
        self.message_handler = MessageHandler(response_timeout)
        self.registry_manager = ServiceRegistryManager(heartbeat_interval)

        # 初始化 DID WBA 验证器
        from ..common.config import AuthConfig

        self.auth_config = auth_config or AuthConfig()
        self.did_wba_verifier = DidWbaVerifier(self.auth_config)

        # 网关状态
        self._running = False

        # 路由清理任务
        self._route_cleanup_task: asyncio.Task | None = None
        self._route_cleanup_interval = 60.0  # 60秒清理一次

        # 初始化恶意请求检测
        self._init_malicious_patterns()

        logger.info(
            "ANPGateway initialized",
            ping_interval=ping_interval,
            connection_timeout=connection_timeout,
            response_timeout=response_timeout,
            heartbeat_interval=heartbeat_interval,
        )

    async def start(self) -> None:
        """启动网关"""
        if self._running:
            return

        # 启动所有管理器
        await self.connection_manager.start()
        await self.message_handler.start()
        await self.registry_manager.start()

        # 启动路由清理任务
        self._route_cleanup_task = asyncio.create_task(self._route_cleanup_loop())

        self._running = True
        logger.info("ANPGateway started successfully")

    async def stop(self) -> None:
        """停止网关"""
        if not self._running:
            return

        self._running = False

        # 停止路由清理任务
        if self._route_cleanup_task:
            self._route_cleanup_task.cancel()
            try:
                await self._route_cleanup_task
            except asyncio.CancelledError:
                pass

        # 停止所有管理器
        await self.registry_manager.stop()
        await self.message_handler.stop()
        await self.connection_manager.stop()

        logger.info("ANPGateway stopped")

    async def handle_websocket_connection(
        self, websocket: WebSocket, connection_id: str | None = None
    ) -> None:
        """处理 WebSocket 连接"""
        if not connection_id:
            import uuid

            connection_id = str(uuid.uuid4())

        await websocket.accept()
        logger.info(f"WebSocket connection accepted: {connection_id}")

        try:
            # 添加连接
            connection = await self.connection_manager.add_connection(
                connection_id, websocket
            )

            # 处理连接消息
            await self._handle_websocket_messages(connection)

        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected: {connection_id}")
        except Exception as e:
            logger.error(f"WebSocket error for {connection_id}: {e}")
        finally:
            # 清理连接
            await self._cleanup_connection(connection_id)

    async def handle_raw_websocket_connection(
        self, websocket, path: str, connection_id: str | None = None
    ) -> None:
        """处理原始 WebSocket 连接（用于独立的 WebSocket 服务器）"""
        import uuid

        if not connection_id:
            connection_id = str(uuid.uuid4())

        logger.info(
            f"Raw WebSocket connection accepted: {connection_id} (path: {path})"
        )

        try:
            # 🆕 尝试头部认证支持
            authenticated_via_headers = False
            if (
                hasattr(websocket, "request_headers")
                and self.auth_config.did_wba_enabled
            ):
                did_result = await self._verify_did_headers(websocket)
                if did_result.success:
                    logger.info(
                        f"Connection {connection_id} authenticated via headers with DID: {did_result.did}"
                    )
                    authenticated_via_headers = True

                    # 直接注册服务
                    registration = await self._register_service_from_headers(
                        connection_id, did_result.did, websocket
                    )

                    if registration:
                        # 创建连接信息（使用原始websocket对象）
                        connection = await self.connection_manager.add_raw_connection(
                            connection_id, websocket
                        )

                        # 认证成功，更新连接状态
                        self.connection_manager.authenticate_connection(
                            connection_id,
                            registration.did,
                            list(registration.advertised_paths),
                        )

                        # 添加路由 - 直接使用连接对象
                        for path in registration.advertised_paths:
                            self.request_router.add_path_route(path, connection)

                        logger.info(
                            f"Connection {connection_id} authenticated and services registered via headers"
                        )
                    else:
                        logger.error(
                            f"Failed to register services for authenticated connection {connection_id}"
                        )
                        return
                else:
                    logger.warning(
                        f"Header authentication failed for connection {connection_id}: {did_result.error}"
                    )

            # 如果没有通过头部认证，使用传统方式
            if not authenticated_via_headers:
                # 创建连接信息（使用原始websocket对象）
                connection = await self.connection_manager.add_raw_connection(
                    connection_id, websocket
                )

            # 处理连接消息
            await self._handle_raw_websocket_messages(connection)

        except Exception as e:
            logger.error(f"Raw WebSocket error for {connection_id}: {e}")
        finally:
            # 清理连接
            await self._cleanup_connection(connection_id)

    async def _handle_raw_websocket_messages(self, connection: ConnectInfo) -> None:
        """处理原始 WebSocket 消息"""
        websocket = connection.websocket
        connection_id = connection.connection_id

        try:
            async for message in websocket:
                try:
                    # 更新连接活动时间
                    connection.update_activity()

                    # 处理不同类型的消息
                    if isinstance(message, str):
                        # JSON 控制消息
                        try:
                            ws_message = json.loads(message)
                            await self._handle_websocket_message(connection, ws_message)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON message from {connection_id}")
                    elif isinstance(message, bytes):
                        # 二进制数据消息（ANPX 协议）
                        await self.message_handler.handle_raw_message(
                            connection, message
                        )
                    else:
                        logger.warning(
                            f"Unknown message type from {connection_id}: {type(message)}"
                        )

                except Exception as e:
                    logger.error(
                        f"Error processing raw WebSocket message from {connection_id}: {e}"
                    )
                    break

        except Exception as e:
            logger.error(
                f"Error in raw WebSocket message loop for {connection_id}: {e}"
            )
        finally:
            logger.info(f"Raw WebSocket message handling ended for {connection_id}")

    async def _handle_websocket_messages(self, connection: ConnectInfo) -> None:
        """处理 WebSocket 消息"""
        websocket = connection.websocket
        connection_id = connection.connection_id

        while True:
            try:
                # 接收消息
                message = await websocket.receive_text()

                # 更新连接活动时间
                connection.update_activity()

                # 解析消息
                import json

                try:
                    ws_message = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON message from {connection_id}")
                    continue

                # 处理不同类型的消息
                await self._handle_websocket_message(connection, ws_message)

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(
                    f"Error processing WebSocket message from {connection_id}: {e}"
                )
                break

    async def _handle_websocket_message(
        self, connection: ConnectInfo, message: dict[str, Any]
    ) -> None:
        """处理 WebSocket 消息"""
        message_type = message.get("type")
        connection_id = connection.connection_id

        if message_type == "authentication":
            # 处理认证消息
            await self._handle_authentication(connection, message)

        elif message_type == "heartbeat":
            # 处理心跳消息
            self._handle_heartbeat(connection)

        elif message_type == "http_response":
            # 处理 HTTP 响应
            self.message_handler.handle_websocket_message(message)

        else:
            logger.debug(f"Unknown message type from {connection_id}: {message_type}")

    async def _handle_authentication(
        self, connection: ConnectInfo, message: dict[str, Any]
    ) -> None:
        """处理认证消息"""
        data = message.get("data", {})
        did_token = data.get("did_token")

        if not did_token:
            logger.warning(
                f"Authentication failed - missing DID token: {connection.connection_id}"
            )
            return

        # 注册服务
        registration = await self.registry_manager.register_service(
            connection.connection_id, did_token, connection.websocket
        )

        if registration:
            # 认证成功，更新连接状态
            self.connection_manager.authenticate_connection(
                connection.connection_id,
                registration.did,
                list(registration.advertised_paths),
            )

            # 添加路由 - 直接使用连接对象
            for path in registration.advertised_paths:
                self.request_router.add_path_route(path, connection)

            logger.info(
                f"Connection authenticated and services registered: {connection.connection_id}"
            )
        else:
            logger.warning(f"Service registration failed: {connection.connection_id}")

    def _handle_heartbeat(self, connection: ConnectInfo) -> None:
        """处理心跳消息"""
        connection.update_ping()
        self.registry_manager.update_heartbeat(connection.connection_id)
        logger.debug(f"Heartbeat received: {connection.connection_id}")

    async def _cleanup_connection(self, connection_id: str) -> None:
        """清理连接"""
        # 移除路由
        self.request_router.remove_connection_routes(connection_id)

        # 注销服务
        self.registry_manager.unregister_service(connection_id)

        # 移除连接
        await self.connection_manager.remove_connection(connection_id)

        logger.info(f"Connection cleaned up: {connection_id}")

    async def _verify_did_headers(self, websocket):
        """Verify DID-WBA headers during WS handshake."""
        try:
            # Derive service domain for signature verification:
            # 1) X-Forwarded-Host (first, if behind reverse proxy)
            # 2) Host header
            # 3) Fallback to websocket.host or "localhost"
            headers = getattr(websocket, "request_headers", None)
            forwarded_host = None
            host_header = None
            try:
                if headers is not None:
                    # Some servers expose CIMultiDict-like, others dict-like
                    forwarded_host = (
                        headers.get("X-Forwarded-Host")
                        if hasattr(headers, "get")
                        else None
                    )
                    if not forwarded_host and hasattr(headers, "get"):
                        forwarded_host = headers.get("x-forwarded-host")

                    host_header = (
                        headers.get("Host") if hasattr(headers, "get") else None
                    )
                    if not host_header and hasattr(headers, "get"):
                        host_header = headers.get("host")
            except Exception:
                forwarded_host = None
                host_header = None

            def _extract_hostname(value: str | None) -> str | None:
                if not value:
                    return None
                # Support comma-separated (first hop), and strip port
                first = str(value).split(",")[0].strip()
                # IPv6 literals may be in [::1]:port, strip brackets then port
                if first.startswith("[") and "]" in first:
                    first = first[1 : first.index("]")]
                return first.split(":")[0].strip() or None

            domain = (
                _extract_hostname(forwarded_host)
                or _extract_hostname(host_header)
                or getattr(websocket, "host", None)
                or "localhost"
            )

            logger.info(
                f"Verifying DID-WBA headers for domain: {domain}",
            )

            result = await self.did_wba_verifier.verify(
                websocket.request_headers, domain
            )
            if result.success:
                logger.info("DID-WBA authenticated", did=result.did)
            else:
                logger.warning("DID-WBA auth failed", error=result.error)
            return result
        except Exception as e:
            logger.error("DID-WBA verification error", error=str(e))
            return DidAuthResult(success=False, error=str(e))

    async def _register_service_from_headers(
        self, connection_id: str, did: str, websocket
    ):
        """Register service using DID from headers authentication."""
        try:
            # Create a mock registration for header-based auth
            import time

            from .registry import ServiceRegistration

            # Read advertised paths from database only (did_proxy_path)
            resolver = get_did_service_resolver()
            normalized = resolver.get_advertised_services(did)
            if not normalized:
                logger.error(
                    "No advertised paths found in database for header-based registration",
                    did=did,
                )
                return None

            registration = ServiceRegistration(
                connection_id=connection_id,
                did=did,
                advertised_paths=set(normalized),
                registered_at=time.time(),
                last_heartbeat=time.time(),
            )

            # Store registration in registry manager
            self.registry_manager._registrations[connection_id] = registration
            self.registry_manager._did_to_connection[did] = connection_id

            # Update path mappings
            for path in normalized:
                if path not in self.registry_manager._path_to_connections:
                    self.registry_manager._path_to_connections[path] = set()
                self.registry_manager._path_to_connections[path].add(connection_id)

                # 获取连接信息并注册路由到路由器
                connection_info = self.connection_manager.get_connection(connection_id)
                if connection_info:
                    self.request_router.add_path_route(path, connection_info)

            logger.info(
                f"Service registered from headers: connection_id={connection_id}, did={did}, paths={normalized}"
            )
            return registration

        except Exception as e:
            logger.error(f"Failed to register service from headers: {e}")
            return None

    def _init_malicious_patterns(self) -> None:
        """初始化恶意请求模式"""
        import re

        # 核心恶意模式（高频检测）
        self._core_patterns = {
            "/wp-admin/",
            "/wp-includes/",
            "/wordpress/",
            "/xmlrpc.php",
            "/wp-config.php",
            "/wp-content/",
            "/wp-json/",
            "/admin/",
            "/administrator/",
            "/phpmyadmin/",
            "/mysql/",
            "/cpanel/",
            "/webmail/",
            "/mail/",
            "/ftp/",
            "/ssh/",
            "/telnet/",
            "/shell/",
            "/cmd/",
            "/exec/",
            "/system/",
            "/eval/",
            "/assert/",
            "/include/",
            "/require/",
        }

        # 协议包装器模式（需要正则匹配）
        self._protocol_patterns = [
            r"/(file|data|php|expect|input|filter|zip|phar|ogg|rar|zlib|bzip2|quoted-printable|rot13)://",
            r"/convert\.(iconv|base64|quoted-printable|uuencode)\.",
            r"/convert\.(base64|quoted-printable|uuencode)-(decode|encode)",
            r"/convert\.iconv\.(utf-[0-9]+|utf-[0-9]+le|utf-[0-9]+be)\.(utf-[0-9]+|utf-[0-9]+le|utf-[0-9]+be)",
        ]

        # 预编译正则表达式
        self._protocol_regex = re.compile(
            "|".join(self._protocol_patterns), re.IGNORECASE
        )

    def _is_malicious_request(self, request_path: str) -> bool:
        """检测恶意请求 - 优化版本"""
        if not request_path:
            return False

        request_path_lower = request_path.lower()

        # 1. 快速检查核心模式（集合查找，O(1)）
        for pattern in self._core_patterns:
            if pattern in request_path_lower:
                return True

        # 2. 正则匹配协议包装器（更精确）
        if self._protocol_regex.search(request_path):
            return True

        return False

    async def _route_cleanup_loop(self) -> None:
        """定期清理不健康路由的循环任务"""
        while self._running:
            try:
                # 清理不健康的路由
                cleaned_count = self.request_router.cleanup_unhealthy_routes()
                if cleaned_count > 0:
                    logger.info(f"Cleaned up {cleaned_count} unhealthy routes")

                await asyncio.sleep(self._route_cleanup_interval)
            except Exception as e:
                logger.error(f"Route cleanup error: {e}")
                await asyncio.sleep(30.0)  # 出错时短暂等待

    async def handle_http_request(self, request: Request) -> Response:
        """处理 HTTP 请求"""
        if not self._running:
            from starlette.responses import JSONResponse

            return JSONResponse({"error": "Gateway not running"}, status_code=503)

        # 提取请求路径
        request_path = str(request.url.path)

        # 过滤恶意请求
        if self._is_malicious_request(request_path):
            logger.warning(f"Malicious request blocked: {request_path}")
            from starlette.responses import JSONResponse

            return JSONResponse({"error": "Forbidden"}, status_code=403)

        # 路由请求 - 直接获取连接对象
        target_connection = self.request_router.route_request(request_path)

        if not target_connection:
            logger.info(f"No route found for path: {request_path}")
            from starlette.responses import JSONResponse

            return JSONResponse(
                {"error": "No route found", "path": request_path}, status_code=404
            )

        # 检查连接健康状态
        if not target_connection.is_healthy:
            logger.warning(
                f"Target connection unavailable: {target_connection.connection_id}"
            )
            from starlette.responses import JSONResponse

            return JSONResponse(
                {
                    "error": "Service unavailable",
                    "connection": target_connection.connection_id,
                },
                status_code=503,
            )

        # 转发请求
        try:
            response = await self.message_handler.handle_http_request(
                request, target_connection.websocket
            )

            logger.info(
                "HTTP request handled successfully",
                path=request_path,
                connection=target_connection.connection_id,
                status=response.status_code,
            )

            return response

        except Exception as e:
            logger.error(f"Error handling HTTP request: {e}")
            from starlette.responses import JSONResponse

            return JSONResponse(
                {"error": "Internal server error", "details": str(e)}, status_code=500
            )

    def get_gateway_stats(self) -> dict[str, Any]:
        """获取网关统计信息"""
        return {
            "running": self._running,
            "connections": self.connection_manager.get_stats(),
            "routing": self.request_router.get_routing_stats(),
            "messages": self.message_handler.get_handler_stats(),
            "registry": self.registry_manager.get_stats(),
        }

    async def health_check(self) -> dict[str, Any]:
        """健康检查"""
        stats = self.get_gateway_stats()

        # 评估健康状态
        is_healthy = self._running and stats["connections"]["healthy_connections"] > 0

        return {
            "status": "healthy" if is_healthy else "degraded",
            "timestamp": asyncio.get_event_loop().time(),
            "details": stats,
        }


class ANPGatewayApp:
    """ANP 网关应用 - FastAPI 集成"""

    def __init__(self, gateway: ANPGateway):
        self.gateway = gateway

    def create_app(self) -> FastAPI:
        """创建 FastAPI 应用"""

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # 启动时执行
            await self.gateway.start()
            yield
            # 关闭时执行
            await self.gateway.stop()

        app = FastAPI(
            title="ANP Gateway",
            description="World-class AI Agent Network Protocol Gateway",
            version="1.0.0",
            lifespan=lifespan,
        )

        # WebSocket 端点
        @app.websocket("/ws")
        async def websocket_endpoint(
            websocket: WebSocket, connection_id: str | None = None
        ):
            await self.gateway.handle_websocket_connection(websocket, connection_id)

        # HTTP 请求处理 - 捕获所有路径
        @app.api_route(
            "/{path:path}",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        )
        async def handle_all_requests(request: Request):
            return await self.gateway.handle_http_request(request)

        # 健康检查端点
        @app.get("/health")
        async def health_check():
            return await self.gateway.health_check()

        # 统计信息端点
        @app.get("/stats")
        async def get_stats():
            return self.gateway.get_gateway_stats()

        # 路由信息端点
        @app.get("/routes")
        async def get_routes():
            # 获取所有路由连接
            route_connections = self.gateway.request_router.list_all_connections()
            healthy_connections = self.gateway.request_router.get_healthy_connections()

            # 构建路由信息
            routes_info = []
            for path, connection in route_connections:
                routes_info.append({
                    "path": path,
                    "connection_id": connection.connection_id,
                    "did": connection.did,
                    "healthy": connection.is_healthy,
                    "age": connection.age,
                    "last_activity": connection.last_activity,
                })

            healthy_routes_info = []
            for path, connection in healthy_connections:
                healthy_routes_info.append({
                    "path": path,
                    "connection_id": connection.connection_id,
                    "did": connection.did,
                })

            return {
                "routes": routes_info,
                "healthy_routes": healthy_routes_info,
                "total": len(routes_info),
                "healthy_total": len(healthy_routes_info),
            }

        return app


def create_gateway(
    ping_interval: float = 30.0,
    connection_timeout: float = 300.0,
    response_timeout: float = 30.0,
    heartbeat_interval: float = 60.0,
    auth_config=None,
) -> ANPGateway:
    """创建网关实例"""
    return ANPGateway(
        ping_interval=ping_interval,
        connection_timeout=connection_timeout,
        response_timeout=response_timeout,
        heartbeat_interval=heartbeat_interval,
        auth_config=auth_config,
    )


def create_app(gateway: ANPGateway | None = None) -> FastAPI:
    """创建网关应用"""
    if gateway is None:
        gateway = create_gateway()

    app_wrapper = ANPGatewayApp(gateway)
    return app_wrapper.create_app()


# 便捷的应用创建
app = create_app()


if __name__ == "__main__":
    import uvicorn

    from anp_proxy.common.config import get_default_bind_host

    uvicorn.run(
        "anp_proxy.gateway.server:app",
        host=get_default_bind_host(),
        port=8000,
        log_level="info",
        reload=True,
    )
