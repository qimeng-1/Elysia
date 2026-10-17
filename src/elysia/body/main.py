"""身体进程入口：随设备在场的 5s 心跳。

启动流程：ensure_dirs → 日志 → 双库启动 → 心跳循环 → 优雅关闭。

P0-C 扩展：soul.ps1/body.ps1 进程管理 + 开机自启注册（桌宠形态）。

P0-R2（D-1）新增：**崩溃现场**（与 `soul/main.py` 同一套约定，独立实现）。
审计实测 `body.err.log` 长期 0 字节：身体进程死时同样什么都不写。
身体与灵魂按架构铁律**互不 import**（只经文件 + SQLite），故这里不做共享模块，
而是各自持有一份"直连磁盘"的现场写法 —— 这份重复是**有意的边界成本**。
两个进程的约定必须一致：
- `data/logs/crash-<ts>.log`：未捕获异常（完整 traceback + 崩溃阶段 + pid + 心跳序号）
- `data/run/body.running`：启动即创建、正常关闭才删除；下次启动发现它还在 ⇒ "上次非正常退出"
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sqlite3
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from elysia.body.heartbeat import BodyHeartbeat
from elysia.body.resource import ResourceSampler
from elysia.core.clock import SystemClock
from elysia.core.config import Settings, get_settings
from elysia.core.log import get_logger, setup_logging
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.supervisor import supervise

# ── P0-R2（D-1）：崩溃现场（约定与 soul/main.py 一致）──────────────
CRASH_LOG_PREFIX = "crash-"
SESSION_MARKER_NAME = "body.running"

# ── P0-R8（D-8）：优雅关闭（约定与 soul/main.py 一致，标记名加 `body_` 前缀）──
# `soul.ps1 stop-body` 写 `body_stop_requested` → 本进程轮询到后走正常关闭序；
# `body_stopped_intentionally` 供下次启动区分"主动停"与"崩溃"。
STOP_REQUEST_NAME = "body_stop_requested"
STOP_INTENTIONAL_NAME = "body_stopped_intentionally"


class _CrashContext:
    """崩溃现场的"黑匣子"：异常抛出时知道走到哪一步了。"""

    __slots__ = ("phase", "started_ts")

    def __init__(self) -> None:
        self.phase = "bootstrap"
        self.started_ts = time.time()


def _read_last_body_beat(heartbeat_db: Path) -> str:
    """现场里的"心跳序号"：heartbeat.db 最后一条 body 心跳（读不到就说为什么）。"""
    if not heartbeat_db.exists():
        return f"读不到（{heartbeat_db} 不存在）"
    try:
        con = sqlite3.connect(f"file:{heartbeat_db}?mode=ro", uri=True, timeout=3.0)
        con.execute("PRAGMA busy_timeout=3000")
        try:
            row = con.execute(
                "SELECT id, ts FROM heartbeats WHERE beat_type='body' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return f"读不到（{type(exc).__name__}: {exc}）"
    if row is None:
        return "还没有任何 body 心跳记录（启动期崩溃）"
    return f"seq={row[0]} ts={row[1]:.3f}"


def _session_marker(settings: Settings) -> Path:
    return settings.run_dir / SESSION_MARKER_NAME


def _stop_request_marker(settings: Settings) -> Path:
    return settings.run_dir / STOP_REQUEST_NAME


def _intentional_marker(settings: Settings) -> Path:
    return settings.run_dir / STOP_INTENTIONAL_NAME


def _intentional_stop_evidence(settings: Settings) -> str | None:
    """上一次是不是**你主动停的**（P0-R8）。返回证据串；没有标记则 None。"""
    marker = _intentional_marker(settings)
    try:
        raw = marker.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"主动停标记存在但读不出（{type(exc).__name__}: {exc}）"
    return " | ".join(line for line in raw.splitlines() if line.strip()) or "空"


def _clear_stop_markers(settings: Settings) -> None:
    """启动时消费掉上一次的停止标记（避免污染下一次判定，P0-R8）。"""
    for marker in (_stop_request_marker(settings), _intentional_marker(settings)):
        with contextlib.suppress(OSError):
            marker.unlink(missing_ok=True)


def _detect_abnormal_exit(settings: Settings) -> str | None:
    """上次是否非正常退出：标记还在 = 上次没走到正常关闭。只陈述事实，不猜死因。

    P0-R8：存在 `body_stopped_intentionally` ⇒ 上次是人为停止，不算非正常退出。
    """
    if _intentional_stop_evidence(settings) is not None:
        return None
    marker = _session_marker(settings)
    try:
        raw = marker.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"上次会话标记存在但读不出（{type(exc).__name__}: {exc}）"
    detail = " | ".join(line for line in raw.splitlines() if line.strip()) or "空"
    return f"上次的会话标记仍在（{detail}）⇒ 上一次没有走到正常关闭"


def _mark_session_start(settings: Settings) -> None:
    """启动即留痕：纯文件写（在日志/库之前，那两者正是会坏的）。"""
    marker = _session_marker(settings)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"pid={os.getpid()}\nstarted_at={time.time():.3f}\n", encoding="utf-8")


def _mark_session_end(settings: Settings) -> None:
    _session_marker(settings).unlink(missing_ok=True)


def _write_crash_report(settings: Settings, exc: BaseException, ctx: _CrashContext) -> Path | None:
    """把"身体是怎么死的"写进 `data/logs/crash-<ts>.log`（不依赖 stderr）。"""
    now = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    path = settings.log_dir / f"{CRASH_LOG_PREFIX}{stamp}.log"
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
    body = "\n".join(
        [
            "Elysia 身体进程崩溃现场（P0-R2 / D-1）",
            "",
            f"崩溃时刻 ：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}（ts={now:.3f}）",
            f"崩溃阶段 ：{ctx.phase}",
            f"本次启动 ：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ctx.started_ts))}"
            f"（已存活 {now - ctx.started_ts:.1f}s）",
            f"进程     ：pid={os.getpid()}  argv={sys.argv!r}",
            f"运行环境 ：Python {sys.version.split()[0]} / {sys.platform}",
            "",
            f"异常     ：{type(exc).__name__}: {exc}",
            f"心跳序号 ：{_read_last_body_beat(settings.data_dir / 'heartbeat.db')}",
            "",
            "完整 traceback：",
            tb,
            "",
        ]
    )
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as write_exc:
        with contextlib.suppress(Exception):
            sys.stderr.write(f"[crash] 无法写崩溃现场 {path}: {write_exc}\n{body}\n")
            sys.stderr.flush()
        return None
    with contextlib.suppress(Exception):
        get_logger("body").critical(
            "body crashed（现场已落盘）", phase=ctx.phase, crash_log=str(path), exc_info=exc
        )
    return path


async def _watch_stop_request(marker: Path, stop: asyncio.Event, log: Any) -> None:
    """P0-R8：轮询"停止请求"标记（身体 5s 一拍，故用独立轮询任务，响应 ≤0.5s）。

    与灵魂的差别：灵魂心跳 1Hz，直接在拍内检查标记；身体 5s 一拍，
    若也只在拍内查，用户按下「停止」后最多要等 5 秒才开始关闭。轮询任务让两者一致。
    """
    while not stop.is_set():
        if marker.exists():
            log.info("收到停止请求（%s）：进入优雅关闭序", marker.name)
            stop.set()
            return
        await asyncio.sleep(0.5)


async def _run(
    settings: Settings,
    *,
    previous_session: str | None = None,
    intentional_stop: str | None = None,
) -> int:
    ctx = _CrashContext()
    try:
        ctx.phase = "setup_logging"
        setup_logging(level=settings.log_level, log_dir=settings.log_dir)
        log = get_logger("body")
        log.info("body process starting", pid=os.getpid())
        if previous_session is not None:
            log.warning("上次是非正常退出（死因未知）", evidence=previous_session)
        elif intentional_stop is not None:
            # P0-R8：上次是人为停的（soul.ps1 stop-body / stop）⇒ 不是异常
            log.info("上次是你主动停的（不是异常退出）", evidence=intentional_stop)

        ctx.phase = "state_store.start"
        state_store = StateStore(settings.data_dir / "state.db")
        heartbeat_store = HeartbeatStore(settings.data_dir / "heartbeat.db")
        await state_store.start()

        ctx.phase = "heartbeat_store.start"
        await heartbeat_store.start()

        ctx.phase = "build_heartbeat"
        heartbeat = BodyHeartbeat(
            state_store=state_store,
            heartbeat_store=heartbeat_store,
            clock=SystemClock(),
            interval_s=settings.body_heartbeat_interval_s,
        )
        heartbeat.attach_resource_sampler(ResourceSampler())

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows：add_signal_handler 仅 Unix
                break

        # F6 的对偶件：写库失败会向上抛（不再伪装成功），而上抛不得让身体僵死 ⇒ 上监督器
        beat_task = asyncio.create_task(
            supervise(heartbeat.run, "body-heartbeat"), name="body-heartbeat"
        )
        # P0-R8：停止请求轮询任务（与心跳并行；stop 事件一置就退出）
        watch_task = asyncio.create_task(
            _watch_stop_request(_stop_request_marker(settings), stop, log),
            name="body-stop-watch",
        )
        ctx.phase = "heartbeat_loop"
        try:
            await stop.wait()
        except KeyboardInterrupt:
            pass
        finally:
            ctx.phase = "shutdown"
            log.info("body stopping：优雅关闭")
            await heartbeat.stop()
            beat_task.cancel()
            watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat_task
            with contextlib.suppress(asyncio.CancelledError):
                await watch_task
            await state_store.close()
            await heartbeat_store.close()
            # P0-R8：正常关闭 = 消费掉停止请求 + 删掉会话标记
            try:
                _stop_request_marker(settings).unlink(missing_ok=True)
                _mark_session_end(settings)
            except OSError:
                log.exception("无法清除停止请求/会话标记（下次启动会误报上次非正常退出）")
            ctx.phase = "stopped"
            log.info("body stopped")
        return 0
    except Exception as exc:
        # P0-R2：任何未捕获异常都必须留下现场。**先写现场，再返回非 0**。
        crash_log = _write_crash_report(settings, exc, ctx)
        if crash_log is not None:
            with contextlib.suppress(Exception):
                get_logger("body").error(
                    "进程异常退出（现场已落盘）", crash_log=str(crash_log), phase=ctx.phase
                )
        return 1


def main() -> int:
    settings = get_settings()
    settings.ensure_dirs()
    # P0-R2：启动即留痕 + 上次是否正常关闭（在任何库/日志初始化之前，纯文件操作）
    previous_session = _detect_abnormal_exit(settings)
    # P0-R8：上次是不是"你主动停的"——主动停不算异常，但要留一句"我知道为什么"。
    intentional_stop = _intentional_stop_evidence(settings)
    # 启动即**消费**这两个标记，避免污染下一次判定（崩溃时下次仍会如实报异常）
    _clear_stop_markers(settings)
    try:
        _mark_session_start(settings)
    except OSError as exc:
        with contextlib.suppress(Exception):
            sys.stderr.write(f"[warn] 无法写启动标记 {_session_marker(settings)}: {exc}\n")
    try:
        return asyncio.run(
            _run(
                settings,
                previous_session=previous_session,
                intentional_stop=intentional_stop,
            )
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        ctx = _CrashContext()
        ctx.phase = "asyncio.run"
        _write_crash_report(settings, exc, ctx)
        return 1


if __name__ == "__main__":
    sys.exit(main())
