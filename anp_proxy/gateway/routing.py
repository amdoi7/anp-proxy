"""
路由匹配模块 - 单一职责：HTTP 路径匹配和路由决策
- 基于前缀树的路径匹配算法
- 最长前缀匹配 (LPM) 策略
- 直接存储连接对象，便于 WebSocket 通信
"""

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

# 运行时导入 ConnectInfo
try:
    from .connection import ConnectInfo
except ImportError:
    try:
        from anp_proxy.gateway.connection import ConnectInfo
    except ImportError:
        # 如果导入失败，定义一个占位符类型
        if TYPE_CHECKING:
            from typing import Any

            ConnectInfo = Any
        else:
            ConnectInfo = object

try:
    from ..common.log_base import get_logger
except ImportError:
    try:
        from anp_proxy.common.log_base import get_logger
    except ImportError:
        # 如果导入失败，使用标准日志
        import logging

        def get_logger(name):
            return logging.getLogger(name)


logger = get_logger(__name__)


class RouteNode:
    """路由树节点 - 直接存储连接对象"""

    __slots__ = ["path_segment", "children", "conn_info", "is_endpoint"]

    def __init__(self, path_segment: str = ""):
        self.path_segment = path_segment
        self.children: dict[str, RouteNode] = {}
        self.conn_info: ConnectInfo | None = None
        self.is_endpoint = False


