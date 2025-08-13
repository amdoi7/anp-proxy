"""DID-WBA helpers for WSS handshake using HTTP headers.

This module integrates the agent_connect.authentication utilities per docs/anp-did-spec.md.
Client:
- Build DID-WBA Authorization header using DIDWbaAuthHeader for a target URL (ws/wss mapped to http/https).
Server (Gateway):
- Verify incoming Authorization header with extract_auth_header_parts, resolve_did_wba_document, and verify_auth_header_signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_connect.authentication import DIDWbaAuthHeader

from ..anp_sdk.anp_auth.did_wba_verifier import (
    DidWbaVerifier as SdkDidWbaVerifier,
    DidWbaVerifierConfig,
    DidWbaVerifierError,
)
from .config import AuthConfig
from .log_base import get_logger

logger = get_logger(__name__)


@dataclass
class DidAuthResult:
    success: bool
    did: str | None = None
    error: str | None = None
    details: dict[str, Any] | None = None


def _normalize_headers(raw_headers: Any) -> dict[str, str]:
    """Normalize websockets headers (HeadersLike) to lowercase dict[str,str]."""
    try:
        return {k.lower(): v for k, v in raw_headers.items()}  # type: ignore[attr-defined]
    except Exception:
        result: dict[str, str] = {}
        for item in raw_headers:  # type: ignore[assignment]
            try:
                k, v = item
                result[str(k).lower()] = str(v)
            except Exception:
                pass
        return result


class DidWbaVerifierAdapter:
    """Adapter that wraps SDK's DidWbaVerifier for gateway usage."""

    def __init__(self, config: AuthConfig):
        self.config = config
        # Load optional JWT keys for SDK verifier
        jwt_private = None
        jwt_public = None
        try:
            if self.config.jwt_private_key_path:
                jwt_private = str(self.config.jwt_private_key_path.read_text())
            if self.config.jwt_public_key_path:
                jwt_public = str(self.config.jwt_public_key_path.read_text())
        except Exception:
            jwt_private = None
            jwt_public = None

        self._verifier = SdkDidWbaVerifier(
            DidWbaVerifierConfig(
                jwt_private_key=jwt_private,
                jwt_public_key=jwt_public,
            )
        )

        # Store DID-specific configurations
        self._did_configs = {}
        if hasattr(config, "did_configs"):
            self._did_configs = config.did_configs

    async def verify(self, headers_like: Any, domain: str) -> DidAuthResult:
        if not self.config.enabled:
            return DidAuthResult(success=False, error="DID-WBA disabled")

        headers = _normalize_headers(headers_like)
        authorization = headers.get("authorization")
        if not authorization:
            return DidAuthResult(success=False, error="Missing Authorization header")

        try:
            # Use default verifier for now - the SDK should handle DID resolution
            result = await self._verifier.verify_auth_header(authorization, domain)
            did = result.get("did")
            if self.config.allowed_dids and did not in set(self.config.allowed_dids):
                return DidAuthResult(success=False, error="DID not allowed")
            return DidAuthResult(success=True, did=did)

        except DidWbaVerifierError as exc:
            return DidAuthResult(success=False, error=str(exc))
        except Exception as exc:
            logger.error("DID-WBA verification error", error=str(exc))
            return DidAuthResult(success=False, error=str(exc))


def build_auth_headers(auth_config: AuthConfig, gateway_url: str) -> dict[str, str]:
    """Build DID-WBA Authorization headers for a given ws(s) URL.

    Uses agent_connect.authentication.DIDWbaAuthHeader.
    """
    if not auth_config.enabled:
        return {}
    if not auth_config.did_document_path or not auth_config.private_key_path:
        logger.warning(
            "Missing DID document or private key path for DID-WBA client header generation"
        )
        return {}

    client = DIDWbaAuthHeader(
        did_document_path=str(auth_config.did_document_path),
        private_key_path=str(auth_config.private_key_path),
    )

    logger.info("Getting auth header for gateway URL: %s", gateway_url)

    headers = client.get_auth_header(gateway_url)
    # Ensure proper case for websockets extra_headers (we will pass as list of tuples)
    normalized = {
        k if isinstance(k, str) else str(k): v if isinstance(v, str) else str(v)
        for k, v in headers.items()
    }
    return normalized


# Backward-compatible export name for gateway imports
DidWbaVerifier = DidWbaVerifierAdapter
