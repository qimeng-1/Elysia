"""灵魂心跳单测：大脑循环集成、欲望持久化、distress 降频。

与 test_dual_heartbeat.py（进程互不阻塞冒烟）互补：本文件聚焦 P1 特有的
数据流 —— 每拍产出含 desire/feelings/will/brain_action 的快照，并将
欲望状态落盘；distress 切换引发心跳间隔变化。
"""

from __future__ import annotations

import asyncio
import json
import logging
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


# ── P0-R4（D-5）：心跳循环分段隔离 ─────────────────────────────────────
# 审计实测旧 `run()` 187 行里 `try` 出现 **0 次**：单拍任何异常都会杀死协程、
# 丢掉整拍状态变更，然后由 supervisor 每秒重启一次。下面三条用例钉住"阶段隔离"：
# 单阶段失败 ⇒ 循环不退、其余阶段照跑、日志里有阶段名。
# 取消测试：`test_stage_a_failure_does_not_kill_loop`（去掉隔离后该用例必红：
# 异常从 run() 抛出，任务以异常结束）。


async def _wait_for_soul_beats(store: HeartbeatStore, wanted: int, timeout_s: float = 5.0) -> int:
    """**按事实轮询**等心跳落库到 N 拍（不按固定虚拟秒数猜：机器负载会让拍数漂）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    count = 0
    while loop.time() < deadline:
        rows = await store.execute_raw("SELECT COUNT(*) FROM heartbeats WHERE beat_type = 'soul'")
        count = int(rows[0][0])
        if count >= wanted:
            return count
        await asyncio.sleep(0.01)
    raise AssertionError(f"{timeout_s}s 内只等到 {count} 拍心跳（期望 >= {wanted}）")


@pytest.mark.asyncio
async def test_stage_a_failure_does_not_kill_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """取消测试（P0-R4）：阶段 A 每拍抛异常 ⇒ 循环不退，C（落库）照跑且日志含阶段名。"""
    caplog.set_level(logging.ERROR, logger="elysia.soul.heartbeat")
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=False)
    await state_store.start()
    await heartbeat_store.start()

    async def boom(_now: float) -> None:
        raise RuntimeError("注入的阶段异常")

    monkeypatch.setattr(soul, "_sync_body_status", boom)
    task = asyncio.create_task(soul.run())
    try:
        await _wait_for_soul_beats(heartbeat_store, 2)
        assert not task.done(), "单阶段失败绝不能杀死心跳循环（旧写法：任务以异常结束）"
    finally:
        await soul.stop()
        await task
        await state_store.close()
        await heartbeat_store.close()

    assert "A:read_body_mode" in caplog.text, "日志必须含**阶段名**（不然排查时不知道坏在哪）"
    assert "注入的阶段异常" in caplog.text


@pytest.mark.asyncio
async def test_stage_c_persist_failure_is_visible_but_loop_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """落库失败**不许静默**（D-5 明确要求），但也不许杀死循环。"""
    caplog.set_level(logging.ERROR, logger="elysia.soul.heartbeat")
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=False)
    await state_store.start()
    await heartbeat_store.start()

    async def boom(self: object, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("注入的落库异常")

    monkeypatch.setattr(HeartbeatStore, "append", boom)
    task = asyncio.create_task(soul.run())
    try:
        for _ in range(200):  # 等到日志里出现该阶段（最多约 2s）
            if "C:persist_beat" in caplog.text:
                break
            await asyncio.sleep(0.01)
        assert not task.done(), "落库失败不得杀死循环（否则她会永远退不回来）"
    finally:
        await soul.stop()
        await task
        await state_store.close()
        await heartbeat_store.close()

    assert "C:persist_beat" in caplog.text
    assert "注入的落库异常" in caplog.text


@pytest.mark.asyncio
async def test_stage_b_brain_failure_does_not_lose_the_beat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """大脑阶段失败 ⇒ 本拍降级为"无大脑输出"，但心跳照落库（她还在场的证据不能丢）。"""
    caplog.set_level(logging.ERROR, logger="elysia.soul.heartbeat")
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    brain = soul._brain_loop
    assert brain is not None

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("注入的大脑异常")

    monkeypatch.setattr(brain, "step", boom)
    task = asyncio.create_task(soul.run())
    try:
        await _wait_for_soul_beats(heartbeat_store, 2)
        assert not task.done()
    finally:
        await soul.stop()
        await task
        await state_store.close()
        await heartbeat_store.close()

    assert "B:brain_step" in caplog.text


# ── P0-R16：缺口事件化 —— 同一批丢失只报一次，新丢失才会再推 TR ──────────


@pytest.mark.asyncio
async def test_feel_memory_gaps_is_episodic(tmp_path: Path) -> None:
    """取消测试（P0-R16）：去掉"只对新增丢失起反应"⇒ 第二轮仍推 TR ⇒ 红。"""
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        await _add_old_memory(heartbeat_store, age_days=100.0, recalled=True)
        await soul._maintain_memories(T0)
        brain = soul._brain_loop
        assert brain is not None

        tr0 = brain.desire_system.state.tr
        await soul._feel_memory_gaps()  # 第一次：新丢失 → 好奇脉冲
        tr1 = brain.desire_system.state.tr
        assert tr1 > tr0

        await soul._feel_memory_gaps()  # 第二轮：没有新增 → 不再推
        assert brain.desire_system.state.tr == pytest.approx(tr1)

        # 又老了一条 → 新的丢失出现 → 再推一次
        await _add_old_memory(heartbeat_store, age_days=120.0, recalled=True)
        await soul._maintain_memories(T0)
        await soul._feel_memory_gaps()
        assert brain.desire_system.state.tr > tr1
    finally:
        await state_store.close()
        await heartbeat_store.close()


# ── P0-R19：选择不回复（b 方案）——reason 当场落库 + 决策确定性 ──────────
# 取消测试：去掉 `_record_restraint` 落库 ⇒ 本用例红；`_decide_intent` 丢掉
# reply_decision ⇒ 返回"回应"而不是"暂不回应" ⇒ 红。


@pytest.mark.asyncio
async def test_restraint_reason_is_logged_and_decision_is_deterministic(tmp_path: Path) -> None:
    from elysia.soul.brain import BrainOutput, WillOutput
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState
    from elysia.soul.expression import RESTRAINT_INTENT, compute_reply_desire

    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        output = BrainOutput(
            desire=DesireState(tr=46.0, cs=62.0, sa=20.0),
            feelings=FeelingState(
                chat=0.30, miss=0.05, explore=0.46, curiosity=0.0, rest=0.05, self_check=0.2
            ),
            will=WillOutput(direction="still", strength=0.54, thought_style=0.0, anim_bias=0.0),
            action="none",
        )
        quiet = compute_reply_desire(chat=0.30, miss=0.05, interval_factor=2.0, distress=True)
        assert quiet.speak is False
        soul._new_interaction = True
        assert soul._decide_intent(output, greeting=False, reply_decision=quiet) == RESTRAINT_INTENT

        # 同一输入 + 同一状态 ⇒ 同一决策（不随机）
        again = compute_reply_desire(chat=0.30, miss=0.05, interval_factor=2.0, distress=True)
        assert again == quiet

        await soul._record_restraint(T0, quiet)
        rows = await heartbeat_store.execute_raw(
            "SELECT intent, llm_text, validation FROM expression_log ORDER BY id DESC LIMIT 1"
        )
        assert rows, "选择不回必须有库内记录（最高验收标准）"
        assert rows[0][0] == RESTRAINT_INTENT
        assert rows[0][1] == ""
        validation = json.loads(rows[0][2])
        assert validation["restraint"] is True
        assert validation["reason"] == quiet.reason
        assert validation["reply_desire"] == pytest.approx(quiet.reply_desire)
    finally:
        await state_store.close()
        await heartbeat_store.close()


# ── P0-R19 闭环接线：回不回决定社交满足（取消测试 #5 的接线部分）──────────
@pytest.mark.asyncio
async def test_social_pulse_settles_by_reply_choice(tmp_path: Path) -> None:
    """选择不回 ⇒ silence（CS 降）；回应 ⇒ interaction（CS 升）。"""
    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        brain = soul._brain_loop
        assert brain is not None
        cs0 = brain.desire_system.state.cs
        await soul._settle_social_pulse(restraint=True)
        assert brain.desire_system.state.cs < cs0, "选择不回必须让社交落空（CS 下降）"
        cs1 = brain.desire_system.state.cs
        await soul._settle_social_pulse(restraint=False)
        assert brain.desire_system.state.cs > cs1, "回应必须有社交满足（CS 上升）"
    finally:
        await state_store.close()
        await heartbeat_store.close()


# ── P0-R19b：一次性修复"关系卡在 1.0"（旧动力学死区的饱和产物）──────────
@pytest.mark.asyncio
async def test_stuck_withdrawal_is_repaired_once(tmp_path: Path) -> None:
    from elysia.soul.brain import BrainOutput, WillOutput
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState

    soul, state_store, heartbeat_store, _clock = _make_soul(tmp_path, with_brain=True)
    await state_store.start()
    await heartbeat_store.start()
    try:
        await state_store.save_json("relationship_withdrawal", 1.0)
        output = BrainOutput(
            desire=DesireState(tr=46.0, cs=62.0, sa=20.0),
            feelings=FeelingState(chat=0.62, miss=0.38, explore=0.46),
            will=WillOutput(direction="reach", strength=0.54, thought_style=0.0, anim_bias=0.0),
            action="none",
        )
        tone = await soul._advance_relationship(T0, output)
        assert tone.withdrawal == 0.0, "卡死的 1.0 必须被一次性修复（否则她会永久拒绝说话）"
        assert await state_store.load_json("relationship_withdrawal", default=None) == 0.0
        assert (
            await state_store.load_json("relationship_withdrawal_repaired", default=False) is True
        )

        # 修复只发生一次：她今后真的再次退缩到 1.0，不会被下次启动抹掉
        soul._relationship_loaded = False
        await state_store.save_json("relationship_withdrawal", 1.0)
        tone = await soul._advance_relationship(T0 + 1.0, output)
        assert tone.withdrawal >= 0.99, "已有修复标记后，真实的 1.0 必须保留"
    finally:
        await state_store.close()
        await heartbeat_store.close()
