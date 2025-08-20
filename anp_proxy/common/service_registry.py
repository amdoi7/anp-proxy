"""Service registry for managing DID to connection info mappings."""

import asyncio
from datetime import datetime, timedelta

from .db_base import execute_query
from .log_base import get_logger

logger = get_logger(__name__)


class ServiceRegistry:
    """service registry with direct service_url->ConnectionInfo mapping."""

    def __init__(self) -> None:
        """
        Initialize service registry.

        Args:
            None
        """
        # Database access is via KISS functions execute_query/execute_upsert

        # 🎯 Core storage: Direct service_url -> connection mapping for O(1) lookup (1:1 relationship)
        self._service_connections: dict[
            str, object
        ] = {}  # service_url -> ConnectionInfo

        # 🔄 Reverse mapping: connection_id -> service_urls for efficient cleanup
        self._connection_services: dict[
            str, list[str]
        ] = {}  # connection_id -> service_urls

        # 📊 Connection metadata: connection_id -> {did, registered_at, etc.}
        self._connection_metadata: dict[str, dict] = {}  # connection_id -> metadata

        # Service URL cache from database
        self._service_cache: dict[str, list[str]] = {}  # did -> service_urls
        self.cache_ttl = timedelta(minutes=5)  # 5 minutes cache TTL
        self._last_cache_update: dict[str, datetime] = {}  # did -> last_update_time

        # Cleanup task
        self._cleanup_task: asyncio.Task | None = None

        logger.info(
            "Service registry initialized (service_url->ConnectionInfo mapping)"
        )

    async def start(self) -> None:
        """Start the service registry cleanup task."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Service registry started")

    async def stop(self) -> None:
        """Stop the service registry."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Clear all caches
        self._service_connections.clear()
        self._connection_services.clear()
        self._connection_metadata.clear()
        self._service_cache.clear()
        self._last_cache_update.clear()

        logger.info("Service registry stopped")

    async def register_connection(
        self, did: str, conn_info: object, advertised_services: list[str] | None = None
    ) -> list[str]:
        """
        Register connection by querying database for DID's service URLs (database-driven architecture).

        Flow:
        1. Query database: SELECT service_url FROM did_services WHERE did = %s
        2. Build mapping: {service_url: conn_info} for each service_url
        3. Return service URLs for this DID

        Args:
            did: DID identifier
            conn_info: ConnectionInfo object
            advertised_services: Ignored - services come from database only

        Returns:
            List of service URLs for this DID from database
        """
        try:
            # Try to get connection_id attribute, fallback to dict key if needed
            if hasattr(conn_info, "connection_id"):
                connection_id = conn_info.connection_id
            elif isinstance(conn_info, dict) and "connection_id" in conn_info:
                connection_id = conn_info["connection_id"]
            else:
                raise AttributeError(
                    "conn_info has no 'connection_id' attribute or key"
                )

            # 🎯 Always query database for service URLs (authoritative source)
            service_urls = await self._get_services_by_did_cached(did)

            if not service_urls:
                logger.warning(
                    "No services found in database for DID",
                    did=did,
                    connection_id=connection_id,
                    database_available=True,
                    database_pool=None,
                )

            # 🎯 Build direct service_url -> conn_info mapping (1:1 relationship)
            for service_url in service_urls:
                self._service_connections[service_url] = conn_info
                logger.debug(
                    "Service mapped to connection",
                    service_url=service_url,
                    connection_id=connection_id,
                    did=did,
                )

            # 🔄 Build reverse mapping for efficient cleanup
            self._connection_services[connection_id] = service_urls.copy()

            # 📊 Store connection metadata
            self._connection_metadata[connection_id] = {
                "connection_id": connection_id,
                "did": did,
                "registered_at": datetime.now(),
                "service_count": len(service_urls),
            }

            logger.info(
                "Connection registered with database-driven service mapping",
                did=did,
                connection_id=connection_id,
                services_count=len(service_urls),
                services=service_urls,
            )

            return service_urls

        except Exception as e:
            logger.error("Failed to register connection", did=did, error=str(e))
            return []

    async def get_connection_by_did(self, did: str) -> object | None:
        """
        Get connection info by DID (searches through metadata).

        Args:
            did: DID identifier

        Returns:
            ConnectionInfo object or None if not found
        """
        # Search through connection metadata to find DID
        for connection_id, metadata in self._connection_metadata.items():
            if metadata.get("did") == did:
                # Find the connection in any service mapping
                for service_connections in self._service_connections.values():
                    for conn_info in service_connections:
                        if conn_info.connection_id == connection_id:
                            return conn_info
        return None

    async def get_connections_for_service(self, service_url: str) -> list[object]:
        """
        Database-driven service discovery ONLY - exact match only.

        Strategy:
        1. Try exact match from memory (active connections)
        2. Query database for service URL mapping to DID
        3. Find active connections for that DID
        4. Try path matching for microservices

        Args:
            service_url: Service URL to find connections for

        Returns:
            List of ConnectionInfo objects (empty if no exact database match)
        """
        connections = []

        try:
            # Strategy 1: Exact match from memory (active connections)
            exact_match = self._service_connections.get(service_url)
            if exact_match:
                connections.append(exact_match)
                logger.debug(
                    "Memory exact match found",
                    service_url=service_url,
                    connection_id=exact_match.connection_id,
                )
                return connections

            # Strategy 2: Database-driven path mapping
            db_connections = await self._find_connections_by_database_mapping(
                service_url
            )
            if db_connections:
                logger.debug(
                    "Database path mapping found",
                    service_url=service_url,
                    connections_count=len(db_connections),
                )
                return db_connections

            # Strategy 3: Path prefix matching for microservices
            path_connections = await self._find_connections_by_path_matching(
                service_url
            )
            if path_connections:
                logger.debug(
                    "Path prefix matching found",
                    service_url=service_url,
                    connections_count=len(path_connections),
                )
                return path_connections

            logger.debug(
                "No connections found for service",
                service_url=service_url,
            )
            return []

        except Exception as e:
            logger.error(
                "Failed to get connections for service",
                service_url=service_url,
                error=str(e),
            )
            return []

    async def _find_exact_matches(self, service_url: str) -> list[object]:
        """Find connections with exact service URL match (legacy method, now uses direct lookup)."""
        # This method is now redundant with direct mapping but kept for compatibility
        exact_match = self._service_connections.get(service_url)
        return [exact_match] if exact_match else []

    async def _find_protocol_agnostic_matches(self, service_url: str) -> list[object]:
        """Find connections ignoring HTTP/HTTPS protocol differences."""
        connections = []

        # Convert to alternate protocol
        if service_url.startswith("https://"):
            alternate_url = service_url.replace("https://", "http://", 1)
        elif service_url.startswith("http://"):
            alternate_url = service_url.replace("http://", "https://", 1)
        else:
            return connections

        # Check for alternate protocol matches in direct mapping
        if alternate_url in self._service_connections:
            conn_info = self._service_connections[alternate_url]
            connections.append(conn_info)
            connection_id = conn_info.connection_id
            metadata = self._connection_metadata.get(connection_id, {})
            did = metadata.get("did", "unknown")
            logger.debug(
                "Protocol-agnostic match found",
                requested=service_url,
                matched=alternate_url,
                did=did,
            )

        return connections

    async def _find_advanced_matches(self, service_url: str) -> list[object]:
        """Find connections using advanced matching strategies."""
        connections = []

        # Parse the service URL
        from urllib.parse import urlparse

        parsed = urlparse(service_url)

        if not parsed.netloc:
            return connections

        # Strategy A: Domain-only matching (for microservices)
        domain_matches = await self._find_domain_matches(parsed.netloc)
        connections.extend(domain_matches)

        # Strategy B: Prefix matching (for REST APIs)
        if parsed.path and parsed.path != "/":
            prefix_matches = await self._find_prefix_matches(parsed.netloc, parsed.path)
            connections.extend(prefix_matches)

        # Remove duplicates while preserving order
        seen = set()
        unique_connections = []
        for conn in connections:
            if conn.connection_id not in seen:
                seen.add(conn.connection_id)
                unique_connections.append(conn)

        return unique_connections

    async def _find_domain_matches(self, domain: str) -> list[object]:
        """Find services registered for the same domain."""
        connections = []

        # Try both protocols for domain-only matching
        for protocol in ["https", "http"]:
            domain_url = f"{protocol}://{domain}"

            if domain_url in self._service_connections:
                conn_info = self._service_connections[domain_url]
                connections.append(conn_info)
                connection_id = conn_info.connection_id
                metadata = self._connection_metadata.get(connection_id, {})
                did = metadata.get("did", "unknown")
                logger.debug(
                    "Domain match found",
                    domain=domain,
                    matched_service=domain_url,
                    did=did,
                )

        return connections

    async def _find_prefix_matches(self, domain: str, path: str) -> list[object]:
        """Find services that match path prefixes."""
        connections = []

        # Generate path prefixes (/api/v1/users -> [/api/v1, /api])
        path_parts = [part for part in path.split("/") if part]

        for i in range(len(path_parts), 0, -1):
            prefix_path = "/" + "/".join(path_parts[:i])

            # Try both protocols
            for protocol in ["https", "http"]:
                prefix_url = f"{protocol}://{domain}{prefix_path}"

                if prefix_url in self._service_connections:
                    conn_info = self._service_connections[prefix_url]
                    connections.append(conn_info)
                    connection_id = conn_info.connection_id
                    metadata = self._connection_metadata.get(connection_id, {})
                    did = metadata.get("did", "unknown")
                    logger.debug(
                        "Prefix match found",
                        original_path=path,
                        matched_prefix=prefix_path,
                        matched_service=prefix_url,
                        did=did,
                    )

        return connections

    async def get_connections_for_service_by_did(self, service_url: str) -> list[str]:
        """
        Get DIDs that can handle a service URL.

        Args:
            service_url: Service URL

        Returns:
            List of DID identifiers
        """
        matching_dids = []

        try:
            # Check if service exists in direct mapping
            if service_url in self._service_connections:
                conn_info = self._service_connections[service_url]
                connection_id = conn_info.connection_id
                metadata = self._connection_metadata.get(connection_id, {})
                did = metadata.get("did")
                if did:
                    matching_dids.append(did)

            logger.debug(
                "Found DIDs for service", service_url=service_url, dids=matching_dids
            )

            return matching_dids

        except Exception as e:
            logger.error(
                "Failed to get DIDs for service", service_url=service_url, error=str(e)
            )
            return []

    async def remove_connection(self, did: str) -> None:
        """
        Remove connection from service registry by DID.

        Args:
            did: DID identifier to remove
        """
        connection_id = None

        # Find connection_id by DID from metadata
        for conn_id, metadata in self._connection_metadata.items():
            if metadata.get("did") == did:
                connection_id = conn_id
                break

        if connection_id:
            await self.remove_connection_by_id(connection_id)
        else:
            logger.warning("Connection not found for DID", did=did)

    async def remove_connection_by_id(self, connection_id: str) -> None:
        """
        Remove connection from registry by connection ID.

        Args:
            connection_id: Connection ID to remove
        """
        try:
            # 1. Get service URLs for this connection
            service_urls = self._connection_services.pop(connection_id, [])

            # 2. Remove from service_url -> conn_info mappings
            for service_url in service_urls:
                if service_url in self._service_connections:
                    # Verify it's the right connection before removing
                    conn_info = self._service_connections[service_url]
                    if conn_info.connection_id == connection_id:
                        del self._service_connections[service_url]
                        logger.debug(
                            "Service unmapped from connection",
                            service_url=service_url,
                            connection_id=connection_id,
                        )

            # 3. Remove connection metadata
            metadata = self._connection_metadata.pop(connection_id, {})
            did = metadata.get("did", "unknown")

            logger.info(
                "Connection removed from registry",
                connection_id=connection_id,
                did=did,
                services_removed=len(service_urls),
            )

        except Exception as e:
            logger.error(
                "Failed to remove connection", connection_id=connection_id, error=str(e)
            )

    async def register_did_services(self, did: str, service_urls: list[str]) -> bool:
        """
        Register service URLs for a DID in database (admin/setup operation only).

        NOTE: This method should only be used for initial database setup.
        During normal runtime, the database is read-only for service discovery.

        Args:
            did: DID identifier
            service_urls: List of service URLs to register

        Returns:
            True if all services registered successfully
        """
        logger.warning(
            "register_did_services called during runtime - this should only be used for setup",
            did=did,
            service_urls=service_urls,
        )

        if not service_urls:
            return True

        success_count = 0
        for service_url in service_urls:
            if self.db.register_did_service(did, service_url):
                success_count += 1

        # Invalidate cache for this DID
        self._service_cache.pop(did, None)
        self._last_cache_update.pop(did, None)

        all_success = success_count == len(service_urls)
        logger.info(
            "DID services registration completed (setup operation)",
            did=did,
            total_services=len(service_urls),
            successful=success_count,
            success=all_success,
        )

        return all_success

    async def _get_services_by_did_cached(self, did: str) -> list[str]:
        """
        Get services by DID with caching.

        Args:
            did: DID identifier

        Returns:
            List of service URLs
        """
        # Check if cache is valid
        last_update = self._last_cache_update.get(did)
        if (
            last_update
            and datetime.now() - last_update < self.cache_ttl
            and did in self._service_cache
        ):
            return self._service_cache[did]

        # Cache miss or expired - query database
        rows = execute_query(
            """
            SELECT proxy_path FROM did_proxy_path
            WHERE did = %s
            ORDER BY created_at
            """,
            (did,),
        )
        service_urls = [row["proxy_path"] for row in rows]

        logger.debug(
            "Database query for DID services",
            did=did,
            found_services=len(service_urls),
            services=service_urls,
            cache_miss=True,
        )

        # Update cache
        self._service_cache[did] = service_urls
        self._last_cache_update[did] = datetime.now()

        return service_urls

    async def _cleanup_loop(self) -> None:
        """Periodic cleanup of expired cache entries."""
        while True:
            try:
                await asyncio.sleep(300)  # Cleanup every 5 minutes

                current_time = datetime.now()

                # Clean up expired cache entries
                expired_dids = []
                for did, last_update in self._last_cache_update.items():
                    if current_time - last_update > self.cache_ttl:
                        expired_dids.append(did)

                for did in expired_dids:
                    self._service_cache.pop(did, None)
                    self._last_cache_update.pop(did, None)

                if expired_dids:
                    logger.debug(
                        "Cache cleanup completed", expired_dids=len(expired_dids)
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in cleanup loop", error=str(e))

    def get_all_dids(self) -> list[str]:
        """Get list of all registered DIDs."""
        dids = []
        for metadata in self._connection_metadata.values():
            did = metadata.get("did")
            if did and did not in dids:
                dids.append(did)
        return dids

    def get_connection_count(self) -> int:
        """Get total number of registered connections."""
        return len(self._connection_metadata)

    def get_stats(self) -> dict[str, int | float | dict]:
        """Get service registry statistics and mappings."""
        # Convert service_connections to a serializable format
        service_mappings = {}
        for service_url, conn_info in self._service_connections.items():
            if hasattr(conn_info, "connection_id"):
                connection_id = conn_info.connection_id
            else:
                connection_id = str(conn_info)

            # Get DID and metadata directly from connection_metadata
            metadata = self._connection_metadata.get(connection_id, {})
            did = metadata.get("did", "unknown")
            registered_at = metadata.get("registered_at", "unknown")

            service_mappings[service_url] = {
                "connection_id": connection_id,
                "did": did,
                "registered_at": str(registered_at),
                "status": "active" if conn_info else "inactive",
            }

        return {
            "total_connections": len(self._connection_metadata),
            "total_services": len(self._service_connections),
            "cached_services": len(self._service_cache),
            "cache_ttl_minutes": self.cache_ttl.total_seconds() / 60,
            "service_mappings": service_mappings,
        }

    async def _find_connections_by_database_mapping(
        self, service_url: str
    ) -> list[object]:
        """
        Find connections by querying database for service URL to DID mapping.

        Strategy:
        1. Query database for DIDs that have this service URL
        2. Find active connections for those DIDs

        Args:
            service_url: Service URL to look up

        Returns:
            List of ConnectionInfo objects for DIDs that have this service
        """
        connections = []

        try:
            if not self.db:
                return []

            # Query database to find which DIDs have this service URL
            query = "SELECT DISTINCT did FROM did_proxy_path WHERE proxy_path = %s"
            result = execute_query(query, (service_url,))

            if not result:
                logger.debug(f"No DIDs found for service URL: {service_url}")
                return []

            # For each DID that has this service, find active connections
            for row in result:
                did = row["did"]
                logger.debug(f"Found DID {did} has service {service_url}")

                # Find active connections for this DID
                for connection_id, metadata in self._connection_metadata.items():
                    if metadata.get("did") == did:
                        # Find the connection object in service mappings
                        for conn_info in self._service_connections.values():
                            if (
                                hasattr(conn_info, "connection_id")
                                and conn_info.connection_id == connection_id
                            ):
                                connections.append(conn_info)
                                logger.info(
                                    f"Database mapping: {service_url} -> DID {did} -> Connection {connection_id}"
                                )
                                break
                        break

        except Exception as e:
            logger.error(f"Database mapping lookup failed for {service_url}: {e}")

        return connections

    async def _find_connections_by_path_matching(
        self, service_url: str
    ) -> list[object]:
        """
        Find connections using intelligent path matching for microservices.

        Strategy:
        1. Extract path components from service URL
        2. Try progressively shorter path prefixes
        3. Match against registered services in memory

        Args:
            service_url: Service URL to match (e.g., "localhost:8089/agents/jsonrpc")

        Returns:
            List of ConnectionInfo objects that can handle this path
        """
        connections = []

        try:
            # Extract path from service URL
            if "/" not in service_url:
                return []

            # Split service URL into host and path
            parts = service_url.split("/")
            if len(parts) < 2:
                return []

            host = parts[0]
            path_segments = parts[1:]

            logger.debug(
                f"Path matching for {service_url}: host={host}, segments={path_segments}"
            )

            # Try progressively shorter path prefixes
            for i in range(len(path_segments), 0, -1):
                prefix_segments = path_segments[:i]
                test_paths = [
                    f"{host}/"
                    + "/".join(prefix_segments),  # e.g., "localhost:8089/agents"
                    "/".join(prefix_segments),  # e.g., "agents"
                ]

                for test_path in test_paths:
                    logger.debug(f"Testing path prefix: {test_path}")
                    exact_match = self._service_connections.get(test_path)
                    if exact_match:
                        connections.append(exact_match)
                        logger.info(f"Path prefix match: {service_url} -> {test_path}")
                        return connections

            # Try database path matching if memory matching failed
            for i in range(len(path_segments), 0, -1):
                prefix_segments = path_segments[:i]
                test_paths = [
                    f"{host}/" + "/".join(prefix_segments),
                    "/".join(prefix_segments),
                ]

                for test_path in test_paths:
                    db_connections = await self._find_connections_by_database_mapping(
                        test_path
                    )
                    if db_connections:
                        logger.info(
                            f"Database path prefix match: {service_url} -> {test_path}"
                        )
                        return db_connections

        except Exception as e:
            logger.error(f"Path matching failed for {service_url}: {e}")

        return connections
