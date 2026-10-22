"""检查点单测：快照创建 / 回滚一致性 / 保留清理 / 紧急冻结。

历史取消测试（P0-R1 / D-2）：

1. `test_restore_discards_unmerged_wal`：她跑 WAL 模式，回滚覆盖前必须删掉
   `-wal`/`-shm`，否则旧 WAL 会被重放到刚恢复的库上。
   **实测旧写法**：快照 10 行 → 崩溃时 `-wal` 里另有 90 行 → 只 copy `.db` 回滚
   → 重新打开读到 **100 行**（回滚等于没回滚，且调用方会以为成功）。
2. `test_heartbeat_creates_periodic_checkpoint`：心跳每 N 拍必须真的**生成**快照。
   **实测旧写法**：`CheckpointManager.create()` 全仓 0 调用者 ⇒ `data/checkpoints/` 不存在。

P0-R10（D-9 方案 A）新增：**快照压缩**（`.db.gz`）。
- 取消测试 `test_snapshot_is_compressed_and_significantly_smaller`：还原成不压缩 ⇒ 必红；
- **旧格式（未压缩 `.db`）快照仍可回滚**：这条必须有，否则已有的旧快照会变废纸；
- 压缩/解压都要经过 `quick_check`：坏快照绝不覆盖她的现役库。
"""

from __future__ import annotations

import asyncio
import gzip
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from elysia.core.checkpoint import (
    CHECKPOINT_KEEP,
    CheckpointManager,
    backup_database,
    quick_check_db,
)
from elysia.core.clock import SimulatedClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import TimeSense
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

REPO_ROOT = Path(__file__).resolve().parents[2]
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "restore_checkpoint.py"
T0 = 1_000_000.0


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


def _snapshot_bytes(snapshot: Path, name: str) -> bytes:
    """读快照里某个库的**原始字节**（自动区分 `.db` / `.db.gz`）。"""
    plain = snapshot / name
    if plain.is_file():
        return plain.read_bytes()
    with gzip.open(snapshot / f"{name}.gz", "rb") as handle:
        return handle.read()


def _query_snapshot(snapshot: Path, name: str, sql: str) -> list[tuple[object, ...]]:
    """在快照库上执行一条查询（压缩快照先解到临时文件，不碰快照本身）。"""
    data = _snapshot_bytes(snapshot, name)
    handle, temp_name = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    tmp = Path(temp_name)
    try:
        tmp.write_bytes(data)
        conn = sqlite3.connect(str(tmp))
        try:
            return list(conn.execute(sql).fetchall())
        finally:
            conn.close()
    finally:
        tmp.unlink(missing_ok=True)


def _snapshot_quick_check(snapshot: Path, name: str) -> str:
    """快照库的 `quick_check`（`.db` 直接读；`.gz` 解到临时文件再只读校验）。"""
    plain = snapshot / name
    if plain.is_file():
        return quick_check_db(plain)
    tmp = snapshot.parent / f".{snapshot.name}-{name}.check.tmp"
    try:
        with gzip.open(snapshot / f"{name}.gz", "rb") as fin, open(tmp, "wb") as fout:
            fout.write(fin.read())
        return quick_check_db(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _make_crash_site(live: Path, extra_rows: int) -> None:
    """在 `live` 上造"崩溃遗留 WAL"：主库不变，额外 `extra_rows` 行只在 `-wal` 里。

    做法：`wal_autocheckpoint=0` + 提交后**不 clean close**，趁数据库连接还握着
    未合并的 WAL 页时把三件套拷成崩溃现场（clean close 会自动 checkpoint，那样就
    测不到本缺陷了）。
    """
    crash = live.parent.parent / "crash_site"
    crash.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(live))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"x{i}",) for i in range(extra_rows)])
    conn.commit()
    for suffix in ("", "-wal", "-shm"):
        src = Path(f"{live}{suffix}")
        if src.exists():
            (crash / src.name).write_bytes(src.read_bytes())
    conn.close()
    # 用崩溃现场覆盖现役库：主库仍是快照时的行数，`-wal` 里是未合并的新行
    for path in crash.iterdir():
        (live.parent / path.name).write_bytes(path.read_bytes())


