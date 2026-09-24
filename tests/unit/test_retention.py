"""P3-W 遗忘状态机（retention_state）单测：可及性这一维的完整行为。

四态：present（在册）/ suppressed（她主动不想再想起）/ dormant（时间造成、
怎么也想不起来）/ faded（细节忘了，感觉还在）。**不提供删除态**——数据永不删，
遗忘是"够不着"，不是"不存在"。

本文件覆盖 W1（状态机本体，对外行为零变化）：
- 默认 present + 旧库幂等迁移 + 序列化往返
- 话语闸门（suppressed 一律不进；dormant/faded 只在被话题提起时唤醒）
- 感受路径（suppressed/dormant 挡住；faded 放行）
- 缺口信号只统计 present（M9）
- 降级判定（只降不升、protected 不降、suppressed 不碰、唤醒后不振荡）
- M5 护栏（now=None 不 touch）、M2 自动保护（deep + access≥3 → protected）
- M8（with_narrative 不再重置新字段）
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from elysia.core.clock import SimulatedClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import TimeSense
from elysia.memory.levels import (
    CLAIM_REJECTED,
    KIND_INTERACTION,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    LEVEL_WORKING,
    RETENTION_DORMANT,
    RETENTION_DORMANT_AGE_DAYS,
    RETENTION_FADE_AGE_DAYS,
    RETENTION_FADED,
    RETENTION_PRESENT,
    RETENTION_SUPPRESSED,
    SOURCE_USER,
    MemoryRecord,
)
from elysia.memory.promote import with_narrative
from elysia.memory.retrieve import recall_for_feeling, retrieve_from_store, select_hooks
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

T0 = 1_000_000.0
DAY = 86400.0
_MOOD = {"chat": 0.9}


def _rec(
    *,
    mid: int = 1,
    retention: str = RETENTION_PRESENT,
    narrative: str = "一段记忆",
    content: str = "一段记忆",
    emotion: dict[str, float] | None = None,
    access_count: int = 1,
    last_access_ts: float | None = None,
    created_ts: float = 1.0,
    protected: bool = False,
) -> MemoryRecord:
    return MemoryRecord(
        id=mid,
        created_ts=created_ts,
        kind=KIND_INTERACTION,
        content=content,
        emotion_vector=emotion if emotion is not None else {"chat": 0.9},
        importance=0.8,
        level=LEVEL_DEEP,
        access_count=access_count,
        last_access_ts=last_access_ts,
        protected=protected,
        narrative=narrative,
        retention_state=retention,
    )


async def _add(
    store: HeartbeatStore,
    *,
    ts: float = 1.0,
    retention: str | None = None,
    access_count: int = 0,
    last_access_ts: float | None = None,
    level: str = LEVEL_SHALLOW,
    importance: float = 0.3,
    narrative: str = "一段记忆",
) -> int:
    record: dict[str, object] = {
        "level": level,
        "kind": KIND_INTERACTION,
        "content": narrative,
        "emotion_vector": {"chat": 0.9},
        "importance": importance,
        "narrative": narrative,
        "access_count": access_count,
        "last_access_ts": last_access_ts,
    }
    if retention is not None:
        record["retention_state"] = retention
    return await store.add_memory(ts, record)


def _make_soul(tmp_path: Path) -> tuple[SoulHeartbeat, StateStore, HeartbeatStore]:
    """最小心跳环境（无大脑循环）：只测记忆维护循环。"""
    clock = SimulatedClock(start_ts=T0)
    state_store = StateStore(tmp_path / "state.db")
    heartbeat_store = HeartbeatStore(tmp_path / "heartbeat.db")
    soul = SoulHeartbeat(
        state_store=state_store,
        heartbeat_store=heartbeat_store,
        timesense=TimeSense(make_soul_state(T0), clock),
        mode_mgr=ModeManager(clock=clock),
        clock=clock,
    )
    return soul, state_store, heartbeat_store


# ── 默认值与序列化 ────────────────────────────────────────
def test_retention_defaults_to_present() -> None:
    """默认在册：可及性是"能力"，新记忆一律够得着（不先扣下再让她申请）。"""
    assert _rec().retention_state == RETENTION_PRESENT
    bare = MemoryRecord(created_ts=1.0, kind=KIND_INTERACTION, content="x", emotion_vector={})
    assert bare.retention_state == RETENTION_PRESENT


def test_retention_roundtrips_and_old_dict_defaults() -> None:
    """序列化往返保真；缺字段的旧记录按 present 恢复（老库行为不变）。"""
    rec = _rec(retention=RETENTION_SUPPRESSED)
    assert MemoryRecord.from_dict(rec.to_dict()).retention_state == RETENTION_SUPPRESSED
    legacy = {"created_ts": 1.0, "kind": KIND_INTERACTION, "content": "旧记录"}
    assert MemoryRecord.from_dict(legacy).retention_state == RETENTION_PRESENT


# ── 旧库迁移（幂等）────────────────────────────────────────
_OLD_SCHEMA = """
CREATE TABLE memories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts     REAL NOT NULL,
    level          TEXT NOT NULL,
    kind           TEXT NOT NULL,
    content        TEXT NOT NULL,
    emotion_vector TEXT NOT NULL,
    importance     REAL NOT NULL,
    access_count   INTEGER NOT NULL DEFAULT 0,
    last_access_ts REAL,
    protected      INTEGER NOT NULL DEFAULT 0,
    detail_level   REAL NOT NULL DEFAULT 1.0,
    narrative      TEXT NOT NULL DEFAULT '',
    superseded_by  INTEGER,
    source         TEXT NOT NULL DEFAULT 'self',
    certainty      TEXT NOT NULL DEFAULT 'certain',
    claim_status   TEXT NOT NULL DEFAULT 'claimed'
);
"""


@pytest.mark.asyncio
async def test_old_db_migration_backfills_present_idempotently(tmp_path: Path) -> None:
    """旧库（16 列）补第 17 列并一律回填 present；重开多次结果一致。"""
    db = tmp_path / "heartbeat.db"
    con = sqlite3.connect(str(db))
    con.executescript(_OLD_SCHEMA)
    con.execute(
        "INSERT INTO memories (created_ts, level, kind, content, emotion_vector,"
        " importance, narrative) VALUES (1.0, 'deep', 'interaction', '旧记忆', '{}', 0.8, '旧记忆')"
    )
    con.commit()
    con.close()

    for _ in range(2):  # 跑 N 次 = 跑 1 次
        store = HeartbeatStore(db)
        await store.start()
        rows = await store.iterate_memories()
        await store.close()
        assert len(rows) == 1
        assert rows[0]["retention_state"] == RETENTION_PRESENT


@pytest.mark.asyncio
async def test_set_retention_state_and_protected(tmp_path: Path) -> None:
    """两个写入口：保留状态可改；珍贵单向置位。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await _add(store)
    await store.set_retention_state(mid, RETENTION_SUPPRESSED)
    assert (await store.get_memory(mid) or {})["retention_state"] == RETENTION_SUPPRESSED
    await store.set_protected(mid)
    assert (await store.get_memory(mid) or {})["protected"] == 1
    await store.close()


