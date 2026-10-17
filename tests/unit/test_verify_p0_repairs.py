"""`scripts/verify_p0_repairs.py`（P0 只读验收）的单测。

这个脚本是**验收工具**，它自己必须可信，所以本文件钉住四件事：
1. 判定逻辑：全绿 / FAIL / WARN / PENDING 各自的退出码（0/1/0/0）；
2. **只读承诺**：跑一遍后 `data/` 下任何文件的 mtime/大小/内容都不得变化、不得新增文件；
3. 假阴性护栏：缺 watchdog.log、会话标记残留、老档案缺停机字段都能被分别识别；
4. `--json` 可被脚本消费。

全部用 `--data-dir` 指向 `tmp_path` 的合成目录，**不碰真实的她**。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY = REPO_ROOT / "scripts" / "verify_p0_repairs.py"
SOUL_PS1 = REPO_ROOT / "scripts" / "soul.ps1"


# ── 合成数据目录 ───────────────────────────────────────────────────
def _kv_db(path: Path, kv: dict[str, str]) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    for key, value in kv.items():
        con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
    con.commit()
    con.close()


def _hb_db(path: Path, ts: float) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE IF NOT EXISTS heartbeats ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
        "beat_type TEXT NOT NULL, payload TEXT NOT NULL)"
    )
    con.execute("INSERT INTO heartbeats (ts, beat_type, payload) VALUES (?, 'soul', '{}')", (ts,))
    con.commit()
    con.close()


def _write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="ascii")


def _dead_pid() -> int:
    """拿一个**确定已退出**的 pid（避免 pid 复用误判）。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.01)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=30)
    return proc.pid


def make_root(
    tmp_path: Path,
    *,
    running: bool = True,
    watchdog_age_s: float = 60.0,
    downtime_fields: bool = True,
    stop_request: bool = False,
    intentional: bool = False,
    graceful_evidence: bool = True,
    checkpoints: bool = True,
    marker_pid: int | None = None,
) -> Path:
    data = tmp_path / "data"
    (data / "run").mkdir(parents=True)
    (data / "logs").mkdir()
    now = time.time()

    payload: dict[str, object] = {
        "first_existence_ts": now - 86400,
        "last_soul_beat_ts": now,
        "last_body_online_ts": 0.0,
        "last_interaction_ts": now,
        "body_away_start_ts": None,
    }
    if downtime_fields:
        payload.update({"last_seen_ts": now, "downtime_total_s": 0.0, "last_downtime_s": 0.0})
    _kv_db(data / "state.db", {"timesense": json.dumps(payload)})
    _hb_db(data / "heartbeat.db", now)

    if running:
        _write_pid(data / "run" / "soul.pid", os.getpid())
        pid_text = marker_pid if marker_pid is not None else os.getpid()
        (data / "run" / "soul.running").write_text(
            f"pid={pid_text}\nstarted_at={now:.3f}\n", encoding="utf-8"
        )
    if checkpoints:
        snapshot = data / "checkpoints" / "p0-test"
        snapshot.mkdir(parents=True)
        _kv_db(snapshot / "state.db", {})
        _hb_db(snapshot / "heartbeat.db", now)

    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - watchdog_age_s))
    (data / "logs" / "watchdog.log").write_text(
        f"[{stamp}] run-ok | alive=True | beat_age=1.0s | verdict=healthy | action=none\n",
        encoding="utf-8",
    )
    if graceful_evidence:
        (data / "logs" / "elysia.log").write_text(
            '{"event": "soul stopping：优雅关闭"}\n{"event": "soul stopped：档案已封存"}\n',
            encoding="utf-8",
        )
    if stop_request:
        (data / "run" / "stop_requested").write_text("requested_at=x\n", encoding="utf-8")
    if intentional:
        (data / "run" / "stopped_intentionally").write_text("by=soul.ps1 stop\n", encoding="utf-8")
    return data


