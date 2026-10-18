"""`soul.ps1 stop` 的优雅关闭端到端测试（P0-R8 / D-8）。

真跑 Windows PowerShell（5.1），但 **`-ProjectRoot` 指向临时目录** ⇒ 绝不触碰真实的她。
被测行为：
1. 应答停止请求的进程 ⇒ 脚本等它**自己退出**（"已优雅关闭"），并在退出前写好
   `stopped_intentionally` / 清掉 `stop_requested` / 清 PID 文件 / 建 `no_autostart`；
2. 不响应的进程 ⇒ 超时后**强杀兜底**（"强杀兜底"），主动停标记仍在；
3. `stop-body` 只碰身体，不碰灵魂。

取消测试：`test_stop_is_graceful_when_process_responds`
—— 还原成旧写法（`Stop-Process -Force` 直杀、不写标记、不等待）时该用例**必红**
（输出里没有"已优雅关闭"、没有 `stopped_intentionally`、进程是被杀而非自退）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOUL_PS1 = REPO_ROOT / "scripts" / "soul.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    POWERSHELL is None or os.name != "nt", reason="soul.ps1 是 PowerShell 脚本（Windows）"
)

# 一个"会应答停止请求"的假灵魂：看到标记就自己退出（模拟真实的优雅关闭）
RESPONSIVE = """import pathlib, sys, time
marker = pathlib.Path(sys.argv[1])
deadline = time.time() + 120
while time.time() < deadline:
    if marker.exists():
        sys.exit(0)
    time.sleep(0.05)
