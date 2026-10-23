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
from typing import Any, cast

import structlog

_LOGGER_NAME = "elysia"

# ── D-21：文件日志按进程分开 ─────────────────────────────────────────
# soul 保持 `elysia.log`（既有消费者：面板/审计脚本读的就是它）；body 用
# `elysia.body.log`。两个常驻进程若共用一个 TimedRotatingFileHandler 目标，
# 午夜轮转时 A 要 rename、B 正持有该文件句柄 ⇒ Windows 上必 PermissionError
# （os.rename → WinError 32），logging 随后把整段 traceback 经 handleError 写进
# stderr（我们的死亡第一现场），且那条日志本身被丢掉（10-23 00:29–09:06 实测
# 18 次 checkpoint 记录全部如此）。`_harden_stream` 只加固控制台流，管不到这里。
LOG_FILE_NAME = "elysia.log"
BODY_LOG_FILE_NAME = "elysia.body.log"


def _entry_module_name() -> str:
    """`python -m <module>` 时入口模块的完整名；拿不到返回空串。"""
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    name = getattr(spec, "name", None)
    return name if isinstance(name, str) else ""


def _file_log_name() -> str:
    """当前进程该写哪个文件日志（D-21：soul 与 body 不共用轮转目标）。"""
    parts = _entry_module_name().split(".")
    if len(parts) >= 2 and parts[-2:] == ["body", "main"]:
        return BODY_LOG_FILE_NAME
    return LOG_FILE_NAME


class _TolerantStreamHandler(logging.StreamHandler[Any]):
    """控制台 handler：**任何字符都不许让日志管道自身报错**（D-21 统一护栏）。

    第一道防線是 `_harden_stream(errors="replace")`（P0-R15，保留原编码只放宽错误）；
    但 pytest capture、自定义流、或运行期被替换的流可能没有 `reconfigure`、或仍是
    `strict` ⇒ `emit` 抛 `UnicodeEncodeError`，logging 的 `handleError` 会把
    `--- Logging error ---` + traceback 写进 stderr。这里兜底：用目标编码 `replace`
    后重写一次；仍写不出就静默丢弃这一条——绝不 `handleError`。
    """

    def emit(self, record: logging.LogRecord) -> None:
        # 注意：**不能**调 `super().emit()`——`logging.StreamHandler.emit` 自己会把
        # 所有异常吞进 `handleError`（那正是 `--- Logging error ---` 的来源）。
        # 所以这里完整实现"格式化 → 写 → flush"，编码失败时用 replace 重写一次。
        try:
            message = self.format(record)
            stream = self.stream
            stream.write(message + self.terminator)
            self.flush()
        except UnicodeEncodeError:
            try:
                stream = self.stream
                encoding = getattr(stream, "encoding", None) or "ascii"
                safe = message.encode(encoding, "replace").decode(encoding, "replace")
                stream.write(safe + self.terminator)
                self.flush()
            except Exception:
                return
        except RecursionError:
            raise
        except Exception:  # 流已关闭 / 其它 IO 问题：同样不许自报错
            return


class _TolerantTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """按天轮转的文件 handler：**轮转失败不许写 traceback、也不许丢当前这条日志**。

    标准库 `doRollover()` 在 `os.rename` 失败时抛 OSError；`Handler.handle()` 捕获后
    调 `handleError()` → stderr 出现 `--- Logging error ---`。这里吞掉轮转异常：
    跳过本轮轮转，日志继续写当前文件（`FileHandler.emit` 会在 stream 被关掉后重开），
    `rolloverAt` 保持过去值 ⇒ 下一条记录会再试。任何 OSError 都不外抛。
    """

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError:
            # 轮转失败 ⇒ 跳过本轮（不 handleError、不丢当前记录）：
            # stream 被 doRollover 关掉后，下一次 FileHandler.emit 会重开同一个文件写；
            # rolloverAt 仍是过去值 ⇒ 下一条记录会再试（占用解除后自动轮转）。
            return


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
        # D-21：soul → elysia.log，body → elysia.body.log（不共用轮转目标）
        file_handler = _TolerantTimedRotatingFileHandler(
            log_dir / _file_log_name(),
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

    # D-21：控制台 handler 自带"编码失败也不自报错"的兜底（不依赖流可 reconfigure）
    console_handler = _TolerantStreamHandler(sys.stdout)
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
