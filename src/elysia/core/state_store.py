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
    CERTAINTY_CERTAIN,
    FALLBACK_CERTAINTY,
    FALLBACK_CLAIM,
    FALLBACK_RETENTION,
    FALLBACK_SOURCE,
    KIND_SELF,
    LEVEL_DEEP,
    RETENTION_PRESENT,
    SOURCE_BY_KIND,
    SOURCE_SELF,
    default_certainty,
    default_source,
)

logger = logging.getLogger("elysia.state_store")

_WRITE_OP = Callable[[sqlite3.Connection], Any]
# 队列表项：`op is None` = **关闭哨兵**（写者见到即自行退出；见 `close()` 的 F7 说明）
_QUEUE_ITEM = tuple[_WRITE_OP | None, "asyncio.Future[Any] | None"]
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
CREATE TABLE IF NOT EXISTS dialogue_turns (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    role           TEXT NOT NULL,
    text           TEXT NOT NULL,
    exchange_id    INTEGER NOT NULL,
    emotion_vector TEXT,
    intent         TEXT
);
CREATE INDEX IF NOT EXISTS idx_dialogue_turns_ts ON dialogue_turns (ts);
CREATE TABLE IF NOT EXISTS kept_moments (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    memory_id INTEGER NOT NULL,
    reason    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kept_moments_memory ON kept_moments (memory_id);
CREATE INDEX IF NOT EXISTS idx_kept_moments_ts ON kept_moments (ts);
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
    retention_state TEXT NOT NULL DEFAULT '{FALLBACK_RETENTION}',
    keep_reason    TEXT,
    source_ref     TEXT
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
CREATE TABLE IF NOT EXISTS memory_narrative_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               REAL NOT NULL,
    memory_id        INTEGER NOT NULL,
    old_narrative    TEXT NOT NULL,
    new_narrative    TEXT NOT NULL,
    certainty_before TEXT NOT NULL,
    certainty_after  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_narrative_history_memory
    ON memory_narrative_history (memory_id, ts);
CREATE TABLE IF NOT EXISTS intentions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts      REAL NOT NULL,
    goal            TEXT NOT NULL,
    action          TEXT NOT NULL,
    target          TEXT,
    kind            TEXT NOT NULL DEFAULT 'task',
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_attempt_ts REAL,
    outcome         TEXT,
    finished_ts     REAL
);
CREATE INDEX IF NOT EXISTS idx_intentions_status ON intentions (status, created_ts);
"""


def _connect(db_path: Path) -> tuple[sqlite3.Connection, bool]:
    """打开连接并做**启动自检**；返回 `(连接, 是否退化)`。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError as exc:
        logger.error("状态库无法打开（可能已损坏，或不是 SQLite 文件）：%s ｜ %s", db_path, exc)
        return conn, True
    return conn, not _verify_integrity(conn, db_path)


