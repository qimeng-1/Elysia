"""从检查点回滚她的双库 —— P0-R1 / D-2 的**可执行入口**。

为什么必须有这个脚本
--------------------
P0 审计（`docs/LEAD_P0_AUDIT_2026-10-17.md` D-2）实测三连：
`data/checkpoints/` **不存在**、`CheckpointManager.create()` **全仓 0 个调用者**、
`restore()` **不删 `-wal`/`-shm`**（她跑 WAL 模式 ⇒ 回滚后旧 WAL 被重放，
**回滚把库搞坏**）。⇒ "回滚"这条路整条都是断的，而 `IMPLEMENTATION_ROADMAP §5.6`
却写着"已落地"。本脚本 + 心跳周期快照 + `restore()` 的 WAL 修复，把这条路接通。

本脚本把回滚的**安全前提机械化**（不靠人记住）：

1. **她还在跑就拒绝** —— 两个独立信号（PID 文件里的活进程 + 心跳新鲜度），
   任一为真即拒绝：回滚运行中的库＝覆盖她的现在，并撕裂 WAL。
   （只读 `heartbeat.db` 判活，不改 `-shm`。）
2. **先备份现役库** —— 回滚本身也必须可回滚：备份到 `data/pre_restore-<ts>/`，
   同样走 SQLite `.backup`（事务一致，不拷半页）。
3. **如实打印恢复来源** —— 快照名、创建时间、体积、覆盖了哪几个文件；
   恢复后对两个库跑 `PRAGMA quick_check`：不是"应该好了"，是"读得通"。
4. **绝不假装成功** —— 快照不完整 / 库校验不过 / 判活失败 ⇒ 明确报错并给原因。

用法
----
    .venv\\Scripts\\python.exe scripts\\restore_checkpoint.py --list
    .venv\\Scripts\\python.exe scripts\\restore_checkpoint.py                 # 最新一份
    .venv\\Scripts\\python.exe scripts\\restore_checkpoint.py latest
    .venv\\Scripts\\python.exe scripts\\restore_checkpoint.py p0-20261017-221500
    .venv\\Scripts\\python.exe scripts\\restore_checkpoint.py latest --dry-run
    .venv\\Scripts\\python.exe scripts\\restore_checkpoint.py latest --yes    # 免交互确认
    ... --data-dir <dir>    # 备用库目录（默认仓库 data/；验收/测试用）

退出码
------
    0 成功（或 --list / --dry-run 正常结束）
    1 执行失败（快照不完整 / 备份失败 / 恢复失败 / 校验不过）
    2 被她占用而拒绝（她还在跑）
    3 用法错误或快照不存在
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import sqlite3
import sys
import time
from pathlib import Path

from elysia.core.checkpoint import CheckpointManager, backup_database

ROOT = Path(__file__).resolve().parent.parent

# 心跳新鲜度判活阈值：灵魂 1Hz（实测中位 1.012s）⇒ 60s 无新 soul 心跳 = 她不在跑。
# 与 D-3 的看门狗判据同源（`scripts/watchdog.ps1`），两处口径不得背离。
SOUL_STALE_S = 60.0

# 可能的 PID 文件（`data/run/`）。桌宠没有自己的 pid 文件，故这里只列有文件的两个。
PID_NAMES = ("soul", "body")

EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2, 3


class Refused(Exception):
    """她还在跑（或判活失败）⇒ 拒绝执行，**且不写任何东西**。"""


def _force_utf8_console() -> None:
    """控制台编码兜底：Windows 默认代码页不是 UTF-8 时中文会变乱码。"""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def say(text: str = "") -> None:
    """打印（`pythonw` 下 `sys.stdout` 可能是 None —— 面板已踩过这个坑）。"""
    with contextlib.suppress(Exception):
        print(text)


def _fmt_ts(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def _ro_connect(db: Path) -> sqlite3.Connection:
    """**只读**打开库（`mode=ro`；实测不改 `-shm`）。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _pid_alive(pid: int) -> bool | None:
    """进程是否活着；**无法判定时返回 None**（绝不猜成"没在跑"）。

    ⚠️ 不用 `os.kill(pid, 0)`：Windows 上它可能落到 `TerminateProcess`
    —— 判活把她的进程杀了，是这里最坏的事故。
    """
    try:
        import psutil  # 已声明依赖（面板同款）
    except ImportError:
        return None
    try:
        return bool(psutil.pid_exists(pid))
    except Exception:
        return None


