"""
Database operations module (simple helpers).
"""

import contextlib
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from ..config import (
    DB_CHARSET,
    DB_CONNECT_TIMEOUT,
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
)
from .log_base import get_logger

logger = get_logger(__name__)


class DatabaseError(Exception):
    """Database operation error."""

    pass


def get_connection():
    """Create a database connection."""
    try:
        return pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset=DB_CHARSET,
            connect_timeout=DB_CONNECT_TIMEOUT,
            cursorclass=DictCursor,
            autocommit=False,
        )
    except Exception as e:
        logger.error("Database connection failed", error=str(e))
        raise DatabaseError(f"Database connection failed: {e}")


@contextlib.contextmanager
def get_db_connection():
    """Context manager for database connection with automatic cleanup."""
    conn = None
    try:
        conn = get_connection()
        yield conn
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error("Database operation failed", error=str(e))
        raise DatabaseError(f"Database operation failed: {e}")
    finally:
        if conn:
            conn.close()


def execute_query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Execute a SELECT query and return rows."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()


def execute_upsert(sql: str, params: tuple = ()) -> int:
    """Execute an INSERT ... ON DUPLICATE KEY UPDATE and return affected rows."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            result = cursor.execute(sql, params)
            conn.commit()
            return result


def test_connection() -> bool:
    """Test database connectivity."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                if result and result.get("1") == 1:
                    logger.info("Database connection test passed")
                    return True
                else:
                    logger.error("Database connection test failed")
                    return False
    except Exception as e:
        logger.error("Database connection test failed", error=str(e))
        return False


def get_database_info() -> dict[str, Any]:
    """Get basic database information."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION() as version")
                result = cursor.fetchone()
                return {
                    "database": "MySQL",
                    "version": result.get("version") if result else "unknown",
                    "host": DB_HOST,
                    "port": DB_PORT,
                    "database_name": DB_NAME,
                }
    except Exception as e:
        logger.error("Failed to get database information", error=str(e))
        return {}


def health_check() -> dict[str, Any]:
    """Database health report."""
    try:
        connection_ok = test_connection()
        db_info = get_database_info()

        return {
            "status": "healthy" if connection_ok else "unhealthy",
            "connection": connection_ok,
            "database_info": db_info,
        }
    except Exception as e:
        logger.error("Database health check failed", error=str(e))
        return {
            "status": "unhealthy",
            "connection": False,
            "error": str(e),
        }
