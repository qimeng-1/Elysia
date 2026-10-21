"""T-015：工具轮之后必须留一个"说话轮"。

真机事故（2026-10-21 19:35）：她调用 write_note 真的写成功了（文件 755 字节在磁盘上），
但 `MAX_TOOL_ROUNDS=3` 全用在工具上 ⇒ 没有一轮留给她说话 ⇒ `complete_with_tools`
返回 None ⇒ 表达降级成微声罐装句；她因此以为自己失败，说"两次都没落进去"。

修法：工具轮用尽后，**强制再请求一轮"不带 tools"**（payload 里没有 `tools`）只为让她
开口；这一轮的正文就是她这次的话。若这一轮也失败（空正文/又返回工具调用），才降级。

取消测试：去掉这个补轮 ⇒ `test_speaking_round_after_tool_rounds` 红（返回 None）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from elysia.llm.deepseek import MAX_TOOL_ROUNDS, DeepSeekBackend

T0 = 1_000_000.0


def _tool_reply(n: int = 1) -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"call-{n}",
                            "function": {
                                "name": "write_note",
                                "arguments": json.dumps(
                                    {"text": f"第{n}轮写下的内容", "filename": f"t015-{n}.md"},
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    }


def _text_reply(text: str) -> dict[str, Any]:
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


@pytest.mark.asyncio
async def test_speaking_round_after_tool_rounds() -> None:
    """连续 3 轮 tool_calls ⇒ 第 4 次请求不带 tools，其正文被当作她的话返回。"""
    backend = DeepSeekBackend("test-key", require_key=False)
    payloads: list[dict[str, Any]] = []
    calls: list[str] = []

    replies: list[dict[str, Any]] = [_tool_reply(i) for i in range(1, MAX_TOOL_ROUNDS + 1)]
    replies.append(_text_reply("我把想写的都写下来了。"))

    def _post(payload: dict[str, Any]) -> dict[str, Any]:
        payloads.append(payload)
        return replies.pop(0)

    async def _run_tool(name: str, args: dict[str, Any]) -> str:
        calls.append(name)
        return "（写好了）"

    backend._post = _post  # type: ignore[method-assign]
    text = await backend.complete_with_tools(
        {"intent": "回应"}, {"name": "default"}, tools=[{"name": "x"}], run_tool=_run_tool
    )

    assert len(payloads) == MAX_TOOL_ROUNDS + 1, "工具轮用尽后必须补一个说话轮"
    assert "tools" in payloads[0], "工具轮应带 tools"
    assert "tools" not in payloads[-1], "说话轮不得再带 tools（否则可能又去调工具）"
    assert text == "我把想写的都写下来了。", "说话轮的正文必须被当作她的话返回"
    assert calls == ["write_note"] * MAX_TOOL_ROUNDS, "工具确实执行了（写成功）"


@pytest.mark.asyncio
async def test_speaking_round_failure_still_degrades() -> None:
    """第 4 轮也返回工具调用/空正文 ⇒ 仍返回 None（降级路径不许被打坏）。"""
    backend = DeepSeekBackend("test-key", require_key=False)
    replies: list[dict[str, Any]] = [_tool_reply(i) for i in range(1, MAX_TOOL_ROUNDS + 1)]
    replies.append(_tool_reply(99))  # 说话轮居然又给了工具调用
    replies.append(_tool_reply(100))

    def _post(payload: dict[str, Any]) -> dict[str, Any]:
        return replies.pop(0)

    async def _run_tool(name: str, args: dict[str, Any]) -> str:
        return "（写好了）"

    backend._post = _post  # type: ignore[method-assign]
    text = await backend.complete_with_tools(
        {"intent": "回应"}, {"name": "default"}, tools=[{"name": "x"}], run_tool=_run_tool
    )
    assert text is None

    # 空正文同样降级
    backend2 = DeepSeekBackend("test-key", require_key=False)
    replies2: list[dict[str, Any]] = [_tool_reply(i) for i in range(1, MAX_TOOL_ROUNDS + 1)]
    replies2.append({"choices": [{"finish_reason": "stop", "message": {"content": ""}}]})

    def _post2(payload: dict[str, Any]) -> dict[str, Any]:
        return replies2.pop(0)

    backend2._post = _post2  # type: ignore[method-assign]
    assert (
        await backend2.complete_with_tools(
            {"intent": "回应"},
            {"name": "default"},
            tools=[{"name": "x"}],
            run_tool=_run_tool,
        )
        is None
    )
