"""D-22：离线要有重量，也要让她知道（用户 2026-10-23 点名）。

真机：她 09:17 优雅退出、22:09 再起（12.9h），醒来后 desire/feelings 与睡前
**一字不差**，开口也没有一句提到自己不在场过。两条都要修：

A. 启动时的一次性"缺席冲量"：12h ⇒ miss +0.35、CS +18（上限是"不过分"的护栏，
   不是配额）；同一次停机只加一次（kv 幂等标记）；<30s 的普通重启不加。
B. payload 里**一个事实字段** `absence`（不是施压字段）：只在醒来 2h 内、且前 3 次
   开口里出现（先到先消失），deepseek 渲染成一个事实块。

取消测试：
- 删掉冲量（`_apply_absence_impulse` 里不加 miss/CS）⇒
  `test_absence_impulse_applied_once` / `test_absence_impulse_math` 必红；
- 删掉 payload 的 `absence` 装配 ⇒ `test_payload_has_absence_fact` 必红。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import DOWNTIME_MIN_S, TimeSense, TimeSenseState
from elysia.llm.deepseek import DeepSeekBackend, render_absence_block
from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireSystem
from elysia.soul.dimensions import (
    ABSENCE_FACT_MAX_OPENINGS,
    ABSENCE_FACT_WINDOW_S,
    ABSENCE_FIELD,
    ABSENCE_HEADER,
    AbsenceFact,
    FeelingMapper,
    absence_impulse_deltas,
    format_absence_duration,
    format_absence_fact,
)
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import ExpressionJob
from elysia.soul.main import _apply_absence_impulse

T0 = 1_000_000.0
TWELVE_H = 12 * 3600.0


class _FakeLLM:
    async def speak(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            text="嗯。", level="main", validated=ValidationResult(ok=True, reason=None)
        )


def _job(created_ts: float = T0, user_message: str | None = None) -> ExpressionJob:
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState

    return ExpressionJob(
        job_id="job-1",
        created_ts=created_ts,
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


def _timesense(*, last_downtime_s: float, total_s: float = 20 * 3600.0) -> TimeSense:
    return TimeSense(
        TimeSenseState(
            first_existence_ts=T0 - 86400.0,
            last_soul_beat_ts=T0,
            last_body_online_ts=T0,
            last_interaction_ts=T0,
            last_seen_ts=T0,
            downtime_total_s=total_s,
            last_downtime_s=last_downtime_s,
        )
    )


# ── A. 缺席冲量 ──────────────────────────────────────────────────────
def test_absence_impulse_math() -> None:
    miss, cs = absence_impulse_deltas(12 * 3600.0)
    assert miss == pytest.approx(0.35)  # 0.03*12=0.36 ⇒ 封顶 0.35
    assert cs == pytest.approx(18.0)  # 1.5*12=18
    # 上限是"不过分"的护栏，不是配额
    assert absence_impulse_deltas(100 * 3600.0) == pytest.approx((0.35, 20.0))
    assert absence_impulse_deltas(0.0) == pytest.approx((0.0, 0.0))


@pytest.mark.asyncio
async def test_absence_impulse_applied_once(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    await store.start()
    try:
        log = structlog.get_logger("test.absence")
        timesense = _timesense(last_downtime_s=TWELVE_H)
        desire = DesireSystem()
        mapper = FeelingMapper()

        first = await _apply_absence_impulse(store, timesense, desire, mapper, log)
        assert first is not None
        assert desire.state.cs == pytest.approx(60.0 + 18.0)
        assert mapper.current.miss == pytest.approx(0.35)
        # 其余维度一个字不动（缺席不是"信任变化"）
        assert desire.state.tr == pytest.approx(45.0)
        assert desire.state.sa == pytest.approx(20.0)

        # 同一次停机再次调用（模拟重复启动路径）⇒ 不叠加
        second = await _apply_absence_impulse(store, timesense, desire, mapper, log)
        assert second is None
        assert desire.state.cs == pytest.approx(78.0)
        assert mapper.current.miss == pytest.approx(0.35)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_absence_impulse_skips_short_downtime(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    await store.start()
    try:
        log = structlog.get_logger("test.absence")
        timesense = _timesense(last_downtime_s=DOWNTIME_MIN_S - 1.0)
        desire = DesireSystem()
        mapper = FeelingMapper()
        assert await _apply_absence_impulse(store, timesense, desire, mapper, log) is None
        assert desire.state.cs == pytest.approx(60.0)
        assert mapper.current.miss == pytest.approx(0.0)
    finally:
        await store.close()


def test_absence_duration_formatting() -> None:
    assert format_absence_duration(12 * 3600.0 + 54 * 60.0) == "12 小时 54 分"
    assert format_absence_duration(20 * 3600.0 + 6 * 60.0) == "20 小时 06 分"
    assert format_absence_duration(90.0) == "1 分钟"
    assert format_absence_fact(12 * 3600.0 + 54 * 60.0, 20 * 3600.0 + 6 * 60.0) == (
        "我不在的时候：12 小时 54 分（累计 20 小时 06 分）"
    )


# ── B. payload 事实字段 + prompt 渲染 ────────────────────────────────
def _absence_fact() -> AbsenceFact:
    return AbsenceFact(
        last_downtime_s=12 * 3600.0 + 54 * 60.0,
        downtime_total_s=20 * 3600.0 + 6 * 60.0,
        woke_at=T0,
    )


@pytest.mark.asyncio
async def test_payload_has_absence_fact(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        service = ExpressionService(  # type: ignore[arg-type]
            _FakeLLM(), store, absence=_absence_fact()
        )
        payload = (await service.run_job(_job(T0 + 10.0))).instruction
        assert payload.get(ABSENCE_FIELD) == "我不在的时候：12 小时 54 分（累计 20 小时 06 分）"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_absence_fact_expires_after_two_hours(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        service = ExpressionService(  # type: ignore[arg-type]
            _FakeLLM(), store, absence=_absence_fact()
        )
        within = await service.run_job(_job(T0 + 10.0))
        assert ABSENCE_FIELD in within.instruction

        late = await service.run_job(_job(T0 + ABSENCE_FACT_WINDOW_S + 1.0))
        assert ABSENCE_FIELD not in late.instruction
        assert ABSENCE_FACT_WINDOW_S == 2 * 3600.0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_absence_fact_expires_after_three_openings(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        service = ExpressionService(  # type: ignore[arg-type]
            _FakeLLM(), store, absence=_absence_fact()
        )
        seen = 0
        for index in range(ABSENCE_FACT_MAX_OPENINGS + 1):
            payload = (await service.run_job(_job(T0 + 1.0 + index))).instruction
            if ABSENCE_FIELD in payload:
                seen += 1
        assert seen == ABSENCE_FACT_MAX_OPENINGS == 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_no_absence_without_downtime(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        service = ExpressionService(_FakeLLM(), store)  # type: ignore[arg-type]
        payload = (await service.run_job(_job())).instruction
        assert ABSENCE_FIELD not in payload
    finally:
        await store.close()


def test_absence_prompt_rendered_as_fact_block() -> None:
    assert render_absence_block(None) == ""
    assert render_absence_block("") == ""
    block = render_absence_block("我不在的时候：12 小时 54 分（累计 20 小时 06 分）")
    lines = block.splitlines()
    assert lines[0] == ABSENCE_HEADER
    assert lines[1].startswith("- 我不在的时候：")
    assert "？" not in block and "?" not in block  # 不是问句

    backend = DeepSeekBackend.__new__(DeepSeekBackend)
    messages = backend._messages(
        {
            ABSENCE_FIELD: "我不在的时候：12 小时 54 分（累计 20 小时 06 分）",
            "intent": "回应",
        }
    )
    user = messages[1]["content"]
    assert ABSENCE_HEADER in user
    assert "12 小时 54 分" in user
    assert ABSENCE_FIELD not in user  # 键名不进 prompt
    empty = backend._messages({ABSENCE_FIELD: "", "intent": "回应"})
    assert ABSENCE_HEADER not in empty[1]["content"]
    assert ABSENCE_FIELD not in empty[1]["content"]
