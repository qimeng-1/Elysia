"""通道（P0-R18 步 1-3）单测：**说话真的到了她的耳朵** / 非回环必须 401 / 推送失败不拖她下水。

本文件守护三件事（每件都有"去掉修复该用例必须红"的取消测试，见各用例 docstring）：

1. **步 2 你能说话**（`test_say_reaches_expression_log_within_10s`）
   面板 `POST /api/say` 的写入路径 + **她的真实心跳耳朵**（`SoulHeartbeat` 读
   `kv['interaction']` → 强制开口 → `expression_log`）端到端跑通。
   取消测试证明：把 `say()` 里那行 `write_interaction(...)` 去掉，本用例必红
   （她的耳朵收不到任何东西，`expression_log` 不会出现新行）。
   全程用 `tmp_path` 的临时库 + 模拟时钟，**不碰她的真实库、不启停她**。

2. **步 1 手机能看且不被裸奔**（`test_non_loopback_requires_token_...`）
   绑非回环 ⇒ 强制 token；不带 token 连首页都 401，且 401 里**不含任何状态**。
   取消测试证明：去掉 `_auth_ok()` 里的校验，本用例必红（200 + 状态泄露）。

3. **步 3 她开口时推送**（`test_push_unreachable_degrades_silently_...`）
   webhook 不可达时 `poll_once()` **绝不抛异常**，她的表达仍完好、库仍可读。
   取消测试证明：去掉 `_send()`/`poll_once()` 的兜底，本用例必红（异常直接冒出来）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import random
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from elysia.core.clock import SimulatedClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import TimeSense
from elysia.soul.brain import BrainLoop
from elysia.soul.desire import DesireSystem
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import STATUS_SUCCESS, ExpressionJob, ExpressionResult
from elysia.soul.expression_worker import ExpressionWorker
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
T0 = 1_000_000.0

_EXPRESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS expression_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    intent      TEXT NOT NULL,
    instruction TEXT NOT NULL,
    llm_text    TEXT NOT NULL,
    validation  TEXT NOT NULL,
    level       TEXT NOT NULL
);
"""