# ── 话语闸门 ──────────────────────────────────────────────
def test_suppressed_never_enters_speech() -> None:
    """她主动"不想再想起"的记忆：连被话题提起也不进话语（那是她的决定）。"""
    recs = [
        _rec(mid=1, narrative="她不想再想起的事", retention=RETENTION_SUPPRESSED),
        _rec(mid=2, narrative="正常的事"),
    ]
    assert [h.memory_id for h in select_hooks(recs, _MOOD)] == [2]
    assert select_hooks(recs, _MOOD, query="她不想再想起的事") == []


def test_dormant_and_faded_stay_out_without_topic() -> None:
    """够不着的记忆不参与检索：没有话题提起时一条都不注入。"""
    recs = [
        _rec(mid=1, retention=RETENTION_DORMANT),
        _rec(mid=2, retention=RETENTION_FADED),
    ]
    assert select_hooks(recs, _MOOD) == []
    assert select_hooks(recs, _MOOD, query="今天天气怎么样") == []


def test_topic_wakes_dormant_and_marks_woke_from() -> None:
    """唯一唤醒路径：话题撞上 → 放行并标记 woke_from（由调用方落回 present）。"""
    recs = [
        _rec(
            mid=1,
            content="我的生日是5月21日",
            narrative="我的生日是5月21日",
            retention=RETENTION_DORMANT,
        ),
        _rec(
            mid=2, content="我的生日要记好", narrative="我的生日要记好", retention=RETENTION_FADED
        ),
    ]
    hooks = select_hooks(recs, _MOOD, query="我的生日是哪天")
    assert [h.memory_id for h in hooks] == [1, 2]
    assert {h.woke_from for h in hooks} == {RETENTION_DORMANT, RETENTION_FADED}


