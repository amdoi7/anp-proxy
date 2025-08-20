"""
DID Service Resolver - Read proxy paths by DID from database.
"""

from .db_base import execute_query, execute_upsert
from .log_base import get_logger

logger = get_logger(__name__)


class DIDServiceResolver:
    """Resolve advertised proxy paths for a DID from the database (read-only)."""

    def __init__(self) -> None:
        """Stateless resolver; no connection/pool state is held."""
        pass

    def initialize(self) -> None:
        """Initialize resolver (no-op)."""
        logger.info("DID service resolver initialized")

    def close(self) -> None:
        """Close resolver (no-op)."""
        logger.info("DID service resolver closed")

    def get_advertised_services(self, did: str) -> list[str]:
        """Get ordered list of proxy paths for the given DID.

        Args:
            did: DID identifier

        Returns:
            List of proxy_path strings
        """
        try:
            sql = "SELECT proxy_path FROM did_proxy_path WHERE did = %s ORDER BY created_at"
            rows = execute_query(sql, (did,))
            proxy_paths = [row["proxy_path"] for row in rows]
            logger.info(
                f"Loaded proxy paths from database for DID {did}: {proxy_paths}"
            )
            return proxy_paths
        except Exception as e:
            logger.error(f"Failed to query proxy paths for DID: {e}")
            return []  # Return empty list on failure to avoid crash

    def get_unique_dids(self) -> list[str]:
        """Get all unique DIDs present in the database.

        Returns:
            List of unique DID identifiers
        """
        try:
            rows = execute_query(
                "SELECT DISTINCT did FROM did_proxy_path ORDER BY did ASC"
            )
            dids = [row["did"] for row in rows]
            if dids:
                logger.info(f"Unique DIDs in database: {dids}")
                return dids
            logger.info("No DIDs found in database")
            return []

        except Exception as e:
            logger.error(f"Failed to query DID list: {e}")
            return []

    def add_did_service(self, did: str, proxy_path: str) -> bool:
        """Add a DID proxy_path mapping (admin/setup only).

        Args:
            did: DID identifier
            proxy_path: Proxy path

        Returns:
            True if the mapping was added or updated
        """
        try:
            sql = (
                "INSERT INTO did_proxy_path (did, proxy_path, created_at, updated_at) "
                "VALUES (%s, %s, NOW(), NOW()) "
                "ON DUPLICATE KEY UPDATE updated_at = NOW()"
            )
            affected = execute_upsert(sql, (did, proxy_path))
            return affected > 0
        except Exception as e:
            logger.error(f"Failed to add DID service mapping: {e}")
            return False

    def get_connection_stats(self) -> dict:
        """Get database-level statistics relevant to DID mappings."""
        try:
            total_dids = execute_query(
                "SELECT COUNT(DISTINCT did) AS c FROM did_proxy_path"
            )[0]["c"]
            total_services = execute_query("SELECT COUNT(*) AS c FROM did_proxy_path")[
                0
            ]["c"]
            return {
                "enabled": True,
                "total_dids": total_dids,
                "total_services": total_services,
                "connection_type": "per-request",
            }
        except Exception as e:
            logger.error(f"Failed to get connection stats: {e}")
            return {"enabled": True, "error": str(e)}

    def bulk_insert_services(self, services: list[dict]) -> int:
        """Bulk insert DID proxy_path mappings.

        Args:
            services: List of dictionaries with 'did' and 'proxy_path' keys

        Returns:
            Number of records affected
        """
        try:
            if not services:
                return 0
            sql = (
                "INSERT INTO did_proxy_path (did, proxy_path, created_at, updated_at) "
                "VALUES (%s, %s, NOW(), NOW()) "
                "ON DUPLICATE KEY UPDATE updated_at = NOW()"
            )
            count = 0
            for svc in services:
                did = svc["did"]
                proxy_path = svc["proxy_path"]
                affected = execute_upsert(sql, (did, proxy_path))
                count += 1 if affected else 0
            return count
        except Exception as e:
            logger.error(f"Failed to bulk insert DID services: {e}")
            return 0


# Global singleton instance
_resolver_instance: DIDServiceResolver | None = None


def get_did_service_resolver() -> DIDServiceResolver:
    """Get singleton instance of DIDServiceResolver."""
    global _resolver_instance
    if _resolver_instance is None:
        _resolver_instance = DIDServiceResolver()
        _resolver_instance.initialize()
    return _resolver_instance


def cleanup_did_service_resolver() -> None:
    """Cleanup resolver resources and reset singleton."""
    global _resolver_instance
    if _resolver_instance:
        _resolver_instance.close()
        _resolver_instance = None
