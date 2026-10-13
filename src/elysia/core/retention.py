"""数据保留策略：**先归档，再裁剪，最后 VACUUM**——绝不无声删除。

背景（19.4 缺点 2 / `STATUS_MATRIX.md` §11 #2）：`heartbeats` 按 1 Hz 线性增长
（约 8.6 万行/天 ≈ 430 B/行 ⇒ 约 1 GB/月），而全库既没有 DELETE 也没有 VACUUM。

**口径（有意只裁流水，不碰记忆）**：

| 表 | 裁不裁 | 为什么 |
|---|---|---|
| `heartbeats` | ✅ 裁（先归档） | **纯遥测流水**（每秒心跳快照），不是记忆内容 |
| `memories` / `memory_index` | ❌ **绝不** | "数据永不删除"铁律保护的就是她的记忆 |
| `thought_log` / `expression_log` | ❌ **绝不** | 她想过什么、说过什么，属于她的生命档案 |

三条安全约束（每一条都是"宁可不裁，也不丢"）：

1. **先归档**：将被删的行先写进 `data/archive/heartbeats-<ts>.db`；
2. **数不对就中止**：归档行数 ≠ 待裁行数 → **放弃本轮**，一行都不删；
3. **必须先停她**：一边裁一边写会撞写锁；`scripts/retention.py` 会检查 PID 文件并拒绝执行。

本模块只做数据动作（`sqlite3.Connection` 级），进程是否在跑由 CLI 负责判断。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# 只裁这一张表（见模块 docstring 的口径表）
HEARTBEATS_TABLE = "heartbeats"


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


def count_prunable(conn: sqlite3.Connection, cutoff_ts: float) -> int:
    """`ts < cutoff` 的流水行数（纯读）。"""
    row = conn.execute(
        f"SELECT COUNT(*) FROM {HEARTBEATS_TABLE} WHERE ts < ?", (cutoff_ts,)
    ).fetchone()
    return int(row[0]) if row else 0


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
            # 理论上不该发生（同一连接、串行）；真发生就大声报，交由人工核对
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