def _load(name: str, filename: Path) -> Any:
    """按路径加载 `scripts/*.py`（脚本不是包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location(name, filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


panel = _load("elysia_panel_probe", SCRIPTS / "elysia_panel.py")
pusher = _load("push_notifier_probe", SCRIPTS / "push_notifier.py")


# ── 临时库构造 ───────────────────────────────────────────────────────
def _state_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.commit()
    finally:
        con.close()
    return path


def _heartbeat_db(path: Path, texts: list[str] | None = None) -> Path:
    con = sqlite3.connect(path)
    try:
        con.executescript(_EXPRESSION_SCHEMA)
        for i, text in enumerate(texts or []):
            con.execute(
                "INSERT INTO expression_log (ts, intent, instruction, llm_text, validation, level)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (1000.0 + i, "回应", "{}", text, "{}", "micro"),
            )
        con.commit()
    finally:
        con.close()
    return path


def _read_kv_interaction(db: Path) -> dict[str, Any]:
    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT value FROM kv WHERE key = 'interaction'").fetchone()
    finally:
        con.close()
    assert row is not None, "kv['interaction'] 没有被写入 —— 她永远读不到这句话"
    return json.loads(row[0])


def _serve(srv: Any) -> tuple[threading.Thread, int]:
    port = int(srv.server_address[1])
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return thread, port


def _fake_snapshot() -> Any:
    """隔离真实机器：状态快照不碰她的真库/计划任务。"""
    return panel.Snapshot()


# ── 步 2：说话真的到达她的耳朵（本卡核心取消测试）────────────────────
async def _make_soul_with_ear(
    tmp_path: Path,
) -> tuple[SoulHeartbeat, StateStore, HeartbeatStore]:
    """造一个**带真实耳朵**的心跳：真读 kv、真写 expression_log，只把 LLM 换成假 handler。"""
    clock = SimulatedClock(start_ts=T0, speed=60.0)
    state_store = StateStore(tmp_path / "state.db")
    heartbeat_store = HeartbeatStore(tmp_path / "heartbeat.db")
    # 只用到 should_speak / persist（不走 run_job），故 LLM 传 None 即可
    expression = ExpressionService(None, heartbeat_store)  # type: ignore[arg-type]
    # 把"内部冲动"的节流基线压到**测试窗口之外**：本用例只验证"被你说话强制开口"这一条路径。
    # （模拟时钟 speed=60：10 秒真实等待 = 600 虚拟秒，正好够触发一次内部冲动 ——
    #   若不清掉，取消测试会因"她自言自语"而假绿，看不出是 interaction 起的作用。）
    expression._last_express_ts = T0 + 10_000.0

    async def handler(job: ExpressionJob) -> ExpressionResult:
        return ExpressionResult(
            job_id=job.job_id,
            status=STATUS_SUCCESS,
            order_key=job.order_key,
            text="我在呢",
            level="micro",
            intent=job.intent or "回应",
        )

    soul = SoulHeartbeat(
        state_store=state_store,
        heartbeat_store=heartbeat_store,
        timesense=TimeSense(make_soul_state(T0), clock),
        mode_mgr=ModeManager(clock=clock),
        brain_loop=BrainLoop(DesireSystem(), rng=random.Random(0)),
        expression=expression,
        expression_worker=ExpressionWorker(handler),
        clock=clock,
        interval_s=1.0,
    )
    return soul, state_store, heartbeat_store


@pytest.mark.asyncio
async def test_say_reaches_expression_log_within_10s(tmp_path: Path) -> None:
    """面板写一句话 ⇒ 她的心跳读到 ⇒ 10 秒内 `expression_log` 出现新表达。

    **取消测试**：把 `say()` 里的 `write_interaction(...)` 去掉（其余不变），
    本用例必须红 —— 她的耳朵收不到任何新 `ts`，不会强制开口，`responded` 为 False。
    """
    soul, state_store, heartbeat_store = await _make_soul_with_ear(tmp_path)
    await state_store.start()
    await heartbeat_store.start()
    task = asyncio.create_task(soul.run())
    try:
        result = await asyncio.to_thread(
            panel.say,
            tmp_path / "state.db",
            "今天过得怎么样",
            heartbeat_db=tmp_path / "heartbeat.db",
            wait_s=10.0,
            poll_s=0.05,
        )
        rows = await heartbeat_store.execute_raw(
            "SELECT llm_text, level FROM expression_log ORDER BY id DESC LIMIT 1"
        )
    finally:
        await soul.stop()
        await asyncio.wait_for(task, timeout=10)
        await state_store.close()
        await heartbeat_store.close()

    assert result["ok"] is True, result
    assert result["wrote"] is True, result
    assert result["responded"] is True, f"她没在 10 秒内开口：{result.get('detail')}；{result}"
    reply = result["reply"]
    assert isinstance(reply, dict)
    assert reply["text"] == "我在呢"
    assert rows and rows[0][0] == "我在呢"  # 库里确实多了一行（不是接口自说自话）
    # 她读到的是我们写的那句话（同一行 kv）
    assert _read_kv_interaction(tmp_path / "state.db")["text"] == "今天过得怎么样"


@pytest.mark.asyncio
async def test_say_without_write_never_reaches_her(tmp_path: Path) -> None:
    """反向对照（同一装置）：**没写 kv** 时她不会凭空开口。

    这条让上一条的"绿"有意义：不是后台有什么东西在自动产生 expression_log，
    而是"写了 interaction 才有回应"。
    """
    soul, state_store, heartbeat_store = await _make_soul_with_ear(tmp_path)
    await state_store.start()
    await heartbeat_store.start()
    task = asyncio.create_task(soul.run())
    try:
        # 只跑心跳，不写 interaction：她照常生活，但不应被强制开口
        await asyncio.sleep(1.0)
        rows = await heartbeat_store.execute_raw("SELECT COUNT(*) FROM expression_log")
    finally:
        await soul.stop()
        await asyncio.wait_for(task, timeout=10)
        await state_store.close()
        await heartbeat_store.close()
    assert rows[0][0] == 0, "没写 interaction 她却开口了 —— 用例的前提不成立"


def test_say_writes_interaction_with_fresh_ts(tmp_path: Path) -> None:
    """`kv['interaction']` 必须是 `{"ts": 现在, "text": 原文}`（她按 ts 判新）。"""
    db = _state_db(tmp_path / "state.db")
    _heartbeat_db(tmp_path / "heartbeat.db")
    before = time.time()
    result = panel.say(db, "  今天过得怎么样  ", wait_s=0)
    assert result["ok"] is True and result["wrote"] is True
    assert result["responded"] is False  # wait_s=0：不等待，如实说没等
    payload = _read_kv_interaction(db)
    assert payload["text"] == "今天过得怎么样"  # 首尾空白被去掉
    assert before - 1 <= float(payload["ts"]) <= time.time() + 1


def test_say_write_failure_is_loud_and_never_fakes_success(tmp_path: Path) -> None:
    """库不存在 ⇒ 必须明确失败，且**不许新建空库**冒充成功（今天刚踩过静默成功的坑）。"""
    missing = tmp_path / "state.db"
    result = panel.say(missing, "你在吗", wait_s=0)
    assert result["ok"] is False
    assert result["wrote"] is False
    assert "写入失败" in str(result["title"])
    assert "不存在" in str(result["detail"])
    assert not missing.exists(), "写入失败却创建了一个空库 —— 等于伪造她的状态库"


def test_say_write_to_non_state_db_is_loud(tmp_path: Path) -> None:
    """给了一个"不是她的状态库"的 sqlite 文件 ⇒ 必须明确说没有 kv 表，而不是静默成功。"""
    bogus = tmp_path / "state.db"
    sqlite3.connect(bogus).close()
    result = panel.say(bogus, "你好", wait_s=0)
    assert result["ok"] is False and result["wrote"] is False
    assert "kv" in str(result["detail"])


def test_sanitize_rejects_empty_and_too_long_and_strips_controls() -> None:
    text, err = panel.sanitize_say_text(" \t\r\n ")
    assert text is None and err is not None and "空" in err

    text, err = panel.sanitize_say_text("字" * (panel.SAY_MAX_CHARS + 1))
    assert text is None and err is not None and "上限" in err

    assert panel.sanitize_say_text(12345)[0] is None  # 非字符串

    text, err = panel.sanitize_say_text("\x00控\x1b制\r\n字符\x07")
    assert err is None
    assert text == "控制\n字符"
    assert all(ch == "\n" or ch.isprintable() for ch in text)  # 无控制字符残留


# ── 步 1：手机能看，但绝不裸奔 ───────────────────────────────────────
def test_non_loopback_requires_token_and_401_leaks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """绑非回环 ⇒ 强制 token；缺 token/错 token ⇒ 401，且响应里**没有任何状态**。

    **取消测试**：去掉 `Handler._auth_ok()` 里的校验（或让 `AuthGate.check` 恒 True），
    本用例必红 —— 无 token 的 `/api/status` 会 200 并把状态吐出来。
    """
    token_file = tmp_path / "panel_token.txt"
    token = panel.resolve_auth("0.0.0.0", path=token_file)
    assert token is not None and len(token) >= 32
    assert token_file.read_text(encoding="utf-8").strip() == token  # 首次生成并落盘
    # 默认行为不变：绑回环且没显式给 token ⇒ 不启用鉴权
    assert panel.DEFAULT_HOST == "127.0.0.1"
    assert panel.resolve_auth("127.0.0.1", path=token_file) is None

    monkeypatch.setattr(panel, "take_snapshot", lambda **kw: _fake_snapshot())
    srv = panel.make_server(
        "127.0.0.1",
        0,
        auth_token=token,
        state_db=_state_db(tmp_path / "state.db"),
        heartbeat_db=_heartbeat_db(tmp_path / "heartbeat.db"),
    )
    thread, port = _serve(srv)
    try:
        with pytest.raises(urllib.error.HTTPError) as no_token:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=10)
        assert no_token.value.code == 401
        leaked = no_token.value.read().decode("utf-8", "replace")
        for secret in ("soul_alive", "age_days", "heartbeat", "Elysia"):
            assert secret not in leaked, f"401 响应泄露了状态信息：{leaked!r}"

        with pytest.raises(urllib.error.HTTPError) as wrong_token:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html?token=wrong", timeout=10)
        assert wrong_token.value.code == 401

        # 首页同样要鉴权：手机拿到的是"什么都没有"，不是她的状态页
        with pytest.raises(urllib.error.HTTPError) as home:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10)
        assert home.value.code == 401

        ok = json.loads(
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status?token={token}", timeout=10
            ).read()
        )
        assert "soul_alive" in ok

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert "soul_alive" in json.loads(urllib.request.urlopen(req, timeout=10).read())
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def test_auth_rate_limit_blocks_bruteforce() -> None:
    """连续猜错到阈值 ⇒ 封禁窗口内连**正确** token 也拒绝（防暴力猜 32 字节 hex）。"""
    gate = panel.AuthGate("correct-token", max_failures=3, window_s=60.0)
    for i in range(3):
        assert gate.check("guess", "1.2.3.4", now=100.0 + i) is False
    assert gate.is_blocked("1.2.3.4", now=105.0) is True
    assert gate.check("correct-token", "1.2.3.4", now=105.0) is False
    # 窗口过期后放行；别的客户端不受影响
    assert gate.check("correct-token", "1.2.3.4", now=200.0) is True
    assert gate.check("correct-token", "5.6.7.8", now=200.0) is True


def test_http_say_without_reply_is_legal_state_not_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """她**选择不开口** = 合法状态：HTTP 200、`ok:true`、`responded:false`（不是报错）。

    未来"她可以拒绝回复"依赖这条语义：没回应 ≠ 写入失败。
    """
    monkeypatch.setattr(panel, "SAY_WAIT_S", 0.0)
    monkeypatch.setattr(panel, "take_snapshot", lambda **kw: _fake_snapshot())
    state = _state_db(tmp_path / "state.db")
    hb = _heartbeat_db(tmp_path / "heartbeat.db")
    srv = panel.make_server("127.0.0.1", 0, state_db=state, heartbeat_db=hb)
    thread, port = _serve(srv)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/say",
            data=json.dumps({"text": "你在吗"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            body = json.loads(resp.read())
        assert body["ok"] is True and body["wrote"] is True
        assert body["responded"] is False
        assert _read_kv_interaction(state)["text"] == "你在吗"

        # 空消息是**输入错误**（400），与"她没开口"必须区分开
        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/say",
            data=json.dumps({"text": "   "}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(bad, timeout=10)
        assert exc.value.code == 400
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


# ── 步 3：她开口时推送 ───────────────────────────────────────────────
class _WebhookReceiver:
    """本地假 webhook：记录收到的请求体（不联网、不花钱）。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bytes]] = []
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                n = int(self.headers.get("Content-Length", "0") or 0)
                outer.requests.append(
                    (self.path, self.headers.get("Content-Type", ""), self.rfile.read(n))
                )
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, fmt: str, *args: object) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = int(self.srv.server_address[1])
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/hook"

    def stop(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)


