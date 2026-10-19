"""S1c：开场去重——`recent_openings` + system 指令（治"句句夜深了"）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dialogue import (
    OPENING_CHARS,
    RECENT_OPENINGS,
    recent_openings,
    record_her_bubble,
    record_user_message,
)
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import ExpressionJob

T0 = 1_000_000.0


class _CaptureLLM:
    def __init__(self, text: str = "我在呢。") -> None:
        self.text = text
        self.payloads: list[dict[str, Any]] = []

    async def speak(self, payload: dict[str, Any]) -> Any:
        self.payloads.append(dict(payload))
        return SimpleNamespace(
            text=self.text,
            level="main",
            validated=ValidationResult(ok=True, reason=None, sanitized=None),
        )


def _job() -> ExpressionJob:
    return ExpressionJob(
        job_id="job-1",
        created_ts=T0,
        heartbeat_seq=1,
        output=BrainOutput(
            desire=DesireState(tr=45.0, cs=62.0, sa=20.0),
            feelings=FeelingState(chat=0.6, miss=0.4, explore=0.4),
            will=WillOutput(thought_style=0.1),
        ),
        summary={"day_phase": "night"},
        intent="发呆呓语",
    )


# ── 最近 8 条开头 ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_recent_openings_last_8_in_order(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        for i in range(10):
            await record_user_message(store, T0 + i * 2, f"用户第{i}条")
            await record_her_bubble(store, T0 + i * 2 + 1, f"第{i}条开场白内容", emotion_vector={})
        openings = await recent_openings(store)
        assert len(openings) == RECENT_OPENINGS == 8
        assert len(openings[0]) <= OPENING_CHARS
        # 只留最近 8 条（编号 2..9），按时间升序
        assert openings[0].startswith("第2条")
        assert openings[-1].startswith("第9条")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_recent_openings_fewer_than_limit(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await record_her_bubble(store, T0, "夜深了，我一个人")
        await record_her_bubble(store, T0 + 1, "其实我想说")
        assert await recent_openings(store) == ["夜深了，我一", "其实我想说"]
    finally:
        await store.close()


# ── 表达指令 ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_job_payload_carries_recent_openings(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await record_her_bubble(store, T0 - 10, "夜深了，我还醒着")
        await record_her_bubble(store, T0 - 5, "刚才看到一片云")
        llm = _CaptureLLM()
        service = ExpressionService(llm, store)  # type: ignore[arg-type]
        await service.run_job(_job())
        payload = llm.payloads[0]
        assert payload["recent_openings"] == ["夜深了，我还", "刚才看到一片"]
        assert payload["state_brief"]["day_phase"] == "night", "时间意识保留（只作背景）"
    finally:
        await store.close()


def test_deepseek_prompt_has_opening_rules_and_user_carries_openings() -> None:
    from elysia.llm.deepseek import DeepSeekBackend

    backend = DeepSeekBackend("test-key", require_key=False)
    payload = {
        "intent": "发呆呓语",
        "emotion_vector": {"chat": 0.6},
        "state_brief": {"day_phase": "night"},
        "recent_openings": ["夜深了，我", "夜里很安静"],
        "constraints": {"max_chars": 120},
        "lexical_permits": [],
    }
    messages = backend._messages(payload)
    system = messages[0]["content"]
    user = __import__("json").loads(messages[1]["content"])
    assert user["recent_openings"] == payload["recent_openings"]
    assert "recent_openings" in system
    assert "夜深了" in system and "开场白" in system
    assert "背景" in system
    # 数据只进 user 段，不进 system 指令区
    assert "夜深了，我" not in system
