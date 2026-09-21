"""P3-A 记忆数据层单测：重要性打分 + 分层定义 + memory 落库往返 + 索引。"""

from __future__ import annotations

from pathlib import Path

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.memory.levels import (
    DETAIL_DECAY_PER_LEVEL,
    KIND_EXPRESSION,
    KIND_INTERACTION,
    KIND_INTERNAL,
    KIND_STATE,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    LEVEL_WORKING,
    PROMOTE_SHALLOW_ACCESS,
    PROMOTE_SHALLOW_IMPORTANCE,
    PROMOTE_WORKING_IMPORTANCE,
    MemoryRecord,
    default_narrative,
    level_rank,
)
from elysia.memory.scorer import content_salience, emotion_strength, importance, record_importance


# ── 重要性打分 ──────────────────────────────────────────
def test_emotion_strength_takes_max_dim() -> None:
    assert emotion_strength({}) == 0.0
    assert emotion_strength({"chat": 0.3, "miss": 0.9, "rest": 0.2}) == pytest.approx(0.9)
    assert emotion_strength({"chat": 1.2}) == pytest.approx(1.0)  # 封顶


def test_importance_bounds_and_kind_ordering() -> None:
    # 情感为 0 时，只由类型基础权重决定
    assert 0.0 <= importance(kind=KIND_INTERACTION, emotion_vector={}) <= 1.0
    interaction = importance(kind=KIND_INTERACTION, emotion_vector={})
    expression = importance(kind=KIND_EXPRESSION, emotion_vector={})
    # 与用户交互 > 说出的话（用户参与度更高 → 更值得记）
    assert interaction > expression


def test_importance_emotion_and_user_bonus() -> None:
    neutral = importance(kind=KIND_INTERACTION, emotion_vector={})
    # 部分情感（不封顶），保证能验证 user bonus 生效
    emotional = importance(kind=KIND_INTERACTION, emotion_vector={"miss": 0.5})
    user = importance(kind=KIND_INTERACTION, emotion_vector={"miss": 0.5}, user_related=True)
    assert emotional > neutral
    assert user > emotional  # 用户相关额外加成


def test_record_importance_from_record() -> None:
    rec = MemoryRecord(created_ts=1.0, kind=KIND_INTERACTION, content="", emotion_vector={})
    assert record_importance(rec) == importance(kind=KIND_INTERACTION, emotion_vector={})


# ── 内容信号（P3-N：闲聊与事实必须分开）─────────────────
def test_content_salience_small_talk_has_no_signal() -> None:
    assert content_salience("") == 0.0
    assert content_salience("是哪一天呢") == 0.0  # 纯提问
    assert content_salience("记不住了吗?") == 0.0  # 纯提问（"记不住"不算叮嘱）
    assert content_salience("你好") == 0.0  # 语气词
    assert content_salience("你真棒") == 0.0  # 极短句


def test_content_salience_facts_get_high_signal() -> None:
    assert content_salience("我的生日是5月21日") >= 0.7  # 日期 + 稳定事实
    assert content_salience("你应该记得，你的生日是11月11日，记好") >= 0.9  # 加叮嘱


def test_content_salience_question_is_lower_than_statement() -> None:
    # 同样提到"生日"，提问不该和事实陈述同分
    assert content_salience("那我的生日呢") < content_salience("我的生日是5月21日")


def test_importance_separates_chat_from_fact() -> None:
    """回归：闲聊留浅层、事实进深层（此前用户输入一律 0.8，两者同分）。"""
    mood = {"chat": 0.79}
    chat = importance(
        kind=KIND_INTERACTION,
        emotion_vector=mood,
        content="是哪一天呢",
        user_related=True,
    )
    fact = importance(
        kind=KIND_INTERACTION,
        emotion_vector=mood,
        content="你的生日是11月11日，记好",
        user_related=True,
    )
    assert chat < PROMOTE_SHALLOW_IMPORTANCE
    assert fact >= PROMOTE_WORKING_IMPORTANCE


def test_importance_expression_small_talk_stays_shallow() -> None:
    val = importance(kind=KIND_EXPRESSION, emotion_vector={"chat": 0.8}, content="想你了")
    assert val < PROMOTE_SHALLOW_IMPORTANCE


