"""T-022b：她的"时间片"——为不聊天而存在的窗口。

新增开口原因 `unfinished`：存在未完成之事（对应记忆未 superseded）且当前沉默
≥20 分钟、且该承诺距上次尝试 ≥2 小时、且没有待处理用户消息 ⇒ 允许为她自己的事
开口一次。**不设每日次数配额**（只靠两个时间条件防节拍器）。

验收（含取消测试）：
1. 条件满足 ⇒ 一次 `speech_cause='unfinished'`（`unfinished_due` 为真 +
   `SpeechTrigger` 返回该原因）；
2. 条件不满足（无未完成事 / 沉默不够 / 冷却内）⇒ 不出现；
3. 取消测试：删条件 ⇒ 红；
4. 源码里没有任何 DAILY/QUOTA 常量。
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import (
    ExpressionService,
)
from elysia.soul.expression_types import ExpressionJob
from elysia.soul.promises import record_promise_memory
from elysia.soul.speech_trigger import (
    ALLOWED_CAUSES,
    CAUSE_UNFINISHED,
    SpeechTrigger,
)
from elysia.soul.tools.writing import WritingWorkspace

T0 = 1_000_000.0
SRC = Path(__file__).resolve().parents[2] / "src" / "elysia"


class _FakeLLM:
    def __init__(self, text: str = "嗯。") -> None:
        self.text = text

    async def speak(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            text=self.text,
            level="main",
            validated=ValidationResult(ok=True, reason=None),
        )


def _job() -> ExpressionJob:
    return ExpressionJob(
        job_id="job-t022b",
        created_ts=T0,
        heartbeat_seq=1,
        output=BrainOutput(
            desire=DesireState(),
            feelings=FeelingState(chat=0.6, miss=0.4),
            will=WillOutput(thought_style=0.1),
        ),
        summary={},
        intent="回应",
    )


async def _service(tmp_path: Path) -> tuple[ExpressionService, HeartbeatStore]:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    service = ExpressionService(
        _FakeLLM(), store, writing_workspace=WritingWorkspace(tmp_path / "home")
    )
    return service, store


async def _pending_promise(store: HeartbeatStore, *, age_s: float = 3 * 3600) -> int:
    pid = int(
        await store.add_intention(
            T0 - age_s, goal="爱莉希雅那篇", action="write_note", target=None, kind="promise"
        )
    )
    await record_promise_memory(store, pid, "爱莉希雅那篇", T0 - age_s)
    return pid


# ── 1. 条件满足 ⇒ 该开口 ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_unfinished_due_when_all_conditions_met(tmp_path: Path) -> None:
    """有未完成之事 + 沉默 ≥20 分钟 + 距上次尝试 ≥2 小时 ⇒ 该开口一次。

    取消测试：删掉 `unfinished_due` 里的沉默判据（或承诺间隔判据）⇒ 本用例红。
    """
    service, store = await _service(tmp_path)
    try:
        await _pending_promise(store)
        await store.think(T0 - 25 * 60, "expression", "嗯。")
        assert await service.unfinished_due(T0) is True
        # 防连发：同一时刻再问一次 ⇒ 不再触发（时间片重新计时）
        assert await service.unfinished_due(T0) is False
    finally:
        await store.close()


# ── 2a. 没有未完成之事 ⇒ 不开口 ─────────────────────────────────────
@pytest.mark.asyncio
async def test_unfinished_not_due_without_pending(tmp_path: Path) -> None:
    """没有未完成之事 ⇒ 永不开口。"""
    service, store = await _service(tmp_path)
    try:
        await store.think(T0 - 25 * 60, "expression", "嗯。")
        assert await service.unfinished_due(T0) is False
    finally:
        await store.close()


# ── 2b. 沉默不够 ⇒ 不开口 ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_unfinished_not_due_when_silence_short(tmp_path: Path) -> None:
    """刚说过话（沉默 <20 分钟）⇒ 不为未完成之事开口。

    取消测试：把 `UNFINISHED_SILENCE_S` 判据删掉 ⇒ 本用例红。
    """
    service, store = await _service(tmp_path)
    try:
        await _pending_promise(store)
        await store.think(T0 - 5 * 60, "expression", "嗯。")
        assert await service.unfinished_due(T0) is False
    finally:
        await store.close()


# ── 2c. 承诺距上次尝试 <2h ⇒ 不开口 ────────────────────────────────
@pytest.mark.asyncio
async def test_unfinished_not_due_inside_attempt_gap(tmp_path: Path) -> None:
    """该承诺 1 小时前刚试过（<2h）⇒ 不开口。

    取消测试：删掉 `UNFINISHED_ATTEMPT_GAP_S` 判据 ⇒ 本用例红。
    """
    service, store = await _service(tmp_path)
    try:
        pid = await _pending_promise(store)
        await store.record_intention_attempt(pid, T0 - 3600, "第二拍未成文")
        await store.think(T0 - 25 * 60, "expression", "嗯。")
        assert await service.unfinished_due(T0) is False
    finally:
        await store.close()


# ── 3. SpeechTrigger 认这个原因 ─────────────────────────────────────
def test_speech_trigger_returns_unfinished_cause() -> None:
    """`unfinished=True` 且无更高优先事件 ⇒ 返回 `speech_cause='unfinished'`。

    取消测试：删掉 `SpeechTrigger.observe` 里的 `elif unfinished` 分支 ⇒ 本用例红。
    """
    trigger = SpeechTrigger()
    cause = trigger.observe({"chat": 0.5, "miss": 0.3, "rest": 0.1}, T0, unfinished=True)
    assert cause == CAUSE_UNFINISHED
    assert CAUSE_UNFINISHED in ALLOWED_CAUSES


# ── 4. 不设每日次数配额（源码级断言）────────────────────────────────
def test_no_daily_or_quota_constants() -> None:
    """T-022b 铁律：不设"每日次数/配额"上限（只靠时间条件防节拍器）。"""
    pattern = re.compile(r"\b[A-Z0-9_]*(?:DAILY|QUOTA)[A-Z0-9_]*\s*=")
    for name in (
        "soul/promises.py",
        "soul/expression_service.py",
        "soul/speech_trigger.py",
        "soul/heartbeat.py",
    ):
        text = (SRC / name).read_text(encoding="utf-8")
        assert not pattern.search(text), f"{name} 里出现了 DAILY/QUOTA 常量"
