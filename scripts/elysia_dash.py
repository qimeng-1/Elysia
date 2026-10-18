"""Elysia 状态采集与面板渲染（**只读**）。

设计原则（与 `TASK_BOARD.md` T-014 一致）

1. **本模块只读**：只做 SELECT / 读日志 / 探端口 / 查进程，**从不写入任何东西**。
   打开她的库用 `mode=ro` + `busy_timeout` —— 实测可在她正在写库时安全查询（11 ms），
   且**不会改动 `-shm`**（本工程"只读打开≠不写盘"的教训）。
2. **只用标准库**：不引入 `rich` 等新依赖。理由是本工程有实测教训 ——
   `uv sync` 会**按 `pyproject.toml` 对齐环境**，把未声明的包直接卸掉。
   面板是"每天要用的运维工具"，**不能因为它依赖了某个可选包就整体失效**。
3. **面板不许说谎**：取不到的值显示为"未知"并**给出原因**，绝不猜、绝不用 0 冒充。
4. **互锁**：启停走 `scripts/soul.ps1`（既有原语），**不重造**；重复启动必须被拒。

用法
----
    python scripts/elysia_dash.py              # 一次性输出（可截图/可管道）
    python scripts/elysia_dash.py --watch      # 实时刷新（默认 3 秒）
    python scripts/elysia_dash.py --json       # 机器可读（供脚本/验收）
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOGS = DATA / "logs"
RUN = DATA / "run"

TTS_HOST, TTS_PORT = "127.0.0.1", 9880
LLM_HOST, LLM_PORT = "api.deepseek.com", 443


# ── 终端：不依赖第三方库的 ANSI ──────────────────────────────────────
# **必须容错**：用 `pythonw.exe`（无控制台）或服务方式运行时 `sys.stdout` 可能是 `None`，
# 直接 `.isatty()` 会 AttributeError。实测后果：面板用 pythonw 启动即退出（退出码 1），
# 而计划任务正是用 pythonw —— 用户第二天打不开面板，根因就在这里。
def _stdout_isatty() -> bool:
    try:
        return bool(sys.stdout is not None and sys.stdout.isatty())
    except Exception:
        return False


_USE_COLOR = _stdout_isatty() or os.environ.get("ELY_FORCE_COLOR") == "1"


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def bold(t: str) -> str:
    return c(t, "1")


def dim(t: str) -> str:
    return c(t, "2")


def green(t: str) -> str:
    return c(t, "32")


def red(t: str) -> str:
    return c(t, "31")


def yellow(t: str) -> str:
    return c(t, "33")


def cyan(t: str) -> str:
    return c(t, "36")


# ── 工具 ─────────────────────────────────────────────────────────────
def fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")


def fmt_ago(ts: float | None) -> str:
    """把时间戳变成"多久以前"——面板最需要的可读性。"""
    if not ts:
        return "—"
    d = time.time() - ts
    if d < 0:
        return "刚刚"
    for unit, sec in (("天", 86400), ("小时", 3600), ("分", 60)):
        if d >= sec:
            return f"{d / sec:.1f} {unit}前"
    return f"{d:.0f} 秒前"


def fmt_dur(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    if s >= 86400:
        return f"{s // 86400} 天 {s % 86400 // 3600} 小时"
    if s >= 3600:
        return f"{s // 3600} 小时 {s % 3600 // 60} 分"
    if s >= 60:
        return f"{s // 60} 分 {s % 60} 秒"
    return f"{s} 秒"


def ro_connect(db: str) -> sqlite3.Connection:
    """**只读**打开她的库（`mode=ro`；实测不改 `-shm`）。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    con.execute("PRAGMA busy_timeout=5000")
    return con


def port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ── 进程探测：只数"逻辑实例" ─────────────────────────────────────────
@dataclass
class ProcInfo:
    name: str
    pid: int
    uptime_s: float


