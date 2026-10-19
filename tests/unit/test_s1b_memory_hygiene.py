"""S1b：记忆卫生——最近交互不进 hooks + 一次交换一条记忆 + 时间档位修正。

真机病根（设计纲 §0）：`id=1000541「那你想听什么呢」`（user、52.6 分钟前）被标「（刚刚）」
并当钩子回喂 ⇒ **延迟回声**（她重新回应早已聊过的话）。

三件事（S1 细则 §S1b）：
- ② `select_hooks`：`kind='interaction'` 且 `now-created_ts < 900s` 且 `not protected` ⇒ 跳过；
- ③ 停止逐条镜像用户消息为记忆；一次交换在下一条消息到来时写成**一条** `kind='exchange'`；
- ④ `age_phrase`：≤10 分钟「刚刚」／≤2 小时「不久前」／当天「今天」。

取消测试（逐条能红，见交付报告）：
- 去掉 ② ⇒ `test_recent_interaction_is_not_hooked` / `test_guard_boundary` 红；
- 去掉 ③ 的封存 ⇒ `test_heartbeat_seals_previous_exchange_on_new_message...` 等红；
- 恢复旧档位 ⇒ `test_age_phrase_new_tiers` / test_retrieve 的档位用例红。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    KIND_EXCHANGE,
    KIND_INTERACTION,
    LEVEL_WORKING,
    SOURCE_USER,
    MemoryRecord,
)
from elysia.memory.retrieve import (
    DIALOGUE_RECALL_GUARD_S,
    age_phrase,
    select_hooks,
)
from elysia.soul.dialogue import (
    EXCHANGE_TEXT_CHARS,
    build_exchange_text,
    record_her_bubble,
    record_user_message,
    seal_pending,
)

T0 = 1_000_000.0
MOOD = {"chat": 0.6, "miss": 0.4}


class _CaptureLLM:
    """假主声：只记录 payload、返回固定合规文本（不联网）。"""

    def __init__(self, text: str = "我在呢。") -> None:
        self.text = text
        self.payloads: list[dict[str, Any]] = []

    async def speak(self, payload: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        from elysia.llm.validator import ValidationResult

        self.payloads.append(dict(payload))
        return SimpleNamespace(
            text=self.text,
            level="main",
            validated=ValidationResult(ok=True, reason=None, sanitized=None),
        )


def _rec(
    *,
    mid: int,
    kind: str = KIND_INTERACTION,
    content: str = "一条记忆",
    created_ts: float = T0,
    protected: bool = False,
    source: str = SOURCE_USER,
) -> MemoryRecord:
    return MemoryRecord(
        id=mid,
        created_ts=created_ts,
        kind=kind,
        content=content,
        emotion_vector={"chat": 0.5},
        importance=0.5,
        level=LEVEL_WORKING,
        protected=protected,
        narrative=content,
        source=source,
        certainty=CERTAINTY_CERTAIN,
    )


# ── ② 进行中的对话不进 hooks（按"还在不在 dialogue_context 里"判）────────
def test_context_member_interaction_is_not_hooked() -> None:
    """取消测试：去掉这道闸 ⇒ 本用例红。

    一条 30 分钟前的交互（超出 900s 兜底窗）**仍在最近 20 轮上下文里**
    ⇒ 它属于"正在进行的对话"，不得被当记忆钩子回喂。
    """
    now = T0 + 10_000.0
    since = now - 3600.0  # 最近 20 轮最旧一条 = 1 小时前（长谈）
    records = [_rec(mid=1, content="那你想听什么呢", created_ts=now - 1800.0)]
    hooks = select_hooks(records, MOOD, now=now, query="想听什么", recent_dialogue_since_ts=since)
    assert hooks == [], f"进行中的对话被当成记忆钩回了：{[h.narrative for h in hooks]}"


def test_interaction_that_fell_out_of_context_becomes_memory() -> None:
    """25 轮之前、已滑出上下文的交互 ⇒ 允许进 hooks（这是"记忆"该有的样子）。"""
    now = T0 + 10_000.0
    since = now - 600.0  # 当前上下文只覆盖最近 10 分钟
    old = _rec(mid=1, content="我去改代码了", created_ts=now - 5000.0)  # 83 分钟前，已滑出
    hooks = select_hooks([old], MOOD, now=now, query="改代码", recent_dialogue_since_ts=since)
    assert [h.memory_id for h in hooks] == [1]
    assert hooks[0].label.startswith("（不久前）"), f"标签应如实说不妨前：{hooks[0].label}"


def test_context_floor_beats_900s_fallback() -> None:
    """上下文下限优先：长谈里超过 900s 的交互仍被挡住；上下文外的老交互放行。"""
    now = T0 + 10_000.0
    since = now - 7200.0
    inside = _rec(mid=1, created_ts=now - 7000.0)  # 比 since 新 ⇒ 还在上下文里
    outside = _rec(mid=2, created_ts=now - 7300.0)  # 比 since 旧 ⇒ 已滑出
    assert select_hooks([inside], MOOD, now=now, recent_dialogue_since_ts=since) == []
    assert [
        h.memory_id for h in select_hooks([outside], MOOD, now=now, recent_dialogue_since_ts=since)
    ] == [2]


def test_fallback_900s_when_context_empty() -> None:
    """兜底路径：上下文为空（刚重启/还没对话）⇒ 5 分钟前的交互仍被 900s 挡住。"""
    now = T0 + 10_000.0
    records = [_rec(mid=1, created_ts=now - 300.0)]
    assert select_hooks(records, MOOD, now=now, recent_dialogue_since_ts=None) == []
    # 但 900s 之外的老交互照常可检索
    older = [_rec(mid=2, created_ts=now - DIALOGUE_RECALL_GUARD_S - 1.0)]
    assert [
        h.memory_id for h in select_hooks(older, MOOD, now=now, recent_dialogue_since_ts=None)
    ] == [2]


def test_protected_recent_interaction_is_retrievable() -> None:
    """S2 承重：她亲手 protected 的交互不被这道闸挡住（与 S0 通道一致）。"""
    now = T0 + 10_000.0
    records = [_rec(mid=1, created_ts=now - 60.0, protected=True)]
    hooks = select_hooks(records, MOOD, now=now, recent_dialogue_since_ts=now - 3600.0)
    assert [h.memory_id for h in hooks] == [1]


# ── ③ 一次交换一条记忆 ───────────────────────────────────────
def test_build_exchange_text_format_and_truncation() -> None:
    turns = [
        {"role": "user", "text": "你" * 500, "exchange_id": 1},
        {
            "role": "her",
            "text": "好" * 500,
            "emotion_vector": {"miss": 0.333, "chat": 0.6},
            "exchange_id": 1,
        },
        {"role": "her", "text": "我等你", "emotion_vector": {"miss": 0.2}, "exchange_id": 1},
    ]
    content, emotion = build_exchange_text(turns)
    assert content.startswith('你："')
    assert "／我（当时 miss=0.20）：" in content  # 取最后一次带感受的 her 轮次
    assert len(content) <= EXCHANGE_TEXT_CHARS * 2 + 40
    assert emotion.get("miss") == 0.2


@pytest.mark.asyncio
async def test_seal_writes_exactly_one_exchange_memory(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    state = StateStore(tmp_path / "state.db")
    await store.start()
    await state.start()
    try:
        await record_user_message(store, T0, "我去改代码了")
        await record_her_bubble(store, T0 + 1, "好，我等你", emotion_vector={"miss": 0.33})
        await record_her_bubble(store, T0 + 2, "改完记得喝水")
        await seal_pending(store, state, T0 + 10)

        memories = await store.iterate_memories()
        exchanges = [m for m in memories if m["kind"] == KIND_EXCHANGE]
        assert len(exchanges) == 1, f"一次交换应只写一条记忆，实际 {len(exchanges)} 条"
        content = exchanges[0]["content"]
        assert "我去改代码了" in content and "好，我等你" in content
        assert "miss=0.33" in content
        assert exchanges[0]["source"] == SOURCE_USER
        # 不再产生逐条 interaction 碎片
        assert not [m for m in memories if m["kind"] == KIND_INTERACTION]

        # 幂等：再封存一次不会重复写
        await seal_pending(store, state, T0 + 20)
        memories = await store.iterate_memories()
        assert len([m for m in memories if m["kind"] == KIND_EXCHANGE]) == 1
    finally:
        await state.close()
        await store.close()


@pytest.mark.asyncio
async def test_restart_seals_pending_exchange(tmp_path: Path) -> None:
    """进程重启时把未封存的那次直接封存。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    state = StateStore(tmp_path / "state.db")
    await store.start()
    await state.start()
    try:
        await record_user_message(store, T0, "今天有点累")
        await record_her_bubble(store, T0 + 2, "那就早点休息呀", emotion_vector={"miss": 0.4})
        assert not [m for m in await store.iterate_memories() if m["kind"] == KIND_EXCHANGE]
        await seal_pending(store, state, T0 + 100)  # 重启时的封存
        exchanges = [m for m in await store.iterate_memories() if m["kind"] == KIND_EXCHANGE]
        assert len(exchanges) == 1 and "今天有点累" in exchanges[0]["content"]
    finally:
        await state.close()
        await store.close()


