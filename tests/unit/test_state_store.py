"""StateStore/HeartbeatStore 单测：WAL / 往返一致 / 崩溃恢复 / 写者存活 / 空洞检测。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import (
    TimeSenseState,
    from_payload,
    to_payload,
)


@pytest.mark.asyncio
async def test_state_store_wal_mode(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    await store.start()
    rows = await store.execute_raw("PRAGMA journal_mode")
    assert rows[0][0] == "wal"
    await store.close()


@pytest.mark.asyncio
async def test_json_roundtrip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    await store.start()
    await store.save_json("timesense", {"a": 1, "b": "中文"})
    loaded = await store.load_json("timesense")
    assert loaded == {"a": 1, "b": "中文"}
    assert await store.load_json("missing", default=None) is None
    await store.close()


@pytest.mark.asyncio
async def test_timesense_fields_roundtrip(tmp_path: Path) -> None:
    """D4 验证：TimeSenseState 持久化往返字段完全一致。"""
    store = StateStore(tmp_path / "state.db")
    await store.start()
    state = TimeSenseState(
        first_existence_ts=1.0,
        last_soul_beat_ts=2.0,
        last_body_online_ts=3.0,
        last_interaction_ts=4.0,
        body_away_start_ts=5.0,
    )
    await store.save_json("timesense", to_payload(state))
    restored = from_payload(await store.load_json("timesense"))
    assert restored == state
    await store.close()


@pytest.mark.asyncio
async def test_heartbeat_append_and_count(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    for i in range(10):
        await store.append(1000.0 + i, "soul", {"seq": i})
    for i in range(3):
        await store.append(1100.0 + i, "body", {})
    assert await store.count("soul") == 10
    assert await store.count("body") == 3
    assert await store.count() == 13
    await store.close()


@pytest.mark.asyncio
async def test_gaps_detection(tmp_path: Path) -> None:
    """心跳空洞检测：相邻间隔 > 阈值 → 空洞区间。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    for i in range(5):
        await store.append(1000.0 + i, "soul", {})
    await store.append(2000.0, "soul", {})  # 与 1004.0 相隔 996s
    await store.append(2001.0, "soul", {})
    gaps = await store.gaps(threshold_s=10.0)
    assert gaps == [(1004.0, 2000.0)]
    assert await store.gaps(threshold_s=5000.0) == []
    await store.close()


@pytest.mark.asyncio
async def test_writer_survives_failed_op(tmp_path: Path) -> None:
    """写者纪律：单个写操作异常不杀死写者，后续写入正常（实测教训固化）。"""
    store = StateStore(tmp_path / "state.db")
    await store.start()

    def bad_op(conn: sqlite3.Connection) -> None:
        raise RuntimeError("boom")

    await store.submit(bad_op)
    await store.save_json("k", "v")  # 排在 bad 之后
    await store.close()  # join 排空：bad 被捕获记日志，save 正常落盘

    # 独立连接验证落盘内容（save_json 存 JSON 序列化值，含引号）
    conn = sqlite3.connect(tmp_path / "state.db")
    rows = conn.execute("SELECT value FROM kv WHERE key = 'k'").fetchall()
    assert rows == [('"v"',)]
    conn.close()


@pytest.mark.asyncio
async def test_close_flushes_pending_writes(tmp_path: Path) -> None:
    """优雅关闭：写入后立即 close，队列排空不丢数据。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    for i in range(50):
        await store.append(1000.0 + i, "soul", {"seq": i})
    await store.close()
    conn = sqlite3.connect(tmp_path / "heartbeat.db")
    count = conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]
    assert count == 50
    conn.close()
