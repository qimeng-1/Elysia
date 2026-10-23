"""D-22/D-24：离线要有重量，也要让她知道（用户 2026-10-23 点名）。

真机：她 09:17 优雅退出、22:09 再起（12.9h），醒来后 desire/feelings 与睡前
**一字不差**，开口也没有一句提到自己不在场过。两条都要修：

A. 启动时的一次性"缺席冲量"：12h ⇒ miss +0.35、CS +18（上限是"不过分"的护栏，
   不是配额）；同一次停机只加一次（kv 幂等标记）；<30s 的普通重启不加。
B. payload 里**一个事实字段** `absence`（不是施压字段）：只在醒来 2h 内、且前 3 次
   开口里出现（先到先消失），deepseek 渲染成一个事实块。

D-24（真机验收发现的语义漏洞）：冲量与事实都曾读 `state.last_downtime_s`（**上一次**
长停机的持久值）⇒ 8 秒的普通重启也会给 12.9 小时前的缺席补冲量、并重新武装
「我不在的时候：12 小时 54 分」的假事实。现在两者**只认本次启动的 gap**
（`_build_timesense` 暴露的 `startup_gap_s`）。

取消测试：
- 删掉冲量（`_apply_absence_impulse` 里不加 miss/CS）⇒ `test_absence_impulse_applied_once` 红；
- 删掉 payload 的 `absence` 装配 ⇒ `test_payload_has_absence_fact` 红；
- 把判据改回 `state.last_downtime_s` ⇒ `test_stale_last_downtime_does_not_rearm_absence` 红。
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import (
    DOWNTIME_MIN_S,
    TimeSense,
    TimeSenseState,
    to_payload,
)
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
    absence_fact_for,
    absence_impulse_deltas,
    format_absence_duration,
    format_absence_fact,
)
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import ExpressionJob
from elysia.soul.main import _apply_absence_impulse, _build_timesense, startup_gap_s

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


def _timesense(
    *,
    last_downtime_s: float,
    total_s: float = 20 * 3600.0,
    startup_gap_s: float | None = None,
) -> TimeSense:
    timesense = TimeSense(
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
    # D-24：本次启动的 gap 由 `_build_timesense` 暴露；测试默认沿用 last_downtime_s
    timesense.startup_gap_s = startup_gap_s if startup_gap_s is not None else last_downtime_s
    return timesense


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
    """D-24 真机场景：上次长停机 12h 还挂在 state 上、本次只停 29s ⇒ 什么都不做。"""
    store = StateStore(tmp_path / "state.db")
    await store.start()
    try:
        log = structlog.get_logger("test.absence")
        timesense = _timesense(last_downtime_s=TWELVE_H, startup_gap_s=DOWNTIME_MIN_S - 1.0)
        desire = DesireSystem()
        mapper = FeelingMapper()
        assert await _apply_absence_impulse(store, timesense, desire, mapper, log) is None
        assert desire.state.cs == pytest.approx(60.0)
        assert mapper.current.miss == pytest.approx(0.0)
        # 事实也不能拿旧值重新武装
        assert (
            absence_fact_for(
                timesense.state,
                now=T0,
                min_downtime_s=DOWNTIME_MIN_S,
                startup_gap_s=startup_gap_s(timesense),
            )
            is None
        )
    finally:
        await store.close()


async def _seed_timesense(
    store: StateStore,
    *,
    last_seen_ts: float,
    last_downtime_s: float,
    total_s: float,
) -> None:
    state = TimeSenseState(
        first_existence_ts=T0 - 86400.0,
        last_soul_beat_ts=last_seen_ts,
        last_body_online_ts=last_seen_ts,
        last_interaction_ts=last_seen_ts,
        last_seen_ts=last_seen_ts,
        downtime_total_s=total_s,
        last_downtime_s=last_downtime_s,
    )
    await store.save_json("timesense", to_payload(state))


@pytest.mark.asyncio
async def test_stale_12h_last_downtime_with_8s_gap_does_nothing(tmp_path: Path) -> None:
    """D-24 回归（真机复现）：上次 12h、本次 8s ⇒ 不加冲量、不生成事实、不写标记。"""
    store = StateStore(tmp_path / "state.db")
    await store.start()
    try:
        log = structlog.get_logger("test.absence")
        await _seed_timesense(
            store,
            last_seen_ts=time.time() - 8.0,
            last_downtime_s=TWELVE_H,
            total_s=20 * 3600.0,
        )
        timesense = await _build_timesense(store, log)
        gap = startup_gap_s(timesense)
        assert gap == pytest.approx(8.0, abs=1.5)
        assert gap < DOWNTIME_MIN_S
        # 旧值仍在持久 state 上——修前代码正是拿它当基准，于是 8s 重启也补 12h 的账
        assert timesense.state.last_downtime_s == pytest.approx(TWELVE_H)

        desire = DesireSystem()
        mapper = FeelingMapper()
        assert await _apply_absence_impulse(store, timesense, desire, mapper, log) is None
        assert desire.state.cs == pytest.approx(60.0)
        assert mapper.current.miss == pytest.approx(0.0)
        assert (
            absence_fact_for(
                timesense.state,
                now=time.time(),
                min_downtime_s=DOWNTIME_MIN_S,
                startup_gap_s=gap,
            )
            is None
        )
        assert await store.load_json("absence_impulse", default=None) is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_long_gap_this_start_applies_and_makes_fact(tmp_path: Path) -> None:
    """本次 gap=12h（与旧 last_downtime_s 无关）⇒ 冲量 +0.35/+18 且事实一行。"""
    store = StateStore(tmp_path / "state.db")
    await store.start()
    try:
        log = structlog.get_logger("test.absence")
        await _seed_timesense(
            store,
            last_seen_ts=time.time() - TWELVE_H,
            last_downtime_s=0.0,  # 旧值为 0，本次仍必须按 12h 施加
            total_s=0.0,
        )
        timesense = await _build_timesense(store, log)
        gap = startup_gap_s(timesense)
        assert gap == pytest.approx(TWELVE_H, abs=5.0)

        desire = DesireSystem()
        mapper = FeelingMapper()
        applied = await _apply_absence_impulse(store, timesense, desire, mapper, log)
        assert applied is not None
        assert desire.state.cs == pytest.approx(78.0, abs=0.01)
        assert mapper.current.miss == pytest.approx(0.35, abs=0.001)
        fact = absence_fact_for(
            timesense.state,
            now=time.time(),
            min_downtime_s=DOWNTIME_MIN_S,
            startup_gap_s=gap,
        )
        assert fact is not None
        assert "12 小时" in fact.line()
        assert "累计" in fact.line()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_second_restart_with_short_gap_does_not_reapply(tmp_path: Path) -> None:
    """连续两次重启（第二次 gap=8s）⇒ 不重复施加（marker + 本次 gap 双保险）。"""
    store = StateStore(tmp_path / "state.db")
    await store.start()
    try:
        log = structlog.get_logger("test.absence")
        # 第一次：12h 长停机 ⇒ 正常施加
        await _seed_timesense(
            store,
            last_seen_ts=time.time() - TWELVE_H,
            last_downtime_s=0.0,
            total_s=0.0,
        )
        first_ts = await _build_timesense(store, log)
        desire = DesireSystem()
        mapper = FeelingMapper()
        assert await _apply_absence_impulse(store, first_ts, desire, mapper, log) is not None
        cs_after_first = desire.state.cs
        miss_after_first = mapper.current.miss

        # 心跳把 state 落库；8 秒后再次重启
        payload = to_payload(first_ts.state)
        payload["last_seen_ts"] = time.time() - 8.0
        await store.save_json("timesense", payload)
        second_ts = await _build_timesense(store, log)
        assert startup_gap_s(second_ts) == pytest.approx(8.0, abs=1.5)

        assert await _apply_absence_impulse(store, second_ts, desire, mapper, log) is None
        assert desire.state.cs == pytest.approx(cs_after_first)
        assert mapper.current.miss == pytest.approx(miss_after_first)
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