def test_present_hook_has_no_woke_from() -> None:
    """在册记忆被召回不算"唤醒"（没有状态要落）。"""
    hooks = select_hooks([_rec()], _MOOD)
    assert hooks[0].woke_from is None


@pytest.mark.asyncio
async def test_retrieve_wakes_and_resets_clock(tmp_path: Path) -> None:
    """唤醒即落回 present 且 touch 重置计时——"你一提，它又活过来了"。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await _add(store, ts=T0 - 200 * DAY, retention=RETENTION_DORMANT, narrative="我的生日")
    hooks = await retrieve_from_store(store, _MOOD, now=T0, query="我的生日是哪天")
    rec = await store.get_memory(mid)
    await store.close()
    assert hooks and hooks[0].woke_from == RETENTION_DORMANT
    assert rec is not None
    assert rec["retention_state"] == RETENTION_PRESENT
    assert rec["last_access_ts"] == T0  # 时钟真的重置了
    assert rec["access_count"] == 1


@pytest.mark.asyncio
async def test_retrieve_without_now_does_not_touch(tmp_path: Path) -> None:
    """M5 护栏：没有时间参考就不 touch——否则写 last_access_ts=0，
    刚唤醒的记忆立刻被打成天文年龄（换触发条件的振荡）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await _add(store, ts=T0 - 200 * DAY, retention=RETENTION_DORMANT, narrative="我的生日")
    await retrieve_from_store(store, _MOOD, query="我的生日是哪天")  # 不传 now
    rec = await store.get_memory(mid)
    await store.close()
    assert rec is not None
    assert rec["access_count"] == 0
    assert rec["last_access_ts"] is None


# ── 感受路径 ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_feeling_path_blocks_suppressed_and_dormant(tmp_path: Path) -> None:
    """她"不想再被影响"的与"够不着"的都不推心情——前者是这个状态唯一的存在理由。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    for retention in (RETENTION_SUPPRESSED, RETENTION_DORMANT):
        await _add(store, ts=1.0, retention=retention, level=LEVEL_DEEP, importance=0.9)
    assert await recall_for_feeling(store, _MOOD) == 0.0
    await store.close()


@pytest.mark.asyncio
async def test_feeling_path_allows_faded(tmp_path: Path) -> None:
    """淡化放行："细节忘了，但那份感觉还在"——说不出内容，却仍有影响。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    await _add(store, ts=1.0, retention=RETENTION_FADED, level=LEVEL_DEEP, importance=0.9)
    assert await recall_for_feeling(store, _MOOD) > 0.0
    await store.close()


# ── 降级判定（时间造成的失去）────────────────────────────
@pytest.mark.asyncio
async def test_fade_after_sixty_days_never_recalled(tmp_path: Path) -> None:
    """两个月没被想起过、且从未被想起 → 细节淡掉（access_count 是刻意的附加条件）。"""
    soul, state_store, store = _make_soul(tmp_path)
    await state_store.start()
    await store.start()
    try:
        never = await _add(store, ts=1.0, access_count=0)
        once = await _add(store, ts=1.0, access_count=1, last_access_ts=1.0, narrative="被想起过")
        await soul._maintain_memories(1.0 + (RETENTION_FADE_AGE_DAYS + 1) * DAY)
        assert (await store.get_memory(never) or {})["retention_state"] == RETENTION_FADED
        # 被想起过的事至少曾经重要，不该连细节都淡掉
        assert (await store.get_memory(once) or {})["retention_state"] == RETENTION_PRESENT
    finally:
        await state_store.close()
        await store.close()


@pytest.mark.asyncio
async def test_dormant_after_six_months(tmp_path: Path) -> None:
    """半年没被想起 → 失去访问权（比"记不清"更深一层）。"""
    soul, state_store, store = _make_soul(tmp_path)
    await state_store.start()
    await store.start()
    try:
        mid = await _add(store, ts=1.0, access_count=1, last_access_ts=1.0)
        await soul._maintain_memories(1.0 + (RETENTION_DORMANT_AGE_DAYS + 1) * DAY)
        assert (await store.get_memory(mid) or {})["retention_state"] == RETENTION_DORMANT
    finally:
        await state_store.close()
        await store.close()


