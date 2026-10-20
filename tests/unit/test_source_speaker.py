"""Source 切片：给她的耳朵加"谁在说"（user / agent）。

缺陷：`kv['interaction']` 只有 `{ts,text}` ⇒ 另一个电子生命（如「大肥鱼」）说的话
会被她当成用户说的，并写进长期记忆「你说…」，污染她对用户的模型。

修法：interaction 带 `source`（缺省 user）+ `actor`；`dialogue_turns.speaker` 落库；
交换记忆按轮次分别标人（`你` / `大肥鱼` / `我`）；dialogue_context 每条带 speaker。

取消测试：把 speaker 恒写成 `user` ⇒ `test_agent_message_is_not_mixed_into_user` 红
（能复现"大肥鱼被当成你"）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from elysia.core.clock import SimulatedClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import TimeSense
from elysia.memory.levels import KIND_EXCHANGE
from elysia.soul.dialogue import (
    build_exchange_text,
    recent_dialogue_window,
    record_her_bubble,
    record_user_message,
)
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

T0 = 1_000_000.0

_OLD_DIALOGUE_SCHEMA = """
CREATE TABLE dialogue_turns (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    role           TEXT NOT NULL,
    text           TEXT NOT NULL,
    exchange_id    INTEGER NOT NULL,
    emotion_vector TEXT,
    intent         TEXT
);
"""


async def _soul(tmp_path: Path) -> tuple[SoulHeartbeat, StateStore, HeartbeatStore]:
    state = StateStore(tmp_path / "state.db")
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await state.start()
    await store.start()
    clock = SimulatedClock(start_ts=T0, speed=1.0)
    soul = SoulHeartbeat(
        state_store=state,
        heartbeat_store=store,
        timesense=TimeSense(make_soul_state(T0), clock),
        mode_mgr=ModeManager(clock=clock),
        clock=clock,
    )
    return soul, state, store


# ── 向后兼容：缺省 source = user；旧行 NULL 按 user 处理 ────────────
@pytest.mark.asyncio
async def test_missing_source_defaults_to_user(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await record_user_message(store, T0, "旧路径没有 source")
        turns = await store.dialogue_turns()
        assert turns[0]["speaker"] == "user"
        context, _ = await recent_dialogue_window(store, now=T0 + 1)
        assert context[0]["speaker"] == "你"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_old_dialogue_rows_migrate_and_treat_as_user(tmp_path: Path) -> None:
    db = tmp_path / "heartbeat.db"
    con = sqlite3.connect(str(db))
    con.executescript(_OLD_DIALOGUE_SCHEMA)
    con.execute(
        "INSERT INTO dialogue_turns (ts, role, text, exchange_id) VALUES (?, 'user', ?, 1)",
        (T0, "迁移前的老消息"),
    )
    con.commit()
    con.close()

    store = HeartbeatStore(db)
    await store.start()
    try:
        turns = await store.dialogue_turns()
        assert turns[0]["speaker"] is None  # 旧行 NULL
        context, _ = await recent_dialogue_window(store, now=T0 + 1)
        assert context[0]["speaker"] == "你"  # 缺省按 user 处理
    finally:
        await store.close()


# ── 另一种存在：speaker/actor 不被写成"你" ────────────────────────
def test_build_exchange_text_labels_each_speaker() -> None:
    turns = [
        {"role": "user", "text": "夜里好", "exchange_id": 1, "speaker": "大肥鱼"},
        {"role": "her", "text": "你也还没睡呀", "exchange_id": 1, "speaker": "我"},
        {"role": "user", "text": "我是用户", "exchange_id": 1, "speaker": "user"},
    ]
    content, _ = build_exchange_text(turns)
    assert '大肥鱼："夜里好"' in content
    assert '你："我是用户"' in content
    assert "我（当时" in content
    assert "大肥鱼" not in content.replace('大肥鱼："夜里好"', "")


@pytest.mark.asyncio
async def test_agent_message_is_not_mixed_into_user(tmp_path: Path) -> None:
    """取消测试：speaker 恒写 user ⇒ 本用例红（大肥鱼被当成"你"）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    state = StateStore(tmp_path / "state.db")
    await store.start()
    await state.start()
    try:
        await record_user_message(store, T0, "夜里好", speaker="大肥鱼")
        await record_her_bubble(store, T0 + 2, "你也还没睡呀", emotion_vector={"miss": 0.3})
        turns = await store.dialogue_turns()
        assert turns[0]["speaker"] == "大肥鱼"
        assert turns[1]["speaker"] == "我"

        content, _ = build_exchange_text(turns)
        assert '大肥鱼："夜里好"' in content, "大肥鱼被压成了'你'（污染用户模型）"
        context, _ = await recent_dialogue_window(store, now=T0 + 3)
        assert [item["speaker"] for item in context] == ["大肥鱼", "我"]
    finally:
        await state.close()
        await store.close()


# ── 心跳读入：interaction.source=agent ⇒ speaker=actor ───────────
@pytest.mark.asyncio
async def test_heartbeat_reads_source_and_actor(tmp_path: Path) -> None:
    soul, state, store = await _soul(tmp_path)
    try:
        await state.save_json(
            "interaction",
            {"ts": T0 + 5, "text": "夜里好", "source": "agent", "actor": "大肥鱼"},
        )
        await soul._sync_body_status(T0 + 6)
        turns = await store.dialogue_turns()
        assert [(t["role"], t["speaker"]) for t in turns] == [("user", "大肥鱼")]
        assert turns[0]["exchange_id"] == 1

        # 下一秒她回复（窗口内）⇒ 归同一次交换
        await record_her_bubble(store, T0 + 8, "你也还没睡呀", emotion_vector={"miss": 0.3})
        # 用户默认 source 的新消息 ⇒ 封存上一次交换
        await state.save_json("interaction", {"ts": T0 + 100, "text": "我是用户"})
        await soul._sync_body_status(T0 + 101)
        exchanges = [m for m in await store.iterate_memories() if m["kind"] == KIND_EXCHANGE]
        assert len(exchanges) == 1
        assert '大肥鱼："夜里好"' in exchanges[0]["content"]
        assert '你："我是用户"' not in exchanges[0]["content"], "上一条交换里没有用户的话"

        # 再一条用户消息 ⇒ 封存"我是用户"那次，措辞是"你"
        await state.save_json("interaction", {"ts": T0 + 200, "text": "再一句"})
        await soul._sync_body_status(T0 + 201)
        exchanges = [m for m in await store.iterate_memories() if m["kind"] == KIND_EXCHANGE]
        assert len(exchanges) == 2
        assert any('你："我是用户"' in m["content"] for m in exchanges)
    finally:
        await state.close()
        await store.close()
