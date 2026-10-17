"""检查点单测：快照创建 / 回滚一致性 / 保留清理 / 紧急冻结。

P0-R1（D-2）新增两块**取消测试**（还原旧写法必须红）：

1. `test_restore_discards_unmerged_wal`：她跑 WAL 模式，回滚覆盖前必须删掉
   `-wal`/`-shm`，否则旧 WAL 会被重放到刚恢复的库上。
   **实测旧写法**：快照 10 行 → 崩溃时 `-wal` 里另有 90 行 → 只 copy `.db` 回滚
   → 重新打开读到 **100 行**（回滚等于没回滚，且调用方会以为成功）。
2. `test_heartbeat_creates_periodic_checkpoint`：心跳每 N 拍必须真的**生成**快照。
   **实测旧写法**：`CheckpointManager.create()` 全仓 0 调用者 ⇒ `data/checkpoints/` 不存在。
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from elysia.core.checkpoint import CHECKPOINT_KEEP, CheckpointManager
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
    assert (checkpoint / "state.db").exists()
    assert (checkpoint / "heartbeat.db").exists()

    # 快照后继续写入
    _make_db(state_db, 100)
    assert _count_rows(state_db) == 100

    # 回滚 → 恢复快照时刻状态
    restored = mgr.restore(checkpoint)
    assert _count_rows(state_db) == 10
    assert _count_rows(heartbeat_db) == 5
    # 如实返回"覆盖了什么"（回滚脚本要打印它）
    assert {p.name for p in restored} == {"state.db", "heartbeat.db"}


@pytest.mark.asyncio
async def test_restore_discards_unmerged_wal(tmp_path: Path) -> None:
    """取消测试（P0-R1）：回滚必须删掉 `-wal`/`-shm`，否则旧 WAL 反噬刚恢复的库。

    旧写法（只 copy 两个 `.db`）在本用例上**必红**：读到 100 行而非快照的 10 行。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"
    _make_db(state_db, 10)
    _make_db(heartbeat_db, 5)

    mgr = CheckpointManager(data_dir)
    checkpoint = await mgr.create(state_db, heartbeat_db)

    # 造崩溃现场：现役库被"主库 10 行 + WAL 里 90 行"替换
    _make_crash_site(state_db, extra_rows=90)
    assert (data_dir / "state.db-wal").exists(), "用例前提：崩溃现场必须带未合并 WAL"

    mgr.restore(checkpoint)

    # ① 内容 = 快照内容（旧写法在这里读到 100）
    assert _count_rows(data_dir / "state.db") == 10
    assert _count_rows(data_dir / "heartbeat.db") == 5
    # ② 伴生文件必须清除（残留 = 下次打开会把回滚前的帧重放回来）
    for name in ("state.db", "heartbeat.db"):
        for suffix in ("-wal", "-shm"):
            assert not (data_dir / f"{name}{suffix}").exists(), f"{name}{suffix} 未清除"


def test_restore_rejects_incomplete_checkpoint(tmp_path: Path) -> None:
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


@pytest.mark.asyncio
async def test_heartbeat_creates_periodic_checkpoint(tmp_path: Path) -> None:
    """取消测试（P0-R1）：心跳跑够 N 拍后 `data/checkpoints/` 必须真的出现快照。

    旧写法（无接线）在本用例上必红：`list_checkpoints()` 为空、目录不存在。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    soul, clock, state_store, heartbeat_store = _make_heartbeat(data_dir)
    await state_store.start()
    await heartbeat_store.start()
    task = asyncio.create_task(soul.run())
    try:
        # 虚拟 5 拍（checkpoint_every_n=3 ⇒ 至少落一份）
        await clock.sleep(5.0)
    finally:
        await soul.stop()
        await task
        await state_store.close()
        await heartbeat_store.close()

    checkpoints = CheckpointManager(data_dir).list_checkpoints()
    assert checkpoints, "心跳跑够 N 拍后必须生成周期快照（旧写法：0 份）"
    latest = checkpoints[-1]
    assert (latest / "state.db").is_file()
    assert (latest / "heartbeat.db").is_file()

    # 快照必须是**可读的**真库（`.backup` 协议产物），不是空文件
    conn = sqlite3.connect(str(latest / "state.db"))
    try:
        keys = [row[0] for row in conn.execute("SELECT key FROM kv")]
    finally:
        conn.close()
    assert "timesense" in keys, "快照里必须有她本拍落盘的时间状态（证明建在拍到落库之后）"


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


def test_restore_script_restores_and_backs_up(tmp_path: Path) -> None:
    """她已停止 ⇒ 回滚成功、先备份现役库、并清除 WAL 伴生文件。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"
    _make_db(state_db, 10)
    _make_db(heartbeat_db, 5)
    mgr = CheckpointManager(data_dir)
    cp = mgr.checkpoint_dir / "p0-test"
    cp.mkdir(parents=True)
    _make_db(cp / "state.db", 10)
    _make_db(cp / "heartbeat.db", 5)
    # 现役库事后被写多（模拟"回滚点之后的生活"），且**带真实未合并 WAL**：
    # 主库仍是 10 行、`-wal` 里另有 67 行（合计读 77）—— 回滚后必须只剩 10 行。
    # ⚠️ 这里**不读**现役库（读写连接一关就会把 WAL checkpoint 掉，前提就没了）。
    _make_crash_site(state_db, extra_rows=67)
    assert (data_dir / "state.db-wal").exists()

    result = _run_script(data_dir, "latest", "--yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "回滚完成" in result.stdout
    assert _count_rows(state_db) == 10
    assert _count_rows(heartbeat_db) == 5
    assert not (data_dir / "state.db-wal").exists()
    assert not (data_dir / "state.db-shm").exists()
    # 回滚本身可回滚：pre_restore 备份存在且是回滚前的数据（77 行）
    backups = list(data_dir.glob("pre_restore-*"))
    assert len(backups) == 1
    assert _count_rows(backups[0] / "state.db") == 77
    assert "现役库已备份" in result.stdout


def test_restore_script_reports_missing_checkpoint(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_db(data_dir / "state.db", 1)
    _make_db(data_dir / "heartbeat.db", 1)
    result = _run_script(data_dir, "latest", "--yes")
    assert result.returncode == 3
    assert "没有可回滚的快照" in result.stdout or "没有任何检查点" in result.stdout
