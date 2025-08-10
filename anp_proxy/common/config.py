"""Configuration management for ANP Proxy."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings

from .constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_HTTP_PORT,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_MAX_RECONNECT_ATTEMPTS,
    DEFAULT_PING_INTERVAL,
    DEFAULT_RECONNECT_DELAY,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_WSS_PORT,
)


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


class AuthConfig(BaseModel):
    """Authentication configuration."""

    enabled: bool = True
    shared_secret: str | None = None
    token_expiry: int = 3600  # seconds
    max_attempts: int = 3
    # DID-WBA sub-config
    did_wba_enabled: bool = False
    did: str | None = None
    did_document_path: Path | None = None
    private_key_path: Path | None = None
    resolver_base_url: str | None = None
    nonce_window_seconds: int = 300
    allowed_dids: list[str] = Field(default_factory=list)
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
    host: str = "0.0.0.0"
    port: int = DEFAULT_HTTP_PORT

    # WebSocket server settings
    wss_host: str = "0.0.0.0"
    wss_port: int = DEFAULT_WSS_PORT

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


class ReceiverConfig(BaseModel):
    """Receiver-specific configuration."""

    # Gateway connection
    gateway_url: str = "wss://localhost:8765"

    # Local app settings
    local_host: str = "127.0.0.1"
    local_port: int = 8000
    local_app_module: str | None = None  # e.g., "myapp:app"

    # Connection settings
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL
    ping_interval: float = DEFAULT_PING_INTERVAL

    # Reconnection
    reconnect_enabled: bool = True
    reconnect_delay: float = DEFAULT_RECONNECT_DELAY
    max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS

    # Security
    tls: TLSConfig = Field(default_factory=TLSConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    # Protocol settings
    chunk_size: int = DEFAULT_CHUNK_SIZE


class ANPConfig(BaseSettings):
    """Main ANP Proxy configuration."""

    # Mode: "gateway", "receiver", or "both"
    mode: str = "both"

    # Component configurations
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    receiver: ReceiverConfig = Field(default_factory=ReceiverConfig)

    # Global settings
    logging: LogConfig = Field(default_factory=LogConfig)
    debug: bool = False

    model_config = {
        "env_prefix": "ANP_",
        "env_nested_delimiter": "__",
        "case_sensitive": False,
    }

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        """Validate mode value."""
        if v not in ["gateway", "receiver", "both"]:
            raise ValueError("mode must be one of: gateway, receiver, both")
        return v

    @classmethod
    def from_file(cls, config_file: Path) -> "ANPConfig":
        """Load configuration from file."""
        import tomli

        with open(config_file, "rb") as f:
            config_data = tomli.load(f)

        return cls(**config_data)

    @classmethod
    def from_dict(cls, config_dict: dict[str, Any]) -> "ANPConfig":
        """Load configuration from dictionary."""
        return cls(**config_dict)

    def save_to_file(self, config_file: Path) -> None:
        """Save configuration to TOML file."""
        import tomli_w

        with open(config_file, "wb") as f:
            tomli_w.dump(self.model_dump(), f)
