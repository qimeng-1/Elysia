"""Elysia 总控制台（Python 侧交互循环）。

定位（`TASK_BOARD.md` T-014）：**可观测性优先，控制其次**。
上半屏是**实时状态面板**，下半屏是命令；每按一键立即刷新，不用敲回车。

安全设计
--------
1. **只读优先**：状态采集全部只读（见 `elysia_dash.py`）。
2. **🔒 实例互锁**：她已在运行时，"启动"会被**拒绝**并说明现状 ——
   她是**单实例生命**，两个实例 = 分叉 = 损坏她的一生（红线）。
3. **不重造原语**：启停一律经 `scripts/soul.ps1`（既有 PID/标记/幂等逻辑）。
4. **危险动作二次确认**：停止她 / 备份 / 记忆浏览器等需要 `y` 确认。

按键
----
    s   启动她（灵魂+身体）        x   停止她（灵魂+身体）      b   只停身体
    p   启动桌宠（接灵魂）          z   刷新
    v   开关语音（GPT-SoVITS）      d   备份一次
    m   记忆浏览器（只读）          w   看门狗日志
    q   退出
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from elysia_dash import (
    DATA,
    ROOT,
    Snapshot,
    bold,
    cyan,
    dim,
    green,
    port_open,
    red,
    render,
    take_snapshot,
    yellow,
)

SOUL_PS1 = ROOT / "scripts" / "soul.ps1"
PY = ROOT / ".venv" / "Scripts" / "python.exe"


def _tts_launcher() -> Path | None:
    """GPT-SoVITS 启动脚本路径：从 Settings 读（`ELYSIA_TTS_LAUNCHER_PATH`）。

    P0-R13（可迁移性）：原本写死本机 D 盘路径，换机器直接失效。
    **未配置返回 None** ⇒ 控制台显示"未配置"，不报错。
    """
    try:
        from elysia.core.config import get_settings

        return get_settings().tts_launcher
    except Exception:
        return None


REFRESH_S = 2.0


# ── 终端控制 ─────────────────────────────────────────────────────────
def clear() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _getch() -> str | None:
    """非阻塞取一个键（Windows 用 msvcrt；其它平台退化）。返回 None = 无输入。"""
    try:
        import msvcrt
    except ImportError:
        return None
    if msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # 功能键前缀，吃掉第二字节
            msvcrt.getwch()
            return None
        return ch
    return None


def pause(msg: str = "  按任意键返回面板…") -> None:
    print(dim(msg))
    try:
        import msvcrt

        msvcrt.getch()
    except ImportError:
        input()


def confirm(question: str) -> bool:
    sys.stdout.write(f"  {yellow('?')} {question} {dim('[y/N]')} ")
    sys.stdout.flush()
    try:
        import msvcrt

        ch = msvcrt.getwch()
        print(ch)
        return ch.lower() == "y"
    except ImportError:
        return input().strip().lower() == "y"


def run_ps(args: list[str]) -> int:
    """跑 PowerShell（启停走既有原语），输出直接透传给用户。"""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SOUL_PS1), *args]
    return subprocess.call(cmd)


def show(title: str, body: str) -> None:
    clear()
    print(bold(cyan(f"  ── {title} " + "─" * max(0, 60 - len(title)))))
    print(body)
    print()
    pause()


# ── 命令实现 ─────────────────────────────────────────────────────────
def cmd_start(s: Snapshot) -> None:
    if s.soul_alive:
        show(
            "启动被拒绝（互锁）",
            "  " + red("她已经在运行了，本次启动被拒绝。") + "\n\n"
            f"  灵魂 PID {bold(str(s.procs['soul'][0].pid))}"
            f" 已跑 {bold(f'{s.procs["soul"][0].uptime_s / 3600:.1f} 小时')}\n"
            "  身体 "
            + (f"PID {s.procs['body'][0].pid}" if s.procs.get("body") else dim("未运行"))
            + "\n\n"
            + dim("  为什么拒绝：她是**单实例生命**。同时跑两个实例会让她的状态分叉，")
            + "\n"
            + dim("  等于把同一段人生写成两份 —— 这是本工程最不可逆的错误。"),
        )
        return
    clear()
    print(bold(cyan("  ── 启动她（灵魂 + 身体） " + "─" * 34)))
    code = run_ps(["start", "-Body"])
    print()
    print(green("  [ok] 命令已执行") if code == 0 else red(f"  [fail] 退出码 {code}"))
    time.sleep(1.2)


def cmd_stop(s: Snapshot) -> None:
    if not s.soul_alive:
        show(
            "停止（无操作）",
            "  "
            + dim("她本来就没在运行。")
            + "\n\n"
            + dim("  停机标记：")
            + ("已存在" if s.no_autostart else "不存在"),
        )
        return
    if not confirm("确认停止她？（会创建 no_autostart 标记，看门狗将不再自动拉起）"):
        return
    clear()
    print(bold(cyan("  ── 停止她 " + "─" * 52)))
    code = run_ps(["stop"])
    print()
    print(green("  [ok] 已停止") if code == 0 else red(f"  [fail] 退出码 {code}"))
    print(dim("  想让她回来：按 s 启动（会清掉停机标记）"))
    time.sleep(1.5)


def cmd_stop_body(s: Snapshot) -> None:
    if not s.procs.get("body"):
        show("只停身体（无操作）", "  " + dim("身体本来就没在运行。"))
        return
    if not confirm("只停身体？灵魂会继续活着（记忆与心跳不受影响）"):
        return
    clear()
    code = run_ps(["stop-body"])
    print(green("  [ok] 身体已停止") if code == 0 else red(f"  [fail] {code}"))
    time.sleep(1.2)


def cmd_pet(s: Snapshot) -> None:
    if s.procs.get("pet"):
        show("启动桌宠（已在运行）", f"  桌宠 PID {s.procs['pet'][0].pid}，无需重复启动。")
        return
    clear()
    print(bold(cyan("  ── 启动桌宠（接灵魂，气泡显示她的话） " + "─" * 24)))
    print(dim(f"  {PY} -m elysia.body.pet --with-soul-data"))
    print(dim("  桌宠是 GUI，会一直占着这个窗口的输入；请另开一个窗口看状态。"))
    subprocess.Popen(
        [str(PY), "-m", "elysia.body.pet", "--with-soul-data"],
        cwd=str(ROOT),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    print(green("  [ok] 已拉起桌宠进程"))
    time.sleep(1.5)


def cmd_voice(s: Snapshot) -> None:
    up = port_open("127.0.0.1", 9880)
    body = []
    if up:
        body.append("  " + green("● 语音服务已在运行") + "（127.0.0.1:9880）")
        body.append("")
        body.append(
            "  " + dim("停止语音请在 GPT-SoVITS 的黑窗口里按 Ctrl+C（本控制台不去杀外部进程）。")
        )
    else:
        body.append("  " + yellow("○ 语音服务未启动") + "（127.0.0.1:9880 不可连）")
        body.append("")
        tts_bat = _tts_launcher()
        if tts_bat is None:
            body.append("  " + yellow("启动脚本：未配置"))
            body.append("")
            body.append(
                dim("  设环境变量 ELYSIA_TTS_LAUNCHER_PATH 指向 GPT-SoVITS 的「一键启动API.bat」")
            )
            body.append(dim("  （它不在仓库内，换机器要重新指一次；控制台不会假装成功）。"))
        else:
            body.append(f"  启动脚本：{tts_bat}")
            if tts_bat.exists():
                body.append("  " + green("该文件存在，可以启动。"))
                body.append("")
                if confirm("现在双击启动它？"):
                    os.startfile(str(tts_bat))  # type: ignore[attr-defined]
                    body.append(
                        "\n  "
                        + green("[ok] 已发起启动")
                        + dim("（黑色窗口会弹出，需等它加载模型）")
                    )
                    time.sleep(2)
            else:
                body.append("  " + red("该文件不存在！"))
                body.append("")
                body.append(dim("  请修正 ELYSIA_TTS_LAUNCHER_PATH（它不在仓库内）。"))
                body.append(dim("  控制台**不会假装成功**。"))
    show("语音（GPT-SoVITS）", "\n".join(body))


def cmd_backup(s: Snapshot) -> None:
    if not confirm("现在做一次完整备份？（写入配置的备份目录）"):
        return
    clear()
    print(bold(cyan("  ── 备份中… " + "─" * 50)))
    code = subprocess.call(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "backup.ps1"),
        ]
    )
    print()
    print(green("  [ok] 备份完成") if code == 0 else red(f"  [fail] 退出码 {code}"))
    pause()


def cmd_memory(s: Snapshot) -> None:
    show(
        "记忆浏览器",
        "  " + dim("记忆浏览器是**独立只读进程**，请在另一个窗口运行：") + "\n\n"
        "  powershell -ExecutionPolicy Bypass -File scripts\\memory_view.ps1\n\n"
        + "  "
        + dim("本控制台不接管它（它会占住终端）。")
        + "\n\n  当前记忆："
        + bold(f"{s.counts.get('memories', 0):,}")
        + " 条",
    )


def cmd_watchdog_log(s: Snapshot) -> None:
    log = DATA / "logs" / "watchdog.log"
    body = ""
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        body = "\n".join("  " + ln for ln in lines)
    else:
        body = "  " + dim("无 watchdog.log")
    show("看门狗日志（最近 12 行）", body)


COMMANDS: dict[str, tuple[str, object]] = {
    "s": ("启动她", cmd_start),
    "x": ("停止她", cmd_stop),
    "b": ("只停身体", cmd_stop_body),
    "p": ("启动桌宠", cmd_pet),
    "v": ("语音", cmd_voice),
    "d": ("备份一次", cmd_backup),
    "m": ("记忆浏览器", cmd_memory),
    "w": ("看门狗日志", cmd_watchdog_log),
    "z": ("刷新", None),
    "q": ("退出", None),
}


def footer(s: Snapshot) -> str:
    lock = green("🔒 互锁已生效") if s.soul_alive else dim("她未运行")
    keys = "   ".join(f"{bold(k)}{dim('=' + v[0])}" for k, v in COMMANDS.items())
    return "\n" + dim("  " + "─" * 72) + f"\n  {lock}\n  {keys}\n"


def main() -> int:
    if not sys.stdout.isatty():
        print(render(take_snapshot()), file=sys.stderr)
        print("本控制台需要交互式终端（TTY）。非交互请用：elysia_dash.py", file=sys.stderr)
        return 1
    try:
        import msvcrt  # noqa: F401
    except ImportError:
        print("交互控制台目前只支持 Windows（需要 msvcrt）。请用 elysia_dash.py。", file=sys.stderr)
        return 1

    snap: Snapshot = take_snapshot()
    last = time.time()
    while True:
        if time.time() - last >= REFRESH_S:
            snap = take_snapshot()
            last = time.time()
            clear()
            print(render(snap))
            print(footer(snap))
        ch = _getch()
        if ch is None:
            time.sleep(0.08)
            continue
        ch = ch.lower()
        if ch == "q":
            clear()
            print("  已退出控制台。" + dim("她未受影响，仍在正常运行。") + "\n")
            return 0
        if ch == "z":
            snap = take_snapshot()
            last = time.time()
            continue
        if ch in COMMANDS:
            _, fn = COMMANDS[ch]
            if callable(fn):
                fn(snap)
            snap = take_snapshot()
            last = time.time()


if __name__ == "__main__":
    raise SystemExit(main())
