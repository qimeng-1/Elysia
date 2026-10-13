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
    """写者纪律：单个写操作异常**向上抛出**（不伪装成功），且写者不死。

    F6（19.4 缺点 5）：旧实现用 `fut.set_result(None)` 把失败伪装成成功——
    `await store.submit(bad_op)` 返回正常，调用者**无从分辨**。本用例同时锁住两件事：
    ① 失败被看见（抛 `RuntimeError`）；② 写者存活（后续写入照常落盘）。
    """
    store = StateStore(tmp_path / "state.db")
    await store.start()

    def bad_op(conn: sqlite3.Connection) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await store.submit(bad_op)

    await store.save_json("k", "v")  # 写者仍存活：排在 bad 之后照常落盘
    await store.close()

    # 独立连接验证落盘内容（save_json 存 JSON 序列化值，含引号）
    conn = sqlite3.connect(tmp_path / "state.db")
    rows = conn.execute("SELECT value FROM kv WHERE key = 'k'").fetchall()
    assert rows == [('"v"',)]
    conn.close()


@pytest.mark.asyncio
async def test_submit_ret_propagates_failure(tmp_path: Path) -> None:
    """`submit_ret` 同样不得伪装成功（F6）：失败要抛，而不是返回 `None` 冒充结果。"""
    store = StateStore(tmp_path / "state.db")
    await store.start()

    def bad_op(conn: sqlite3.Connection) -> str:
        raise RuntimeError("boom-ret")

    with pytest.raises(RuntimeError, match="boom-ret"):
        await store.submit_ret(bad_op)
    await store.close()


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


# ── F7：关闭序（不再以 writer.cancel() 为主手段）──────────────────


@pytest.mark.asyncio
async def test_close_stops_writer_by_sentinel_not_cancel(tmp_path: Path) -> None:
    """F7：关闭以**哨兵**让写者自行退出，不再以 `cancel()` 为主手段。

    根因：`asyncio.to_thread` 里的 sqlite 调用**无法被取消**——硬 cancel 会让它
    继续在线程里跑，而我们紧接着就在**同一个连接**上 `close()`，那才是竞态
    （19.4 缺点 5）。所以这里断言"写者是**自行返回**的，而不是被取消的"。
    """
    store = StateStore(tmp_path / "state.db")
    await store.start()
    writer = store._writer
    assert writer is not None

    await store.close()

    assert writer.done(), "写者应已退出"
    assert not writer.cancelled(), "写者应当是自行返回（哨兵），而不是被 cancel"


@pytest.mark.asyncio
async def test_write_after_close_is_rejected(tmp_path: Path) -> None:
    """关停后拒收新写并**说真话**（抛错），而不是静默丢弃（与 F6 同一原则）。"""
    store = StateStore(tmp_path / "state.db")
    await store.start()
    await store.close()

    def noop(conn: sqlite3.Connection) -> None:
        return None

    with pytest.raises(RuntimeError, match="已关闭"):
        await store.submit(noop)
    with pytest.raises(RuntimeError, match="已关闭"):
        await store.submit_ret(noop)


@pytest.mark.asyncio
async def test_close_is_idempotent(tmp_path: Path) -> None:
    """重复 close 不该炸（关闭序可能被多处触发：正常退出 / 异常退出 / 测试收尾）。"""
    store = StateStore(tmp_path / "state.db")
    await store.start()
    await store.close()
    await store.close()
