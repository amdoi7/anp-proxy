"""Logging configuration and utilities."""

import logging
import logging.handlers
import os
import sys

import structlog

from .config import LogConfig

# Global initialization state
_logging_initialized = False
_default_log_level = logging.INFO


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


def setup_logging(
    level: int = logging.INFO,
    log_file: str | None = None,
    include_location: bool = True,
    enable_console_colors: bool = True,
    force_reconfigure: bool = False,
) -> None:
    """
    Configure structured logging for ANP Proxy.

    Args:
        level: The logging level
        log_file: The log file path, default is None (auto-generated)
        include_location: Whether to include filename and line number
        enable_console_colors: Whether to enable colored console output
        force_reconfigure: Whether to force reconfiguration even if already configured
    """
    # Configure standard library logging
    root_logger = logging.getLogger()

    # Check if already configured
    if not force_reconfigure and root_logger.handlers:
        # Already configured, just update level if needed
        root_logger.setLevel(level)
        return

    root_logger.setLevel(level)

    # Remove existing handlers only if force_reconfigure is True
    if force_reconfigure:
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    # Get log file path
    if log_file is None:
        # Get the project root (anp-proxy/ directory)
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        log_dir = os.path.join(project_root, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "anp_proxy.log")

    # Configure structlog processors for console output
    console_processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if include_location:
        console_processors.append(
            structlog.processors.CallsiteParameterAdder(
                parameters=[
                    structlog.processors.CallsiteParameter.FILENAME,
                    structlog.processors.CallsiteParameter.LINENO,
                ]
            )
        )

    # Add console renderer
    console_processors.append(structlog.dev.ConsoleRenderer(colors=False))

    # Configure structlog for console output
    structlog.configure(
        processors=console_processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,  # type: ignore[arg-type]
        cache_logger_on_first_use=True,
    )

    # Decide whether to enable ANSI colors based on TTY and env flags
    try:
        is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    except Exception:
        is_tty = False
    env_no_color = os.environ.get("NO_COLOR") or os.environ.get("ANP_NO_COLOR")
    console_colors_enabled = bool(enable_console_colors and is_tty and not env_no_color)

    # Add console handler with color support (disabled for non-TTY or NO_COLOR)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)

    if console_colors_enabled:
        console_handler.setFormatter(ColoredFormatter("%(message)s"))
    else:
        console_handler.setFormatter(logging.Formatter("%(message)s"))

    root_logger.addHandler(console_handler)

    # Add file handler with clean format (no color codes)
    try:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)

        # Create a clean formatter for file output that removes color codes
        class CleanFormatter(logging.Formatter):
            def format(self, record):
                # Remove ANSI color codes from the message
                import re

                ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
                if hasattr(record, "msg"):
                    record.msg = ansi_escape.sub("", str(record.msg))
                return super().format(record)

        file_handler.setFormatter(CleanFormatter("%(message)s"))
        root_logger.addHandler(file_handler)

        logger = structlog.get_logger("setup")
        logger.info("Logging to file", log_file=log_file)

    except Exception as e:
        logger = structlog.get_logger("setup")
        logger.error("Failed to set up file logging", log_file=log_file, error=str(e))


