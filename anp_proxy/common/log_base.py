"""
简洁高效的日志系统 - 基于 Loguru 最佳实践
遵循 KISS 原则，提供结构化日志和性能监控
"""

import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from loguru import logger


class LogConfig:
    """日志配置中心"""

    TIMEZONE = timezone(timedelta(hours=8))  # 东八区
    VALID_LEVELS = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}

    @staticmethod
    def _format_extra(record):
        """提取公共的 extra 字段格式化逻辑"""
        if not record["extra"]:
            return ""
        return " | ".join(f"{k}={v}" for k, v in record["extra"].items())

    @staticmethod
    def console_formatter(record):
        """控制台日志格式 - 彩色输出，简洁明了"""
        # 基础格式：时间 | 级别 | 模块:函数:行号 | 消息
        base = (
            "<green>{time:HH:mm:ss} [CST]</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        )

        # 如果有额外字段，添加到末尾
        extra_str = LogConfig._format_extra(record)
        if extra_str:
            base += f" | <dim>{extra_str}</dim>"

        return base + "\n"

    @staticmethod
    def file_formatter(record):
        """文件日志格式 - 结构化，便于解析"""
        # 基础格式：时间 | 级别 | 模块:函数:行号 | 消息
        base = (
            "{time:HH:mm:ss} [CST] | {level: <8} | {name}:{function}:{line} | {message}"
        )

        # 如果有额外字段，添加到末尾
        extra_str = LogConfig._format_extra(record)
        if extra_str:
            base += f" | {extra_str}"

        return base + "\n"


def setup_logging(
    level: str = "INFO", log_dir: str | None = None, environment: str = "development"
) -> None:
    """配置 Loguru 日志系统"""
    # 验证日志级别
    if level.upper() not in LogConfig.VALID_LEVELS:
        raise ValueError(
            f"Invalid log level: {level}. Must be one of {LogConfig.VALID_LEVELS}"
        )

    # 移除默认处理器
    logger.remove()

    # 环境区分配置
    if environment == "production":
        # 生产环境：只输出到文件，JSON格式，诊断信息关闭
        # 使用项目根目录的 logs 文件夹，与 manage.sh 保持一致
        log_dir_path = (
            Path(log_dir) if log_dir else Path(__file__).parent.parent.parent / "logs"
        )
        log_dir_path.mkdir(parents=True, exist_ok=True)

        today = datetime.now(LogConfig.TIMEZONE).strftime("%Y%m%d")
        log_file = log_dir_path / f"anp_proxy_{today}.log"

        logger.add(
            str(log_file),
            format=LogConfig.file_formatter,
            level=level,
            rotation="1 day",
            retention="30 days",
            compression="gz",
            encoding="utf-8",
            backtrace=False,  # 生产环境关闭
            diagnose=False,  # 生产环境关闭
        )
    else:
        # 开发环境：控制台 + 文件输出
        logger.add(
            sys.stdout,
            format=LogConfig.console_formatter,
            level=level,
            colorize=True,
            backtrace=True,  # 显示完整堆栈跟踪
            diagnose=True,  # 显示变量值
        )

        # 文件输出 - 结构化，适合生产环境
        # 使用项目根目录的 logs 文件夹，与 manage.sh 保持一致
        log_dir_path = (
            Path(log_dir) if log_dir else Path(__file__).parent.parent.parent / "logs"
        )
        log_dir_path.mkdir(parents=True, exist_ok=True)

        today = datetime.now(LogConfig.TIMEZONE).strftime("%Y%m%d")
        log_file = log_dir_path / f"anp_proxy_{today}.log"

        logger.add(
            str(log_file),
            format=LogConfig.file_formatter,
            level=level,
            rotation="1 day",
            retention="30 days",
            compression="gz",
            encoding="utf-8",
            backtrace=True,  # 显示完整堆栈跟踪
            diagnose=True,  # 显示变量值
        )


def log_execution_time(operation_name: str = "operation"):
    """性能监控装饰器"""
    import asyncio

    def decorator(func):
        @wraps(func)
        @logger.catch
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            result = await func(*args, **kwargs)
            duration = round((time.perf_counter() - start) * 1000, 1)
            logger.info(f"{operation_name} 完成", duration_ms=duration)
            return result

        @wraps(func)
        @logger.catch
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            result = func(*args, **kwargs)
            duration = round((time.perf_counter() - start) * 1000, 1)
            logger.info(f"{operation_name} 完成", duration_ms=duration)
            return result

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


@contextmanager
def log_operation(operation_name: str, **context):
    """上下文管理器版本的操作日志"""
    start = time.perf_counter()
    ctx_logger = logger.bind(operation=operation_name, **context)

    try:
        yield ctx_logger
        duration = round((time.perf_counter() - start) * 1000, 1)
        ctx_logger.info(f"{operation_name} 完成", duration_ms=duration)
    except Exception as e:
        duration = round((time.perf_counter() - start) * 1000, 1)
        ctx_logger.error(f"{operation_name} 失败", error=str(e), duration_ms=duration)
        raise


__all__ = [
    "setup_logging",
    "log_execution_time",
    "log_operation",
    "logger",
]