# ── 分层常量 ────────────────────────────────────────────
def test_level_ordering() -> None:
    assert level_rank(LEVEL_SHALLOW) < level_rank(LEVEL_WORKING) < level_rank(LEVEL_DEEP)
    # 晋升阈值合理性：工作层门槛高于浅层，深层更高
    assert PROMOTE_SHALLOW_IMPORTANCE < PROMOTE_WORKING_IMPORTANCE
    assert PROMOTE_SHALLOW_ACCESS >= 1


def test_memory_record_roundtrip_json() -> None:
    rec = MemoryRecord(
        created_ts=123.0,
        kind=KIND_EXPRESSION,
        content="想你了",
        emotion_vector={"miss": 0.8},
        importance=0.7,
        level=LEVEL_WORKING,
        access_count=3,
        last_access_ts=200.0,
        protected=True,
        detail_level=0.5,
        narrative="她想他了",
        id=7,
    )
    d = rec.to_dict()
    restored = MemoryRecord.from_dict(d)
    assert restored == rec


def test_default_narrative_fallback_to_content() -> None:
    rec = MemoryRecord(created_ts=1.0, kind=KIND_EXPRESSION, content="内容台词", emotion_vector={})
    assert default_narrative(rec) == "内容台词"


# ── 存储落库 + 往返 ────────────────────────────────────
@pytest.mark.asyncio
async def test_memory_add_and_retrieve(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await store.add_memory(
        100.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "你说晚安了",
            "emotion_vector": {"chat": 0.6},
            "importance": 0.7,
            "protected": True,
        },
    )
    assert isinstance(mid, int) and mid >= 1
    fetched = await store.get_memory(mid)
    assert fetched is not None
    assert fetched["content"] == "你说晚安了"
    assert fetched["emotion_vector"] == {"chat": 0.6}
    assert fetched["protected"] is True
    await store.close()


@pytest.mark.asyncio
async def test_memory_counts_and_iteration(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    await store.add_memory(
        1.0, {"level": LEVEL_SHALLOW, "kind": KIND_STATE, "content": "a", "emotion_vector": {}}
    )
    await store.add_memory(
        2.0, {"level": LEVEL_SHALLOW, "kind": KIND_STATE, "content": "b", "emotion_vector": {}}
    )
    await store.add_memory(
        3.0, {"level": LEVEL_DEEP, "kind": KIND_INTERNAL, "content": "c", "emotion_vector": {}}
    )
    assert await store.count_memories() == 3
    assert await store.count_memories(LEVEL_SHALLOW) == 2
    assert await store.count_memories(LEVEL_DEEP) == 1
    all_rows = await store.iterate_memories()
    assert len(all_rows) == 3
    await store.close()


@pytest.mark.asyncio
async def test_memory_level_update(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await store.add_memory(
        1.0, {"level": LEVEL_SHALLOW, "kind": KIND_STATE, "content": "x", "emotion_vector": {}}
    )
    await store.update_memory_level(mid, level=LEVEL_WORKING, detail_level=DETAIL_DECAY_PER_LEVEL)
    fetched = await store.get_memory(mid)
    assert fetched is not None
    assert fetched["level"] == LEVEL_WORKING
    assert fetched["detail_level"] == pytest.approx(DETAIL_DECAY_PER_LEVEL)
    await store.close()


@pytest.mark.asyncio
async def test_memory_index_add_and_decay(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await store.add_memory(
        1.0, {"level": LEVEL_SHALLOW, "kind": KIND_STATE, "content": "y", "emotion_vector": {}}
    )
    await store.add_memory_index(
        mid, path_key="user/晚安", strength=0.9, emotions=0.8, last_retrieve_ts=100.0
    )
    idx_rows = await store.execute_raw(
        "SELECT id, strength FROM memory_index WHERE memory_id = ?", (mid,)
    )
    assert len(idx_rows) == 1
    idx_id, _ = idx_rows[0]
    await store.decay_memory_index([(idx_id, 0.4)])
    after = await store.execute_raw("SELECT strength FROM memory_index WHERE id = ?", (idx_id,))
    assert after[0][0] == pytest.approx(0.4)
    await store.close()