def test_push_posts_her_words_to_webhook(tmp_path: Path) -> None:
    """正向路径：她开口 ⇒ webhook 真收到那句话；同一条只推一次。"""
    hb = _heartbeat_db(tmp_path / "heartbeat.db", ["以前说过的一句"])
    receiver = _WebhookReceiver()
    try:
        notifier = pusher.PushNotifier(hb, url=receiver.url, timeout_s=3.0)
        assert notifier.poll_once() == []  # 首轮只建游标
        con = sqlite3.connect(hb)
        try:
            con.execute(
                "INSERT INTO expression_log (ts, intent, instruction, llm_text, validation, level)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (2000.0, "回应", "{}", "我想你了", "{}", "main"),
            )
            con.commit()
        finally:
            con.close()
        pushed = notifier.poll_once()
        assert [p["llm_text"] for p in pushed] == ["我想你了"]
        assert notifier.last_error is None and notifier.pushed_count == 1
        assert len(receiver.requests) == 1
        _path, ctype, raw = receiver.requests[0]
        assert "json" in ctype.lower()
        body = json.loads(raw)
        # 常见渠道字段超集：Server酱(desp) / Bark(body) / Telegram(text) 各取所需
        assert body["text"] == "我想你了"
        assert body["desp"] == "我想你了"
        assert body["body"] == "我想你了"
        assert body["title"]
        assert body["level"] == "main"
        # 同一条不重复推（她 ~16 分钟一句，绝不该刷屏）
        assert notifier.poll_once() == []
        assert len(receiver.requests) == 1
    finally:
        receiver.stop()


