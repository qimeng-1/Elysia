"""优雅关闭（P0-R8 / D-8）单测：**停得要体面，且"主动停"不得误报成崩溃**。

审计实测（`TASK_BOARD` P0-R8 卡）：旧停止路径是 `Stop-Process -Force`
⇒ ①`main._run()` 的 finally（排空表达 → 关库 → 写 `soul stopped`）**从未执行**；
②`soul.running` 每次都残留 ⇒ R2 的异常退出检测**每次启动都误报**。

本文件钉住三件事：
1. `stop_requested` 标记 → 心跳循环**自己退出**并置 stop 事件（取消测试：还原成
   "只在 Ctrl+C/SIGTERM 停" ⇒ `_run` 永不返回，用例红）；
2. 端到端（进程内）：标记出现 ⇒ `_run` 走完正常关闭序，日志出现
   `soul stopping` / `soul stopped`，停止请求被消费；
3. `stopped_intentionally` 存在 ⇒ 下次启动报"上次是你主动停的"，**不报非正常退出**
   （取消测试：不写该标记 ⇒ 必报非正常退出，用例红）。body 同套约定。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import pytest

import elysia.body.main as body_main
import elysia.soul.main as soul_main
from elysia.core.clock import SimulatedClock
from elysia.core.config import Settings
from elysia.core.log import get_logger
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import TimeSense
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

T0 = 1_000_000.0


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        run_dir=tmp_path / "run",
        # ⚠️ 这个文件会真跑一次 `_run`（进程内集成测试）：**必须**关掉主声与外部服务，
        # 绝不允许测试打出付费 LLM 请求（`.env` 里有 key，不显式覆盖就会读到）。
        llm_main_api_key="",
        llm_fallback_base="",
        llm_fallback_api_key="",
        tts_gpt_sovits_url="http://127.0.0.1:1",
    )


def _boom(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("注入的启动期异常")


@pytest.fixture(autouse=True)
def _restore_logging() -> object:
    """`_run`/`main` 会调 `setup_logging`（替换 root handlers）：结束后还原并关句柄。"""
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


# ── 1) 标记 → 心跳循环自己退出 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_marker_exits_heartbeat_loop(tmp_path: Path) -> None:
    """取消测试（P0-R8）：写 `stop_requested` ⇒ 心跳循环退出并置 stop 事件。

    还原旧写法（心跳不看标记、只等 Ctrl+C/SIGTERM）时，本用例在 `wait_for` 处超时变红。
    """
    clock = SimulatedClock(start_ts=T0, speed=60.0)
    state_store = StateStore(tmp_path / "state.db")
    heartbeat_store = HeartbeatStore(tmp_path / "heartbeat.db")
    await state_store.start()
    await heartbeat_store.start()
    stop_event = asyncio.Event()
    stop_marker = tmp_path / soul_main.STOP_REQUEST_NAME
    soul = SoulHeartbeat(
        state_store=state_store,
        heartbeat_store=heartbeat_store,
        timesense=TimeSense(make_soul_state(T0), clock),
        mode_mgr=ModeManager(clock=clock),
        clock=clock,
        interval_s=1.0,
        stop_marker=stop_marker,
        stop_event=stop_event,
    )
    task = asyncio.create_task(soul.run())
    try:
        await asyncio.sleep(0.05)  # 让它进入循环
        assert not stop_event.is_set()
        stop_marker.write_text("requested_at=test\n", encoding="utf-8")
        await asyncio.wait_for(task, timeout=5.0)  # 旧写法：永不返回 ⇒ 超时红
    finally:
        await soul.stop()
        await state_store.close()
        await heartbeat_store.close()

    assert stop_event.is_set(), "看到停止请求必须置 stop 事件（main 靠它走正常关闭序）"


# ── 2) 端到端（进程内）：_run 走完正常关闭序 ───────────────────────


@pytest.mark.asyncio
async def test_run_completes_graceful_shutdown_on_stop_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """停止请求 ⇒ 日志出现 `soul stopping`/`soul stopped`，且未落库路径被正常关闭。

    取消测试：心跳不看标记时 `_run` 永不返回 ⇒ `wait_for` 超时红（旧的强杀路径下，
    这两行日志在历史上**从未打印过**）。
    """
    settings = _settings(tmp_path)
    settings.ensure_dirs()  # 停止标记要写进 run_dir（真实 main() 也会先 ensure_dirs）
    monkeypatch.setattr(soul_main, "get_settings", lambda: settings)
    stop_marker = settings.run_dir / soul_main.STOP_REQUEST_NAME

    task = asyncio.create_task(soul_main._run(settings))
    try:
        await asyncio.sleep(0.3)  # 等它完成启动（库/身份种子/时间感）
        stop_marker.write_text("requested_at=test\n", encoding="utf-8")
        rc = await asyncio.wait_for(task, timeout=20.0)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    assert rc == 0
    log_text = (settings.log_dir / "elysia.log").read_text(encoding="utf-8")
    assert "收到停止请求" in log_text
    assert "soul stopping" in log_text, "正常关闭序必须被调用（旧强杀路径下它从未出现）"
    assert "soul stopped" in log_text, "档案封存这一步必须执行"
    # 停止请求被本进程消费（否则下次启动会被残留标记立刻关掉）
    assert not stop_marker.exists()
    # 她确实活过（有心跳），并且优雅关闭把库关干净了
    assert (settings.data_dir / "heartbeat.db").exists()


# ── 3) "主动停" vs "崩溃"：不得把人为停止当成故障 ──────────────────


def test_intentional_stop_is_not_reported_as_abnormal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消测试（P0-R8）：有 `stopped_intentionally` ⇒ 不报"非正常退出"。

    还原成"不写该标记"时，本用例红：同样的 `soul.running` 残留会被判成死因未知。
    """
    settings = _settings(tmp_path)
    settings.run_dir.mkdir(parents=True, exist_ok=True)
    (settings.run_dir / soul_main.SESSION_MARKER_NAME).write_text("pid=1\n", encoding="utf-8")
    (settings.run_dir / soul_main.STOP_INTENTIONAL_NAME).write_text(
        "by=soul.ps1 stop\n", encoding="utf-8"
    )

    assert soul_main._detect_abnormal_exit(settings) is None, (
        "只要「主动停」标记在，残留的 soul.running 就不是故障"
    )

    # 端到端：启动日志必须说"上次是你主动停的"，而不是"死因未知"
    monkeypatch.setattr(soul_main, "get_settings", lambda: settings)
    monkeypatch.setattr(soul_main, "_build_timesense", _boom)
    assert soul_main.main() == 1  # 我们故意注入启动期崩溃，让 main 快速返回

    log_text = (settings.log_dir / "elysia.log").read_text(encoding="utf-8")
    assert "上次是你主动停的" in log_text
    assert "上次是非正常退出" not in log_text, "主动停被误报成崩溃 = R2 的护栏被噪声淹没"
    # 启动即消费：标记不能污染下一次判定
    assert not (settings.run_dir / soul_main.STOP_INTENTIONAL_NAME).exists()
    assert not (settings.run_dir / soul_main.STOP_REQUEST_NAME).exists()


