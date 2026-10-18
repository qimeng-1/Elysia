"""P0-R15：日志管道自身不得因编码失败而崩溃。

事故取证背景（2026-10-18 21:07:06 实测）：`data/logs/soul.err.log` 开头是
`--- Logging error ---` + `Traceback ... logging/__init__.py:1163 in emit`。
根因：Windows 重定向下 `sys.stderr` 是 **cp936(GBK)**，而日志事件里的 `⇒` 等字符
非 GBK 可编码 ⇒ 标准库 logging 自己抛 `UnicodeEncodeError`，把**真正的死因**盖住了。
我们靠 stderr 判死因（R2/R14），所以这条必须先修。

取消测试：`test_setup_logging_survives_cp936_stderr_with_non_gbk_chars`
—— 去掉 `setup_logging` 里的 `_harden_stream` 调用 ⇒ 该用例必红。
"""

from __future__ import annotations

import io
import json
import logging
import sys

import pytest
import structlog

from elysia.core.log import get_logger, setup_logging


@pytest.fixture(autouse=True)
def _restore_logging() -> object:
    """每个用例结束后恢复 logging/structlog，避免把严格流留给后续测试。"""
    root = logging.getLogger()
    saved = list(root.handlers)
    yield
    root.handlers = saved
    structlog.reset_defaults()


def _strict_cp936_stream() -> tuple[io.TextIOWrapper, io.BytesIO]:
    """模拟 Windows 控制台/重定向：cp936 编码 + 严格错误（写不了就抛，和真机一样）。"""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp936", errors="strict", write_through=True)
    return stream, raw


def test_setup_logging_survives_cp936_stderr_with_non_gbk_chars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """取消测试（P0-R15）：cp936 stderr 下写含 `⇒`/emoji 的事件 ⇒ **不得出现 Logging error**。"""
    stream, raw = _strict_cp936_stream()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)

    setup_logging(level="INFO", log_dir=tmp_path)  # type: ignore[arg-type]
    log = get_logger("r15-console")
    log.warning("记录一次事件", detail="⇒ 上一次没有走到正常关闭", emoji="🔥")

    for handler in logging.getLogger().handlers:
        handler.flush()
    text = raw.getvalue().decode("cp936", errors="replace")

    assert "--- Logging error ---" not in text, (
        "日志管道自己报错了 —— 这正是事故里把真实死因盖住的噪声"
    )
    assert "记录一次事件" in text
    assert "上一次没有走到正常关闭" in text
    # cp936 编不了 ⇒ / emoji：应被替换而不是抛异常（原编码不变）
    assert "⇒" not in text
    assert "?" in text


def test_file_log_keeps_full_unicode(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """文件 handler 是 UTF-8 ⇒ 控制台替换不影响文件日志完整性（格式不变）。"""
    stream, _raw = _strict_cp936_stream()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)

    setup_logging(level="INFO", log_dir=tmp_path)  # type: ignore[arg-type]
    get_logger("r15-file").warning("文件通道", detail="⇒ 不应被替换", emoji="🔥")
    for handler in logging.getLogger().handlers:
        handler.flush()

    log_file = tmp_path / "elysia.log"  # type: ignore[operator]
    rows = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    payload = json.dumps(rows, ensure_ascii=False)
    assert "⇒ 不应被替换" in payload, "文件通道必须保留完整 Unicode"
    assert "🔥" in payload


def test_setup_logging_tolerates_none_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """pythonw.exe 下 stdout/stderr 可能是 None（本工程踩过）⇒ setup 不得因此崩溃。"""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    setup_logging(level="INFO")  # 不抛异常即通过
    get_logger("r15-none").warning("没有控制台也要能记日志")
