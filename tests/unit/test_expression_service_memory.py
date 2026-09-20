"""P3 运行时接线单测：user_message 打字对话 + 记忆写入 + 检索注入。

覆盖三类接线（P3-G）：
1. user_message → 表达指令 payload（T2 话题非指令）
2. 表达完成 → 她的发言写入 memories（经历）
3. retriever 回调 → memory_hooks 注入
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.memory.levels import KIND_EXPRESSION, KIND_INTERACTION
from elysia.memory.retrieve import retrieve_from_store
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService

_call_payload: list[dict[str, Any]] = []


class FakeLLM:
    """记录最后一次收到的 payload，返回固定文本。"""

    async def speak(self, payload: dict[str, Any]) -> Any:
        _call_payload.append(payload)
        from types import SimpleNamespace

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
async def test_retriever_injects_hooks(store: HeartbeatStore) -> None:
    await store.start()
    try:
        # 先写入一条记忆，检索时应能注入 hooks
        await store.add_memory(
            1.0,
            {
                "level": "deep",
                "kind": KIND_INTERACTION,
                "content": "你上次说喜欢晚霞",
                "emotion_vector": {"chat": 0.9, "miss": 0.4},
                "importance": 0.9,
                "protected": True,
                "narrative": "你上次说喜欢晚霞",
            },
        )
        _call_payload.clear()
        svc = ExpressionService(
            FakeLLM(),
            store,
            retriever=retrieve_from_store,
        )
        await svc.tick(_make_output(), now=2.0, force=True)
        payload = _call_payload[0]
        assert payload.get("memory_hooks") == ["你上次说喜欢晚霞"]
    finally:
        await store.close()
