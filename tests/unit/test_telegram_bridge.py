"""P0-R20 Telegram 双向桥单测 —— 收得进、发得出、不该发的绝不发。

守护四件事（每件都有"去掉修复该用例必须红"的取消测试，见各用例 docstring）：

1. **白名单**：非白名单 chat_id 的消息**不写她的库、不回复**（`test_whitelist_blocks_...`）。
   ⇒ 去掉白名单校验，该用例必红（kv 被写）。
2. **BOM**：`telegram_token.txt` 带 UTF-8 BOM 仍能正确读出 token
   （`test_bom_token_...`）。⇒ 把 `utf-8-sig` 退回 `utf-8`，token 带 `\\ufeff` 形状不对，
   该用例必红（本工程刚因 BOM 让 `push_url` 静默失效）。
3. **失败静默**：Telegram 不可达时收发都不冒异常（`test_telegram_unreachable_...`、
   `test_send_failure_is_silent_...`）。⇒ 去掉兜底，`URLError` 会冒出来，该用例必红。
4. **不补推历史**：首次启动时 `thought_log` 已有 100 条气泡 ⇒ 一条都不发
   （`test_first_poll_does_not_backfill_...`）。⇒ 去掉首次建游标，该用例必红（发 100 条）。

以及离线全链路（假 HTTP 层，不联网）：收到消息 → 写她的耳朵 → 她回两句 → 两条气泡发回。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
PS1 = SCRIPTS / "register_telegram_task.ps1"
TOKEN = "123456789:AAFakeTokenForTests_abcdefghijklmnopqrst"


def _load(name: str, filename: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bridge = _load("telegram_bridge_probe", SCRIPTS / "telegram_bridge.py")


# ── 假 HTTP 层（离线；记录 method/payload，可注入失败）─────────────────
class FakeTelegram:
    """替换 `bridge._open_telegram` —— 不联网，跑的是**真实的** TelegramAPI 解析/构造。"""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.fail_all = False
        self.fail_send = False

    def __call__(self, req: Any, timeout_s: float) -> bytes:
        if self.fail_all:
            raise urllib.error.URLError("telegram unreachable")
        method = str(req.full_url).rsplit("/", 1)[-1]
        payload = json.loads(req.data.decode("utf-8")) if req.data else {}
        self.calls.append(method)
        if method == "getUpdates":
            result, self.updates = self.updates, []
            return json.dumps({"ok": True, "result": result}).encode("utf-8")
        if method == "sendMessage":
            if self.fail_send:
                raise urllib.error.URLError("send unreachable")
            self.sent.append(dict(payload))
            return json.dumps({"ok": True, "result": {"message_id": len(self.sent)}}).encode(
                "utf-8"
            )
        return json.dumps({"ok": True, "result": True}).encode("utf-8")


# ── 临时库/配置 ─────────────────────────────────────────────────────
def _state_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.commit()
    finally:
        con.close()
    return path


def _heartbeat_db(path: Path, bubbles: list[str] | None = None, ts: float = 1000.0) -> Path:
    con = sqlite3.connect(path)
    try:
        con.executescript(
            "CREATE TABLE IF NOT EXISTS thought_log ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
            " kind TEXT NOT NULL, text TEXT NOT NULL);"
        )
        for i, text in enumerate(bubbles or []):
            con.execute(
                "INSERT INTO thought_log (ts, kind, text) VALUES (?, 'expression', ?)",
                (ts + i, text),
            )
        con.commit()
    finally:
        con.close()
    return path


def _chat_file(tmp_path: Path, chat_id: int | None) -> Path:
    p = tmp_path / "run" / "telegram_chat.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    if chat_id is not None:
        p.write_text(f"{chat_id}\n", encoding="utf-8")
    return p


def _make_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    chat_id: int | None = 111,
    bubbles: list[str] | None = None,
    ts: float = 1000.0,
) -> tuple[Any, FakeTelegram, Path, Path]:
    fake = FakeTelegram()
    monkeypatch.setattr(bridge, "_open_telegram", fake)
    state = _state_db(tmp_path / "state.db")
    hb = _heartbeat_db(tmp_path / "heartbeat.db", bubbles, ts=ts)
    chat = _chat_file(tmp_path, chat_id)
    b = bridge.TelegramBridge(
        TOKEN,
        api=bridge.TelegramAPI(TOKEN),
        state_db=state,
        heartbeat_db=hb,
        chat_file=chat,
        pid_file=tmp_path / "run" / "telegram_bridge.pid",
    )
    return b, fake, state, hb


def _update(chat_id: int, text: str, update_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "chat": {"id": chat_id}, "text": text},
    }


def _kv_interaction(db: Path) -> dict[str, Any] | None:
    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT value FROM kv WHERE key = 'interaction'").fetchone()
    finally:
        con.close()
    return json.loads(row[0]) if row else None


def _add_bubble(hb: Path, text: str, *, ts: float | None = None) -> int:
    con = sqlite3.connect(hb)
    try:
        cur = con.execute(
            "INSERT INTO thought_log (ts, kind, text) VALUES (?, 'expression', ?)",
            (time.time() if ts is None else ts, text),
        )
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


# ── 1. 白名单（取消测试）───────────────────────────────────────────
def test_whitelist_blocks_non_owner_and_never_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非白名单 chat_id ⇒ **不写她的库、不回复**，只记日志。

    **取消测试**：去掉 `_handle_update` 里的白名单分支 ⇒ 该消息会被写进
    `kv['interaction']`，本用例必红（她会被陌生人刺激开口）。
    """
    b, fake, state, _ = _make_bridge(tmp_path, monkeypatch, chat_id=111)
    assert b.handle_update(_update(999, "你好，在吗")) == "ignored"
    assert _kv_interaction(state) is None, "非白名单的消息被写进了她的库"
    assert fake.sent == [], "对非白名单用户回复了（等于告诉对方机器人存在）"