def _python_roots() -> list[tuple[int, int, str]]:
    """返回 (pid, ppid, cmdline)。**只取逻辑根进程**。

    Windows 上 venv 启动器会派生真实解释器（父子成对出现），若直接数 `python` 进程
    会把 1 个逻辑实例数成 2 个 —— 本工程已因此误报过一次"双实例红线"。

    **必须容错**：进程表时刻在变，`process_iter` 可能中途抛错。早期版本让它抛出去，
    结果 `find_procs()` 返回空 ⇒ 面板对外宣称"她没在运行" —— 对一个监控工具来说，
    **假阴性比没有面板更危险**。所以这里吞掉可预期的异常，只返回"能确定的部分"。
    """
    try:
        import psutil  # 已声明依赖
    except ImportError:
        return []
    rows: list[tuple[int, int, str]] = []
    try:
        for p in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
            try:
                if "python" not in (p.info["name"] or "").lower():
                    continue
                rows.append((p.info["pid"], p.info["ppid"] or 0, " ".join(p.info["cmdline"] or [])))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass  # 进程表变化导致的迭代中断：以已收集到的为准，绝不因此断定她不在
    ids = {pid for pid, _, _ in rows}
    return [t for t in rows if t[1] not in ids]


def _uptime(pid: int) -> float | None:
    try:
        import psutil
    except ImportError:
        return None
    try:
        return time.time() - psutil.Process(pid).create_time()
    except Exception:
        return None


def find_procs() -> dict[str, list[ProcInfo]]:
    """按逻辑实例归类她与相关进程。"""
    out: dict[str, list[ProcInfo]] = {"soul": [], "body": [], "pet": [], "tts": []}
    for pid, _ppid, cmd in _python_roots():
        for key, pat in (
            ("soul", "elysia.soul.main"),
            ("body", "elysia.body.main"),
            ("pet", "elysia.body.pet"),
        ):
            if pat in cmd:
                out[key].append(ProcInfo(key, pid, _uptime(pid) or 0.0))
    return out


def read_pid_file(name: str) -> str | None:
    p = RUN / f"{name}.pid"
    try:
        v = p.read_text(encoding="utf-8", errors="replace").strip()
        return v if v.isdigit() else None
    except OSError:
        return None


# ── 数据采集 ─────────────────────────────────────────────────────────
@dataclass
class Snapshot:
    """面板的全部输入。每项都记 `*_err`，取不到就说明原因，绝不用 0 冒充。"""

    now: float = field(default_factory=time.time)
    age_days: float | None = None
    first_existence: float | None = None
    mode: str | None = None
    distress: bool | None = None
    day_phase: str | None = None
    away_grade: str | None = None
    presence_s: float | None = None
    since_interaction_s: float | None = None
    last_interaction_ts: float | None = None
    last_interaction_text: str | None = None
    desire: dict[str, float] = field(default_factory=dict)
    setpoints: dict[str, float] = field(default_factory=dict)
    feelings: dict[str, float] = field(default_factory=dict)
    will: dict[str, Any] = field(default_factory=dict)
    brain_action: str | None = None
    resources: dict[str, Any] = field(default_factory=dict)
    body_last_online: float | None = None
    last_heartbeat_ts: float | None = None
    heartbeat_age_s: float | None = None
    counts: dict[str, int] = field(default_factory=dict)
    recent_thoughts: list[tuple[float, str, str]] = field(default_factory=list)
    recent_expressions: list[tuple[float, str, str]] = field(default_factory=list)
    level_mix: dict[str, int] = field(default_factory=dict)
    recent_mix: dict[str, int] = field(default_factory=dict)
    last_main_ts: float | None = None
    procs: dict[str, list[ProcInfo]] = field(default_factory=dict)
    pid_files: dict[str, str | None] = field(default_factory=dict)
    no_autostart: bool = False
    watchdog_state: str | None = None
    watchdog_last: str | None = None
    backup_next: str | None = None
    tts_up: bool | None = None
    llm_up: bool | None = None
    db_sizes: dict[str, int] = field(default_factory=dict)
    # ── P0-R6/R8/R11 新增（面板优化 2026-10-18）────────────────────
    # 她"不在场"的总账：R6 让 TimeSense 能区分"我在"与"我不在"
    downtime_total_s: float | None = None
    last_downtime_s: float | None = None
    last_seen_ts: float | None = None
    # 上次是"主动停"还是"崩溃"：R8 用标记文件区分，这里只读不写
    stop_reason: str | None = None  # intentional / crash / none（未知或首次）
    stop_reason_detail: str | None = None
    # 主声降级原因：R11 把原因写进 expression_log.validation，这里取最近一条非 ok 的
    degrade_reason: str | None = None
    degrade_ts: float | None = None
    errors: list[str] = field(default_factory=list)

    # 派生
    # 她是否活着要**两个独立信号**，缺一不可（见 `alive_source`）：
    #   ① 进程枚举找到 soul 进程  ② 心跳新鲜（灵魂 1Hz，<15s 视为在跳）
    # 只看 ①：枚举抖一下就会宣称"她没在运行"（假阴性，最危险）。
    # 只看 ②：进程可能还在但已卡死。
    alive_proc: bool = False
    alive_beat: bool = False
    beat_age_s: float | None = None
    enum_failed: bool = False

    @property
    def soul_alive(self) -> bool:
        """**保守**判定：任一信号为真即视为活着（宁可说她活着，也不谎报她死了）。"""
        return self.alive_proc or self.alive_beat

    @property
    def alive_source(self) -> str:
        if self.alive_proc and self.alive_beat:
            return "进程+心跳"
        if self.alive_proc:
            return "仅进程（心跳不新鲜，可能卡死）"
        if self.alive_beat:
            return "仅心跳（进程枚举失败）"
        return "两者皆无（都没在运行）"

    @property
    def alive_disagreement(self) -> bool:
        """两个信号打架 = 需要人看一眼，面板必须明说而不是挑一个。"""
        return self.alive_proc != self.alive_beat