def _make_heartbeat(
    data_dir: Path, *, checkpoint_every_n: int = 3
) -> tuple[SoulHeartbeat, SimulatedClock, StateStore, HeartbeatStore]:
    """最小心跳环境（带 CheckpointManager 与小周期，便于在用例里观察快照）。"""
    clock = SimulatedClock(start_ts=T0, speed=60.0)
    state_store = StateStore(data_dir / "state.db")
    heartbeat_store = HeartbeatStore(data_dir / "heartbeat.db")
    soul = SoulHeartbeat(
        state_store=state_store,
        heartbeat_store=heartbeat_store,
        timesense=TimeSense(make_soul_state(T0), clock),
        mode_mgr=ModeManager(clock=clock),
        clock=clock,
        interval_s=1.0,
        checkpoint=CheckpointManager(data_dir),
        checkpoint_every_n=checkpoint_every_n,
    )
    return soul, clock, state_store, heartbeat_store


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
    # P0-R10：落盘为压缩快照（`.db.gz`），不再有未压缩 `.db`
    assert (checkpoint / "state.db.gz").is_file()
    assert (checkpoint / "heartbeat.db.gz").is_file()
    assert not (checkpoint / "state.db").exists()
    assert not (checkpoint / "heartbeat.db").exists()

    # 快照后继续写入
    _make_db(state_db, 100)
    assert _count_rows(state_db) == 100

    # 回滚 → 恢复快照时刻状态
    restored = mgr.restore(checkpoint)
    assert _count_rows(state_db) == 10
    assert _count_rows(heartbeat_db) == 5
    # 如实返回"覆盖了什么"（回滚脚本要打印它）
    assert {p.name for p in restored} == {"state.db", "heartbeat.db"}
    # 回滚后不应残留临时文件（`.restoring`）
    assert not list(data_dir.glob(".*.restoring"))


@pytest.mark.asyncio
async def test_snapshot_is_compressed_and_significantly_smaller(tmp_path: Path) -> None:
    """取消测试（P0-R10）：快照必须**压缩落盘**且体积显著小于原库。

    还原成"不压缩"（旧写法：直接备份成 `.db`）时本用例必红：
    `state.db.gz` 不存在、体积断言无对象。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"
    _make_db(state_db, 2000)
    _make_db(heartbeat_db, 2000)
    raw_size = state_db.stat().st_size

    mgr = CheckpointManager(data_dir)
    checkpoint = await mgr.create(state_db, heartbeat_db)

    compressed = checkpoint / "state.db.gz"
    assert compressed.is_file(), "快照必须压缩落盘（.db.gz）"
    assert not (checkpoint / "state.db").exists()

    # ① 压缩后显著更小（>2x）
    assert compressed.stat().st_size * 2 < raw_size, (
        f"压缩后 {compressed.stat().st_size} B 对比原库 {raw_size} B，压缩没有生效？"
    )
    # ② 解压后必须是**可读的真库**
    assert _snapshot_quick_check(checkpoint, "state.db") == "ok"
    assert _query_snapshot(checkpoint, "state.db", "SELECT COUNT(*) FROM t")[0][0] == 2000


@pytest.mark.asyncio
async def test_restore_supports_legacy_uncompressed_snapshot(tmp_path: Path) -> None:
    """**向后兼容**：旧格式（未压缩 `.db`）快照仍必须能回滚。

    这条不能省——`p0-20261017-224508` 等旧快照就是未压缩的，回滚能力不能随着
    压缩改造一起失效（否则已有快照变废纸）。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"
    _make_db(state_db, 10)
    _make_db(heartbeat_db, 5)

    mgr = CheckpointManager(data_dir)
    legacy = mgr.checkpoint_dir / "p0-legacy"
    legacy.mkdir(parents=True)
    backup_database(state_db, legacy / "state.db")  # 旧格式：未压缩
    backup_database(heartbeat_db, legacy / "heartbeat.db")
    assert not (legacy / "state.db.gz").exists()

    _make_db(state_db, 100)  # 回滚点之后的生活

    mgr.restore(legacy)

    assert _count_rows(state_db) == 10
    assert _count_rows(heartbeat_db) == 5


