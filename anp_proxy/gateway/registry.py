"""
服务注册模块 - 单一职责：DID 到路径的映射管理和服务发现
- DID 认证和验证
- 服务路径注册
- 动态服务发现
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

try:
    from ..common.did_wba import DidWbaVerifier
    from ..common.log_base import get_logger
    from ..common.service_registry import ServiceRegistry
except ImportError:
    from anp_proxy.common.did_wba import DidWbaVerifier
    from anp_proxy.common.log_base import get_logger
    # from anp_proxy.common.service_registry import ServiceRegistry  # 未使用

logger = get_logger(__name__)


@dataclass
class ServiceRegistration:
    """服务注册信息"""

    connection_id: str
    did: str
    advertised_paths: set[str]
    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """初始化后处理"""
        if isinstance(self.advertised_paths, list):
            self.advertised_paths = set(self.advertised_paths)

    @property
    def age(self) -> float:
        """注册年龄"""
        return time.time() - self.registered_at

    @property
    def last_seen(self) -> float:
        """上次心跳间隔"""
        return time.time() - self.last_heartbeat

    def update_heartbeat(self) -> None:
        """更新心跳时间"""
        self.last_heartbeat = time.time()


class DidAuthenticator:
    """DID 认证器 - 单一职责：DID 认证和验证"""

    def __init__(self, config=None):
        try:
            # 尝试创建 DidWbaVerifier，传入配置
            if config:
                self.did_verifier = DidWbaVerifier(config)
            else:
                # 如果没有配置，尝试使用默认配置或 None
                try:
                    self.did_verifier = DidWbaVerifier({})
                except Exception:
                    self.did_verifier = None
                    logger.warning(
                        "DidWbaVerifier initialization failed, DID auth disabled"
                    )
        except Exception as e:
            logger.warning(f"Failed to initialize DidWbaVerifier: {e}")
            self.did_verifier = None

        logger.info(
            "DidAuthenticator initialized", has_verifier=self.did_verifier is not None
        )

    async def authenticate_did(self, connection_id: str, did_token: str) -> str | None:
        """认证 DID"""
        if not self.did_verifier:
            # 如果没有验证器，简单提取 DID（用于测试）
            logger.warning(
                "DID verifier not available, using basic token parsing",
                connection_id=connection_id,
            )
            # 简单的 DID 提取逻辑
            if did_token and did_token.startswith("did:"):
                return did_token
            return None

        try:
            # 验证 DID 令牌
            did_info = await self.did_verifier.verify_token(did_token)

            if did_info and did_info.get("did"):
                did = did_info["did"]
                logger.info(
                    "DID authenticated successfully",
                    connection_id=connection_id,
                    did=did,
                )
                return did
            else:
                logger.warning(
                    "DID authentication failed - invalid token",
                    connection_id=connection_id,
                )
                return None

        except Exception as e:
            logger.error(f"DID authentication error: {e}", connection_id=connection_id)
            return None

    def validate_did_format(self, did: str) -> bool:
        """验证 DID 格式"""
        if not did or not isinstance(did, str):
            return False

        # 基本 DID 格式验证: did:method:identifier
        parts = did.split(":")
        return len(parts) >= 3 and parts[0] == "did"


class ServiceDiscovery:
    """服务发现器 - 单一职责：查询和发现服务能力"""

    def __init__(self):
        logger.info("ServiceDiscovery initialized")

    async def discover_services(
        self, connection_id: str, websocket
    ) -> list[str] | None:
        """发现连接提供的服务"""
        try:
            # 发送服务发现请求
            discovery_request = {
                "type": "service_discovery_request",
                "data": {
                    "request_id": f"discovery_{connection_id}_{int(time.time())}",
                    "timestamp": time.time(),
                },
            }

            # 发送请求
            import json

            await websocket.send(json.dumps(discovery_request))

            # 等待响应 (简化版本，实际应该有超时和重试机制)
            response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            response_data = json.loads(response)

            # 解析服务列表
            if response_data.get("type") == "service_discovery_response":
                services = response_data.get("data", {}).get("services", [])
                logger.info(
                    "Services discovered",
                    connection_id=connection_id,
                    services=services,
                )
                return services

            logger.warning(
                "Invalid service discovery response",
                connection_id=connection_id,
                response_type=response_data.get("type"),
            )
            return None

        except TimeoutError:
            logger.warning("Service discovery timeout", connection_id=connection_id)
            return None
        except Exception as e:
            logger.error(f"Service discovery error: {e}", connection_id=connection_id)
            return None


class ServiceRegistryManager:
    """服务注册管理器 - 单一职责：管理服务注册和路径映射"""

    def __init__(
        self, heartbeat_interval: float = 60.0, cleanup_interval: float = 300.0
    ):
        self.heartbeat_interval = heartbeat_interval
        self.cleanup_interval = cleanup_interval

        # 服务注册存储
        self._registrations: dict[
            str, ServiceRegistration
        ] = {}  # connection_id -> registration
        self._did_to_connection: dict[str, str] = {}  # did -> connection_id
        self._path_to_connections: dict[
            str, set[str]
        ] = {}  # path -> set of connection_ids

        # 认证器和发现器
        self.authenticator = DidAuthenticator()
        self.discovery = ServiceDiscovery()

        # 后台任务
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

        logger.info(
            "ServiceRegistryManager initialized",
            heartbeat_interval=heartbeat_interval,
            cleanup_interval=cleanup_interval,
        )

    async def start(self) -> None:
        """启动注册管理器"""
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("ServiceRegistryManager started")

    async def stop(self) -> None:
        """停止注册管理器"""
        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # 清理所有注册
        self._registrations.clear()
        self._did_to_connection.clear()
        self._path_to_connections.clear()

        logger.info("ServiceRegistryManager stopped")

    async def register_service(
        self, connection_id: str, did_token: str, websocket
    ) -> ServiceRegistration | None:
        """注册服务"""
        try:
            # 1. 认证 DID
            did = await self.authenticator.authenticate_did(connection_id, did_token)
            if not did:
                logger.warning(
                    "Service registration failed - DID authentication failed"
                )
                return None

            # 2. 发现服务能力
            services = await self.discovery.discover_services(connection_id, websocket)
            if not services:
                logger.warning("Service registration failed - no services discovered")
                # 允许注册空服务列表，但记录警告
                services = []

            # 3. 创建服务注册
            registration = ServiceRegistration(
                connection_id=connection_id, did=did, advertised_paths=set(services)
            )

            # 4. 存储注册信息
            self._registrations[connection_id] = registration
            self._did_to_connection[did] = connection_id

            # 5. 更新路径映射
            for path in registration.advertised_paths:
                if path not in self._path_to_connections:
                    self._path_to_connections[path] = set()
                self._path_to_connections[path].add(connection_id)

            logger.info(
                "Service registered successfully",
                connection_id=connection_id,
                did=did,
                paths=list(registration.advertised_paths),
            )

            return registration

        except Exception as e:
            logger.error(
                f"Service registration error: {e}", connection_id=connection_id
            )
            return None

    def unregister_service(self, connection_id: str) -> ServiceRegistration | None:
        """注销服务"""
        registration = self._registrations.pop(connection_id, None)
        if not registration:
            return None

        # 清理 DID 映射
        self._did_to_connection.pop(registration.did, None)

        # 清理路径映射
        for path in registration.advertised_paths:
            if path in self._path_to_connections:
                self._path_to_connections[path].discard(connection_id)
                if not self._path_to_connections[path]:
                    del self._path_to_connections[path]

        logger.info(
            "Service unregistered", connection_id=connection_id, did=registration.did
        )

        return registration

    def get_connections_for_path(self, path: str) -> list[str]:
        """获取处理特定路径的连接列表"""
        connections = self._path_to_connections.get(path, set())

        # 过滤活跃的注册
        active_connections = []
        for connection_id in connections:
            registration = self._registrations.get(connection_id)
            if registration and registration.last_seen < self.cleanup_interval:
                active_connections.append(connection_id)

        return active_connections

    def get_registration(self, connection_id: str) -> ServiceRegistration | None:
        """获取服务注册信息"""
        return self._registrations.get(connection_id)

    def update_heartbeat(self, connection_id: str) -> bool:
        """更新连接心跳"""
        registration = self._registrations.get(connection_id)
        if registration:
            registration.update_heartbeat()
            return True
        return False

    def find_connection_by_did(self, did: str) -> str | None:
        """通过 DID 查找连接"""
        return self._did_to_connection.get(did)

    async def _cleanup_loop(self) -> None:
        """清理过期注册的循环任务"""
        while self._running:
            try:
                await self._cleanup_expired_registrations()
                await asyncio.sleep(self.cleanup_interval)
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                await asyncio.sleep(30.0)  # 出错时短暂等待

    async def _cleanup_expired_registrations(self) -> None:
        """清理过期的注册 - 并发优化版本"""
        now = time.time()
        expired_connections = []

        for connection_id, registration in self._registrations.items():
            if now - registration.last_heartbeat > self.cleanup_interval:
                expired_connections.append(connection_id)

        if not expired_connections:
            return

        logger.info(
            f"Found {len(expired_connections)} expired registrations to clean up"
        )

        # 并发清理过期的注册
        cleanup_tasks = []
        for connection_id in expired_connections:
            task = asyncio.create_task(self._cleanup_single_registration(connection_id))
            cleanup_tasks.append(task)

        # 等待所有清理任务完成，忽略异常
        if cleanup_tasks:
            results = await asyncio.gather(*cleanup_tasks, return_exceptions=True)

            # 统计清理结果
            success_count = 0
            error_count = 0
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    error_count += 1
                    logger.error(
                        f"Failed to cleanup registration {expired_connections[i]}: {result}"
                    )
                else:
                    success_count += 1

            logger.info(
                f"Cleanup completed: {success_count} successful, {error_count} failed"
            )

    async def _cleanup_single_registration(self, connection_id: str) -> bool:
        """清理单个注册，返回是否成功"""
        try:
            registration = self.unregister_service(connection_id)
            if registration:
                logger.debug(f"Successfully cleaned up registration: {connection_id}")
                return True
            else:
                logger.warning(
                    f"Registration not found during cleanup: {connection_id}"
                )
                return False
        except Exception as e:
            logger.error(f"Error cleaning up registration {connection_id}: {e}")
            raise

    def list_all_registrations(self) -> list[ServiceRegistration]:
        """列出所有注册"""
        return list(self._registrations.values())

    def list_all_paths(self) -> list[str]:
        """列出所有已注册的路径"""
        return list(self._path_to_connections.keys())

    def get_stats(self) -> dict[str, Any]:
        """获取注册统计"""
        return {
            "total_registrations": len(self._registrations),
            "total_paths": len(self._path_to_connections),
            "unique_dids": len(self._did_to_connection),
            "heartbeat_interval": self.heartbeat_interval,
            "cleanup_interval": self.cleanup_interval,
        }
