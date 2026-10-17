"""数据保留策略：**先归档，再降采样/裁剪，最后 VACUUM**——绝不无声删除。

背景（19.4 缺点 2 / `STATUS_MATRIX.md` §11 #2）：`heartbeats` 按 1 Hz 线性增长
（约 8.6 万行/天 ≈ 430 B/行 ⇒ 约 1 GB/月），而全库既没有 DELETE 也没有 VACUUM。

**口径（有意只动流水，不碰记忆）**：

| 表 | 动不动 | 为什么 |
|---|---|---|
| `heartbeats` | ✅ 分层降采样 + 裁剪（先归档） | **纯遥测流水**（每秒心跳快照），不是记忆内容 |
| `memories` / `memory_index` | ❌ **绝不** | "数据永不删除"铁律保护的就是她的记忆 |
| `thought_log` / `expression_log` | ❌ **绝不** | 她想过什么、说过什么，属于她的生命档案 |

三条安全约束（每一条都是"宁可不裁，也不丢"）：

1. **先归档**：被降采样/裁剪的行先写进 `_archive/heartbeats/heartbeat-YYYYMM.db.gz`；
2. **数不对就中止**：归档行数 ≠ 删除行数 → **放弃本轮**；
3. **必须先停她**：一边裁一边写会撞写锁；`scripts/retention.py` 会检查 PID 文件并拒绝执行。

P0-R12（遗忘机制，用户口径："大部分事情可以遗忘，但是重要的记忆应该不会褪色，
至于什么才算重要，我们要不要把决定权交给她自己呢？"）
--------------------------------------------------------------
三层粒度（只动 `heartbeats`）：

| 层 | 年龄 | 粒度 |
|---|---|---|
| 热 | 0–3 天 | 原样 1 Hz（不动） |
| 温 | 3–30 天 | 每分钟聚合一行 |
| 冷 | 30–365 天 | 每小时聚合一行 |
| 年 | > 365 天 | 每天聚合一行 |

聚合行保留：`tr/cs/sa` 的 min/max/mean、主导 mode、presence 和、distress 是否发生、
条数与首末时刻（`payload.tier` 标明层与桶大小，可反查）。

**例外一：`beat_type='event'` 永远原样全精度**（痛苦过的证据）。

**例外二（本卡最重要的一条）：把"什么算重要"交给她。**
凡 `memories.protected = 1` **或** `importance >= 阈值`（默认 0.8）的记忆，其
`created_ts` **前后各 6 小时**的心跳窗口**永不降采样**——她认定的重要时刻，
那段时间的生命流水全精度永久保留。程序不预设"什么重要"，只兑现她的判断（T2）。

本模块只做数据动作（`sqlite3.Connection` 级），进程是否在跑由 CLI 负责判断。
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# 只裁这一张表（见模块 docstring 的口径表）
HEARTBEATS_TABLE = "heartbeats"

# ── P0-R12：三层降采样参数（单一出处）────────────────────
HOT_DAYS = 3.0  # 0–3 天：原样保留
WARM_DAYS = 30.0  # 3–30 天：每分钟一行
COLD_DAYS = 365.0  # 30–365 天：每小时一行；>365 天：每天一行
EVENT_BEAT_TYPE = "event"  # 事件（如 distress）永不降采样
# "她认定重要"的两条路：显式 protected，或 importance 越过阈值
IMPORTANT_IMPORTANCE = 0.8
PROTECTED_WINDOW_S = 6 * 3600.0  # 重要时刻前后各 6 小时全精度保留
TIER_MINUTE = "minute"
TIER_HOUR = "hour"
TIER_DAY = "day"

# 归档文件名（按月，gzip）：heartbeat-YYYYMM.db.gz
ARCHIVE_STEM = "heartbeat"
ARCHIVE_COMPRESS_LEVEL = 6


@dataclass(frozen=True)
class RetentionReport:
    """一轮保留动作的结果（干跑也会给出前四项）。"""

    db_path: Path
    cutoff_ts: float
    keep_days: float
    prunable_rows: int
    archived_rows: int = 0
    deleted_rows: int = 0
    archive_path: Path | None = None
    vacuumed: bool = False
    size_before: int = 0
    size_after: int = 0
    dry_run: bool = False

    @property
    def freed_bytes(self) -> int:
        return max(0, self.size_before - self.size_after)


@dataclass(frozen=True)
class TierReport:
    """一轮**分层降采样**的结果（P0-R12）。

    干跑时只填 `hot_rows / pending_rows / protected_rows / event_rows / tier_buckets`。
    """

    db_path: Path
    now: float
    dry_run: bool
    hot_rows: int  # 热层（0–3 天）原样行数——不动
    pending_rows: int  # 待降采样的原始行数
    protected_rows: int  # 因"她认定重要"而跳过的行数（前后 ±6h）
    event_rows: int  # event 行数（永不降采样）
    archived_rows: int = 0
    deleted_rows: int = 0
    aggregated_rows: int = 0
    archive_paths: tuple[Path, ...] = ()
    tier_buckets: dict[str, int] = field(default_factory=dict)
    vacuumed: bool = False
    size_before: int = 0
    size_after: int = 0

    @property
    def freed_bytes(self) -> int:
        return max(0, self.size_before - self.size_after)


def count_prunable(conn: sqlite3.Connection, cutoff_ts: float) -> int:
    """`ts < cutoff` 的流水行数（纯读）。"""
    row = conn.execute(
        f"SELECT COUNT(*) FROM {HEARTBEATS_TABLE} WHERE ts < ?", (cutoff_ts,)
    ).fetchone()
    return int(row[0]) if row else 0


# ── 归档（旧接口：整段一个 sqlite 文件；保留给 prune_heartbeats）──────
def _archive_rows(conn: sqlite3.Connection, archive_path: Path, cutoff_ts: float) -> int:
    """把待裁行**复制**进归档库，返回归档行数（调用方负责核对）。

    归档表用 `CREATE TABLE ... AS SELECT * FROM main.heartbeats WHERE 0` 建：
    **结构照抄主库**，列名/类型自动一致，不会随主库演进而悄悄漂移。
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    conn.execute("ATTACH DATABASE ? AS archive", (str(archive_path),))
    try:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS archive.{HEARTBEATS_TABLE} AS "
            f"SELECT * FROM main.{HEARTBEATS_TABLE} WHERE 0"
        )
        table = f"archive.{HEARTBEATS_TABLE}"
        before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.execute(
            f"INSERT INTO {table} SELECT * FROM main.{HEARTBEATS_TABLE} WHERE ts < ?",
            (cutoff_ts,),
        )
        after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS archive.idx_archive_ts ON {HEARTBEATS_TABLE} (ts)"
        )
    finally:
        conn.execute("DETACH DATABASE archive")
    return int(after - before)