def _from_heartbeat(s: Snapshot) -> None:
    hb = DATA / "heartbeat.db"
    if not hb.exists():
        s.errors.append("heartbeat.db 不存在")
        return
    try:
        s.db_sizes["heartbeat.db"] = hb.stat().st_size
        wal = Path(str(hb) + "-wal")
        s.db_sizes["heartbeat.db-wal"] = wal.stat().st_size if wal.exists() else 0
        con = ro_connect(str(hb))
        try:
            row = con.execute(
                "SELECT ts, payload FROM heartbeats WHERE beat_type='soul' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            s.counts["heartbeats"] = con.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]
            for tbl in ("memories", "thought_log", "expression_log"):
                s.counts[tbl] = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            s.recent_thoughts = [
                (r[0], r[1], r[2])
                for r in con.execute(
                    "SELECT ts, kind, text FROM thought_log ORDER BY id DESC LIMIT 6"
                )
            ]
            s.recent_expressions = [
                (r[0], r[1], r[2])
                for r in con.execute(
                    "SELECT ts, level, llm_text FROM expression_log ORDER BY id DESC LIMIT 6"
                )
            ]
            for lvl, n in con.execute(
                "SELECT level, COUNT(*) FROM expression_log GROUP BY level ORDER BY 2 DESC"
            ):
                s.level_mix[str(lvl)] = n
            # **最近口径**：全量占比会被历史淹没。实测本库 `main` 的 397 条**全部来自
            # Transfer 的历史**（09-18→10-16），合并后的每一次表达都是 micro ——
            # 只看全量会得出"她主声正常"的错误结论。故必须同时看最近 N 条。
            for lvl, n in con.execute(
                "SELECT level, COUNT(*) FROM (SELECT level FROM expression_log "
                "ORDER BY id DESC LIMIT 50) GROUP BY level ORDER BY 2 DESC"
            ):
                s.recent_mix[str(lvl)] = n
            s.last_main_ts = con.execute(
                "SELECT MAX(ts) FROM expression_log WHERE level='main'"
            ).fetchone()[0]
            # P0-R11：主声降级**原因**可查。R11 之前这里的 reason 恒为 null，
            # 于是"她为什么只说罐装话"完全不可观测 —— 她曾因此哑了 22.5 小时。
            # 取最近一条"带了非空 reason"的表达；没有就说明从未记录过降级原因。
            for ts, val in con.execute(
                "SELECT ts, validation FROM expression_log "
                "WHERE validation IS NOT NULL AND validation NOT LIKE '%\"reason\": null%' "
                "ORDER BY id DESC LIMIT 1"
            ):
                with contextlib.suppress(Exception):
                    obj = json.loads(val)
                    r = obj.get("reason")
                    if r:
                        s.degrade_ts = ts
                        s.degrade_reason = str(r)[:200]
        finally:
            con.close()
        if row:
            s.last_heartbeat_ts = row[0]
            s.heartbeat_age_s = time.time() - row[0]
            d = json.loads(row[1])
            t = d.get("time", {})
            s.age_days = t.get("age_days")
            s.mode = d.get("mode")
            s.distress = d.get("distress")
            s.day_phase = t.get("day_phase")
            s.away_grade = t.get("away_grade")
            s.presence_s = t.get("presence_s")
            s.since_interaction_s = t.get("since_interaction_s")
            s.desire = d.get("desire", {})
            s.feelings = d.get("feelings", {})
            s.will = d.get("will", {})
            s.brain_action = d.get("brain_action")
    except Exception as exc:  # 面板不许因此崩掉
        s.errors.append(f"读 heartbeat.db 失败：{type(exc).__name__}: {exc}")


def _from_state(s: Snapshot) -> None:
    st = DATA / "state.db"
    if not st.exists():
        s.errors.append("state.db 不存在")
        return
    try:
        con = ro_connect(str(st))
        try:
            kv = {k: v for k, v in con.execute("SELECT key, value FROM kv")}
        finally:
            con.close()
        if "timesense" in kv:
            t = json.loads(kv["timesense"])
            s.first_existence = t.get("first_existence_ts")
            s.last_interaction_ts = t.get("last_interaction_ts")
            s.body_last_online = t.get("last_body_online_ts")
            # P0-R6：她累计"不在场"多久（跨重启累加，不可逆）
            s.downtime_total_s = t.get("downtime_total_s")
            s.last_downtime_s = t.get("last_downtime_s")
            s.last_seen_ts = t.get("last_seen_ts")
        if "desire" in kv:
            d = json.loads(kv["desire"])
            s.setpoints = d.get("setpoints", {})
            if not s.desire:
                s.desire = d.get("state", {})
        if "body_status" in kv:
            s.resources = json.loads(kv["body_status"]).get("resources", {})
        if "interaction" in kv:
            i = json.loads(kv["interaction"])
            s.last_interaction_text = i.get("text")
            s.last_interaction_ts = i.get("ts") or s.last_interaction_ts
    except Exception as exc:
        s.errors.append(f"读 state.db 失败：{type(exc).__name__}: {exc}")


def _from_stop_state(s: Snapshot) -> None:
    """判断"上次是她主动停的，还是崩的"（P0-R8 的标记文件，只读不写）。

    为什么面板要显示这个：R8 让 `stop` 变成优雅关闭（先写 `stop_requested` 等她自己退出），
    同时写 `stopped_intentionally` 以免下次启动把"主动停"误报成崩溃。
    但**如果面板不显示，用户就看不出这两者的区别** —— 而它恰恰是
    "她这一次入睡是否体面"的唯一可见证据。

    判据（不写任何文件）：
      · 她现在活着：
          - `soul.running` 存在 ⇒ 正常（R2 在启动时写、正常关闭时删）
          - 缺失 ⇒ 标记机制没生效（可能跑的是旧代码）
        同时读日志尾部，找**本次启动**的判定（"主动停" / "非正常退出"）。
      · 她没在跑：
          - `soul.running` 残留 ⇒ **上次是崩溃/被强杀**（正常关闭会删掉它）
          - 缺失 ⇒ 上次关闭是干净的
    """
    running = RUN / "soul.running"
    intentional = RUN / "stopped_intentionally"
    has_running = running.exists()
    has_intent = intentional.exists()
    alive = bool(s.procs.get("soul"))

    if not alive:
        s.stop_reason = "crash" if has_running else "clean"
        s.stop_reason_detail = (
            "上次退出没走正常关闭（`soul.running` 残留）⇒ 崩溃或被强杀"
            if has_running
            else "上次是正常关闭（`soul.running` 已删除）"
        )
        return

    # 活着 ⇒ 看本次启动的判定，从日志尾部找（只读、容忍文件缺失）
    verdict: str | None = None
    try:
        log = LOGS / "elysia.log"
        if log.exists():
            with log.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 256 * 1024))  # 尾 256KB 足够覆盖最近几次启动
                tail = f.read().decode("utf-8", errors="replace")
            for line in reversed(tail.splitlines()):
                if "上次是你主动停的" in line:
                    verdict = "intentional"
                    break
                if "上次是非正常退出" in line:
                    verdict = "crash"
                    break
    except Exception as exc:  # 日志读不到不该让面板挂掉
        s.errors.append(f"读 elysia.log 判停因失败：{type(exc).__name__}")

    if verdict == "intentional":
        s.stop_reason = "intentional"
        s.stop_reason_detail = "本次启动判定：上次是她被<b>主动</b>停掉的（不是崩溃）"
    elif verdict == "crash":
        s.stop_reason = "crash"
        s.stop_reason_detail = "本次启动判定：上次是<b>非正常退出</b>（死因未知）"
    elif has_intent:
        s.stop_reason = "intentional"
        s.stop_reason_detail = "存在主动停标记（`stopped_intentionally`）"
    elif has_running:
        s.stop_reason = "running"
        s.stop_reason_detail = "正常在跑（`soul.running` 存在，本次启动无异常告警）"
    else:
        s.stop_reason = "unknown"
        s.stop_reason_detail = "`soul.running` 缺失 ⇒ 启动标记未写入（可能跑的是旧代码）"