def test_abnormal_stop_still_reported_without_intentional_marker(tmp_path: Path) -> None:
    """对照：**没有**主动停标记的残留（真崩溃/被强杀）仍必须报"非正常退出"。"""
    settings = _settings(tmp_path)
    settings.run_dir.mkdir(parents=True, exist_ok=True)
    (settings.run_dir / soul_main.SESSION_MARKER_NAME).write_text("pid=1\n", encoding="utf-8")

    evidence = soul_main._detect_abnormal_exit(settings)
    assert evidence is not None
    assert "没有走到正常关闭" in evidence


def test_body_intentional_stop_is_not_reported_as_abnormal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """body 同套约定：`body_stopped_intentionally` ⇒ 不报非正常退出（stop-body 也是优雅的）。"""
    settings = _settings(tmp_path)
    settings.run_dir.mkdir(parents=True, exist_ok=True)
    (settings.run_dir / body_main.SESSION_MARKER_NAME).write_text("pid=2\n", encoding="utf-8")
    (settings.run_dir / body_main.STOP_INTENTIONAL_NAME).write_text(
        "by=soul.ps1 stop-body\n", encoding="utf-8"
    )

    assert body_main._detect_abnormal_exit(settings) is None

    monkeypatch.setattr(body_main, "get_settings", lambda: settings)
    monkeypatch.setattr(body_main, "ResourceSampler", _boom)
    assert body_main.main() == 1

    log_text = (settings.log_dir / "elysia.log").read_text(encoding="utf-8")
    assert "上次是你主动停的" in log_text
    assert "上次是非正常退出" not in log_text
    assert not (settings.run_dir / body_main.STOP_INTENTIONAL_NAME).exists()


@pytest.mark.asyncio
async def test_body_stop_request_watcher_sets_event(tmp_path: Path) -> None:
    """身体 5s 一拍，用独立轮询任务响应停止请求（≤0.5s），否则停她要等 5 秒。"""
    stop = asyncio.Event()
    marker = tmp_path / body_main.STOP_REQUEST_NAME
    task = asyncio.create_task(body_main._watch_stop_request(marker, stop, get_logger("test")))
    await asyncio.sleep(0.05)
    assert not stop.is_set()

    marker.write_text("requested_at=test\n", encoding="utf-8")
    await asyncio.wait_for(task, timeout=3.0)
    assert stop.is_set()