def prune_heartbeats(
    db_path: Path,
    *,
    keep_days: float,
    archive_dir: Path,
    now: float | None = None,
    dry_run: bool = False,
) -> RetentionReport:
    """归档并裁剪 `heartbeats` 中 `ts` 早于 `keep_days` 的行，然后 `VACUUM`。

    归档行数与待裁行数不一致时**抛错且一行不删**（宁可不裁，也不丢）。
    """
    now = time.time() if now is None else now
    cutoff = now - keep_days * 86400.0
    size_before = db_path.stat().st_size if db_path.exists() else 0

    # autocommit：VACUUM 不能在挂起事务里跑
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        prunable = count_prunable(conn, cutoff)
        if dry_run or prunable == 0:
            return RetentionReport(
                db_path=db_path,
                cutoff_ts=cutoff,
                keep_days=keep_days,
                prunable_rows=prunable,
                size_before=size_before,
                size_after=size_before,
                dry_run=dry_run,
            )

        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        archive_path = archive_dir / f"{db_path.stem}-{stamp}.db"
        archived = _archive_rows(conn, archive_path, cutoff)
        if archived != prunable:
            archive_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"归档行数（{archived}）≠ 待裁行数（{prunable}）：**本轮放弃，一行未删**"
            )

        conn.execute(f"DELETE FROM {HEARTBEATS_TABLE} WHERE ts < ?", (cutoff,))
        deleted = int(conn.execute("SELECT changes()").fetchone()[0])
        if deleted != prunable:
            raise RuntimeError(
                f"删除行数（{deleted}）≠ 待裁行数（{prunable}）；归档已保留，请人工核对"
            )

        conn.execute("VACUUM")
    finally:
        conn.close()

    size_after = db_path.stat().st_size if db_path.exists() else 0
    return RetentionReport(
        db_path=db_path,
        cutoff_ts=cutoff,
        keep_days=keep_days,
        prunable_rows=prunable,
        archived_rows=archived,
        deleted_rows=deleted,
        archive_path=archive_path,
        vacuumed=True,
        size_before=size_before,
        size_after=size_after,
    )


