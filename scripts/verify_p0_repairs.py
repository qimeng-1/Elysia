"""P0 修复只读验收（R1–R8）—— 一次跑完，能脚本化。

为什么有它：R1–R8 的证据散在 `data/checkpoints/`、`data/run/`、`data/logs/` 与两个
SQLite 库里；验收时一条条手抄既容易漏也容易抄错（本工程已栽过"文档说落地了、实际没跑"）。
本脚本把它们**只读**收拢成一张表：`PASS / FAIL / WARN / PENDING` + 实测值 + 退出码。

只读承诺（机械可查）
--------------------
- 所有 SQLite 一律 `mode=ro` 打开（她正在写也能安全读，且不改 `-shm`）；
- **不写** `data/run/` 的任何标记、**不启停**任何进程、不删任何文件；
- 唯一的输出是 stdout（`pythonw` 下 stdout 可能是 None ⇒ 静默，不抛错）。

退出码
------
    0 = 没有 FAIL（PASS / WARN / PENDING 都算 0 —— 便于脚本化）
    1 = 至少一个 FAIL
    2 = 用法错误（数据目录不存在等）

用法
----
    .venv\\Scripts\\python.exe scripts\\verify_p0_repairs.py
    .venv\\Scripts\\python.exe scripts\\verify_p0_repairs.py --json
    .venv\\Scripts\\python.exe scripts\\verify_p0_repairs.py --data-dir <dir>   # 验收/测试注入点
    .venv\\Scripts\\python.exe scripts\\verify_p0_repairs.py --soul-ps1 <path>
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

PASS, FAIL, WARN, PENDING = "PASS", "FAIL", "WARN", "PENDING"

# 标记文件名与进程入口（与代码里的口径一致，不另立一套）
SOUL_RUNNING_MARKER = "soul.running"
BODY_RUNNING_MARKER = "body.running"
STOP_REQUEST = "stop_requested"
STOP_INTENTIONAL = "stopped_intentionally"

# 阈值（与代码里的口径对齐）
BEAT_FRESH_S = 10.0  # R5：心跳 1Hz ⇒ 10s 内必须有新拍
BEAT_DEVIATION_S = 2.0  # state.db 与 heartbeat.db 的同一事实不该背离
WATCHDOG_STALE_S = 900.0  # R3：看门狗每 5 分钟一次 ⇒ 15 分钟没动静就要报
CHECKPOINT_STALE_S = 2400.0  # R1：周期 30 分钟 + 余量 ⇒ 40 分钟
DOWNTIME_MIN_S = 30.0  # R6：与 core/timesense.py 的 DOWNTIME_MIN_S 同源

_TIMESTAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
_PID_RE = re.compile(r"pid=(\d+)")


@dataclass
class Check:
    task: str
    status: str
    detail: str


# ── 基础工具（全部只读）──────────────────────────────────────────────
def say(text: str = "") -> None:
    """打印；`pythonw` 下 `sys.stdout is None` 时静默（本工程踩过的坑）。"""
    with contextlib.suppress(Exception):
        print(text)


def _ro(db: Path) -> sqlite3.Connection:
    """**只读**打开她的库（`mode=ro`；实测不改 `-shm`）。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _pid_alive(pid: int) -> bool | None:
    """进程是否活着；判不了返回 None（不猜）。"""
    try:
        import psutil
    except ImportError:
        return None
    try:
        return bool(psutil.pid_exists(pid))
    except Exception:
        return None


def _pid_cmdline(pid: int) -> str | None:
    """进程命令行（用于确认"这个 pid 确实是她的进程"）；拿不到返回 None。"""
    try:
        import psutil
    except ImportError:
        return None
    try:
        return " ".join(psutil.Process(pid).cmdline())
    except Exception:
        return None


