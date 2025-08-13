"""Database adapter for service discovery."""

import contextlib
import threading
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from .config import DatabaseConfig
from .log_base import get_logger

logger = get_logger(__name__)


class DIDService:
    """DID service model."""

    def __init__(
        self,
        id: int | None = None,
        did: str = "",
        service_url: str = "",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ):
        self.id = id
        self.did = did
        self.service_url = service_url
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DIDService":
        """Create instance from database row."""
        return cls(
            id=data.get("id"),
            did=data.get("did", ""),
            service_url=data.get("service_url", ""),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database operations."""
        return {
            "id": self.id,
            "did": self.did,
            "service_url": self.service_url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ConnectionPool:
    """Simple connection pool for PyMySQL connections."""

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        self._pool: list[Connection] = []
        self._lock = threading.Lock()
        self._connection_config = {
            "host": config.host,
            "port": config.port,
            "user": config.user,
            "password": config.password,
            "db": config.database,
            "charset": "utf8mb4",
            "autocommit": False,
            "cursorclass": DictCursor,
        }

    def initialize(self) -> None:
        """Initialize the connection pool."""
        with self._lock:
            for _ in range(self.config.min_connections):
                conn = pymysql.connect(**self._connection_config)
                self._pool.append(conn)

    def get_connection(self) -> Connection:
        """Get a connection from the pool."""
        with self._lock:
            if self._pool:
                return self._pool.pop()
            else:
                # Create a new connection if pool is empty and we haven't reached max
                return pymysql.connect(**self._connection_config)

    def return_connection(self, conn: Connection) -> None:
        """Return a connection to the pool."""
        with self._lock:
            if len(self._pool) < self.config.max_connections:
                try:
                    # Test if connection is still alive
                    conn.ping(reconnect=False)
                    self._pool.append(conn)
                except Exception:
                    # Connection is dead, close it
                    conn.close()
            else:
                # Pool is full, close the connection
                conn.close()

    def close(self) -> None:
        """Close all connections in the pool."""
        with self._lock:
            for conn in self._pool:
                conn.close()
            self._pool.clear()

    @property
    def size(self) -> int:
        """Get current pool size."""
        with self._lock:
            return len(self._pool)

    @property
    def freesize(self) -> int:
        """Get number of available connections."""
        return self.size


class DatabaseAdapter:
    """PyMySQL database adapter for service discovery and connection management."""

    def __init__(self, config: DatabaseConfig) -> None:
        """
        Initialize database adapter.

        Args:
            config: Database configuration
        """
        self.config = config
        self.pool: ConnectionPool | None = None

        logger.info("Database adapter initialized", enabled=config.enabled)

    def initialize(self) -> None:
        """Initialize database connection pool."""
        if not self.config.enabled:
            logger.info("Database adapter disabled")
            return

        try:
            # Create connection pool
            self.pool = ConnectionPool(self.config)
            self.pool.initialize()

            # Test connection
            self._test_connection()

            # Create tables if not exist
            self._create_tables()

            logger.info(
                "Database connection pool initialized",
                host=self.config.host,
                database=self.config.database,
                pool_size=f"{self.config.min_connections}-{self.config.max_connections}",
            )

        except Exception as e:
            logger.error("Failed to initialize database connection", error=str(e))
            raise

    def close(self) -> None:
        """Close database connection pool."""
        if self.pool:
            self.pool.close()
            logger.info("Database connection pool closed")

    def _test_connection(self) -> None:
        """Test database connection."""
        if not self.pool:
            return

        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                if not result or result["1"] != 1:
                    raise RuntimeError("Database connection test failed")

    def _create_tables(self) -> None:
        """Create database tables if they don't exist."""
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS did_services (
            id INT AUTO_INCREMENT PRIMARY KEY,
            did VARCHAR(255) NOT NULL,
            service_url VARCHAR(512) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_did (did),
            UNIQUE KEY unique_did_service (did, service_url)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """

        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(create_table_sql)
                conn.commit()

    @contextlib.contextmanager
    def get_connection(self) -> Generator[Connection, None, None]:
        """Context manager for database connections."""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        conn = self.pool.get_connection()
        try:
            yield conn
        finally:
            self.pool.return_connection(conn)

    def get_services_by_did(self, did: str) -> list[str]:
        """
        Get all service URLs for a DID.

        Args:
            did: DID identifier

        Returns:
            List of service URLs
        """
        if not self.pool:
            return []

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    query = """
                        SELECT service_url FROM did_services
                        WHERE did = %s

                        ORDER BY created_at
                    """
                    cursor.execute(query, (did,))
                    result = cursor.fetchall()
                    return [row["service_url"] for row in result]

        except Exception as e:
            logger.error("Failed to get services by DID", did=did, error=str(e))
            return []

    def register_did_service(self, did: str, service_url: str) -> bool:
        """
        Register a DID service (for database setup/admin only - not used in runtime).

        NOTE: This method should only be used for initial database setup or admin operations.
        During normal runtime, the database is read-only for service discovery.

        Args:
            did: DID identifier
            service_url: Service URL

        Returns:
            True if registered successfully
        """
        if not self.pool:
            return False

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # Use INSERT ... ON DUPLICATE KEY UPDATE for MySQL
                    query = """
                        INSERT INTO did_services (did, service_url, created_at, updated_at)
                        VALUES (%s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            updated_at = VALUES(updated_at)
                    """
                    now = datetime.now()
                    cursor.execute(query, (did, service_url, now, now))
                    conn.commit()

                    logger.debug(
                        "DID service registered (setup/admin operation)",
                        did=did,
                        service_url=service_url,
                    )
                    return True

        except Exception as e:
            logger.error(
                "Failed to register DID service",
                did=did,
                service_url=service_url,
                error=str(e),
            )
            return False

    def get_connection_stats(self) -> dict[str, int | str | bool]:
        """Get database connection statistics."""
        if not self.pool:
            return {"enabled": False}

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # Total DIDs with services
                    cursor.execute(
                        "SELECT COUNT(DISTINCT did) as total_dids FROM did_services"
                    )
                    dids_result = cursor.fetchone()
                    total_dids = dids_result["total_dids"] if dids_result else 0

                    # Total service registrations
                    cursor.execute(
                        "SELECT COUNT(*) as total_services FROM did_services"
                    )
                    services_result = cursor.fetchone()
                    total_services = (
                        services_result["total_services"] if services_result else 0
                    )

                    return {
                        "enabled": True,
                        "total_dids": total_dids,
                        "total_services": total_services,
                        "pool_size": f"{self.config.min_connections}-{self.config.max_connections}",
                        "pool_current_size": self.pool.size,
                        "pool_free_size": self.pool.freesize,
                    }

        except Exception as e:
            logger.error("Failed to get connection stats", error=str(e))
            return {"enabled": True, "error": str(e)}

    def execute_query(
        self, query: str, params: tuple | None = None, fetch_one: bool = False
    ) -> list[dict[str, Any]] | dict[str, Any] | None:
        """
        Execute a custom query with parameters.

        Args:
            query: SQL query string
            params: Query parameters
            fetch_one: Whether to fetch only one result

        Returns:
            Query results or None
        """
        if not self.pool:
            return None

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)

                    if query.strip().upper().startswith("SELECT"):
                        if fetch_one:
                            return cursor.fetchone()
                        else:
                            return cursor.fetchall()
                    else:
                        conn.commit()
                        return cursor.rowcount

        except Exception as e:
            logger.error("Failed to execute query", query=query, error=str(e))
            return None

    def bulk_insert_services(self, services: list[dict[str, Any]]) -> int:
        """
        Bulk insert DID services for better performance.

        Args:
            services: List of service dictionaries with 'did' and 'service_url' keys

        Returns:
            Number of inserted records
        """
        if not self.pool or not services:
            return 0

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    query = """
                        INSERT IGNORE INTO did_services (did, service_url, created_at, updated_at)
                        VALUES (%s, %s, %s, %s)
                    """
                    now = datetime.now()
                    data = [
                        (service["did"], service["service_url"], now, now)
                        for service in services
                    ]

                    cursor.executemany(query, data)
                    conn.commit()

                    logger.info(f"Bulk inserted {cursor.rowcount} DID services")
                    return cursor.rowcount

        except Exception as e:
            logger.error("Failed to bulk insert services", error=str(e))
            return 0