# ── P0-R12：她认定重要的时间窗口（protected / 高 importance）──────────
def protected_windows(
    conn: sqlite3.Connection,
    *,
    importance_threshold: float = IMPORTANT_IMPORTANCE,
    window_s: float = PROTECTED_WINDOW_S,
) -> list[tuple[float, float]]:
    """从 `memories` 推导"永不降采样"的时间窗口（已合并重叠）。

    两条路都算：她显式 `protected=1`，或 `importance >= 阈值`。
    **决定权在她**——程序不预设哪些记忆重要，只兑现她给出的重要性。
    """
    rows = conn.execute(
        "SELECT created_ts FROM memories WHERE protected = 1 OR importance >= ?",
        (importance_threshold,),
    ).fetchall()
    spans = sorted((float(row[0]) - window_s, float(row[0]) + window_s) for row in rows)
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _tier_of(ts: float, *, hot_cutoff: float, warm_cutoff: float, cold_cutoff: float) -> str | None:
    """按年龄分层：None = 热层（不动），否则返回聚合粒度。"""
    if ts >= hot_cutoff:
        return None
    if ts >= warm_cutoff:
        return TIER_MINUTE
    if ts >= cold_cutoff:
        return TIER_HOUR
    return TIER_DAY


_TIER_SECONDS = {TIER_MINUTE: 60, TIER_HOUR: 3600, TIER_DAY: 86400}


def _pending_where() -> str:
    """ "待降采样"的 SQL 谓词：热层之外、非 event、不在保护窗口内、**且尚未聚合过**。

    `payload NOT LIKE '%"tier"%'` 保证幂等：聚合行自己带 `tier` 标记，下一轮不会再被
    当成原始行重新归档/重算（否则每周跑一次就把同一批数据重复写进档案）。
    """
    return (
        f"ts < :hot AND beat_type != :event AND payload NOT LIKE '%\"tier\"%' AND NOT EXISTS ("
        f"SELECT 1 FROM protected_windows w "
        f"WHERE {HEARTBEATS_TABLE}.ts >= w.start_ts AND {HEARTBEATS_TABLE}.ts <= w.end_ts)"
    )


def _pending_params(hot_cutoff: float) -> dict[str, Any]:
    return {"hot": hot_cutoff, "event": EVENT_BEAT_TYPE}


def _prepare_windows(conn: sqlite3.Connection, windows: list[tuple[float, float]]) -> None:
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS protected_windows (start_ts REAL, end_ts REAL)")
    conn.execute("DELETE FROM protected_windows")
    if windows:
        conn.executemany("INSERT INTO protected_windows VALUES (?, ?)", windows)


def _payload_stats(payload: Any) -> tuple[dict[str, float], str | None, float, bool]:
    """从一条心跳 payload 里抽出聚合所需字段（容错：脏 payload 不影响整轮）。"""
    numbers: dict[str, float] = {}
    mode: str | None = None
    presence = 0.0
    distress = False
    if not isinstance(payload, dict):
        return numbers, mode, presence, distress
    desire = payload.get("desire")
    if isinstance(desire, dict):
        for key in ("tr", "cs", "sa"):
            value = desire.get(key)
            if isinstance(value, (int, float)):
                numbers[key] = float(value)
    raw_mode = payload.get("mode")
    if isinstance(raw_mode, str):
        mode = raw_mode
    time_summary = payload.get("time")
    if isinstance(time_summary, dict):
        raw_presence = time_summary.get("presence_s")
        if isinstance(raw_presence, (int, float)):
            presence = float(raw_presence)
    distress = bool(payload.get("distress"))
    return numbers, mode, presence, distress