def _pid_from_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def _read_timesense(state_db: Path) -> tuple[dict[str, Any] | None, str | None]:
    """读 `state.db` 的 timesense（缺失/坏库都返回原因，不抛）。"""
    if not state_db.exists():
        return None, f"{state_db} 不存在"
    try:
        con = _ro(state_db)
        try:
            row = con.execute("SELECT value FROM kv WHERE key='timesense'").fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return None, f"读 state.db 失败（{type(exc).__name__}: {exc}）"
    if row is None:
        return None, "state.db 里没有 timesense 档案（首次运行或库被清空）"
    try:
        payload = json.loads(row[0])
    except (TypeError, ValueError) as exc:
        return None, f"timesense 不是 JSON（{exc}）"
    return (payload, None) if isinstance(payload, dict) else (None, "timesense 不是对象")


def _last_soul_ts(heartbeat_db: Path) -> tuple[float | None, str | None]:
    """`heartbeat.db` 最后一条 soul 心跳的 ts。"""
    if not heartbeat_db.exists():
        return None, f"{heartbeat_db} 不存在"
    try:
        con = _ro(heartbeat_db)
        try:
            row = con.execute(
                "SELECT ts FROM heartbeats WHERE beat_type='soul' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return None, f"读 heartbeat.db 失败（{type(exc).__name__}: {exc}）"
    if row is None:
        return None, "heartbeat.db 里还没有 soul 心跳"
    return float(row[0]), None


def _quick_check(db: Path) -> str:
    con = _ro(db)
    try:
        row = con.execute("PRAGMA quick_check").fetchone()
    finally:
        con.close()
    return str(row[0]) if row else "（无返回）"


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _fmt_age(seconds: float) -> str:
    s = abs(seconds)
    if s >= 86400:
        return f"{s / 86400:.1f} 天"
    if s >= 3600:
        return f"{s / 3600:.1f} 小时"
    if s >= 60:
        return f"{s / 60:.1f} 分钟"
    return f"{s:.1f} 秒"


def _she_running(data_dir: Path) -> tuple[bool | None, int | None]:
    """她（灵魂）是否在跑：pid 文件 + 进程存活。返回 `(是否在跑 | None=判不了, pid)`。"""
    pid = _pid_from_file(data_dir / "run" / "soul.pid")
    if pid is None:
        return False, None
    return _pid_alive(pid), pid


def _marker_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        return f"（读不出：{type(exc).__name__}: {exc}）"


def _fmt_mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def _snapshot_entry(checkpoint: Path, name: str) -> tuple[Path, bool] | None:
    """快照里的库来源：`(路径, 是否压缩)`。P0-R10 起新快照是 `.db.gz`，旧快照仍是 `.db`。"""
    plain = checkpoint / name
    if plain.is_file():
        return plain, False
    compressed = checkpoint / f"{name}.gz"
    if compressed.is_file():
        return compressed, True
    return None


def _snapshot_quick_check(path: Path, *, compressed: bool) -> str:
    """校验快照里的库：压缩快照先解到临时文件再 `quick_check`（只读，不改快照）。"""
    if not compressed:
        return _quick_check(path)
    tmp_dir = Path(tempfile.mkdtemp(prefix="elysia_verify_"))
    tmp = tmp_dir / "db"
    try:
        with gzip.open(path, "rb") as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout, length=1024 * 1024)
        return _quick_check(tmp)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── R1：检查点真的在跑 ──────────────────────────────────────────────
