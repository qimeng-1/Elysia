r"""数据保留：**先归档，再分层降采样/裁剪 heartbeats 流水，最后 VACUUM**。

用法（**必须先停她** —— 一边裁一边写会撞写锁）：

```powershell
cd <项目根>
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop
python scripts\retention.py                               # 干跑：只看计划（缺省就是干跑）
python scripts\retention.py --apply                       # 真做：三层降采样 + 归档 + VACUUM
python scripts\retention.py --mode prune --keep-days 90 --apply   # 旧口径：整段裁剪
```

P0-R12（遗忘机制）：三层粒度 + **把"什么算重要"交给她**
- 热（0–3 天）原样 1Hz；温（3–30 天）每分钟一行；冷（30–365 天）每小时一行；
  >365 天每天一行；
- `beat_type='event'` 永不降采样（痛苦过的证据）；
- 凡 `memories.protected=1` 或 `importance >= 阈值的记忆`，其 `created_ts` 前后各 6 小时
  的流水**永不降采样**——她认定的重要时刻，那段时间的生命流水全精度保留；
- 裁剪前先归档到 `_archive/heartbeats/heartbeat-YYYYMM.db.gz`（可解压回查）；
- `memories` / `memory_index` / `thought_log` / `expression_log` **一行不动**（跑完核对）。

口径与安全约束见 `src/elysia/core/retention.py` 的模块 docstring。

退出码: 0 成功（含"没东西可裁"）／ 1 被安全检查拦下或执行失败 ／ 2 参数错误
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

import psutil

from elysia.core.retention import (
    COLD_DAYS,
    HOT_DAYS,
    IMPORTANT_IMPORTANCE,
    PROTECTED_WINDOW_S,
    WARM_DAYS,
    TierReport,
    prune_heartbeats,
    tier_heartbeats,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PID_FILE = PROJECT_ROOT / "data" / "run" / "soul.pid"
# 归档在**仓库外**（与她的一生数据同级的 _archive/），不随仓库搬家
DEFAULT_ARCHIVE_DIR = PROJECT_ROOT.parent / "_archive" / "heartbeats"


def _force_utf8_console() -> None:
    """控制台编码兜底：Windows 默认代码页不是 UTF-8 时中文会变乱码。"""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def _running_soul_pid(pid_file: Path = PID_FILE) -> int | None:
    """灵魂进程若在跑就返回它的 PID（用项目自带的 psutil，不引入新依赖）。"""
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if psutil.pid_exists(pid) else None


def _print_tier_report(report: TierReport) -> None:
    mb = 1024 * 1024
    print(f"[{'干跑' if report.dry_run else '已执行'}] 库 = {report.db_path}")
    print(f"           里程碑 = {report.now:.0f}（now）")
    print(
        f"           层级行数：热(0-3d)={report.hot_rows} 待降采样={report.pending_rows} "
        f"受保护跳过={report.protected_rows} event 全精度={report.event_rows}"
    )
    if report.dry_run:
        print("           （干跑：未归档、未降采样、未 VACUUM；加 --apply 真做）")
        return
    tier_text = " ".join(
        f"{tier}={count}" for tier, count in sorted(report.tier_buckets.items()) if count
    )
    print(f"           聚合桶 = {report.aggregated_rows}（{tier_text or '无'}）")
    print(
        f"           已归档 = {report.archived_rows} 行 -> {len(report.archive_paths)} 个 gz 档案"
    )
    for path in report.archive_paths:
        print(f"                     {path}")
    print(f"           已删除原始行 = {report.deleted_rows}；VACUUM = {report.vacuumed}")
    print(
        f"           体积 {report.size_before / mb:.2f} MB -> {report.size_after / mb:.2f} MB"
        f"（回收 {report.freed_bytes / mb:.2f} MB）"
    )


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(
        description="Elysia 数据保留（归档 + 分层降采样/裁剪 + VACUUM）"
    )
    parser.add_argument(
        "--mode",
        choices=("tier", "prune"),
        default="tier",
        help="tier=三层降采样（P0-R12，默认）；prune=整段裁剪（旧口径）",
    )
    parser.add_argument(
        "--keep-days", type=float, default=90.0, help="prune 模式：保留最近多少天（默认 90）"
    )
    parser.add_argument("--hot-days", type=float, default=HOT_DAYS, help="热层天数（默认 3）")
    parser.add_argument("--warm-days", type=float, default=WARM_DAYS, help="温层上限（默认 30）")
    parser.add_argument("--cold-days", type=float, default=COLD_DAYS, help="冷层上限（默认 365）")
    parser.add_argument(
        "--importance-threshold",
        type=float,
        default=IMPORTANT_IMPORTANCE,
        help="重要度阈值：>= 它的记忆周边窗口全精度保留（默认 0.8）",
    )
    parser.add_argument(
        "--protected-window-hours",
        type=float,
        default=PROTECTED_WINDOW_S / 3600.0,
        help="重要记忆前后各保留多少小时（默认 6）",
    )
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "heartbeat.db")
    parser.add_argument(
        "--pid-file",
        type=Path,
        default=PID_FILE,
        help="灵魂 PID 文件（默认仓库 data/run/soul.pid；验收入口）",
    )
    parser.add_argument(
        "--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR, help="归档目录（gz 档案）"
    )
    parser.add_argument("--apply", action="store_true", help="真正执行（缺省为干跑）")
    parser.add_argument("--force", action="store_true", help="跳过'她必须先停'检查（危险，不推荐）")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"[skip] 库不存在：{args.db}")
        return 0

    pid = _running_soul_pid(args.pid_file)
    if pid is not None and not args.force:
        print(
            f"[拒绝] 灵魂仍在运行（PID {pid}）——一边裁一边写会撞写锁。请先执行：\n"
            f"        powershell -ExecutionPolicy Bypass -File scripts\\soul.ps1 stop",
            file=sys.stderr,
        )
        return 1

    if args.mode == "tier":
        report = tier_heartbeats(
            args.db,
            archive_dir=args.archive_dir,
            dry_run=not args.apply,
            hot_days=args.hot_days,
            warm_days=args.warm_days,
            cold_days=args.cold_days,
            importance_threshold=args.importance_threshold,
            protected_window_s=args.protected_window_hours * 3600.0,
        )
        _print_tier_report(report)
        return 0

    legacy = prune_heartbeats(
        args.db,
        keep_days=args.keep_days,
        archive_dir=args.archive_dir,
        dry_run=not args.apply,
    )
    print(f"[{'干跑' if legacy.dry_run else '已执行'}] 库 = {legacy.db_path}（旧口径 prune）")
    print(f"           保留窗口 = 最近 {legacy.keep_days:g} 天")
    print(f"           待裁流水 = {legacy.prunable_rows} 行")
    if legacy.dry_run:
        print("           （干跑：未归档、未删除、未 VACUUM；加 --apply 真做）")
        return 0
    if legacy.prunable_rows == 0:
        print("           没有需要裁剪的流水")
        return 0
    mb = 1024 * 1024
    print(f"           已归档 = {legacy.archived_rows} 行 -> {legacy.archive_path}")
    print(f"           已删除 = {legacy.deleted_rows} 行；VACUUM = {legacy.vacuumed}")
    print(
        f"           体积 {legacy.size_before / mb:.2f} MB -> {legacy.size_after / mb:.2f} MB"
        f"（回收 {legacy.freed_bytes / mb:.2f} MB）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