class _Bucket:
    """一个聚合桶：tr/cs/sa 的 min/max/mean、主导 mode、presence 和、distress 有无。"""

    __slots__ = ("count", "distress", "first_ts", "last_ts", "modes", "numbers", "presence")

    def __init__(self, ts: float) -> None:
        self.count = 0
        self.first_ts = ts
        self.last_ts = ts
        self.numbers: dict[str, tuple[float, float, float]] = {}  # key -> (min, max, sum)
        self.modes: dict[str, int] = {}
        self.presence = 0.0
        self.distress = False

    def add(self, ts: float, payload: Any) -> None:
        self.count += 1
        self.first_ts = min(self.first_ts, ts)
        self.last_ts = max(self.last_ts, ts)
        numbers, mode, presence, distress = _payload_stats(payload)
        for key, value in numbers.items():
            low, high, total = self.numbers.get(key, (value, value, 0.0))
            self.numbers[key] = (min(low, value), max(high, value), total + value)
        if mode is not None:
            self.modes[mode] = self.modes.get(mode, 0) + 1
        self.presence += presence
        self.distress = self.distress or distress

    def to_row(
        self, *, beat_type: str, tier: str, bucket_s: int, bucket_index: int
    ) -> tuple[float, str, str]:
        payload: dict[str, Any] = {
            "tier": tier,
            "bucket_s": bucket_s,
            "bucket": bucket_index,
            "count": self.count,
            "first_ts": round(self.first_ts, 3),
            "last_ts": round(self.last_ts, 3),
            "beat_type": beat_type,
            "presence_s_sum": round(self.presence, 3),
            "distress_any": self.distress,
        }
        if self.numbers:
            payload["desire"] = {
                key: {
                    "min": round(low, 3),
                    "max": round(high, 3),
                    "mean": round(total / self.count, 3),
                }
                for key, (low, high, total) in self.numbers.items()
            }
        if self.modes:
            payload["mode"] = max(self.modes.items(), key=lambda item: item[1])[0]
            payload["mode_counts"] = dict(self.modes)
        ts = bucket_index * bucket_s
        return (ts, beat_type, json.dumps(payload, ensure_ascii=False))


