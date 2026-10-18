"""structlog JSON 日志基础设施（评估报告 §8-1：日志规范在业务代码前落地）。

设计：
- 双通道：控制台彩色可读 + 文件 JSON（按天轮转，保留 14 天）
- 统一入口 get_logger()，业务模块一律使用，禁止裸 print
- 时间戳 ISO UTC，含异常堆栈渲染
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import cast

import structlog

_LOGGER_NAME = "elysia"


def _harden_stream(stream: object) -> None:
    """让日志控制台流**永不因编码失败而崩溃**（P0-R15）。

    背景（2026-10-18 实测）：Windows 上 `soul.err.log` 被重定向时，Python 按
    **cp936(GBK)** 编码写 stderr；而日志事件里含 `⇒`、emoji 等非 GBK 字符 ⇒
    标准库 logging 在 emit 里抛 `UnicodeEncodeError`，于是 stderr 开头全是
    `--- Logging error ---` + 它自己的 traceback，**她的真实死因反而被盖住**。
    （R2/R14 的取证链就建立在 stderr 上，所以必须先保证"日志管道自身不报错"。）

    做法：保留原编码，只把不可编码字符替换为 `?`（`errors="replace"`）。
    安全边界：
    - `pythonw.exe` 下 `sys.stdout`/`sys.stderr` 可能是 **None**
      （本工程踩过：面板一启动就退出），先判空；
    - 非 TextIOWrapper（pytest capture / 自定义流）没有 `reconfigure`，跳过；
    - 流已关闭或重配失败：吞掉，绝不因"让日志更安全"而让进程起不来。
    """
    if stream is None:
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    # 已关闭 / 不可重配：保持原样，日志另有文件通道兜底
    with contextlib.suppress(ValueError, OSError, AttributeError):
        reconfigure(errors="replace")


def setup_logging(*, level: str = "INFO", log_dir: Path | None = None) -> None:
    """配置全局日志。

    Args:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR）。
        log_dir: 文件日志目录（按天轮转）；None 时仅输出控制台。
    """
    # P0-R15：先加固控制台流（保留编码、只放宽错误处理），再建 handler。
    # 文件 handler 明确是 UTF-8，不受影响，保持既有格式不变。
    _harden_stream(sys.stdout)
    _harden_stream(sys.stderr)

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