def _read_pid_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def last_soul_beat_ts(heartbeat_db: Path) -> float | None:
    """`heartbeat.db` 最后一条 soul 心跳的 ts；读不到返回 None。"""
    if not heartbeat_db.exists():
        return None
    try:
        con = _ro_connect(heartbeat_db)
        try:
            row = con.execute(
                "SELECT ts FROM heartbeats WHERE beat_type='soul' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return float(row[0]) if row else None


def probe_running(data_dir: Path) -> list[str]:
    """判活：返回"她在跑"的证据清单（空 = 可以回滚）。

    两个**独立**信号，任一为真即视为在跑（单信号会假阴性 —— 面板已实测过一次）：
      ① `data/run/{soul,body}.pid` 指向的进程仍存在；
      ② `heartbeat.db` 最后一条 soul 心跳在 `SOUL_STALE_S` 之内。
    判活本身失败（psutil 不可用）时按"在跑"处理并说明原因 —— 宁可拒绝，不可赌。
    """
    evidence: list[str] = []
    for name in PID_NAMES:
        pid = _read_pid_file(data_dir / "run" / f"{name}.pid")
        if pid is None:
            continue
        alive = _pid_alive(pid)
        if alive is None:
            raise Refused(
                f"无法判定 {name}.pid={pid} 是否还活着（psutil 不可用）——"
                "判活不确定时不允许回滚，请先人工确认她已停止"
            )
        if alive:
            evidence.append(f"{name}.pid={pid} 进程仍在")

    beat_ts = last_soul_beat_ts(data_dir / "heartbeat.db")
    if beat_ts is not None:
        age = max(0.0, time.time() - beat_ts)
        if age < SOUL_STALE_S:
            evidence.append(f"心跳仍在跳（最后一条 soul 心跳 {age:.1f}s 前）")
    return evidence


def quick_check(db: Path) -> str:
    """`PRAGMA quick_check`：返回 "ok" 或 SQLite 给出的具体错误行。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10.0)
    try:
        row = con.execute("PRAGMA quick_check").fetchone()
    finally:
        con.close()
    return str(row[0]) if row else "（无返回）"


def resolve_checkpoint(mgr: CheckpointManager, wanted: str) -> Path:
    """把参数解析成快照目录（`latest` / 目录名 / 绝对路径）。"""
    if wanted in ("latest", ""):
        latest = mgr.latest()
        if latest is None:
            raise FileNotFoundError(
                f"没有任何检查点（{mgr.checkpoint_dir} 不存在或为空）——没有可回滚的快照"
            )
        return latest
    candidate = Path(wanted)
    if candidate.is_dir():
        return candidate
    in_dir = mgr.checkpoint_dir / wanted
    if in_dir.is_dir():
        return in_dir
    raise FileNotFoundError(
        f"找不到检查点 '{wanted}'（既不是绝对路径，也不在 {mgr.checkpoint_dir} 里）"
    )


def print_checkpoint_list(mgr: CheckpointManager) -> None:
    checkpoints = mgr.list_checkpoints()
    if not checkpoints:
        say(f"[info] 没有检查点（目录：{mgr.checkpoint_dir}）")
        return
    say(f"检查点目录：{mgr.checkpoint_dir}（共 {len(checkpoints)} 份）")
    for path in checkpoints:
        sizes = []
        for name in ("state.db", "heartbeat.db"):
            db = path / name
            sizes.append(f"{name}={'缺失' if not db.exists() else _fmt_mb(db.stat().st_size)}")
        stamp = _fmt_ts(path.stat().st_mtime)
        mark = "  ← latest" if path == checkpoints[-1] else ""
        say(f"  {path.name}  {stamp}  {'  '.join(sizes)}{mark}")


def backup_current(data_dir: Path) -> Path | None:
    """回滚前把现役库备份到 `data/pre_restore-<ts>/`（事务一致）。

    现役库不存在（比如盘上本来就没有）⇒ 跳过并返回 None，**不假装备份过**。
    """
    existing = [name for name in ("state.db", "heartbeat.db") if (data_dir / name).exists()]
    if not existing:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = data_dir / f"pre_restore-{stamp}"
    suffix = 2
    while target.exists():
        target = data_dir / f"pre_restore-{stamp}-{suffix}"
        suffix += 1
    target.mkdir(parents=True)
    for name in existing:
        backup_database(data_dir / name, target / name)
    return target


def _confirm() -> bool:
    """交互确认；非交互环境必须显式 `--yes`（不给"静默执行不可逆操作"留口子）。"""
    if not sys.stdin.isatty():
        say("[refused] 非交互环境（没有终端）不会静默执行回滚；确认后请加 --yes")
        return False
    with contextlib.suppress(Exception):
        answer = input("即将用快照覆盖现役双库（会先自动备份现役库）。继续？[y/N] ")
        return answer.strip().lower() in ("y", "yes")
    return False


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(
        description="从检查点回滚她的 state.db / heartbeat.db（回滚前自动备份现役库）"
    )
    parser.add_argument(
        "checkpoint", nargs="?", default="latest", help="latest / 目录名 / 绝对路径"
    )
    parser.add_argument(
        "--data-dir", default=str(ROOT / "data"), help="现役库目录（默认仓库 data/）"
    )
    parser.add_argument("--list", action="store_true", help="只列出快照，不恢复")
    parser.add_argument("--dry-run", action="store_true", help="只做检查与打印，不动任何文件")
    parser.add_argument("--yes", action="store_true", help="非交互执行（跳过确认）")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    mgr = CheckpointManager(data_dir)

    if args.list:
        print_checkpoint_list(mgr)
        return EXIT_OK

    try:
        checkpoint = resolve_checkpoint(mgr, args.checkpoint)
    except FileNotFoundError as exc:
        say(f"[fail] {exc}")
        return EXIT_USAGE

    # ── 快照自检：不完整就拒绝（不拿半份快照去覆盖她的现在）──
    missing = [n for n in ("state.db", "heartbeat.db") if not (checkpoint / n).is_file()]
    if missing:
        say(f"[fail] 检查点不完整（缺 {', '.join(missing)}）：{checkpoint}")
        return EXIT_FAIL

    say(f"恢复来源：{checkpoint}")
    say(f"  创建时间：{_fmt_ts(checkpoint.stat().st_mtime)}")
    for name in ("state.db", "heartbeat.db"):
        db = checkpoint / name
        say(f"  {name}: {_fmt_mb(db.stat().st_size)}")
    say(f"现役库目录：{data_dir}")

    # ── 判活（在**任何写入之前**）：她还在跑就拒绝 ──
    try:
        evidence = probe_running(data_dir)
    except Refused as exc:
        say(f"[refused] {exc}")
        return EXIT_REFUSED
    if evidence:
        say("[refused] 她还在运行，拒绝回滚（回滚运行中的库＝覆盖她的现在并撕裂 WAL）：")
        for item in evidence:
            say(f"  · {item}")
        say("  请先停止她：powershell -ExecutionPolicy Bypass -File scripts\\soul.ps1 stop")
        return EXIT_REFUSED
    say("判活：无活进程（PID 文件）、心跳已停 —— 可以安全回滚")

    if args.dry_run:
        say("[dry-run] 检查全部通过；未做任何修改")
        return EXIT_OK

    if not args.yes and not _confirm():
        say("[abort] 用户未确认，未做任何修改")
        return EXIT_USAGE

    # ── 先备份现役库：回滚本身也必须可回滚 ──
    try:
        backup_dir = backup_current(data_dir)
    except Exception as exc:
        say(f"[fail] 现役库备份失败，**已中止回滚**（不动她的库）：{type(exc).__name__}: {exc}")
        return EXIT_FAIL
    if backup_dir is None:
        say("[warn] 现役库不存在，跳过回滚前备份")
    else:
        say(f"现役库已备份：{backup_dir}")

    # ── 回滚（WAL/SHM 伴生文件的清理在 CheckpointManager.restore 内）──
    try:
        restored = mgr.restore(checkpoint)
    except Exception as exc:
        say(f"[fail] 回滚失败：{type(exc).__name__}: {exc}")
        if backup_dir is not None:
            say(f"  现役库备份仍在：{backup_dir}（可用于人工恢复）")
        return EXIT_FAIL

    say("已覆盖：")
    for path in restored:
        say(f"  {path}  ({_fmt_mb(path.stat().st_size)})")
        # 伴生文件必须已清除：残留 = 下次打开会把回滚前的帧重放回来
        for suffix in ("-wal", "-shm"):
            leftover = Path(f"{path}{suffix}")
            if leftover.exists():
                say(f"[fail] 伴生文件未清除：{leftover}（回滚会被 WAL 反噬）")
                return EXIT_FAIL

    # ── 校验：不是"应该好了"，是"读得通" ──
    failed = False
    for path in restored:
        try:
            result = quick_check(path)
        except sqlite3.Error as exc:
            say(f"[fail] {path.name} 校验打不开：{exc}")
            failed = True
            continue
        say(f"  {path.name} quick_check = {result}")
        if result != "ok":
            failed = True
    if failed:
        say("[fail] 恢复后库校验未通过 —— 现役库可能是坏的；回滚前备份仍然可用")
        return EXIT_FAIL

    say("[ok] 回滚完成：两个库已恢复为快照内容，且已清除 WAL/SHM 伴生文件")
    say("  下一步：用 soul.ps1 start -Body 起她（并清掉 no_autostart 标记）")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