def _verify_integrity(conn: sqlite3.Connection, db_path: Path) -> bool:
    """启动时完整性校验（`PROJECT_ENGINEERING_STANDARD.md` §3.2 的要求）；通过返回 True。

    **告警但不阻断启动，也不自动恢复**——两条都是刻意的：

    - 不阻断：她的生命循环不该因为一次校验失败就彻底醒不来。启动后写操作会按
      F6 的约定**大声失败**（不再伪装成功），所以问题看得见。
    - 不自动恢复：用陈旧备份覆盖当前数据是**不可逆**的动作，机器不该替人做这个决定。
      正确动作是人工恢复：`soul.ps1 stop` 后从 `data/checkpoints/` 或备份取回。
    """
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        logger.error("状态库完整性校验无法执行：%s ｜ %s", db_path, exc)
        return False
    result = str(row[0]) if row else "unknown"
    if result != "ok":
        logger.error(
            "状态库完整性校验**未通过**：%s（%s）｜ 请先 stop 进程，"
            "再从 data/checkpoints 或备份恢复",
            result,
            db_path,
        )
        return False
    return True


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
        self._closed = False
        self._degraded = False  # True = 库坏了/初始化失败，但进程仍醒着（见 `start`）

    @property
    def degraded(self) -> bool:
        """是否处于**退化运行**（库损坏或初始化失败）。

        给观测用：健康时恒 False；True 意味着写操作会大声失败、需要人工恢复。
        """
        return self._degraded

    def _ensure_open(self) -> None:
        """关停后拒绝新写：**说真话**，不静默丢弃（与 F6 同一原则）。"""
        if self._closed:
            raise RuntimeError("状态库已关闭，拒绝新的写操作")

    async def start(self) -> None:
        self._closed = False
        conn, degraded = await asyncio.to_thread(_connect, self._db_path)
        self._conn = conn
        self._degraded = degraded
        try:
            if self._schema is not None:
                # executescript 支持多语句 schema（建表 + 索引），自带 commit
                await asyncio.to_thread(self._conn.executescript, self._schema)
            for stmt in self._migrations:
                await asyncio.to_thread(_run_migration, self._conn, stmt)
        except sqlite3.DatabaseError as exc:
            # 退化运行：库坏了也让她醒着（写会按 F6 大声失败），而不是彻底起不来
            self._degraded = True
            logger.error("状态库初始化失败，进入**退化运行**：%s ｜ %s", self._db_path, exc)
        self._writer = asyncio.create_task(self._write_loop(), name=f"writer-{self._db_path.stem}")

    async def _write_loop(self) -> None:
        assert self._conn is not None
        while True:
            op, fut = await self._queue.get()
            try:
                if op is None:
                    # 关闭哨兵：**自行退出**（不是被 cancel）——见 `close()` 的 F7 说明
                    return
                try:
                    result = await asyncio.to_thread(op, self._conn)
                except Exception as exc:
                    # 写者存活（后续写入不受影响），但**失败必须被调用者看见**：
                    # 旧写法 `fut.set_result(None)` 把失败伪装成成功（F6，19.4 缺点 5）。
                    logger.exception("write op failed（写者存活，后续写入不受影响）")
                    if fut is not None and not fut.done():
                        fut.set_exception(exc)
                else:
                    if fut is not None and not fut.done():
                        fut.set_result(result)
            finally:
                self._queue.task_done()

    async def submit(self, op: _WRITE_OP) -> None:
        """提交写操作并等待完成；**失败向上抛出**（不再伪装成功，F6）。

        调用方若是常驻循环，请用 `core.supervisor.supervise` 兜住——
        库负责说真话，循环负责不死。
        """
        self._ensure_open()
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self._queue.put((op, fut))
        await self._queue.join()
        fut.result()

    async def submit_ret(self, op: _WRITE_OP) -> Any:
        """提交带返回值的写操作并等待结果（单写者串行，写后读一致）。"""
        self._ensure_open()
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
        """优雅关闭：**哨兵停写者**（不再以 `cancel()` 为主手段，F7）。

        顺序里每一步都为下一步消除竞态：

        1. `_closed = True` —— 拒收新写（`submit` / `submit_ret` 会抛）
        2. **排空队列**（`_JOIN_TIMEOUT_S` 兜底）——等的是"已提交的写都落盘"
        3. 投**关闭哨兵** → 写者**自行返回**。为什么不用 `cancel()`：
           `asyncio.to_thread` 里的 sqlite 调用**无法被取消**，硬 cancel 会让它
           继续在线程里跑，而我们紧接着就在**同一个连接**上 `close()` ——
           这正是 19.4 缺点 5 记的关闭竞态（F7）
        4. 等写者真正退出（再给一个 `_JOIN_TIMEOUT_S`）；**只有**在这一步超时后，
           才允许 `cancel()` 作为最后兜底
        5. 关连接 —— 此刻已保证没有在飞的写
        """
        if self._conn is None:
            return
        self._closed = True
        try:
            await asyncio.wait_for(self._queue.join(), timeout=_JOIN_TIMEOUT_S)
        except TimeoutError:
            logger.warning("write queue drain timeout（%ss），继续关闭", _JOIN_TIMEOUT_S)
        if self._writer is not None:
            await self._queue.put((None, None))  # 哨兵：写者见到即自行退出
            try:
                await asyncio.wait_for(asyncio.shield(self._writer), timeout=_JOIN_TIMEOUT_S)
            except TimeoutError:
                logger.warning("写者未在宽限内退出，强制取消（最后兜底）")
                self._writer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._writer
            self._writer = None
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
                # S2（她的"这条我想留着"）：她给出的理由，旧行 NULL
                "ALTER TABLE memories ADD COLUMN keep_reason TEXT",
                # P2（世界记忆）：来源引用（read_url 最终 URL），24h 去重用；旧行 NULL
                "ALTER TABLE memories ADD COLUMN source_ref TEXT",
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
            # S2：她留住这条记忆时给的理由（旧行为 NULL）
            "keep_reason": row[17] if len(row) > 17 else None,
            # P2：来源引用（世界记忆的 URL；旧行为 NULL）
            "source_ref": row[18] if len(row) > 18 else None,
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

    async def add_dialogue_turn(
        self,
        ts: float,
        role: str,
        text: str,
        exchange_id: int,
        *,
        emotion_vector: dict[str, float] | None = None,
        intent: str | None = None,
    ) -> int:
        """写一条对话轮次（S1a）：`role='user'/'her'`，同一次交换共用 `exchange_id`。

        原话不裁剪（上下文读取时才截断）；`emotion_vector`/`intent` 只有 her 侧有。
        返回新行 id。
        """
        emotion = json.dumps(emotion_vector, ensure_ascii=False) if emotion_vector else None

        def _add(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "INSERT INTO dialogue_turns (ts, role, text, exchange_id, emotion_vector, intent)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (ts, role, text, int(exchange_id), emotion, intent),
            )
            conn.commit()
            assert cur.lastrowid is not None
            return int(cur.lastrowid)

        return int(await self.submit_ret(_add))

    async def max_exchange_id(self) -> int:
        """当前交换号（无对话时 0）。用户新消息 = 在这个值上 +1。"""
        rows = await self.execute_raw("SELECT COALESCE(MAX(exchange_id), 0) FROM dialogue_turns")
        return int(rows[0][0]) if rows else 0

    async def last_user_turn_ts(self) -> float | None:
        """最近一条用户轮次的时刻（S1d 归属窗口：超出窗口的气泡算"自发"）。"""
        rows = await self.execute_raw(
            "SELECT ts FROM dialogue_turns WHERE role = 'user' ORDER BY id DESC LIMIT 1"
        )
        return float(rows[0][0]) if rows else None

    async def dialogue_turns(self, limit: int | None = None) -> list[dict[str, Any]]:
        """读最近 `limit` 条对话轮次（按时间**升序**返回；不含 limit 则按时间升序全量）。"""
        if limit is None:
            rows = await self.execute_raw(
                "SELECT id, ts, role, text, exchange_id, emotion_vector, intent"
                " FROM dialogue_turns ORDER BY id"
            )
        else:
            rows = await self.execute_raw(
                "SELECT id, ts, role, text, exchange_id, emotion_vector, intent"
                " FROM dialogue_turns ORDER BY id DESC LIMIT ?",
                (int(limit),),
            )
            rows = list(reversed(rows))
        out: list[dict[str, Any]] = []
        for rid, ts, role, text, exchange_id, emotion, intent in rows:
            out.append(
                {
                    "id": int(rid),
                    "ts": float(ts),
                    "role": str(role),
                    "text": str(text),
                    "exchange_id": int(exchange_id),
                    "emotion_vector": json.loads(emotion) if emotion else {},
                    "intent": intent,
                }
            )
        return out

    async def latest_memory(self, kind: str) -> dict[str, Any] | None:
        """最近一条指定 kind 的记忆（S2："最近一条已封存的 exchange"）。"""
        rows = await self.execute_raw(
            "SELECT * FROM memories WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
        )
        return self._memory_row_to_dict(rows[0]) if rows else None

    async def keep_moment(
        self,
        memory_id: int,
        reason: str,
        *,
        now: float,
        importance_floor: float,
    ) -> bool:
        """把一条记忆标记为"她留住的"（S2）；返回是否**首次**留住（幂等）。

        原子生效：`protected=1`、`level='deep'`、`importance=max(旧值, floor)`、
        `keep_reason=理由`，并记一行 `kept_moments`（memory_id 唯一 ⇒ 重复留不计数，
        也用于"当天已留几条"的跨重启统计）。
        """

        def _keep(conn: sqlite3.Connection) -> bool:
            conn.execute(
                "UPDATE memories SET protected = 1, level = 'deep',"
                " importance = MAX(importance, ?), keep_reason = ? WHERE id = ?",
                (float(importance_floor), reason, int(memory_id)),
            )
            cur = conn.execute(
                "INSERT OR IGNORE INTO kept_moments (ts, memory_id, reason) VALUES (?, ?, ?)",
                (float(now), int(memory_id), reason),
            )
            conn.commit()
            return cur.rowcount > 0

        return bool(await self.submit_ret(_keep))

    async def kept_moment_count_since(self, since_ts: float) -> int:
        """`since_ts` 之后她留住的数量（本地日界统计用，S2 上限）。"""
        rows = await self.execute_raw(
            "SELECT COUNT(*) FROM kept_moments WHERE ts >= ?", (float(since_ts),)
        )
        return int(rows[0][0]) if rows else 0

    async def rewrite_narrative(
        self,
        memory_id: int,
        *,
        new_narrative: str,
        certainty_after: str,
        now: float,
    ) -> dict[str, Any] | None:
        """S3：仅改写 `narrative` + 降 certainty（`content` 永不改），并留历史。

        返回 `{old_narrative, certainty_before, content}`；记忆不存在返回 None。
        同时 `access_count += 1`、`last_access_ts = now`（沿用既有口径）。
        """

        def _rewrite(conn: sqlite3.Connection) -> dict[str, Any] | None:
            row = conn.execute(
                "SELECT narrative, certainty, content FROM memories WHERE id = ?",
                (int(memory_id),),
            ).fetchone()
            if row is None:
                return None
            old_narrative, certainty_before, content = str(row[0]), str(row[1]), str(row[2])
            conn.execute(
                "UPDATE memories SET narrative = ?, certainty = ?,"
                " access_count = access_count + 1, last_access_ts = ? WHERE id = ?",
                (new_narrative, certainty_after, float(now), int(memory_id)),
            )
            conn.execute(
                "INSERT INTO memory_narrative_history (ts, memory_id, old_narrative,"
                " new_narrative, certainty_before, certainty_after)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    float(now),
                    int(memory_id),
                    old_narrative,
                    new_narrative,
                    certainty_before,
                    certainty_after,
                ),
            )
            conn.commit()
            return {
                "old_narrative": old_narrative,
                "certainty_before": certainty_before,
                "content": content,
            }

        outcome = await self.submit_ret(_rewrite)
        return dict(outcome) if isinstance(outcome, dict) else None

    async def narrative_drift(self, memory_id: int) -> list[dict[str, Any]]:
        """某条记忆的漂移史（S3）：按时间升序。"""
        rows = await self.execute_raw(
            "SELECT ts, memory_id, old_narrative, new_narrative, certainty_before,"
            " certainty_after FROM memory_narrative_history"
            " WHERE memory_id = ? ORDER BY ts, id",
            (int(memory_id),),
        )
        return [
            {
                "ts": float(r[0]),
                "memory_id": int(r[1]),
                "old_narrative": str(r[2]),
                "new_narrative": str(r[3]),
                "certainty_before": str(r[4]),
                "certainty_after": str(r[5]),
            }
            for r in rows
        ]

    async def memory_drift_summary(self, limit: int = 20) -> list[dict[str, Any]]:
        """最近的漂移条目（新的在前），供总领窗核对"她改过什么"。"""
        rows = await self.execute_raw(
            "SELECT ts, memory_id, old_narrative, new_narrative, certainty_before,"
            " certainty_after FROM memory_narrative_history ORDER BY ts DESC, id DESC LIMIT ?",
            (int(limit),),
        )
        return [
            {
                "ts": float(r[0]),
                "memory_id": int(r[1]),
                "old_narrative": str(r[2]),
                "new_narrative": str(r[3]),
                "certainty_before": str(r[4]),
                "certainty_after": str(r[5]),
            }
            for r in rows
        ]

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
                " source, certainty, claim_status, retention_state, source_ref)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    record.get("source_ref"),
                ),
            )
            conn.commit()
            assert cur.lastrowid is not None
            return int(cur.lastrowid)

        return int(await self.submit_ret(_add))

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        rows = await self.execute_raw("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return self._memory_row_to_dict(rows[0]) if rows else None

    async def find_recent_memory(
        self, kind: str, *, source_ref: str, since: float
    ) -> dict[str, Any] | None:
        """最近一条同来源（`source_ref`）的同 kind 记忆（P2：同 URL 24h 去重）。"""
        rows = await self.execute_raw(
            "SELECT * FROM memories WHERE kind = ? AND source_ref = ? AND created_ts >= ?"
            " ORDER BY id DESC LIMIT 1",
            (kind, source_ref, float(since)),
        )
        return self._memory_row_to_dict(rows[0]) if rows else None

    # ── P3：她的意图（"打算做"跨时间存活）───────────────────────────
    @staticmethod
    def _intention_row(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "id": int(row[0]),
            "created_ts": float(row[1]),
            "goal": str(row[2]),
            "action": str(row[3]),
            "target": row[4],
            "kind": str(row[5]),
            "status": str(row[6]),
            "attempts": int(row[7]),
            "last_attempt_ts": row[8],
            "outcome": row[9],
            "finished_ts": row[10],
        }

    async def add_intention(
        self,
        ts: float,
        *,
        goal: str,
        action: str,
        target: str | None,
        kind: str,
    ) -> int:
        """写一条 pending 意图（P3）；返回意图 id。"""

        def _add(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "INSERT INTO intentions (created_ts, goal, action, target, kind, status)"
                " VALUES (?, ?, ?, ?, ?, 'pending')",
                (float(ts), goal, action, target, kind),
            )
            conn.commit()
            assert cur.lastrowid is not None
            return int(cur.lastrowid)

        return int(await self.submit_ret(_add))

    async def get_intention(self, intention_id: int) -> dict[str, Any] | None:
        rows = await self.execute_raw("SELECT * FROM intentions WHERE id = ?", (int(intention_id),))
        return self._intention_row(rows[0]) if rows else None

    async def next_pending_intention(self) -> dict[str, Any] | None:
        """最早一条 pending 意图（执行器一次只拿一条）。"""
        rows = await self.execute_raw(
            "SELECT * FROM intentions WHERE status = 'pending' ORDER BY created_ts, id LIMIT 1"
        )
        return self._intention_row(rows[0]) if rows else None

    async def pending_intention_count(self) -> int:
        rows = await self.execute_raw("SELECT COUNT(*) FROM intentions WHERE status = 'pending'")
        return int(rows[0][0]) if rows else 0

    async def record_intention_attempt(self, intention_id: int, now: float, outcome: str) -> None:
        """失败一次：attempts+1、留最后尝试时间与原因（状态仍由调用方决定）。"""

        def _update(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE intentions SET attempts = attempts + 1, last_attempt_ts = ?,"
                " outcome = ? WHERE id = ?",
                (float(now), outcome, int(intention_id)),
            )
            conn.commit()

        await self.submit(_update)

    async def finish_intention(
        self, intention_id: int, now: float, status: str, outcome: str
    ) -> None:
        """终结一条意图：done / failed / dropped + outcome + finished_ts。"""

        def _update(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE intentions SET status = ?, outcome = ?, finished_ts = ? WHERE id = ?",
                (status, outcome, float(now), int(intention_id)),
            )
            conn.commit()

        await self.submit(_update)

    async def recent_finished_intentions(
        self, since: float, *, limit: int = 3
    ) -> list[dict[str, Any]]:
        rows = await self.execute_raw(
            "SELECT * FROM intentions WHERE status IN ('done', 'failed')"
            " AND finished_ts IS NOT NULL AND finished_ts >= ?"
            " ORDER BY finished_ts DESC, id DESC LIMIT ?",
            (float(since), int(limit)),
        )
        return [self._intention_row(row) for row in rows]

    async def recent_world_memories(self, since: float, *, limit: int = 5) -> list[dict[str, Any]]:
        rows = await self.execute_raw(
            "SELECT * FROM memories WHERE kind = 'world' AND created_ts >= ?"
            " ORDER BY id DESC LIMIT ?",
            (float(since), int(limit)),
        )
        return [self._memory_row_to_dict(row) for row in rows]

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

    async def mark_as_self(self, memory_id: int) -> None:
        """认领为自我认知（第八节 S2）：升格 `kind=self` 并直接落深层 + 珍贵。

        只应由**她自己的动作**调用（adopt 工具）——程序只产生候选，
        "这算不算我"永不由程序置（第八节 8.5 / D12）。

        一次 UPDATE 完成全部升格，避免出现"是自我认知却还在浅层 / 已被模糊"的
        中间态：`kind`/`source`/`certainty` 改成她认领的（她自己的、她确信的）；
        `level` 直接落 `deep` 且 `protected=1`（N5：她一旦认领就是核心，
        不等她想起 3 次；protected 同时是遗忘状态机的安全阀——永不降级、永不模糊）；
        `detail_level` 复位为完整（认回来的事不该再是模糊的）；
        `retention_state` 回到 `present`（把它接回来，它当然在册）。
        """

        def _mark(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE memories SET kind = ?, source = ?, certainty = ?, level = ?,"
                " protected = 1, detail_level = 1.0, retention_state = ? WHERE id = ?",
                (
                    KIND_SELF,
                    SOURCE_SELF,
                    CERTAINTY_CERTAIN,
                    LEVEL_DEEP,
                    RETENTION_PRESENT,
                    memory_id,
                ),
            )
            conn.commit()

        await self.submit(_mark)

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