def _aggregate_pending(
    conn: sqlite3.Connection,
    *,
    hot_cutoff: float,
    warm_cutoff: float,
    cold_cutoff: float,
    chunk: int = 20_000,
) -> tuple[dict[tuple[str, str, int], _Bucket], int]:
    """流式聚合待降采样行（不把整表读进内存）。返回 (桶字典, 原始行数)。"""
    buckets: dict[tuple[str, str, int], _Bucket] = {}
    pending = 0
    cursor = conn.execute(
        f"SELECT ts, beat_type, payload FROM {HEARTBEATS_TABLE} WHERE {_pending_where()}",
        _pending_params(hot_cutoff),
    )

    while True:
        rows = cursor.fetchmany(chunk)
        if not rows:
            break
        for ts, beat_type, raw_payload in rows:
            pending += 1
            tier = _tier_of(
                float(ts), hot_cutoff=hot_cutoff, warm_cutoff=warm_cutoff, cold_cutoff=cold_cutoff
            )
            if tier is None:  # 理论上不会（SQL 已过滤），防御
                continue
            bucket_s = _TIER_SECONDS[tier]
            bucket_index = int(float(ts) // bucket_s)
            key = (str(beat_type), tier, bucket_index)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = buckets[key] = _Bucket(float(ts))
            try:
                payload = json.loads(raw_payload)
            except (TypeError, ValueError):
                payload = None
            bucket.add(float(ts), payload)
    return buckets, pending


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _month_start_ts(year: int, month: int) -> int:
    """某年某月 1 日 0 点（UTC）的秒级时间戳（不依赖操作系统 mktime，负时间也可算）。"""
    return int((datetime(year, month, 1, tzinfo=UTC) - _EPOCH).total_seconds())


def _months_between(conn: sqlite3.Connection) -> list[tuple[str, int, int]]:
    """库里流水覆盖的月份列表：`[(YYYYMM, start_ts, next_month_start_ts)]`（升序）。

    ⚠️ 不用 `time.localtime/mktime`：测试会用 1970 附近甚至更早的虚拟时间戳，
    Windows 上对负时间戳调用 localsid 会抛 OSError（实测 Errno 22）。用 datetime 算。
    """
    row = conn.execute(f"SELECT MIN(ts), MAX(ts) FROM {HEARTBEATS_TABLE}").fetchone()
    if row is None or row[0] is None:
        return []
    first = _EPOCH + timedelta(seconds=float(row[0]))
    last = _EPOCH + timedelta(seconds=float(row[1]))
    year, month = first.year, first.month
    months: list[tuple[str, int, int]] = []
    while (year, month) <= (last.year, last.month):
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        months.append(
            (
                f"{year:04d}{month:02d}",
                _month_start_ts(year, month),
                _month_start_ts(next_year, next_month),
            )
        )
        year, month = next_year, next_month
    return months


def _archive_month(
    conn: sqlite3.Connection,
    archive_dir: Path,
    month_key: str,
    start_ts: int,
    end_ts: int,
    hot_cutoff: float,
) -> tuple[Path, int]:
    """把某月待降采样行的**完整原始行**归档进 `heartbeat-YYYYMM.db.gz`（可重复执行：合并）。"""
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{ARCHIVE_STEM}-{month_key}.db.gz"
    tmp_dir = Path(tempfile.mkdtemp(prefix="elysia_archive_"))
    tmp_db = tmp_dir / f"{ARCHIVE_STEM}-{month_key}.db"
    try:
        if path.exists():
            with gzip.open(path, "rb") as fin, open(tmp_db, "wb") as fout:
                shutil.copyfileobj(fin, fout, length=1024 * 1024)
        conn.execute("ATTACH DATABASE ? AS archive", (str(tmp_db),))
        try:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS archive.{HEARTBEATS_TABLE} AS "
                f"SELECT * FROM main.{HEARTBEATS_TABLE} WHERE 0"
            )
            table = f"archive.{HEARTBEATS_TABLE}"
            before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            params = {**_pending_params(hot_cutoff), "start": start_ts, "end": end_ts}
            conn.execute(
                f"INSERT INTO {table} SELECT * FROM main.{HEARTBEATS_TABLE} "
                f"WHERE {_pending_where()} AND ts >= :start AND ts < :end",
                params,
            )
            after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS archive.idx_archive_ts ON {HEARTBEATS_TABLE} (ts)"
            )
        finally:
            conn.execute("DETACH DATABASE archive")
        tmp_gz = path.with_name(f".{path.name}.tmp")
        with (
            open(tmp_db, "rb") as fin,
            gzip.open(tmp_gz, "wb", compresslevel=ARCHIVE_COMPRESS_LEVEL) as fout,
        ):
            shutil.copyfileobj(fin, fout, length=1024 * 1024)
        os.replace(tmp_gz, path)
        return path, int(after - before)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _table_counts(conn: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    return {
        name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in tables
    }


# 绝不被保留策略碰的四张表（P0-R12 的口径表）
UNTOUCHABLE_TABLES = ("memories", "memory_index", "thought_log", "expression_log")


def tier_heartbeats(
    db_path: Path,
    *,
    archive_dir: Path,
    now: float | None = None,
    dry_run: bool = False,
    hot_days: float = HOT_DAYS,
    warm_days: float = WARM_DAYS,
    cold_days: float = COLD_DAYS,
    importance_threshold: float = IMPORTANT_IMPORTANCE,
    protected_window_s: float = PROTECTED_WINDOW_S,
) -> TierReport:
    """三层降采样 `heartbeats`：热层不动；温/冷/年层聚合；**先归档**；最后 VACUUM。

    - `event` 行永不降采样（痛苦过的证据）；
    - 她认定重要（`protected=1` 或 `importance >= 阈值`）的记忆，其 `created_ts`
      前后 `protected_window_s` 内的流水永不降采样；
    - `memories` / `memory_index` / `thought_log` / `expression_log` 一行不动（跑完核对）；
    - 归档行数 ≠ 删除行数 ⇒ 抛错（宁可不裁，也不丢）。
    """
    now = time.time() if now is None else now
    hot_cutoff = now - hot_days * 86400.0
    warm_cutoff = now - warm_days * 86400.0
    cold_cutoff = now - cold_days * 86400.0
    size_before = db_path.stat().st_size if db_path.exists() else 0

    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        windows = protected_windows(
            conn,
            importance_threshold=importance_threshold,
            window_s=protected_window_s,
        )
        _prepare_windows(conn, windows)
        params = _pending_params(hot_cutoff)
        # 铁律核对基线：此后无论怎么动 heartbeats，这四张表的行数必须一模一样
        counts_before = _table_counts(conn, UNTOUCHABLE_TABLES)

        hot_rows = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {HEARTBEATS_TABLE} WHERE ts >= ?", (hot_cutoff,)
            ).fetchone()[0]
        )
        event_rows = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {HEARTBEATS_TABLE} WHERE ts < ? AND beat_type = ?",
                (hot_cutoff, EVENT_BEAT_TYPE),
            ).fetchone()[0]
        )
        protected_rows = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {HEARTBEATS_TABLE} WHERE ts < ? AND beat_type != ? "
                f"AND EXISTS (SELECT 1 FROM protected_windows w WHERE {HEARTBEATS_TABLE}.ts "
                f">= w.start_ts AND {HEARTBEATS_TABLE}.ts <= w.end_ts)",
                (hot_cutoff, EVENT_BEAT_TYPE),
            ).fetchone()[0]
        )
        pending_rows = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {HEARTBEATS_TABLE} WHERE {_pending_where()}",
                params,
            ).fetchone()[0]
        )

        if dry_run or pending_rows == 0:
            return TierReport(
                db_path=db_path,
                now=now,
                dry_run=dry_run,
                hot_rows=hot_rows,
                pending_rows=pending_rows,
                protected_rows=protected_rows,
                event_rows=event_rows,
                size_before=size_before,
                size_after=size_before,
            )

        # ① 聚合（流式；不把整表读进内存）
        buckets, pending_seen = _aggregate_pending(
            conn,
            hot_cutoff=hot_cutoff,
            warm_cutoff=warm_cutoff,
            cold_cutoff=cold_cutoff,
        )
        if pending_seen != pending_rows:
            raise RuntimeError(
                f"聚合扫描行数（{pending_seen}）≠ 计划行数（{pending_rows}）："
                "**本轮放弃，一行未删**"
            )

        # ② 先归档（按月；每行原始数据都进 gz 档案）
        archive_paths: list[Path] = []
        archived_total = 0
        for month_key, start_ts, end_ts in _months_between(conn):
            path, archived = _archive_month(
                conn, archive_dir, month_key, start_ts, end_ts, hot_cutoff
            )
            if archived:
                archive_paths.append(path)
                archived_total += archived

        # ③ 再删除被降采样的原始行（谓词与归档完全一致）
        deleted_total = 0
        for _month_key, start_ts, end_ts in _months_between(conn):
            conn.execute(
                f"DELETE FROM {HEARTBEATS_TABLE} "
                f"WHERE {_pending_where()} AND ts >= :start AND ts < :end",
                {**_pending_params(hot_cutoff), "start": start_ts, "end": end_ts},
            )
            deleted_total += int(conn.execute("SELECT changes()").fetchone()[0])
        if deleted_total != archived_total:
            raise RuntimeError(
                f"归档行数（{archived_total}）≠ 删除行数（{deleted_total}）；"
                "档案已保留，请人工核对（本轮未继续）"
            )

        # ④ 写入聚合行（每桶一行）
        conn.executemany(
            f"INSERT INTO {HEARTBEATS_TABLE} (ts, beat_type, payload) VALUES (?, ?, ?)",
            [
                bucket.to_row(
                    beat_type=beat_type,
                    tier=tier,
                    bucket_s=_TIER_SECONDS[tier],
                    bucket_index=bucket_index,
                )
                for (beat_type, tier, bucket_index), bucket in buckets.items()
            ],
        )

        # ⑤ 铁律核对：她的记忆与话语一行都不能少（同一连接内、VACUUM 前后都成立）
        conn.execute("VACUUM")
        counts_after = _table_counts(conn, UNTOUCHABLE_TABLES)
        tier_buckets = {
            tier: sum(1 for key in buckets if key[1] == tier)
            for tier in (TIER_MINUTE, TIER_HOUR, TIER_DAY)
        }
    finally:
        conn.close()

    if counts_after != counts_before:
        raise RuntimeError(
            f"保留策略动了不该动的表：{counts_before} → {counts_after}"
            "（这是严重缺陷，档案与现库都在，请人工核对）"
        )

    size_after = db_path.stat().st_size if db_path.exists() else 0
    return TierReport(
        db_path=db_path,
        now=now,
        dry_run=False,
        hot_rows=hot_rows,
        pending_rows=pending_rows,
        protected_rows=protected_rows,
        event_rows=event_rows,
        archived_rows=archived_total,
        deleted_rows=deleted_total,
        aggregated_rows=len(buckets),
        archive_paths=tuple(archive_paths),
        tier_buckets=tier_buckets,
        vacuumed=True,
        size_before=size_before,
        size_after=size_after,
    )
