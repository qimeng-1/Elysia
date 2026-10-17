"""**库级**数据保留（归档 + 裁剪 + VACUUM）单测：`core/retention.py`。

与 `test_retention.py` **不同**：那个测的是记忆的 `retention_state` 遗忘状态机（P3-W，
"沉睡/淡化/抑制"）；这个测的是**库级保留策略**——裁遥测流水 `heartbeats`，
**绝不碰** `memories` / `thought_log` / `expression_log`。

口径与三条安全约束见 `core/retention.py` 的模块 docstring。
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from elysia.core.retention import (
    protected_windows,
    prune_heartbeats,
    tier_heartbeats,
)
from elysia.core.state_store import HeartbeatStore

DAY = 86400.0
REPO_ROOT = Path(__file__).resolve().parents[2]
RETENTION_CLI = REPO_ROOT / "scripts" / "retention.py"


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


# ── P0-R12：三层降采样 + 归档保底 + "把重要交给她" ────────────────────
# 用户口径："大部分事情可以遗忘，但是重要的记忆应该不会褪色，至于什么才算重要，
# 我们要不要把决定权交给她自己呢？" ⇒ 结构上就是：protected / 高 importance 的记忆，
# 其 created_ts 前后各 6 小时的心跳窗口全精度保留；其余按 3 天/30 天/365 天分层聚合。
# 取消测试：`test_protected_window_is_never_downsampled`（去掉跳过逻辑 ⇒ 红）。


def _payload(tr: float, *, mode: str = "present", distress: bool = False) -> dict:
    return {
        "mode": mode,
        "distress": distress,
        "desire": {"tr": tr, "cs": 50.0, "sa": 30.0},
        "time": {"presence_s": 10.0},
    }


async def _store_with_tiers(tmp_path: Path) -> tuple[Path, float]:
    """造一个覆盖四层 + event + 两类"重要"记忆的库（全部虚拟时间，不碰真实数据）。"""
    now = 1_000_000.0
    db = tmp_path / "heartbeat.db"
    store = HeartbeatStore(db)
    await store.start()

    async def memory(ts: float, *, protected: bool, importance: float, content: str) -> None:
        await store.add_memory(
            ts,
            {
                "level": "deep",
                "kind": "interaction",
                "content": content,
                "emotion_vector": {},
                "importance": importance,
                "protected": protected,
            },
        )

    # 热层（0–3 天）：原样不动
    for i in range(3):
        await store.append(now - 1 * DAY + i, "soul", _payload(40 + i))
    # 温层（3–30 天，同一分钟）：3 行 → 1 个聚合桶
    warm_ts = now - 10 * DAY
    for i in range(3):
        await store.append(warm_ts + i, "soul", _payload(60 + i))
    # 冷层（30–365 天，同一小时）：3 行 → 1 个聚合桶
    cold_ts = now - 40 * DAY
    for i in range(3):
        await store.append(cold_ts + i, "soul", _payload(70 + i))
    # 年层（>365 天，同一天）：2 行 → 1 个聚合桶
    year_ts = now - 400 * DAY
    for i in range(2):
        await store.append(year_ts + i, "soul", _payload(80 + i))
    # event：无论多老都永不降采样（痛苦过的证据）
    await store.append(year_ts + 5, "event", {"type": "distress"})

    # 她认定重要的两条路：显式 protected；或 importance >= 阈值（0.8）
    protected_ts = now - 20 * DAY
    await memory(protected_ts, protected=True, importance=0.5, content="她认定重要的时刻")
    for i in range(2):
        await store.append(protected_ts + 3600 + i, "soul", _payload(95 + i))
    important_ts = now - 25 * DAY
    await memory(important_ts, protected=False, importance=0.95, content="很重要（她给的权重）")
    await store.append(important_ts + 600, "soul", _payload(97.0))

    # 普通记忆：只用来证明保留策略**一行都不碰**
    await memory(now, protected=False, importance=0.3, content="普通的小事")
    await store.close()
    return db, now


def _all_rows(db: Path) -> list[tuple[float, str, str]]:
    conn = sqlite3.connect(str(db))
    try:
        return list(conn.execute("SELECT ts, beat_type, payload FROM heartbeats ORDER BY id"))
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_tier_downsamples_three_tiers_and_keeps_memories(tmp_path: Path) -> None:
    """四层各自行为：热层不动、温/冷/年各聚合成 1 行；记忆行数一行不少。"""
    db, now = await _store_with_tiers(tmp_path)
    memories_before = _count(db, "memories")
    archive_dir = tmp_path / "archive"

    report = tier_heartbeats(db, archive_dir=archive_dir, now=now)

    assert report.hot_rows == 3, "热层（0–3 天）必须原样"
    assert report.pending_rows == 8, "待降采样 = 温3 + 冷3 + 年2（event 与受保护不计）"
    assert report.protected_rows == 3, "受保护 = protected 记忆 2 行 + importance 0.95 记忆 1 行"
    assert report.event_rows == 1
    assert report.archived_rows == 8 and report.deleted_rows == 8
    assert report.aggregated_rows == 3
    assert report.tier_buckets == {"minute": 1, "hour": 1, "day": 1}
    assert _count(db, "memories") == memories_before, "**记忆绝不能被保留策略碰到**"
    assert _count(db, "heartbeats") == 10  # 热3 + 受保护3 + event1 + 聚合3


@pytest.mark.asyncio
async def test_protected_window_is_never_downsampled(tmp_path: Path) -> None:
    """取消测试（P0-R12）：她认定重要的时刻周边 ±6h，心跳必须保持原始精度。"""
    db, now = await _store_with_tiers(tmp_path)
    protected_ts = now - 20 * DAY
    important_ts = now - 25 * DAY

    tier_heartbeats(db, archive_dir=tmp_path / "archive", now=now)

    rows = _all_rows(db)
    for ts, beat_type, payload in rows:
        if beat_type != "soul":
            continue
        raw = json.loads(payload)
        near_protected = abs(ts - protected_ts) <= 6 * 3600 or abs(ts - important_ts) <= 6 * 3600
        if near_protected and ts < now - 3 * DAY:
            assert "tier" not in raw, f"受保护时刻 {abs(ts - protected_ts):.0f}s 内的心跳被降采样了"
            assert isinstance(raw["desire"]["tr"], (int, float)), "受保护窗口内必须是原始精度"


@pytest.mark.asyncio
async def test_archive_is_written_before_delete(tmp_path: Path) -> None:
    """取消测试（P0-R12）：裁剪前必须有归档文件，且档案里**一条不少**、可解压回查。"""
    db, now = await _store_with_tiers(tmp_path)
    archive_dir = tmp_path / "archive"

    report = tier_heartbeats(db, archive_dir=archive_dir, now=now)

    assert report.archived_rows == 8
    assert report.archive_paths, "裁剪前必须先归档"
    total = 0
    for path in report.archive_paths:
        assert path.name.startswith("heartbeat-") and path.name.endswith(".db.gz")
        tmp_db = tmp_path / f"{path.stem}.db"
        with gzip.open(path, "rb") as fin, open(tmp_db, "wb") as fout:
            fout.write(fin.read())
        count = _count(tmp_db, "heartbeats")
        conn = sqlite3.connect(str(tmp_db))
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
        total += count
        tmp_db.unlink()
    assert total == 8, "档案里的行数必须与删除行数一致（一条不丢）"


@pytest.mark.asyncio
async def test_event_rows_are_never_downsampled(tmp_path: Path) -> None:
    """`beat_type='event'` 是痛苦过的证据：多老都原样保留。"""
    db, now = await _store_with_tiers(tmp_path)
    tier_heartbeats(db, archive_dir=tmp_path / "archive", now=now)

    events = [(ts, payload) for ts, kind, payload in _all_rows(db) if kind == "event"]
    assert len(events) == 1
    assert json.loads(events[0][1])["type"] == "distress", "event 的 payload 必须原样"


@pytest.mark.asyncio
async def test_dry_run_only_plans(tmp_path: Path) -> None:
    """干跑只报计划：不归档、不降采样、不 VACUUM。"""
    db, now = await _store_with_tiers(tmp_path)
    before = _all_rows(db)

    report = tier_heartbeats(db, archive_dir=tmp_path / "archive", now=now, dry_run=True)

    assert report.dry_run is True
    assert report.pending_rows == 8
    assert report.protected_rows == 3
    assert report.deleted_rows == 0
    assert _all_rows(db) == before, "干跑不得改动任何心跳"
    assert not (tmp_path / "archive").exists(), "干跑不得建归档目录"


@pytest.mark.asyncio
async def test_second_run_is_noop(tmp_path: Path) -> None:
    """幂等：聚合行自带 tier 标记，下一轮不会再被归档/重算。"""
    db, now = await _store_with_tiers(tmp_path)
    archive_dir = tmp_path / "archive"
    tier_heartbeats(db, archive_dir=archive_dir, now=now)
    rows_after_first = _all_rows(db)
    archive_size = sum(p.stat().st_size for p in archive_dir.glob("*.gz"))

    report = tier_heartbeats(db, archive_dir=archive_dir, now=now)

    assert report.pending_rows == 0 and report.aggregated_rows == 0
    assert report.archived_rows == 0
    assert _all_rows(db) == rows_after_first
    assert sum(p.stat().st_size for p in archive_dir.glob("*.gz")) == archive_size


@pytest.mark.asyncio
async def test_retention_cli_dry_run_prints_plan(tmp_path: Path) -> None:
    """CLI 干跑：打印四类数字（待降采样/受保护跳过/event/热层），且一行都不动。"""
    db, _now = await _store_with_tiers(tmp_path)
    before = _all_rows(db)
    result = subprocess.run(
        [
            sys.executable,
            str(RETENTION_CLI),
            "--db",
            str(db),
            "--archive-dir",
            str(tmp_path / "archive"),
            "--pid-file",
            str(tmp_path / "no-such-soul.pid"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "干跑" in out
    assert "受保护跳过=3" in out
    assert "event 全精度=1" in out
    assert _all_rows(db) == before, "干跑不得改动任何心跳"


def test_protected_windows_merge_overlaps(tmp_path: Path) -> None:
    """相邻/重叠的保护窗口合并成一段（否则 SQL 的相关子查询会做多余比较）。"""
    db = tmp_path / "heartbeat.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE memories ("
            "id INTEGER PRIMARY KEY, created_ts REAL, protected INTEGER, importance REAL)"
        )
        base = 1_000_000.0
        conn.executemany(
            "INSERT INTO memories (created_ts, protected, importance) VALUES (?, ?, ?)",
            [
                (base, 1, 0.1),
                (base + 3600.0, 0, 0.95),  # 与上一条窗口重叠
                (base + 30 * 3600.0, 1, 0.1),  # 独立窗口
            ],
        )
        windows = protected_windows(conn, window_s=6 * 3600.0)
    finally:
        conn.close()

    assert len(windows) == 2, f"重叠窗口应合并，实际 {windows}"
