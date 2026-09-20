"""P3-B 三层晋升机制单测：晋升判定 + 细节模糊化 + 珍贵保护 + 叙事补全。"""

from __future__ import annotations

import pytest

from elysia.memory.levels import (
    DETAIL_DECAY_PER_LEVEL,
    KIND_INTERACTION,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    LEVEL_WORKING,
    PROMOTE_SHALLOW_ACCESS,
    PROMOTE_SHALLOW_IMPORTANCE,
    PROMOTE_WORKING_IMPORTANCE,
    MemoryRecord,
)
from elysia.memory.promote import (
    can_reach_deep,
    decide_promotion,
    promote_batch,
    with_narrative,
)


def _rec(
    *,
    level: str,
    importance: float,
    access_count: int = 0,
    protected: bool = False,
    detail_level: float = 1.0,
    narrative: str = "",
    content: str = "经历内容",
) -> MemoryRecord:
    return MemoryRecord(
        created_ts=1.0,
        kind=KIND_INTERACTION,
        content=content,
        emotion_vector={"chat": 0.6},
        importance=importance,
        level=level,
        access_count=access_count,
        protected=protected,
        detail_level=detail_level,
        narrative=narrative,
        id=1,
    )


# ── 浅层 → 工作 ─────────────────────────────────────────
def test_shallow_promote_by_importance() -> None:
    promo = decide_promotion(_rec(level=LEVEL_SHALLOW, importance=PROMOTE_SHALLOW_IMPORTANCE))
    assert promo is not None
    assert promo.level == LEVEL_WORKING
    # 非珍贵记忆：细节随晋升损失
    assert promo.detail_level == pytest.approx(1.0 * (1 - DETAIL_DECAY_PER_LEVEL))


def test_shallow_promote_by_access() -> None:
    promo = decide_promotion(
        _rec(level=LEVEL_SHALLOW, importance=0.0, access_count=PROMOTE_SHALLOW_ACCESS)
    )
    assert promo is not None
    assert promo.level == LEVEL_WORKING


def test_shallow_not_promote_below_thresholds() -> None:
    assert (
        decide_promotion(_rec(level=LEVEL_SHALLOW, importance=PROMOTE_SHALLOW_IMPORTANCE - 0.1))
        is None
    )


# ── 工作 → 深层 ─────────────────────────────────────────
def test_working_promote_to_deep() -> None:
    promo = decide_promotion(_rec(level=LEVEL_WORKING, importance=PROMOTE_WORKING_IMPORTANCE))
    assert promo is not None
    assert promo.level == LEVEL_DEEP
    # 在当前完整度上衰减一层（工作层初始 1.0 → 深层 0.5）
    assert promo.detail_level == pytest.approx(1.0 * (1 - DETAIL_DECAY_PER_LEVEL))


def test_working_not_promote_below_threshold() -> None:
    assert (
        decide_promotion(_rec(level=LEVEL_WORKING, importance=PROMOTE_WORKING_IMPORTANCE - 0.1))
        is None
    )


# ── 珍贵记忆保护 ────────────────────────────────────────
def test_protected_never_blurs() -> None:
    promo = decide_promotion(
        _rec(level=LEVEL_SHALLOW, importance=1.0, protected=True, detail_level=1.0)
    )
    assert promo is not None
    assert promo.level == LEVEL_WORKING
    assert promo.detail_level == pytest.approx(1.0)  # 珍贵：细节不降


def test_protected_deep_keeps_full_detail() -> None:
    promo = decide_promotion(
        _rec(
            level=LEVEL_WORKING,
            importance=PROMOTE_WORKING_IMPORTANCE,
            protected=True,
            detail_level=0.8,
        )
    )
    assert promo is not None
    assert promo.level == LEVEL_DEEP
    assert promo.detail_level == pytest.approx(0.8)


# ── 深层不再晋升 ────────────────────────────────────────
def test_deep_never_promotes() -> None:
    assert decide_promotion(_rec(level=LEVEL_DEEP, importance=1.0)) is None


# ── 批量晋升 + 叙事补全 ────────────────────────────────
def test_promote_batch_filters_eligible() -> None:
    records = [
        _rec(level=LEVEL_SHALLOW, importance=0.9),  # 晋升
        _rec(level=LEVEL_SHALLOW, importance=0.0),  # 不晋升
        _rec(level=LEVEL_WORKING, importance=0.9),  # 晋升
    ]
    promotions = promote_batch(records)
    assert len(promotions) == 2
    assert {p.level for p in promotions} == {LEVEL_WORKING, LEVEL_DEEP}


def test_with_narrative_fills_from_content() -> None:
    rec = _rec(level=LEVEL_DEEP, importance=0.9, narrative="")
    filled = with_narrative(rec)
    assert filled.narrative == rec.content
    # 原记录不被修改（不可变语义）
    assert rec.narrative == ""


def test_with_narrative_keeps_existing() -> None:
    rec = _rec(level=LEVEL_DEEP, importance=0.9, narrative="已有的核心叙事")
    assert with_narrative(rec).narrative == "已有的核心叙事"


# ── can_reach_deep 辅助 ─────────────────────────────────
def test_can_reach_deep() -> None:
    assert can_reach_deep(_rec(level=LEVEL_DEEP, importance=0.0))
    assert can_reach_deep(_rec(level=LEVEL_WORKING, importance=0.0))
    assert can_reach_deep(_rec(level=LEVEL_SHALLOW, importance=PROMOTE_WORKING_IMPORTANCE))
    assert not can_reach_deep(_rec(level=LEVEL_SHALLOW, importance=0.0))