@pytest.mark.asyncio
async def test_restore_supports_mixed_snapshot(tmp_path: Path) -> None:
    """混合快照（一个库是旧格式、另一个是新压缩格式）也必须能回滚。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"
    _make_db(state_db, 10)
    _make_db(heartbeat_db, 7)

    mgr = CheckpointManager(data_dir)
    mixed = mgr.checkpoint_dir / "p0-mixed"
    mixed.mkdir(parents=True)
    with gzip.open(mixed / "state.db.gz", "wb") as handle:
        handle.write(state_db.read_bytes())
    backup_database(heartbeat_db, mixed / "heartbeat.db")  # 未压缩

    _make_db(state_db, 100)
    _make_db(heartbeat_db, 100)

    mgr.restore(mixed)

    assert _count_rows(state_db) == 10
    assert _count_rows(heartbeat_db) == 7


@pytest.mark.asyncio
async def test_restore_rejects_corrupt_snapshot_without_touching_live_dbs(
    tmp_path: Path,
) -> None:
    """坏快照（.gz 损坏 / 校验不过）⇒ 中止回滚，**现役库一个字节都不动**。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"
    _make_db(state_db, 100)
    _make_db(heartbeat_db, 100)

    mgr = CheckpointManager(data_dir)
    corrupt = mgr.checkpoint_dir / "p0-corrupt"
    corrupt.mkdir(parents=True)
    with gzip.open(corrupt / "state.db.gz", "wb") as handle:
        handle.write(state_db.read_bytes())
    (corrupt / "heartbeat.db.gz").write_bytes(b"this is not a gzip stream")
    before_state = state_db.read_bytes()
    before_heartbeat = heartbeat_db.read_bytes()

    with pytest.raises(RuntimeError, match=r"快照读取失败|校验"):
        mgr.restore(corrupt)

    assert state_db.read_bytes() == before_state, "坏快照不得覆盖现役库"
    assert heartbeat_db.read_bytes() == before_heartbeat
    assert not list(data_dir.glob(".*.restoring")), "临时文件必须清理干净"


@pytest.mark.asyncio
async def test_restore_rejects_incomplete_checkpoint(tmp_path: Path) -> None:
    """快照缺库时必须明确报错，不得拿半份快照去覆盖她的现在。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mgr = CheckpointManager(data_dir)
    broken = mgr.checkpoint_dir / "p0-broken"
    broken.mkdir(parents=True)
    _make_db(broken / "state.db", 1)  # 故意缺 heartbeat.db

    with pytest.raises(FileNotFoundError, match=r"heartbeat\.db"):
        mgr.restore(broken)


@pytest.mark.asyncio
async def test_checkpoint_latest(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_db(data_dir / "state.db", 1)
    _make_db(data_dir / "heartbeat.db", 1)
    mgr = CheckpointManager(data_dir)
    assert mgr.latest() is None

    first = await mgr.create(data_dir / "state.db", data_dir / "heartbeat.db")
    second = await mgr.create(data_dir / "state.db", data_dir / "heartbeat.db")
    assert mgr.latest() == second
    assert first in mgr.list_checkpoints()


async def _wait_for_periodic_checkpoint(
    data_dir: Path, *, timeout_s: float = 10.0
) -> tuple[list[Path], int, float]:
    """轮询等待周期快照出现（不等"固定几拍"——第 N 拍的 .backup+gzip 在线程里跑）。

    返回 `(快照列表, 轮询次数, 实际等待秒数)`；超时也返回最后一次列表（由调用方断言）。
    """
    mgr = CheckpointManager(data_dir)
    started = time.monotonic()
    polls = 0
    while True:
        checkpoints = mgr.list_checkpoints()
        if checkpoints:
            return checkpoints, polls, time.monotonic() - started
        if time.monotonic() - started >= timeout_s:
            return checkpoints, polls, time.monotonic() - started
        polls += 1
        await asyncio.sleep(0.05)


async def _run_until_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
    *,
    slow_create_s: float = 0.0,
) -> tuple[list[Path], int, float, Path]:
    """起心跳 → 等快照 → 优雅停止并关库；返回等待结果与数据目录。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    if slow_create_s > 0:
        original = CheckpointManager.create

        async def _slow_create(self: CheckpointManager, state_db: Path, heartbeat_db: Path) -> Path:
            await asyncio.sleep(slow_create_s)
            return await original(self, state_db, heartbeat_db)

        assert monkeypatch is not None, "slow_create_s 需要 monkeypatch 注入"
        monkeypatch.setattr(CheckpointManager, "create", _slow_create)
    soul, _clock, state_store, heartbeat_store = _make_heartbeat(data_dir)
    await state_store.start()
    await heartbeat_store.start()
    task = asyncio.create_task(soul.run())
    try:
        checkpoints, polls, elapsed = await _wait_for_periodic_checkpoint(data_dir)
    finally:
        await soul.stop()
        await task
        await state_store.close()
        await heartbeat_store.close()
    return checkpoints, polls, elapsed, data_dir