def test_first_message_binds_and_is_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有白名单文件时：首条消息绑定该 chat，回一条"已绑定"，并且**这条也要转达给她**。"""
    b, fake, state, _ = _make_bridge(tmp_path, monkeypatch, chat_id=None)
    assert b.handle_update(_update(4242, "在吗")) == "delivered"
    assert b.chat_id == 4242
    bound_text = b._chat_file.read_text(encoding="utf-8")
    assert bound_text.strip() == "4242"
    assert not bound_text.encode("utf-8").startswith(b"\xef\xbb\xbf")  # 绑定文件不写 BOM
    assert any("已绑定" in str(m.get("text", "")) for m in fake.sent)
    assert _kv_interaction(state)["text"] == "在吗"


# ── 2. BOM（取消测试）──────────────────────────────────────────────
def test_bom_token_is_read_correctly(tmp_path: Path) -> None:
    """token 文件带 UTF-8 BOM（PowerShell 常见）仍必须读出**干净 token**。

    **取消测试**：把 `load_token` 的 `utf-8-sig` 退回 `utf-8` ⇒ token 变
    `'\\ufeff123456789:...'`，形状校验失败/拒绝启动，本用例必红。
    """
    p = tmp_path / "telegram_token.txt"
    p.write_bytes(b"\xef\xbb\xbf" + TOKEN.encode("utf-8") + b"\r\n")
    assert bridge.load_token(p) == TOKEN

    # 带 BOM + 前后空白/空行，也要稳
    p.write_bytes(b"\xef\xbb\xbf\r\n  " + TOKEN.encode("utf-8") + b"  \r\n")
    assert bridge.load_token(p) == TOKEN


def test_token_missing_empty_or_malformed_refuses_to_start(tmp_path: Path) -> None:
    """取不到/空/形状不对 ⇒ **明确拒绝启动**（BridgeConfigError），不半残运行。"""
    with pytest.raises(bridge.BridgeConfigError):
        bridge.load_token(tmp_path / "missing.txt")

    empty = tmp_path / "empty.txt"
    empty.write_text("\n   \n", encoding="utf-8")
    with pytest.raises(bridge.BridgeConfigError):
        bridge.load_token(empty)

    bad = tmp_path / "bad.txt"
    bad.write_text("not-a-bot-token\n", encoding="utf-8")
    with pytest.raises(bridge.BridgeConfigError):
        bridge.load_token(bad)


def test_token_never_logged_in_full() -> None:
    """日志只露前 8 位；完整 token 绝不出现（它等于机器人控制权）。"""
    masked = bridge.mask_token(TOKEN)
    assert masked == TOKEN[:8] + "…"
    assert TOKEN not in masked


# ── 3. 失败静默（取消测试）─────────────────────────────────────────
def test_telegram_unreachable_does_not_break_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telegram 不可达时：绑定回执发不出去也不抛异常，且**消息仍写进她的库**。

    **取消测试**：去掉 `_safe_send`/`run_once` 的兜底 ⇒ `URLError` 会冒到调用方，本用例必红。
    """
    b, fake, state, _ = _make_bridge(tmp_path, monkeypatch, chat_id=None)
    fake.fail_all = True
    assert b.handle_update(_update(4242, "在吗")) == "delivered"
    assert _kv_interaction(state)["text"] == "在吗"
    assert b.run_once() == {"received": 0, "sent": 0}  # 不抛异常、桥还活着
    assert b.last_send_error is not None


