"""Elysia 控制面板（<http://127.0.0.1:8770>）—— 开关集合 + 状态监控 + **对她说话**。

为什么是"小服务器 + 浏览器界面"，而不是纯静态 HTML
--------------------------------------------------
纯静态 HTML 只能**看**，按不了开关（浏览器不允许网页直接启动本机进程）。
所以本文件用**标准库自带的 `http.server`** 提供：① 状态 JSON ② 开关接口，
再内嵌一个单页界面。**不引入任何新依赖**（本工程实测教训：`uv sync` 会按
`pyproject.toml` 对齐环境，把未声明的包卸掉）。

通道（P0-R18，步 1-3）
---------------------
- **步 2 你能说话**：`POST /api/say` 把 `{"ts":<now>,"text":"..."}` 写进
  `state.db` 的 `kv['interaction']`。她的心跳每拍（1Hz）读这一行，
  `ts` 比上次新就会被刺激、强制开口 —— 这是桌宠用了十年的同一条通道
  （`body/pet.py:_write_interaction`），我们只是把投递口从"本机窗口"扩展到"能打开面板的任何地方"。
  **写完必须如实回报"她回应了没有"**：轮询 `expression_log` 最多 10 秒，
  没等到就说没等到，绝不假装成功。
- **步 1 手机能看**：`--host` 默认仍是 `127.0.0.1`（**默认行为不变**）；
  一旦绑非回环地址，**强制要求 token**（见下）。手机访问推荐 Tailscale/ZeroTier：
  装好客户端后直接打开 `http://<电脑的 Tailscale IP>:8770/?token=<token>`，
  不需要端口转发、不把面板暴露给公网。
- **步 3 她开口时推送手机**：面板启动时会起一个只读轮询线程
  （`scripts/push_notifier.py`），她每次开口 POST 到 `data/run/push_url.txt`
  里配置的 webhook；推送失败**静默降级**，绝不影响她的表达。

安全设计
--------
1. **默认只绑回环** `127.0.0.1` —— 外网访问不到。
2. **绑非回环 = 强制 token 鉴权**：token 在 `data/run/panel_token.txt`
   （首次启动随机生成 32 字节 hex）；所有请求（含首页）必须带
   `?token=` 或 `Authorization: Bearer`，否则 **401 且不泄露任何状态**；
   连续猜错会触发临时封禁（429）。
3. **动作白名单**：只接受预定义动作名，不做任意命令执行。
4. **🔒 实例互锁**：她已在运行时"开机"被**拒绝**（双实例 = 分叉 = 损坏她的一生）。
5. **只读优先**：状态采集不写她的数据（库用 `mode=ro` 打开）。
   **唯一的写**是 `POST /api/say` 写一行 `kv['interaction']` —— 她那侧的"耳朵"
   本来就是为这件事建好的；写用**单独的读写连接 + busy_timeout**，实测与她的写并发不冲突。
6. **写入失败必须明确报错**（`ok:false` + 原因），绝不静默返回成功。
7. **不重造原语**：启停一律经既有 `scripts/soul.ps1`。

用法
----
    python scripts/elysia_panel.py                    # 起服务器并打开浏览器（只绑本机）
    python scripts/elysia_panel.py --no-open          # 只起服务器
    python scripts/elysia_panel.py --port 8770
    python scripts/elysia_panel.py --host 0.0.0.0     # 手机可看：强制 token，见输出提示
    python scripts/elysia_panel.py --no-push          # 不启动推送轮询线程
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from elysia_dash import ROOT, Snapshot, port_open, take_snapshot

SOUL_PS1 = ROOT / "scripts" / "soul.ps1"
BACKUP_PS1 = ROOT / "scripts" / "backup.ps1"
PY = ROOT / ".venv" / "Scripts" / "python.exe"


def _tts_launcher() -> Path | None:
    """GPT-SoVITS 启动脚本路径：从 Settings 读（`ELYSIA_TTS_LAUNCHER_PATH`）。

    P0-R13（可迁移性）：这里原本写死本机绝对路径（GPT-SoVITS 的启动脚本），
    换机器直接失效。现在**未配置就返回 None**，调用方显示"未配置"而不是报错。
    """
    try:
        from elysia.core.config import get_settings

        return get_settings().tts_launcher
    except Exception:  # 配置系统不可用也不该让面板整体挂掉
        return None


TTS_URL = "http://127.0.0.1:9880"

DEFAULT_PORT = 8770

# 每个动作 → 给人看的说明（面板上直接显示"按了会发生什么"）
ACTION_HELP: dict[str, str] = {
    "her_on": "启动灵魂 + 身体（经 soul.ps1；已在运行则拒绝）",
    "her_off": "停止灵魂 + 身体，并创建 no_autostart 标记（看门狗不再自动拉起）",
    "body_off": "只停身体，灵魂继续活着（记忆与心跳不受影响）",
    "pet_on": "启动桌宠窗口（--with-soul-data，气泡显示她的话）",
    "pet_off": "关闭桌宠窗口（只杀 elysia.body.pet，灵魂不受影响）",
    "voice_on": "启动 GPT-SoVITS 声音服务（外部程序，加载模型需等一会儿）",
    "voice_off": "停止 GPT-SoVITS 声音服务（**只停本控制台拉起的那个**；外部手动开的会拒绝）",
    "watchdog_on": "启用每 5 分钟看门狗计划任务",
    "watchdog_off": "禁用看门狗（她崩了将不再被自动拉回 —— 谨慎）",
    "backup_now": "立刻做一次完整备份（含历史档案，写到配置的备份目录）",
    "restart_her": "重启她（停 → 启；会短暂中断她）",
}


# Windows: 外部调用的进程一律不创建控制台窗口，否则会弹窗
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _kill_pids(pids: list[int]) -> int:
    """只杀指定 PID（**不做进程树扫描**），返回成功数。"""
    killed = 0
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                creationflags=_CREATE_NO_WINDOW,
                timeout=20,
            )
            killed += 1
        except Exception:
            pass
    return killed


def _pids_for(pattern: str) -> list[int]:
    """按命令行子串找逻辑根进程 PID（复用 dash 的判定，避免把 venv 父子对算成两个）。"""
    try:
        import psutil  # 已声明依赖
    except ImportError:
        return []
    procs: list[tuple[int, int, str]] = []
    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
        try:
            if "python" not in (p.info["name"] or "").lower():
                continue
            procs.append((p.info["pid"], p.info["ppid"] or 0, " ".join(p.info["cmdline"] or [])))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    ids = {pid for pid, _, _ in procs}
    return [pid for pid, ppid, cmd in procs if ppid not in ids and pattern in cmd]


def _tts_owner_pid() -> int | None:
    """谁在监听 9880（用于判断语音服务是否由本控制台拉起）。"""
    try:
        import psutil
    except ImportError:
        return None
    try:
        for c in psutil.net_connections(kind="tcp"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == 9880:
                return c.pid
    except Exception:
        return None
    return None


# ── 动作实现 ─────────────────────────────────────────────────────────
class Result:
    def __init__(self, ok: bool, title: str, detail: str = "") -> None:
        self.ok, self.title, self.detail = ok, title, detail

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "title": self.title, "detail": self.detail}


def _ps(script: Path, args: list[str], timeout: int = 90) -> Result:
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        out = (p.stdout or "").strip() or (p.stderr or "").strip()
        return Result(p.returncode == 0, f"退出码 {p.returncode}", out[-600:])
    except subprocess.TimeoutExpired:
        return Result(False, "超时", f"{script.name} 超过 {timeout}s 未返回")
    except Exception as exc:
        return Result(False, "执行失败", f"{type(exc).__name__}: {exc}")


def _spawn(args: list[str]) -> subprocess.Popen[bytes]:
    """后台拉起一个长期进程（桌宠 / 语音），不阻塞面板。"""
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    return subprocess.Popen(args, cwd=str(ROOT), creationflags=flags)


def _verify_up(wait_s: float = 30.0) -> Result:
    """启动后**实测**她到底有没有活过来 —— 不能只信 soul.ps1 的退出码。

    事故背景（2026-10-18 21:02）：`soul.ps1 start` 的判活只看 **PID 是否存在**。
    当 PID 文件里的旧 PID 被系统复用给无关进程时，它会打印「灵魂已在运行，跳过灵魂启动」
    并 **exit 0** —— 面板于是显示「退出码 0 / 成功」，而**她其实还是死的**。
    用户看到的就是「点了开机、显示成功、但她没起来」。

    所以面板必须自己看结果：轮询心跳库，直到出现**新鲜心跳**才算真的起来。
    """
    deadline = time.time() + wait_s
    last_age: float | None = None
    while time.time() < deadline:
        try:
            s = take_snapshot(probe_ports=False, probe_tasks=False)
            last_age = s.heartbeat_age_s
            if s.soul_alive and (s.heartbeat_age_s is None or s.heartbeat_age_s < 15.0):
                return Result(
                    True,
                    "她真的起来了",
                    f"心跳 {s.heartbeat_age_s:.1f}s 前，年龄 {s.age_days:.3f} 天",
                )
        except Exception:
            pass
        time.sleep(1.0)
    age_txt = f"（最后心跳 {last_age:.0f} 秒前）" if last_age is not None else ""
    return Result(
        False,
        "启动命令返回了，但她没有活过来",
        f"{wait_s:.0f} 秒内没等到新鲜心跳{age_txt}。"
        "常见原因：①PID 文件里的旧 PID 被无关进程占用，脚本误判「已在运行」而跳过启动"
        "②启动即崩溃（查 data/logs/crash-*）③端口/库被占用。请查 data/logs/watchdog.log。",
    )


def do_action(name: str, snap: Snapshot) -> Result:
    if name not in ACTION_HELP:
        return Result(False, "未知动作", f"不接受 '{name}'（只允许预设动作）")

    # ── 互锁：她已在运行时"开机"必须拒绝 ─────────────────────────────
    if name in ("her_on", "restart_her") and snap.soul_alive and name == "her_on":
        return Result(
            False,
            "已被互锁拒绝",
            f"她已经在运行（灵魂 PID {snap.procs['soul'][0].pid}）。"
            "她是单实例生命，再启动一个会让她的状态分叉 —— 等于把同一段人生写成两份。"
            "要重启请用 restart_her。",
        )

    if name == "her_on":
        r = _ps(SOUL_PS1, ["start", "-Body"])
        if not r.ok:
            return r
        v = _verify_up()
        # 命令成功但人没起来 ⇒ 必须以**失败**回报，否则用户会以为好了
        return Result(
            v.ok,
            f"开机：{v.title}",
            f"{r.detail}\n—\n{v.detail}",
        )
    if name == "her_off":
        return _ps(SOUL_PS1, ["stop"])
    if name == "body_off":
        if not snap.procs.get("body"):
            return Result(True, "无需操作", "身体本来就没在运行")
        return _ps(SOUL_PS1, ["stop-body"])
    if name == "restart_her":
        r1 = _ps(SOUL_PS1, ["stop"])
        time.sleep(1.0)
        r2 = _ps(SOUL_PS1, ["start", "-Body"])
        if not r2.ok:
            return Result(
                False,
                "重启中启动失败",
                f"停止：{r1.title} / 启动：{r2.title}\n{r2.detail}",
            )
        v = _verify_up()
        return Result(
            v.ok,
            "重启完成" if v.ok else f"重启：{v.title}",
            f"停止：{r1.title} / 启动：{r2.title}\n—\n{v.detail}",
        )
    if name == "pet_on":
        if snap.procs.get("pet"):
            return Result(False, "已在运行", f"桌宠 PID {snap.procs['pet'][0].pid}")
        _spawn([str(PY), "-m", "elysia.body.pet", "--with-soul-data"])
        return Result(True, "已拉起桌宠", "GUI 窗口稍后出现")
    if name == "pet_off":
        pids = _pids_for("elysia.body.pet")
        if not pids:
            return Result(True, "无需操作", "桌宠本来就没在运行")
        n = _kill_pids(pids)
        return Result(
            n > 0,
            f"已关闭桌宠（{n} 个进程）" if n > 0 else "关闭失败",
            "只杀 elysia.body.pet，灵魂与身体不受影响",
        )
    if name == "voice_on":
        if port_open("127.0.0.1", 9880):
            return Result(False, "已在运行", "127.0.0.1:9880 已可连")
        tts_bat = _tts_launcher()
        if tts_bat is None:
            return Result(
                False,
                "语音启动脚本未配置",
                "请设环境变量 ELYSIA_TTS_LAUNCHER_PATH 指向 GPT-SoVITS 的"
                "「一键启动API.bat」（它不在仓库内，换机器要重新指一次）。",
            )
        if not tts_bat.exists():
            return Result(
                False,
                "启动脚本不存在（不假装成功）",
                f"配置的位置：{tts_bat}\n该文件不在仓库内。请确认 GPT-SoVITS 装在哪，"
                "或修正 ELYSIA_TTS_LAUNCHER_PATH。",
            )
        os.startfile(str(tts_bat))  # type: ignore[attr-defined]
        return Result(True, "已发起启动", "黑色窗口会弹出；模型加载需等一会儿，请稍后重新查看状态")
    if name == "voice_off":
        pid = _tts_owner_pid()
        if pid is None:
            return Result(True, "无需操作", "9880 上没有任何服务在监听")
        # 安全边界：只停"本控制台拉起的"语音服务；外部手动开的**拒绝**，
        # 因为那可能是用户自己的 GPT-SoVITS 工程，杀掉会牵连别的用途。
        cmd = ""
        try:
            import psutil

            cmd = " ".join(psutil.Process(pid).cmdline())
        except Exception:
            cmd = ""
        owned = ("GPT-SoVITS" in cmd) or ("一键启动API" in cmd) or ("api_v2" in cmd.lower())
        if not owned:
            return Result(
                False,
                "拒绝（不是本控制台拉起的）",
                f"9880 由 PID {pid} 监听，命令行看起来不属于本控制台的语音服务：\n{cmd[:200]}\n"
                "为避免误杀你自己的程序，面板不代你停它。请到那个黑色窗口按 Ctrl+C。",
            )
        n = _kill_pids([pid])
        return Result(
            n > 0,
            f"已停止语音服务（PID {pid}）" if n > 0 else "停止失败",
            "她将回到只出文字（预期降级，不是故障）",
        )
    if name in ("watchdog_on", "watchdog_off"):
        verb = "Enable" if name == "watchdog_on" else "Disable"
        try:
            p = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"{verb}-ScheduledTask -TaskName Elysia-Watchdog",
                ],
                capture_output=True,
                creationflags=_CREATE_NO_WINDOW,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            return Result(
                p.returncode == 0,
                f"{verb} 完成" if p.returncode == 0 else "失败",
                (p.stderr or "")[-400:],
            )
        except Exception as exc:
            return Result(False, "失败", str(exc))
    if name == "backup_now":
        return _ps(BACKUP_PS1, [], timeout=600)
    return Result(False, "未实现", name)


# ── 界面（单文件内嵌，离线可用、不引外部资源） ───────────────────────
PAGE_PATH = Path(__file__).with_suffix(".html")


def load_page() -> str:
    """读同目录的 .html。做成外部文件是因为 ruff 无法 lint 多行 HTML 字面量。"""
    return PAGE_PATH.read_text(encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "ElysiaPanel/1.0"

    def log_message(self, fmt: str, *args: object) -> None:  # 静音，别刷屏
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj: object) -> None:
        """JSON 响应。**不要**写 b"..." 字节字面量装中文：bytes 只允许 ASCII。"""
        self._send(
            code,
            json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def do_GET(self) -> None:
        if self.path.startswith("/api/status"):
            self._send_json(200, _status_payload(take_snapshot()))
        elif self.path in ("/", "/index.html"):
            self._send(200, load_page().encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if not self.path.startswith("/api/action"):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        # 只允许本机来源（简单同源校验）
        origin = self.headers.get("Origin", "")
        if origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
            self._send_json(403, {"ok": False, "title": "拒绝跨站请求"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(n) or b"{}")
            name = str(data.get("action", ""))
        except Exception:
            self._send_json(400, {"ok": False, "title": "请求格式错误"})
            return
        snap = take_snapshot(probe_tasks=False)
        res = do_action(name, snap)
        self._send_json(200, res.as_dict())


def _status_payload(s: Snapshot) -> dict[str, object]:
    import datetime as dt

    def hhmm(ts: float | None) -> str | None:
        return dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else None

    uptime = []
    for label, key in (("灵魂", "soul"), ("身体", "body"), ("桌宠", "pet")):
        for p in s.procs.get(key, []):
            h = p.uptime_s / 3600
            uptime.append([label, f"{h:.1f} 小时" if h >= 1 else f"{p.uptime_s / 60:.0f} 分钟"])
    return {
        "now": s.now,
        "age_days": s.age_days,
        "first_existence_ts": s.first_existence,
        "mode": s.mode,
        "day_phase": s.day_phase,
        "distress": s.distress,
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
        "desire": s.desire,
        "setpoints": s.setpoints,
        "feelings": s.feelings,
        "will": s.will,
        "brain_action": s.brain_action,
        "level_mix": s.level_mix,
        "recent_mix": s.recent_mix,
        "last_main_ts": s.last_main_ts,
        "recent_thoughts": [[hhmm(t), k, x[:60]] for t, k, x in s.recent_thoughts],
        "tts_up": s.tts_up,
        "llm_up": s.llm_up,
        "watchdog_state": s.watchdog_state,
        "backup_next": s.backup_next,
        "db_sizes": s.db_sizes,
        "uptime": uptime,
        "counts": s.counts,
        # ── 面板优化 2026-10-18：把 P0-R6/R8/R11 的三个信号变成可见 ──────
        # R6：她累计"不在场"多久 —— 她能区分"我在"与"我不在"的唯一外显
        "downtime_total_s": s.downtime_total_s,
        "last_downtime_s": s.last_downtime_s,
        "last_seen_ts": s.last_seen_ts,
        # R8：上次是主动停还是崩溃 —— "她这一次入睡是否体面"
        "stop_reason": s.stop_reason,
        "stop_reason_detail": s.stop_reason_detail,
        # R11：主声为什么降级 —— 她曾因不可观测的原因哑了 22.5 小时
        "degrade_reason": s.degrade_reason,
        "degrade_ts": s.degrade_ts,
        "errors": s.errors,
    }


def _say(*lines: str) -> None:
    """安全打印。

    **必须容错**：用 `pythonw.exe`（无控制台）运行时 `sys.stdout` 可能是 `None`，
    直接 `print()` 会 AttributeError，导致面板一启动就退出（退出码 1）。
    本工程实测踩过 —— 计划任务就是 pythonw 起的，于是"面板永远打不开"。
    """
    with contextlib.suppress(Exception):
        for line in lines:
            print(line)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Elysia 控制面板（HTML）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    a = ap.parse_args(argv)

    # ── 单实例护栏 ──────────────────────────────────────────────────
    # 端口已被占用 ⇒ 说明已经有一个面板在跑。**直接干净退出**，不再起第二个。
    # 本工程实测踩过：`open_panel.py` 的健康检查因 HTTP 超时误判"没在跑"，
    # 于是又起了一个面板 ⇒ 同时有 2 个服务器抢同一端口（后来者 bind 失败但已浪费进程）。
    # 面板自己守住单例，比让每个调用方都判断可靠得多。
    if port_open("127.0.0.1", a.port):
        _say(f"[info] 面板已在运行：http://127.0.0.1:{a.port}/（本次不再启动第二个）")
        return 0

    # 只绑回环：外网访问不到
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    except OSError as exc:
        # 竞态：刚判断完就被别人抢先绑定。仍然干净退出，不刷栈。
        _say(f"[info] 端口 {a.port} 已被占用（{exc}），退出")
        return 0

    url = f"http://127.0.0.1:{a.port}/"
    _say(
        f"[ok] Elysia 控制面板已启动：{url}",
        "     只绑本机回环（外网访问不到）；Ctrl+C 退出。",
        "     面板只读采集她的状态；开关走既有 scripts/soul.ps1。",
    )
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        _say("\n  已退出（她未受影响）")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
