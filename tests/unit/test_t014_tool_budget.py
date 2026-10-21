"""T-014：工具回合的 token 预算——她的笔不再被截断。

真机根因（2026-10-21）：`_payload()` 给**所有**请求写死 `max_tokens=200`，带 tools 时
write_note 的 arguments 被截断成非法 JSON → `_tool_args` 旧实现静默返回 `{}` →
`write_note(text="")` → "正文为空"；她反复 3 轮都写不成，还以为写好了。

修法：
- 工具回合 `TOOL_MAX_TOKENS = 8192`（用户拍板：她想写的东西不设成本限制；模型输出上限
  等于不设人为限制）；单轮语音仍 **200**（节奏不变）；
- `_tool_args()` 区分"没给参数({})"与"参数被截断/非法(None)"；`finish_reason=='length'`
  且有 tool_calls ⇒ 更大预算重试一次；仍截断 ⇒ 回执含"截断"字样且**不执行**；
- 每次工具调用落 `thought_log kind='tool'` JSON（O1 留痕）。

取消测试：
- 把工具回合改回 200 ⇒ `test_tool_payload_budget` 红；
- 去掉截断守卫（以 `{}` 执行）⇒ `test_truncated_arguments_are_not_executed` 红；
- 去掉留痕回调 ⇒ `test_successful_write_note_is_logged` 红。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.chain import LLMChain
from elysia.llm.deepseek import (
    TOOL_MAX_TOKENS,
    DeepSeekBackend,
    _tool_args,
)
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import ExpressionJob
from elysia.soul.tools.writing import WritingWorkspace

T0 = 1_000_000.0


def _job(user_message: str | None = None) -> ExpressionJob:
    return ExpressionJob(
        job_id="job-1",
        created_ts=T0,
        heartbeat_seq=1,
        output=BrainOutput(
            desire=DesireState(),
            feelings=FeelingState(chat=0.6, miss=0.4),
            will=WillOutput(thought_style=0.1),
        ),
        summary={},
        user_message=user_message,
        intent="回应",
    )


def _tool_response(arguments: str, *, finish_reason: str = "tool_calls") -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {"name": "write_note", "arguments": arguments},
                        }
                    ],
                },
            }
        ]
    }


def _text_response(text: str) -> dict[str, Any]:
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


# ── 验收 1：工具回合 8192；单轮语音 200 ─────────────────────
def test_tool_payload_budget() -> None:
    backend = DeepSeekBackend("test-key", require_key=False)
    tools = [{"type": "function", "function": {"name": "write_note"}}]
    assert TOOL_MAX_TOKENS == 8192
    assert backend._payload([{"role": "user", "content": "x"}], tools=tools)["max_tokens"] == 8192
    assert backend._payload([{"role": "user", "content": "x"}])["max_tokens"] == 200
    # 显式覆盖（截断重试用）
    assert (
        backend._payload(
            [{"role": "user", "content": "x"}], tools=tools, max_tokens=TOOL_MAX_TOKENS
        )["max_tokens"]
        == TOOL_MAX_TOKENS
    )


# ── _tool_args 的三种语义 ──────────────────────────────────
def test_tool_args_distinguish_missing_from_truncated() -> None:
    assert _tool_args({"function": {}}) == {}  # 没给参数
    assert _tool_args({"function": {"arguments": ""}}) == {}
    assert _tool_args({"function": {"arguments": '{"text": "ok"}'}}) == {"text": "ok"}
    assert _tool_args({"function": {"arguments": '{"text": "半截'}}) is None  # 截断
    assert _tool_args({"function": {"arguments": "[1, 2]"}}) is None  # 非对象


# ── 验收 2：被截断 ⇒ 不执行、回执含"截断"、先重试一次 ─────────
@pytest.mark.asyncio
async def test_truncated_arguments_are_not_executed() -> None:
    backend = DeepSeekBackend("test-key", require_key=False)
    payloads: list[dict[str, Any]] = []
    calls: list[tuple[str, dict[str, Any]]] = []

    replies = [
        _tool_response('{"text": "这是一个被截断的长正文', finish_reason="length"),
        _tool_response('{"text": "仍然被截断的长正文', finish_reason="length"),
        _text_response("如果写不了就直说。"),
    ]

    def _post(payload: dict[str, Any]) -> dict[str, Any]:
        payloads.append(payload)
        return replies[min(len(payloads) - 1, len(replies) - 1)]

    async def _run_tool(name: str, args: dict[str, Any]) -> str:
        calls.append((name, args))
        return "（写好了）"

    backend._post = _post  # type: ignore[method-assign]
    text = await backend.complete_with_tools(
        {"intent": "回应"}, {"name": "default"}, tools=[{"name": "x"}], run_tool=_run_tool
    )
    assert text == "如果写不了就直说。"
    assert calls == [], "参数被截断时绝不许以空参数执行工具"
    assert len(payloads) == 3
    assert payloads[0]["max_tokens"] == TOOL_MAX_TOKENS
    assert payloads[1]["max_tokens"] == TOOL_MAX_TOKENS  # 重试（8192 已是模型上限）
    # 回给模型的 tool 消息必须明确说"截断/没有执行"
    tool_messages = [
        m for m in payloads[2]["messages"] if isinstance(m, dict) and m.get("role") == "tool"
    ]
    assert tool_messages, "截断后必须回一条 tool 回执，而不是静默继续"
    assert "截断" in str(tool_messages[-1]["content"])
    assert "没有执行" in str(tool_messages[-1]["content"])


# ── 验收 3：成功 write_note ⇒ thought_log kind='tool' ok=True ──
@pytest.mark.asyncio
async def test_successful_write_note_is_logged(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        backend = DeepSeekBackend("test-key", require_key=False)
        replies = [
            _tool_response(
                json.dumps(
                    {"text": "今天的风很轻，我把她写下来。", "filename": "t014.md"},
                    ensure_ascii=False,
                )
            ),
            _text_response("写好了。"),
        ]

        def _post(payload: dict[str, Any]) -> dict[str, Any]:
            return replies.pop(0)

        backend._post = _post  # type: ignore[method-assign]
        chain = LLMChain(main=backend)
        writing = WritingWorkspace(tmp_path / "writing")

        async def _retriever(*_args: Any, **_kwargs: Any) -> list[Any]:
            return []

        service = ExpressionService(  # type: ignore[arg-type]
            chain, store, writing_workspace=writing, retriever=_retriever
        )
        result = await service.run_job(_job(user_message="写点什么吧"))
        assert result.text == "写好了。"
        assert (tmp_path / "writing" / "t014.md").is_file()

        rows = await store.execute_raw(
            "SELECT kind, text FROM thought_log WHERE kind = 'tool' ORDER BY id DESC LIMIT 1"
        )
        assert rows, "工具调用必须落 thought_log kind='tool'（O1 留痕）"
        record = json.loads(rows[0][1])
        assert record["tool"] == "write_note"
        assert record["ok"] is True
        assert record["args_len"] > 0
        assert record["finish_reason"] == "tool_calls"
        assert record["result_head"]
        assert isinstance(record["elapsed_ms"], (int, float))
    finally:
        await store.close()
