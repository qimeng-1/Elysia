"""崩溃现场单测（P0-R2 / D-1）：她死时必须留下可复盘的现场。

审计实测：三次死亡（00:47 / 00:52 / 03:51）的 `soul.err.log`、`body.err.log`
**全 0 字节**、系统事件无记录 ⇒ "死得不明不白"。本文件的用例钉住三件事：

1. **启动期异常必须落盘**：crash log 里要有 traceback、崩溃阶段、心跳序号；
   —— 还原旧写法（`_run` 不兜底）时 `main()` 会**直接把异常抛出来**，用例红。
2. **连日志系统都没起来时也要落盘**（`setup_logging` 自己抛）。
3. **跨重启的"上次是否正常关闭"检测**：标记在 = 上次非正常退出，必须显式记一笔。
"""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path

import pytest

import elysia.body.main as body_main
import elysia.soul.main as soul_main
from elysia.core.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        run_dir=tmp_path / "run",
    )


@pytest.fixture(autouse=True)
def _restore_logging() -> object:
    """`setup_logging` 会替换 root handlers：用例结束后还原并关掉新增的文件句柄。"""
    root = logging.getLogger()
    saved = list(root.handlers)
    saved_level = root.level
    yield
    for handler in list(root.handlers):
        if handler not in saved:
            with contextlib.suppress(Exception):
                handler.close()
    root.handlers = saved
    root.setLevel(saved_level)


def _crash_logs(settings: Settings) -> list[Path]:
    return sorted(settings.log_dir.glob("crash-*.log"))