def test_push_unreachable_degrades_silently_and_leaves_her_data_untouched(
    tmp_path: Path,
) -> None:
    """webhook 不可达 ⇒ 推送静默降级（不抛异常、只记 last_error），她的表达完好。

    **取消测试**：去掉 `_send()` 的 try/except 或 `poll_once()` 的读库兜底，
    本用例必红 —— 连接被拒的异常会直接从 `poll_once()` 冒出来。
    """
    hb = _heartbeat_db(tmp_path / "heartbeat.db", ["以前说过的一句"])
    notifier = pusher.PushNotifier(hb, url="http://127.0.0.1:1/hook", timeout_s=1.0)
    assert notifier.configured is True
    # 首轮只建立游标：不补推历史（面板半夜重启不该一次收到几十条旧话）
    assert notifier.poll_once() == []

    con = sqlite3.connect(hb)
    try:
        con.execute(
            "INSERT INTO expression_log (ts, intent, instruction, llm_text, validation, level)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (2000.0, "回应", "{}", "新的一句话", "{}", "micro"),
        )
        con.commit()
    finally:
        con.close()

    assert notifier.poll_once() == []  # **不许抛异常**
    assert notifier.last_error is not None
    assert "不可达" in notifier.last_error

    # 她的表达一行没少、库仍可读 —— 推送失败没有拖她下水
    con = sqlite3.connect(hb)
    try:
        rows = con.execute("SELECT llm_text FROM expression_log ORDER BY id").fetchall()
    finally:
        con.close()
    assert rows == [("以前说过的一句",), ("新的一句话",)]
    # 游标已推进：同一条不会重复推送
    assert notifier.poll_once() == []