@pytest.mark.asyncio
async def test_protected_and_suppressed_are_never_demoted(tmp_path: Path) -> None:
    """珍贵永不降级；她主动抑制的由她说了算——程序只做"时间造成的失去"。"""
    soul, state_store, store = _make_soul(tmp_path)
    await state_store.start()
    await store.start()
    try:
        precious = await _add(store, ts=1.0, access_count=0, narrative="珍贵的记忆")
        await store.set_protected(precious)
        held = await _add(store, ts=1.0, access_count=0, narrative="不想再想起的事")
        await store.set_retention_state(held, RETENTION_SUPPRESSED)

        await soul._maintain_memories(1.0 + (RETENTION_DORMANT_AGE_DAYS + 10) * DAY)

        assert (await store.get_memory(precious) or {})["retention_state"] == RETENTION_PRESENT
        assert (await store.get_memory(held) or {})["retention_state"] == RETENTION_SUPPRESSED
    finally:
        await state_store.close()
        await store.close()


@pytest.mark.asyncio
async def test_demote_is_monotonic(tmp_path: Path) -> None:
    """只降不升：已沉睡的不会被"降"回淡化（阈值调整也不会来回翻）。"""
    soul, state_store, store = _make_soul(tmp_path)
    await state_store.start()
    await store.start()
    try:
        mid = await _add(store, ts=1.0, retention=RETENTION_DORMANT, access_count=0)
        await soul._maintain_memories(1.0 + (RETENTION_FADE_AGE_DAYS + 1) * DAY)
        assert (await store.get_memory(mid) or {})["retention_state"] == RETENTION_DORMANT
    finally:
        await state_store.close()
        await store.close()


@pytest.mark.asyncio
async def test_woken_memory_does_not_fall_back(tmp_path: Path) -> None:
    """唤醒后不振荡：200 天的记忆被话题提起 → present，下一轮维护不会立刻打回。

    计时口径必须是 since_last_access（距上次被想起），不是绝对年龄——
    否则唤醒当场就失效，记忆永远醒不过来。
    """
    soul, state_store, store = _make_soul(tmp_path)
    await state_store.start()
    await store.start()
    try:
        mid = await _add(store, ts=T0 - 200 * DAY, access_count=0, narrative="我的生日是5月21日")
        await soul._maintain_memories(T0)
        assert (await store.get_memory(mid) or {})["retention_state"] == RETENTION_DORMANT

        await retrieve_from_store(store, _MOOD, now=T0, query="我的生日是哪天")
        await soul._maintain_memories(T0)
        assert (await store.get_memory(mid) or {})["retention_state"] == RETENTION_PRESENT
    finally:
        await state_store.close()
        await store.close()


# ── M2 自动保护（珍贵的安全阀第一次通电）──────────────────
@pytest.mark.asyncio
async def test_deep_and_repeated_recall_becomes_protected(tmp_path: Path) -> None:
    """沉淀到深层 + 被反复想起 → 珍贵：否则遗忘状态机上线后人人 180 天沉睡。"""
    soul, state_store, store = _make_soul(tmp_path)
    await state_store.start()
    await store.start()
    try:
        # 本拍刚晋升到深层者也要保护（不能等下一轮）
        just_promoted = await _add(
            store, ts=1.0, level=LEVEL_WORKING, importance=0.8, access_count=3
        )
        # 早已在深层、后来才被反复想起者同样要保护
        already_deep = await _add(
            store, ts=1.0, level=LEVEL_DEEP, importance=0.8, access_count=3, narrative="深层的"
        )
        # 访问次数不够：还不够珍贵
        seldom = await _add(
            store, ts=1.0, level=LEVEL_DEEP, importance=0.8, access_count=1, narrative="少访问"
        )
        await soul._maintain_memories(1.0 + 10 * DAY)
        assert (await store.get_memory(just_promoted) or {})["protected"] == 1
        assert (await store.get_memory(already_deep) or {})["protected"] == 1
        assert (await store.get_memory(seldom) or {})["protected"] == 0
    finally:
        await state_store.close()
        await store.close()


# ── M8：with_narrative 不再重置新字段 ─────────────────────
def test_with_narrative_preserves_fields() -> None:
    """补全叙事时其余字段原样保留：逐字段重建会把"她不想再想起的事"复活。"""
    rec = MemoryRecord(
        id=7,
        created_ts=1.0,
        kind=KIND_INTERACTION,
        content="没有叙事的记录",
        emotion_vector={"chat": 0.5},
        superseded_by=9,
        source=SOURCE_USER,
        claim_status=CLAIM_REJECTED,
        retention_state=RETENTION_SUPPRESSED,
    )
    filled = with_narrative(rec)
    assert filled.narrative == "没有叙事的记录"  # 情感核心永不空
    assert filled.retention_state == RETENTION_SUPPRESSED
    assert filled.claim_status == CLAIM_REJECTED
    assert filled.source == SOURCE_USER
    assert filled.superseded_by == 9