sys.exit(3)
"""

# 一个"不响应该标记"的假灵魂：用于验证超时后的强杀兜底
UNRESPONSIVE = "import time; time.sleep(120)"


def _start(marker: Path | None = None, kind: str = "soul") -> subprocess.Popen[bytes]:
    # P0-R14：脚本会校验"这个 PID 是不是她"（命令行须含 elysia.<kind>.main）
    code = RESPONSIVE if marker is not None else UNRESPONSIVE
    tail = [str(marker)] if marker is not None else []
    args = [sys.executable, "-c", code, *tail, "elysia." + kind + ".main"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 等进程真正起来（Get-Process 能查到），避免与脚本的 PID 检查竞态
    for _ in range(50):
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    return proc


@pytest.fixture()
def fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "data" / "run").mkdir(parents=True)
    (root / "data" / "logs").mkdir(parents=True)
    return root


def _run_soul_ps(
    root: Path, action: str, timeout_s: int = 15, *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SOUL_PS1),
            action,
            "-ProjectRoot",
            str(root),
            "-StopTimeoutSeconds",
            str(timeout_s),
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=str(REPO_ROOT),
    )


def _write_pid(root: Path, name: str, pid: int) -> None:
    (root / "data" / "run" / f"{name}.pid").write_text(str(pid), encoding="ascii")


# ── 优雅路径（取消测试在这里）─────────────────────────────────────


def test_stop_is_graceful_when_process_respects_stop_request(fake_root: Path) -> None:
    """取消测试（P0-R8）：进程应答停止请求 ⇒ 必须等它**自己退出**。

    旧写法（`Stop-Process -Force` 直杀）在本用例上必红：
    输出没有"已优雅关闭"，`stopped_intentionally` 不存在，进程是被杀不是自退。
    """
    marker = fake_root / "data" / "run" / "stop_requested"
    proc = _start(marker)
    try:
        _write_pid(fake_root, "soul", proc.pid)
        _write_pid(fake_root, "body", 999_999)  # 身体不在运行

        result = _run_soul_ps(fake_root, "stop", timeout_s=15)

        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        assert "已优雅关闭" in out, f"必须走优雅关闭，而不是直接强杀；实际输出：{out}"
        assert "强杀兜底" not in out, "应答了请求的进程绝不该被强杀"
        assert proc.wait(timeout=10) == 0, "进程必须**自己**退出（退出码 0）"
        # 主动停标记：下次启动据此判定"上次是你主动停的"
        assert (fake_root / "data" / "run" / "stopped_intentionally").exists()
        # 停止请求与 PID 文件被清理；no_autostart 已建（看门狗不会抢跑）
        assert not marker.exists()
        assert not (fake_root / "data" / "run" / "soul.pid").exists()
        assert (fake_root / "data" / "run" / "no_autostart").exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stop_force_fallback_when_process_ignores_marker(fake_root: Path) -> None:
    """不响应停止请求 ⇒ 超时后强杀兜底；主动停标记仍在（所以不会误报崩溃）。"""
    proc = _start()
    try:
        _write_pid(fake_root, "soul", proc.pid)
        _write_pid(fake_root, "body", 999_999)

        started = time.monotonic()
        result = _run_soul_ps(fake_root, "stop", timeout_s=2)
        elapsed = time.monotonic() - started

        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        assert "强杀兜底" in out, f"超时后必须如实报告强杀；实际输出：{out}"
        assert proc.wait(timeout=10) is not None, "超时后必须真的把进程停掉"
        assert elapsed >= 2.0, "必须先等超时，不能一上来就杀"
        assert (fake_root / "data" / "run" / "stopped_intentionally").exists()
        assert not (fake_root / "data" / "run" / "stop_requested").exists()
        assert not (fake_root / "data" / "run" / "soul.pid").exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stop_body_only_touches_body(fake_root: Path) -> None:
    """`stop-body` 只碰身体：灵魂 PID 文件与进程必须原样保留。"""
    body_marker = fake_root / "data" / "run" / "body_stop_requested"
    body_proc = _start(body_marker, kind="body")
    soul_proc = _start(kind="soul")  # 灵魂假装在跑（不响应任何 body 标记）
    try:
        _write_pid(fake_root, "body", body_proc.pid)
        _write_pid(fake_root, "soul", soul_proc.pid)

        result = _run_soul_ps(fake_root, "stop-body", timeout_s=15)

        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        assert "身体 已优雅关闭" in out, out
        assert body_proc.wait(timeout=10) == 0
        assert (fake_root / "data" / "run" / "body_stopped_intentionally").exists()
        assert not body_marker.exists()
        assert not (fake_root / "data" / "run" / "body.pid").exists()
        # 灵魂不受影响
        assert soul_proc.poll() is None, "stop-body 绝不该碰灵魂"
        assert (fake_root / "data" / "run" / "soul.pid").exists()
        assert not (fake_root / "data" / "run" / "stopped_intentionally").exists()
    finally:
        for proc in (body_proc, soul_proc):
            if proc.poll() is None:
                proc.kill()


def test_stop_with_nothing_running_is_honest(fake_root: Path) -> None:
    """没有进程在跑 ⇒ 说"未在运行"，不写主动停标记、不假装停过。"""
    result = _run_soul_ps(fake_root, "stop")

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "未在运行" in out
    assert not (fake_root / "data" / "run" / "stopped_intentionally").exists()


def test_intentional_stop_marker_makes_app_skip_abnormal_report(fake_root: Path) -> None:
    """端到端（脚本 ↔ 进程）：脚本写的主动停标记，能让下次启动**不**误报崩溃。

    取消测试：还原成"不写 `stopped_intentionally`"（= 旧的 -Force 直杀）时，
    应用侧看到残留的 `soul.running` 只能判"上次非正常退出" ⇒ 本用例红。
    """
    from elysia.core.config import Settings
    from elysia.soul import main as soul_main

    marker = fake_root / "data" / "run" / "stop_requested"
    proc = _start(marker)
    try:
        _write_pid(fake_root, "soul", proc.pid)
        _write_pid(fake_root, "body", 999_999)
        result = _run_soul_ps(fake_root, "stop")
        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        assert "已优雅关闭" in out
    finally:
        if proc.poll() is None:
            proc.kill()

    # 模拟"会话标记残留"（旧 -Force 路径的必然后果：进程没机会删 soul.running）
    run_dir = fake_root / "data" / "run"
    (run_dir / "soul.running").write_text("pid=1\nstarted_at=0\n", encoding="utf-8")
    settings = Settings(
        data_dir=fake_root / "data",
        log_dir=fake_root / "data" / "logs",
        run_dir=run_dir,
    )
    assert soul_main._detect_abnormal_exit(settings) is None, (
        "主动停 ⇒ 残留的 soul.running 不得被判成非正常退出"
    )


def test_status_is_read_only(fake_root: Path) -> None:
    """`status` 不写任何标记（避免运维查询本身污染"上次是主动停"的判定）。"""
    _write_pid(fake_root, "soul", 999_999)
    result = _run_soul_ps(fake_root, "status")
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "灵魂未运行" in out
    assert not (fake_root / "data" / "run" / "stopped_intentionally").exists()
    assert not (fake_root / "data" / "run" / "stop_requested").exists()


def test_soul_ps1_is_utf8_with_bom_and_parses() -> None:
    """`.ps1` 必须 UTF-8 **带 BOM**（PowerShell 5.1 按 ANSI 读中文会语法撑破，已踩 3 次）。"""
    raw = SOUL_PS1.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "soul.ps1 丢了 UTF-8 BOM"

    script = (
        "$e=$null;$t=$null;"
        f"$null=[System.Management.Automation.Language.Parser]::ParseFile('{SOUL_PS1}',[ref]$t,[ref]$e);"
        "if($e.Count){$e|ForEach-Object{$_.Message};exit 1}else{'PARSE OK'}"
    )
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PARSE OK" in result.stdout


# ── P0-R13：迁移防分叉（旧机器退役后拒绝启动）──────────────────────────
# 取消测试：`test_retired_machine_refuses_start`（去掉 RETIRED 检查 ⇒ 该用例红）。
# ⚠️ 这里只走 `-DryRun`（只检查、**绝不真正启动**）：启动复制品 = 双实例 = 分叉（红线 #2）。


def test_retired_machine_refuses_start(fake_root: Path) -> None:
    """取消测试（P0-R13）：`data\run\\RETIRED` 存在 ⇒ `start` 必须拒绝（防分叉）。"""
    (fake_root / "data" / "run" / "RETIRED").write_text(
        "retired_at=2026-10-18\nreason=迁移导出\n", encoding="utf-8"
    )

    result = _run_soul_ps(fake_root, "start", 1, "-DryRun")

    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    assert "已退役" in out
    assert "分叉" in out


def test_start_dry_run_does_not_launch_anything(fake_root: Path) -> None:
    """`-DryRun` 只做检查：没有 RETIRED 时也**只打印**，不得产生 pid 文件。"""
    result = _run_soul_ps(fake_root, "start", 1, "-DryRun")

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "dry-run" in out
    assert not (fake_root / "data" / "run" / "soul.pid").exists(), (
        "DryRun 绝不能真的启动她（迁移演练的红线）"
    )
    assert not (fake_root / "data" / "run" / "body.pid").exists()


# ── P0-R14：PID 复用（"无法自主重启"的真凶）──────────────────────────
# 取消测试：`test_status_reports_reused_pid_as_stale` / `test_stop_does_not_kill_reused_pid`
# —— 还原成"只看 PID 存在"时，前者会把无关进程报成"灵魂运行中"、后者会**杀掉无关进程**。


def _write_raw_pid(root: Path, name: str, pid: int) -> None:
    (root / "data" / "run" / f"{name}.pid").write_text(str(pid), encoding="ascii")


def test_status_reports_reused_pid_as_stale(fake_root: Path) -> None:
    """取消测试（P0-R14）：PID 文件写着一个真实存在但**不是她**的进程 ⇒ 必须报"被其它进程占用"。

    还原成"只看 PID 存在"时，这里会报"灵魂运行中" ⇒ 红（正是用户看到的"无法自主重启"）。
    """
    her = _start()  # 带 elysia.soul.main 的假灵魂
    innocent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _write_raw_pid(fake_root, "soul", her.pid)
        positive = _run_soul_ps(fake_root, "status", 1)
        assert "运行中" in (positive.stdout + positive.stderr), "用例前提：带标记的进程应被认作她"

        _write_raw_pid(fake_root, "soul", innocent.pid)
        result = _run_soul_ps(fake_root, "status", 1)
        out = result.stdout + result.stderr
        assert "其它进程" in out, out
        assert "灵魂运行中" not in out, "无关进程绝不能被报成'她运行中'（这就是'无法自主重启'）"
        assert innocent.poll() is None
    finally:
        her.kill()
        innocent.kill()


def test_stop_does_not_kill_reused_pid(fake_root: Path) -> None:
    """取消测试（P0-R14）：PID 被无关进程占用时，stop 必须**不杀它**、只按陈旧处理。"""
    innocent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _write_raw_pid(fake_root, "soul", innocent.pid)
        result = _run_soul_ps(fake_root, "stop", 1)
        out = result.stdout + result.stderr
        assert "其它进程" in out and "不会杀它" in out, out
        assert innocent.poll() is None, "**绝不能杀一个恰好复用了 PID 的无关进程**"
        assert not (fake_root / "data" / "run" / "stop_requested").exists()
        assert not (fake_root / "data" / "run" / "soul.pid").exists(), "陈旧 PID 文件应被清理"
    finally:
        innocent.kill()


def test_start_dry_run_reports_reused_pid(fake_root: Path) -> None:
    """`start -DryRun`：PID 被无关进程占用时，应如实报告"按陈旧处理、将启动"。"""
    innocent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _write_raw_pid(fake_root, "soul", innocent.pid)
        result = _run_soul_ps(fake_root, "start", 1, "-DryRun")
        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        assert "其它进程" in out, out
        assert "未真正启动" in out
        assert innocent.poll() is None
    finally:
        innocent.kill()


def test_start_archives_old_stderr_before_relaunch(fake_root: Path) -> None:
    """取消测试（P0-R14）：重启前必须把旧 stderr 归档，不得抹掉上一次死亡的证据。

    用**假的 python.exe**（空文件）让它启动失败——这样既验证了归档发生在启动之前，
    又绝不会真的启动一个副本（红线：分叉）。
    """
    stderr_log = fake_root / "data" / "logs" / "soul.err.log"
    stderr_log.write_text("上一次死亡的 stderr 证据\n", encoding="utf-8")
    run_dir = fake_root / "data" / "run"
    (run_dir / "soul.running").write_text("pid=1\nstarted_at=1792288735.173\n", encoding="utf-8")
    fake_venv = fake_root / ".venv" / "Scripts"
    fake_venv.mkdir(parents=True)
    (fake_venv / "python.exe").write_bytes(b"")  # 无效可执行文件：Start-Process 必失败

    result = _run_soul_ps(fake_root, "start", 1)

    out = result.stdout + result.stderr
    archived = sorted((fake_root / "data" / "logs").glob("soul.err.*.log"))
    assert archived, f"旧 stderr 必须被归档；实际输出：{out}"
    assert archived[0].read_text(encoding="utf-8").startswith("上一次死亡的 stderr 证据")
    assert "1792288735" not in archived[0].name  # 文件名是分钟级时间戳，不是原始 epoch
