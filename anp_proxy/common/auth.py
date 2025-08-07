"""Authentication and authorization for ANP Proxy."""

import hashlib
import hmac
import secrets
import time
from typing import Any

import jwt
from pydantic import BaseModel

from .config import AuthConfig
from .log_base import get_logger

logger = get_logger(__name__)


class AuthToken(BaseModel):
    """Authentication token structure."""

    user_id: str
    issued_at: float
    expires_at: float
    permissions: dict[str, Any] = {}


class AuthManager:
    """Manages authentication and authorization."""

    def __init__(self, config: AuthConfig) -> None:
        """
        Initialize auth manager.

        Args:
            config: Authentication configuration
        """
        self.config = config
        self.shared_secret = config.shared_secret or self._generate_secret()
        self.failed_attempts: dict[str, int] = {}
        self.blocked_until: dict[str, float] = {}

        logger.info("Auth manager initialized", enabled=config.enabled)

    def _generate_secret(self) -> str:
        """Generate a random shared secret."""
        secret = secrets.token_urlsafe(32)
        logger.warning(
            "Generated random shared secret. "
            "For production, set ANP_AUTH__SHARED_SECRET environment variable."
        )
        return secret

    def create_token(
        self,
        user_id: str,
        permissions: dict[str, Any] | None = None
    ) -> str:
        """
        Create an authentication token.

        Args:
            user_id: User identifier
            permissions: User permissions

        Returns:
            JWT token string
        """
        if not self.config.enabled:
            return ""

        now = time.time()
        expires_at = now + self.config.token_expiry

        token_data = AuthToken(
            user_id=user_id,
            issued_at=now,
            expires_at=expires_at,
            permissions=permissions or {}
        )

        payload = token_data.dict()
        token = jwt.encode(payload, self.shared_secret, algorithm="HS256")

        logger.debug("Token created", user_id=user_id, expires_at=expires_at)
        return token

    def verify_token(self, token: str) -> AuthToken | None:
        """
        Verify and decode an authentication token.

        Args:
            token: JWT token string

        Returns:
            Decoded token data if valid, None otherwise
        """
        if not self.config.enabled:
            return AuthToken(user_id="anonymous", issued_at=time.time(), expires_at=time.time() + 3600)

        try:
            payload = jwt.decode(token, self.shared_secret, algorithms=["HS256"])
            token_data = AuthToken(**payload)

            # Check expiration
            if time.time() > token_data.expires_at:
                logger.warning("Token expired", user_id=token_data.user_id)
                return None

            logger.debug("Token verified", user_id=token_data.user_id)
            return token_data

        except jwt.InvalidTokenError as e:
            logger.warning("Invalid token", error=str(e))
            return None
        except Exception as e:
            logger.error("Token verification failed", error=str(e))
            return None

    def authenticate_connection(
        self,
        client_id: str,
        credentials: dict[str, Any]
    ) -> str | None:
        """
        Authenticate a WebSocket connection.

        Args:
            client_id: Client identifier (IP, etc.)
            credentials: Authentication credentials

        Returns:
            Authentication token if successful, None otherwise
        """
        if not self.config.enabled:
            return self.create_token("anonymous")

        # Check if client is blocked
        if self._is_blocked(client_id):
            logger.warning("Client blocked due to too many failed attempts", client_id=client_id)
            return None

        # Validate credentials
        if self._validate_credentials(credentials):
            # Reset failed attempts on successful auth
            self.failed_attempts.pop(client_id, None)
            self.blocked_until.pop(client_id, None)

            user_id = credentials.get("user_id", client_id)
            return self.create_token(user_id)
        else:
            # Track failed attempt
            self._record_failed_attempt(client_id)
            logger.warning("Authentication failed", client_id=client_id)
            return None

    def _validate_credentials(self, credentials: dict[str, Any]) -> bool:
        """
        Validate authentication credentials.

        Args:
            credentials: Credentials to validate

        Returns:
            True if valid, False otherwise
        """
        # Simple shared secret validation
        provided_secret = credentials.get("shared_secret")
        if not provided_secret:
            return False

        return hmac.compare_digest(provided_secret, self.shared_secret)

    def _is_blocked(self, client_id: str) -> bool:
        """Check if client is currently blocked."""
        if client_id in self.blocked_until:
            if time.time() < self.blocked_until[client_id]:
                return True
            else:
                # Block period expired, clean up
                del self.blocked_until[client_id]
        return False

    def _record_failed_attempt(self, client_id: str) -> None:
        """Record a failed authentication attempt."""
        self.failed_attempts[client_id] = self.failed_attempts.get(client_id, 0) + 1

        if self.failed_attempts[client_id] >= self.config.max_attempts:
            # Block client for 15 minutes
            self.blocked_until[client_id] = time.time() + 900
            logger.warning(
                "Client blocked due to repeated failures",
                client_id=client_id,
                attempts=self.failed_attempts[client_id]
            )

    def create_challenge(self) -> dict[str, Any]:
        """
        Create an authentication challenge.

        Returns:
            Challenge data
        """
        if not self.config.enabled:
            return {}

        nonce = secrets.token_urlsafe(16)
        timestamp = int(time.time())

        return {
            "nonce": nonce,
            "timestamp": timestamp,
            "algorithm": "HS256"
        }

    def verify_challenge_response(
        self,
        challenge: dict[str, Any],
        response: dict[str, Any]
    ) -> bool:
        """
        Verify response to authentication challenge.

        Args:
            challenge: Original challenge data
            response: Client response

        Returns:
            True if response is valid, False otherwise
        """
        if not self.config.enabled:
            return True

        try:
            # Verify timestamp (within 5 minutes)
            now = int(time.time())
            if abs(now - challenge["timestamp"]) > 300:
                return False

            # Calculate expected response
            message = f"{challenge['nonce']}:{challenge['timestamp']}"
            expected_signature = hmac.new(
                self.shared_secret.encode(),
                message.encode(),
                hashlib.sha256
            ).hexdigest()

            provided_signature = response.get("signature")
            return hmac.compare_digest(expected_signature, provided_signature or "")

        except Exception as e:
            logger.error("Challenge verification failed", error=str(e))
            return False