class PathRouter:
    """路径路由器 - 直接管理连接对象映射，带路由缓存优化"""

    def __init__(self, cache_size: int = 1000):
        self.root = RouteNode()
        self._route_count = 0

        # 路由缓存优化
        self._route_cache: dict[str, ConnectInfo | None] = {}
        self._cache_size = cache_size
        self._cache_hits = 0
        self._cache_misses = 0

        logger.info(
            "PathRouter initialized with connection object storage and cache",
            cache_size=cache_size,
        )

    def add_route(self, path: str, conn_info: ConnectInfo) -> None:
        """添加路由规则 - 直接存储连接对象"""
        if not path or not conn_info:
            logger.warning(
                "Invalid route parameters",
                path=path,
                connection_id=getattr(conn_info, "connection_id", None),
            )
            return

        # 标准化路径
        normalized_path = self._normalize_path(path)
        segments = self._split_path(normalized_path)

        # 构建路由树
        current_node = self.root
        for segment in segments:
            if segment not in current_node.children:
                current_node.children[segment] = RouteNode(segment)
            current_node = current_node.children[segment]

        # 设置终点 - 存储连接对象
        current_node.conn_info = conn_info
        current_node.is_endpoint = True
        self._route_count += 1

        # 清理缓存中相关的路径
        self._invalidate_cache_for_path(normalized_path)

        logger.debug(
            "Route added with connection object",
            path=normalized_path,
            connection_id=conn_info.connection_id,
            segments=len(segments),
        )

    def remove_route(self, path: str) -> bool:
        """移除路由规则"""
        if not path:
            return False

        normalized_path = self._normalize_path(path)
        segments = self._split_path(normalized_path)

        # 查找目标节点
        current_node = self.root
        node_path = [self.root]

        for segment in segments:
            if segment not in current_node.children:
                return False
            current_node = current_node.children[segment]
            node_path.append(current_node)

        if not current_node.is_endpoint:
            return False

        # 移除终点标记和连接
        current_node.conn_info = None
        current_node.is_endpoint = False
        self._route_count -= 1

        # 清理空节点
        self._cleanup_empty_nodes(node_path, segments)

        # 清理缓存中相关的路径
        self._invalidate_cache_for_path(normalized_path)

        logger.debug("Route removed", path=normalized_path)
        return True

    def find_route(self, request_path: str) -> ConnectInfo | None:
        """查找路由 - 返回连接对象，实现最长前缀匹配，带缓存优化"""
        if not request_path:
            return None

        normalized_path = self._normalize_path(request_path)

        # 检查缓存
        if normalized_path in self._route_cache:
            cached_result = self._route_cache[normalized_path]
            # 验证缓存的连接是否仍然健康
            if cached_result is None or cached_result.is_healthy:
                self._cache_hits += 1
                logger.debug(
                    "Route cache hit",
                    request_path=normalized_path,
                    connection_id=cached_result.connection_id
                    if cached_result
                    else None,
                )
                return cached_result
            else:
                # 缓存的连接不健康，移除缓存
                del self._route_cache[normalized_path]

        # 缓存未命中，执行实际查找
        self._cache_misses += 1
        result = self._find_route_impl(normalized_path)

        # 更新缓存
        self._update_cache(normalized_path, result)

        logger.debug(
            "Route lookup result",
            request_path=normalized_path,
            connection_id=result.connection_id if result else None,
            cache_hit=False,
        )

        return result

    def _find_route_impl(self, normalized_path: str) -> ConnectInfo | None:
        """实际的路由查找实现"""
        segments = self._split_path(normalized_path)

        # 最长前缀匹配
        best_match_connection = None
        current_node = self.root

        # 检查根节点
        if current_node.is_endpoint and current_node.conn_info:
            best_match_connection = current_node.conn_info

        # 遍历路径段
        for segment in segments:
            if segment not in current_node.children:
                break

            current_node = current_node.children[segment]

            # 如果当前节点是终点且连接健康，更新最佳匹配
            if (
                current_node.is_endpoint
                and current_node.conn_info
                and current_node.conn_info.is_healthy
            ):
                best_match_connection = current_node.conn_info

        return best_match_connection

    def _update_cache(self, path: str, result: ConnectInfo | None) -> None:
        """更新路由缓存"""
        # 如果缓存已满，移除最旧的条目（简单的 FIFO 策略）
        if len(self._route_cache) >= self._cache_size:
            # 移除第一个条目
            oldest_path = next(iter(self._route_cache))
            del self._route_cache[oldest_path]

        self._route_cache[path] = result

    def _invalidate_cache_for_path(self, path: str) -> None:
        """清理与指定路径相关的缓存"""
        # 移除精确匹配的缓存
        self._route_cache.pop(path, None)

        # 移除可能受影响的父路径缓存
        path_parts = path.strip("/").split("/")
        for i in range(len(path_parts)):
            parent_path = "/" + "/".join(path_parts[: i + 1])
            self._route_cache.pop(parent_path, None)

    def remove_connection_routes(self, connection_id: str) -> int:
        """移除特定连接的所有路由"""
        removed_count = 0
        routes_to_remove = []

        # 找到所有该连接的路由
        self._collect_connection_routes(self.root, [], connection_id, routes_to_remove)

        # 移除路由
        for route_path in routes_to_remove:
            if self.remove_route(route_path):
                removed_count += 1

        logger.info(
            "Connection routes removed",
            connection_id=connection_id,
            count=removed_count,
        )

        return removed_count

    def remove_unhealthy_routes(self) -> int:
        """移除不健康连接的路由"""
        removed_count = 0
        routes_to_remove = []

        # 找到所有不健康连接的路由
        self._collect_unhealthy_routes(self.root, [], routes_to_remove)

        # 移除路由
        for route_path in routes_to_remove:
            if self.remove_route(route_path):
                removed_count += 1

        if removed_count > 0:
            logger.info(f"Removed {removed_count} unhealthy routes")

        return removed_count

    def _collect_unhealthy_routes(
        self, node: RouteNode, path_segments: list[str], routes: list[str]
    ) -> None:
        """收集不健康连接的路由"""
        # 如果当前节点的连接不健康，记录路径
        if node.is_endpoint and node.conn_info and not node.conn_info.is_healthy:
            path = "/" + "/".join(path_segments) if path_segments else "/"
            routes.append(path)

        # 递归检查子节点
        for segment, child_node in node.children.items():
            self._collect_unhealthy_routes(
                child_node, path_segments + [segment], routes
            )

    def _normalize_path(self, path: str) -> str:
        """标准化路径"""
        if not path:
            return "/"

        # 移除查询参数和片段
        parsed = urlparse(path)
        path = parsed.path

        # 确保以 / 开始
        if not path.startswith("/"):
            path = "/" + path

        # 移除尾部 / (除非是根路径)
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")

        return path

    def _split_path(self, path: str) -> list[str]:
        """分割路径为段"""
        if path == "/":
            return []

        # 移除开头的 / 并分割
        segments = path.lstrip("/").split("/")
        return [seg for seg in segments if seg]  # 过滤空段

    def _cleanup_empty_nodes(
        self, node_path: list[RouteNode], segments: list[str]
    ) -> None:
        """清理空节点"""
        # 从叶子节点向上清理
        for i in range(len(node_path) - 1, 0, -1):
            node = node_path[i]
            parent = node_path[i - 1]
            segment = segments[i - 1] if i <= len(segments) else ""

            # 如果节点没有子节点且不是终点，则可以删除
            if not node.children and not node.is_endpoint and segment:
                parent.children.pop(segment, None)
            else:
                # 如果节点仍有用，停止清理
                break

    def _collect_connection_routes(
        self,
        node: RouteNode,
        path_segments: list[str],
        connection_id: str,
        routes: list[str],
    ) -> None:
        """收集特定连接的所有路由"""
        # 如果当前节点属于目标连接，记录路径
        if (
            node.is_endpoint
            and node.conn_info
            and node.conn_info.connection_id == connection_id
        ):
            path = "/" + "/".join(path_segments) if path_segments else "/"
            routes.append(path)

        # 递归检查子节点
        for segment, child_node in node.children.items():
            self._collect_connection_routes(
                child_node, path_segments + [segment], connection_id, routes
            )

    def list_routes(self) -> list[tuple[str, ConnectInfo]]:
        """列出所有路由 - 返回路径和连接对象的元组"""
        routes = []
        self._collect_all_routes(self.root, [], routes)
        return routes

    def _collect_all_routes(
        self,
        node: RouteNode,
        path_segments: list[str],
        routes: list[tuple[str, ConnectInfo]],
    ) -> None:
        """收集所有路由"""
        if node.is_endpoint and node.conn_info:
            path = "/" + "/".join(path_segments) if path_segments else "/"
            routes.append((path, node.conn_info))

        for segment, child_node in node.children.items():
            self._collect_all_routes(child_node, path_segments + [segment], routes)

    def get_healthy_routes(self) -> list[tuple[str, ConnectInfo]]:
        """获取所有健康的路由"""
        all_routes = self.list_routes()
        return [(path, conn) for path, conn in all_routes if conn.is_healthy]

    def get_stats(self) -> dict[str, Any]:
        """获取路由统计，包含缓存统计"""
        all_routes = self.list_routes()
        healthy_routes = self.get_healthy_routes()

        connection_counts = {}
        for _, conn_info in all_routes:
            conn_id = conn_info.connection_id
            connection_counts[conn_id] = connection_counts.get(conn_id, 0) + 1

        # 计算缓存命中率
        total_requests = self._cache_hits + self._cache_misses
        cache_hit_rate = (
            (self._cache_hits / total_requests * 100) if total_requests > 0 else 0
        )

        return {
            "total_routes": len(all_routes),
            "healthy_routes": len(healthy_routes),
            "unhealthy_routes": len(all_routes) - len(healthy_routes),
            "unique_connections": len(connection_counts),
            "routes_per_connection": connection_counts,
            "tree_depth": self._calculate_max_depth(),
            "cache_stats": {
                "cache_size": len(self._route_cache),
                "max_cache_size": self._cache_size,
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "cache_hit_rate": f"{cache_hit_rate:.2f}%",
            },
        }

    def _calculate_max_depth(self) -> int:
        """计算路由树最大深度"""

        def depth(node: RouteNode) -> int:
            if not node.children:
                return 0
            return 1 + max(depth(child) for child in node.children.values())

        return depth(self.root)


