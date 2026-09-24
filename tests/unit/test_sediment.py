"""第八节 S3 单测：自我认知的沉淀（程序找"重复模式"，把候选递给她）。

纯函数模块，不依赖存储；"程序只做发现、不做认定"由数据层断言把关
（候选的正文必须**照抄原文**，且标注必须是 inference/probable）。
"""

from __future__ import annotations

from typing import Any

import pytest

from elysia.memory.levels import (
    CERTAINTY_PROBABLE,
    CLAIM_REJECTED,
    KIND_EXPRESSION,
    KIND_INTERACTION,
    KIND_SELF,
    RETENTION_DORMANT,
    SOURCE_INFERENCE,
    MemoryRecord,
)
from elysia.memory.sediment import (
    SEDIMENT_INTENSITY_MAX,
    SEDIMENT_MIN_OCCURRENCES,
    PatternSignal,
    find_candidate,
)

T0 = 1_000_000.0
_TEXT = "我在意的是每一个和我相遇的人"


def _rec(
    mid: int,
    text: str = _TEXT,
    *,
    days: float = 0.0,
    kind: str = KIND_INTERACTION,
    **over: Any,
) -> MemoryRecord:
    """造一条记忆（默认：可参与沉淀的交互经历）。"""
    return MemoryRecord(
        created_ts=T0 + days * 86400.0,
        kind=kind,
        content=text,
        emotion_vector={"chat": 0.5},
        importance=0.5,
        narrative=text,
        id=mid,
        **over,
    )


def test_no_pattern_when_too_few_occurrences() -> None:
    """只提过两次，算不上"反复"（可能是巧合或重复写入）。"""
    assert find_candidate([_rec(1), _rec(2, days=3)]) is None


def test_no_pattern_within_one_day() -> None:
    """同一场对话里说三遍不是"反复出现"，是复述——跨度不足一天不算。"""
    records = [_rec(1), _rec(2, days=0.2), _rec(3, days=0.5)]
    assert find_candidate(records) is None


def test_pattern_from_repeated_experience() -> None:
    """同一件事跨三天被提起三次 → 沉淀为一个候选，代表是最早那条。"""
    records = [_rec(i + 1, _TEXT, days=float(i) * 1.5) for i in range(SEDIMENT_MIN_OCCURRENCES)]
    pattern = find_candidate(records)
    assert pattern is not None
    assert pattern.occurrences == SEDIMENT_MIN_OCCURRENCES
    assert pattern.span_days == pytest.approx(3.0)
    # 代表 = 最早出现的那条（记录 1：days=0）
    assert pattern.record.id == 1
    assert pattern.text == _TEXT


def test_pattern_ignores_her_own_expression_echo() -> None:
    """她说的话不算经历：近逐字重复是回声，不是模式（与缺口/hooks 同一取舍）。"""
    records = [
        _rec(1),
        _rec(2, days=1.0),
        _rec(3, days=2.0, kind=KIND_EXPRESSION),
    ]
    assert find_candidate(records) is None


def test_pattern_ignores_unreachable_and_superseded() -> None:
    """够不着的（她忘了的）、已被取代的、她拒绝认领的，都不算"我是什么"的证据。"""
    assert (
        find_candidate(
            [_rec(1), _rec(2, days=1.0), _rec(3, days=2.0, retention_state=RETENTION_DORMANT)]
        )
        is None
    )
    assert find_candidate([_rec(1), _rec(2, days=1.0), _rec(3, days=2.0, superseded_by=9)]) is None
    assert (
        find_candidate([_rec(1), _rec(2, days=1.0), _rec(3, days=2.0, claim_status=CLAIM_REJECTED)])
        is None
    )


def test_pattern_not_offered_twice() -> None:
    """挨着的两条：这件事**已经递过候选** → 不再重复递（递过的不再是"新发现"）。"""
    offered = _rec(9, days=2.0, source=SOURCE_INFERENCE, certainty=CERTAINTY_PROBABLE)
    records = [_rec(1), _rec(2, days=1.0), _rec(3, days=2.0), offered]
    assert find_candidate(records) is None


def test_pattern_not_offered_when_already_claimed() -> None:
    """她已经认领过这件事 → 不必再递（自我认知是答案，不是候选）。"""
    claimed = _rec(9, days=2.0, kind=KIND_SELF)
    records = [_rec(1), _rec(2, days=1.0), _rec(3, days=2.0), claimed]
    assert find_candidate(records) is None


def test_returns_the_strongest_pattern_only() -> None:
    """只递一个：提起得最多的优先（她一次只想一件事）。"""
    weak = [_rec(10 + i, "我今天吃了火锅", days=float(i)) for i in range(3)]
    strong = [_rec(20 + i, _TEXT, days=float(i)) for i in range(4)]
    pattern = find_candidate([*weak, *strong])
    assert pattern is not None
    assert pattern.occurrences == 4
    assert pattern.text == _TEXT


def test_intensity_is_capped() -> None:
    """脉冲是"轻微的心里一动"：封顶，连续多轮不把 TR/CS 顶满。"""
    base = PatternSignal(record=_rec(1), occurrences=3, span_days=1.0)
    many = PatternSignal(record=_rec(1), occurrences=10, span_days=30.0)
    assert base.to_event_intensity() == pytest.approx(0.2)
    assert many.to_event_intensity() == pytest.approx(SEDIMENT_INTENSITY_MAX)
