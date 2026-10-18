"""退场哨兵（P0-R14）：把"死因未知"变成"退出码 N"。

背景（2026-10-18 21:02:23 事故）：她**节律完美**（死前 15 分钟心跳中位 1.007s）地被瞬间杀掉，
`crash-*.log` 没有（R2 只覆盖 Python 异常）、Windows 审计日志未开启 ⇒ 无法指认凶手。
Windows 上 `TerminateProcess` 的退出码与"自己崩溃"不同（常见 1 / 0xFFFFFFFF），
**这是区分"自己崩了"与"被别人杀了"的最低成本证据**。

做法：本脚本由 `soul.ps1` 在启动她之后作为**兄弟进程**拉起（**不是她的父进程**，
不进入任何"随父进程一起死"的进程树），只做两件事：
1. 用 `OpenProcess` 拿句柄 + `WaitForSingleObject` 等她退出；
2. `GetExitCodeProcess` 取退出码，写 `data/logs/exit-<ts>.json`，
   并尽力往 `watchdog.log` 追加一行（面板读日志就能看到"上一次她是怎么退出的"）。

用法（由 soul.ps1 调用，一般不用手动跑）：
    python scripts/soul_sentinel.py --pid 12345 --out data/logs \
        --kind soul --started-ts 1792288735.1

退出码：0 = 已写下证据（或平台不支持/进程本来就不在，均已如实记录）
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path

# 已知的"异常退出码"提示（只是提示，不做断言——真正的判据是"有没有 crash log"）
_EXIT_HINTS = {
    1: "Python 未捕获异常 / 被 TerminateProcess(1) 杀 —— 两者都常见，需结合 crash log 判断",
    0xC0000005: "访问冲突（原生崩溃）",
    0xC0000409: "栈溢出/安全缓冲（快速失败）",
    0xC000013A: "被 Ctrl+C / 控制台中断",
    0xFFFFFFFF: "终止进程（常见于外部强杀 / TerminateProcess）",
}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_watchlog(out_dir: Path, line: str) -> None:
    """尽力往 watchdog.log 追加一行（文件可能正被看门狗写；失败不影响证据落盘）。"""
    with (
        contextlib.suppress(OSError),
        open(out_dir / "watchdog.log", "a", encoding="utf-8") as handle,
    ):
        handle.write(line + "\n")


def _crash_logs_since(log_dir: Path, started_ts: float) -> list[str]:
    """她这次运行期间出现的 crash log（有 ⇒ 更可能是"自己崩了"）。"""
    return sorted(p.name for p in log_dir.glob("crash-*.log") if p.stat().st_mtime >= started_ts)


def _wait_windows(pid: int) -> tuple[int | None, str]:
    """等进程退出并返回 (退出码, note)。非 Windows / 打不开句柄都如实返回 None。"""
    if sys.platform != "win32":
        return None, "平台不是 Windows：无法读取非子进程的退出码（未采集）"
    import ctypes
    from ctypes import wintypes

    PROCESS_SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    WAIT_INFINITE = 0xFFFFFFFF
    WAIT_OBJECT_0 = 0x0

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    access = PROCESS_SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        return None, f"OpenProcess 失败（err={ctypes.get_last_error()}）——进程可能已退出或权限不足"
    try:
        wait = kernel32.WaitForSingleObject(handle, WAIT_INFINITE)
        if wait != WAIT_OBJECT_0:
            return None, f"WaitForSingleObject 返回 {wait}"
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None, f"GetExitCodeProcess 失败（err={ctypes.get_last_error()}）"
        return int(code.value), "ok"
    finally:
        kernel32.CloseHandle(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="退场哨兵：记录她的退出码（P0-R14）")
    parser.add_argument("--pid", type=int, required=True, help="她要被观察的进程 PID")
    parser.add_argument("--out", type=Path, default=Path("data/logs"), help="证据输出目录")
    parser.add_argument("--kind", default="soul", choices=("soul", "body"), help="进程种类")
    parser.add_argument(
        "--started-ts", type=float, default=0.0, help="她这次启动的时刻（epoch 秒）"
    )
    args = parser.parse_args(argv)

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    started_ts = args.started_ts or time.time()

    exit_code, note = _wait_windows(args.pid)
    ended_ts = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(ended_ts))
    path = out_dir / f"exit-{stamp}-{args.kind}-{args.pid}.json"
    crash_logs = _crash_logs_since(out_dir, started_ts)
    payload = {
        "kind": args.kind,
        "pid": args.pid,
        "exit_code": exit_code,
        "exit_code_hex": (f"0x{exit_code:X}" if exit_code is not None else None),
        "exit_hint": _EXIT_HINTS.get(exit_code) if exit_code is not None else None,
        "started_ts": started_ts,
        "ended_ts": ended_ts,
        "lived_s": round(max(0.0, ended_ts - started_ts), 1),
        "ended_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ended_ts)),
        "crash_logs_since_start": crash_logs,
        "note": note,
        "verdict_hint": (
            "有 crash log ⇒ 更像她自己崩了（看 crash-*.log）"
            if crash_logs
            else "无 crash log ⇒ 更像被外部杀掉/进程被终止（R2 的异常路径没跑）"
        ),
    }
    _write(path, payload)
    code_text = payload["exit_code_hex"] or "unknown"
    _append_watchlog(
        out_dir,
        f"[{payload['ended_at']}] exit-sentinel | {args.kind} pid={args.pid} "
        f"exit={code_text} lived={payload['lived_s']}s crash_logs={len(crash_logs)} -> {path.name}",
    )
    print(f"[exit-sentinel] {args.kind} pid={args.pid} exit={code_text} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
