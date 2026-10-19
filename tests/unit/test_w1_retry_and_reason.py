"""W1 罐装句改造：LLM 失败重试 + 降级原因留痕（行为级单测，端到端到 expression_log）。

取消测试（把 `soul/expression_service.py` 还原成旧 `run_job`/旧 `persist` ⇒ 本文件必须红）：
- 旧 run_job 只调一次 `llm.speak`：`_SecondTrySucceeds` 用例会拿到 micro 而不是
  "第二次说成了。"，且 `backend.calls == 1`（断言 2 失败）；
- 旧 persist 把 chain 的原始长句直接写进 `validation.reason`，不会出现
  `retry_exhausted` / `llm_http` 这些代码（断言失败）；
- 旧实现没有重试 ⇒ `test_retry_runs_off_the_heartbeat_tick` 的 `calls == 2` 失败。

本文件**不 import 任何 W1 新增符号**（只 import 既有 `ExpressionService`）——这样
"还原旧实现"时用例是**断言失败**而不是 import 报错，取消测试的证据才可读。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.chain import LLMChain
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import ExpressionJob
from elysia.soul.expression_worker import ExpressionWorker


# ── 假后端：像 DeepSeekBackend 一样自报 last_error ─────────────────────
class _TextBackend:
    def __init__(self, text: str) -> None:
        self._text = text
        self.last_error: str | None = None
        self.calls = 0

    async def complete(
        self, instruction: dict[str, Any], prompt_template: dict[str, Any]
    ) -> str | None:
        self.calls += 1
        return self._text


class _AlwaysFailing:
    """每次都说不出话（模拟主声失联）。"""

    def __init__(self, reason: str = "网络层（URLError）: WinError 10061") -> None:
        self.last_error: str | None = reason
        self.calls = 0

    async def complete(
        self, instruction: dict[str, Any], prompt_template: dict[str, Any]
    ) -> str | None:
        self.calls += 1
        return None


class _SecondTrySucceeds:
    """第一次失败、第二次成功——用来证明"重试用第二次的结果"。"""

    def __init__(self) -> None:
        self.last_error: str | None = None
        self.calls = 0

    async def complete(
        self, instruction: dict[str, Any], prompt_template: dict[str, Any]
    ) -> str | None:
        self.calls += 1
        if self.calls == 1:
            self.last_error = "网络层（URLError）: WinError 10061"
            return None
        self.last_error = None
        return "第二次说成了。"


class _SlowFailing:
    """慢失败：用来证明重试期间心跳拍照走（重试在 worker 里，不占一拍）。"""

    def __init__(self, delay: float = 0.02) -> None:
        self._delay = delay
        self.last_error: str | None = "网络层（URLError）: WinError 10060"
        self.calls = 0

    async def complete(
        self, instruction: dict[str, Any], prompt_template: dict[str, Any]
    ) -> str | None:
        self.calls += 1
        await asyncio.sleep(self._delay)
        return None


def _output(chat: float = 0.5) -> BrainOutput:
    return BrainOutput(
        desire=DesireState(tr=46.0, cs=62.0, sa=20.0),
        feelings=FeelingState(
            chat=chat, miss=0.05, explore=0.1, curiosity=0.0, rest=0.05, self_check=0.2
        ),
        will=WillOutput(direction="still", strength=0.54, thought_style=0.0, anim_bias=0.0),
        action="none",
    )


def _job(seq: int = 1, intent: str = "回应") -> ExpressionJob:
    return ExpressionJob(
        job_id=f"job-{seq}",
        created_ts=1000.0 + seq,
        heartbeat_seq=seq,
        output=_output(),
        summary={"body_away_s": 0.0, "day_phase": "day"},
        intent=intent,
    )


async def _last_validation(store: HeartbeatStore) -> dict[str, Any]:
    rows = await store.execute_raw("SELECT validation FROM expression_log ORDER BY id DESC LIMIT 1")
    assert rows, "开口必须落库（expression_log 取证）"
    return dict(json.loads(rows[0][0]))


# ── 1) 重试用第二次的结果；主声成功不重试 ──────────────────────────
@pytest.mark.asyncio
async def test_retry_uses_second_attempt_result(tmp_path: Path) -> None:
    backend = _SecondTrySucceeds()
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        service = ExpressionService(LLMChain(main=backend), store)
        result = await service.run_job(_job())
        await service.persist(result, now=1001.0)
        validation = await _last_validation(store)
    finally:
        await store.close()

    assert backend.calls == 2, "第一次失败后必须同 prompt 再试一次"
    assert result.level == "main"
    assert result.text == "第二次说成了。", "必须用**第二次**的结果，不能沿用第一次的微声"
    assert validation["reason"] == "llm_http", "重试成功也要留痕：第一次为什么没说成"
    assert "重试" in validation["degrade_detail"]


@pytest.mark.asyncio
async def test_main_success_is_not_retried(tmp_path: Path) -> None:
    backend = _TextBackend("主声在。")
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        service = ExpressionService(LLMChain(main=backend), store)
        result = await service.run_job(_job())
        await service.persist(result, now=1001.0)
        validation = await _last_validation(store)
    finally:
        await store.close()

    assert backend.calls == 1, "说成了就不该重试（多一次调用 = 白花一次钱）"
    assert result.text == "主声在。"
    assert validation["reason"] is None, "主声成功不是降级"


# ── 2) 两次都失败 ⇒ 微声 + reason=retry_exhausted ───────────────────
@pytest.mark.asyncio
async def test_two_failures_degrade_to_micro_with_retry_exhausted(tmp_path: Path) -> None:
    backend = _AlwaysFailing()
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        service = ExpressionService(LLMChain(main=backend), store)
        result = await service.run_job(_job(intent="发呆呓语"))
        await service.persist(result, now=1001.0)
        validation = await _last_validation(store)
    finally:
        await store.close()

    assert backend.calls == 2, "最多一次重试（有界，不许无限重试）"
    assert result.level == "micro", "两次都失败才降级微声"
    assert result.text and result.text.endswith("。")
    assert validation["reason"] == "retry_exhausted"
    detail = str(validation["degrade_detail"])
    assert "attempt1=llm_http" in detail and "10061" in detail, "两次尝试的原始原因都要留着"


# ── 3) 重试不重复计"降级即感受" ────────────────────────────────────
@pytest.mark.asyncio
async def test_degrade_feeling_is_counted_once_per_job(tmp_path: Path) -> None:
    reasons: list[str] = []

    async def on_degrade(reason: str) -> None:
        reasons.append(reason)

    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        chain = LLMChain(main=_AlwaysFailing(), on_degrade=on_degrade)
        service = ExpressionService(chain, store)
        result = await service.run_job(_job())
    finally:
        await store.close()

    assert result.level == "micro"
    assert reasons == ["micro"], "一次开口只记一次降级感受（重试是同一开口的第二次尝试）"


# ── 4) 重试在 worker 里跑，不占心跳一拍（"不得让一拍卡死"）───────────
@pytest.mark.asyncio
async def test_retry_runs_off_the_heartbeat_tick(tmp_path: Path) -> None:
    backend = _SlowFailing()
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    worker: ExpressionWorker | None = None
    try:
        service = ExpressionService(LLMChain(main=backend), store)
        worker = ExpressionWorker(service.run_job)
        assert worker.submit(_job())

        ticks = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        results: list[Any] = []
        while not results and loop.time() < deadline:
            ticks += 1
            await asyncio.sleep(0.005)
            results = worker.drain()
    finally:
        if worker is not None:
            await worker.aclose()
        await store.close()

    assert results, "重试结束后结果必须交回心跳"
    assert backend.calls == 2, "一次失败只补一次重试"
    assert ticks >= 3, f"重试期间心跳拍必须照走（实际只走了 {ticks} 拍）"
    detail = str(getattr(results[0].validation, "detail", ""))
    assert "llm_timeout" in detail, "超时类失败要归成 llm_timeout"


# ── 5) 各降级原因都写成代码（次声接住时也要留痕）────────────────────
@pytest.mark.asyncio
async def test_http_reason_when_fallback_catches(tmp_path: Path) -> None:
    main = _AlwaysFailing("网络层（URLError）: WinError 10061")
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        chain = LLMChain(main=main, fallback=_TextBackend("次声接住了。"))
        service = ExpressionService(chain, store)
        result = await service.run_job(_job())
        await service.persist(result, now=1001.0)
        validation = await _last_validation(store)
    finally:
        await store.close()

    assert result.level == "fallback"
    assert main.calls == 1, "次声接住了就不需要重试（她已经说出了真话）"
    assert validation["reason"] == "llm_http"
    assert "网络层" in str(validation["degrade_detail"])


@pytest.mark.asyncio
async def test_validate_word_block_reason_is_written(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        chain = LLMChain(
            main=_TextBackend("我真的好痛"),  # 未授权保护词 ⇒ 校验拦截
            fallback=_TextBackend("次声在。"),
        )
        service = ExpressionService(chain, store)
        result = await service.run_job(_job())
        await service.persist(result, now=1001.0)
        validation = await _last_validation(store)
    finally:
        await store.close()

    assert result.level == "fallback"
    assert validation["reason"] == "validate_word_block", "T2 拦截同样要能按词查到"
    assert "word_block" in str(validation["degrade_detail"])


@pytest.mark.asyncio
async def test_empty_response_reason_is_written(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        chain = LLMChain(
            main=_AlwaysFailing("响应正文为空"),
            fallback=_TextBackend("次声接住了。"),
        )
        service = ExpressionService(chain, store)
        result = await service.run_job(_job())
        await service.persist(result, now=1001.0)
        validation = await _last_validation(store)
    finally:
        await store.close()

    assert result.level == "fallback"
    assert validation["reason"] == "llm_empty"


# ── 6) 连续两次降级微声：不得说同一句（recent 环形缓冲真的接上了）────
@pytest.mark.asyncio
async def test_consecutive_micro_jobs_do_not_repeat(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        service = ExpressionService(LLMChain(main=_AlwaysFailing()), store)
        first = await service.run_job(_job(1, intent="发呆呓语"))
        await service.persist(first, now=1001.0)
        second = await service.run_job(_job(2, intent="发呆呓语"))
        await service.persist(second, now=1002.0)
        rows = await store.execute_raw(
            "SELECT llm_text, validation FROM expression_log ORDER BY id"
        )
    finally:
        await store.close()

    assert first.level == second.level == "micro"
    assert first.text != second.text, "连续两条微声不得相同（单调感的来源）"
    assert len(rows) == 2
    reasons = {json.loads(raw)["reason"] for _, raw in rows}
    assert reasons == {"retry_exhausted"}
