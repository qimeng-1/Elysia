"""看门狗判活单测（P0-R3 / D-3）：必须能发现"进程在、心跳停"，且自己每次都要留痕。

审计实测（`LEAD_P0_AUDIT_2026-10-17.md` D-3）：
- 旧 `Test-SoulAlive` **只看 PID 是否存在** ⇒ 僵死（进程在、心跳停）永远救不了；
- 2026-10-17 00:52 → 20:41 她停机 **16.82 小时**，`watchdog.log` **一个字都没写**
  （看门狗自己没跑，也没人知道）。

本文件的用例用**真实 PowerShell 脚本 + 注入的临时项目根**跑端到端：
不碰真实的 `data/`、不会启停真实的她（`-SoulScript` 指向一个只写标记的假脚本）。

取消测试：`test_watchdog_kills_stale_process_and_restarts`
—— 去掉心跳新鲜度判据（退回"只看 PID"）时，该用例**必红**：
进程不会被杀、假 soul.ps1 不会被调用、日志里不会出现"判定僵死"。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = REPO_ROOT / "scripts" / "watchdog.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    POWERSHELL is None or os.name != "nt", reason="看门狗是 PowerShell 脚本（Windows）"
)

FAKE_SOUL = """param([string]$Action = "status", [switch]$Body)
$marker = $env:ELYSIA_TEST_RESTART_MARKER
if ($marker) {
    $line = "restart:action=" + $Action + ";body=" + $Body
    Add-Content -Path $marker -Value $line -Encoding ASCII
}
"""


def _make_heartbeat_db(path: Path, *, age_s: float | None) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE IF NOT EXISTS heartbeats ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
        "beat_type TEXT NOT NULL, payload TEXT NOT NULL)"
    )
    if age_s is not None:
        con.execute(
            "INSERT INTO heartbeats (ts, beat_type, payload) VALUES (?, 'soul', '{}')",
            (time.time() - age_s,),
        )
    con.commit()
    con.close()


@pytest.fixture()
def fake_root(tmp_path: Path) -> Path:
    """临时项目根：data/{run,logs} + 一个只写标记的假 soul.ps1。"""
    root = tmp_path / "proj"
    (root / "data" / "run").mkdir(parents=True)
    (root / "data" / "logs").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "scripts" / "soul.ps1").write_text(FAKE_SOUL, encoding="ascii")
    return root


def _restart_marker(fake_root: Path) -> Path:
    return fake_root / "restart-called.txt"


def _run_watchdog(fake_root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "ELYSIA_TEST_RESTART_MARKER": str(_restart_marker(fake_root))}
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WATCHDOG),
            "-ProjectRoot",
            str(fake_root),
            "-PythonExe",
            sys.executable,
            "-SoulScript",
            str(fake_root / "scripts" / "soul.ps1"),
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env=env,
        cwd=str(REPO_ROOT),
    )


def _log_text(fake_root: Path) -> str:
    path = fake_root / "data" / "logs" / "watchdog.log"
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _start_dummy_process(tag: str = "elysia.soul.main") -> subprocess.Popen[bytes]:
    """一个"活着但什么都不做"的进程，当作僵死的她（杀掉它不会伤到任何真实进程）。

    P0-R14：看门狗现在会校验命令行身份；`tag` 就是让它"被认作她"的标记。
    `tag=""` 表示**无关进程**（模拟 PID 被系统复用）。
    """
    args = [sys.executable, "-c", "import time; time.sleep(120)"]
    if tag:
        args.append(tag)
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _write_pid(fake_root: Path, pid: int) -> None:
    (fake_root / "data" / "run" / "soul.pid").write_text(str(pid), encoding="utf-8")


# ── 取消测试：进程在、心跳停 ⇒ 必须 kill 后重启 ──────────────────────


def test_watchdog_kills_stale_process_and_restarts(fake_root: Path) -> None:
    """取消测试（P0-R3）：旧判据只看 PID ⇒ 该用例必红（不杀、不重启、无"僵死"日志）。"""
    proc = _start_dummy_process()
    try:
        _write_pid(fake_root, proc.pid)
        _make_heartbeat_db(fake_root / "data" / "heartbeat.db", age_s=3600.0)

        result = _run_watchdog(fake_root, "-StaleSeconds", "60", "-StartupGraceSeconds", "0")

        assert result.returncode == 0, result.stdout + result.stderr
        log = _log_text(fake_root)
        assert "判定僵死" in log, f"必须判定僵死并记录理由；实际日志：{log}"
        assert "verdict=stale" in log
        assert "action=killed&restart" in log
        # 僵死进程必须真的被杀（旧写法：只看 PID ⇒ 永远认为她活着）
        assert proc.wait(timeout=15) is not None
        # 重启必须真的被调用（走 soul.ps1 原语；这里是假脚本写的标记）
        marker = _restart_marker(fake_root)
        assert marker.exists(), "判定僵死后必须调用 soul.ps1 start -Body"
        assert "restart:action=start;body=True" in marker.read_text(encoding="ascii")
    finally:
        if proc.poll() is None:
            proc.kill()


def test_watchdog_leaves_healthy_process_alone(fake_root: Path) -> None:
    """心跳新鲜 ⇒ 不杀、不重启，且每次运行都留一行（run-ok）。"""
    proc = _start_dummy_process()
    try:
        _write_pid(fake_root, proc.pid)
        _make_heartbeat_db(fake_root / "data" / "heartbeat.db", age_s=1.0)

        result = _run_watchdog(fake_root, "-StaleSeconds", "60", "-StartupGraceSeconds", "0")

        assert result.returncode == 0, result.stdout + result.stderr
        log = _log_text(fake_root)
        assert "verdict=healthy" in log
        assert "判定僵死" not in log
        assert proc.poll() is None, "活着的她绝不能被误杀"
        assert not _restart_marker(fake_root).exists()
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_watchdog_does_not_kill_during_startup_grace(fake_root: Path) -> None:
    """她刚起来、心跳还没写出来时不能判僵死（错杀比不杀更伤）。"""
    proc = _start_dummy_process()
    try:
        _write_pid(fake_root, proc.pid)
        _make_heartbeat_db(fake_root / "data" / "heartbeat.db", age_s=3600.0)

        # 不传 -StartupGraceSeconds ⇒ 用默认 120s 宽限
        result = _run_watchdog(fake_root, "-StaleSeconds", "60")

        assert result.returncode == 0, result.stdout + result.stderr
        log = _log_text(fake_root)
        assert "verdict=starting(grace)" in log
        assert "判定僵死" not in log
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=10)


# ── 每次运行都留痕 + 失败可见 ───────────────────────────────────────


def test_watchdog_logs_every_run_even_when_nothing_happens(fake_root: Path) -> None:
    """旧版只在"有动作"时写日志；现在**每次运行**都必须留下 run-ok。"""
    (fake_root / "data" / "run" / "no_autostart").write_text("", encoding="utf-8")

    for _ in range(2):
        result = _run_watchdog(fake_root)
        assert result.returncode == 0, result.stdout + result.stderr

    log = _log_text(fake_root)
    assert log.count("run-ok") == 2, f"每次运行都要留痕；实际：{log}"
    assert "verdict=stopped_by_user" in log
    assert not _restart_marker(fake_root).exists()
    # "最后一次成功运行时间" = 最后一条 run-ok 行的时间戳（面板读尾行即可）
    last_line = [line for line in log.splitlines() if "run-ok" in line][-1]
    assert last_line.startswith("[")


def test_watchdog_reports_unknown_beat_without_killing(fake_root: Path) -> None:
    """心跳读不出来 ⇒ **不许猜成"她死了"**（进程在就当她在），但要把原因写清楚。"""
    proc = _start_dummy_process()
    try:
        _write_pid(fake_root, proc.pid)
        (fake_root / "data" / "heartbeat.db").write_bytes(b"not-a-sqlite-file")

        result = _run_watchdog(fake_root, "-StaleSeconds", "60", "-StartupGraceSeconds", "0")

        assert result.returncode == 0, result.stdout + result.stderr
        log = _log_text(fake_root)
        assert "unknown_beat" in log
        assert "读心跳失败" in log
        assert proc.poll() is None, "判不了的时候绝不杀（宁可少杀，不可错杀）"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_watchdog_missing_python_is_reported(fake_root: Path) -> None:
    """python 解释器不见时，判活必须**明说原因**，不能静默当成"没心跳"。"""
    proc = _start_dummy_process()
    try:
        _write_pid(fake_root, proc.pid)
        _make_heartbeat_db(fake_root / "data" / "heartbeat.db", age_s=1.0)

        result = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WATCHDOG),
                "-ProjectRoot",
                str(fake_root),
                "-PythonExe",
                str(fake_root / "no-such-python.exe"),
                "-SoulScript",
                str(fake_root / "scripts" / "soul.ps1"),
                "-StartupGraceSeconds",
                "0",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            cwd=str(REPO_ROOT),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        log = _log_text(fake_root)
        assert "python 解释器不存在" in log
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_watchdog_does_not_fake_success_when_soul_script_missing(fake_root: Path) -> None:
    """找不到 soul.ps1 ⇒ 明确报失败（不许假装拉起），且仍以 run-ok 收尾。"""
    (fake_root / "scripts" / "soul.ps1").unlink()
    _make_heartbeat_db(fake_root / "data" / "heartbeat.db", age_s=3600.0)

    result = subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WATCHDOG),
            "-ProjectRoot",
            str(fake_root),
            "-PythonExe",
            sys.executable,
            "-SoulScript",
            str(fake_root / "scripts" / "soul.ps1"),
            "-StartupGraceSeconds",
            "0",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=str(REPO_ROOT),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log = _log_text(fake_root)
    assert "找不到 soul.ps1" in log
    assert "failed(no-script)" in log
    assert "verdict=not_running" in log


# ── 脚本自身的硬约束（本工程踩过 3 次的 .ps1 编码坑）─────────────────


def test_watchdog_is_utf8_with_bom_and_parses() -> None:
    """`.ps1` 必须 UTF-8 **带 BOM**：PowerShell 5.1 按 ANSI 读中文会语法撑破（已踩 3 次）。"""
    raw = WATCHDOG.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "watchdog.ps1 丢了 UTF-8 BOM（PS 5.1 会按 ANSI 读中文）"

    script = (
        "$e=$null;$t=$null;"
        f"$null=[System.Management.Automation.Language.Parser]::ParseFile('{WATCHDOG}',[ref]$t,[ref]$e);"
        "if($e.Count){$e|ForEach-Object{$_.Message};exit 1}else{'PARSE OK'}"
    )
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PARSE OK" in result.stdout


# ── P0-R14：PID 复用（不能杀无辜进程）+ 1 分钟巡视 + 退场哨兵 ──────────────


def test_watchdog_does_not_kill_reused_pid(fake_root: Path) -> None:
    """取消测试（P0-R14）：PID 被无关进程占用 ⇒ 只清理 PID 文件，**绝不杀它**。"""
    innocent = _start_dummy_process(tag="")  # 命令行不含 elysia.soul.main
    try:
        _write_pid(fake_root, innocent.pid)
        _make_heartbeat_db(fake_root / "data" / "heartbeat.db", age_s=3600.0)
        (fake_root / "data" / "run" / "no_autostart").write_text("", encoding="utf-8")

        result = _run_watchdog(fake_root, "-StaleSeconds", "60", "-StartupGraceSeconds", "0")

        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        log = _log_text(fake_root)
        assert "其它进程占用" in log, log
        assert innocent.poll() is None, "**绝不能杀掉恰好复用了 PID 的无关进程**"
        assert not (fake_root / "data" / "run" / "soul.pid").exists(), "陈旧 PID 文件应被清理"
        assert "killed" not in log
    finally:
        if innocent.poll() is None:
            innocent.kill()


def test_watchdog_default_interval_is_one_minute() -> None:
    """取消测试（P0-R14）：巡视间隔必须是 1 分钟（旧值 5 分钟 = 4.8 分钟盲窗）。"""
    text = WATCHDOG.read_text(encoding="utf-8", errors="replace")
    assert "[int]$IntervalMinutes = 1" in text, "默认巡视间隔应为 1 分钟"
    # 注册方式必须能**长期**重复：旧 /SC MINUTE 只重复 72 小时就停了；
    # 现在用"每天 00:00 的触发器 + 每 N 分钟重复、持续 24h"（ScheduledTasks 模块）。
    assert "New-ScheduledTaskTrigger -Daily" in text
    assert "RepetitionInterval" in text and "RepetitionDuration" in text


def test_exit_sentinel_records_exit_code(tmp_path: Path) -> None:
    """P0-R14：退场哨兵必须写下退出码（区分"自己崩了 vs 被别人杀了"的最低成本证据）。"""
    sentinel = REPO_ROOT / "scripts" / "soul_sentinel.py"
    target = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    out_dir = tmp_path / "logs"
    watcher = subprocess.Popen(
        [
            sys.executable,
            str(sentinel),
            "--pid",
            str(target.pid),
            "--out",
            str(out_dir),
            "--kind",
            "soul",
            "--started-ts",
            str(time.time()),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.5)
        target.kill()  # 模拟"被 TerminateProcess 杀掉"
        target.wait(timeout=20)
        assert watcher.wait(timeout=30) == 0
    finally:
        for proc in (target, watcher):
            if proc.poll() is None:
                proc.kill()

    files = list(out_dir.glob("exit-*.json"))
    assert files, "哨兵必须写下 exit-*.json"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "soul"
    assert payload["pid"] == target.pid
    assert payload["exit_code"] is not None, payload
    assert payload["exit_code_hex"] is not None
    assert payload["crash_logs_since_start"] == []
    assert "watchdog.log" in "".join(p.name for p in out_dir.iterdir())


def test_exit_sentinel_handles_missing_process(tmp_path: Path) -> None:
    """进程本来就不在 ⇒ 哨兵也要写下证据并正常退出（不假装知道退出码）。"""
    sentinel = REPO_ROOT / "scripts" / "soul_sentinel.py"
    out_dir = tmp_path / "logs"
    result = subprocess.run(
        [
            sys.executable,
            str(sentinel),
            "--pid",
            "999999",
            "--out",
            str(out_dir),
            "--kind",
            "body",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    files = list(out_dir.glob("exit-*.json"))
    assert files
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["exit_code"] is None
    assert payload["note"], "拿不到退出码时必须写明原因"
