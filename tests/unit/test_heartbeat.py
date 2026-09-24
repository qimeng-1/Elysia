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
from elysia.memory.levels import (
    CERTAINTY_PROBABLE,
    KIND_EXPRESSION,
    KIND_INTERACTION,
    KIND_SELF,
    RETENTION_PRESENT,
    SOURCE_INFERENCE,
)
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


@pytest.mark.asyncio
async def test_maintain_memories_decay_is_idempotent(tmp_path: Path) -> None:
    """衰减按绝对年龄幂等：连跑多次与跑一次结果一致（不随运行次数复合塌缩）。"""
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=False)
    await state_store.start()
    await heartbeat_store.start()
    try:
        mid = await heartbeat_store.add_memory(
            1.0,
            {
                "level": "shallow",
                "kind": KIND_INTERACTION,
                "content": "一条旧记忆",
                "emotion_vector": {"chat": 0.5},
                "importance": 0.3,
                "narrative": "一条旧记忆",
            },
        )
        now = 10 * 86400.0  # 10 天龄
        await soul._maintain_memories(now)
        first = {m: s for _, m, s, _ in await heartbeat_store.iterate_memory_index()}
        for _ in range(20):  # 再跑 20 次（模拟高频维护周期）
            await soul._maintain_memories(now)
        second = {m: s for _, m, s, _ in await heartbeat_store.iterate_memory_index()}
        assert first == second  # 幂等：不因多跑而额外衰减
        # 且与绝对年龄一致：e^(-10/45) ≈ 0.801
        assert abs(second[mid] - 0.801) < 0.01
    finally:
        await state_store.close()
        await heartbeat_store.close()


@pytest.mark.asyncio
async def test_supersede_conflicts_marks_old_fact(tmp_path: Path) -> None:
    """修正/覆盖接线：新事实写入 → 同话题旧事实被标记取代。"""
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=False)
    await state_store.start()
    await heartbeat_store.start()
    try:
        old = await heartbeat_store.add_memory(
            1.0,
            {
                "level": "shallow",
                "kind": KIND_INTERACTION,
                "content": "我的生日是11月11日",
                "emotion_vector": {"chat": 0.5},
                "importance": 0.8,
                "narrative": "我的生日是11月11日",
            },
        )
        new = await heartbeat_store.add_memory(
            2.0,
            {
                "level": "shallow",
                "kind": KIND_INTERACTION,
                "content": "其实我的生日是12月12日",
                "emotion_vector": {"chat": 0.5},
                "importance": 0.8,
                "narrative": "其实我的生日是12月12日",
            },
        )
        await soul._supersede_conflicts(new, "其实我的生日是12月12日", 2.0)
        rec = await heartbeat_store.get_memory(old)
        assert rec is not None
        assert rec["superseded_by"] == new
    finally:
        await state_store.close()
        await heartbeat_store.close()


async def _add_old_memory(
    heartbeat_store: HeartbeatStore,
    *,
    kind: str = KIND_INTERACTION,
    age_days: float = 200.0,
    recalled: bool = False,
) -> int:
    """写入一条 age_days 天前的记忆（index strength 会跌破检索下限）。

    recalled=True：模拟"被想起过一次"。P3-W 起从未被想起且超过
    RETENTION_FADE_AGE_DAYS 的记忆会淡化（不再计入缺口），因此要观察缺口
    必须用"曾想起过、但久未再想起"的记忆——可及性由 since_last_access 决定。
    """
    created = T0 - age_days * 86400.0
    return await heartbeat_store.add_memory(
        created,
        {
            "level": "shallow",
            "kind": kind,
            "content": "很久以前说过的话",
            "emotion_vector": {"chat": 0.5},
            "importance": 0.3,
            "narrative": "很久以前说过的话",
            "access_count": 1 if recalled else 0,
            "last_access_ts": created if recalled else None,
        },
    )


