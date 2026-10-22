"""T-033：说话规则融合第③步——her own words（只减重复，阈值一个不动）。

把 T-022a 的 `unfinished_things` 与兜底提醒 `promise_reminder` 合成**一个** payload
字段 `own_words`：同一承诺一轮只出现一次，≤3 条、去重、一种句式；到期（承诺 >24h 或
之后她已开口 ≥2 次）那条只是把句式换成「我想起来：…」，不再额外加第二段。

取消测试：把 payload 改回 `unfinished_things` + `promise_reminder` 双字段 ⇒
`test_payload_has_only_own_words` 必红；把到期句式去掉 ⇒ 到期用例必红。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.deepseek import OWN_WORDS_HEADER, DeepSeekBackend, render_own_words_block
from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import ExpressionJob
from elysia.soul.promises import (
    OWN_WORDS_FIELD,
    own_words,
    own_words_line,
)

T0 = 1_000_000.0


class _FakeLLM:
    def __init__(self, text: str = "嗯。") -> None:
        self.text = text

    async def speak(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            text=self.text,
            level="main",
            validated=ValidationResult(ok=True, reason=None),
        )


def _job(user_message: str | None = None) -> ExpressionJob:
    return ExpressionJob(
        job_id="job-1",
        created_ts=T0,
        heartbeat_seq=1,
        output=BrainOutput(
            desire=DesireState(),
            feelings=FeelingState(chat=0.6, miss=0.4),
            will=WillOutput(thought_style=0.1),
        ),
        summary={},
        user_message=user_message,
        intent="回应",
    )


async def _promise(store: HeartbeatStore, goal: str, created: float) -> int:
    return int(
        await store.add_intention(
            created, goal=goal, action="write_note", target=None, kind="promise"
        )
    )


def test_own_words_line_shapes() -> None:
    assert own_words_line("爱莉希雅那篇") == "我说过要写「爱莉希雅那篇」，还没写。"
    assert (
        own_words_line("爱莉希雅那篇", due=True) == "我想起来：我说过要写「爱莉希雅那篇」，还没写。"
    )
    # 每条 ≤120 字：超长 goal 截断，不把一句提醒撑爆
    assert len(own_words_line("字" * 500)) <= 20 + 120


@pytest.mark.asyncio
async def test_own_words_empty_without_promises(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        assert await own_words(store, T0) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_own_words_not_due_is_plain_sentence(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await _promise(store, "爱莉希雅那篇", T0 - 100)
        words = await own_words(store, T0)
        assert words == ["我说过要写「爱莉希雅那篇」，还没写。"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_own_words_due_replaces_sentence_not_adds_one(tmp_path: Path) -> None:
    """>24h ⇒ 仍是**一条**，只是句式变「我想起来：…」；12h 冷却内回到普通句式。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await _promise(store, "爱莉希雅那篇", T0 - 25 * 3600)
        words = await own_words(store, T0)
        assert len(words) == 1, f"到期也只许一条（不许再加第二段）：{words}"
        assert words[0] == "我想起来：我说过要写「爱莉希雅那篇」，还没写。"

        # 同一承诺 12h 冷却内：不再提醒，回到普通句式（仍只有一条）
        again = await own_words(store, T0 + 60)
        assert len(again) == 1
        assert not again[0].startswith("我想起来：")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_own_words_due_by_two_openings(tmp_path: Path) -> None:
    """承诺之后她已开口 ≥2 次仍未动 ⇒ 不等 24h 也换「我想起来：…」。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await _promise(store, "爱莉希雅那篇", T0 - 100)
        await store.think(T0 - 50, "expression", "嗯。")
        await store.think(T0 - 40, "expression", "好。")
        words = await own_words(store, T0)
        assert len(words) == 1
        assert words[0].startswith("我想起来：")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_own_words_dedupes_same_goal(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await _promise(store, "同一篇", T0 - 100)
        await _promise(store, "同一篇", T0 - 99)
        await _promise(store, "另一篇", T0 - 98)
        words = await own_words(store, T0)
        assert len(words) == 2, f"同一 goal 一轮只出现一次：{words}"
        assert sum("同一篇" in word for word in words) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_own_words_truncates_to_three(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        for index in range(4):
            await _promise(store, f"第{index}篇", T0 - 100 + index)
        words = await own_words(store, T0)
        assert len(words) == 3, f"最多 3 条：{words}"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_payload_has_only_own_words(tmp_path: Path) -> None:
    """全量替换：payload 里没有 `unfinished_things` / `promise_reminder` 两个键。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await _promise(store, "爱莉希雅那篇", T0 - 100)
        service = ExpressionService(_FakeLLM(), store)  # type: ignore[arg-type]
        payload = (await service.run_job(_job("在吗"))).instruction
        assert OWN_WORDS_FIELD in payload
        for old in (
            "pending_promises",
            "pending_promises_hint",
            "unfinished_things",
            "promise_reminder",
        ):
            assert old not in payload, f"旧字段 {old} 仍在 payload 里"
        assert len(payload[OWN_WORDS_FIELD]) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_payload_own_words_empty_without_debt(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        service = ExpressionService(_FakeLLM(), store)  # type: ignore[arg-type]
        payload = (await service.run_job(_job("在吗"))).instruction
        assert payload.get(OWN_WORDS_FIELD) == []
    finally:
        await store.close()


def test_prompt_renders_own_words_as_one_block() -> None:
    """deepseek 的 prompt 渲染：一个块、逐行；空 ⇒ 不出现该块，也不再出现旧键名。"""
    assert render_own_words_block([]) == ""
    assert render_own_words_block(None) == ""
    block = render_own_words_block(
        ["我说过要写「甲」，还没写。", "我想起来：我说过要写「乙」，还没写。"]
    )
    lines = block.splitlines()
    assert lines[0] == OWN_WORDS_HEADER
    assert lines[1] == "- 我说过要写「甲」，还没写。"
    assert lines[2].startswith("- 我想起来：")

    backend = DeepSeekBackend.__new__(DeepSeekBackend)
    messages = backend._messages(
        {OWN_WORDS_FIELD: ["我说过要写「甲」，还没写。"], "intent": "回应"}
    )
    user = messages[1]["content"]
    system = messages[0]["content"]
    assert OWN_WORDS_HEADER in user
    assert "我说过要写「甲」，还没写。" in user
    assert OWN_WORDS_FIELD not in user
    assert OWN_WORDS_FIELD not in system

    empty = backend._messages({OWN_WORDS_FIELD: [], "intent": "回应"})
    assert OWN_WORDS_HEADER not in empty[1]["content"]
    assert OWN_WORDS_FIELD not in empty[1]["content"]