def test_send_failure_is_silent_then_retries_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """发送失败 ⇒ `poll_expression_once()` 不抛异常；失败的气泡留在内存，下次重试不丢。

    **取消测试**：去掉发送处的 try/except ⇒ `URLError` 会从 `poll_expression_once()`
    冒出来，本用例必红。
    """
    b, fake, _state, hb = _make_bridge(tmp_path, monkeypatch, chat_id=111)
    b.initialize_cursor()
    _add_bubble(hb, "我在呢～")
    fake.fail_send = True
    assert b.poll_expression_once() == 0  # **不抛异常**
    assert b.last_send_error is not None
    assert fake.sent == []

    fake.fail_send = False
    assert b.poll_expression_once() == 1  # 上一条没丢，重试成功
    assert [m["text"] for m in fake.sent] == ["我在呢～"]


# ── 4. 不补推历史（取消测试）───────────────────────────────────────
def test_first_poll_does_not_backfill_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次启动时库里已有 100 条气泡 ⇒ **一条都不发**（重启不该刷屏）。

    **取消测试**：去掉 `initialize_cursor()` 的首次建基线 ⇒ 100 条会被全推出去，用例必红。
    """
    b, fake, _state, hb = _make_bridge(
        tmp_path, monkeypatch, chat_id=111, bubbles=[f"旧气泡{i}" for i in range(100)]
    )
    assert b.poll_expression_once() == 0
    assert fake.sent == []
    # 之后的新气泡照常发
    _add_bubble(hb, "新的一句话")
    assert b.poll_expression_once() == 1
    assert [m["text"] for m in fake.sent] == ["新的一句话"]


# ── 发：多句分气泡 ─────────────────────────────────────────────────
def test_multi_sentence_reply_goes_as_separate_bubbles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R19 的 1–3 句回复 ⇒ 每条一个 `sendMessage`（一个气泡），不合并成一段。"""
    b, fake, _state, hb = _make_bridge(tmp_path, monkeypatch, chat_id=111)
    b.initialize_cursor()
    _add_bubble(hb, "嗯。")
    _add_bubble(hb, "我在呢～")
    assert b.poll_expression_once() == 2
    assert [m["text"] for m in fake.sent] == ["嗯。", "我在呢～"]
    assert all(m["chat_id"] == 111 for m in fake.sent)


def test_legacy_multi_sentence_row_is_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """兼容 R19 之前的旧数据：一行装多句 ⇒ 按句边界拆成多个气泡。"""
    b, fake, _state, hb = _make_bridge(tmp_path, monkeypatch, chat_id=111)
    b.initialize_cursor()
    _add_bubble(hb, "第一句。第二句！第三句？")
    assert b.poll_expression_once() == 3
    assert [m["text"] for m in fake.sent] == ["第一句。", "第二句！", "第三句？"]


# ── 收：指令与校验 ─────────────────────────────────────────────────
def test_status_reports_her_state_and_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/status 回她的简况（醒着/心跳/年龄/今天几句），且**不写她的库**（这不是一句话）。"""
    b, fake, state, _hb = _make_bridge(
        tmp_path, monkeypatch, chat_id=111, bubbles=["今天第一句。", "今天第二句。"], ts=time.time()
    )
    monkeypatch.setattr(
        bridge,
        "take_snapshot",
        lambda **kw: SimpleNamespace(
            soul_alive=True, heartbeat_age_s=3.2, age_days=1.5, mode="present"
        ),
    )
    assert b.handle_update(_update(111, "/status")) == "status"
    text = str(fake.sent[0]["text"])
    assert "醒着" in text
    assert "3 秒前" in text
    assert "1.50 天" in text
    assert "今天开口：2 句" in text
    assert _kv_interaction(state) is None


def test_unknown_command_is_not_forwarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未知 /命令 只回提示，**不写她的库**（避免把垃圾指令塞进她的记忆）。"""
    b, fake, state, _ = _make_bridge(tmp_path, monkeypatch, chat_id=111)
    assert b.handle_update(_update(111, "/foo")) == "unknown_command"
    assert _kv_interaction(state) is None
    assert "只支持" in str(fake.sent[0]["text"])