def setup_logging_with_config(
    config: LogConfig,
    force_reconfigure: bool = False,
) -> None:
    """
    Configure structured logging for ANP Proxy using LogConfig.

    Args:
        config: Logging configuration
        force_reconfigure: Whether to force reconfiguration even if already configured
    """
    global _logging_initialized

    # Configure standard library logging
    root_logger = logging.getLogger()
    level = getattr(logging, config.level)

    # Check if already configured
    if not force_reconfigure and root_logger.handlers:
        # Already configured, just update level if needed
        root_logger.setLevel(level)
        return

    root_logger.setLevel(level)

    # Remove existing handlers only if force_reconfigure is True
    if force_reconfigure:
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    # Create formatters; ensure filename and line number are included
    log_format = config.format
    has_filename = "%(filename)" in log_format
    has_lineno = "%(lineno)" in log_format
    if not (has_filename and has_lineno):
        if "%(message)s" in log_format:
            log_format = log_format.replace(
                "%(message)s", "%(filename)s:%(lineno)d: %(message)s"
            )
        else:
            # Fallback: append location info
            log_format = f"{log_format} %(filename)s:%(lineno)d"

    # Add newline to format
    log_format = log_format + "\n"

    formatter = logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")
    colored_formatter = ColoredFormatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")

    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(colored_formatter)
    root_logger.addHandler(console_handler)

    # File handler (optional)
    if config.file:
        try:
            config.file.parent.mkdir(parents=True, exist_ok=True)

            # Parse max_size (e.g., "10MB" -> 10*1024*1024)
            max_bytes = _parse_size(config.max_size)

            file_handler = logging.handlers.RotatingFileHandler(
                filename=config.file,
                maxBytes=max_bytes,
                backupCount=config.backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(level)

            # Create a clean formatter for file output that removes color codes
            class CleanFormatter(logging.Formatter):
                def format(self, record):
                    # Remove ANSI color codes from the message
                    import re

                    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
                    if hasattr(record, "msg"):
                        record.msg = ansi_escape.sub("", str(record.msg))
                    return super().format(record)

            file_handler.setFormatter(
                CleanFormatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")
            )
            root_logger.addHandler(file_handler)

            logger = structlog.get_logger("setup")
            logger.info("Logging to file", log_file=str(config.file))

        except Exception as e:
            logger = structlog.get_logger("setup")
            logger.error(
                "Failed to set up file logging", log_file=str(config.file), error=str(e)
            )

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.CallsiteParameterAdder(
                parameters=[
                    structlog.processors.CallsiteParameter.FILENAME,
                    structlog.processors.CallsiteParameter.LINENO,
                ]
            ),
            structlog.processors.TimeStamper(fmt="ISO"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,  # type: ignore[arg-type]
        cache_logger_on_first_use=True,
    )

    _logging_initialized = True


def setup_enhanced_logging(
    level: str | int = "INFO",
    log_file: str | None = None,
    include_location: bool = True,
    enable_console_colors: bool = True,
    force_reconfigure: bool = False,
) -> None:
    """
    Enhanced logging setup function for main entry modules.

    This function is designed to be used in main entry modules as specified in workspace rules.
    It provides a simplified interface for setting up logging with sensible defaults.

    Args:
        level: The logging level as string or int
        log_file: The log file path, default is None (auto-generated)
        include_location: Whether to include filename and line number
        enable_console_colors: Whether to enable colored console output
        force_reconfigure: Whether to force reconfiguration even if already configured
    """
    # Convert string level to int if needed
    if isinstance(level, str):
        level = getattr(logging, level.upper())

    # Call the existing setup_logging function
    setup_logging(
        level=level,
        log_file=log_file,
        include_location=include_location,
        enable_console_colors=enable_console_colors,
        force_reconfigure=force_reconfigure,
    )


def _ensure_logging_initialized(level: int | None = None) -> None:
    """Ensure logging is initialized only once."""
    global _logging_initialized, _default_log_level

    if not _logging_initialized:
        init_level = level if level is not None else _default_log_level
        setup_logging(level=init_level, include_location=True)
        _logging_initialized = True


def set_default_log_level(level: int) -> None:
    """Set the default log level for automatic initialization."""
    global _default_log_level
    _default_log_level = level


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


def get_logger(name: str, level: int | None = None) -> structlog.BoundLogger:
    """
    Get a structlog logger with the specified name.

    Args:
        name: The name of the logger
        level: Optional logging level for initialization

    Returns:
        A structlog BoundLogger instance
    """
    _ensure_logging_initialized(level)
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

message_logger = get_logger("anp_proxy.message")
auth_logger = get_logger("anp_proxy.auth")
database_logger = get_logger("anp_proxy.database")
common_logger = get_logger("anp_proxy.common")

# Auto-initialize logging when this module is imported
_ensure_logging_initialized()


# Usage Examples:
#
# 1. Basic usage:
#    from anp_proxy.common.log_base import get_logger
#    logger = get_logger(__name__)
#    logger.info("Processing request", request_id="123", method="GET")
#
# 2. Using LoggerMixin:
#    from anp_proxy.common.log_base import LoggerMixin
#    class MyClass(LoggerMixin):
#        def my_method(self):
#            self.logger.info("Method called", param="value")
#
# 3. Using pre-configured loggers:
#    from anp_proxy.common.log_base import message_logger
#    message_logger.error("Failed to send message", error="timeout")
#
# 4. Exception logging:
#    try:
#        # some operation
#        pass
#    except Exception as e:
#        logger.exception("Operation failed", operation="data_processing")
#
# 5. Enhanced logging setup in main entry modules:
#    from anp_proxy.common.log_base import setup_enhanced_logging
#    setup_enhanced_logging(level="DEBUG", log_file="app.log")
#
# 6. Using LogConfig for advanced configuration:
#    from anp_proxy.common.log_base import setup_logging_with_config
#    from anp_proxy.common.config import LogConfig
#    config = LogConfig(level="DEBUG", file=Path("debug.log"))
#    setup_logging_with_config(config)
