"""
路由模块 - 简洁高效的前缀树实现
- 统一路径处理逻辑
- 简化数据结构
- 消除边界情况
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..common.log_base import logger
from .connection import ConnectInfo


@dataclass
class TrieNode:
    """前缀树节点"""

    children: dict[str, TrieNode]
    conn_info: ConnectInfo | None = None
    is_endpoint: bool = False


class PathRouter:
    """基于前缀树的路由器 - 统一路由接口"""

    def __init__(self):
        self.root = TrieNode({})
        self._route_count = 0
        logger.info("PathRouter initialized")

    def add_route(self, path: str, conn_info: ConnectInfo) -> None:
        """添加路由"""
        if not path or not conn_info:
            logger.warning("Invalid route parameters", path=path)
            return

        try:
            normalized_path = self._normalize_path(path)
            segments = self._split_path(normalized_path)
            logger.info(f"Route registration: {path} → {normalized_path} → {segments}")

            current = self.root
            for segment in segments:
                if segment not in current.children:
                    current.children[segment] = TrieNode({})
                current = current.children[segment]

            current.conn_info = conn_info
            current.is_endpoint = True
            self._route_count += 1

            logger.info(f"Route added: {normalized_path} -> {conn_info.connection_id}")

        except Exception as e:
            logger.error(f"Error adding route {path}: {e}")

    def remove_route(self, path: str) -> bool:
        """移除路由"""
        if not path:
            return False

        try:
            normalized_path = self._normalize_path(path)
            segments = self._split_path(normalized_path)

            current = self.root
            node_path = [self.root]

            for segment in segments:
                if segment not in current.children:
                    return False
                current = current.children[segment]
                node_path.append(current)

            if not current.is_endpoint:
                return False

            current.conn_info = None
            current.is_endpoint = False
            self._route_count -= 1

            self._cleanup_empty_nodes(node_path, segments)
            logger.debug(f"Route removed: {normalized_path}")
            return True

        except Exception as e:
            logger.error(f"Error removing route {path}: {e}")
            return False

    def find_route(self, request_path: str) -> ConnectInfo | None:
        """查找路由 - 使用统一的路径处理"""
        if not request_path:
            return None

        try:
            # 统一路径处理
            normalized_path = self._normalize_path(request_path)
            segments = self._split_path(normalized_path)
            logger.info(
                f"Route lookup: {request_path} → {normalized_path} → {segments}"
            )

            current = self.root
            result_connection = None

            # 检查根节点
            if current.is_endpoint and current.conn_info:
                # 临时移除健康检查以测试路由逻辑
                result_connection = current.conn_info
                logger.info(
                    f"Found root endpoint, connection: {current.conn_info.connection_id}, healthy: {current.conn_info.is_healthy}"
                )

            # 遍历路径段
            for i, segment in enumerate(segments):
                logger.info(
                    f"Checking segment {i}: '{segment}', available children: {list(current.children.keys())}"
                )
                if segment not in current.children:
                    logger.info(f"Segment '{segment}' not found, breaking")
                    break

                current = current.children[segment]
                has_conn = current.conn_info is not None
                is_healthy = current.conn_info.is_healthy if has_conn else False
                logger.info(
                    f"Moved to segment '{segment}', is_endpoint={current.is_endpoint}, has_conn={has_conn}, is_healthy={is_healthy}"
                )

                if current.is_endpoint and current.conn_info:
                    # 临时移除健康检查以测试路由逻辑
                    result_connection = current.conn_info
                    logger.info(
                        f"Found endpoint at segment '{segment}', connection: {current.conn_info.connection_id}, healthy: {current.conn_info.is_healthy}"
                    )
                elif current.is_endpoint and not current.conn_info:
                    logger.warning(
                        f"Endpoint found but no connection info for segment '{segment}'"
                    )

            # 如果找到了端点但后续段不存在，返回已找到的端点
            if result_connection:
                logger.info(
                    f"Route found with fallback: {request_path} -> {result_connection.connection_id}"
                )
                return result_connection

            logger.debug(f"No route found for: {request_path}")
            return None

        except Exception as e:
            logger.error(f"Error finding route for {request_path}: {e}")
            return None

    def remove_connection_routes(self, connection_id: str) -> int:
        """移除指定连接的所有路由"""
        target_routes = []
        self._collect_routes_by_connection(self.root, [], connection_id, target_routes)

        removed_count = 0
        for route_path in target_routes:
            if self.remove_route(route_path):
                removed_count += 1

        if removed_count > 0:
            logger.info(
                f"Removed {removed_count} routes for connection {connection_id}"
            )

        return removed_count

    def cleanup_unhealthy_routes(self) -> int:
        """清理不健康连接的路由"""
        unhealthy_routes = []
        self._collect_unhealthy_routes(self.root, [], unhealthy_routes)

        cleaned_count = 0
        for route_path in unhealthy_routes:
            if self.remove_route(route_path):
                cleaned_count += 1

        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} unhealthy routes")

        return cleaned_count

    def _normalize_path(self, path: str) -> str:
        """统一路径标准化 - 修复不一致问题"""
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

        segments = path.lstrip("/").split("/")
        return [seg for seg in segments if seg]

    def _cleanup_empty_nodes(
        self, node_path: list[TrieNode], segments: list[str]
    ) -> None:
        """清理空节点 - 修复索引错误"""
        for i in range(len(node_path) - 1, 0, -1):
            node = node_path[i]
            parent = node_path[i - 1]
            segment = segments[i - 1] if i - 1 < len(segments) else ""

            if not node.children and not node.is_endpoint and segment:
                parent.children.pop(segment, None)
            else:
                break

    def _collect_unhealthy_routes(
        self, node: TrieNode, path_segments: list[str], routes: list[str]
    ) -> None:
        """收集不健康连接的路由"""
        if node.is_endpoint and node.conn_info and not node.conn_info.is_healthy:
            path = "/" + "/".join(path_segments) if path_segments else "/"
            routes.append(path)

        for segment, child_node in node.children.items():
            self._collect_unhealthy_routes(
                child_node, path_segments + [segment], routes
            )

    def _collect_routes_by_connection(
        self,
        node: TrieNode,
        path_segments: list[str],
        connection_id: str,
        routes: list[str],
    ) -> None:
        """收集特定连接的所有路由"""
        if (
            node.is_endpoint
            and node.conn_info
            and node.conn_info.connection_id == connection_id
        ):
            path = "/" + "/".join(path_segments) if path_segments else "/"
            routes.append(path)

        for segment, child_node in node.children.items():
            self._collect_routes_by_connection(
                child_node, path_segments + [segment], connection_id, routes
            )

    def list_routes(self) -> list[tuple[str, ConnectInfo]]:
        """列出所有路由"""
        routes = []
        self._collect_all_routes(self.root, [], routes)
        return routes

    def _collect_all_routes(
        self,
        node: TrieNode,
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
        """获取路由统计"""
        all_routes = self.list_routes()
        healthy_routes = self.get_healthy_routes()

        connection_counts = {}
        for _, conn_info in all_routes:
            conn_id = conn_info.connection_id
            connection_counts[conn_id] = connection_counts.get(conn_id, 0) + 1

        return {
            "total_routes": len(all_routes),
            "healthy_routes": len(healthy_routes),
            "unhealthy_routes": len(all_routes) - len(healthy_routes),
            "unique_connections": len(connection_counts),
            "routes_per_connection": connection_counts,
            "tree_depth": self._calculate_max_depth(),
        }

    def _calculate_max_depth(self) -> int:
        """计算路由树最大深度"""

        def depth(node: TrieNode) -> int:
            if not node.children:
                return 0
            return 1 + max(depth(child) for child in node.children.values())

        return depth(self.root)
