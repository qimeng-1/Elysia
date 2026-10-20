"""P4：事件驱动节奏——不再用"经过时间"当触发器（静默不设上限）。

用户纠正（2026-10-20 22:3x）：触发只来自离散原因；
没有事件 ⇒ 她可以几小时/一整天不说话——这不是异常，是生活。
`rhythm_interval_s` 只作**事件后的响应延迟**，不决定"何时轮到说"。

取消测试：
- 把"经过时间 ⇒ 开口"加回调度器 ⇒ `test_no_events_means_silence_even_after_a_day` 红；
- 允许开口时 cause 为 none ⇒ `test_every_speech_has_a_traceable_cause` 红。
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
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import ExpressionJob
from elysia.soul.speech_trigger import (
    ALLOWED_CAUSES,
    CAUSE_DISCOVERY,
    CAUSE_NONE,
    CAUSE_PRESENCE,
    CAUSE_RESPONSE,
    CAUSE_THRESHOLD,
    CAUSE_WRITE,
    THRESHOLD_BANDS,
    SpeechTrigger,
)

T0 = 1_000_000.0


def _feel(**overrides: float) -> dict[str, float]:
    base = {"chat": 0.50, "miss": 0.20, "rest": 0.05}
    base.update(overrides)
    return base


def _output(**overrides: float) -> BrainOutput:
    return BrainOutput(
        desire=DesireState(),
        feelings=FeelingState(**_feel(**overrides)),
        will=WillOutput(thought_style=0.0),
    )


class _FakeLLM:
    async def speak(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            text="嗯。", level="main", validated=ValidationResult(ok=True, reason=None)
        )


# ── 没有事件 ⇒ 永远不说话（时间不是触发器）──────────────────
def test_no_events_means_silence_even_after_a_day() -> None:
    trigger = SpeechTrigger()
    feelings = _feel()
    now = T0
    for second in range(0, 86400, 60):  # 整整一天，每分钟一拍
        cause = trigger.observe(feelings, now + second, delay_s=0.0)
        assert cause is None, f"没有事件却开口了（第 {second}s，cause={cause}）"
    assert trigger.pending_cause is None


# ── 阈值跨越（迟滞，防抖）──────────────────────────────────
def test_threshold_crossing_with_hysteresis() -> None:
    trigger = SpeechTrigger()
    low, high = THRESHOLD_BANDS["miss"]
    # 先建立基线（低侧）
    assert trigger.observe(_feel(miss=low - 0.05), T0) is None
    # 跨上高侧 ⇒ 一次 threshold
    cause = trigger.observe(_feel(miss=high + 0.01), T0 + 10)
    assert cause == CAUSE_THRESHOLD
    # 继续在高侧抖动 ⇒ 不再触发（迟滞）
    assert trigger.observe(_feel(miss=high + 0.02), T0 + 20) is None
    assert trigger.observe(_feel(miss=high - 0.01), T0 + 30) is None
    # 从高侧跨回低侧 ⇒ 再一次 threshold
    cause = trigger.observe(_feel(miss=low - 0.01), T0 + 40)
    assert cause == CAUSE_THRESHOLD


def test_threshold_event_respects_response_delay() -> None:
    trigger = SpeechTrigger()
    low, high = THRESHOLD_BANDS["miss"]
    trigger.observe(_feel(miss=low - 0.05), T0)
    assert trigger.observe(_feel(miss=high + 0.01), T0 + 1, delay_s=300.0) is None
    assert trigger.pending_cause == CAUSE_THRESHOLD
    assert trigger.observe(_feel(miss=high + 0.01), T0 + 100) is None
    assert trigger.observe(_feel(miss=high + 0.01), T0 + 301) == CAUSE_THRESHOLD


# ── 事件类型 ──────────────────────────────────────────────
def test_discovery_and_write_events() -> None:
    trigger = SpeechTrigger()
    assert trigger.observe(_feel(), T0, discovery=True, delay_s=0.0) == CAUSE_DISCOVERY
    trigger.reset()
    assert trigger.observe(_feel(), T0, wrote=True, delay_s=0.0) == CAUSE_WRITE


def test_force_and_greeting_are_immediate() -> None:
    trigger = SpeechTrigger()
    assert trigger.observe(_feel(), T0, force=True) == CAUSE_RESPONSE
    trigger.reset()
    assert trigger.observe(_feel(), T0, greeting=True) == CAUSE_PRESENCE


def test_dialogue_active_suppresses_internal_but_not_force() -> None:
    trigger = SpeechTrigger()
    assert trigger.observe(_feel(), T0, discovery=True, dialogue_active=True) is None
    assert trigger.pending_cause is None
    assert trigger.observe(_feel(), T0 + 1, force=True, dialogue_active=True) == CAUSE_RESPONSE


# ── 可追溯：每次开口都有原因，永不 none ────────────────────
def test_every_speech_has_a_traceable_cause() -> None:
    trigger = SpeechTrigger()
    low, high = THRESHOLD_BANDS["miss"]
    trigger.observe(_feel(miss=low - 0.05), T0)
    causes = []
    for beat in range(2000):
        cause = trigger.observe(
            _feel(miss=high + 0.01 if beat % 2 else low - 0.01),
            T0 + beat,
            discovery=(beat == 500),
            delay_s=0.0,
        )
        if cause is not None:
            causes.append(cause)
    assert causes, "回放里一次都没开口（尺子失效）"
    assert all(cause in ALLOWED_CAUSES for cause in causes)
    assert all(cause != CAUSE_NONE for cause in causes)


# ── 运行逻辑里不许出现"每天/每小时做几件"的配额常量──────────
def test_no_quota_constants_in_runtime_code() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "elysia" / "soul"
    pattern = re.compile(r"\b(DAILY|QUOTA|PER_DAY|MAX_PER)\b")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            offenders.append(f"{path.name}:{match.group(0)}")
    assert offenders == [], f"运行代码里出现了行为配额：{offenders}"


# ── speech_cause 真的落进 expression_log ───────────────────
@pytest.mark.asyncio
async def test_speech_cause_is_persisted(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        service = ExpressionService(_FakeLLM(), store)  # type: ignore[arg-type]
        job = ExpressionJob(
            job_id="job-1",
            created_ts=T0,
            heartbeat_seq=1,
            output=_output(),
            summary={"day_phase": "night"},
            intent="思念",
            speech_cause=CAUSE_DISCOVERY,
        )
        result = await service.run_job(job)
        assert result.speech_cause == CAUSE_DISCOVERY
        await service.persist(result, now=T0 + 1)
        rows = await store.execute_raw(
            "SELECT validation FROM expression_log ORDER BY id DESC LIMIT 1"
        )
        assert rows
        import json

        validation = json.loads(rows[0][0])
        assert validation["speech_cause"] == CAUSE_DISCOVERY
    finally:
        await store.close()
