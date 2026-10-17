"""检查点 / 回滚 / 紧急冻结（路线图 5.6，P0 落地）。

- 检查点：SQLite `.backup` 到 `data/checkpoints/<version>-<ts>/`，保留最近 10 份
- 回滚：恢复快照两个库（须在进程停止后执行）
- 紧急冻结：`data/freeze` 标记文件存在 → 灵魂仅记录心跳，
  禁止一切输出与行为（恢复需人工 unfreeze）

回滚是「地基优先」铁律的工程前提——有退路，才有底气修。

P0-R10（D-9 方案 A）：**快照压缩**
--------------------------------
每 30 分钟一份**完整**快照，实测她一份 = 96.8 MB，而她的库以 ~45.6 MB/天增长
⇒ 10 份保留现在 0.93 GB、1 个月 16 GB、3 个月 45 GB（C 盘余 110 GB，2–3 个月撞墙）。
实测 `gzip -6` 把 `heartbeat.db` 从 96.8 MB 压到 **9.3 MB（10.4x，1.7s）**
⇒ 10 份约 **93 MB**，几乎免费解决问题。

- **备份协议不变**：仍走 SQLite `.backup`（事务一致快照），只是落盘形态从
  `<name>.db` 变成 `<name>.db.gz`；
- **向后兼容**：`restore()` 优先使用同目录下**未压缩**的 `.db`（旧快照
  `p0-20261017-224508` 就是未压缩的，必须仍可回滚），否则用 `.gz` 解压；
- **原子性**：快照先写临时文件再 `os.replace`；回滚先把所有库解到临时文件并
  `quick_check` 通过后才覆盖现役库（坏快照绝不会"覆盖一半"）。
"""

from __future__ import annotations

import asyncio
import gzip
import os
import shutil
import sqlite3
import time
from pathlib import Path

CHECKPOINT_KEEP = 10  # 保留最近 10 份快照
FREEZE_MARKER_NAME = "freeze"  # 紧急冻结标记文件
# 快照落盘形态：gzip 压缩（P0-R10）。压缩级别 6 = 实测 96.8MB→9.3MB / 1.7s 的性价比点。
CHECKPOINT_GZ_SUFFIX = ".gz"
CHECKPOINT_COMPRESS_LEVEL = 6
_STREAM_CHUNK = 1024 * 1024  # 压缩/解压时的流式块大小（1 MiB）

# SQLite WAL 模式的**伴生文件**后缀。回滚覆盖前必须一并删除：
# 她跑在 WAL 模式，若把旧 `-wal` 留在原地，下次打开会把"回滚前未合并的帧"
# **重放到刚恢复的库上** ⇒ 回滚不但没回滚，还把库搞坏（P0 审计 D-2）。
# `scripts/backup.ps1` 的保留策略早就懂这个陷阱（它显式删 -wal/-shm）；
# 本模块此前**不懂**——同一工程里两套口径，实测代价是"回滚变二次损坏"。
WAL_COMPANION_SUFFIXES = ("-wal", "-shm")

# 一份检查点必须覆盖的两个库（单一出处：创建与回滚都按这张表走）
_DB_NAMES: tuple[str, ...] = ("state.db", "heartbeat.db")


def _compressed_name(name: str) -> str:
    """`state.db` → `state.db.gz`。"""
    return f"{name}{CHECKPOINT_GZ_SUFFIX}"


