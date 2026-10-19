"""P0-R11（D-10）单测：**死代理不得让她永久哑掉** + 降级原因必须可查。

事故复盘（2026-10-18 确诊）：urllib 的全局 opener 在首次调用时缓存 ProxyHandler。
她启动时系统代理（127.0.0.1:7890）开着 ⇒ opener 绑死代理；代理退出后该进程内
所有 urlopen 都打向死端口 ⇒ WinError 10061 ⇒ 主声**永久**降级 micro（哑了 22.5 小时）。

本文件用**本机真 HTTP 服务**做端到端（不访问外网、不花钱），钉住三件事：
1. 代理是死的 ⇒ 客户端**仍能连上**（自动绕过代理直连重试），不得返回 None；
2. 真·HTTP 错误（401）**不**被误判成"代理坏了"（不做无谓直连重试）；
3. 降级原因写进 `ValidationResult.reason` ⇒ 落库后 `expression_log.validation.reason` 可查。

取消测试：`test_dead_proxy_is_healed_by_direct_retry`
—— 还原成 `urllib.request.urlopen`（全局缓存 opener）时，该用例必红（返回 None）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar
from urllib import request as urlrequest

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.chain import LLMChain
from elysia.llm.deepseek import DeepSeekBackend
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import ExpressionJob

_INSTRUCTION: dict[str, Any] = {
    "version": 1,
    "intent": "回应",
    "state_brief": {"tr": 50.0, "cs": 50.0, "sa": 30.0},
    "lexical_permits": [],
}

_DEAD_PROXY = "http://127.0.0.1:9"  # Discard 端口：本机几乎不可能有监听 ⇒ 连接被拒


class _ChatHandler(BaseHTTPRequestHandler):
    """极简 DeepSeek 替身：POST 任意路径返回一句正文。"""

    reply: ClassVar[dict[str, Any]] = {"choices": [{"message": {"content": "嗯，我在呢"}}]}
    status = 200
    calls = 0

    def do_POST(self) -> None:
        _ChatHandler.calls += 1
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)  # 请求体丢掉即可
        body = json.dumps(_ChatHandler.reply, ensure_ascii=False).encode("utf-8")
        self.send_response(_ChatHandler.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """测试里不打印访问日志。"""


@pytest.fixture()
def chat_server() -> str:
    _ChatHandler.reply = {"choices": [{"message": {"content": "嗯，我在呢"}}]}
    _ChatHandler.status = 200
    _ChatHandler.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChatHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _force_dead_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 urllib 认为"系统代理 = 死端口"，并禁用 localhost 直连豁免。"""
    monkeypatch.setattr(
        urlrequest, "getproxies", lambda: {"http": _DEAD_PROXY, "https": _DEAD_PROXY}
    )
    monkeypatch.setattr(urlrequest, "proxy_bypass", lambda _host: False)


def _backend(base: str) -> DeepSeekBackend:
    return DeepSeekBackend("test-key", base=base, timeout_s=5.0)