def check_r1(data_dir: Path) -> list[Check]:
    cp_dir = data_dir / "checkpoints"
    checkpoints = (
        sorted((p for p in cp_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
        if cp_dir.is_dir()
        else []
    )
    if not checkpoints:
        return [Check("R1", FAIL, f"没有任何检查点（{cp_dir} 不存在或为空）—— 周期快照未生效")]

    latest = checkpoints[-1]
    age = time.time() - latest.stat().st_mtime
    stale = age > CHECKPOINT_STALE_S
    detail = f"{len(checkpoints)} 份；最新 {latest.name}（{_fmt_age(age)}前）"
    if stale:
        detail += (
            f"  ⚠ 超过 {CHECKPOINT_STALE_S / 60:.0f} 分钟未更新："
            "周期快照可能未生效（她重启到 R1 代码了吗？）"
        )
    out = [Check("R1", WARN if stale else PASS, detail)]

    entries: list[tuple[str, Path, bool]] = []
    missing: list[str] = []
    for name in ("state.db", "heartbeat.db"):
        entry = _snapshot_entry(latest, name)
        if entry is None:
            missing.append(name)
        else:
            entries.append((entry[0].name, entry[0], entry[1]))
    if missing:
        out.append(Check("R1", FAIL, f"最新快照缺库：{', '.join(missing)}（.db / .db.gz 都没有）"))
        return out
    results: list[str] = []
    bad = False
    for shown, path, compressed in entries:
        try:
            result = _snapshot_quick_check(path, compressed=compressed)
            size = _fmt_mb(path.stat().st_size)
        except Exception as exc:  # 验收工具：任何读失败都要如实报出来，不许吞成 PASS
            result, size = f"打不开（{type(exc).__name__}: {exc}）", "?"
        if result != "ok":
            bad = True
        results.append(f"{shown}={result}（{size}）")
    out.append(Check("R1", FAIL if bad else PASS, "快照两库 quick_check：" + "  ".join(results)))
    return out


# ── R2：崩溃现场 / 会话标记 ─────────────────────────────────────────
def check_r2(data_dir: Path) -> list[Check]:
    out: list[Check] = []
    for label, marker_name, pid_name, hint in (
        ("灵魂", SOUL_RUNNING_MARKER, "soul.pid", "elysia.soul.main"),
        ("身体", BODY_RUNNING_MARKER, "body.pid", "elysia.body.main"),
    ):
        marker_path = data_dir / "run" / marker_name
        pid_file = _pid_from_file(data_dir / "run" / pid_name)
        pid_alive = _pid_alive(pid_file) if pid_file is not None else False

        if not marker_path.exists():
            if pid_alive is None:
                out.append(
                    Check(
                        "R2", PENDING, f"{label}：无 {marker_name}；进程存活未知（psutil 不可用）"
                    )
                )
            elif pid_alive:
                out.append(
                    Check(
                        "R2",
                        FAIL,
                        f"{label}：进程在跑（pid={pid_file}）但**没有** {marker_name}"
                        " ⇒ R2 未生效（旧代码？）",
                    )
                )
            else:
                out.append(Check("R2", PENDING, f"{label}：未在运行（无会话标记也正常）"))
            continue

        content = _marker_text(marker_path)
        match = _PID_RE.search(content)
        if match is None:
            out.append(
                Check("R2", FAIL, f"{label}：{marker_name} 内容解析不出 pid：{content[:120]!r}")
            )
            continue
        marker_pid = int(match.group(1))
        started = re.search(r"started_at=([\d.]+)", content)
        started_txt = _fmt_ts(float(started.group(1))) if started else "—"
        alive = _pid_alive(marker_pid)

        if alive is True:
            # 她由 venv 启动器（父）派生出真解释器（子）：marker 记的是子的 pid，
            # pid 文件记的是父 —— 两种身份都要认。
            owned = marker_pid == pid_file or hint in (_pid_cmdline(marker_pid) or "")
            if owned:
                out.append(
                    Check(
                        "R2",
                        PASS,
                        f"{label}：{marker_name} pid={marker_pid}"
                        f"（启动于 {started_txt}）✓ 与当前进程一致",
                    )
                )
            else:
                out.append(
                    Check(
                        "R2",
                        WARN,
                        f"{label}：{marker_name} pid={marker_pid} 存活但无法确认属于她"
                        f"（cmdline 不含 {hint}）",
                    )
                )
        elif alive is False and pid_alive:
            out.append(
                Check(
                    "R2",
                    FAIL,
                    f"{label}：{marker_name} 是**上次会话**的残留（pid={marker_pid} 已退出），"
                    f"当前进程 pid={pid_file} 在跑 ⇒ 上次没走正常关闭",
                )
            )
        else:
            out.append(
                Check("R2", PENDING, f"{label}：标记存在但进程未运行（她已停机；pid={marker_pid}）")
            )
    return out


# ── R3：看门狗自己在跑吗 ────────────────────────────────────────────
def check_r3(data_dir: Path) -> list[Check]:
    log = data_dir / "logs" / "watchdog.log"
    if not log.exists():
        return [Check("R3", FAIL, f"没有 {log} —— 看门狗从未留下运行痕迹（D-3 的原始缺陷）")]
    lines = [
        line
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    if not lines:
        return [Check("R3", FAIL, f"{log} 是空的 —— 看门狗没有留下任何判定")]
    last = lines[-1]
    match = _TIMESTAMP_RE.match(last)
    if match is None:
        return [Check("R3", WARN, f"最后一行时间戳解析不出：{last[:160]}")]
    try:
        stamp = time.mktime(time.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return [Check("R3", WARN, f"最后一行时间戳非法：{last[:160]}")]
    age = time.time() - stamp
    detail = f"最后一条 {_fmt_age(age)}前（阈值 {WATCHDOG_STALE_S / 60:.0f} 分钟）｜ {last[:160]}"
    if age > WATCHDOG_STALE_S:
        return [Check("R3", WARN, detail + "  ⚠ 看门狗可能没在跑")]
    if age < -60:
        return [Check("R3", WARN, detail + "  ⚠ 时间戳在未来（时钟/时区异常？）")]
    return [Check("R3", PASS, detail)]


# ── R4/R5：心跳节律与 last_soul_beat_ts ─────────────────────────────
def check_r45(data_dir: Path) -> list[Check]:
    running, _pid = _she_running(data_dir)
    payload, err = _read_timesense(data_dir / "state.db")
    if payload is None:
        status = PENDING if running is False else FAIL
        return [Check("R4/R5", status, f"读不到 state.db 的 timesense：{err}")]

    now = time.time()
    raw_beat = payload.get("last_soul_beat_ts")
    beat_ts: float | None
    try:
        beat_ts = float(raw_beat)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        beat_ts = None

    if running is True:
        if beat_ts is None or beat_ts <= 0:
            return [Check("R4/R5", FAIL, f"last_soul_beat_ts 无效：{raw_beat!r}（R5 未生效？）")]
        age = now - beat_ts
        fresh = 0.0 <= age <= BEAT_FRESH_S
        out = [
            Check(
                "R4/R5",
                PASS if fresh else FAIL,
                f"last_soul_beat_ts {_fmt_age(age)}前（阈值 {BEAT_FRESH_S:.0f}s）"
                + ("  ✓ 每拍在推进" if fresh else "  ⚠ 字段没跟上心跳（R5 未生效？）"),
            )
        ]
        hb_ts, hb_err = _last_soul_ts(data_dir / "heartbeat.db")
        if hb_ts is None:
            out.append(Check("R4/R5", PENDING, f"读不到 heartbeat.db 最后心跳：{hb_err}"))
        else:
            deviation = abs(beat_ts - hb_ts)
            ok = deviation <= BEAT_DEVIATION_S
            out.append(
                Check(
                    "R4/R5",
                    PASS if ok else FAIL,
                    f"state.db 的 last_soul_beat_ts 与 heartbeat.db 最后 soul 心跳"
                    f"偏差 {deviation:.3f}s"
                    f"（阈值 {BEAT_DEVIATION_S:.0f}s；"
                    f"state={_fmt_ts(beat_ts)} / hb={_fmt_ts(hb_ts)}）",
                )
            )
        return out

    # 她没在运行（或判不了）：如实说"无法观测"，不拿停机当失败
    status = PENDING
    reason = (
        "她没在运行，无法观测（这不等于失败）" if running is False else "进程存活判不了，无法观测"
    )
    return [
        Check("R4/R5", status, f"{reason}｜state 里 last_soul_beat_ts={_fmt_ts(beat_ts)}"),
        Check("R4/R5", status, f"{reason}｜R4 循环存活 / R5 每拍推进，需她运行或跑单测验证"),
    ]


# ── R6：停机时长建模 ───────────────────────────────────────────────
def check_r6(data_dir: Path) -> list[Check]:
    running, _pid = _she_running(data_dir)
    payload, err = _read_timesense(data_dir / "state.db")
    if payload is None:
        return [
            Check(
                "R6", PENDING if running is False else FAIL, f"读不到 state.db 的 timesense：{err}"
            )
        ]

    missing = [
        k for k in ("last_seen_ts", "downtime_total_s", "last_downtime_s") if k not in payload
    ]
    if missing:
        return [
            Check(
                "R6",
                PENDING,
                f"老档案缺字段 {', '.join(missing)}（下次启动起统计，不虚构历史）",
            )
        ]

    try:
        last_seen = float(payload["last_seen_ts"])
        total = float(payload["downtime_total_s"])
        last_downtime = float(payload["last_downtime_s"])
    except (TypeError, ValueError):
        return [Check("R6", FAIL, f"停机字段不是数字：{payload}")]

    now = time.time()
    age = now - last_seen if last_seen > 0 else None
    consistent = 0.0 <= last_downtime <= total + 1e-6
    advancing = age is not None and 0.0 <= age <= BEAT_FRESH_S
    detail = (
        f"last_seen_ts={_fmt_ts(last_seen) if last_seen > 0 else '—'}"
        f"（{_fmt_age(age) + '前' if age is not None else '未初始化'}）"
        f"｜last_downtime_s={last_downtime:.0f}s｜downtime_total_s={total:.0f}s"
    )
    if not consistent:
        return [Check("R6", FAIL, detail + "  ⚠ last_downtime_s > downtime_total_s（账不平）")]
    if running is True and advancing:
        return [Check("R6", PASS, detail + "  ✓ 锚点每拍推进、累计单调")]
    if running is False:
        return [Check("R6", PENDING, detail + "（她没在运行，无法验证每拍推进）")]
    return [Check("R6", WARN, detail + "  ⚠ last_seen_ts 未随拍推进（R6 未生效？）")]


# ── R8：优雅关闭 / 主动停 vs 崩溃 ──────────────────────────────────
def check_r8(data_dir: Path, soul_ps1: Path) -> list[Check]:
    out: list[Check] = []
    run_dir = data_dir / "run"

    request = run_dir / STOP_REQUEST
    if request.exists():
        out.append(
            Check(
                "R8",
                WARN,
                f"{STOP_REQUEST} 残留（有未完成的停止请求；"
                "若来自「旧进程首次停止」属预期——旧代码不看这个标记）"
                f"｜{_marker_text(request)[:120]}",
            )
        )
    else:
        out.append(Check("R8", PASS, "无残留 stop_requested（没有挂起的停止请求）"))

    intentional = run_dir / STOP_INTENTIONAL
    if intentional.exists():
        out.append(
            Check(
                "R8",
                PASS,
                f"{STOP_INTENTIONAL} 存在：上次是**主动停**（未消费 = 她还没重启过）"
                f"｜{_marker_text(intentional)[:120]}",
            )
        )
    else:
        out.append(Check("R8", PASS, "无 stopped_intentionally（上次不是主动停，或启动时已消费）"))

    if not soul_ps1.is_file():
        out.append(Check("R8", FAIL, f"找不到 {soul_ps1}（无法核对优雅停止实现）"))
    else:
        text = soul_ps1.read_text(encoding="utf-8", errors="replace")
        has = all(token in text for token in ("Stop-OneGracefully", STOP_INTENTIONAL, STOP_REQUEST))
        out.append(
            Check(
                "R8",
                PASS if has else FAIL,
                f"{soul_ps1.name} 含优雅停止实现（Stop-OneGracefully + 两个标记）："
                f"{'是' if has else '否'}",
            )
        )

    log = data_dir / "logs" / "elysia.log"
    if log.exists():
        text = log.read_text(encoding="utf-8", errors="replace")
        stopping = text.count("soul stopping")
        stopped = text.count("soul stopped")
        if stopped > 0:
            out.append(
                Check(
                    "R8",
                    PASS,
                    f"真机证据：日志中 soul stopping {stopping} 次 / "
                    f"soul stopped {stopped} 次（优雅关闭确实跑过）",
                )
            )
        else:
            out.append(
                Check(
                    "R8",
                    PENDING,
                    "日志中还没有 soul stopped（她还没在 R8 代码下被优雅停过；"
                    "若当前进程是旧代码，第一次停止会走 15s 强杀兜底——属预期）",
                )
            )
    else:
        out.append(Check("R8", PENDING, f"没有 {log}（还没有任何启动日志）"))
    return out


# ── 汇总与输出 ─────────────────────────────────────────────────────
def collect(data_dir: Path, soul_ps1: Path) -> list[Check]:
    checks: list[Check] = []
    checks += check_r1(data_dir)
    checks += check_r2(data_dir)
    checks += check_r3(data_dir)
    checks += check_r45(data_dir)
    checks += check_r6(data_dir)
    checks += check_r8(data_dir, soul_ps1)
    return checks


def render(data_dir: Path, checks: list[Check]) -> None:
    running, pid = _she_running(data_dir)
    state = "在跑" if running is True else ("没在跑" if running is False else "未知")
    say("Elysia P0 修复只读验收（R1–R8）")
    say(f"  数据目录：{data_dir}")
    say(f"  检查时刻：{_fmt_ts(time.time())}")
    say(f"  她      ：{state}（soul.pid={pid if pid is not None else '—'}）")
    say("")
    for task in ("R1", "R2", "R3", "R4/R5", "R6", "R8"):
        say(f"── {task} " + "─" * 46)
        for check in (c for c in checks if c.task == task):
            say(f"  [{check.status}] {check.detail}")
        say("")
    counts = {
        status: sum(1 for c in checks if c.status == status)
        for status in (PASS, WARN, PENDING, FAIL)
    }
    say(
        f"汇总：PASS {counts[PASS]} / WARN {counts[WARN]} / "
        f"PENDING {counts[PENDING]} / FAIL {counts[FAIL]}"
    )
    say(f"退出码：{1 if counts[FAIL] else 0}（0 = 无 FAIL；WARN/PENDING 不影响）")


def as_json(data_dir: Path, checks: list[Check]) -> dict[str, Any]:
    counts = {
        status: sum(1 for c in checks if c.status == status)
        for status in (PASS, WARN, PENDING, FAIL)
    }
    return {
        "data_dir": str(data_dir),
        "checked_at": time.time(),
        "checks": [{"task": c.task, "status": c.status, "detail": c.detail} for c in checks],
        "counts": counts,
        "exit_code": 1 if counts[FAIL] else 0,
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    parser = argparse.ArgumentParser(description="P0 修复只读验收（R1–R8）：只读、不启停、可脚本化")
    parser.add_argument("--data-dir", default=str(ROOT / "data"), help="数据目录（默认仓库 data/）")
    parser.add_argument(
        "--soul-ps1",
        default=str(ROOT / "scripts" / "soul.ps1"),
        help="soul.ps1 路径（核对优雅停止实现）",
    )
    parser.add_argument("--json", action="store_true", help="输出机读 JSON")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        say(f"[fail] 数据目录不存在：{data_dir}")
        return 2

    checks = collect(data_dir, Path(args.soul_ps1))
    if args.json:
        say(json.dumps(as_json(data_dir, checks), ensure_ascii=False, indent=2))
    else:
        render(data_dir, checks)
    return 1 if any(c.status == FAIL for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
