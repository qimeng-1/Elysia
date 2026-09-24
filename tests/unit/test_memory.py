"""P3-A 记忆数据层单测：重要性打分 + 分层定义 + memory 落库往返 + 索引。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.memory.levels import (
    CERTAINTIES,
    CERTAINTY_CERTAIN,
    CERTAINTY_PROBABLE,
    CLAIM_CLAIMED,
    CLAIM_REJECTED,
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
    SOURCE_OBSERVATION,
    SOURCE_SELF,
    SOURCE_USER,
    SOURCES,
    MemoryRecord,
    default_certainty,
    default_narrative,
    default_source,
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


# ── 来源 / 确定性（P3-T）：这段记忆是谁的、她有多确定 ──────
def test_provenance_defaults_derived_from_kind() -> None:
    """未标注时按 kind 推导：用户告知=user/确信，她说的=self，观察=observation。"""
    assert default_source(KIND_INTERACTION) == SOURCE_USER
    assert default_source(KIND_EXPRESSION) == SOURCE_SELF
    assert default_source(KIND_STATE) == SOURCE_OBSERVATION
    assert default_certainty(KIND_INTERACTION) == CERTAINTY_CERTAIN
    assert default_certainty(KIND_INTERNAL) == CERTAINTY_PROBABLE
    # 未知 kind 有兜底，不抛异常（防御旧库里的意外取值）
    assert default_source("nope") in SOURCES
    assert default_certainty("nope") in CERTAINTIES


def test_from_dict_fills_missing_provenance() -> None:
    """旧记录缺 source/certainty 键时按 kind 补默认——老库行为不变。"""
    legacy = MemoryRecord.from_dict(
        {"created_ts": 1.0, "kind": KIND_INTERACTION, "content": "生日", "emotion_vector": {}}
    )
    assert legacy.source == SOURCE_USER
    assert legacy.certainty == CERTAINTY_CERTAIN
    # 显式给出时以显式为准，不被默认值覆盖
    explicit = MemoryRecord.from_dict(
        {
            "created_ts": 1.0,
            "kind": KIND_INTERACTION,
            "content": "猜测",
            "emotion_vector": {},
            "source": SOURCE_SELF,
            "certainty": CERTAINTY_PROBABLE,
        }
    )
    assert explicit.source == SOURCE_SELF
    assert explicit.certainty == CERTAINTY_PROBABLE
    # 序列化往返不丢字段
    assert MemoryRecord.from_dict(explicit.to_dict()) == explicit


@pytest.mark.asyncio
async def test_add_memory_tags_provenance(tmp_path: Path) -> None:
    """写入点标注优先；未标注时按 kind 落默认（库中不出现 NULL）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    tagged = await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "她的猜测",
            "emotion_vector": {},
            "source": SOURCE_SELF,
            "certainty": CERTAINTY_PROBABLE,
        },
    )
    untagged = await store.add_memory(
        2.0,
        {"level": LEVEL_SHALLOW, "kind": KIND_INTERACTION, "content": "晚霞", "emotion_vector": {}},
    )
    tagged_rec = await store.get_memory(tagged)
    untagged_rec = await store.get_memory(untagged)
    await store.close()
    assert tagged_rec is not None and tagged_rec["source"] == SOURCE_SELF
    assert tagged_rec["certainty"] == CERTAINTY_PROBABLE
    assert untagged_rec is not None and untagged_rec["source"] == SOURCE_USER
    assert untagged_rec["certainty"] == CERTAINTY_CERTAIN


