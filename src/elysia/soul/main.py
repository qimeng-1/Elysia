"""灵魂进程入口：常驻心跳（1Hz）+ 大脑循环（P1）+ 优雅关闭。

启动流程：ensure_dirs → 日志双通道 → 双库启动 → TimeSense 恢复/新生
→ 欲望系统恢复/新生 → 大脑循环初始化 → 心跳循环 → 阻塞等待停止信号
→ 优雅关闭（排空写队列）。

P1 新增：欲望系统 DesireSystem + 大脑循环 BrainLoop 的恢复与初始化。

停止路径（P0-A）：
- Ctrl+C（KeyboardInterrupt）
- Unix SIGTERM（add_signal_handler）
- Windows 强杀（soul.ps1 stop）：数据损失 <= 1s，WAL 自动恢复
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import Any

from elysia.core.checkpoint import CheckpointManager
from elysia.core.clock import SystemClock
from elysia.core.config import Settings, get_settings
from elysia.core.log import get_logger, setup_logging
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import TimeSense, from_payload
from elysia.soul.away_life import AwayLife
from elysia.soul.brain import BrainLoop
from elysia.soul.desire import DesireSystem
from elysia.soul.distress import DistressMonitor
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state, stop_with_cancel


async def _build_timesense(state_store: StateStore, log: Any) -> TimeSense:
    """TimeSense 恢复/新生：已有档案恢复；无档案或数据缺失时新生（无 0 时刻）。"""
    payload = await state_store.load_json("timesense")
    now = SystemClock().now()
    if payload is None:
        log.info("first existence：她诞生了", first_existence_ts=now)
        return TimeSense(make_soul_state(now))

    state = from_payload(payload)
    if state.first_existence_ts <= 0:
        log.warning("first_existence 缺失，重设出生时刻", now=now)
        state.first_existence_ts = now
    if state.last_interaction_ts <= 0:
        state.last_interaction_ts = now
    if state.last_soul_beat_ts <= 0:
        state.last_soul_beat_ts = now
    log.info(
        "soul resumed",
        age_days=round((now - state.first_existence_ts) / 86400.0, 3),
        away=state.body_away_start_ts,
    )
    return TimeSense(state)


async def _build_desire(state_store: StateStore, log: Any) -> DesireSystem:
    """欲望系统恢复/新生：已有档案恢复；无档案时新生（默认初始值）。"""
    payload = await state_store.load_json("desire")
    if payload is None:
        log.info("desire system initialized：默认初始值 TR=45 CS=60 SA=20")
        return DesireSystem()
    try:
        return DesireSystem.from_payload(payload)
    except Exception:
        log.warning("desire 恢复失败，新生", exc_info=True)
        return DesireSystem()


async def _run(settings: Settings) -> int:
    setup_logging(level=settings.log_level, log_dir=settings.log_dir)
    log = get_logger("soul")
    log.info("soul process starting", pid=os.getpid())

    state_store = StateStore(settings.data_dir / "state.db")
    heartbeat_store = HeartbeatStore(settings.data_dir / "heartbeat.db")
    await state_store.start()
    await heartbeat_store.start()

    timesense = await _build_timesense(state_store, log)
    desire = await _build_desire(state_store, log)
    mode_mgr = ModeManager()

    # P1：大脑循环
    brain_loop = BrainLoop(desire_system=desire)

    heartbeat = SoulHeartbeat(
        state_store=state_store,
        heartbeat_store=heartbeat_store,
        timesense=timesense,
        mode_mgr=mode_mgr,
        brain_loop=brain_loop,
        clock=SystemClock(),
        interval_s=settings.heartbeat_interval_s,
        distress_monitor=DistressMonitor(),
        away_life=AwayLife(heartbeat_store),
        checkpoint=CheckpointManager(settings.data_dir),
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows：add_signal_handler 仅 Unix
            break

    beat_task = asyncio.create_task(heartbeat.run(), name="soul-heartbeat")
    try:
        await stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("soul stopping：优雅关闭（排空写队列）")
        heartbeat.set_distress(False)
        await heartbeat.stop()
        await stop_with_cancel(beat_task)
        await state_store.close()
        await heartbeat_store.close()
        log.info("soul stopped：档案已封存")
    return 0


def main() -> int:
    settings = get_settings()
    settings.ensure_dirs()
    try:
        return asyncio.run(_run(settings))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
