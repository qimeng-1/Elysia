"""S1a：对话上下文（`dialogue_context`）——她"接得上话"的物理基础。

真机病根（设计纲 §0）：指令里只有最后一条 `user_message`，所以她说「一定」时
不知道它在指向什么。S1a 把最近 20 条真实轮次按时间顺序注入表达指令。

取消测试：把 `expression_service.run_job` 里装配 `dialogue_context` 的那几行拿掉 ⇒
`test_new_user_message_is_in_current_payload` 与
`test_two_round_continuity_payload_contains_previous_round` 必红（失败信息会打出 payload）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dialogue import (
    CONTEXT_MAX_CHARS,
    CONTEXT_TEXT_CHARS,
    MAX_CONTEXT_TURNS,
    build_dialogue_context,
    record_her_bubble,
    record_user_message,
)
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import ExpressionJob

T0 = 1_000_000.0


class _CaptureLLM:
    """假主声：记录每次 payload，返回固定合规文本（不联网）。"""

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


def _output() -> BrainOutput:
    return BrainOutput(
        desire=DesireState(tr=45.0, cs=62.0, sa=20.0),
        feelings=FeelingState(
            chat=0.62, miss=0.38, explore=0.46, curiosity=0.0, rest=0.05, self_check=0.2
        ),
        will=WillOutput(direction="reach", strength=0.55, thought_style=0.1, anim_bias=0.0),
        action="none",
    )


def _job(seq: int = 1, *, user_message: str | None = None) -> ExpressionJob:
    return ExpressionJob(
        job_id=f"job-{seq}",
        created_ts=T0 + seq,
        heartbeat_seq=seq,
        output=_output(),
        summary={"body_away_s": 0.0, "day_phase": "day"},
        user_message=user_message,
        intent="回应",
    )


async def _seed_round(store: HeartbeatStore, now: float, user: str, her: str) -> None:
    await record_user_message(store, now, user)
    await record_her_bubble(
        store, now + 1.0, her, emotion_vector={"miss": 0.33, "chat": 0.62}, intent="回应"
    )


# ── 表与读写 ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_dialogue_turns_roundtrip(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        assert await store.max_exchange_id() == 0
        await store.add_dialogue_turn(T0, "user", "第一句", 1)
        await store.add_dialogue_turn(
            T0 + 1, "her", "她的话", 1, emotion_vector={"miss": 0.4}, intent="回应"
        )
        turns = await store.dialogue_turns()
        assert [t["role"] for t in turns] == ["user", "her"]
        assert [t["exchange_id"] for t in turns] == [1, 1]
        assert turns[1]["emotion_vector"] == {"miss": 0.4}
        assert turns[1]["intent"] == "回应"
        assert await store.max_exchange_id() == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_exchange_boundary_one_user_three_bubbles(tmp_path: Path) -> None:
    """一条用户消息 + 她三句气泡 ⇒ 同一 exchange_id；下一条用户消息 ⇒ 新 id。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        first = await record_user_message(store, T0, "我们去吃饭吧")
        for i in range(3):
            await record_her_bubble(store, T0 + 1 + i, f"第{i + 1}句")
        second = await record_user_message(store, T0 + 10, "好呀")
        await record_her_bubble(store, T0 + 11, "那我们走")
        assert first == 1 and second == 2
        turns = await store.dialogue_turns()
        assert [(t["role"], t["exchange_id"]) for t in turns] == [
            ("user", 1),
            ("her", 1),
            ("her", 1),
            ("her", 1),
            ("user", 2),
            ("her", 2),
        ]
    finally:
        await store.close()


