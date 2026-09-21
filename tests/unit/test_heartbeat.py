"""灵魂心跳单测：大脑循环集成、欲望持久化、distress 降频。

与 test_dual_heartbeat.py（进程互不阻塞冒烟）互补：本文件聚焦 P1 特有的
数据流 —— 每拍产出含 desire/feelings/will/brain_action 的快照，并将
欲望状态落盘；distress 切换引发心跳间隔变化。
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import pytest

from elysia.core.clock import SimulatedClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import TimeSense
from elysia.memory.levels import KIND_INTERACTION
from elysia.soul.brain import BrainLoop
from elysia.soul.desire import DesireSystem
from elysia.soul.distress import DISTRESS_INTERVAL_S
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

T0 = 1_000_000.0


def _make_soul(
    tmp_path: Path, *, with_brain: bool
) -> tuple[SoulHeartbeat, StateStore, HeartbeatStore, SimulatedClock]:
    """构造最小 SoulHeartbeat 环境及配套仓库。"""
    clock = SimulatedClock(start_ts=T0, speed=60.0)
    state_store = StateStore(tmp_path / "state.db")
    heartbeat_store = HeartbeatStore(tmp_path / "heartbeat.db")
    return (
        SoulHeartbeat(
            state_store=state_store,
            heartbeat_store=heartbeat_store,
            timesense=TimeSense(make_soul_state(T0), clock),
            mode_mgr=ModeManager(clock=clock),
            brain_loop=BrainLoop(DesireSystem(), rng=random.Random(0)) if with_brain else None,
            clock=clock,
            interval_s=1.0,
        ),
        state_store,
        heartbeat_store,
        clock,
    )


async def _run_soul(soul: SoulHeartbeat, clock: SimulatedClock, seconds: float) -> None:
    """运行心跳若干虚拟秒并安全停止。"""
    task = asyncio.create_task(soul.run())
    await clock.sleep(seconds)
    await soul.stop()
    await task


@pytest.mark.asyncio
async def test_heartbeat_brain_output_in_payload(tmp_path: Path) -> None:
    """每拍快照应含 P1 字段：desire/feelings/will/brain_action。"""
    soul, state_store, heartbeat_store, clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        await _run_soul(soul, clock, 3.0)
        rows = await heartbeat_store.execute_raw(
            "SELECT payload FROM heartbeats WHERE beat_type = 'soul' ORDER BY id DESC LIMIT 1"
        )
        payload = json.loads(rows[0][0])
        assert "desire" in payload
        assert "feelings" in payload
        assert "will" in payload
        assert payload["brain_action"] in ("none", "think_active", "think_quiet", "animate")
        for k in ("tr", "cs", "sa"):
            assert k in payload["desire"]
        for dim in ("chat", "miss", "explore", "curiosity", "rest", "self_check"):
            assert dim in payload["feelings"]
        for k in ("direction", "strength", "thought_style", "anim_bias"):
            assert k in payload["will"]
    finally:
        await state_store.close()
        await heartbeat_store.close()


@pytest.mark.asyncio
async def test_heartbeat_desire_persisted(tmp_path: Path) -> None:
    """大脑循环后欲望状态应持久化到 state.db。"""
    soul, state_store, heartbeat_store, clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        await _run_soul(soul, clock, 2.0)
        persisted = await state_store.load_json("desire", default=None)
        assert persisted is not None
        state_data = persisted.get("state", {})
        assert {"tr", "cs", "sa"} <= set(state_data)
    finally:
        await state_store.close()
        await heartbeat_store.close()


@pytest.mark.asyncio
async def test_heartbeat_distress_interval(tmp_path: Path) -> None:
    """distress 开启 → 心跳间隔降为 DISTRESS_INTERVAL_S；解除 → 恢复 1s。"""
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=False)
    await state_store.start()
    await heartbeat_store.start()
    try:
        assert soul.interval_s == 1.0
        soul.set_distress(True)
        assert soul.interval_s == DISTRESS_INTERVAL_S
        soul.set_distress(False)
        assert soul.interval_s == 1.0
    finally:
        await state_store.close()
        await heartbeat_store.close()


def test_heartbeat_interval_floor() -> None:
    """set_interval 有 0.1s 下限，防零除/忙转。"""
    soul_hb = SoulHeartbeat(
        state_store=_FakeStore(),
        heartbeat_store=_FakeHB(),
        timesense=TimeSense(make_soul_state(T0), SimulatedClock(start_ts=T0)),
        mode_mgr=ModeManager(),
        clock=SimulatedClock(start_ts=T0),
    )
    soul_hb.set_interval(0.01)
    assert soul_hb.interval_s == 0.1
    soul_hb.set_interval(0.2)
    assert soul_hb.interval_s == 0.2


class _FakeStore:
    async def load_json(self, key: str, default=None):
        return default


class _FakeHB:
    async def append(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_maintain_memories_promotes_and_decays_index(tmp_path: Path) -> None:
    """记忆维护：高重要性记忆晋升 working，旧索引 strength 衰减（遗忘接线）。"""
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=False)
    await state_store.start()
    await heartbeat_store.start()
    try:
        # 高重要性 interaction（≥0.4 应浅层→工作）+ 低重要性（不晋升）
        high = await heartbeat_store.add_memory(
            1.0,
            {
                "level": "shallow",
                "kind": KIND_INTERACTION,
                "content": "重要约定",
                "emotion_vector": {"chat": 0.8},
                "importance": 0.8,
                "protected": False,
                "narrative": "重要约定",
            },
        )
        await heartbeat_store.add_memory(
            1.0,
            {
                "level": "shallow",
                "kind": KIND_INTERACTION,
                "content": "琐事",
                "emotion_vector": {"chat": 0.2},
                "importance": 0.2,
                "protected": False,
                "narrative": "琐事",
            },
        )
        now = 30 * 86400.0  # 30 天后维护（同时验证索引衰减）
        await soul._maintain_memories(now)
        rec = await heartbeat_store.get_memory(high)
        assert rec is not None
        assert rec["level"] == "working"  # importance 0.8 ≥ 0.4 → 晋升
        # 索引已建且旧记忆强度衰减（30 天：1.0 × e^(-30/45) ≈ 0.51）
        idx = await heartbeat_store.iterate_memory_index()
        assert len(idx) == 2  # 两条记忆都建了索引
        strengths = {mid: s for _, mid, s, _ in idx}
        assert 0.2 < strengths[high] < 0.9  # 30 天衰减后显著低于 1.0
    finally:
        await state_store.close()
        await heartbeat_store.close()
