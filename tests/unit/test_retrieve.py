"""P3-D 记忆检索 + 表达注入单测：染色挑选 + memory_hooks 注入。"""

from __future__ import annotations

from pathlib import Path

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.memory.levels import (
    KIND_INTERACTION,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    LEVEL_WORKING,
    MemoryRecord,
)
from elysia.memory.retrieve import (
    MAX_HOOKS,
    MemoryHit,
    mood_similarity,
    retrieve_from_store,
    score_memory,
    select_hooks,
)


def _rec(
    *,
    level: str = LEVEL_WORKING,
    emotion: dict[str, float] | None = None,
    narrative: str = "一段记忆叙事",
    mid: int | None = 1,
) -> MemoryRecord:
    return MemoryRecord(
        id=mid,
        created_ts=1.0,
        kind=KIND_INTERACTION,
        content="内容",
        emotion_vector=emotion if emotion is not None else {"chat": 0.5},
        importance=0.7,
        level=level,
        access_count=1,
        protected=False,
        narrative=narrative,
    )


# ── 情绪染色 ────────────────────────────────────────────
def test_mood_similarity_matches_overlapping_dims() -> None:
    mem = {"miss": 0.8, "chat": 0.2}
    mood = {"miss": 0.9, "chat": 0.0}
    score = mood_similarity(mem, mood)
    assert score > 0.0  # 有共同感受 → 正匹配
    # 无关维度不算分
    assert mood_similarity({"explore": 0.8}, {"miss": 0.9}) == 0.0


def test_mood_similarity_empty() -> None:
    assert mood_similarity({}, {"miss": 0.9}) == 0.0
    assert mood_similarity({"miss": 0.8}, {}) == 0.0


# ── 检索得分 ────────────────────────────────────────────
def test_score_memory_privildeges_dep_and_mood() -> None:
    deep = score_memory(_rec(level=LEVEL_DEEP, emotion={"miss": 0.9}), {"miss": 1.0})
    shallow = score_memory(_rec(level=LEVEL_SHALLOW, emotion={"chat": 0.2}), {"miss": 1.0})
    assert deep > shallow  # 深层 + 高匹配 → 分更高


def test_score_memory_index_availability() -> None:
    fresh = score_memory(_rec(), current_mood={}, index_strength=0.9)
    faded = score_memory(_rec(), current_mood={}, index_strength=0.1)
    assert fresh > faded  # 索引强的记忆更易召回


# ── 挑选 hooks ──────────────────────────────────────────
def test_select_hooks_sorts_by_score_and_caps() -> None:
    recs = [
        _rec(mid=1, level=LEVEL_DEEP, emotion={"miss": 0.9}, narrative="珍视"),
        _rec(mid=2, level=LEVEL_SHALLOW, emotion={"chat": 0.1}, narrative="弱"),
        _rec(mid=3, level=LEVEL_WORKING, emotion={"miss": 0.5}, narrative="中"),
        _rec(mid=4, level=LEVEL_WORKING, emotion={"miss": 0.6}, narrative="中2"),
    ]
    hooks = select_hooks(recs, {"miss": 1.0})
    assert len(hooks) == MAX_HOOKS  # 截断到上限
    assert hooks[0].memory_id == 1  # 最高分排最前
    scores = [h.score for h in hooks]
    assert scores == sorted(scores, reverse=True)


def test_select_hooks_returns_memoryhit_shape() -> None:
    hooks = select_hooks([_rec(narrative="想你了")], {"miss": 1.0})
    assert len(hooks) == 1
    h = hooks[0]
    assert isinstance(h, MemoryHit)
    assert h.narrative == "想你了"
    assert h.level == LEVEL_WORKING
    assert h.protected is False


def test_select_hooks_empty() -> None:
    assert select_hooks([], {"miss": 1.0}) == []


# ── 基于存储检索（异步，落库往返） ──────────────────────
@pytest.mark.asyncio
async def test_retrieve_from_store(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    # 期待的情感匹配记忆
    await store.add_memory(
        2.0,
        {
            "level": LEVEL_DEEP,
            "kind": KIND_INTERACTION,
            "content": "深深想念的回忆",
            "emotion_vector": {"miss": 0.9, "chat": 0.2},
            "importance": 0.9,
            "protected": True,
            "narrative": "你在深夜陪我聊天的回忆",
        },
    )
    await store.add_memory(
        3.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "普通事",
            "emotion_vector": {"chat": 0.1, "miss": 0.05},
            "importance": 0.3,
            "narrative": "普通日常",
        },
    )
    hooks = await retrieve_from_store(store, {"miss": 1.0})
    await store.close()
    assert len(hooks) == 2
    # 深层次高匹配的排最前
    assert hooks[0].narrative == "你在深夜陪我聊天的回忆"
    # 校验 hook 命中确实带叙事
    assert {"你在深夜陪我聊天的回忆", "普通日常"} == {h.narrative for h in hooks}