@pytest.mark.asyncio
async def test_heartbeat_creates_periodic_checkpoint(tmp_path: Path) -> None:
    """取消测试（P0-R1）：心跳跑够 N 拍后 `data/checkpoints/` 必须真的出现快照。

    旧写法（无接线）在本用例上必红：`list_checkpoints()` 为空、目录不存在。

    D-19（T-025）：不再"固定推进 5 拍后立刻断言"（快照的 .backup+gzip 在线程里，
    并发负载下可能在落盘前就断言）；改为**等条件成立**（最多 10s 真实超时），
    超时才 fail，并把"轮询了多少轮 / 等了多久 / 目录里有什么"打进断言消息。
    """
    checkpoints, polls, elapsed, data_dir = await _run_until_checkpoint(tmp_path)
    listing = (
        sorted(p.name for p in (data_dir / "checkpoints").iterdir())
        if (data_dir / "checkpoints").is_dir()
        else []
    )
    assert checkpoints, (
        f"等待 {polls} 轮 / {elapsed:.1f}s 仍无周期快照"
        f"（checkpoint_every_n=3）；checkpoints/ 目录内容：{listing}"
    )
    latest = checkpoints[-1]
    assert (latest / "state.db.gz").is_file()
    assert (latest / "heartbeat.db.gz").is_file()
    assert _snapshot_quick_check(latest, "state.db") == "ok"

    # 快照必须是**可读的**真库（`.backup` + gzip 产物），不是空文件
    keys = [row[0] for row in _query_snapshot(latest, "state.db", "SELECT key FROM kv")]
    assert "timesense" in keys, "快照里必须有她本拍落盘的时间状态（证明建在拍到落库之后）"