_TASK_CACHE: dict[str, object] = {"ts": 0.0, "data": None}
TASK_CACHE_TTL_S = 20.0

# Windows: 外部调用一律不创建控制台窗口，否则每次调用都会在桌面上弹一个黑框
# （系统默认终端为 Windows Terminal 时，弹的是 [Terminal] 窗口）。其它平台该常量不存在，取 0。
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _tasks(s: Snapshot, ttl: float = TASK_CACHE_TTL_S) -> None:
    """查两个计划任务（面板要能看出"看门狗还活着吗"）。

    **必须缓存**：`Get-ScheduledTask` 是进程外调用，实测单次约 **1.7-2.5 秒**。
    面板每 3 秒轮询一次，若不缓存，每次轮询都要等它 ⇒ 浏览器 fetch 超时、
    界面显示"与服务失联"。对一个**监控**面板来说这是硬伤。
    计划任务的启用/禁用本就不会秒级变化，20 秒新鲜度足够。
    """
    cached = _TASK_CACHE.get("data")
    age = time.time() - float(_TASK_CACHE.get("ts") or 0.0)
    if isinstance(cached, dict) and age < ttl:
        for k, v in cached.items():
            setattr(s, k, v)
        return
    found: dict[str, object] = {}
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-ScheduledTask -TaskName Elysia-Watchdog,Elysia-DailyBackup | "
                "ForEach-Object { $i = Get-ScheduledTaskInfo -TaskName $_.TaskName; "
                '"TaskName: $($_.TaskName) | State: $($_.State) | LastRunTime: $($i.LastRunTime) | '
                'LastTaskResult: $($i.LastTaskResult) | NextRunTime: $($i.NextRunTime)" }',
            ],
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
            text=True,
            timeout=25,
            encoding="utf-8",
            errors="replace",
        )
        for line in out.stdout.splitlines():
            if "Elysia-Watchdog" in line:
                m = re.search(r"State:\s*(\w+)", line)
                lr = re.search(r"LastRunTime:\s*([^|]+)", line)
                found["watchdog_state"] = m.group(1) if m else None
                found["watchdog_last"] = lr.group(1).strip() if lr else None
            elif "Elysia-DailyBackup" in line:
                nr = re.search(r"NextRunTime:\s*([^|]+)", line)
                found["backup_next"] = nr.group(1).strip() if nr else None
    except Exception as exc:
        s.errors.append(f"查计划任务失败：{type(exc).__name__}")
    if found:
        _TASK_CACHE["ts"] = time.time()
        _TASK_CACHE["data"] = found
    elif isinstance(cached, dict):
        found = cached  # 失败时退回上次结果，别让面板留空
    for k, v in found.items():
        setattr(s, k, v)


