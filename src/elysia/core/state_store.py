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

from elysia.memory.levels import (
    CERTAINTY_BY_KIND,
    FALLBACK_CERTAINTY,
    FALLBACK_CLAIM,
    FALLBACK_RETENTION,
    FALLBACK_SOURCE,
    SOURCE_BY_KIND,
    default_certainty,
    default_source,
)

logger = logging.getLogger("elysia.state_store")

_WRITE_OP = Callable[[sqlite3.Connection], Any]
_QUEUE_ITEM = tuple[_WRITE_OP, "asyncio.Future[Any] | None"]
_JOIN_TIMEOUT_S = 10.0

_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_HEARTBEAT_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS heartbeats (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    beat_type TEXT NOT NULL,
    payload   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_heartbeats_ts ON heartbeats (ts);
CREATE TABLE IF NOT EXISTS thought_log (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   REAL NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expression_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    intent      TEXT NOT NULL,
    instruction TEXT NOT NULL,
    llm_text    TEXT NOT NULL,
    validation  TEXT NOT NULL,
    level       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
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
    source         TEXT NOT NULL DEFAULT '{FALLBACK_SOURCE}',
    certainty      TEXT NOT NULL DEFAULT '{FALLBACK_CERTAINTY}',
    claim_status   TEXT NOT NULL DEFAULT '{FALLBACK_CLAIM}',
    retention_state TEXT NOT NULL DEFAULT '{FALLBACK_RETENTION}'
);
CREATE INDEX IF NOT EXISTS idx_memories_ts ON memories (created_ts);
CREATE INDEX IF NOT EXISTS idx_memories_level ON memories (level);
CREATE TABLE IF NOT EXISTS memory_index (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id        INTEGER NOT NULL,
    path_key         TEXT NOT NULL,
    last_retrieve_ts REAL,
    strength         REAL NOT NULL,
    emotions         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_index_key ON memory_index (path_key);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _run_migration(conn: sqlite3.Connection, stmt: str) -> None:
    """执行一条迁移语句；列已存在则跳过（幂等，兼容新旧库）。"""
    try:
        conn.execute(stmt)
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise


def _backfill_sql(column: str, mapping: dict[str, str], fallback: str) -> str:
    """生成"按 kind 回填一列"的迁移语句（幂等：只填 NULL，跑 N 次=跑 1 次）。

    映射取自 levels.py，与 from_dict/add_memory 的默认同源，避免回填值漂移。
    """
    whens = " ".join(f"WHEN '{kind}' THEN '{value}'" for kind, value in mapping.items())
    return (
        f"UPDATE memories SET {column} = CASE kind {whens} ELSE '{fallback}' END"
        f" WHERE {column} IS NULL"
    )


class _AsyncSQLite:
    """单写者队列底座：连接生命周期在事件循环内，写操作串行落盘。"""

    def __init__(
        self, db_path: Path, *, schema: str | None = None, migrations: tuple[str, ...] = ()
    ) -> None:
        self._db_path = db_path
        self._schema = schema
        self._migrations = migrations
        self._conn: sqlite3.Connection | None = None
        self._queue: asyncio.Queue[_QUEUE_ITEM] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._conn = await asyncio.to_thread(_connect, self._db_path)
        if self._schema is not None:
            # executescript 支持多语句 schema（建表 + 索引），自带 commit
            await asyncio.to_thread(self._conn.executescript, self._schema)
        for stmt in self._migrations:
            await asyncio.to_thread(_run_migration, self._conn, stmt)
        self._writer = asyncio.create_task(self._write_loop(), name=f"writer-{self._db_path.stem}")

    async def _write_loop(self) -> None:
        assert self._conn is not None
        while True:
            op, fut = await self._queue.get()
            try:
                result = await asyncio.to_thread(op, self._conn)
            except Exception:
                logger.exception("write op failed（写者存活，后续写入不受影响）")
                if fut is not None:
                    fut.set_result(None)
            else:
                if fut is not None:
                    fut.set_result(result)
            finally:
                self._queue.task_done()

    async def submit(self, op: _WRITE_OP) -> None:
        """提交写操作并等待执行完成（单写者串行，写后读一致）。"""
        await self._queue.put((op, None))
        await self._queue.join()

    async def submit_ret(self, op: _WRITE_OP) -> Any:
        """提交带返回值的写操作并等待结果（单写者串行，写后读一致）。"""
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self._queue.put((op, fut))
        await self._queue.join()
        return fut.result()

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
        super().__init__(
            db_path,
            schema=_HEARTBEAT_SCHEMA,
            # 旧库补列（新库已在 schema 内含）：记忆修正/覆盖标记 + 来源/确定性 + 认领 + 保留
            migrations=(
                "ALTER TABLE memories ADD COLUMN superseded_by INTEGER",
                "ALTER TABLE memories ADD COLUMN source TEXT",
                "ALTER TABLE memories ADD COLUMN certainty TEXT",
                "ALTER TABLE memories ADD COLUMN claim_status TEXT",
                "ALTER TABLE memories ADD COLUMN retention_state TEXT",
                # 旧数据回填（P3-T）：按 kind 推导；只填 NULL，幂等
                _backfill_sql("source", SOURCE_BY_KIND, FALLBACK_SOURCE),
                _backfill_sql("certainty", CERTAINTY_BY_KIND, FALLBACK_CERTAINTY),
                # 旧数据回填（P3-V）：认领默认与 kind 无关，一律 claimed（老记忆全部可用）
                f"UPDATE memories SET claim_status = '{FALLBACK_CLAIM}' WHERE claim_status IS NULL",
                # 旧数据回填（P3-W）：保留默认与 kind 无关，一律 present（老记忆全部够得着）
                f"UPDATE memories SET retention_state = '{FALLBACK_RETENTION}'"
                " WHERE retention_state IS NULL",
            ),
        )

    @staticmethod
    def _memory_row_to_dict(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
        """memories 表行 → 序列化字典（列顺序与 schema 对齐）。"""
        return {
            "id": row[0],
            "created_ts": row[1],
            "level": row[2],
            "kind": row[3],
            "content": row[4],
            "emotion_vector": json.loads(row[5]),
            "importance": row[6],
            "access_count": row[7],
            "last_access_ts": row[8],
            "protected": bool(row[9]),
            "detail_level": row[10],
            "narrative": row[11],
            "superseded_by": row[12],
            "source": row[13],
            "certainty": row[14],
            "claim_status": row[15],
            "retention_state": row[16],
        }

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

    async def think(self, ts: float, kind: str, text: str) -> None:
        """记录一条内部念头（离线自主生活的结构化自我叙事）。"""

        def _think(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO thought_log (ts, kind, text) VALUES (?, ?, ?)",
                (ts, kind, text),
            )
            conn.commit()

        await self.submit(_think)

    async def thought_count(self) -> int:
        rows = await self.execute_raw("SELECT COUNT(*) FROM thought_log")
        return int(rows[0][0])

    async def expression(
        self,
        ts: float,
        intent: str,
        instruction: dict[str, Any],
        llm_text: str,
        validation: dict[str, Any],
        level: str,
    ) -> None:
        """记录一次表达全链路（P2 §九：表达日志 + 越权取证）。"""

        def _expression(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO expression_log (ts, intent, instruction, llm_text, validation, level)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    intent,
                    json.dumps(instruction, ensure_ascii=False),
                    llm_text,
                    json.dumps(validation, ensure_ascii=False),
                    level,
                ),
            )
            conn.commit()

        await self.submit(_expression)

    async def add_memory(self, ts: float, record: dict[str, Any]) -> int:
        """写入一条记忆（P3 §九 memories 表），返回新记忆 id。

        只追加；emotion_vector/其他结构为 JSON 序列化（可导出格式，P3-A 决策）。
        source/certainty（P3-T）：写入点能标就标；未标注时按 kind 推导默认。
        claim_status（P3-V）：写入一律默认 claimed——认领是**能力**，默认给她；
        只有她自己的动作（disclaim 工具）才能改成 rejected。
        retention_state（P3-W）：写入一律默认 present——可及性由时间（维护循环）
        与她的动作（forget 工具）改变，不在写入点标注。
        """
        emotion = json.dumps(record.get("emotion_vector", {}), ensure_ascii=False)
        kind = str(record.get("kind", "internal"))

        def _add(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "INSERT INTO memories (created_ts, level, kind, content, emotion_vector,"
                " importance, access_count, last_access_ts, protected, detail_level, narrative,"
                " source, certainty, claim_status, retention_state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    record.get("level", "shallow"),
                    kind,
                    record.get("content", ""),
                    emotion,
                    record.get("importance", 0.0),
                    record.get("access_count", 0),
                    record.get("last_access_ts"),
                    1 if record.get("protected") else 0,
                    record.get("detail_level", 1.0),
                    record.get("narrative", ""),
                    record.get("source") or default_source(kind),
                    record.get("certainty") or default_certainty(kind),
                    record.get("claim_status") or FALLBACK_CLAIM,
                    record.get("retention_state") or FALLBACK_RETENTION,
                ),
            )
            conn.commit()
            assert cur.lastrowid is not None
            return int(cur.lastrowid)

        return int(await self.submit_ret(_add))

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        rows = await self.execute_raw("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return self._memory_row_to_dict(rows[0]) if rows else None

    async def count_memories(self, level: str | None = None) -> int:
        if level is None:
            rows = await self.execute_raw("SELECT COUNT(*) FROM memories")
        else:
            rows = await self.execute_raw("SELECT COUNT(*) FROM memories WHERE level = ?", (level,))
        return int(rows[0][0])

    async def iterate_memories(self) -> list[dict[str, Any]]:
        """读取全部记忆（序列化字典列表）——用于晋升扫描/导出/检索。"""
        rows = await self.execute_raw("SELECT * FROM memories")
        return [self._memory_row_to_dict(r) for r in rows]

    async def update_memory_level(
        self,
        memory_id: int,
        *,
        level: str,
        detail_level: float | None = None,
    ) -> None:
        """晋升/模糊化时更新记忆层级与细节完整度（P3-B）。"""

        def _update(conn: sqlite3.Connection) -> None:
            if detail_level is None:
                conn.execute("UPDATE memories SET level = ? WHERE id = ?", (level, memory_id))
            else:
                conn.execute(
                    "UPDATE memories SET level = ?, detail_level = ? WHERE id = ?",
                    (level, detail_level, memory_id),
                )
            conn.commit()

        await self.submit(_update)

    async def add_memory_index(
        self,
        memory_id: int,
        *,
        path_key: str,
        strength: float,
        emotions: float,
        last_retrieve_ts: float | None = None,
    ) -> int:
        """为记忆建立检索索引路径（P3 §8.3 索引衰减的载体），返回索引行 id。"""

        def _add(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "INSERT INTO memory_index (memory_id, path_key, last_retrieve_ts,"
                " strength, emotions) VALUES (?, ?, ?, ?, ?)",
                (memory_id, path_key, last_retrieve_ts, strength, emotions),
            )
            conn.commit()
            assert cur.lastrowid is not None
            return int(cur.lastrowid)

        return int(await self.submit_ret(_add))

    async def decay_memory_index(self, updated: list[tuple[int, float]]) -> None:
        """批量更新索引 strength（P3-C 索引衰减落库）。"""

        def _decay(conn: sqlite3.Connection) -> None:
            for idx_id, new_strength in updated:
                conn.execute(
                    "UPDATE memory_index SET strength = ? WHERE id = ?",
                    (new_strength, idx_id),
                )
            conn.commit()

        await self.submit(_decay)

    async def touch_memory(self, memory_id: int, ts: float) -> None:
        """检索命中记忆：递增 access_count + 更新 last_access_ts（P3-B 晋升依据）。"""

        def _touch(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE memories SET access_count = access_count + 1,"
                " last_access_ts = ? WHERE id = ?",
                (ts, memory_id),
            )
            conn.commit()

        await self.submit(_touch)

    async def mark_superseded(self, memory_id: int, superseded_by: int) -> None:
        """标记旧记忆被新记忆取代（修正/覆盖）：保留数据，但不再被检索召回。"""

        def _mark(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE memories SET superseded_by = ? WHERE id = ?",
                (superseded_by, memory_id),
            )
            conn.commit()

        await self.submit(_mark)

    async def set_claim_status(self, memory_id: int, status: str) -> None:
        """更新记忆的认领状态（P3-V）。

        只应由**她自己的动作**调用（disclaim 工具）——程序不得代她拒绝认领，
        否则"她可以不认领"就变成程序的默认拦截（铁律一）。
        """

        def _set(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE memories SET claim_status = ? WHERE id = ?",
                (status, memory_id),
            )
            conn.commit()

        await self.submit(_set)

    async def set_retention_state(self, memory_id: int, state: str) -> None:
        """更新记忆的保留状态（P3-W）：够不够得着 / 想不想够。

        两类调用者，各管一半：
        - 时间造成的失去（`faded`/`dormant`）→ 由程序的心跳维护循环设置
        - 主动抑制（`suppressed`）与唤醒回 `present` → 只能由**她的动作**或
          "她提起这件事"产生；程序不得代她决定"不想再想起"（铁律一）。
        """

        def _set(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE memories SET retention_state = ? WHERE id = ?",
                (state, memory_id),
            )
            conn.commit()

        await self.submit(_set)

    async def set_protected(self, memory_id: int) -> None:
        """标记记忆为珍贵（P3-W 自动保护）：晋升深层且被反复想起者永不降级。

        只单向置位（True）：珍贵由"沉淀深度 + 被想起次数"自动得出，
        没有"取消珍贵"的动作——遗忘状态机不需要它，也就不提供。
        """

        def _set(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE memories SET protected = 1 WHERE id = ?", (memory_id,))
            conn.commit()

        await self.submit(_set)

    async def iterate_memory_index(self) -> list[tuple[int, int, float, float]]:
        """遍历全部索引行：(index_id, memory_id, strength, last_retrieve_ts)。"""
        rows = await self.execute_raw(
            "SELECT id, memory_id, strength, last_retrieve_ts FROM memory_index"
        )
        return [
            (int(r[0]), int(r[1]), float(r[2]), float(r[3]) if r[3] is not None else 0.0)
            for r in rows
        ]

    async def gaps(self, threshold_s: float, beat_type: str = "soul") -> list[tuple[float, float]]:
        """心跳空洞：相邻间隔 > 阈值的区间 [(gap_start, gap_end), ...]。

        验收门"心跳日志连续 24h 无中断"的检测器（默认检测灵魂心跳）。
        """
        rows = await self.execute_raw(
            "SELECT ts FROM heartbeats WHERE beat_type = ? ORDER BY ts ASC",
            (beat_type,),
        )
        found: list[tuple[float, float]] = []
        for prev, cur in pairwise(rows):
            start, end = prev[0], cur[0]
            if end - start > threshold_s:
                found.append((start, end))
        return found