def _boom(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("注入的启动期异常")


# ── 灵魂 ──────────────────────────────────────────────────────────


def test_soul_startup_crash_writes_crash_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消测试（P0-R2）：`_build_timesense` 抛 ⇒ crash log 必须能读到该异常。

    还原旧写法（`_run` 无兜底）：`main()` 直接把 RuntimeError 抛给调用方、
    **一个字节都不写** ⇒ 本用例红。
    """
    settings = _settings(tmp_path)
    monkeypatch.setattr(soul_main, "get_settings", lambda: settings)
    monkeypatch.setattr(soul_main, "_build_timesense", _boom)

    assert soul_main.main() == 1, "崩溃后必须返回非 0（不是假装正常退出）"

    logs = _crash_logs(settings)
    assert len(logs) == 1, "必须在 data/logs/ 留下且只留下一份 crash log"
    text = logs[0].read_text(encoding="utf-8")
    assert "RuntimeError: 注入的启动期异常" in text
    assert "完整 traceback" in text
    assert "Traceback (most recent call last)" in text
    assert "崩溃阶段 ：build_timesense" in text, "现场必须能指出崩在哪一步"
    assert "pid=" in text
    assert "心跳序号" in text
    assert "TimeSense" in text
    # 崩溃没有走到正常关闭 ⇒ 会话标记必须仍在（下次启动据此报警）
    assert (settings.run_dir / soul_main.SESSION_MARKER_NAME).exists()


def test_soul_crash_before_logging_setup_still_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`setup_logging` 自己抛也必须留现场——这正是旧写法写不出东西的典型。

    旧代码在 `setup_logging` 之后才有 log；它一抛，`soul.err.log` 靠 stderr 重定向
    可能已失效（D-1 的最省解释）⇒ 现场为零。现在写现场不经日志系统。
    """
    settings = _settings(tmp_path)
    monkeypatch.setattr(soul_main, "get_settings", lambda: settings)
    monkeypatch.setattr(soul_main, "setup_logging", _boom)

    assert soul_main.main() == 1
    logs = _crash_logs(settings)
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "崩溃阶段 ：setup_logging" in text
    assert "RuntimeError: 注入的启动期异常" in text


def test_soul_abnormal_exit_is_reported_on_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上次崩溃 ⇒ 下次启动必须显式记"上次是非正常退出（死因未知）"。"""
    settings = _settings(tmp_path)
    monkeypatch.setattr(soul_main, "get_settings", lambda: settings)
    monkeypatch.setattr(soul_main, "_build_timesense", _boom)

    assert soul_main.main() == 1  # 第一次：崩
    evidence = soul_main._detect_abnormal_exit(settings)
    assert evidence is not None
    assert "没有走到正常关闭" in evidence

    assert soul_main.main() == 1  # 第二次：先读旧标记，再（又一次）崩
    log_text = (settings.log_dir / "elysia.log").read_text(encoding="utf-8")
    assert "上次是非正常退出" in log_text, "上次非正常退出必须写进她的日志（可复盘）"
    assert "上次的会话标记仍在" in log_text, "日志要带上判定依据，而不是只给结论"


def test_soul_session_marker_roundtrip(tmp_path: Path) -> None:
    """标记语义：启动写 / 正常关闭删 / 检测只看它。"""
    settings = _settings(tmp_path)
    assert soul_main._detect_abnormal_exit(settings) is None

    soul_main._mark_session_start(settings)
    marker = settings.run_dir / soul_main.SESSION_MARKER_NAME
    assert marker.exists()
    assert f"pid={__import__('os').getpid()}" in marker.read_text(encoding="utf-8")
    assert soul_main._detect_abnormal_exit(settings) is not None

    soul_main._mark_session_end(settings)  # 正常关闭
    assert soul_main._detect_abnormal_exit(settings) is None


def test_soul_crash_log_survives_unreadable_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """库坏到读不出时，现场仍要写得出来（只说"读不到"和原因，不得让写现场二次崩）。"""
    settings = _settings(tmp_path)
    monkeypatch.setattr(soul_main, "get_settings", lambda: settings)
    monkeypatch.setattr(soul_main, "_build_timesense", _boom)
    # 把两个库都塞成"不是 SQLite 文件"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "state.db").write_bytes(b"not-a-sqlite-file")
    (settings.data_dir / "heartbeat.db").write_bytes(b"not-a-sqlite-file")

    assert soul_main.main() == 1
    logs = _crash_logs(settings)
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "读不到" in text  # 心跳序号/TimeSense 都读不到，但现场本身必须在


# ── 身体（同一套约定，独立实现）────────────────────────────────────


def test_body_startup_crash_writes_crash_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """身体进程同样必须留现场（`body.err.log` 也曾长期 0 字节）。"""
    settings = _settings(tmp_path)
    monkeypatch.setattr(body_main, "get_settings", lambda: settings)
    monkeypatch.setattr(body_main, "ResourceSampler", _boom)

    assert body_main.main() == 1
    logs = _crash_logs(settings)
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "Elysia 身体进程崩溃现场" in text
    assert "崩溃阶段 ：build_heartbeat" in text
    assert "RuntimeError: 注入的启动期异常" in text
    assert (settings.run_dir / body_main.SESSION_MARKER_NAME).exists()


def test_body_session_marker_roundtrip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert body_main._detect_abnormal_exit(settings) is None
    body_main._mark_session_start(settings)
    assert body_main._detect_abnormal_exit(settings) is not None
    body_main._mark_session_end(settings)
    assert body_main._detect_abnormal_exit(settings) is None


def test_crash_log_is_json_readable_by_eye(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """现场是给人看的：要有可读的键值行（不是一坨 JSON 或二进制）。"""
    settings = _settings(tmp_path)
    monkeypatch.setattr(soul_main, "get_settings", lambda: settings)
    monkeypatch.setattr(soul_main, "_build_timesense", _boom)
    soul_main.main()
    text = _crash_logs(settings)[0].read_text(encoding="utf-8")
    for key in ("崩溃时刻", "崩溃阶段", "进程", "异常", "心跳序号", "TimeSense", "完整 traceback"):
        assert key in text
    # 日志（elysia.log）仍是 JSON 行，可被机读——现场与常规日志互不干扰
    assert json.loads((settings.log_dir / "elysia.log").read_text(encoding="utf-8").splitlines()[0])
