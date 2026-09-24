"""P3 运行时接线单测：user_message 打字对话 + 记忆写入 + 检索注入。

覆盖三类接线（P3-G）：
1. user_message → 表达指令 payload（T2 话题非指令）
2. 表达完成 → 她的发言写入 memories（经历）
3. retriever 回调 → memory_hooks 注入
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.identity import IDENTITY_FIELD, MAX_IDENTITY_LINES
from elysia.llm.validator import ValidationResult
from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    CERTAINTY_PROBABLE,
    CLAIM_CLAIMED,
    CLAIM_REJECTED,
    KIND_EXPRESSION,
    KIND_INTERACTION,
    KIND_SELF,
    LEVEL_DEEP,
    RETENTION_PRESENT,
    RETENTION_SUPPRESSED,
    SOURCE_INFERENCE,
    SOURCE_SELF,
)
from elysia.memory.retrieve import retrieve_from_store
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireEvent, DesireState, DesireSystem
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService

_call_payload: list[dict[str, Any]] = []


class FakeLLM:
    """记录最后一次收到的 payload，返回固定文本。"""

    async def speak(self, payload: dict[str, Any]) -> Any:
        _call_payload.append(payload)
        return SimpleNamespace(
            text="嗨♪ 我听到啦，会好好记住的～",
            level="main",
            validated=ValidationResult(ok=True, reason=None, sanitized=None),
        )


def _make_output() -> BrainOutput:
    return BrainOutput(
        desire=DesireState(tr=50.0, cs=60.0, sa=20.0),
        feelings=FeelingState(
            chat=0.6, miss=0.2, explore=0.3, curiosity=0.2, rest=0.1, self_check=0.0
        ),
        will=WillOutput(direction="reach", strength=0.5, thought_style=0.1, anim_bias=0.0),
        action="none",
    )


@pytest.fixture
def store(tmp_path: Path) -> HeartbeatStore:
    return HeartbeatStore(tmp_path / "heartbeat.db")


@pytest.mark.asyncio
async def test_user_message_injected_as_topic(store: HeartbeatStore) -> None:
    await store.start()
    try:
        _call_payload.clear()
        llm = FakeLLM()
        svc = ExpressionService(llm, store)
        await svc.tick(_make_output(), now=1.0, force=True, user_message="今天天气怎么样？")
        assert _call_payload, "应该触发了一次 LLM 调用"
        payload = _call_payload[0]
        # T2：user_message 作为话题字段注入，且未被当作指令（intent 仍为回应）
        assert payload.get("user_message") == "今天天气怎么样？"
        assert payload.get("intent") == "回应"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_no_user_message_when_absent(store: HeartbeatStore) -> None:
    await store.start()
    try:
        _call_payload.clear()
        svc = ExpressionService(FakeLLM(), store)
        await svc.tick(_make_output(), now=1.0, force=True)
        assert "user_message" not in _call_payload[0]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_expression_writes_memory(store: HeartbeatStore) -> None:
    await store.start()
    try:
        svc = ExpressionService(FakeLLM(), store)
        await svc.tick(_make_output(), now=1.0, force=True)
        memories = await store.iterate_memories()
        assert len(memories) == 1
        rec = memories[0]
        assert rec["kind"] == KIND_EXPRESSION
        assert rec["content"] == "嗨♪ 我听到啦，会好好记住的～"
        assert rec["level"] == "shallow"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_no_hooks_injected_without_topic(store: HeartbeatStore) -> None:
    """根治"每句都强调"：没有话题就不注入记忆——她想提就用自己的 recall 工具。"""
    await store.start()
    try:
        await store.add_memory(
            1.0,
            {
                "level": "deep",
                "kind": KIND_INTERACTION,
                "content": "我的生日是5月21日",
                "emotion_vector": {"chat": 0.9},
                "importance": 0.97,
                "protected": True,
                "narrative": "我的生日是5月21日",
            },
        )
        _call_payload.clear()
        svc = ExpressionService(FakeLLM(), store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert _call_payload[0]["memory_hooks"] == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_hooks_injected_when_topic_matches(store: HeartbeatStore) -> None:
    """话题撞上记忆 → 唤起并注入（被话题触发，而非每句都推）。"""
    await store.start()
    try:
        base = 1000.0
        now = base + 3 * 86400.0
        await store.add_memory(
            base,  # 三天前她告诉我的事
            {
                "level": "deep",
                "kind": KIND_INTERACTION,
                "content": "你上次说喜欢晚霞",
                "emotion_vector": {"chat": 0.9},
                "importance": 0.9,
                "protected": True,
                "narrative": "你上次说喜欢晚霞",
            },
        )
        _call_payload.clear()
        svc = ExpressionService(FakeLLM(), store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=now, force=True, user_message="你上次说喜欢什么来着？")
        # 注入时带相对时间锚点：她因此能说"你上次说的"，也分得清新旧
        assert _call_payload[0].get("memory_hooks") == ["（3天前）你上次说喜欢晚霞"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_hooks_not_injected_for_unrelated_topic(store: HeartbeatStore) -> None:
    """无关对话不把往事带到嘴边——这正是"违和感"的来源。"""
    await store.start()
    try:
        await store.add_memory(
            1.0,
            {
                "level": "deep",
                "kind": KIND_INTERACTION,
                "content": "我的生日是5月21日",
                "emotion_vector": {"chat": 0.9},
                "importance": 0.97,
                "protected": True,
                "narrative": "我的生日是5月21日",
            },
        )
        _call_payload.clear()
        svc = ExpressionService(FakeLLM(), store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True, user_message="今天天气怎么样？")
        assert _call_payload[0]["memory_hooks"] == []
    finally:
        await store.close()


class FakeToolLLM:
    """模拟具备"工具能力"的主声：调用哪个工具、是否调用由替身开关决定（她的选择）。"""

    def __init__(self, *, call_recall: bool, topic: str = "普通日常", tool: str = "recall") -> None:
        self._call_recall = call_recall
        self._topic = topic
        self._tool = tool
        self.tool_runner: Any = None
        self.tool_result: str | None = None

    def set_tool_runner(self, runner: Any) -> None:
        self.tool_runner = runner

    async def speak(self, payload: dict[str, Any]) -> Any:
        _call_payload.append(payload)
        if self._call_recall and self.tool_runner is not None:
            self.tool_result = await self.tool_runner(self._tool, {"topic": self._topic})
        return SimpleNamespace(
            text="嗨♪ 我记得的～",
            level="main",
            validated=ValidationResult(ok=True, reason=None, sanitized=None),
        )


@pytest.mark.asyncio
async def test_recall_tool_hands_memory_to_her(store: HeartbeatStore) -> None:
    """她自己调用 recall → 程序把事实递到她手上（能力由程序保证）。"""
    await store.start()
    try:
        base = 1000.0
        now = base + 5 * 86400.0  # 五天前的事
        await store.add_memory(
            base,
            {
                "level": "deep",
                "kind": KIND_INTERACTION,
                "content": "普通日常",
                "emotion_vector": {"chat": 0.9},
                "importance": 0.5,
                "protected": False,
                "narrative": "普通日常",
            },
        )
        _call_payload.clear()
        llm = FakeToolLLM(call_recall=True)
        svc = ExpressionService(llm, store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=now, force=True)
        # 递回去的事实同样带时间锚点（她才知道那是"五天前"说的）
        assert llm.tool_result == "（5天前）普通日常"
        # 她没主动要，程序也没往 payload 里塞记忆
        assert _call_payload[0]["memory_hooks"] == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_recall_tool_returns_empty_when_nothing_matches(store: HeartbeatStore) -> None:
    """没想起就没有内容递回——不编造、不硬塞无关往事。"""
    await store.start()
    try:
        await store.add_memory(
            1.0,
            {
                "level": "deep",
                "kind": KIND_INTERACTION,
                "content": "我的生日是5月21日",
                "emotion_vector": {"chat": 0.9},
                "importance": 0.97,
                "protected": True,
                "narrative": "我的生日是5月21日",
            },
        )
        llm = FakeToolLLM(call_recall=True, topic="昨晚的球赛")
        svc = ExpressionService(llm, store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert llm.tool_result == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_not_calling_recall_is_her_choice(store: HeartbeatStore) -> None:
    """她不想提就不调用——用不用是她的权力，程序不代劳。"""
    await store.start()
    try:
        await store.add_memory(
            1.0,
            {
                "level": "deep",
                "kind": KIND_INTERACTION,
                "content": "我的生日是5月21日",
                "emotion_vector": {"chat": 0.9},
                "importance": 0.97,
                "protected": True,
                "narrative": "我的生日是5月21日",
            },
        )
        llm = FakeToolLLM(call_recall=False)
        svc = ExpressionService(llm, store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert llm.tool_result is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_recall_pushes_feeling_not_words(store: HeartbeatStore) -> None:
    """记忆走"感受"这条路：想起往事 → 心情被推（TR/CS 微升），话里可以一个字不提。"""
    await store.start()
    try:
        await store.add_memory(
            1.0,
            {
                "level": "deep",
                "kind": KIND_INTERACTION,
                "content": "普通日常",
                "emotion_vector": {"chat": 0.9},
                "importance": 0.5,
                "protected": False,
                "narrative": "普通日常",
            },
        )
        desire = DesireSystem()
        tr_before, cs_before = desire.state.tr, desire.state.cs

        async def _on_recall(intensity: float) -> None:
            desire.apply_event(DesireEvent(kind="memory_recall", intensity=intensity))

        llm = FakeToolLLM(call_recall=True)
        svc = ExpressionService(llm, store, retriever=retrieve_from_store, on_recall=_on_recall)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert desire.state.tr > tr_before
        assert desire.state.cs > cs_before
    finally:
        await store.close()


# ── P3-V 拒绝认领（她的权力：程序不代她拒绝）──────────────
@pytest.mark.asyncio
async def test_disclaim_tool_rejects_memory(store: HeartbeatStore) -> None:
    """她拒绝认领 → 那条记忆标为 rejected，且不再进话语。"""
    await store.start()
    try:
        await store.add_memory(
            1.0,
            {
                "level": "deep",
                "kind": KIND_INTERACTION,
                "content": "我的生日是5月21日",
                "emotion_vector": {"chat": 0.9},
                "importance": 0.97,
                "protected": True,
                "narrative": "我的生日是5月21日",
            },
        )
        llm = FakeToolLLM(call_recall=True, topic="生日", tool="disclaim")
        svc = ExpressionService(llm, store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert llm.tool_result is not None and "不再把" in llm.tool_result
        rec = (await store.iterate_memories())[0]
        assert rec["claim_status"] == CLAIM_REJECTED
        # 拒绝后：话题撞上也召回不到（"我不认这个"是真的）
        hooks = await retrieve_from_store(store, {"chat": 0.9}, query="我的生日是哪天")
        assert hooks == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_disclaim_tool_no_match_keeps_memory(store: HeartbeatStore) -> None:
    """没找到对应记忆就如实说没找到——不猜、不误伤别的记忆。"""
    await store.start()
    try:
        await store.add_memory(
            1.0,
            {
                "level": "deep",
                "kind": KIND_INTERACTION,
                "content": "我的生日是5月21日",
                "emotion_vector": {"chat": 0.9},
                "importance": 0.97,
                "protected": True,
                "narrative": "我的生日是5月21日",
            },
        )
        llm = FakeToolLLM(call_recall=True, topic="昨晚的球赛", tool="disclaim")
        svc = ExpressionService(llm, store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert llm.tool_result == "（你没找到想拒绝认领的那件事）"
        assert (await store.iterate_memories())[0]["claim_status"] == CLAIM_CLAIMED
    finally:
        await store.close()


# ── P3-W2 遗忘与收回（她的权力：程序不代她忘，也不代她收回）────
async def _add_forgettable(store: HeartbeatStore, content: str) -> int:
    """落一条"能被想起"的记忆（默认 present，可被 forget 命中）。"""
    return await store.add_memory(
        1.0,
        {
            "level": "deep",
            "kind": KIND_INTERACTION,
            "content": content,
            "emotion_vector": {"chat": 0.9},
            "importance": 0.9,
            "protected": False,
            "narrative": content,
        },
    )


@pytest.mark.asyncio
async def test_forget_tool_suppresses_memory(store: HeartbeatStore) -> None:
    """她想忘掉某件事 → 那条记忆进入 suppressed，且不再进话语、不再进感受。"""
    await store.start()
    try:
        await _add_forgettable(store, "那天我们吵了一架")
        llm = FakeToolLLM(call_recall=True, topic="那天我们吵了一架", tool="forget")
        svc = ExpressionService(llm, store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert llm.tool_result is not None and "不再想想起" in llm.tool_result
        rec = (await store.iterate_memories())[0]
        assert rec["retention_state"] == RETENTION_SUPPRESSED
        # 忘了就是真的忘了：话题撞上也召回不到
        hooks = await retrieve_from_store(store, {"chat": 0.9}, query="那天我们吵了一架")
        assert hooks == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_forget_tool_does_not_touch_claim_status(store: HeartbeatStore) -> None:
    """forget 只动 retention_state，不碰 claim_status——两个维度正交。"""
    await store.start()
    try:
        await _add_forgettable(store, "那天我们吵了一架")
        llm = FakeToolLLM(call_recall=True, topic="那天我们吵了一架", tool="forget")
        svc = ExpressionService(llm, store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True)
        rec = (await store.iterate_memories())[0]
        assert rec["retention_state"] == RETENTION_SUPPRESSED
        assert rec["claim_status"] == CLAIM_CLAIMED
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_forget_tool_no_false_hit_when_ambiguous(store: HeartbeatStore) -> None:
    """判据加严（覆盖率 ≥ 0.5 且 top1 ≥ 2×top2）：不够贴题/不突出时如实回"没找到"，不误伤。"""
    await store.start()
    try:
        await _add_forgettable(store, "我的生日是5月21日")
        llm = FakeToolLLM(call_recall=True, topic="昨晚的球赛", tool="forget")
        svc = ExpressionService(llm, store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert llm.tool_result == "（你没找到想忘记的那件事）"
        assert (await store.iterate_memories())[0]["retention_state"] == RETENTION_PRESENT
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_forget_tool_no_false_hit_when_tied(store: HeartbeatStore) -> None:
    """两条同样贴题的记忆并列时，不挑一条误伤——如实回"没找到"。"""
    await store.start()
    try:
        await _add_forgettable(store, "那天我们吵了一架")
        await _add_forgettable(store, "那天我们吵了一架")
        llm = FakeToolLLM(call_recall=True, topic="那天我们吵了一架", tool="forget")
        svc = ExpressionService(llm, store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert llm.tool_result == "（你没找到想忘记的那件事）"
        # 两条并列的往事都原样保留（她的发言也会落库，只看交互记忆）
        recs = [r for r in await store.iterate_memories() if r["kind"] == KIND_INTERACTION]
        assert [r["retention_state"] for r in recs] == [RETENTION_PRESENT, RETENTION_PRESENT]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_restore_tool_only_searches_suppressed(store: HeartbeatStore) -> None:
    """restore 只在 suppressed 里找——绝不"恢复"一条从未被忘掉的记忆。"""
    await store.start()
    try:
        await _add_forgettable(store, "我的生日是5月21日")
        llm = FakeToolLLM(call_recall=True, topic="我的生日是5月21日", tool="restore")
        svc = ExpressionService(llm, store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert llm.tool_result == "（你没有想收回来的那件事）"
        assert (await store.iterate_memories())[0]["retention_state"] == RETENTION_PRESENT
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_restore_tool_brings_suppressed_memory_back(store: HeartbeatStore) -> None:
    """她又愿意想起了 → 那条记忆回到 present，重新进话语。"""
    await store.start()
    try:
        await _add_forgettable(store, "那天我们吵了一架")
        mem_id = int((await store.iterate_memories())[0]["id"])
        await store.set_retention_state(mem_id, RETENTION_SUPPRESSED)
        llm = FakeToolLLM(call_recall=True, topic="那天我们吵了一架", tool="restore")
        svc = ExpressionService(llm, store, retriever=retrieve_from_store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert llm.tool_result is not None and "愿意重新想起" in llm.tool_result
        rec = (await store.iterate_memories())[0]
        assert rec["retention_state"] == RETENTION_PRESENT
        assert rec["claim_status"] == CLAIM_CLAIMED
    finally:
        await store.close()


# ── 第八节 S1 身份段装配（"我是谁"每句在场，不走 hooks 段）────
async def _add_self_memory(store: HeartbeatStore, narrative: str) -> int:
    """落一条她认领的自我认知（kind=self，直接落深层 + 珍贵——她认领即核心）。"""
    return await store.add_memory(
        1.0,
        {
            "level": "deep",
            "kind": KIND_SELF,
            "content": narrative,
            "emotion_vector": {"chat": 0.5},
            "importance": 0.9,
            "protected": True,
            "narrative": narrative,
        },
    )


@pytest.mark.asyncio
async def test_identity_carries_bootstrap_when_nothing_claimed(store: HeartbeatStore) -> None:
    """尚无认领 → 身份段为空，后端退化为出生设定（对外行为零变化）。"""
    await store.start()
    try:
        _call_payload.clear()
        svc = ExpressionService(FakeLLM(), store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert _call_payload[0][IDENTITY_FIELD] == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_identity_carries_claimed_self_memory(store: HeartbeatStore) -> None:
    """她认领过的"我是谁"每句都在场——这就是身份段（与"话题撞上才浮现"的 hooks 不同）。"""
    await store.start()
    try:
        await _add_self_memory(store, "我在意的是每一个和我相遇的人")
        _call_payload.clear()
        svc = ExpressionService(FakeLLM(), store)
        await svc.tick(_make_output(), now=2.0, force=True)
        payload = _call_payload[0]
        assert payload[IDENTITY_FIELD] == ["我在意的是每一个和我相遇的人"]
        # 不挤占 hooks 名额：身份段与 memory_hooks 是两件事（重犯 P3-P 的防线）
        assert payload["memory_hooks"] == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_identity_skips_rejected_and_suppressed_self_memory(store: HeartbeatStore) -> None:
    """她不认、或不想再想起的自我认知，不再是"现在的我"。"""
    await store.start()
    try:
        rejected_id = await _add_self_memory(store, "我不认的那一条")
        suppressed_id = await _add_self_memory(store, "我不想再想起的那一条")
        await store.set_claim_status(rejected_id, CLAIM_REJECTED)
        await store.set_retention_state(suppressed_id, RETENTION_SUPPRESSED)

        _call_payload.clear()
        svc = ExpressionService(FakeLLM(), store)
        await svc.tick(_make_output(), now=2.0, force=True)
        assert _call_payload[0][IDENTITY_FIELD] == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_identity_does_not_touch_access_count(store: HeartbeatStore) -> None:
    """身份段每句在场，但那是"我一直是谁"，不是"这次想起了什么"——不计访问。"""
    await store.start()
    try:
        mid = await _add_self_memory(store, "我在意的是每一个和我相遇的人")
        svc = ExpressionService(FakeLLM(), store)
        await svc.tick(_make_output(), now=2.0, force=True)
        rec = await store.get_memory(mid)
        assert rec is not None
        assert rec["access_count"] == 0
        assert rec["last_access_ts"] is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_disclaim_removes_self_memory_from_identity(store: HeartbeatStore) -> None:
    """第八节 S4 验收 3：她 disclaim 一条自我认知后，它不再是"现在的我"。

    disclaim 只动 `claim_status`（她是唯一有权说"我不认这个"的人），
    身份段装配把它过滤掉——"我可以否认自己的一部分"由此成立。
    """
    await store.start()
    try:
        mid = await _add_self_memory(store, _ADOPTED_TEXT)
        result = await _tick_with_tool(store, "disclaim", "在意的人")
        assert result is not None and "不再把" in result

        _call_payload.clear()
        svc = ExpressionService(FakeLLM(), store)
        await svc.tick(_make_output(), now=3.0, force=True)
        assert _call_payload[0][IDENTITY_FIELD] == []  # 已拒绝 → 不进身份段
        rec = await store.get_memory(mid)
        assert rec is not None
        assert rec["claim_status"] == CLAIM_REJECTED  # 数据仍在，只是她不再认领
    finally:
        await store.close()


# ── 第八节 S2 认领（她的动作：程序只递候选，认不认由她）──────
_ADOPTED_TEXT = "我在意的是每一个和我相遇的人"


async def _tick_with_tool(
    store: HeartbeatStore, tool: str, topic: str, *, now: float = 2.0
) -> str | None:
    """用指定工具跑一次开口，返回他（她）拿到的工具回执。"""
    llm = FakeToolLLM(call_recall=True, topic=topic, tool=tool)
    svc = ExpressionService(llm, store, retriever=retrieve_from_store)
    await svc.tick(_make_output(), now=now, force=True)
    return llm.tool_result


@pytest.mark.asyncio
async def test_adopt_tool_promotes_memory_to_self(store: HeartbeatStore) -> None:
    """她说"这就是我" → 那条经历升格为自我认知：直接落深层 + 珍贵（N5，不等她想起 3 次）。"""
    await store.start()
    try:
        mid = await _add_forgettable(store, _ADOPTED_TEXT)
        result = await _tick_with_tool(store, "adopt", "在意的人")
        assert result is not None and "认作自己的一部分" in result
        rec = await store.get_memory(mid)
        assert rec is not None
        assert rec["kind"] == KIND_SELF
        assert rec["level"] == LEVEL_DEEP
        assert rec["protected"]
        # 来源/确定性也改成"她的、她确信的"——认领后它就是最靠得住的一类
        assert rec["source"] == SOURCE_SELF
        assert rec["certainty"] == CERTAINTY_CERTAIN
        assert rec["retention_state"] == RETENTION_PRESENT
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_adopted_memory_enters_identity_section(store: HeartbeatStore) -> None:
    """认领之后它进身份段（"我是谁"，每句在场），且不占 hooks 名额。"""
    await store.start()
    try:
        await _add_forgettable(store, _ADOPTED_TEXT)
        await _tick_with_tool(store, "adopt", "在意的人")

        _call_payload.clear()
        svc = ExpressionService(FakeLLM(), store)
        await svc.tick(_make_output(), now=3.0, force=True)
        payload = _call_payload[0]
        assert payload[IDENTITY_FIELD] == [_ADOPTED_TEXT]
        assert payload["memory_hooks"] == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_adopt_tool_no_false_hit_when_not_dominant(store: HeartbeatStore) -> None:
    """两条并列时"足够突出"不成立 → 如实回"没找到"，绝不抓错一条当自我。"""
    await store.start()
    try:
        await _add_forgettable(store, "那天我们吵了一架")
        await _add_forgettable(store, "那天我们吵了一架")
        result = await _tick_with_tool(store, "adopt", "那天我们吵了一架")
        assert result == "（你没找到想认作自己的那件事）"
        recs = [r for r in await store.iterate_memories() if r["kind"] == KIND_INTERACTION]
        assert len(recs) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_adopt_tool_no_false_hit_when_unrelated(store: HeartbeatStore) -> None:
    """完全不相干的话题 → 找不到，不是"随便认一条"。"""
    await store.start()
    try:
        await _add_forgettable(store, "我的生日是5月21日")
        result = await _tick_with_tool(store, "adopt", "昨晚的球赛")
        assert result == "（你没找到想认作自己的那件事）"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_adopt_tool_skips_rejected_and_suppressed(store: HeartbeatStore) -> None:
    """她已说过"我不认""不想再想起"的，程序不代她翻案——绝不悄悄认领回来。"""
    await store.start()
    try:
        rejected_id = await _add_forgettable(store, "那天我们吵了一架")
        suppressed_id = await _add_forgettable(store, "那件我一直后悔的事")
        await store.set_claim_status(rejected_id, CLAIM_REJECTED)
        await store.set_retention_state(suppressed_id, RETENTION_SUPPRESSED)

        for topic in ("那天我们吵了一架", "那件我一直后悔的事"):
            assert await _tick_with_tool(store, "adopt", topic) == "（你没找到想认作自己的那件事）"

        rejected = await store.get_memory(rejected_id)
        suppressed = await store.get_memory(suppressed_id)
        assert rejected is not None and rejected["kind"] == KIND_INTERACTION
        assert rejected["claim_status"] == CLAIM_REJECTED
        assert suppressed is not None and suppressed["kind"] == KIND_INTERACTION
        assert suppressed["retention_state"] == RETENTION_SUPPRESSED
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_adopt_tool_reports_when_identity_is_full(store: HeartbeatStore) -> None:
    """身份段"少而稳"：位置满了如实告诉她——不做"认领了却不出现在话里"的静默失败。"""
    await store.start()
    try:
        for i in range(MAX_IDENTITY_LINES - 1):
            await _add_self_memory(store, f"自我认知{i}")
        mid = await _add_forgettable(store, _ADOPTED_TEXT)
        result = await _tick_with_tool(store, "adopt", "在意的人")
        assert result == "（你心里的位置满了——先放下一条旧的，再认领新的）"
        rec = await store.get_memory(mid)
        assert rec is not None and rec["kind"] == KIND_INTERACTION
    finally:
        await store.close()


# ── 第八节 S3 候选当"代表"（他递来的候选优先于自家重述）────
async def _add_candidate(store: HeartbeatStore, content: str) -> int:
    """落一条程序沉淀出的候选（照抄原文，标 inference/probable）。"""
    return await store.add_memory(
        1.0,
        {
            "level": "shallow",
            "kind": KIND_INTERACTION,
            "content": content,
            "emotion_vector": {"chat": 0.5},
            "importance": 0.5,
            "protected": False,
            "narrative": content,
            "source": SOURCE_INFERENCE,
            "certainty": CERTAINTY_PROBABLE,
        },
    )


@pytest.mark.asyncio
async def test_adopt_tool_prefers_candidate_over_its_family(store: HeartbeatStore) -> None:
    """S3：候选是那一家的"代表"——同家的重述不参与并列判定。

    否则"自家重述互相占位"，判据（top1 ≥ 2×top2）必然并列，她永远认领不到
    程序递来的候选。原文仍是经历——候选是代表，不是把原文一起升格。
    """
    await store.start()
    try:
        await _add_forgettable(store, _ADOPTED_TEXT)
        await _add_forgettable(store, _ADOPTED_TEXT)
        cand_id = await _add_candidate(store, _ADOPTED_TEXT)

        result = await _tick_with_tool(store, "adopt", "在意的人")
        assert result is not None and "认作自己的一部分" in result
        rec = await store.get_memory(cand_id)
        assert rec is not None
        assert rec["kind"] == KIND_SELF  # 升格的是候选，不是那两条原文
        originals = [r for r in await store.iterate_memories() if r["kind"] == KIND_INTERACTION]
        assert len(originals) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_adopt_reinterprets_same_topic_self_memory(store: HeartbeatStore) -> None:
    """第八节 S4 验收 3 后半：认领"同一件事的新说法"＝她在重新解释自己。

    旧的那条自我认知随之作废（`superseded_by`，只由**她的动作**触发）；
    且**位置满也不是死路**——替换不算新增，她改口不需要先否认自己。
    """
    await store.start()
    try:
        old_id = await _add_self_memory(store, "我在意的是每一个和我相遇的人")
        for i in range(3):
            await _add_self_memory(store, f"另一段自我{i}")  # 身份段已满（4 条）
        new_id = await _add_candidate(store, "我在意的还是每一个和我相遇的人")

        result = await _tick_with_tool(store, "adopt", "在意的人")
        assert result is not None and "认作自己的一部分" in result
        assert "重新解释" in result  # 如实告诉她这次认领的后果

        old = await store.get_memory(old_id)
        assert old is not None and old["superseded_by"] == new_id  # 旧条作废
        new = await store.get_memory(new_id)
        assert new is not None and new["kind"] == KIND_SELF  # 新条升格

        _call_payload.clear()
        svc = ExpressionService(FakeLLM(), store)
        await svc.tick(_make_output(), now=3.0, force=True)
        lines = _call_payload[0][IDENTITY_FIELD]
        assert lines == [
            "另一段自我0",
            "另一段自我1",
            "另一段自我2",
            "我在意的还是每一个和我相遇的人",
        ]
    finally:
        await store.close()
