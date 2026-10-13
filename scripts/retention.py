r"""数据保留：**先归档，再裁剪 heartbeats 流水，最后 VACUUM**。

用法（**必须先停她** —— 一边裁一边写会撞写锁）：

```powershell
cd <项目根>
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop
python scripts\retention.py --keep-days 90              # 干跑：只看计划（缺省就是干跑）
python scripts\retention.py --keep-days 90 --apply      # 真做
```

口径与三条安全约束见 `src/elysia/core/retention.py` 的模块 docstring
（只裁遥测流水 `heartbeats`，**绝不碰** `memories` / `thought_log` / `expression_log`）。

退出码: 0 成功（含"没东西可裁"）／ 1 被安全检查拦下或执行失败 ／ 2 参数错误
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

import psutil

from elysia.core.retention import prune_heartbeats

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PID_FILE = PROJECT_ROOT / "data" / "run" / "soul.pid"


def _force_utf8_console() -> None:
    """控制台编码兜底：Windows 默认代码页不是 UTF-8 时中文会变乱码。"""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def _running_soul_pid() -> int | None:
    """灵魂进程若在跑就返回它的 PID（用项目自带的 psutil，不引入新依赖）。"""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if psutil.pid_exists(pid) else None


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(description="Elysia 数据保留（归档 + 裁剪 + VACUUM）")
    parser.add_argument(
        "--keep-days", type=float, default=90.0, help="保留最近多少天的心跳流水（默认 90）"
    )
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "heartbeat.db")
    parser.add_argument("--archive-dir", type=Path, default=PROJECT_ROOT / "data" / "archive")
    parser.add_argument("--apply", action="store_true", help="真正执行（缺省为干跑）")
    parser.add_argument("--force", action="store_true", help="跳过'她必须先停'检查（危险，不推荐）")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"[skip] 库不存在：{args.db}")
        return 0

    pid = _running_soul_pid()
    if pid is not None and not args.force:
        print(
            f"[拒绝] 灵魂仍在运行（PID {pid}）——一边裁一边写会撞写锁。请先执行：\n"
            f"        powershell -ExecutionPolicy Bypass -File scripts\\soul.ps1 stop",
            file=sys.stderr,
        )
        return 1

    report = prune_heartbeats(
        args.db,
        keep_days=args.keep_days,
        archive_dir=args.archive_dir,
        dry_run=not args.apply,
    )

    print(f"[{'干跑' if report.dry_run else '已执行'}] 库 = {report.db_path}")
    print(f"           保留窗口 = 最近 {report.keep_days:g} 天")
    print(f"           待裁流水 = {report.prunable_rows} 行")
    if report.dry_run:
        print("           （干跑：未归档、未删除、未 VACUUM；加 --apply 真做）")
        return 0
    if report.prunable_rows == 0:
        print("           没有需要裁剪的流水")
        return 0
    mb = 1024 * 1024
    print(f"           已归档 = {report.archived_rows} 行 -> {report.archive_path}")
    print(f"           已删除 = {report.deleted_rows} 行；VACUUM = {report.vacuumed}")
    print(
        f"           体积 {report.size_before / mb:.2f} MB -> {report.size_after / mb:.2f} MB"
        f"（回收 {report.freed_bytes / mb:.2f} MB）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
