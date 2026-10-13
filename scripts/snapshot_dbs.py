"""把 SQLite 库做成**事务一致快照**（供 `scripts/backup.ps1` 调用）。

为什么需要它：她运行时的 SQLite 处于 WAL 模式，`heartbeat.db` + `-wal` + `-shm`
的**字节级拷贝不等于事务一致的快照**（可能拷到写了一半的页）。本脚本走
`core.checkpoint.backup_database`（SQLite 原生 `.backup` 协议，读事务内完成），
因此**她照常跑也能拿到一致快照**。

用法:
    python scripts/snapshot_dbs.py <src_db> <dest_db> [<src_db> <dest_db> ...]

退出码: 0 = 全部成功（源不存在会跳过并记 [skip]）；1 = 有库快照失败（清单打到 stderr）
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from elysia.core.checkpoint import backup_database


def _force_utf8_console() -> None:
    """控制台编码兜底：Windows 默认代码页不是 UTF-8 时中文会变乱码。"""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def main(pairs: list[str]) -> int:
    _force_utf8_console()
    if not pairs or len(pairs) % 2 != 0:
        print("用法：snapshot_dbs.py <src_db> <dest_db> [<src> <dest> ...]", file=sys.stderr)
        return 2
    failed: list[str] = []
    for i in range(0, len(pairs), 2):
        src, dest = Path(pairs[i]), Path(pairs[i + 1])
        if not src.exists():
            print(f"[skip] {src} 不存在")
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            backup_database(src, dest)
            print(f"[ok] {src.name} -> {dest}")
        except Exception as exc:
            failed.append(f"{src}: {exc}")
            print(f"[fail] {src}: {exc}", file=sys.stderr)
    if failed:
        print(f"共 {len(failed)} 个库快照失败", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
