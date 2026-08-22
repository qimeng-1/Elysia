"""SQLite WAL 状态库：双库分离（P0 决策 D2）。

- state.db     当前状态（读改写，每秒）——TimeSense 等单一事实源落点
- heartbeat.db 生命档案（只追加）——灵魂 1Hz / 身体 5s 心跳，天然适合备份与压缩

设计（ADR-002 + 实测验证模式）：
- WAL + busy_timeout：断电崩溃可自动恢复，记忆不丢失
- 单写者纪律：所有写操作经 asyncio.Queue 串行，写者协程 to_thread 落盘
- check_same_thread=False：写者线程安全（单写者串行，无竞争）
- 优雅关闭：join(10s) 兜底，写者取消后关闭连接，绝不死锁
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

logger = logging.getLogger("elysia.state_store")

_WRITE_OP = Callable[[sqlite3.Connection], None]
_JOIN_TIMEOUT_S = 10.0

_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_HEARTBEAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS heartbeats (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    beat_type TEXT NOT NULL,
    payload   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_heartbeats_ts ON heartbeats (ts);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


class _AsyncSQLite:
    """单写者队列底座：连接生命周期在事件循环内，写操作串行落盘。"""

    def __init__(self, db_path: Path, *, schema: str | None = None) -> None:
        self._db_path = db_path
        self._schema = schema
        self._conn: sqlite3.Connection | None = None
        self._queue: asyncio.Queue[_WRITE_OP] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._conn = await asyncio.to_thread(_connect, self._db_path)
        if self._schema is not None:
            # executescript 支持多语句 schema（建表 + 索引），自带 commit
            await asyncio.to_thread(self._conn.executescript, self._schema)
        self._writer = asyncio.create_task(self._write_loop(), name=f"writer-{self._db_path.stem}")

    async def _write_loop(self) -> None:
        assert self._conn is not None
        while True:
            op = await self._queue.get()
            try:
                await asyncio.to_thread(op, self._conn)
            except Exception:
                logger.exception("write op failed（写者存活，后续写入不受影响）")
            finally:
                self._queue.task_done()

    async def submit(self, op: _WRITE_OP) -> None:
        """提交写操作并等待执行完成（单写者串行，写后读一致）。"""
        await self._queue.put(op)
        await self._queue.join()

    async def execute_raw(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        """同步执行查询（WAL 下读写并发安全，读走独立路径）。"""
        assert self._conn is not None

        def _run(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
            return list(conn.execute(sql, params).fetchall())

        return await asyncio.to_thread(_run, self._conn)

    async def close(self) -> None:
        """优雅关闭：排空队列（10s 兜底）→ 停写者 → 关连接。"""
        try:
            await asyncio.wait_for(self._queue.join(), timeout=_JOIN_TIMEOUT_S)
        except TimeoutError:
            logger.warning("write queue drain timeout（%ss），强制关闭", _JOIN_TIMEOUT_S)
        if self._writer is not None:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None


class StateStore(_AsyncSQLite):
    """状态库（state.db）：当前状态读改写，键值存储。"""

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path, schema=_STATE_SCHEMA)

    async def load_json(self, key: str, default: Any = None) -> Any:
        rows = await self.execute_raw("SELECT value FROM kv WHERE key = ?", (key,))
        if not rows:
            return default
        return json.loads(rows[0][0])

    async def save_json(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)

        def _save(conn: sqlite3.Connection) -> None:
            conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, payload))
            conn.commit()

        await self.submit(_save)


class HeartbeatStore(_AsyncSQLite):
    """生命档案库（heartbeat.db）：只追加心跳，永不修改历史。"""

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path, schema=_HEARTBEAT_SCHEMA)

    async def append(self, ts: float, beat_type: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False)

        def _append(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO heartbeats (ts, beat_type, payload) VALUES (?, ?, ?)",
                (ts, beat_type, data),
            )
            conn.commit()

        await self.submit(_append)

    async def count(self, beat_type: str | None = None) -> int:
        if beat_type is None:
            rows = await self.execute_raw("SELECT COUNT(*) FROM heartbeats")
        else:
            rows = await self.execute_raw(
                "SELECT COUNT(*) FROM heartbeats WHERE beat_type = ?", (beat_type,)
            )
        return int(rows[0][0])

    async def first_ts(self) -> float | None:
        rows = await self.execute_raw("SELECT MIN(ts) FROM heartbeats")
        return rows[0][0] if rows[0][0] is not None else None

    async def last_ts(self) -> float | None:
        rows = await self.execute_raw("SELECT MAX(ts) FROM heartbeats")
        return rows[0][0] if rows[0][0] is not None else None

    async def gaps(self, threshold_s: float) -> list[tuple[float, float]]:
        """心跳空洞：相邻心跳间隔 > 阈值的区间 [(gap_start, gap_end), ...]。

        验收门"心跳日志连续 24h 无中断"的检测器。
        """
        rows = await self.execute_raw("SELECT ts FROM heartbeats ORDER BY ts ASC")
        found: list[tuple[float, float]] = []
        for prev, cur in pairwise(rows):
            start, end = prev[0], cur[0]
            if end - start > threshold_s:
                found.append((start, end))
        return found
