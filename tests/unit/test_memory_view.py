"""记忆浏览器数据读取单测（只读路径，不涉及 Qt 实例）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.identity import IDENTITY_SEEDS
from elysia.memory.levels import (
    CERTAINTY_PROBABLE,
    KIND_INTERACTION,
    KIND_SELF,
    LEVEL_SHALLOW,
    SOURCE_INFERENCE,
    SOURCE_SELF,
)
from elysia.soul.expression_service import ensure_identity_seeds
from elysia.tools.memory_view import (
    COLUMNS,
    TAG_CANDIDATE,
    TAG_SEED,
    TAG_SELF,
    _cluster_sizes,
    _in_identity,
    _is_candidate,
    _is_seed,
    _tag_text,
    load_current_mood,
    load_index_strengths,
    load_memories,
)


def test_load_memories_missing_db(tmp_path: Path) -> None:
    assert load_memories(tmp_path / "nope.db") == []
    assert load_index_strengths(tmp_path / "nope.db") == {}
    assert load_current_mood(tmp_path / "nope.db") == {}


@pytest.mark.asyncio
async def test_load_memories_parses_fields(tmp_path: Path) -> None:
    db = tmp_path / "heartbeat.db"
    store = HeartbeatStore(db)
    await store.start()
    mid = await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "我的生日是11月11日",
            "emotion_vector": {"chat": 0.5},
            "importance": 0.8,
            "protected": True,
            "narrative": "生日",
        },
    )
    await store.close()

    records = load_memories(db)
    assert len(records) == 1
    rec = records[0]
    assert rec.id == mid
    assert rec.emotion_vector == {"chat": 0.5}  # JSON 已解析为字典
    assert rec.protected is True
    assert rec.superseded_by is None


@pytest.mark.asyncio
async def test_load_index_strengths(tmp_path: Path) -> None:
    db = tmp_path / "heartbeat.db"
    store = HeartbeatStore(db)
    await store.start()
    mid = await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "往事",
            "emotion_vector": {"chat": 0.5},
            "importance": 0.5,
            "narrative": "往事",
        },
    )
    await store.add_memory_index(memory_id=mid, path_key="main", strength=0.42, emotions=0.0)
    await store.close()

    assert load_index_strengths(db) == {mid: 0.42}


@pytest.mark.asyncio
async def test_load_current_mood_from_latest_beat(tmp_path: Path) -> None:
    db = tmp_path / "heartbeat.db"
    store = HeartbeatStore(db)
    await store.start()
    await store.append(1.0, "soul", {"feelings": {"chat": 0.7, "miss": 0.2}})
    await store.append(2.0, "soul", {"feelings": {"chat": 0.3}})
    await store.append(2.5, "body", {"feelings": {"chat": 0.9}})  # 身体拍不算
    await store.close()

    assert load_current_mood(db) == {"chat": 0.3}  # 取最近一次灵魂拍


def test_load_current_mood_tolerates_bad_payload(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "heartbeat.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE heartbeats (id INTEGER PRIMARY KEY, ts REAL, beat_type TEXT, payload TEXT)"
    )
    con.execute(
        "INSERT INTO heartbeats (ts, beat_type, payload) VALUES (1.0, 'soul', ?)", (json.dumps({}),)
    )
    con.commit()
    con.close()
    assert load_current_mood(db) == {}  # 无 feelings 字段 → 空心境


def test_load_memories_tolerates_missing_superseded_column(tmp_path: Path) -> None:
    """旧库（未执行迁移、无 superseded_by 列）仍可观测，按"未取代"处理。"""
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
        " VALUES (1.0, 'shallow', 'interaction', '旧记忆', ?, 0.5, 0, 0, 1.0, '旧记忆')",
        (json.dumps({"chat": 0.5}),),
    )
    con.commit()
    con.close()

    records = load_memories(db)
    assert len(records) == 1
    assert records[0].content == "旧记忆"
    assert records[0].superseded_by is None


# ── 第八节 S4：标签列（身份段的两端一眼可见）──────────────
def _memory(content: str, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "level": LEVEL_SHALLOW,
        "kind": KIND_INTERACTION,
        "content": content,
        "emotion_vector": {"chat": 0.5},
        "importance": 0.5,
        "narrative": content,
        "source": SOURCE_SELF,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_tag_text_marks_self_and_candidate(tmp_path: Path) -> None:
    """标签列：自我（她已认领）/ 候选（`source=inference`，待她认领）/ 空（寻常经历）。"""
    db = tmp_path / "heartbeat.db"
    store = HeartbeatStore(db)
    await store.start()
    claimed_id = await store.add_memory(1.0, _memory("我在意的是每一个和我相遇的人"))
    candidate_id = await store.add_memory(
        2.0,
        _memory(
            "今天又提到在意的人",
            source=SOURCE_INFERENCE,
            certainty=CERTAINTY_PROBABLE,
        ),
    )
    plain_id = await store.add_memory(3.0, _memory("今天天气不错"))
    await store.mark_as_self(claimed_id)  # 她认领 → 升格为自我
    await store.close()

    by_id = {rec.id: rec for rec in load_memories(db)}
    assert _tag_text(by_id[claimed_id]) == TAG_SELF
    assert _tag_text(by_id[candidate_id]) == TAG_CANDIDATE
    assert _is_candidate(by_id[candidate_id])
    assert _tag_text(by_id[plain_id]) == ""
    assert "标签" in COLUMNS


@pytest.mark.asyncio
async def test_cluster_sizes_groups_reworded_same_event(tmp_path: Path) -> None:
    """A2「簇」列：同一件事的换说法归到一簇，孤例自成一簇。"""
    db = tmp_path / "heartbeat.db"
    store = HeartbeatStore(db)
    await store.start()
    long_form = await store.add_memory(1.0, _memory("那我在告诉你哦，我的生日是5月21日，要记好哦"))
    short_form = await store.add_memory(2.0, _memory("那我的生日呢"))
    alone = await store.add_memory(3.0, _memory("你喜欢看晚霞"))
    await store.close()

    sizes = _cluster_sizes(load_memories(db))
    assert sizes[long_form] == 2
    assert sizes[short_form] == 2  # 换说法也归同一簇（Jaccard 判不出，覆盖判得出）
    assert sizes[alone] == 1
    assert "簇" in COLUMNS


@pytest.mark.asyncio
async def test_tag_text_marks_seed_and_identity_quota(tmp_path: Path) -> None:
    """S5：程序种入的出生设定自成一档（与"她自己认领的"分开），并计入身份段名额。"""
    db = tmp_path / "heartbeat.db"
    store = HeartbeatStore(db)
    await store.start()
    seeded = await ensure_identity_seeds(store, 1.0)
    await store.add_memory(2.0, _memory("今天天气不错"))
    await store.close()

    records = load_memories(db)
    seeds = [rec for rec in records if _is_seed(rec)]
    assert len(seeds) == seeded == len(IDENTITY_SEEDS)
    assert all(_tag_text(rec) == TAG_SEED for rec in seeds)
    # 身份段名额：在册的自我认知（含种子）都占位——"她此刻是谁"看得见
    assert sum(1 for rec in records if _in_identity(rec)) == len(IDENTITY_SEEDS)
    assert _in_identity(seeds[0])  # 种子默认在册
    assert not any(_is_seed(rec) for rec in records if rec.kind != KIND_SELF)