@pytest.mark.asyncio
async def test_old_db_provenance_backfill_is_idempotent(tmp_path: Path) -> None:
    """旧库缺 source/certainty 列：启动补列 + 按 kind 回填；跑两次结果一致。"""
    import json
    import sqlite3

    db = tmp_path / "old.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, created_ts REAL, level TEXT,"
        " kind TEXT, content TEXT, emotion_vector TEXT, importance REAL,"
        " access_count INTEGER, last_access_ts REAL, protected INTEGER,"
        " detail_level REAL, narrative TEXT)"
    )
    con.execute(
        "INSERT INTO memories (created_ts, level, kind, content, emotion_vector,"
        " importance, access_count, protected, detail_level, narrative)"
        " VALUES (1.0, 'shallow', 'interaction', '我的生日是5月21日', ?, 0.8, 0, 0, 1.0, '生日')",
        (json.dumps({"chat": 0.5}),),
    )
    con.execute(
        "INSERT INTO memories (created_ts, level, kind, content, emotion_vector,"
        " importance, access_count, protected, detail_level, narrative)"
        " VALUES (2.0, 'shallow', 'expression', '我在呢', ?, 0.1, 0, 0, 1.0, '我在呢')",
        (json.dumps({}),),
    )
    con.commit()
    con.close()

    rows: dict[str, dict[str, object]] = {}
    for _ in range(2):  # 跑两次：幂等（跑 N 次 = 跑 1 次）
        store = HeartbeatStore(db)
        await store.start()
        rows = {str(r["content"]): r for r in await store.iterate_memories()}
        await store.close()

    assert rows["我的生日是5月21日"]["source"] == SOURCE_USER
    assert rows["我的生日是5月21日"]["certainty"] == CERTAINTY_CERTAIN
    assert rows["我在呢"]["source"] == SOURCE_SELF


# ── 认领状态（P3-V）：默认是她的记忆；拒绝认领只由她的动作产生 ──
def test_claim_default_is_claimed() -> None:
    """缺 claim_status 的旧记录默认 claimed——老库的记忆全部仍可用。"""
    legacy = MemoryRecord.from_dict(
        {"created_ts": 1.0, "kind": KIND_INTERACTION, "content": "生日", "emotion_vector": {}}
    )
    assert legacy.claim_status == CLAIM_CLAIMED
    explicit = MemoryRecord.from_dict(
        {
            "created_ts": 1.0,
            "kind": KIND_INTERACTION,
            "content": "不认的事",
            "emotion_vector": {},
            "claim_status": CLAIM_REJECTED,
        }
    )
    assert explicit.claim_status == CLAIM_REJECTED
    assert MemoryRecord.from_dict(explicit.to_dict()) == explicit


@pytest.mark.asyncio
async def test_add_memory_defaults_claimed(tmp_path: Path) -> None:
    """写入默认 claimed：认领是能力，落库即给她（不是先扣下再申请）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await store.add_memory(
        1.0,
        {"level": LEVEL_SHALLOW, "kind": KIND_INTERACTION, "content": "晚霞", "emotion_vector": {}},
    )
    rec = await store.get_memory(mid)
    await store.close()
    assert rec is not None and rec["claim_status"] == CLAIM_CLAIMED


@pytest.mark.asyncio
async def test_old_db_claim_backfill_and_reject(tmp_path: Path) -> None:
    """旧库缺 claim_status 列：启动补列 + 一律回填 claimed（幂等）；她可否决。"""
    import json
    import sqlite3

    db = tmp_path / "old_claim.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, created_ts REAL, level TEXT,"
        " kind TEXT, content TEXT, emotion_vector TEXT, importance REAL,"
        " access_count INTEGER, last_access_ts REAL, protected INTEGER,"
        " detail_level REAL, narrative TEXT)"
    )
    con.execute(
        "INSERT INTO memories (created_ts, level, kind, content, emotion_vector,"
        " importance, access_count, protected, detail_level, narrative)"
        " VALUES (1.0, 'shallow', 'interaction', '我的生日是5月21日', ?, 0.8, 0, 0, 1.0, '生日')",
        (json.dumps({"chat": 0.5}),),
    )
    con.commit()
    con.close()

    rows: list[dict[str, Any]] = []
    for _ in range(2):  # 跑两次：幂等（跑 N 次 = 跑 1 次）
        store = HeartbeatStore(db)
        await store.start()
        rows = await store.iterate_memories()
        await store.close()
    assert rows[0]["claim_status"] == CLAIM_CLAIMED

    # 她的动作：拒绝认领（落库往返）
    store = HeartbeatStore(db)
    await store.start()
    mid = int(rows[0]["id"])
    await store.set_claim_status(mid, CLAIM_REJECTED)
    fetched = await store.get_memory(mid)
    await store.close()
    assert fetched is not None and fetched["claim_status"] == CLAIM_REJECTED