def _run(
    data_dir: Path, *extra: str, soul_ps1: Path | None = None
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(VERIFY),
        "--data-dir",
        str(data_dir),
        "--soul-ps1",
        str(soul_ps1 if soul_ps1 is not None else SOUL_PS1),
        *extra,
    ]
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=str(REPO_ROOT),
    )


# ── 判定与退出码 ───────────────────────────────────────────────────
def test_all_green_exit_0(tmp_path: Path) -> None:
    result = _run(make_root(tmp_path))
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "[FAIL]" not in out
    assert "[PASS]" in out
    assert "退出码：0" in out


def test_missing_watchdog_log_is_fail_exit_1(tmp_path: Path) -> None:
    data = make_root(tmp_path)
    (data / "logs" / "watchdog.log").unlink()

    result = _run(data)

    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "[FAIL]" in out
    assert "看门狗从未留下运行痕迹" in out


def test_stale_watchdog_log_is_warn_exit_0(tmp_path: Path) -> None:
    result = _run(make_root(tmp_path, watchdog_age_s=3600.0))
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "[WARN]" in out
    assert "看门狗可能没在跑" in out


def test_stop_request_residual_is_warn_exit_0(tmp_path: Path) -> None:
    result = _run(make_root(tmp_path, stop_request=True))
    out = result.stdout + result.stderr
    assert result.returncode == 0, f"WARN 不该让退出码变 1（脚本化要求）：\n{out}"
    assert "[WARN]" in out
    assert "stop_requested 残留" in out


def test_not_running_is_pending_not_fail(tmp_path: Path) -> None:
    result = _run(make_root(tmp_path, running=False))
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "[PENDING]" in out
    assert "她没在运行" in out


def test_old_archive_missing_downtime_fields_is_pending(tmp_path: Path) -> None:
    result = _run(make_root(tmp_path, downtime_fields=False))
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "老档案缺字段" in out


def test_stale_session_marker_is_fail(tmp_path: Path) -> None:
    """会话标记里的 pid 已退出、而当前进程在跑 ⇒ 上次没走正常关闭（这是 FAIL）。"""
    result = _run(make_root(tmp_path, marker_pid=_dead_pid()))
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "[FAIL]" in out
    assert "上次会话" in out and "残留" in out


def test_missing_soul_ps1_is_fail(tmp_path: Path) -> None:
    result = _run(make_root(tmp_path), soul_ps1=tmp_path / "no-such-soul.ps1")
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "找不到" in out


# ── 只读承诺（本脚本自己不能被它自己的"验收"污染）────────────────────
def _fingerprint(root: Path) -> dict[str, tuple[int, int, str]]:
    state: dict[str, tuple[int, int, str]] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        state[str(path.relative_to(root))] = (stat.st_mtime_ns, stat.st_size, digest)
    return state


def test_script_writes_nothing(tmp_path: Path) -> None:
    """跑一遍只读验收后：文件清单 / mtime / 大小 / 内容都不得变化，也不得新增文件。"""
    data = make_root(tmp_path, stop_request=True)  # 连 WARN 分支一起走
    before = _fingerprint(data)

    result = _run(data)
    assert result.returncode == 0, result.stdout + result.stderr

    after = _fingerprint(data)
    assert after == before, "只读验收脚本改动了 data/ 下的文件（禁止：不写标记、不写日志）"


# ── 机读输出 ───────────────────────────────────────────────────────
def test_json_output_is_consumable(tmp_path: Path) -> None:
    result = _run(make_root(tmp_path, stop_request=True), "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["exit_code"] == 0
    assert payload["counts"]["FAIL"] == 0
    assert payload["counts"]["WARN"] >= 1
    tasks = {item["task"] for item in payload["checks"]}
    assert {"R1", "R2", "R3", "R4/R5", "R6", "R8"} <= tasks


def test_bad_data_dir_exit_2(tmp_path: Path) -> None:
    result = _run(tmp_path / "no-such-dir")
    assert result.returncode == 2
    assert "数据目录不存在" in (result.stdout + result.stderr)