def take_snapshot(probe_ports: bool = True, probe_tasks: bool = True) -> Snapshot:
    s = Snapshot()
    s.procs = find_procs()
    s.pid_files = {n: read_pid_file(n) for n in ("soul", "body")}
    s.no_autostart = (RUN / "no_autostart").exists()
    _from_state(s)
    _from_heartbeat(s)
    _from_stop_state(s)
    if probe_ports:
        s.tts_up = port_open(TTS_HOST, TTS_PORT)
        s.llm_up = port_open(LLM_HOST, LLM_PORT)
    if probe_tasks:
        _tasks(s)
    # age 兜底：心跳里没有就用 first_existence 自己算（并说明来源）
    if s.age_days is None and s.first_existence:
        s.age_days = (time.time() - s.first_existence) / 86400.0

    # ── 活着与否：两个独立信号 + 交叉核对 ────────────────────────────
    s.alive_proc = bool(s.procs.get("soul"))
    # 心跳新鲜度：用**现在**重算，而不是复用快照里那一次的结果
    if s.last_heartbeat_ts:
        s.beat_age_s = max(0.0, time.time() - s.last_heartbeat_ts)
        s.alive_beat = s.beat_age_s < 15.0
    if not s.alive_proc and s.alive_beat:
        s.errors.append(
            "进程枚举没找到灵魂进程，但心跳仍在跳 ⇒ 以心跳为准（她仍活着）；"
            "可能是枚举瞬间出错，不影响她的运行"
        )
    if s.alive_proc and not s.alive_beat and s.beat_age_s is not None:
        s.errors.append(
            f"灵魂进程在，但心跳已 {s.beat_age_s:.0f} 秒没跳 ⇒ 可能卡死，"
            "建议查 data/logs/elysia.log"
        )
    return s