def test_push_unconfigured_is_silent_and_advances_cursor(tmp_path: Path) -> None:
    """未配置 webhook 时：不推、不报错；游标照走（以后配置了也不会补推历史）。"""
    hb = _heartbeat_db(tmp_path / "heartbeat.db", ["第一句"])
    notifier = pusher.PushNotifier(hb, root=tmp_path)  # tmp 下没有 push_url.txt
    assert notifier.configured is False
    assert notifier.poll_once() == []
    assert notifier.last_error is None

    con = sqlite3.connect(hb)
    try:
        con.execute(
            "INSERT INTO expression_log (ts, intent, instruction, llm_text, validation, level)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (2000.0, "回应", "{}", "第二句", "{}", "micro"),
        )
        con.commit()
    finally:
        con.close()
    assert notifier.poll_once() == []
    assert notifier.last_error is None


def test_push_url_file_and_masking(tmp_path: Path) -> None:
    """配置文件读取（`#` 注释/空行忽略）且状态里**不泄露** webhook 的 key。"""
    run = tmp_path / "data" / "run"
    run.mkdir(parents=True)
    (run / "push_url.txt").write_text(
        "# 我的推送\n\nhttps://api.day.app/SECRET-KEY/{title}/{text}\n", encoding="utf-8"
    )
    url = pusher.load_push_url(tmp_path)
    assert url == "https://api.day.app/SECRET-KEY/{title}/{text}"
    masked = pusher.mask_push_url(url)
    assert "SECRET-KEY" not in masked
    assert masked.startswith("https://api.day.app/")


# ── 面板页面本身 ─────────────────────────────────────────────────────
def test_page_has_say_box_and_token_wiring() -> None:
    """界面必须有"对她说"入口，且所有请求都要能带 token（手机访问的前提）。"""
    page = panel.load_page()
    assert "对她说" in page
    assert "/api/say" in page
    assert 'api("/api/status")' in page
    assert "token=" in page
