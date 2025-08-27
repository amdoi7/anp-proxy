"""Configuration management for ANP Proxy."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_HTTP_PORT,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_PING_INTERVAL,
    DEFAULT_TIMEOUT_SECONDS,
)


def get_default_bind_host() -> str:
    """Return default bind host depending on platform.

    - macOS (Darwin): "0.0.0.0" so services are reachable from host/browser
    - others: "127.0.0.1" for localhost-only by default
    """
    import platform

    system_name = platform.system().lower()
    return "0.0.0.0" if system_name == "darwin" else "127.0.0.1"


class TLSConfig(BaseModel):
    """TLS/SSL configuration."""

    enabled: bool = True
    cert_file: Path | None = None
    key_file: Path | None = None
    ca_file: Path | None = None
    verify_mode: str = "required"  # none, optional, required

    @field_validator("verify_mode")
    @classmethod
    def validate_verify_mode(cls, v: str) -> str:
        """Validate verify_mode value."""
        if v not in ["none", "optional", "required"]:
            raise ValueError("verify_mode must be one of: none, optional, required")
        return v


class DatabaseConfig(BaseModel):
    """Database configuration for service discovery (did_services table only)."""

    enabled: bool = False
    host: str = "localhost"
    port: int = 3306
    user: str = "anp_user"
    password: str = ""
    database: str = "anp_proxy"
    charset: str = "utf8mb4"
    connect_timeout: float = 10.0


class AuthConfig(BaseModel):
    """Authentication configuration."""

    enabled: bool = True
    shared_secret: str | None = None
    token_expiry: int = 3600  # seconds
    max_attempts: int = 3
    # DID-WBA sub-config
    did: str | None = None
    resolver_base_url: str | None = None
    nonce_window_seconds: int = 300
    # JWT for DID-WBA (server-side issuance/verification)
    jwt_private_key_path: Path | None = None
    jwt_public_key_path: Path | None = None


class LogConfig(BaseModel):
    """Logging configuration."""

    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    file: Path | None = None
    max_size: str = "10MB"
    backup_count: int = 5

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"level must be one of: {valid_levels}")
        return v.upper()


class GatewayConfig(BaseModel):
    """Gateway-specific configuration."""

    # HTTP server settings
    host: str = Field(default_factory=get_default_bind_host)
    port: int = DEFAULT_HTTP_PORT

    # Connection settings
    max_connections: int = DEFAULT_MAX_CONNECTIONS
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    keepalive_timeout: float = DEFAULT_KEEPALIVE_INTERVAL

    # Security
    tls: TLSConfig = Field(default_factory=TLSConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    # Protocol settings
    chunk_size: int = DEFAULT_CHUNK_SIZE
    ping_interval: float = DEFAULT_PING_INTERVAL

    # Database for service discovery
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

    # Smart routing configuration
    enable_smart_routing: bool = True
    service_cache_ttl: int = 300  # Service discovery cache TTL in seconds


class ANPConfig(BaseModel):
    """Main ANP Proxy configuration."""

    # Mode: "gateway" only
    mode: str = "gateway"

    # Component configurations
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)

    # Global settings
    logging: LogConfig = Field(default_factory=LogConfig)
    debug: bool = False

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        """Validate mode value."""
        if v not in ["gateway"]:
            raise ValueError("mode must be: gateway")
        return v

    @classmethod
    def from_file(cls, config_file: Path) -> "ANPConfig":
        """Load configuration from TOML file."""
        import rtoml

        with open(config_file, encoding="utf-8") as f:
            config_data = rtoml.load(f)

        return cls(**config_data)

    @classmethod
    def from_dict(cls, config_dict: dict[str, Any]) -> "ANPConfig":
        """Load configuration from dictionary."""
        return cls(**config_dict)

    def save_to_file(self, config_file: Path) -> None:
        """Save configuration to TOML file."""
        import rtoml

        with open(config_file, "w", encoding="utf-8") as f:
            rtoml.dump(self.model_dump(), f)