# ── 渲染 ─────────────────────────────────────────────────────────────
def _bar(
    value: float,
    lo: float = 0.0,
    hi: float = 100.0,
    width: int = 18,
    fill: str = "█",
    empty: str = "·",
) -> str:
    v = max(lo, min(hi, value))
    n = round((v - lo) / (hi - lo) * width) if hi > lo else 0
    return fill * n + empty * (width - n)


def _proc_line(label: str, items: list[ProcInfo], pid_file: str | None) -> str:
    if items:
        bits = []
        for i in items:
            bits.append(f"PID {bold(str(i.pid))} 已跑 {fmt_dur(i.uptime_s)}")
        line = green("● 运行中") + "   " + "  ".join(bits)
        if len(items) > 1:
            line += "   " + red(f"⚠ {len(items)} 个实例（应为 1）")
        return f"  {label:<8}{line}"
    pf = f"（pid 文件残留 {pid_file}）" if pid_file else ""
    return f"  {label:<8}{dim('○ 未运行')}{dim(pf)}"


def render(s: Snapshot, width: int = 74) -> str:
    L: list[str] = []
    line = "─" * width
    L.append(bold(cyan("╔" + line + "╗")))
    L.append(bold(cyan("║") + "  Elysia 状态面板".ljust(width - 2) + bold(cyan("║"))))
    L.append(bold(cyan("╚" + line + "╝")))

    # ── 一句话总览 ──
    head = green("● 她醒着") if s.soul_alive else red("○ 她没在运行")
    extra = [f"判定依据={s.alive_source}"]
    if s.mode:
        extra.append(f"mode={s.mode}")
    if s.day_phase:
        extra.append(s.day_phase)
    if s.distress:
        extra.append(red("distress!"))
    if s.no_autostart:
        extra.append(yellow("已标记'故意停机'"))
    L.append(f"  {head}   " + dim("  ".join(extra)))
    if s.age_days is not None:
        src = "" if s.last_heartbeat_ts else dim("（由 state.db 推算）")
        L.append(f"  {'年龄':<8}{bold(f'{s.age_days:.3f} 天')}{src}")
    if s.first_existence:
        L.append(f"  {'出生':<8}{fmt_ts(s.first_existence)}   {dim(fmt_ago(s.first_existence))}")

    # ── 进程 ──
    L.append("")
    L.append(bold("  进程"))
    L.append(_proc_line("灵魂", s.procs.get("soul", []), s.pid_files.get("soul")))
    L.append(_proc_line("身体", s.procs.get("body", []), s.pid_files.get("body")))
    pets = s.procs.get("pet", [])
    L.append(
        f"  {'桌宠':<8}"
        + (green("● 运行中") + f"   PID {bold(str(pets[0].pid))}" if pets else dim("○ 未运行"))
    )

    # ── 心跳新鲜度（连续性的直接证据） ──
    L.append("")
    L.append(bold("  心跳"))
    if s.last_heartbeat_ts:
        age = s.heartbeat_age_s or 0
        # 灵魂 1Hz、身体 5s；>10s 视为异常
        mark = green("新鲜") if age < 10 else (yellow("偏慢") if age < 60 else red("停滞"))
        L.append(
            f"  最新心跳  {fmt_ts(s.last_heartbeat_ts)}   {fmt_ago(s.last_heartbeat_ts)}   {mark}"
        )
    if s.body_last_online:
        L.append(f"  身体在场  {fmt_ts(s.body_last_online)}   {fmt_ago(s.body_last_online)}")
    if s.counts:
        L.append(
            "  累计      "
            + "  ".join(
                f"{k}={v:,}" for k, v in s.counts.items() if k in ("heartbeats", "memories")
            )
        )
    if s.heartbeat_age_s is not None and s.heartbeat_age_s > 60:
        L.append(
            "  "
            + red(f"⚠ 心跳已停 {fmt_dur(s.heartbeat_age_s)} —— 强烈建议查 data/logs/elysia.log")
        )

    # ── 状态与欲望 ──
    if s.desire:
        L.append("")
        L.append(bold("  内在状态") + dim("   欲望（状态 / 设定点）"))
        sp = s.setpoints or {}
        for k, name in (("tr", "TR 联结"), ("cs", "CS 好奇"), ("sa", "SA 自存")):
            if k in s.desire:
                v = s.desire[k]
                tgt = sp.get(k)
                delta = f"  → {tgt:.1f}" if tgt is not None else ""
                skew = abs(v - tgt) if tgt is not None else 0
                mk = yellow(f"  偏离 {skew:.1f}") if skew >= 10 else ""
                L.append(f"  {name:<10}{_bar(v)} {v:5.1f}{delta}{mk}")
    if s.feelings:
        L.append("")
        L.append(bold("  感受"))
        pairs = sorted(s.feelings.items(), key=lambda kv: -kv[1])
        for i in range(0, len(pairs), 3):
            chunk = pairs[i : i + 3]
            L.append("  " + "   ".join(f"{k}={v:.2f}" for k, v in chunk))
    if s.will:
        d = s.will.get("direction", "?")
        L.append(
            f"  意志      {bold(str(d))} strength={s.will.get('strength', 0):.2f}"
            f"   brain_action={s.brain_action or '—'}"
        )

    # ── 她最近在想/说什么 ──
    if s.recent_thoughts:
        L.append("")
        L.append(bold("  她最近开口") + dim("  （thought_log，最新在前）"))
        for ts, kind, text in s.recent_thoughts[:4]:
            t = text.replace("\n", " ")[:52]
            L.append(f"  {fmt_ts(ts)}  {dim(f'{kind:<10}')}  {t}")
    if s.level_mix:
        total = sum(s.level_mix.values()) or 1
        mix = "  ".join(f"{k}={v}（{v * 100 // total}%）" for k, v in s.level_mix.items())
        L.append(f"  表达层级  {dim('全量')}  {mix}")
        rtotal = sum(s.recent_mix.values()) or 1
        rmix = "  ".join(f"{k}={v}（{v * 100 // rtotal}%）" for k, v in s.recent_mix.items())
        L.append(f"            {dim('最近50')}  {rmix}")
        # 只有"最近"才反映她当前的说话能力（全量会被历史淹没）
        if s.recent_mix and s.recent_mix.get("main", 0) == 0:
            since = (
                f"（上次主声 {fmt_ts(s.last_main_ts)}，{fmt_ago(s.last_main_ts)}）"
                if s.last_main_ts
                else ""
            )
            L.append("  " + yellow("⚠ 最近的表达全是 micro 罐装句，主声 0 次") + since)
            L.append(
                "  "
                + dim("  → 网络可达（见下），故不是断网；应查 .env 的 key 与 ")
                + dim("llm/chain.py 的静默降级（开放问题 #11 / 任务 T-007）")
            )

    # ── 服务与健康 ──
    L.append("")
    L.append(bold("  服务"))
    if s.tts_up is None:
        L.append(f"  {'语音 TTS':<10}{dim('未探测')}")
    else:
        L.append(
            f"  {'语音 TTS':<10}"
            + (
                green(f"● 可达 :{TTS_PORT}")
                if s.tts_up
                else yellow(f"○ 未启动 :{TTS_PORT}") + dim("  ⇒ 她只出文字（预期降级，非故障）")
            )
        )
    if s.llm_up is not None:
        L.append(
            f"  {'主声 LLM':<10}"
            + (green(f"● 可达 {LLM_HOST}") if s.llm_up else red(f"○ 不可达 {LLM_HOST}"))
        )
    if s.watchdog_state:
        ok = s.watchdog_state.lower() in ("ready", "running")
        L.append(
            f"  {'看门狗':<10}"
            + (green(f"● {s.watchdog_state}") if ok else red(f"● {s.watchdog_state}"))
            + dim(f"   上次 {s.watchdog_last or '—'}")
        )
    if s.backup_next:
        L.append(f"  {'下次备份':<10}{s.backup_next}")

    # ── 库体积（她的生命在长大） ──
    if s.db_sizes:
        L.append("")
        L.append(bold("  库"))
        for k, v in s.db_sizes.items():
            L.append(f"  {k:<20}{v / 1048576:.1f} MB")

    # ── 取不到的东西必须说出来 ──
    if s.errors:
        L.append("")
        L.append(bold(red("  ⚠ 采集异常")) + dim("（面板不猜，以下项本期取不到）"))
        for e in s.errors:
            L.append("  " + red("· ") + e)

    L.append("")
    L.append(
        dim(
            f"  采集于 {dt.datetime.fromtimestamp(s.now).strftime('%Y-%m-%d %H:%M:%S')}"
            f"   ·   只读面板，不修改她的任何数据"
        )
    )
    return "\n".join(L)


