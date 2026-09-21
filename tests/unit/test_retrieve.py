"""P3-D 记忆检索 + 表达注入单测：染色挑选 + memory_hooks 注入。"""

from __future__ import annotations

from pathlib import Path

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.memory.levels import (
    KIND_EXPRESSION,
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
    score_breakdown,
    score_memory,
    select_hooks,
)


def _rec(
    *,
    level: str = LEVEL_WORKING,
    emotion: dict[str, float] | None = None,
    narrative: str = "一段记忆叙事",
    mid: int | None = 1,
    created_ts: float = 1.0,
    importance: float = 0.7,
) -> MemoryRecord:
    return MemoryRecord(
        id=mid,
        created_ts=created_ts,
        kind=KIND_INTERACTION,
        content="内容",
        emotion_vector=emotion if emotion is not None else {"chat": 0.5},
        importance=importance,
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


def test_mood_similarity_is_cosine_not_magnitude() -> None:
    """情绪强度大 ≠ 更匹配：同一方向应得同一分（否则聊天时全体通吃）。"""
    mood = {"miss": 0.4, "chat": 0.6}
    strong = {"miss": 0.8, "chat": 1.2}
    weak = {"miss": 0.2, "chat": 0.3}
    assert mood_similarity(strong, mood) == pytest.approx(mood_similarity(weak, mood), abs=0.01)


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


def test_score_breakdown_includes_importance() -> None:
    """得分的"本身价值"项必须存在：重要度要真的参与打分，否则形同摆设。"""
    parts = score_breakdown(_rec(importance=0.8), {"chat": 0.5})
    assert set(parts) == {"level", "emotion", "index", "importance", "recency"}
    assert parts["importance"] == pytest.approx(0.4)


def test_score_memory_fact_beats_small_talk() -> None:
    """回归：同类、同心情、同时间下，有意义的事（高重要度）必须压过闲聊。"""
    mood = {"chat": 0.8, "explore": 0.5}
    now = 10 * 86400.0
    small_talk = _rec(
        mid=1, level=LEVEL_SHALLOW, importance=0.37, created_ts=now - 3600, emotion=mood
    )
    fact = _rec(mid=2, level=LEVEL_DEEP, importance=0.97, created_ts=now - 3600, emotion=mood)
    assert score_memory(fact, mood, now=now) > score_memory(small_talk, mood, now=now)


def test_score_memory_recency_boosts_recent_over_old() -> None:
    """短期记忆可靠召回：同样条件下，昨天的大餐该比一月前的记忆分高。"""
    now = 10 * 86400.0  # 第 10 天
    yesterday = _rec(
        created_ts=now - 1 * 86400.0,
        level=LEVEL_SHALLOW,
        emotion={"chat": 0.5},
        narrative="昨天的大餐",
    )
    month_ago = _rec(
        created_ts=now - 30 * 86400.0,
        level=LEVEL_SHALLOW,
        emotion={"chat": 0.5},
        narrative="一个月前的事",
    )
    recent = score_memory(yesterday, {"chat": 0.5}, now=now)
    stale = score_memory(month_ago, {"chat": 0.5}, now=now)
    assert recent > stale  # 新鲜度让短期记忆被优先召回


def test_select_hooks_excludes_own_expression_echo() -> None:
    """检索不应把她自己刚说过的话当"记起你"注入，避免重启重复她上次的话。"""
    own_echo = MemoryRecord(
        id=2,
        created_ts=2.0,
        kind=KIND_EXPRESSION,
        content="上次我说的话",
        emotion_vector={"chat": 0.9},
        importance=0.5,
        level=LEVEL_SHALLOW,
        access_count=1,
        protected=False,
        narrative="上次我说的话",
    )
    hooks = select_hooks([own_echo, _rec(mid=1, narrative="真正的经历")], {"chat": 1.0})
    # 她的发言回声被排除，只保留真实经历
    assert [h.memory_id for h in hooks] == [1]


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


@pytest.mark.asyncio
async def test_retrieve_touches_access_count(tmp_path: Path) -> None:
    """检索命中要递增 access_count（P3-B 浅层→工作晋升依据）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "昨天的经历",
            "emotion_vector": {"chat": 0.9},
            "importance": 0.3,
            "protected": False,
            "narrative": "昨天的经历",
        },
    )
    hooks = await retrieve_from_store(store, {"chat": 1.0}, now=10.0)
    rec = await store.get_memory(mid)
    assert hooks  # 有命中
    assert rec is not None
    assert rec["access_count"] == 1  # 命中一次 → 计数 +1
    assert rec["last_access_ts"] == 10.0
    await store.close()


@pytest.mark.asyncio
async def test_retrieve_uses_index_strength(tmp_path: Path) -> None:
    """索引 strength 参与打分：同记忆索引弱 → 分更低（P3-C 遗忘消费方）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "一段往事",
            "emotion_vector": {"chat": 0.9},
            "importance": 0.5,
            "protected": False,
            "narrative": "一段往事",
        },
    )
    idx_id = await store.add_memory_index(
        memory_id=mid, path_key="main", strength=1.0, emotions=0.0
    )
    # 未衰减：默认可用性 1.0
    hooks = await retrieve_from_store(store, {"chat": 1.0}, now=10.0)
    assert hooks[0].score > 0.8
    # 索引衰减到 floor 以下：可用性被拉低
    await store.decay_memory_index([(idx_id, 0.1)])
    hooks2 = await retrieve_from_store(store, {"chat": 1.0}, now=10.0)
    assert hooks2[0].score < hooks[0].score
    await store.close()