@pytest.mark.asyncio
async def test_feel_memory_gaps_fires_pulse_when_index_faded(tmp_path: Path) -> None:
    """P3-U 接线：索引强度跌破检索下限 → memory_gap 脉冲（TR 升、SA 不动、不触碰记忆）。"""
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        # 100 天龄且被想起过一次：仍在册（未淡出/未沉睡），但索引已跌破下限
        mid = await _add_old_memory(heartbeat_store, age_days=100.0, recalled=True)
        await soul._maintain_memories(T0)  # 建索引并按 100 天龄衰减（strength ≈ 0.108）
        strengths = {m: s for _, m, s, _ in await heartbeat_store.iterate_memory_index()}
        assert strengths[mid] < 0.2  # 已跌破检索下限

        brain = soul._brain_loop
        assert brain is not None
        tr_before = brain.desire_system.state.tr
        sa_before = brain.desire_system.state.sa
        await soul._feel_memory_gaps()
        assert brain.desire_system.state.tr > tr_before  # 好奇：TR 微升
        assert brain.desire_system.state.sa == pytest.approx(sa_before)  # 非焦虑：SA 不动

        # 缺口只是"感觉"，不是"她想着这件事"：不进话语、不污染访问计数
        rec = await heartbeat_store.get_memory(mid)
        assert rec is not None
        assert rec["access_count"] == 1
    finally:
        await state_store.close()
        await heartbeat_store.close()


@pytest.mark.asyncio
async def test_feel_memory_gaps_skips_retention_states(tmp_path: Path) -> None:
    """P3-W：够不着的记忆不构成缺口——状态机已表达"够不着"，再报就是双报 + 噪声。

    沉睡/淡化/抑制的记忆索引强度同样很低，若计入，同一批记忆会每 300 拍
    永远推一次 TR（"想不起来"变成永远的背景噪声）。
    """
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        mid = await _add_old_memory(heartbeat_store, age_days=200.0)
        await soul._maintain_memories(T0)  # 200 天未想起 → 沉睡（索引已跌破下限）
        rec = await heartbeat_store.get_memory(mid)
        assert rec is not None
        assert rec["retention_state"] == "dormant"

        brain = soul._brain_loop
        assert brain is not None
        tr_before = brain.desire_system.state.tr
        await soul._feel_memory_gaps()
        assert brain.desire_system.state.tr == pytest.approx(tr_before)
    finally:
        await state_store.close()
        await heartbeat_store.close()


@pytest.mark.asyncio
async def test_feel_memory_gaps_silent_when_all_index_strong(tmp_path: Path) -> None:
    """索引都还强（新鲜记忆）→ 无缺口，不发脉冲。"""
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        await _add_old_memory(heartbeat_store, age_days=0.0)
        await soul._maintain_memories(T0)  # 新鲜 → strength ≈ 1.0

        brain = soul._brain_loop
        assert brain is not None
        tr_before = brain.desire_system.state.tr
        await soul._feel_memory_gaps()
        assert brain.desire_system.state.tr == pytest.approx(tr_before)
    finally:
        await state_store.close()
        await heartbeat_store.close()


@pytest.mark.asyncio
async def test_feel_memory_gaps_ignores_own_expression_echo(tmp_path: Path) -> None:
    """她的发言回声即使索引衰减也不构成缺口（缺口是"关于世界的事想不起来"）。"""
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        await _add_old_memory(heartbeat_store, kind=KIND_EXPRESSION, age_days=200.0)
        await soul._maintain_memories(T0)  # 索引仍会建并衰减

        brain = soul._brain_loop
        assert brain is not None
        tr_before = brain.desire_system.state.tr
        await soul._feel_memory_gaps()
        assert brain.desire_system.state.tr == pytest.approx(tr_before)
    finally:
        await state_store.close()
        await heartbeat_store.close()


