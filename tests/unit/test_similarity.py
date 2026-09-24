"""第十五节 A1 单测：同话题 / "同一件事"判定的**单一入口** + 覆盖通路两道护栏（V1~V3）。

样本**照抄本库真实记忆正文**（设计期只读探针读出的 id：28/30/32/34/46/50/81/105/218），
因此这里断言的不是"设计者想象的句子"，而是"她真实说过的话"——
换说法的同一件事该捞回（$\alpha$ 类）、子串碎片不该被当成一件事（$\beta$ 类）。
"""

from __future__ import annotations

import pytest

from elysia.memory.similarity import (
    COVERAGE_THRESHOLD,
    MIN_SHARED_BIGRAMS,
    MIN_SHORTER_BIGRAMS,
    coverage,
    query_coverage,
    same_event,
)
from elysia.memory.supersede import content_similarity

# 批内去重 / 沉淀共用的"兼容阈值"（A1 只统一实现，不统一阈值）
THRESHOLD = 0.35

# ── V1：$\alpha$ 类"换说法的同一件事"该被捞回（Jaccard 漏、覆盖补）──
_ALPHA_PAIRS = [
    ("那我的生日呢", "那我在告诉你哦，我的生日是5月21日，要记好哦"),
    ("记得你的生日吗", "你应该记得，你的生日是11月11日，记好了哦"),
    ("可以分别告诉我，你的生日，以及我的生日吗？还记得吗？", "记得你的生日吗"),
    ("我上一句话说的是什么呢？你的生日你还记得吗？", "那我的生日呢，你还记得吗？"),
    ("那我的生日呢", "那我的生日你也记好哦，是5月21日"),
    ("你还记得你的生日吗？", "可以分别告诉我，你的生日，以及我的生日吗？还记得吗？"),
]


@pytest.mark.parametrize(("a", "b"), _ALPHA_PAIRS)
def test_same_event_catches_reworded_same_thing(a: str, b: str) -> None:
    """换了说法、只共享关键片段（"生日"/"记得"）也算同一件事。"""
    assert same_event(a, b, jaccard_threshold=THRESHOLD) is True


@pytest.mark.parametrize(("a", "b"), _ALPHA_PAIRS)
def test_alpha_pairs_are_missed_by_jaccard_alone(a: str, b: str) -> None:
    """反证：这些对**全都**在对称 Jaccard 门槛之下——A1 的收益不是幻觉。"""
    assert content_similarity(a, b) < THRESHOLD
    assert coverage(a, b) >= COVERAGE_THRESHOLD


# ── V2：不同事实不得被合并 ──────────────────────────────
def test_same_event_keeps_different_facts_apart() -> None:
    """「我的生日」与「你的生日」是两件事（本库 #145/#81 型），不能并成一条。"""
    assert (
        same_event("我的生日是5月21日", "你的生日是11月11日", jaccard_threshold=THRESHOLD) is False
    )


def test_same_event_keeps_unrelated_apart() -> None:
    assert same_event("你的生日是5月21日", "你喜欢看晚霞", jaccard_threshold=THRESHOLD) is False
    assert coverage("你的生日是5月21日", "你喜欢看晚霞") == 0.0


# ── V3：$\beta$ 类子串碎片护栏 ──────────────────────────
_BETA_PAIRS = [
    ("不对哦", "不对哦，是猜一下我最喜欢你什么"),
    ("不对哦", "不对哦，我纠正一下，爱莉希雅也就是你的生日是11月11日，我的生日是5月21日"),
]


@pytest.mark.parametrize(("a", "b"), _BETA_PAIRS)
def test_fragment_is_not_same_event(a: str, b: str) -> None:
    """ "完全包含"不再自动成立：碎片（"不对哦"）不是一件事。"""
    assert coverage(a, b) == 1.0  # 覆盖口径本身会给满分
    assert same_event(a, b, jaccard_threshold=THRESHOLD) is False  # 护栏拦住


def test_guard_is_what_blocks_the_fragment() -> None:
    """反证护栏确实在起作用：把两道护栏放开，同一对立刻被判成同一件事。"""
    long_text = "不对哦，是猜一下我最喜欢你什么"
    assert (
        same_event(
            "不对哦",
            long_text,
            jaccard_threshold=THRESHOLD,
            min_shared=1,
            min_shorter=1,
        )
        is True
    )


def test_guard_constants_are_the_documented_ones() -> None:
    """护栏数值是"照实写下的经验值"（本库实测），不是随手取的。"""
    assert COVERAGE_THRESHOLD == 0.7
    assert MIN_SHARED_BIGRAMS == 3
    assert MIN_SHORTER_BIGRAMS == 5


# ── 逐字 / 近似重复通路不退化（现状行为保住）────────────
def test_same_event_keeps_verbatim_duplicates() -> None:
    assert (
        same_event(
            "你的生日是5月21日，要记好哦", "你的生日是5月21日，要记好", jaccard_threshold=THRESHOLD
        )
        is True
    )
    assert same_event("那我的生日呢", "那我的生日呢", jaccard_threshold=THRESHOLD) is True


# ── 边界 ────────────────────────────────────────────────
def test_empty_text_is_never_same_event() -> None:
    assert coverage("", "有内容") == 0.0
    assert coverage("有内容", "") == 0.0
    assert same_event("", "", jaccard_threshold=THRESHOLD) is False


# ── 覆盖口径的方向性（`query_coverage` 固定按 query 归一）──
def test_query_coverage_is_query_normalized() -> None:
    """问句在说的事被记忆接住了多少：分母只能是 query（方向不能反）。"""
    query = "那我的生日呢"
    fact = "那我在告诉你哦，我的生日是5月21日，要记好哦"
    assert query_coverage(query, fact) == pytest.approx(0.8)
    assert query_coverage(fact, query) == pytest.approx(0.2)  # 反向：长句被短句接不住


def test_coverage_takes_the_shorter_side() -> None:
    """`coverage` 与方向无关（对称）：短的一方被长的一方覆盖多少。"""
    a = "那我的生日呢"
    b = "那我在告诉你哦，我的生日是5月21日，要记好哦"
    assert coverage(a, b) == coverage(b, a) == pytest.approx(0.8)
    assert query_coverage("", b) == 0.0
