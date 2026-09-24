"""记忆修正/覆盖单测：同话题新事实取代旧事实 + 检索不再召回被取代记忆。"""

from __future__ import annotations

from pathlib import Path

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.memory.levels import (
    KIND_EXPRESSION,
    KIND_INTERACTION,
    KIND_SELF,
    LEVEL_SHALLOW,
    MemoryRecord,
)
from elysia.memory.retrieve import retrieve_from_store
from elysia.memory.supersede import (
    SUPERSEDE_SIMILARITY,
    content_similarity,
    find_superseded,
)


def _rec(
    *,
    mid: int,
    content: str,
    created_ts: float = 1.0,
    kind: str = KIND_INTERACTION,
    superseded_by: int | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=mid,
        created_ts=created_ts,
        kind=kind,
        content=content,
        emotion_vector={"chat": 0.5},
        importance=0.8,
        level=LEVEL_SHALLOW,
        narrative=content,
        superseded_by=superseded_by,
    )


# ── 相似度 ────────────────────────────────────────────
def test_similarity_catches_correction() -> None:
    """生日更正（同话题、仅数字不同）→ 高相似。"""
    sim = content_similarity("我的生日是11月11日", "其实我的生日是12月12日")
    assert sim >= SUPERSEDE_SIMILARITY


def test_similarity_unrelated_low() -> None:
    """无关内容（两顿饭）→ 低相似，不误判。"""
    assert content_similarity("我昨天吃了一顿大餐", "我吃了火锅") < SUPERSEDE_SIMILARITY


def test_similarity_ignores_punctuation() -> None:
    assert content_similarity("生日：11月11日！", "生日是11月11日") > 0.5


# ── 取代判定 ───────────────────────────────────────────
def test_find_superseded_detects_birthday_correction() -> None:
    old = _rec(mid=1, content="我的生日是11月11日", created_ts=1.0)
    ids = find_superseded("其实我的生日是12月12日", 2.0, [old])
    assert ids == [1]


def test_find_superseded_ignores_unrelated() -> None:
    meal = _rec(mid=1, content="我昨天吃了一顿大餐", created_ts=1.0)
    assert find_superseded("我吃了火锅", 2.0, [meal]) == []


def test_find_superseded_skips_expression_and_newer() -> None:
    her_words = _rec(mid=1, content="我的生日是11月11日", kind=KIND_EXPRESSION)
    newer = _rec(mid=2, content="我的生日是11月11日", created_ts=5.0)
    assert find_superseded("我的生日是11月11日", 2.0, [her_words, newer]) == []


def test_find_superseded_skips_self_memory() -> None:
    """自我认知不被取代（第八节 N4）：更新"我是谁"必须走她自己的动作，一句闲聊不算数。"""
    claimed_self = _rec(mid=1, content="我是爱莉希雅，我记得每个相遇的人", kind=KIND_SELF)
    assert find_superseded("我是爱莉希雅，我记得每个相遇的人", 2.0, [claimed_self]) == []


def test_find_superseded_skips_already_superseded() -> None:
    gone = _rec(mid=1, content="我的生日是11月11日", superseded_by=9)
    assert find_superseded("其实我的生日是12月12日", 2.0, [gone]) == []


# ── 落库往返 + 检索过滤 ────────────────────────────────
@pytest.mark.asyncio
async def test_superseded_memory_not_retrieved(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    old_id = await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "我的生日是11月11日",
            "emotion_vector": {"chat": 0.5},
            "importance": 0.8,
            "narrative": "我的生日是11月11日",
        },
    )
    new_id = await store.add_memory(
        2.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "其实我的生日是12月12日",
            "emotion_vector": {"chat": 0.5},
            "importance": 0.8,
            "narrative": "其实我的生日是12月12日",
        },
    )
    await store.mark_superseded(old_id, new_id)

    # 多日后新鲜度归零，旧错误事实不再出现在 hooks（曾经会排第一）
    hooks = await retrieve_from_store(store, {"chat": 1.0}, now=30.0 * 86400)
    names = [h.narrative for h in hooks]
    assert "我的生日是11月11日" not in names
    assert "其实我的生日是12月12日" in names

    rec = await store.get_memory(old_id)
    assert rec is not None
    assert rec["superseded_by"] == new_id  # 数据保留（可溯源），仅标记失效
    await store.close()
