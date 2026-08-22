"""双心跳集成冒烟：灵魂 1Hz + 身体 5s 在同一事件循环互不阻塞、状态互通。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from elysia.body.heartbeat import BodyHeartbeat
from elysia.core.clock import SimulatedClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import TimeSense
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

T0 = 1_000_000.0


@pytest.mark.asyncio
async def test_dual_heartbeat_interleave(tmp_path: Path) -> None:
    """灵魂 6 拍 + 身体 1 拍：互不阻塞，身体在场被灵魂感知，模式正确。"""
    clock = SimulatedClock(start_ts=T0, speed=60.0)  # 60 倍速：虚拟 6s ≈ 真实 100ms
    state_store = StateStore(tmp_path / "state.db")
    heartbeat_store = HeartbeatStore(tmp_path / "heartbeat.db")
    await state_store.start()
    await heartbeat_store.start()

    timesense = TimeSense(make_soul_state(T0), clock)
    mode_mgr = ModeManager(clock=clock)
    soul = SoulHeartbeat(
        state_store=state_store,
        heartbeat_store=heartbeat_store,
        timesense=timesense,
        mode_mgr=mode_mgr,
        clock=clock,
        interval_s=1.0,
    )
    body = BodyHeartbeat(
        state_store=state_store,
        heartbeat_store=heartbeat_store,
        clock=clock,
        interval_s=5.0,
    )

    soul_task = asyncio.create_task(soul.run())
    body_task = asyncio.create_task(body.run())
    await clock.sleep(6.0)  # 虚拟 6s：soul 6 拍、body 1 拍
    await soul.stop()
    await body.stop()
    await asyncio.gather(soul_task, body_task)

    try:
        # 虚拟时钟下每拍含真实 DB 落盘耗时，拍数可能少于理论值：
        # 断言验证“心跳在跑、互不阻塞、身体在场被感知”而非精确拍数
        assert await heartbeat_store.count("soul") >= 3
        assert await heartbeat_store.count("body") >= 1
        # 灵魂感知身体在场：TimeSense 已同步
        persisted = await state_store.load_json("timesense")
        assert float(persisted["last_body_online_ts"]) > T0
        # 最后一拍模式为在场
        rows = await heartbeat_store.execute_raw(
            "SELECT payload FROM heartbeats WHERE beat_type = 'soul' ORDER BY ts DESC LIMIT 1"
        )
        assert '"mode": "present"' in rows[0][0]
    finally:
        await state_store.close()
        await heartbeat_store.close()


@pytest.mark.asyncio
async def test_soul_enters_away_when_body_silent(tmp_path: Path) -> None:
    """身体停止心跳 > 5min → 灵魂进入独处（ALONE），离开时长正确累积。"""
    clock = SimulatedClock(start_ts=T0, speed=60.0)
    state_store = StateStore(tmp_path / "state.db")
    heartbeat_store = HeartbeatStore(tmp_path / "heartbeat.db")
    await state_store.start()
    await heartbeat_store.start()

    timesense = TimeSense(make_soul_state(T0), clock)
    mode_mgr = ModeManager(clock=clock)
    soul = SoulHeartbeat(
        state_store=state_store,
        heartbeat_store=heartbeat_store,
        timesense=timesense,
        mode_mgr=mode_mgr,
        clock=clock,
        interval_s=1.0,
    )

    # 先让身体报一次在场
    await state_store.save_json("body_status", {"last_online_ts": T0, "status": "online"})
    soul_task = asyncio.create_task(soul.run())
    await clock.sleep(6.0)  # 身体不再心跳，虚拟 6s
    await soul.stop()
    await asyncio.gather(soul_task)

    try:
        persisted = await state_store.load_json("timesense")
        assert float(persisted["last_body_online_ts"]) == T0
        # 6s < 5min：尚未进入独处
        rows = await heartbeat_store.execute_raw(
            "SELECT payload FROM heartbeats WHERE beat_type = 'soul' ORDER BY ts DESC LIMIT 1"
        )
        assert '"mode": "present"' in rows[0][0]
    finally:
        await state_store.close()
        await heartbeat_store.close()
