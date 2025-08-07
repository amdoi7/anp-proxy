"""Logging configuration and utilities."""

import logging
import logging.handlers
import sys

import structlog

from .config import LogConfig


class ColoredFormatter(logging.Formatter):
    """Custom colored formatter for console output."""

    COLORS = {
        "DEBUG": "\033[94m",  # Blue
        "INFO": "\033[92m",  # Green
        "WARNING": "\033[93m",  # Yellow
        "ERROR": "\033[91m",  # Red
        "CRITICAL": "\033[95m",  # Magenta
        "RESET": "\033[0m",  # Reset
    }

    def format(self, record):
        levelname = record.levelname
        message = super().format(record)
        color = self.COLORS.get(levelname, self.COLORS["RESET"])
        return color + message + self.COLORS["RESET"]


def setup_logging(config: LogConfig) -> None:
    """
    Configure structured logging for ANP Proxy.

    Args:
        config: Logging configuration
    """
    # Configure standard library logging
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.level))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create formatters with file and line number information
    log_format = config.format
    if "%(filename)s" not in log_format:
        # Add location info if not present
        log_format = '[%(asctime)s] %(levelname)-8s %(filename)s:%(lineno)d: %(message)s'

    formatter = logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")
    colored_formatter = ColoredFormatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")

    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(colored_formatter)
    root_logger.addHandler(console_handler)

    # File handler (optional)
    if config.file:
        config.file.parent.mkdir(parents=True, exist_ok=True)

        # Parse max_size (e.g., "10MB" -> 10*1024*1024)
        max_bytes = _parse_size(config.max_size)

        file_handler = logging.handlers.RotatingFileHandler(
            filename=config.file,
            maxBytes=max_bytes,
            backupCount=config.backup_count,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="ISO"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def _parse_size(size_str: str) -> int:
    """
    Parse size string like '10MB' to bytes.

    Args:
        size_str: Size string (e.g., "10MB", "1GB")

    Returns:
        Size in bytes
    """
    size_str = size_str.upper().strip()

    if size_str.endswith("KB"):
        return int(size_str[:-2]) * 1024
    elif size_str.endswith("MB"):
        return int(size_str[:-2]) * 1024 * 1024
    elif size_str.endswith("GB"):
        return int(size_str[:-2]) * 1024 * 1024 * 1024
    elif size_str.endswith("B"):
        return int(size_str[:-1])
    else:
        # Assume bytes if no unit
        return int(size_str)


def get_logger(name: str) -> structlog.BoundLogger:
    """
    Get a structured logger instance.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Structured logger instance
    """
    return structlog.get_logger(name)


class LoggerMixin:
    """Mixin class to add logging capabilities."""

    @property
    def logger(self) -> structlog.BoundLogger:
        """Get logger for this class."""
        if not hasattr(self, "_logger"):
            self._logger = get_logger(self.__class__.__module__)
        return self._logger


# Pre-configured loggers for common components
protocol_logger = get_logger("anp_proxy.protocol")
gateway_logger = get_logger("anp_proxy.gateway")
receiver_logger = get_logger("anp_proxy.receiver")
common_logger = get_logger("anp_proxy.common")