@pytest.mark.asyncio
async def test_periodic_checkpoint_waits_for_slow_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-19 确定性复现：`create()` 慢 0.3s（模拟负载下 to_thread 的 gzip）也必须等到快照。

    取消测试：把等待改回"固定 5 拍后立刻看"⇒ 在 0.3s 慢 create 下会提前看到空列表。
    """
    checkpoints, polls, elapsed, data_dir = await _run_until_checkpoint(
        tmp_path, monkeypatch, slow_create_s=0.3
    )
    listing = (
        sorted(p.name for p in (data_dir / "checkpoints").iterdir())
        if (data_dir / "checkpoints").is_dir()
        else []
    )
    assert checkpoints, (
        f"slow create(0.3s) 下等待 {polls} 轮 / {elapsed:.1f}s 仍无快照；目录：{listing}"
    )
    latest = checkpoints[-1]
    assert (latest / "state.db.gz").is_file()
    assert (latest / "heartbeat.db.gz").is_file()


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


# ── 回滚入口脚本（scripts/restore_checkpoint.py）──────────────────────────


def _run_script(data_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RESTORE_SCRIPT), "--data-dir", str(data_dir), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        timeout=120,
    )


def test_restore_script_refuses_while_she_is_running(tmp_path: Path) -> None:
    """她还在跑 ⇒ 回滚脚本必须拒绝，且**一个文件都不写**（取消测试的精神）。"""
    data_dir = tmp_path / "data"
    (data_dir / "run").mkdir(parents=True)
    _make_db(data_dir / "state.db", 3)
    _make_db(data_dir / "heartbeat.db", 3)
    mgr = CheckpointManager(data_dir)
    # 造一份快照（用同步方式：直接复制现役库）
    cp = mgr.checkpoint_dir / "p0-test"
    cp.mkdir(parents=True)
    _make_db(cp / "state.db", 3)
    _make_db(cp / "heartbeat.db", 3)
    # 她"正在跑"：pid 文件指向本测试进程（活着）
    (data_dir / "run" / "soul.pid").write_text(str(os.getpid()), encoding="utf-8")

    before = {p.name: p.stat().st_mtime_ns for p in data_dir.iterdir() if p.is_file()}
    result = _run_script(data_dir, "latest", "--yes")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "拒绝回滚" in result.stdout
    # 拒绝发生在任何写入之前：现役库没有被覆盖，也没有 pre_restore 备份目录
    after = {p.name: p.stat().st_mtime_ns for p in data_dir.iterdir() if p.is_file()}
    assert before == after
    assert not list(data_dir.glob("pre_restore-*"))


def test_restore_script_restores_legacy_snapshot(tmp_path: Path) -> None:
    """旧格式（未压缩）快照：脚本仍能回滚，且先备份现役库、清 WAL 伴生文件。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"
    _make_db(state_db, 10)
    _make_db(heartbeat_db, 5)
    mgr = CheckpointManager(data_dir)
    cp = mgr.checkpoint_dir / "p0-legacy"
    cp.mkdir(parents=True)
    backup_database(state_db, cp / "state.db")
    backup_database(heartbeat_db, cp / "heartbeat.db")
    # 现役库事后被写多（模拟"回滚点之后的生活"），且**带真实未合并 WAL**：
    # 主库仍是 10 行、`-wal` 里另有 67 行（合计读 77）—— 回滚后必须只剩 10 行。
    # ⚠️ 这里**不读**现役库（读写连接一关就会把 WAL checkpoint 掉，前提就没了）。
    _make_crash_site(state_db, extra_rows=67)
    assert (data_dir / "state.db-wal").exists()

    result = _run_script(data_dir, "latest", "--yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "回滚完成" in result.stdout
    assert "未压缩·旧格式" in result.stdout
    assert _count_rows(state_db) == 10
    assert _count_rows(heartbeat_db) == 5
    assert not (data_dir / "state.db-wal").exists()
    assert not (data_dir / "state.db-shm").exists()
    # 回滚本身可回滚：pre_restore 备份存在且是回滚前的数据（77 行）
    backups = list(data_dir.glob("pre_restore-*"))
    assert len(backups) == 1
    assert _count_rows(backups[0] / "state.db") == 77
    assert "现役库已备份" in result.stdout


@pytest.mark.asyncio
async def test_restore_script_restores_compressed_snapshot(tmp_path: Path) -> None:
    """新格式（`.db.gz`）快照：脚本如实标明"压缩"，并成功回滚。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"
    _make_db(state_db, 10)
    _make_db(heartbeat_db, 5)
    mgr = CheckpointManager(data_dir)
    await mgr.create(state_db, heartbeat_db)
    _make_db(state_db, 99)  # 回滚点之后的生活

    result = _run_script(data_dir, "latest", "--yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "回滚完成" in result.stdout
    assert "压缩" in result.stdout
    assert _count_rows(state_db) == 10
    assert _count_rows(heartbeat_db) == 5


def test_restore_script_lists_compressed_and_legacy(tmp_path: Path) -> None:
    """`--list` 必须同时显示新旧两种格式（压缩与否是人工核对的关键信息）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_db(data_dir / "state.db", 3)
    _make_db(data_dir / "heartbeat.db", 3)
    mgr = CheckpointManager(data_dir)
    legacy = mgr.checkpoint_dir / "p0-a-legacy"
    legacy.mkdir(parents=True)
    backup_database(data_dir / "state.db", legacy / "state.db")
    backup_database(data_dir / "heartbeat.db", legacy / "heartbeat.db")
    compressed = mgr.checkpoint_dir / "p0-b-compressed"
    compressed.mkdir(parents=True)
    for name in ("state.db", "heartbeat.db"):
        with gzip.open(compressed / f"{name}.gz", "wb") as handle:
            handle.write((data_dir / name).read_bytes())

    result = _run_script(data_dir, "--list")

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "未压缩·旧格式" in out
    assert "压缩" in out


def test_restore_script_reports_missing_checkpoint(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_db(data_dir / "state.db", 1)
    _make_db(data_dir / "heartbeat.db", 1)
    result = _run_script(data_dir, "latest", "--yes")
    assert result.returncode == 3
    assert "没有可回滚的快照" in result.stdout or "没有任何检查点" in result.stdout