@pytest.mark.asyncio
async def test_pure_solo_bubbles_do_not_become_exchange(tmp_path: Path) -> None:
    """没有用户轮次的独白不算交换（不写 exchange 记忆）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    state = StateStore(tmp_path / "state.db")
    await store.start()
    await state.start()
    try:
        await record_her_bubble(store, T0, "夜深了，我一个人待着")
        await seal_pending(store, state, T0 + 10)
        assert not [m for m in await store.iterate_memories() if m["kind"] == KIND_EXCHANGE]
    finally:
        await state.close()
        await store.close()


@pytest.mark.asyncio
async def test_heartbeat_seals_previous_exchange_on_new_message(tmp_path: Path) -> None:
    """心跳接线：第二条用户消息到来 ⇒ 封存第一次交换；且不再逐条镜像为 interaction。"""
    from elysia.core.clock import SimulatedClock
    from elysia.core.mode import ModeManager
    from elysia.core.timesense import TimeSense
    from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

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
    try:
        # 第一轮：你说 + 她回
        await state.save_json("interaction", {"ts": T0 + 5, "text": "我去改代码了"})
        await soul._sync_body_status(T0 + 6)
        await record_her_bubble(store, T0 + 7, "好，我等你", emotion_vector={"miss": 0.33})
        # 第二轮：新消息到来 ⇒ 第一轮封存
        await state.save_json("interaction", {"ts": T0 + 100, "text": "改完了"})
        await soul._sync_body_status(T0 + 101)

        memories = await store.iterate_memories()
        exchanges = [m for m in memories if m["kind"] == KIND_EXCHANGE]
        assert len(exchanges) == 1, f"第二轮到来时应封存第一次交换，实际 {len(exchanges)} 条"
        assert "我去改代码了" in exchanges[0]["content"]
        assert not [m for m in memories if m["kind"] == KIND_INTERACTION], (
            "不得再把用户消息逐条镜像成 interaction 记忆（'一定'碎片）"
        )
        turns = await store.dialogue_turns()
        assert [t["role"] for t in turns] == ["user", "her", "user"]
    finally:
        await state.close()
        await store.close()


# ── ④ 时间档位 ───────────────────────────────────────────────
def test_age_phrase_new_tiers() -> None:
    assert age_phrase(5.0 / 1440.0) == "刚刚"
    assert age_phrase(15.0 / 1440.0) == "不久前"
    assert age_phrase(52.0 / 1440.0) == "不久前"  # 52 分钟不再叫「刚刚」
    assert age_phrase(3.0 / 24.0) == "今天"  # 超过 2 小时
    assert age_phrase(2.0 / 24.0 + 1e-6) == "今天"


# ── 接线：表达服务把"上下文最旧 ts"传给记忆检索 ─────────────────
@pytest.mark.asyncio
async def test_run_job_passes_context_since_ts_to_retriever(tmp_path: Path) -> None:
    from elysia.soul.brain import BrainOutput, WillOutput
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState
    from elysia.soul.expression_service import ExpressionService
    from elysia.soul.expression_types import ExpressionJob

    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await record_user_message(store, T0, "第一轮")
        await record_her_bubble(store, T0 + 1, "第一轮的回答")
        await record_user_message(store, T0 + 10, "第二轮")

        captured: dict[str, Any] = {}

        async def _retriever(_store: Any, _mood: Any, **kwargs: Any) -> list[Any]:
            captured.update(kwargs)
            return []

        service = ExpressionService(_CaptureLLM(), store, retriever=_retriever)  # type: ignore[arg-type]
        job = ExpressionJob(
            job_id="job-1",
            created_ts=T0 + 20,
            heartbeat_seq=1,
            output=BrainOutput(
                desire=DesireState(tr=45.0, cs=62.0, sa=20.0),
                feelings=FeelingState(chat=0.6, miss=0.4, explore=0.4),
                will=WillOutput(thought_style=0.1),
            ),
            summary={},
            user_message="第二轮",
            intent="回应",
        )
        await service.run_job(job)
        assert captured.get("recent_dialogue_since_ts") == T0, (
            f"检索必须收到对话上下文最旧 ts（T0），实际：{captured}"
        )
    finally:
        await store.close()
