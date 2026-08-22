"""检查点 / 回滚 / 紧急冻结（路线图 5.6，P0 落地）。

- 检查点：SQLite .backup 到 data/checkpoints/<version>-<ts>/，保留最近 10 份
- 回滚：恢复快照两个库（须在进程停止后执行）
- 紧急冻结：data/freeze 标记文件存在 → 灵魂仅记录心跳，
  禁止一切输出与行为（恢复需人工 unfreeze）

回滚是「地基优先」铁律的工程前提——有退路，才有底气修。
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import time
from pathlib import Path

CHECKPOINT_KEEP = 10  # 保留最近 10 份快照
FREEZE_MARKER_NAME = "freeze"  # 紧急冻结标记文件


def _backup_file(src: Path, dst: Path) -> None:
    """SQLite 在线备份（.backup API，线程安全）。"""
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


class CheckpointManager:
    """状态库快照管理（快照创建 / 列表 / 回滚 / 冻结）。"""

    def __init__(self, data_dir: Path, *, version: str = "p0") -> None:
        self._data_dir = data_dir
        self._version = version

    @property
    def checkpoint_dir(self) -> Path:
        return self._data_dir / "checkpoints"

    async def create(self, state_db: Path, heartbeat_db: Path) -> Path:
        """生成快照（命名含版本与时间戳），清理超龄快照。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.checkpoint_dir / f"{self._version}-{stamp}"
        suffix = 2
        while target.exists():
            target = self.checkpoint_dir / f"{self._version}-{stamp}-{suffix}"
            suffix += 1
        target.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_backup_file, state_db, target / "state.db")
        await asyncio.to_thread(_backup_file, heartbeat_db, target / "heartbeat.db")
        self._prune()
        return target

    def list_checkpoints(self) -> list[Path]:
        if not self.checkpoint_dir.exists():
            return []
        return sorted(
            (p for p in self.checkpoint_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name,
        )

    def restore(self, checkpoint: Path) -> None:
        """回滚：用快照覆盖两个库（先停止灵魂/身体进程）。"""
        shutil.copy2(checkpoint / "state.db", self._data_dir / "state.db")
        shutil.copy2(checkpoint / "heartbeat.db", self._data_dir / "heartbeat.db")

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
