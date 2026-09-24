"""表达异步化数据模型单测（《运行时可靠性收口》步 1.1）。

纯类型 + 纯函数：任务标识、偏序键、偏序比较、结果状态与默认值。
本步**不接线**，故无异步、无外部依赖。
"""

from __future__ import annotations

from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput
from elysia.soul.expression_types import (
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    ExpressionJob,
    ExpressionResult,
    is_fresh,
    make_job_id,
)
from elysia.tts.chain import TTSResult


def _job(seq: int = 1, ts: float = 100.0, force: bool = False) -> ExpressionJob:
    return ExpressionJob(
        job_id=make_job_id(seq, ts),
        created_ts=ts,
        heartbeat_seq=seq,
        output=BrainOutput(),
        summary={"day_phase": "day", "body_away_s": 0.0},
        force=force,
    )


# ── 任务标识 ────────────────────────────────────────────────────


def test_make_job_id_includes_seq_and_ts() -> None:
    assert make_job_id(7, 123.5) == "7-123.5"


def test_make_job_id_distinguishes_same_tick_jobs() -> None:
    # 同一拍（seq 相同）也可能有多个 job：靠创建时刻区分
    assert make_job_id(3, 10.0) != make_job_id(3, 11.0)


# ── 偏序键与偏序比较 ────────────────────────────────────────────


def test_job_order_key_is_seq_then_ts() -> None:
    j = _job(seq=5, ts=42.0)
    assert j.order_key == (5, 42.0)


def test_is_fresh_accepts_when_nothing_accepted_yet() -> None:
    assert is_fresh((1, 1.0), None) is True


def test_is_fresh_accepts_equal_order() -> None:
    assert is_fresh((2, 2.0), (2, 2.0)) is True


def test_is_fresh_accepts_newer_order() -> None:
    assert is_fresh((3, 1.0), (2, 9.0)) is True
    assert is_fresh((2, 5.0), (2, 4.0)) is True


def test_is_fresh_rejects_older_order() -> None:
    # 旧结果不得覆盖新结果（20.6 的 1.4）
    assert is_fresh((1, 9.0), (2, 0.0)) is False
    assert is_fresh((2, 4.0), (2, 5.0)) is False


# ── ExpressionJob 默认值 ────────────────────────────────────────


def test_job_defaults_are_inert() -> None:
    j = ExpressionJob(
        job_id="1-1.0",
        created_ts=1.0,
        heartbeat_seq=1,
        output=BrainOutput(),
        summary={},
    )
    assert j.vrram_mb == 0.0
    assert j.env is None
    assert j.user_message is None
    assert j.force is False  # 内部念头（D1：遇满可丢）


# ── ExpressionResult ───────────────────────────────────────────


def test_result_defaults_are_empty() -> None:
    r = ExpressionResult(job_id="1-1.0", status=STATUS_SUCCESS, order_key=(1, 1.0))
    assert r.text == ""
    assert r.level == ""
    assert r.validation is None
    assert r.instruction is None
    assert r.intent == ""
    assert r.tts is None
    assert r.error is None


def test_result_carries_persist_payload() -> None:
    # D2：落库留在心跳 ⇒ 结果必须带回取证与写入所需的一切
    r = ExpressionResult(
        job_id="9-2.0",
        status=STATUS_SUCCESS,
        order_key=(9, 2.0),
        text="晚上好",
        level="main",
        validation=ValidationResult(ok=True, reason=None),
        instruction={"intent": "回应"},
        intent="回应",
        tts=TTSResult(text="晚上好", audio=None, source="cache"),
    )
    assert r.text == "晚上好"
    assert r.level == "main"
    assert r.intent == "回应"
    assert r.instruction == {"intent": "回应"}
    assert r.tts is not None and r.tts.source == "cache"


def test_status_constants_are_distinct() -> None:
    assert len({STATUS_SUCCESS, STATUS_SKIPPED, STATUS_TIMEOUT, STATUS_FAILED}) == 4
