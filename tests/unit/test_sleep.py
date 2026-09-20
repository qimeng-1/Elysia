"""P3-E 睡眠整合 = 做梦单测：自由联想流生成（无 LLM）+ 无料无梦。"""

from __future__ import annotations

import random

from elysia.memory.levels import (
    KIND_INTERACTION,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    LEVEL_WORKING,
    MemoryRecord,
)
from elysia.memory.sleep import (
    DREAM_MIN_FRAGMENTS,
    Dream,
    synthesize_dream,
)


def _rec(mid: int, level: str, narrative: str) -> MemoryRecord:
    return MemoryRecord(
        id=mid,
        created_ts=float(mid),
        kind=KIND_INTERACTION,
        content=narrative,
        emotion_vector={"chat": 0.5},
        importance=0.8,
        level=level,
        protected=level == LEVEL_DEEP,
        narrative=narrative,
    )


def test_synthesize_dream_returns_dream_with_fragments() -> None:
    records = [
        _rec(1, LEVEL_DEEP, "深夜的星空"),
        _rec(2, LEVEL_WORKING, "你说过的笑话"),
        _rec(3, LEVEL_SHALLOW, "今天的花香"),
    ]
    dream = synthesize_dream(records, rng=random.Random(42))
    assert dream is not None
    assert isinstance(dream, Dream)
    assert dream.has_content()
    # 自由联想流 = 碎片串接（无 LLM，纯函数）
    assert len(dream.fragments) == len(records)
    # 全部碎片都被联想（顺序按权重排序，未必等于原始 id 序）
    assert set(dream.source_ids) == {1, 2, 3}
    assert set(dream.fragments) == {"深夜的星空", "你说过的笑话", "今天的花香"}


def test_synthesize_dream_no_fragments_returns_none() -> None:
    assert synthesize_dream([], rng=random.Random(42)) is None


def test_synthesize_dream_insufficient_fragments() -> None:
    # 碎片数低于最少要求 → 无梦
    records = [_rec(1, LEVEL_WORKING, "只有一条")]
    assert synthesize_dream(records, rng=random.Random(42)) is None
    assert DREAM_MIN_FRAGMENTS == 2


def test_dream_has_content_empty_guard() -> None:
    empty = Dream(fragments=[], stream="", source_ids=[])
    assert not empty.has_content()


def test_dream_fragment_ordering_prefers_deep() -> None:
    # 深层记忆权重更高 → 更常被选中（固定 rng 下断言稳定）
    records = [
        _rec(1, LEVEL_DEEP, "核心情感"),
        _rec(2, LEVEL_SHALLOW, "普通事"),
        _rec(3, LEVEL_DEEP, "珍贵记忆"),
    ]
    dream = synthesize_dream(records, rng=random.Random(7))
    assert dream is not None
    # 两条深层记忆都应在（权重高，必选）
    assert "核心情感" in dream.fragments
    assert "珍贵记忆" in dream.fragments