# ── 1) 取消测试：死代理 ⇒ 直连兜底成功 ────────────────────────────
@pytest.mark.asyncio
async def test_dead_proxy_is_healed_by_direct_retry(
    chat_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消测试（P0-R11）：系统代理指向死端口 ⇒ 必须自动直连重试并**照样说得出话**。

    还原成旧写法（`urllib.request.urlopen`：全局 opener 缓存首次读到的代理）时，
    本用例必红——返回 None（= 她永久只发罐装话）。
    """
    _force_dead_proxy(monkeypatch)
    backend = _backend(chat_server)

    text = await backend.complete(_INSTRUCTION, {})

    assert text == "嗯，我在呢", (
        f"死代理下必须直连兜底成功，实际返回 {text!r}（last_error={backend.last_error}）"
    )
    assert backend.last_error is None, "成功时不得残留失败原因"


@pytest.mark.asyncio
async def test_dead_proxy_and_dead_endpoint_reports_both_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """代理与直连都不通 ⇒ 返回 None，但 `last_error` 必须同时说清两条路径的失败原因。"""
    _force_dead_proxy(monkeypatch)
    backend = _backend("http://127.0.0.1:1")  # 本地没有这个端口

    text = await backend.complete(_INSTRUCTION, {})

    assert text is None
    assert backend.last_error is not None
    assert "连接层失败" in backend.last_error
    assert "代理路径" in backend.last_error and "直连路径" in backend.last_error


# ── 2) 真·HTTP 错误不得被当成"代理坏了" ─────────────────────────────
@pytest.mark.asyncio
async def test_http_401_is_not_retried_as_proxy_failure(
    chat_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """401（key 错/无权限）是配置问题，不是代理问题：**只发一次**，别做无谓直连重试。"""
    monkeypatch.setattr(urlrequest, "getproxies", lambda: {})
    _ChatHandler.status = 401
    _ChatHandler.reply = {"error": {"message": "invalid api key"}}
    backend = _backend(chat_server)

    opens: list[bool] = []
    real_open = backend._open

    def counting_open(body: bytes, *, use_proxy: bool) -> dict[str, Any]:
        opens.append(use_proxy)
        return real_open(body, use_proxy=use_proxy)

    monkeypatch.setattr(backend, "_open", counting_open)

    text = await backend.complete(_INSTRUCTION, {})

    assert text is None
    assert opens == [True], f"HTTP 401 不该触发直连重试，实际 opener 调用：{opens}"
    assert backend.last_error is not None and "HTTPError" in backend.last_error


# ── 3) 降级原因可查（T-007）：微声不再"无原因" ────────────────────
class _FailingBackend:
    """失败替身：像 DeepSeekBackend 一样自报 last_error。"""

    def __init__(self, reason: str) -> None:
        self.last_error: str | None = reason

    async def complete(
        self, instruction: dict[str, Any], prompt_template: dict[str, Any]
    ) -> str | None:
        return None


class _RaisingBackend:
    async def complete(
        self, instruction: dict[str, Any], prompt_template: dict[str, Any]
    ) -> str | None:
        raise RuntimeError("连接层失败：WinError 10061")

    last_error: str | None = None


class _TextBackend:
    def __init__(self, text: str) -> None:
        self._text = text
        self.last_error: str | None = None

    async def complete(
        self, instruction: dict[str, Any], prompt_template: dict[str, Any]
    ) -> str | None:
        return self._text


@pytest.mark.asyncio
async def test_micro_result_carries_degrade_reason() -> None:
    """取消测试的一半：`expression_log.validation.reason` 不得再是 null。"""
    reasons: list[str] = []

    async def on_degrade(reason: str) -> None:
        reasons.append(reason)

    chain = LLMChain(
        main=_FailingBackend("网络层（URLError）: WinError 10061 由于目标计算机积极拒绝"),
        on_degrade=on_degrade,
    )
    result = await chain.speak(_INSTRUCTION)

    assert result.level == "micro"
    assert reasons == ["micro"], "降级回调的语义（级别）保持不变"
    assert result.validated is not None
    assert result.validated.ok is True
    assert result.validated.reason is not None
    assert "main 无文本" in result.validated.reason
    assert "10061" in result.validated.reason


@pytest.mark.asyncio
async def test_backend_exception_degrades_instead_of_crashing() -> None:
    """后端抛异常 ⇒ 也要降级成微声（她这一句不能直接没声音）。"""
    chain = LLMChain(main=_RaisingBackend())
    result = await chain.speak(_INSTRUCTION)

    assert result.level == "micro"
    assert result.validated is not None and result.validated.reason is not None
    assert "main 后端异常" in result.validated.reason


@pytest.mark.asyncio
async def test_fallback_result_carries_why_main_was_not_used() -> None:
    """次声成功时，也要记下"主声为什么没用上"（校验拦截）。"""
    chain = LLMChain(main=_TextBackend("我真的好痛"), fallback=_TextBackend("次声在"))
    result = await chain.speak({**_INSTRUCTION, "lexical_permits": []})

    assert result.level == "fallback"
    assert result.validated is not None
    assert result.validated.reason is not None
    assert "main 被校验拦截" in result.validated.reason


# ── 4) 落库取证：expression_log.validation.reason 可查 ─────────────
def _brain_output() -> BrainOutput:
    return BrainOutput(
        desire=DesireState(tr=50.0, cs=50.0, sa=30.0),
        feelings=FeelingState(
            chat=0.4, miss=0.1, explore=0.5, curiosity=0.3, rest=0.2, self_check=0.05
        ),
        will=WillOutput(direction="reach", strength=0.56, thought_style=-0.3, anim_bias=0.2),
        action="none",
    )


@pytest.mark.asyncio
async def test_micro_reason_is_persisted_into_expression_log(tmp_path: Any) -> None:
    """端到端：主声失联 ⇒ 她仍说罐装话，但库里能查出**为什么**（此前 reason=null）。

    W1 起 `reason` 是固定代码（这里是 `retry_exhausted`：同 prompt 重试一次仍失败），
    后端/校验器原话进 `degrade_detail`——代码可聚合，原话留证据，两样都不丢。
    """
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        chain = LLMChain(main=_FailingBackend("网络层（URLError）: WinError 10061"))
        service = ExpressionService(chain, store)
        job = ExpressionJob(
            job_id="job-1",
            created_ts=1_000.0,
            heartbeat_seq=1,
            output=_brain_output(),
            summary={"body_away_s": 0.0, "day_phase": "day"},
            intent="回应",
        )
        result = await service.run_job(job)
        await service.persist(result, now=1_001.0)

        rows = await store.execute_raw(
            "SELECT level, validation FROM expression_log ORDER BY id DESC LIMIT 1"
        )
    finally:
        await store.close()

    assert rows, "罐装话也必须落库（气泡要显示）"
    level, raw_validation = rows[0]
    assert level == "micro"
    validation = json.loads(raw_validation)
    assert validation["ok"] is True
    assert validation["reason"] == "retry_exhausted", "两次都失败 ⇒ 代码写清（可聚合）"
    assert "10061" in validation["degrade_detail"], "原始原因不得丢（到底哪一步坏了）"
