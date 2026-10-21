"""T-017（D-9）：重启不再重放最后一条 interaction。

真机：20:01 的话，在 20:50 与 21:30 两次重启后各被当成新消息处理一次（她多写了两页文件）。
根因：`_last_interaction_event_ts` 纯内存初始化 0.0，重启归零 ⇒ `kv.ts > 0` 恒成立。

修法：启动时用**持久化**的 `State.last_interaction_ts` 当基线。
验收：旧消息（ts == 已答 ts）不触发；停机期间新到的（ts > 已答 ts）仍触发。

取消测试：把 seed 改回 0.0 ⇒ `test_old_interaction_not_replayed_after_restart` 红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elysia.core.clock import SimulatedClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import TimeSense
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

T0 = 1_000_000.0


async def _soul(tmp_path: Path) -> tuple[SoulHeartbeat, StateStore, HeartbeatStore]:
    state = StateStore(tmp_path / "state.db")
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await state.start()
    await store.start()
    clock = SimulatedClock(start_ts=T0, speed=1.0)
    soul = SoulHeartbeat(
        state_store=state,
        heartbeat_store=store,
        timesense=TimeSense(make_soul_state(T0), clock),
        mode_mgr=ModeManager(clock=clock),
        clock=clock,
    )
    return soul, state, store


@pytest.mark.asyncio
async def test_old_interaction_not_replayed_after_restart(tmp_path: Path) -> None:
    soul, state, store = await _soul(tmp_path)
    try:
        answered_ts = T0 + 100.0
        # 耐久化：上次已答过的那条消息（TimeSense.state 持久化值）
        soul._timesense.state.last_interaction_ts = answered_ts
        await state.save_json("interaction", {"ts": answered_ts, "text": "20:01 的旧话"})
        # 重启后的基线 seed
        soul._seed_interaction_baseline()

        await soul._sync_body_status(answered_ts + 1.0)
        assert soul._new_interaction is False, "重启后旧消息被当成新消息重放了"
        assert await store.dialogue_turns() == [], "旧消息不得再产生一条用户轮次"
    finally:
        await state.close()
        await store.close()


@pytest.mark.asyncio
async def test_new_interaction_arrived_while_offline_still_triggers(tmp_path: Path) -> None:
    soul, state, store = await _soul(tmp_path)
    try:
        answered_ts = T0 + 100.0
        soul._timesense.state.last_interaction_ts = answered_ts
        # 她停机期间来的真·新消息：ts 比已答的那条新
        await state.save_json("interaction", {"ts": answered_ts + 10.0, "text": "停机时来的新话"})
        soul._seed_interaction_baseline()

        await soul._sync_body_status(answered_ts + 11.0)
        assert soul._new_interaction is True, "停机期间的新消息必须仍触发"
        turns = await store.dialogue_turns()
        assert [(t["role"], t["text"]) for t in turns] == [("user", "停机时来的新话")]
    finally:
        await state.close()
        await store.close()