def backup_database(src: Path, dst: Path) -> None:
    """SQLite **在线**备份（`.backup` 协议，线程安全、事务一致）。

    **为什么不能用文件拷贝**：她运行时的 SQLite 处于 WAL 模式，
    `db` + `-wal` + `-shm` 的**字节级拷贝不等于事务一致的快照**（可能拷到写了一半的页）。
    `.backup` 走 SQLite 自己的备份协议、在读事务内完成，故**她照常跑也能拿到一致快照**。
    `scripts/backup.ps1` → `scripts/snapshot_dbs.py` 复用的就是本函数
    （19.4 缺点 5 记的"备份原子性"由此闭合；ADR-002 里"robocopy 天然覆盖 WAL"的
    旧断言已在更正节里纠正）。
    """
    dst.unlink(missing_ok=True)  # 目标须是干净文件（页大小可能与源不同）
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def backup_database_compressed(src: Path, dst_gz: Path) -> None:
    """事务一致备份 → gzip 压缩落盘（P0-R10）。

    顺序刻意如此：**先**用 `.backup` 在临时文件里拿到一致快照，**再**压缩到临时
    `.gz`，最后 `os.replace` 原子改名。任何一步失败都不会留下一份"看起来可用"的
    半份快照（上一轮踩过"文档说有了、实际没有"的坑，这里不重复）。
    """
    tmp_db = dst_gz.with_name(f".{dst_gz.name}.backup.tmp")
    tmp_gz = dst_gz.with_name(f".{dst_gz.name}.writing.tmp")
    try:
        backup_database(src, tmp_db)
        with (
            open(tmp_db, "rb") as fin,
            gzip.open(tmp_gz, "wb", compresslevel=CHECKPOINT_COMPRESS_LEVEL) as fout,
        ):
            shutil.copyfileobj(fin, fout, length=_STREAM_CHUNK)
        os.replace(tmp_gz, dst_gz)
    finally:
        tmp_db.unlink(missing_ok=True)
        tmp_gz.unlink(missing_ok=True)


def decompress_file(src_gz: Path, dst: Path) -> None:
    """把 `.gz` 解压成 `dst`（流式，不在内存里放整库）。"""
    with gzip.open(src_gz, "rb") as fin, open(dst, "wb") as fout:
        shutil.copyfileobj(fin, fout, length=_STREAM_CHUNK)


def quick_check_db(path: Path) -> str:
    """`PRAGMA quick_check`：返回 `"ok"` 或 SQLite 给出的具体错误行（只读打开）。"""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
    try:
        row = con.execute("PRAGMA quick_check").fetchone()
    finally:
        con.close()
    return str(row[0]) if row else "（无返回）"