def test_overlong_message_is_rejected_not_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超过 500 字 ⇒ 明确拒绝并告知，**不静默截断**（她读到半句比没读到更糟）。"""
    b, fake, state, _ = _make_bridge(tmp_path, monkeypatch, chat_id=111)
    assert b.handle_update(_update(111, "字" * 501)) == "rejected"
    assert _kv_interaction(state) is None
    assert "500" in str(fake.sent[0]["text"])


# ── 离线全链路（验收要求：收 → 写库 → 她回 → 发回）──────────────────
def test_offline_full_chain_receive_then_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """假 HTTP 层下跑通完整链路：收到消息 → 写进她的耳朵 → 她回两句 → 两条气泡发回手机。"""
    b, fake, state, hb = _make_bridge(tmp_path, monkeypatch, chat_id=111)
    b.initialize_cursor()

    # ① 收：你的消息进她的库
    fake.updates = [_update(111, "今天过得怎么样")]
    first = b.run_once()
    assert first["received"] == 1
    assert _kv_interaction(state)["text"] == "今天过得怎么样"
    assert "getUpdates" in fake.calls

    # ② 她回：R19 把两句分别落 thought_log（真机就是这个形状）
    _add_bubble(hb, "嗯。")
    _add_bubble(hb, "我在呢～")

    # ③ 发：两条气泡分别到手机
    second = b.run_once()
    assert second["sent"] == 2
    assert [m["text"] for m in fake.sent] == ["嗯。", "我在呢～"]
    assert all(m["chat_id"] == 111 for m in fake.sent)


def test_backoff_doubles_and_caps_at_60() -> None:
    """网络失败指数退避：1→2→4→8→16→32→60→60（封顶 60 秒，不退出）。"""
    value = 1.0
    seq = []
    for _ in range(8):
        seq.append(value)
        value = bridge.next_backoff(value)
    assert seq == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


# ── 计划任务脚本（建但不注册 + UTF-8 BOM）───────────────────────────
def test_register_task_ps1_is_utf8_bom_and_parses() -> None:
    """新建的 `.ps1` 必须 **UTF-8 带 BOM**（本工程踩过 3 次 ANSI 读中文撑破语法），且语法可解析。"""
    raw = PS1.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "register_telegram_task.ps1 缺少 UTF-8 BOM"
    text = raw.decode("utf-8-sig")
    assert "-Register" in text and "-Unregister" in text
    assert "telegram_bridge.py" in text

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("本机没有 PowerShell，跳过语法解析")
    script = (
        "$errors = $null; "
        "[System.Management.Automation.PSParser]::Tokenize("
        f"(Get-Content -Raw -LiteralPath '{PS1}'), [ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Output $_.Message }; exit 1 }"
    )
    proc = subprocess.run(
        [powershell, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=60,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ── 命令行入口（cp936 下不许崩）────────────────────────────────────
def test_cli_help_and_missing_token_do_not_crash(tmp_path: Path) -> None:
    """真机踩过：`argparse --help` 把含 `↔` 的 description 写 cp936 stdout ⇒
    `UnicodeEncodeError` + traceback。修复后 `--help` 必须 exit 0；缺 token 必须
    exit 2（明确拒绝启动），都不是 traceback。"""
    script = str(SCRIPTS / "telegram_bridge.py")
    helped = subprocess.run(
        [sys.executable, script, "--help"], capture_output=True, text=True, timeout=60
    )
    assert helped.returncode == 0, helped.stderr
    assert "usage" in helped.stdout.lower()

    missing = subprocess.run(
        [sys.executable, script, "--once", "--token-file", str(tmp_path / "nope.txt")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert missing.returncode == 2
    assert "Traceback" not in (missing.stdout + missing.stderr)
