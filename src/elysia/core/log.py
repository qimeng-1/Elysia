"""structlog JSON 日志基础设施（评估报告 §8-1：日志规范在业务代码前落地）。

设计：
- 双通道：控制台彩色可读 + 文件 JSON（按天轮转，保留 14 天）
- 统一入口 get_logger()，业务模块一律使用，禁止裸 print
- 时间戳 ISO UTC，含异常堆栈渲染
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import cast

import structlog

_LOGGER_NAME = "elysia"


def setup_logging(*, level: str = "INFO", log_dir: Path | None = None) -> None:
    """配置全局日志。

    Args:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR）。
        log_dir: 文件日志目录（按天轮转）；None 时仅输出控制台。
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    handlers: list[logging.Handler] = []
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            log_dir / "elysia.log",
            when="midnight",
            backupCount=14,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[
                    *shared_processors,
                    structlog.processors.JSONRenderer(ensure_ascii=False),
                ],
                foreign_pre_chain=shared_processors,
            )
        )
        handlers.append(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                *shared_processors,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            foreign_pre_chain=shared_processors,
        )
    )
    handlers.append(console_handler)

    root.handlers = handlers

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取统一 logger；name 为空时使用默认名。

    注意：structlog 在首次使用时惰性绑定，此处返回 lazy proxy，
    但接口与 BoundLogger 一致，故标注为 BoundLogger。
    """
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name or _LOGGER_NAME))
