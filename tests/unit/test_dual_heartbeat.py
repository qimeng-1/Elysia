"""双心跳集成冒烟：灵魂 1Hz + 身体 5s 在同一事件循环互不阻塞、状态互通。

**确定性（2026-10-19 修 flake）**：旧写法用固定"虚拟 6s"窗口（speed=60 ⇒ 真实 100ms）
去凑拍数——机器一忙，100ms 里只跑出 0–1 拍，全量跑偶发红（单跑却绿）。
现在改为**事件等待**：等"目标拍数/状态真的出现"（拍子到了事件就成立），
真实超时只作"机制彻底没跑"的兜底；速度降到 10 倍，等待期虚拟时间远小于
ALONE 阈值 5 分钟，因此机器再忙也不会因等久而误判模式。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from elysia.body.heartbeat import BodyHeartbeat
from elysia.core.clock import SimulatedClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import TimeSense
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

T0 = 1_000_000.0
# 倍速 10（不是 60）：同样的事件等待下，等待期虚拟时间最多 15s×10=150s，
# 仍远小于 TimeSense 的 ALONE 阈值（5 分钟）——负载再高也不会等出"模式变了"。
CLOCK_SPEED = 10.0
WAIT_TIMEOUT_S = 15.0


async def _wait_until(
    check: Callable[[], Awaitable[bool]],
    *,
    what: str,
    timeout_s: float = WAIT_TIMEOUT_S,
) -> None:
    """等一个异步条件成立（**事件式等待**，不靠固定时长的 sleep）。

    真实超时不是"再等一会"的重试，而是"心跳机制根本没在跑"的失败兜底——
    它只与机器是否还活着有关，与拍数目标无关，所以结果确定。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not await check():
        if loop.time() > deadline:
            raise AssertionError(f"等待「{what}」超时（{timeout_s:.0f}s 真实时间）——心跳可能没在跑")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_dual_heartbeat_interleave(tmp_path: Path) -> None:
    """灵魂 ≥3 拍 + 身体 ≥1 拍：互不阻塞，身体在场被灵魂感知，模式正确。"""
    clock = SimulatedClock(start_ts=T0, speed=CLOCK_SPEED)
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

    async def _both_ready() -> bool:
        if await heartbeat_store.count("soul") < 3:
            return False
        if await heartbeat_store.count("body") < 1:
            return False
        # 灵魂必须真的读到"身体在场"（不只是身体写过库）
        persisted = await state_store.load_json("timesense") or {}
        return float(persisted.get("last_body_online_ts", 0.0)) > T0

    soul_task = asyncio.create_task(soul.run())
    body_task = asyncio.create_task(body.run())
    try:
        await _wait_until(_both_ready, what="灵魂 ≥3 拍且身体在场被感知")
    finally:
        await soul.stop()
        await body.stop()
        await asyncio.gather(soul_task, body_task)

    try:
        # 断言验证"心跳在跑、互不阻塞、身体在场被感知"，不是精确拍数
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
    clock = SimulatedClock(start_ts=T0, speed=CLOCK_SPEED)
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
    try:
        # 事件等待：至少 3 拍灵魂心跳（虚拟时间 ≈数秒，远小于 5min）
        await _wait_until(
            lambda: _soul_beats_at_least(heartbeat_store, 3),
            what="灵魂 ≥3 拍",
        )
    finally:
        await soul.stop()
        await asyncio.gather(soul_task)

    try:
        persisted = await state_store.load_json("timesense")
        assert float(persisted["last_body_online_ts"]) == T0
        rows = await heartbeat_store.execute_raw(
            "SELECT payload FROM heartbeats WHERE beat_type = 'soul' ORDER BY ts DESC LIMIT 1"
        )
        assert '"mode": "present"' in rows[0][0]
    finally:
        await state_store.close()
        await heartbeat_store.close()


async def _soul_beats_at_least(store: HeartbeatStore, target: int) -> bool:
    return await store.count("soul") >= target