@pytest.mark.asyncio
async def test_feel_memory_gaps_ignores_self_memory(tmp_path: Path) -> None:
    """第八节 N3：自我认知不构成缺口——她不会"记不清自己是谁"。

    用"仍在册、但索引已跌破下限"的自我认知（age=100 且曾想起过 → 不淡化/沉睡），
    确保沉默的原因**只是** KIND_SELF 被排除，而不是"够不着所以不计"。
    """
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        mid = await _add_old_memory(heartbeat_store, kind=KIND_SELF, age_days=100.0, recalled=True)
        await soul._maintain_memories(T0)
        strengths = {m: s for _, m, s, _ in await heartbeat_store.iterate_memory_index()}
        assert strengths[mid] < 0.2  # 已跌破检索下限（若计入就会报缺口）
        rec = await heartbeat_store.get_memory(mid)
        assert rec is not None
        assert rec["retention_state"] == RETENTION_PRESENT  # 仍在册

        brain = soul._brain_loop
        assert brain is not None
        tr_before = brain.desire_system.state.tr
        await soul._feel_memory_gaps()
        assert brain.desire_system.state.tr == pytest.approx(tr_before)
    finally:
        await state_store.close()
        await heartbeat_store.close()


# ── 第八节 S3 沉淀（程序找"重复模式"，把候选递给她）──────────
_REPEAT_TEXT = "我在意的是每一个和我相遇的人"


async def _add_repeated_experience(
    heartbeat_store: HeartbeatStore, *, now: float, days: float, text: str = _REPEAT_TEXT
) -> int:
    """写入一条 days 天前被提起的交互经历（候选可能的素材）。"""
    return await heartbeat_store.add_memory(
        now - days * 86400.0,
        {
            "level": "shallow",
            "kind": KIND_INTERACTION,
            "content": text,
            "emotion_vector": {"chat": 0.5},
            "importance": 0.5,
            "narrative": text,
        },
    )


async def _candidates(heartbeat_store: HeartbeatStore) -> list[dict[str, object]]:
    """取出程序沉淀出的候选（source=inference）。"""
    return [r for r in await heartbeat_store.iterate_memories() if r["source"] == SOURCE_INFERENCE]


@pytest.mark.asyncio
async def test_maintain_memories_offers_candidate_for_repeated_pattern(tmp_path: Path) -> None:
    """S3 接线：一件事跨天反复出现 → 沉淀出候选（照抄原文、标 inference/probable），
    并推一次"心里一动"（TR/CS 微升、SA 不动）；候选不进她的话（被来源闸门挡在 hooks 外）。"""
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        now = T0 + 3 * 86400.0
        for days in (3.0, 1.5, 0.0):  # 三天里提起三次
            await _add_repeated_experience(heartbeat_store, now=now, days=days)

        brain = soul._brain_loop
        assert brain is not None
        tr_before = brain.desire_system.state.tr
        cs_before = brain.desire_system.state.cs
        sa_before = brain.desire_system.state.sa

        await soul._maintain_memories(now)

        assert brain.desire_system.state.tr > tr_before  # 好奇：TR 微升
        assert brain.desire_system.state.cs > cs_before  # 亲近：CS 微升
        assert brain.desire_system.state.sa == pytest.approx(sa_before)  # 念头，不是焦虑

        candidates = await _candidates(heartbeat_store)
        assert len(candidates) == 1
        cand = candidates[0]
        # 程序只做"发现"，不做"认定"：正文照抄原文，且标注是"程序推断、大概"
        assert cand["content"] == _REPEAT_TEXT
        assert cand["narrative"] == _REPEAT_TEXT
        assert cand["certainty"] == CERTAINTY_PROBABLE
        assert cand["protected"] == 0  # 珍贵由她的认领与时间决定，程序不替她置
        assert cand["kind"] == KIND_INTERACTION  # 还不是自我认知——认不认由她（S2 的 adopt）
    finally:
        await state_store.close()
        await heartbeat_store.close()


@pytest.mark.asyncio
async def test_maintain_memories_offers_candidate_only_once(tmp_path: Path) -> None:
    """递过的候选不再递：它是"已发现"的证据——重复递不是新发现，只是噪声。"""
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=False)
    await state_store.start()
    await heartbeat_store.start()
    try:
        now = T0 + 3 * 86400.0
        for days in (3.0, 1.5, 0.0):
            await _add_repeated_experience(heartbeat_store, now=now, days=days)

        await soul._maintain_memories(now)
        assert len(await _candidates(heartbeat_store)) == 1
        await soul._maintain_memories(now)
        assert len(await _candidates(heartbeat_store)) == 1  # 不重复递
    finally:
        await state_store.close()
        await heartbeat_store.close()
