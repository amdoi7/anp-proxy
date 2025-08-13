"""Smart router for intelligent service discovery and request routing."""

from fastapi import Request

from ..common.log_base import get_logger
from ..common.service_registry import ServiceRegistry

logger = get_logger(__name__)


class SmartRouter:
    """
    Smart router for intelligent request routing based on Host headers.

    Features:
    - Extracts service URLs from HTTP Host headers
    - Service discovery through registry
    - Load balancing across healthy connections
    - Request routing cache for performance
    """

    def __init__(self, service_registry: ServiceRegistry) -> None:
        """
        Initialize smart router.

        Args:
            service_registry: Service registry for connection discovery
        """
        self.service_registry = service_registry

        logger.info("Smart router initialized")

    async def route_request(self, request: Request) -> str | None:
        """
        Route HTTP request to the best available connection using robust matching.

        Process:
        1. Extract and normalize service URL from Host header and path
        2. Try multiple matching strategies (exact, protocol-agnostic, prefix, host-only)
        3. Find available connections for the matched service
        4. Use load balancer to select optimal connection
        5. Return connection ID for request forwarding

        Args:
            request: HTTP request to route

        Returns:
            Connection ID if routing successful, None if no connections available
        """
        try:
            # 1. Extract and normalize service URL from request
            service_url = self._extract_service_url(request)
            if not service_url:
                logger.warning(
                    "No service URL found in request",
                    host=request.headers.get("host"),
                    path=str(request.url.path),
                )
                return None

            # 2. Try multiple matching strategies
            connection_id = await self._find_service_match(service_url)
            if not connection_id:
                logger.warning(
                    "No service match found using any strategy",
                    service_url=service_url,
                    host=request.headers.get("host"),
                    path=str(request.url.path),
                )
                return None

            logger.debug(
                "Request routed successfully using robust matching",
                service_url=service_url,
                connection_id=connection_id,
            )

            return connection_id

        except Exception as e:
            logger.error(
                "Error routing request",
                host=request.headers.get("host"),
                path=str(request.url.path),
                error=str(e),
            )
            return None

    async def route_request_with_universal_fallback(
        self, request: Request
    ) -> str | None:
        """
        Database-driven routing with robust matching strategies.

        Uses multiple matching strategies to find the best service match:
        1. Exact match
        2. Protocol-agnostic match
        3. Path prefix match
        4. Host-only match

        Args:
            request: HTTP request to route

        Returns:
            Connection ID if database mapping exists, None otherwise
        """
        host = request.headers.get("host")
        path = str(request.url.path)

        if not host:
            logger.warning("No host header in request")
            return None

        # Extract and normalize service URL from request
        service_url = self._extract_service_url(request)
        if not service_url:
            return None

        # Try multiple matching strategies
        connection_id = await self._find_service_match(service_url)
        if connection_id:
            logger.debug(
                "Request routed successfully with robust matching",
                service_url=service_url,
                connection_id=connection_id,
            )
            return connection_id

        logger.warning(
            "No route found for service using any matching strategy",
            host=host,
            path=path,
            service_url=service_url,
        )
        return None

    async def _try_route_to_service(
        self, service_url: str, request: Request | None = None
    ) -> str | None:
        """
        Try to route to a specific service URL.

        Args:
            service_url: Service URL to match
            request: Optional request object (for backward compatibility)

        Returns:
            Connection ID if match found, None otherwise
        """
        try:
            connection_infos = await self.service_registry.get_connections_for_service(
                service_url
            )
            if connection_infos:
                # Return first available connection
                return connection_infos[0].connection_id
        except Exception as e:
            logger.debug(f"Failed to route to service {service_url}: {e}")
        return None

    def _extract_service_url(self, request: Request) -> str | None:
        """
        Extract service URL from HTTP request with robust matching strategies.

        This method supports multiple service URL formats and matching strategies:
        1. Exact match: "api.agent.com/v1/chat"
        2. Protocol-agnostic: "https://api.agent.com/v1/chat" -> "api.agent.com/v1/chat"
        3. Path prefix matching: "api.agent.com/v1/chat" matches "api.agent.com/v1/*"
        4. Host-only matching: "api.agent.com" matches "api.agent.com/*"

        Args:
            request: HTTP request

        Returns:
            Normalized service URL or None if Host header missing
        """
        host = request.headers.get("host")
        if not host:
            return None

        # Extract path and normalize
        path = str(request.url.path) or "/"

        # Remove trailing slash for consistency (except root path)
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        # Construct base service URL (host + path)
        service_url = f"{host}{path}"

        # Normalize the service URL for consistent matching
        normalized_url = self._normalize_service_url(service_url)

        logger.debug(
            "Service URL extracted and normalized",
            original_host=host,
            original_path=path,
            service_url=service_url,
            normalized_url=normalized_url,
        )

        return normalized_url

    def _normalize_service_url(self, service_url: str) -> str:
        """
        Normalize service URL for consistent matching.

        Normalization steps:
        1. Remove protocol scheme (http://, https://)
        2. Remove default ports (80, 443)
        3. Normalize path separators
        4. Remove trailing slashes (except root)

        Args:
            service_url: Raw service URL

        Returns:
            Normalized service URL
        """
        if not service_url:
            return service_url

        # Remove protocol scheme if present
        if service_url.startswith(("http://", "https://")):
            # Find the first slash after protocol
            scheme_end = service_url.find("://")
            if scheme_end != -1:
                service_url = service_url[scheme_end + 3 :]

        # Remove default ports
        if ":80/" in service_url or service_url.endswith(":80"):
            service_url = service_url.replace(":80/", "/").replace(":80", "")
        if ":443/" in service_url or service_url.endswith(":443"):
            service_url = service_url.replace(":443/", "/").replace(":443", "")

        # Normalize path separators (ensure forward slashes)
        service_url = service_url.replace("\\", "/")

        # Remove trailing slash except for root path
        if service_url != "/" and service_url.endswith("/"):
            service_url = service_url.rstrip("/")

        return service_url

    async def _find_service_match(self, service_url: str) -> str | None:
        """
        Find service match using multiple strategies.

        Matching strategies (in order of preference):
        1. Exact match
        2. Protocol-agnostic match
        3. Path prefix match
        4. Host-only match

        Args:
            service_url: Normalized service URL to match

        Returns:
            Connection ID if match found, None otherwise
        """
        if not service_url:
            return None

        # Strategy 1: Exact match
        connection_id = await self._try_route_to_service(service_url, None)
        if connection_id:
            logger.debug(f"Exact match found for {service_url}")
            return connection_id

        # Strategy 2: Protocol-agnostic match
        if service_url.startswith(("http://", "https://")):
            normalized = self._normalize_service_url(service_url)
            connection_id = await self._try_route_to_service(normalized, None)
            if connection_id:
                logger.debug(
                    f"Protocol-agnostic match found: {service_url} -> {normalized}"
                )
                return connection_id

        # Strategy 3: Path prefix matching
        connection_id = await self._try_path_prefix_match(service_url)
        if connection_id:
            logger.debug(f"Path prefix match found for {service_url}")
            return connection_id

        # Strategy 4: Host-only matching
        connection_id = await self._try_host_only_match(service_url)
        if connection_id:
            logger.debug(f"Host-only match found for {service_url}")
            return connection_id

        return None

    async def _try_path_prefix_match(self, service_url: str) -> str | None:
        """
        Try to match service URL using path prefix strategy.

        This strategy looks for services that match the host and path prefix.
        Example: "api.agent.com/v1/chat" matches "api.agent.com/v1/*"

        Args:
            service_url: Service URL to match

        Returns:
            Connection ID if match found, None otherwise
        """
        try:
            # Parse service URL to extract host and path
            if "/" in service_url:
                host, path = service_url.split("/", 1)
                if not path:
                    path = "/"
            else:
                host = service_url
                path = "/"

            # Try different path prefix patterns
            path_parts = path.strip("/").split("/")

            # Try increasingly specific prefixes
            for i in range(len(path_parts), 0, -1):
                prefix = "/".join(path_parts[:i])
                if prefix:
                    pattern = f"{host}/{prefix}/*"
                    connection_id = await self._try_route_to_service(pattern, None)
                    if connection_id:
                        logger.debug(f"Path prefix match: {service_url} -> {pattern}")
                        return connection_id

            # Try host with wildcard
            pattern = f"{host}/*"
            connection_id = await self._try_route_to_service(pattern, None)
            if connection_id:
                logger.debug(f"Host wildcard match: {service_url} -> {pattern}")
                return connection_id

        except Exception as e:
            logger.debug(f"Path prefix matching failed for {service_url}: {e}")

        return None

    async def _try_host_only_match(self, service_url: str) -> str | None:
        """
        Try to match service URL using host-only strategy.

        This strategy looks for services that match just the host.
        Example: "api.agent.com/v1/chat" matches "api.agent.com/*"

        Args:
            service_url: Service URL to match

        Returns:
            Connection ID if match found, None otherwise
        """
        try:
            # Extract host from service URL
            if "/" in service_url:
                host = service_url.split("/")[0]
            else:
                host = service_url

            # Try to match host with wildcard pattern
            host_pattern = f"{host}/*"
            connection_id = await self._try_route_to_service(host_pattern, None)
            return connection_id
        except Exception as e:
            logger.debug(f"Host-only matching failed for {service_url}: {e}")
            return None

    async def route_request_with_fallback(self, request: Request) -> str | None:
        """
        Legacy fallback method - now delegates to universal routing.

        Maintained for backward compatibility.
        """
        return await self.route_request_with_universal_fallback(request)

    # Test and validation methods
    def test_url_normalization(self) -> dict[str, str]:
        """
        Test URL normalization functionality.

        Returns:
            Dictionary of test cases and their normalized results
        """
        test_cases = [
            "https://api.agent.com/v1/chat",
            "http://api.agent.com:80/v1/chat/",
            "api.agent.com/v1/chat",
            "api.agent.com:443/v1/chat",
            "https://api.agent.com:443/v1/chat/",
            "api.agent.com",
            "api.agent.com/",
        ]

        results = {}
        for test_url in test_cases:
            normalized = self._normalize_service_url(test_url)
            results[test_url] = normalized

        return results

    def get_matching_strategies_info(self) -> dict[str, str]:
        """
        Get information about available matching strategies.

        Returns:
            Dictionary describing each matching strategy
        """
        return {
            "exact_match": "Exact service URL match (e.g., 'api.agent.com/v1/chat')",
            "protocol_agnostic": "Ignore HTTP/HTTPS protocol differences",
            "path_prefix": "Match by path prefix (e.g., 'api.agent.com/v1/*' matches 'api.agent.com/v1/chat')",
            "host_only": "Match by host only with wildcard (e.g., 'api.agent.com/*')",
        }
