"""D-21：日志管道自身不得报错（GBK 控制台 / 共享文件轮转）。

实测根因（2026-10-23 真机取证 + 本文件复现）：
- `soul.err.log` 里 10-23 00:29–09:06 的 18 条 traceback **不是编码问题**，而是
  `TimedRotatingFileHandler.doRollover()` 的 `os.rename` 撞上另一个进程（body）正持有
  同一个 `data/logs/elysia.log` ⇒ `PermissionError [WinError 32]` ⇒ logging 的
  `handleError` 把 traceback 写进 stderr，**那条日志本身也被丢掉**。
  `_harden_stream` 只加固控制台流，管不到文件 handler 的轮转。
- 全角「｜」/emoji 在 GBK 下本身已被 `_harden_stream(errors="replace")` 兜住
  （子进程 cp936 复现：stderr 0 字节、无 `--- Logging error ---`）。

修法（本文件同时钉住三条）：
1. soul 与 body 的 JSON 文件日志分开（elysia.log / elysia.body.log），不共用轮转目标；
2. 文件轮转失败吞掉（不写 traceback、不丢当前记录，下一条再试）；
3. 控制台 handler 自带编码兜底（流不可 reconfigure 也不许 handleError）。

取消测试：
- 把 `_file_log_name()` 改回恒为 elysia.log ⇒ `test_soul_and_body_log_to_distinct_files` 红；
- 把 `_TolerantTimedRotatingFileHandler` 换回标准 `TimedRotatingFileHandler` ⇒
  `test_rotation_blocked_by_another_handle_is_silent` 红（stderr 出现 `--- Logging error ---`）；
- 把 `_TolerantStreamHandler` 换回标准 `StreamHandler` ⇒
  `test_stream_handler_survives_unencodable_without_self_error` 红。
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from elysia.core.log import (
    BODY_LOG_FILE_NAME,
    LOG_FILE_NAME,
    _file_log_name,
    _TolerantStreamHandler,
    _TolerantTimedRotatingFileHandler,
    setup_logging,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _teardown_logging() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    if extra:
        env.update(extra)
    return env


def _run_script(script: Path, *args: str, env: dict[str, str] | None = None) -> Any:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(REPO_ROOT),
        env=_subprocess_env(env),
        capture_output=True,
        timeout=180,
    )


# ── 1. GBK 控制台：任何字符都不许让日志管道自己报错 ────────────────────
def test_cp936_console_does_not_self_error(tmp_path: Path) -> None:
    """子进程 + cp936 + 重定向：｜/emoji/生僻字都不得触发 `--- Logging error ---`，
    且文件通道（UTF-8 JSON）内容完整。"""
    logs = tmp_path / "logs"
    script = tmp_path / "probe_gbk.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r})\n"
        "import logging\n"
        "import structlog\n"
        "from pathlib import Path\n"
        "from elysia.core.log import setup_logging\n"
        f"setup_logging(level='INFO', log_dir=Path({str(logs)!r}))\n"
        "logging.getLogger('elysia.soul.heartbeat').info(\n"
        "    'checkpoint created ｜ %s ｜ seq=%d', 'p0-probe', 1)\n"
        "structlog.get_logger('elysia.probe').warning(\n"
        "    '全角竖线｜与 emoji 🚀 生僻字 𠮷', evidence='测试')\n"
        "print('stdout 直写：｜🚀𠮷')\n",
        encoding="utf-8",
    )
    proc = _run_script(script, env={"PYTHONIOENCODING": "cp936"})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert b"--- Logging error ---" not in proc.stderr, proc.stderr.decode("utf-8", "replace")
    text = (logs / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "全角竖线｜与 emoji 🚀 生僻字 𠮷" in text
    assert "checkpoint created ｜ p0-probe ｜ seq=1" in text


# ── 2. 控制台 handler：流不可 recconfigure 时也不许 handleError ─────────
class _StrictCp936Stream:
    """模拟一个没有 reconfigure（pytest capture / 自定义流）的严格 cp936 流。"""

    encoding = "cp936"

    def __init__(self) -> None:
        self.chunks: list[str] = []

    def write(self, text: str) -> int:
        text.encode("cp936")  # 不可编码 ⇒ UnicodeEncodeError（strict）
        self.chunks.append(text)
        return len(text)

    def flush(self) -> None:
        return


def test_stream_handler_survives_unencodable_without_self_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[logging.LogRecord] = []
    monkeypatch.setattr(logging.Handler, "handleError", lambda _self, record: errors.append(record))

    stream = _StrictCp936Stream()
    handler = _TolerantStreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("elysia.test.tolerant_stream")
    logger.handlers = [handler]
    logger.setLevel("INFO")
    logger.propagate = False
    try:
        logger.info("｜🚀")
    finally:
        logger.handlers = []
        handler.close()

    assert errors == [], "控制台编码失败不许走 logging.handleError"
    assert stream.chunks, "应当用 errors=replace 重写一次（而不是整条丢掉）"


# ── 3. soul / body 不共用轮转目标（根因：Windows 上同一文件无法被两个进程同时轮转）──
def test_soul_and_body_log_to_distinct_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import __main__ as main_module

    original = getattr(main_module, "__spec__", None)
    try:
        monkeypatch.setattr(
            main_module,
            "__spec__",
            types.SimpleNamespace(name="elysia.body.main"),
            raising=False,
        )
        assert _file_log_name() == BODY_LOG_FILE_NAME
        body_dir = tmp_path / "body"
        setup_logging(level="INFO", log_dir=body_dir)
        assert (body_dir / BODY_LOG_FILE_NAME).exists()
        assert not (body_dir / LOG_FILE_NAME).exists(), "body 不能与 soul 共用 elysia.log"
        _teardown_logging()

        monkeypatch.setattr(
            main_module,
            "__spec__",
            types.SimpleNamespace(name="elysia.soul.main"),
            raising=False,
        )
        assert _file_log_name() == LOG_FILE_NAME
        soul_dir = tmp_path / "soul"
        setup_logging(level="INFO", log_dir=soul_dir)
        assert (soul_dir / LOG_FILE_NAME).exists()
    finally:
        _teardown_logging()
        monkeypatch.setattr(main_module, "__spec__", original, raising=False)


# ── 4. 两个 handler 都换成了容忍版 ─────────────────────────────────────
def test_setup_logging_uses_tolerant_handlers(tmp_path: Path) -> None:
    setup_logging(level="INFO", log_dir=tmp_path)
    try:
        handlers = logging.getLogger().handlers
        file_handlers = [
            handler
            for handler in handlers
            if isinstance(handler, logging.handlers.TimedRotatingFileHandler)
        ]
        assert file_handlers and isinstance(file_handlers[0], _TolerantTimedRotatingFileHandler)
        console_handlers = [
            handler
            for handler in handlers
            if isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler)
        ]
        assert console_handlers and isinstance(console_handlers[0], _TolerantStreamHandler)
    finally:
        _teardown_logging()


# ── 5. 轮转被另一个句柄占用：不得写 traceback、不得丢当前记录 ───────────
def test_rotation_blocked_by_another_handle_is_silent(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    target = logs / LOG_FILE_NAME
    script = tmp_path / "probe_rotate.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r})\n"
        "import logging\n"
        "from pathlib import Path\n"
        "from elysia.core.log import setup_logging\n"
        f"setup_logging(level='INFO', log_dir=Path({str(logs)!r}))\n"
        "for handler in logging.getLogger().handlers:\n"
        "    if isinstance(handler, logging.handlers.TimedRotatingFileHandler):\n"
        "        handler.rolloverAt = time.time() - 3600\n"
        "logging.getLogger('elysia.soul.heartbeat').info(\n"
        "    'checkpoint created | %s | seq=%d | now=%.3f', 'p0-repro', 42, 1.5)\n"
        "logging.shutdown()\n",
        encoding="utf-8",
    )
    # 父进程持有同文件的句柄 —— 等价于真机里 body 正占着 elysia.log
    with open(target, "ab") as holder:
        assert holder  # 句柄始终开着，子进程 rename 才会失败
        proc = _run_script(script)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert b"--- Logging error ---" not in proc.stderr, proc.stderr.decode("utf-8", "replace")
    assert not list(logs.glob("elysia.log.*")), "另一个句柄占着时 rename 本就不可能成功"
    text = target.read_text(encoding="utf-8")
    assert "checkpoint created | p0-repro | seq=42" in text, "轮转失败也不许丢这条日志"


# ── 6. 没有争用时轮转必须照常工作（根因修完不能把轮转弄坏）─────────────
def test_single_process_rotation_still_works(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    script = tmp_path / "probe_rotate_ok.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r})\n"
        "import logging\n"
        "from pathlib import Path\n"
        "from elysia.core.log import setup_logging\n"
        f"setup_logging(level='INFO', log_dir=Path({str(logs)!r}))\n"
        "for handler in logging.getLogger().handlers:\n"
        "    if isinstance(handler, logging.handlers.TimedRotatingFileHandler):\n"
        "        handler.rolloverAt = time.time() - 3600\n"
        "logging.getLogger('elysia.soul.heartbeat').info(\n"
        "    'checkpoint created | %s | seq=%d | now=%.3f', 'p0-repro', 43, 1.5)\n"
        "logging.shutdown()\n",
        encoding="utf-8",
    )
    proc = _run_script(script)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert b"--- Logging error ---" not in proc.stderr
    assert list(logs.glob("elysia.log.*")), "没有争用时应当照常轮转"
    assert "checkpoint created | p0-repro | seq=43" in (logs / LOG_FILE_NAME).read_text(
        encoding="utf-8"
    )


# ── 7. checkpoint 文案已换 ASCII（D-21 顺手项，避免再依赖控制台编码）─────
def test_checkpoint_log_message_is_ascii() -> None:
    import inspect

    from elysia.soul.heartbeat import SoulHeartbeat

    source = inspect.getsource(SoulHeartbeat._save_checkpoint)
    assert "checkpoint created | %s | seq=%d | now=%.3f" in source
    assert "｜" not in source, "checkpoint 文案里不应再用全角竖线"
