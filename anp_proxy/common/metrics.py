"""Connection metrics and health monitoring definitions."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class HealthStatus(Enum):
    """Connection health status enumeration."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ConnectionPhase(Enum):
    """Connection lifecycle phases."""

    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    REGISTERING = "registering"
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"


@dataclass
class ConnectionMetrics:
    """Connection performance and health metrics."""

    connection_id: str
    did: str
    service_urls: list[str] = field(default_factory=list)
    connected_at: datetime = field(default_factory=datetime.now)
    last_ping: datetime = field(default_factory=datetime.now)
    last_request: datetime | None = None

    # Request statistics
    total_requests: int = 0
    pending_requests: int = 0
    success_count: int = 0
    error_count: int = 0

    # Performance metrics
    avg_response_time: float = 0.0
    min_response_time: float = float("inf")
    max_response_time: float = 0.0

    # Health status
    health_status: HealthStatus = HealthStatus.HEALTHY
    phase: ConnectionPhase = ConnectionPhase.CONNECTING

    def calculate_error_rate(self) -> float:
        """Calculate error rate percentage."""
        total_completed = self.success_count + self.error_count
        if total_completed == 0:
            return 0.0
        return (self.error_count / total_completed) * 100.0

    def update_response_time(self, response_time: float) -> None:
        """Update response time statistics."""
        if response_time < self.min_response_time:
            self.min_response_time = response_time
        if response_time > self.max_response_time:
            self.max_response_time = response_time

        # Update average (exponential moving average)
        if self.avg_response_time == 0.0:
            self.avg_response_time = response_time
        else:
            # Use alpha = 0.1 for exponential moving average
            self.avg_response_time = 0.1 * response_time + 0.9 * self.avg_response_time

    def is_healthy(self) -> bool:
        """Check if connection is considered healthy."""
        # Check error rate
        error_rate = self.calculate_error_rate()
        if error_rate > 10.0:  # More than 10% error rate
            return False

        # Check response time
        if self.avg_response_time > 5.0:  # More than 5 seconds average
            return False

        # Check pending requests
        if self.pending_requests > 100:  # More than 100 pending requests
            return False

        # Check last ping time
        time_since_ping = (datetime.now() - self.last_ping).total_seconds()
        if time_since_ping > 120:  # No ping for more than 2 minutes
            return False

        return True

    def get_health_status(self) -> HealthStatus:
        """Get current health status based on metrics."""
        if not self.is_healthy():
            # Determine if degraded or unhealthy
            error_rate = self.calculate_error_rate()
            time_since_ping = (datetime.now() - self.last_ping).total_seconds()

            if (
                error_rate > 50.0
                or self.avg_response_time > 30.0
                or time_since_ping > 300
            ):  # 5 minutes
                return HealthStatus.UNHEALTHY
            else:
                return HealthStatus.DEGRADED

        return HealthStatus.HEALTHY

    def to_dict(self) -> dict:
        """Convert metrics to dictionary for serialization."""
        return {
            "connection_id": self.connection_id,
            "did": self.did,
            "service_urls": self.service_urls,
            "connected_at": self.connected_at.isoformat(),
            "last_ping": self.last_ping.isoformat(),
            "last_request": self.last_request.isoformat()
            if self.last_request
            else None,
            "total_requests": self.total_requests,
            "pending_requests": self.pending_requests,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "error_rate": self.calculate_error_rate(),
            "avg_response_time": self.avg_response_time,
            "min_response_time": self.min_response_time
            if self.min_response_time != float("inf")
            else 0.0,
            "max_response_time": self.max_response_time,
            "health_status": self.health_status.value,
            "phase": self.phase.value,
        }


@dataclass
class ServiceMetrics:
    """Service-level aggregated metrics."""

    service_url: str
    active_connections: int = 0
    healthy_connections: int = 0
    total_requests: int = 0
    avg_response_time: float = 0.0
    error_rate: float = 0.0
    last_updated: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Convert service metrics to dictionary."""
        return {
            "service_url": self.service_url,
            "active_connections": self.active_connections,
            "healthy_connections": self.healthy_connections,
            "total_requests": self.total_requests,
            "avg_response_time": self.avg_response_time,
            "error_rate": self.error_rate,
            "last_updated": self.last_updated.isoformat(),
        }