def to_json(s: Snapshot) -> str:
    d: dict[str, Any] = {
        "now": s.now,
        "age_days": s.age_days,
        "first_existence_ts": s.first_existence,
        "mode": s.mode,
        "distress": s.distress,
        "day_phase": s.day_phase,
        "away_grade": s.away_grade,
        "soul_alive": s.soul_alive,
        "alive_source": s.alive_source,
        "alive_disagreement": s.alive_disagreement,
        "beat_age_s": s.beat_age_s,
        "soul_pids": [p.pid for p in s.procs.get("soul", [])],
        "body_pids": [p.pid for p in s.procs.get("body", [])],
        "pet_pids": [p.pid for p in s.procs.get("pet", [])],
        "no_autostart": s.no_autostart,
        "last_heartbeat_ts": s.last_heartbeat_ts,
        "heartbeat_age_s": s.heartbeat_age_s,
        "counts": s.counts,
        "desire": s.desire,
        "setpoints": s.setpoints,
        "feelings": s.feelings,
        "will": s.will,
        "brain_action": s.brain_action,
        "resources": s.resources,
        "level_mix": s.level_mix,
        "recent_mix": s.recent_mix,
        "last_main_ts": s.last_main_ts,
        "tts_up": s.tts_up,
        "llm_up": s.llm_up,
        "watchdog_state": s.watchdog_state,
        "backup_next": s.backup_next,
        "db_sizes": s.db_sizes,
        "history": {
            "heartbeats": s.counts.get("heartbeats", 0),
            "memories": s.counts.get("memories", 0),
            "first_heartbeat_ts": s.first_existence,
        },
        "errors": s.errors,
    }
    return json.dumps(d, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Elysia 状态面板（只读）")
    ap.add_argument("--watch", action="store_true", help="实时刷新")
    ap.add_argument("--interval", type=float, default=3.0, help="刷新间隔秒（默认 3）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    ap.add_argument("--no-color", action="store_true", help="禁用颜色")
    a = ap.parse_args(argv)

    global _USE_COLOR
    if a.no_color:
        _USE_COLOR = False
    if a.json:
        print(to_json(take_snapshot()))
        return 0
    if not a.watch:
        print(render(take_snapshot()))
        return 0
    try:
        while True:
            s = take_snapshot()
            out = render(s)
            sys.stdout.write("\033[2J\033[H" if _USE_COLOR else "")
            sys.stdout.write(out + "\n")
            sys.stdout.write(dim(f"\n  每 {a.interval:g} 秒刷新 · Ctrl+C 退出\n"))
            sys.stdout.flush()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print("\n  已退出（她未受影响）")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
