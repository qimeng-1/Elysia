"""检查点单测：快照创建 / 回滚一致性 / 保留清理 / 紧急冻结。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from elysia.core.checkpoint import CHECKPOINT_KEEP, CheckpointManager


def _make_db(path: Path, rows: int) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("DELETE FROM t")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"v{i}",) for i in range(rows)])
    conn.commit()
    conn.close()


def _count_rows(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    conn.close()
    return n


@pytest.mark.asyncio
async def test_checkpoint_create_and_restore(tmp_path: Path) -> None:
    """快照 → 写入更多数据 → 回滚 → 数据与快照一致（验收门检查点测试）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"
    _make_db(state_db, 10)
    _make_db(heartbeat_db, 5)

    mgr = CheckpointManager(data_dir)
    checkpoint = await mgr.create(state_db, heartbeat_db)
    assert checkpoint.exists()
    assert (checkpoint / "state.db").exists()
    assert (checkpoint / "heartbeat.db").exists()

    # 快照后继续写入
    _make_db(state_db, 100)
    assert _count_rows(state_db) == 100

    # 回滚 → 恢复快照时刻状态
    mgr.restore(checkpoint)
    assert _count_rows(state_db) == 10
    assert _count_rows(heartbeat_db) == 5


@pytest.mark.asyncio
async def test_checkpoint_prune_keeps_latest(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"
    _make_db(state_db, 1)
    _make_db(heartbeat_db, 1)

    mgr = CheckpointManager(data_dir)
    for _ in range(CHECKPOINT_KEEP + 3):
        await mgr.create(state_db, heartbeat_db)
    assert len(mgr.list_checkpoints()) == CHECKPOINT_KEEP


def test_freeze_marker(tmp_path: Path) -> None:
    mgr = CheckpointManager(tmp_path)
    assert mgr.is_frozen() is False
    mgr.freeze()
    assert mgr.is_frozen() is True
    assert mgr.freeze_marker.exists()
    mgr.unfreeze()
    assert mgr.is_frozen() is False
