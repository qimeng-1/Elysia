"""**库级**数据保留（归档 + 裁剪 + VACUUM）单测：`core/retention.py`。

与 `test_retention.py` **不同**：那个测的是记忆的 `retention_state` 遗忘状态机（P3-W，
"沉睡/淡化/抑制"）；这个测的是**库级保留策略**——裁遥测流水 `heartbeats`，
**绝不碰** `memories` / `thought_log` / `expression_log`。

口径与三条安全约束见 `core/retention.py` 的模块 docstring。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from elysia.core.retention import prune_heartbeats
from elysia.core.state_store import HeartbeatStore

DAY = 86400.0


async def _store_with_rows(tmp_path: Path, rows: list[tuple[float, str]]) -> Path:
    """造一个库：若干心跳流水 + **一条记忆**（用来证明保留策略不碰记忆）。"""
    db = tmp_path / "heartbeat.db"
    store = HeartbeatStore(db)
    await store.start()
    for ts, kind in rows:
        await store.append(ts, kind, {"seq": int(ts)})
    await store.add_memory(
        1000.0,
        {
            "level": "shallow",
            "kind": "interaction",
            "content": "别删我",
            "emotion_vector": {},
            "importance": 0.5,
        },
    )
    await store.close()
    return db


def _count(db: Path, table: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_dry_run_counts_without_touching(tmp_path: Path) -> None:
    """干跑只报计划：不归档、不删除、不 VACUUM。"""
    now = 1_000_000.0
    db = await _store_with_rows(tmp_path, [(now - 200 * DAY, "soul"), (now - 1 * DAY, "soul")])

    report = prune_heartbeats(
        db, keep_days=90, archive_dir=tmp_path / "archive", now=now, dry_run=True
    )

    assert report.prunable_rows == 1
    assert report.deleted_rows == 0
    assert report.archive_path is None
    assert report.dry_run is True
    assert _count(db, "heartbeats") == 2, "干跑不得删数据"
    assert not (tmp_path / "archive").exists(), "干跑不得建归档"


@pytest.mark.asyncio
async def test_prune_archives_then_deletes_and_vacuums(tmp_path: Path) -> None:
    """真跑：先归档、再删除、后 VACUUM；窗口内的行留下，记忆一行不动。"""
    now = 1_000_000.0
    db = await _store_with_rows(
        tmp_path,
        [(now - 200 * DAY, "soul"), (now - 100 * DAY, "soul"), (now - 1 * DAY, "soul")],
    )

    report = prune_heartbeats(db, keep_days=90, archive_dir=tmp_path / "archive", now=now)

    assert report.prunable_rows == 2
    assert report.archived_rows == 2, "必须先归档"
    assert report.deleted_rows == 2
    assert report.vacuumed is True
    assert report.archive_path is not None and report.archive_path.exists()

    assert _count(db, "heartbeats") == 1, "窗口内的那一行必须留下"
    assert _count(db, "memories") == 1, "**记忆绝不能被保留策略碰到**"

    conn = sqlite3.connect(str(report.archive_path))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0] == 2
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_archive_columns_match_live_table(tmp_path: Path) -> None:
    """归档表结构必须与主库一致（防止主库日后加列、归档悄悄漂移）。"""
    now = 1_000_000.0
    db = await _store_with_rows(tmp_path, [(now - 200 * DAY, "soul")])
    report = prune_heartbeats(db, keep_days=90, archive_dir=tmp_path / "archive", now=now)
    assert report.archive_path is not None

    def columns(path: Path) -> list[str]:
        c = sqlite3.connect(str(path))
        try:
            return [row[1] for row in c.execute("PRAGMA table_info(heartbeats)")]
        finally:
            c.close()

    assert columns(report.archive_path) == columns(db)


@pytest.mark.asyncio
async def test_nothing_to_prune_is_noop(tmp_path: Path) -> None:
    """没有超龄流水时不建归档、不 VACUUM（幂等且无副作用）。"""
    now = 1_000_000.0
    db = await _store_with_rows(tmp_path, [(now - 1 * DAY, "soul")])

    report = prune_heartbeats(db, keep_days=90, archive_dir=tmp_path / "archive", now=now)

    assert report.prunable_rows == 0
    assert report.archive_path is None
    assert report.vacuumed is False
    assert not (tmp_path / "archive").exists()
