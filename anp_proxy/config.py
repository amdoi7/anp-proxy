"""Project-wide configuration constants for KISS database module.

Priority: environment variables override config.toml values.
"""

from __future__ import annotations

import os
from pathlib import Path

import rtoml


def _load_toml() -> dict:
    """Load top-level config.toml if available."""
    try:
        # anp_proxy/ is this file's directory; project root is parent
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config.toml"
        if rtoml and config_path.exists():
            with config_path.open("r", encoding="utf-8") as f:
                return rtoml.load(f)
    except Exception:
        pass
    return {}


_TOML = _load_toml()


def _get_toml_db(key: str, default: object) -> object:
    try:
        return _TOML.get("gateway", {}).get("database", {}).get(key, default)
    except Exception:
        return default


DB_HOST: str = os.getenv("DB_HOST", str(_get_toml_db("host", "localhost")))
DB_PORT: int = int(os.getenv("DB_PORT", str(_get_toml_db("port", 3306))))
DB_USER: str = os.getenv("DB_USER", str(_get_toml_db("user", "root")))
DB_PASSWORD: str = os.getenv("DB_PASSWORD", str(_get_toml_db("password", "")))
# Default to did_db per project requirement
DB_NAME: str = os.getenv("DB_NAME", str(_get_toml_db("database", "did_db")))
DB_CHARSET: str = os.getenv("DB_CHARSET", str(_get_toml_db("charset", "utf8mb4")))
DB_CONNECT_TIMEOUT: float = float(
    os.getenv("DB_CONNECT_TIMEOUT", str(_get_toml_db("connect_timeout", 5.0)))
)