# ── 上下文预算 ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_context_order_roles_and_truncation(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await store.add_dialogue_turn(T0, "user", "你好呀", 1)
        await store.add_dialogue_turn(T0 + 10, "her", "在呢" * 500, 1, intent="回应")
        await store.add_dialogue_turn(T0 + 20, "user", "那就好", 2)
        context = await build_dialogue_context(store, now=T0 + 100)
        assert [item["role"] for item in context] == ["user", "her", "user"]
        assert context[0]["text"] == "你好呀"
        assert len(context[1]["text"]) == CONTEXT_TEXT_CHARS
        ages = [item["age_s"] for item in context]
        assert ages == sorted(ages, reverse=True), "时间升序排列 ⇒ age_s 应递减（最旧的最大）"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_context_keeps_latest_20_turns(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        for i in range(25):
            role = "user" if i % 2 == 0 else "her"
            await store.add_dialogue_turn(T0 + i, role, f"第{i}条", i // 2 + 1)
        context = await build_dialogue_context(store, now=T0 + 1000)
        assert len(context) == MAX_CONTEXT_TURNS == 20
        assert context[0]["text"] == "第5条"
        assert context[-1]["text"] == "第24条"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_context_total_length_drops_oldest_not_latest(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        # 20 条 × 200 字 = 4000 字 > 2000 上限 ⇒ 从最旧丢，最近一条永远在
        for i in range(20):
            await store.add_dialogue_turn(
                T0 + i, "her", f"{i:02d}" + "字" * (CONTEXT_TEXT_CHARS - 2), i
            )
        context = await build_dialogue_context(store, now=T0 + 1000)
        assert sum(len(item["text"]) for item in context) <= CONTEXT_MAX_CHARS
        assert context[-1]["text"].startswith("19")
        assert len(context) <= 10, "2000 字上限下不应还留着 20 条 200 字的长轮次"
    finally:
        await store.close()


# ── 表达指令注入 ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_job_payload_carries_dialogue_context(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await _seed_round(store, T0, "我去改代码了", "好，我等你")
        llm = _CaptureLLM()
        service = ExpressionService(llm, store)  # type: ignore[arg-type]
        await service.run_job(_job(1, user_message="改完了"))
        payload = llm.payloads[0]
        context = payload.get("dialogue_context")
        assert context, "payload 必须带 dialogue_context"
        assert [item["role"] for item in context] == ["user", "her"]
        assert context[0]["text"] == "我去改代码了"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_new_user_message_is_in_current_payload(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await _seed_round(store, T0, "上一轮的话", "上一轮的回答")
        await record_user_message(store, T0 + 20, "这一轮的新消息")
        llm = _CaptureLLM()
        service = ExpressionService(llm, store)  # type: ignore[arg-type]
        await service.run_job(_job(2, user_message="这一轮的新消息"))
        context = llm.payloads[0].get("dialogue_context") or []
        assert context and context[-1]["text"] == "这一轮的新消息", (
            f"她那次的上下文里必须包含这条新用户消息，实际：{context}"
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_two_round_continuity_payload_contains_previous_round(tmp_path: Path) -> None:
    """「我去改代码了」→「改完了」两轮：第二次的 payload 必须还能看到第一轮
    （这就是她说「一定」时接不上的反面）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await _seed_round(store, T0, "我去改代码了", "好，我等你")
        await _seed_round(store, T0 + 100, "改完了", "辛苦啦")
        llm = _CaptureLLM()
        service = ExpressionService(llm, store)  # type: ignore[arg-type]
        await service.run_job(_job(3, user_message="改完了"))
        context = llm.payloads[0].get("dialogue_context") or []
        texts = [item["text"] for item in context]
        assert "我去改代码了" in texts, f"第二轮 payload 里丢了第一轮：{texts}"
        assert "改完了" in texts
    finally:
        await store.close()


# ── 数据只进 user 段、不进 system 指令区 ─────────────────────
def test_deepseek_user_section_carries_context_and_system_has_instruction() -> None:
    from elysia.llm.deepseek import DeepSeekBackend

    backend = DeepSeekBackend("test-key", require_key=False)
    payload = {
        "intent": "回应",
        "emotion_vector": {"chat": 0.6},
        "dialogue_context": [
            {"role": "user", "text": "我去改代码了", "age_s": 120.0},
            {"role": "her", "text": "好，我等你", "age_s": 118.0},
        ],
        "constraints": {"max_chars": 120},
        "lexical_permits": [],
    }
    messages = backend._messages(payload)
    system = messages[0]["content"]
    user = json.loads(messages[1]["content"])
    assert user["dialogue_context"] == payload["dialogue_context"]
    assert "dialogue_context" in system and "接得上" in system
    assert "不要把它原样复述" in system
    # T2：对话数据只作为 user 段资料，不混进 system 的指令区
    assert "我去改代码了" not in system


# ── 接线：心跳真的把 kv['interaction'] 写成用户轮次 ──────────────
@pytest.mark.asyncio
async def test_heartbeat_records_user_turn_from_interaction_kv(tmp_path: Path) -> None:
    from elysia.core.clock import SimulatedClock
    from elysia.core.mode import ModeManager
    from elysia.core.state_store import StateStore
    from elysia.core.timesense import TimeSense
    from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

    state_store = StateStore(tmp_path / "state.db")
    heartbeat_store = HeartbeatStore(tmp_path / "heartbeat.db")
    await state_store.start()
    await heartbeat_store.start()
    clock = SimulatedClock(start_ts=T0, speed=1.0)
    soul = SoulHeartbeat(
        state_store=state_store,
        heartbeat_store=heartbeat_store,
        timesense=TimeSense(make_soul_state(T0), clock),
        mode_mgr=ModeManager(clock=clock),
        clock=clock,
    )
    try:
        await state_store.save_json("interaction", {"ts": T0 + 5, "text": "你在吗"})
        await soul._sync_body_status(T0 + 10)
        turns = await heartbeat_store.dialogue_turns()
        assert [(t["role"], t["text"]) for t in turns] == [("user", "你在吗")]
        assert turns[0]["exchange_id"] == 1
    finally:
        await state_store.close()
        await heartbeat_store.close()


# ── 写失败不得影响她说话 ─────────────────────────────────────
@pytest.mark.asyncio
async def test_write_failure_does_not_break_speaking(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:

        async def _boom(*_args: Any, **_kwargs: Any) -> int:
            raise RuntimeError("库写坏了")

        store.add_dialogue_turn = _boom  # type: ignore[method-assign]
        assert await record_user_message(store, T0, "你好") is None  # 吞掉异常
        await record_her_bubble(store, T0 + 1, "我在")  # 不抛

        llm = _CaptureLLM()
        service = ExpressionService(llm, store)  # type: ignore[arg-type]
        result = await service.run_job(_job(9, user_message="你好"))
        assert result.text == "我在呢。", "对话轮次写失败绝不能让她说不出话"
        assert llm.payloads[0]["dialogue_context"] == []
    finally:
        await store.close()