class RequestRouter:
    """请求路由器 - 提供统一的路由接口"""

    def __init__(self):
        self.path_router = PathRouter()
        logger.info("RequestRouter initialized with connection object support")

    def add_path_route(self, path: str, conn_info: ConnectInfo) -> None:
        """添加路径路由 - 接受连接对象"""
        self.path_router.add_route(path, conn_info)

    def remove_path_route(self, path: str) -> bool:
        """移除路径路由"""
        return self.path_router.remove_route(path)

    def remove_connection_routes(self, connection_id: str) -> int:
        """移除连接的所有路由"""
        return self.path_router.remove_connection_routes(connection_id)

    def cleanup_unhealthy_routes(self) -> int:
        """清理不健康的路由"""
        return self.path_router.remove_unhealthy_routes()

    def route_request(self, request_path: str) -> ConnectInfo | None:
        """路由请求 - 返回目标连接对象"""
        # 提取路径
        if not request_path:
            return None

        # 如果是完整URL，提取路径
        if request_path.startswith(("http://", "https://")):
            parsed = urlparse(request_path)
            raw_path = parsed.path
        else:
            raw_path = request_path

        # 去除单个前导 /
        path = raw_path[1:] if raw_path.startswith("/") else raw_path

        # 执行路由匹配 - 返回连接对象
        conn_info = self.path_router.find_route(path)

        logger.debug(
            "Request routed to connection",
            request_path=request_path,
            extracted_path=path,
            target_connection=conn_info.connection_id if conn_info else None,
            connection_healthy=conn_info.is_healthy if conn_info else None,
        )

        return conn_info

    def list_all_routes(self) -> list[tuple[str, str]]:
        """列出所有路由 - 返回路径和连接ID的元组（向后兼容）"""
        routes = self.path_router.list_routes()
        return [(path, conn.connection_id) for path, conn in routes]

    def list_all_connections(self) -> list[tuple[str, ConnectInfo]]:
        """列出所有路由连接对象"""
        return self.path_router.list_routes()

    def get_healthy_connections(self) -> list[tuple[str, ConnectInfo]]:
        """获取所有健康的路由连接"""
        return self.path_router.get_healthy_routes()

    def get_routing_stats(self) -> dict[str, Any]:
        """获取路由统计"""
        return self.path_router.get_stats()