class CheckpointManager:
    """状态库快照管理（快照创建 / 列表 / 回滚 / 冻结）。"""

    def __init__(self, data_dir: Path, *, version: str = "p0") -> None:
        self._data_dir = data_dir
        self._version = version

    @property
    def data_dir(self) -> Path:
        """现役库所在目录（周期快照的源；供心跳等调用方定位 `state.db`/`heartbeat.db`）。"""
        return self._data_dir

    @property
    def checkpoint_dir(self) -> Path:
        return self._data_dir / "checkpoints"

    async def create(self, state_db: Path, heartbeat_db: Path) -> Path:
        """生成快照（命名含版本与时间戳，**落盘为 `.db.gz`**），清理超龄快照。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.checkpoint_dir / f"{self._version}-{stamp}"
        suffix = 2
        while target.exists():
            target = self.checkpoint_dir / f"{self._version}-{stamp}-{suffix}"
            suffix += 1
        target.mkdir(parents=True, exist_ok=True)
        try:
            # 两个库互相独立 ⇒ 并行做（backup 是 I/O、gzip 释放 GIL，实测能重叠）
            await asyncio.gather(
                asyncio.to_thread(
                    backup_database_compressed, state_db, target / _compressed_name("state.db")
                ),
                asyncio.to_thread(
                    backup_database_compressed,
                    heartbeat_db,
                    target / _compressed_name("heartbeat.db"),
                ),
            )
        except Exception:
            # 半份快照绝不留：删掉整个目录再抛出，让调用方看到失败
            shutil.rmtree(target, ignore_errors=True)
            raise
        self._prune()
        return target

    def list_checkpoints(self) -> list[Path]:
        if not self.checkpoint_dir.exists():
            return []
        return sorted(
            (p for p in self.checkpoint_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name,
        )

    def latest(self) -> Path | None:
        """最新一份快照（没有则 None）——回滚入口的 `latest` 语义。"""
        checkpoints = self.list_checkpoints()
        return checkpoints[-1] if checkpoints else None

    def _source_for(self, checkpoint: Path, name: str) -> tuple[Path, bool]:
        """返回 `(源文件, 是否压缩)`。**旧格式优先**：未压缩 `.db` 存在就用它（向后兼容）。"""
        plain = checkpoint / name
        if plain.is_file():
            return plain, False
        compressed = checkpoint / _compressed_name(name)
        if compressed.is_file():
            return compressed, True
        raise FileNotFoundError(f"检查点缺 {name}（也缺 {_compressed_name(name)}）：{checkpoint}")

    def restore(self, checkpoint: Path) -> list[Path]:
        """回滚：用快照覆盖两个库（先停止灵魂/身体进程）。

        两段式，**先把所有库准备成可用的临时文件并校验，再一次性替换现役库**：
        1. 每个库：旧格式 `.db` 直接复制 / 新格式 `.gz` 解压 → `.<name>.restoring`
        2. 对临时文件跑 `PRAGMA quick_check`，任一份不是 `ok` ⇒ 抛错，**现役库一个字节都不动**
        3. 删除 `-wal`/`-shm` 伴生文件后 `os.replace` 覆盖两个库

        **必须同时删除 `-wal`/`-shm` 伴生文件**（`WAL_COMPANION_SUFFIXES`）。
        实测（P0 审计 D-2，2026-10-17）：快照 10 行 → 崩溃时 `-wal` 里另有 90 行
        未合并 → 只 copy `.db` 回滚 → 重新打开读到 **100 行**。
        即：少了这步，"回滚"会把回滚前的事务重放回来，**等于没回滚**，
        而且调用方会以为恢复成功（最坏的失败模式：假装成功）。

        返回**实际被覆盖**的现役文件清单（供回滚脚本如实打印"覆盖了什么"）。
        """
        if not checkpoint.is_dir():
            raise FileNotFoundError(f"检查点不存在或不是目录：{checkpoint}")
        missing = [
            name
            for name in _DB_NAMES
            if not (checkpoint / name).is_file()
            and not (checkpoint / _compressed_name(name)).is_file()
        ]
        if missing:
            raise FileNotFoundError(f"检查点不完整（缺 {', '.join(missing)}）：{checkpoint}")

        staged: dict[str, Path] = {}
        try:
            # ① 先把两份库都准备成临时文件（解压/复制），此时**没有动现役库**
            for name in _DB_NAMES:
                src, compressed = self._source_for(checkpoint, name)
                tmp = self._data_dir / f".{name}.restoring"
                staged[name] = tmp  # 先登记：解压失败时 finally 也会清掉半份临时文件
                try:
                    if compressed:
                        decompress_file(src, tmp)
                    else:
                        shutil.copy2(src, tmp)
                except Exception as exc:
                    raise RuntimeError(
                        f"快照读取失败（{name}：{type(exc).__name__}: {exc}）"
                        "——已中止回滚，现役库未改动"
                    ) from exc
            # ② 校验：坏快照绝不覆盖她的现在
            for name, tmp in staged.items():
                result = quick_check_db(tmp)
                if result != "ok":
                    raise RuntimeError(
                        f"快照校验未通过（{name}：{result}）——已中止回滚，现役库未改动"
                    )
            # ③ 替换：先清 WAL/SHM 伴生文件，再原子覆盖（顺序反了会留下"新库+旧 WAL"）
            restored: list[Path] = []
            for name in _DB_NAMES:
                dst = self._data_dir / name
                for suffix in WAL_COMPANION_SUFFIXES:
                    Path(f"{dst}{suffix}").unlink(missing_ok=True)
                os.replace(staged.pop(name), dst)
                restored.append(dst)
            return restored
        finally:
            for leftover in staged.values():
                leftover.unlink(missing_ok=True)

    def _prune(self) -> None:
        checkpoints = self.list_checkpoints()
        for stale in checkpoints[:-CHECKPOINT_KEEP]:
            shutil.rmtree(stale, ignore_errors=True)

    # ── 紧急冻结 ──────────────────────────────────────────

    @property
    def freeze_marker(self) -> Path:
        return self._data_dir / FREEZE_MARKER_NAME

    def is_frozen(self) -> bool:
        """冻结中：仅记录心跳，禁止一切输出与行为。"""
        return self.freeze_marker.exists()

    def freeze(self) -> None:
        self.freeze_marker.touch()

    def unfreeze(self) -> None:
        self.freeze_marker.unlink(missing_ok=True)
