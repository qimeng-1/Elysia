"""D-15：工具参数"结构上不可能坏"——执行前 schema 校验 + 可选受约束输出（两层）。

第一层（provider 无关，必须做）：`DeepSeekBackend._run_call` 执行前按
`soul/tools/schema.py` 的最小 JSON Schema 校验参数——不合法**不执行**，回
「（工具参数不合法：<原因>；没有执行，请补全后用同样的工具再试一次）」，
留痕 `ok=False, reason='schema:<原因>'`；现有 3 轮工具回合自然重试，仍不合法就
正常说话（不死循环、绝不把缺字段当空字符串执行）。

第二层（尽力而为）：强制写作轮（T-021 第二拍）尝试 `response_format`；后端回
400 不支持 ⇒ 自动去掉重试一次、只警告一次、本进程内熔断，绝不影响正常开口。

取消测试（每条都能红）：
- 去掉 `_run_call` 的 schema 校验 ⇒ 用例 1/2/4 红（空参数会被执行）；
- 把 `write_note.text` 的 `x-non-blank` 去掉 ⇒ 用例 2 红；
- 把截断回执换成新路径 ⇒ 用例 5 红；
- 去掉 `_post_tool_payload` 的 400 降级 ⇒ 用例 6 红；
- 去掉 `_disable_response_format` 熔断 ⇒ 用例 6 的"后续请求不再带"断言红。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib import error as urlerror

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.chain import WRITE_NOTE_TOOL, LLMChain
from elysia.llm.deepseek import DeepSeekBackend
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import ExpressionJob
from elysia.soul.tools.schema import TOOL_SCHEMAS, validate_tool_args
from elysia.soul.tools.writing import WritingWorkspace

T0 = 1_000_000.0


def _tool_response(
    name: str, arguments: str, *, finish_reason: str = "tool_calls"
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-d15",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                },
            }
        ]
    }


def _text_response(text: str) -> dict[str, Any]:
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


async def _run_rounds(
    replies: list[dict[str, Any]],
    *,
    tool: str = "write_note",
) -> tuple[
    str | None, list[tuple[str, dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """跑一次带工具的开口；返回（正文, 执行了的工具调用, HTTP payload, 留痕记录）。"""
    backend = DeepSeekBackend("test-key", require_key=False)
    payloads: list[dict[str, Any]] = []
    calls: list[tuple[str, dict[str, Any]]] = []
    records: list[dict[str, Any]] = []
    pending = list(replies)

    def _post(payload: dict[str, Any]) -> dict[str, Any]:
        payloads.append(payload)
        return pending.pop(0)

    async def _run_tool(name: str, args: dict[str, Any]) -> str:
        calls.append((name, args))
        return "（已写进你的文件夹：x.md；本次 5 字，文件 10 字节）"

    backend._post = _post  # type: ignore[method-assign]
    backend.set_tool_observer(lambda record: records.append(record))
    text = await backend.complete_with_tools(
        {"intent": "回应"},
        {"name": "default"},
        tools=[{"type": "function", "function": {"name": tool}}],
        run_tool=_run_tool,
    )
    return text, calls, payloads, records


def _tool_messages(payloads: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for payload in payloads:
        for message in payload.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "tool":
                out.append(str(message.get("content") or ""))
    return out


# ── 1. write_note 参数 {} ⇒ 不执行、回执明确、留痕 ok=False ──────────
@pytest.mark.asyncio
async def test_write_note_empty_args_not_executed() -> None:
    """`write_note` 参数 `{}` ⇒ 不执行；回执含"参数不合法"与"没有执行"；留痕 ok=false。

    取消测试：去掉 `_run_call` 的 `validate_tool_args` ⇒ `calls` 非空（本用例红）。
    """
    text, calls, payloads, records = await _run_rounds(
        [_tool_response("write_note", "{}"), _text_response("那我先不写了。")]
    )
    assert text == "那我先不写了。"
    assert calls == [], "参数为 {} 的 write_note 被执行了"
    receipts = _tool_messages(payloads)
    assert receipts, "参数不合法时必须回一条 tool 回执"
    assert "参数不合法" in receipts[0]
    assert "没有执行" in receipts[0]
    assert "text" in receipts[0], "回执要说清缺哪个字段"
    assert records and records[0]["tool"] == "write_note"
    assert records[0]["ok"] is False
    assert str(records[0].get("reason") or "").startswith("schema:")


# ── 2. write_note text 全是空白 ⇒ 不执行 ────────────────────────────
@pytest.mark.asyncio
async def test_write_note_blank_text_not_executed() -> None:
    """`{"text": "   "}` ⇒ 空白不算正文，不执行；回执点明空白。

    取消测试：把 schema 里 `text` 的 `x-non-blank` 去掉 ⇒ 本用例红。
    """
    text, calls, payloads, records = await _run_rounds(
        [
            _tool_response("write_note", json.dumps({"text": "   "}, ensure_ascii=False)),
            _text_response("嗯。"),
        ]
    )
    assert text == "嗯。"
    assert calls == [], "空白正文的 write_note 被执行了"
    receipts = _tool_messages(payloads)
    assert "参数不合法" in receipts[0] and "没有执行" in receipts[0]
    assert "空白" in receipts[0]
    assert records[0]["ok"] is False


# ── 3. write_note 参数合法 ⇒ 正常执行、留痕 ok=true ──────────────────
@pytest.mark.asyncio
async def test_write_note_valid_args_executed() -> None:
    """参数合法 ⇒ 正常写，留痕 ok=True。"""
    text, calls, _payloads, records = await _run_rounds(
        [
            _tool_response(
                "write_note",
                json.dumps({"text": "她自己的正文", "path": "a.md"}, ensure_ascii=False),
            ),
            _text_response("写好了。"),
        ]
    )
    assert text == "写好了。"
    assert calls == [("write_note", {"text": "她自己的正文", "path": "a.md"})]
    assert records and records[0]["ok"] is True
    assert "reason" not in records[0]


# ── 4. rename_my_file 缺 new_name ⇒ 不执行、回执明确 ─────────────────
@pytest.mark.asyncio
async def test_rename_missing_new_name_not_executed() -> None:
    """`rename_my_file` 缺 `new_name` ⇒ 不执行，回执点名字段。

    取消测试：把 `rename_my_file` 的 required 去掉 ⇒ 本用例红。
    """
    _text, calls, payloads, records = await _run_rounds(
        [_tool_response("rename_my_file", '{"path": "a.md"}'), _text_response("好。")],
        tool="rename_my_file",
    )
    assert calls == [], "缺 new_name 的 rename_my_file 被执行了"
    receipts = _tool_messages(payloads)
    assert "参数不合法" in receipts[0] and "没有执行" in receipts[0]
    assert "new_name" in receipts[0]
    assert records[0]["ok"] is False


# ── 5. 截断 JSON ⇒ 仍走 T-014 的"截断"回执（语义不回退）──────────────
@pytest.mark.asyncio
async def test_truncated_arguments_still_rejected() -> None:
    """`_tool_args` 返回 None（截断）⇒ 仍是 T-014 的截断回执，不执行。"""
    _text, calls, payloads, _records = await _run_rounds(
        [
            _tool_response("write_note", '{"text": "半截正文', finish_reason="length"),
            _tool_response("write_note", '{"text": "仍然半截', finish_reason="length"),
            _text_response("写不了就直说。"),
        ]
    )
    assert calls == []
    receipts = _tool_messages(payloads)
    assert receipts and "截断" in receipts[-1] and "没有执行" in receipts[-1]


# ── 6. 第二层：response_format 400 ⇒ 自动退化 + 只警告一次 + 熔断 ─────
@pytest.mark.asyncio
async def test_response_format_400_degrades_and_breaks_circuit(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """后端对 response_format 回 400 ⇒ 去掉它重试成功；后续请求不再带它。

    取消测试：去掉 `_post_tool_payload` 的 400 降级 ⇒ 本用例直接抛 HTTPError（红）；
    去掉 `_disable_response_format` 熔断 ⇒ 第二次调用又会带 response_format（红）。
    """
    import elysia.llm.deepseek as deepseek_mod

    monkeypatch.setattr(deepseek_mod, "_RESPONSE_FORMAT_DISABLED", False)
    backend = DeepSeekBackend("test-key", require_key=False)
    payloads: list[dict[str, Any]] = []

    def _post(payload: dict[str, Any]) -> dict[str, Any]:
        payloads.append(payload)
        if "response_format" in payload:
            raise urlerror.HTTPError("http://x", 400, "Bad Request", {}, None)
        return _text_response("嗯。")

    backend._post = _post  # type: ignore[method-assign]

    async def _run_tool(name: str, args: dict[str, Any]) -> str:
        return "（好的）"

    with caplog.at_level(logging.WARNING, logger="elysia.llm.deepseek"):
        text = await backend.complete_with_tools(
            {"intent": "写作"},
            {"name": "default"},
            tools=[WRITE_NOTE_TOOL],
            run_tool=_run_tool,
            response_format={"type": "json_object"},
        )
        assert text == "嗯。"
        assert len(payloads) == 2, "400 之后必须去掉该参数重试一次"
        assert payloads[0]["response_format"] == {"type": "json_object"}
        assert "response_format" not in payloads[1]
        warnings = [rec for rec in caplog.records if "response_format" in rec.getMessage()]
        assert len(warnings) == 1, "只警告一次"

        # 熔断：本进程内后续请求不再尝试 response_format
        payloads.clear()
        text2 = await backend.complete_with_tools(
            {"intent": "写作"},
            {"name": "default"},
            tools=[WRITE_NOTE_TOOL],
            run_tool=_run_tool,
            response_format={"type": "json_object"},
        )
        assert text2 == "嗯。"
        assert payloads, "第二次调用没有发生"
        assert all("response_format" not in payload for payload in payloads)
        assert len([rec for rec in caplog.records if "response_format" in rec.getMessage()]) == 1


# ── 7. 落库留痕：schema 不合法 ⇒ thought_log kind='tool' 里 ok=false ──
@pytest.mark.asyncio
async def test_invalid_args_logged_to_thought_log(tmp_path: Path) -> None:
    """不合法参数不写盘，但 `thought_log kind='tool'` 留痕 `ok=false, reason=schema:…`。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        backend = DeepSeekBackend("test-key", require_key=False)
        replies = [_tool_response("write_note", "{}"), _text_response("嗯，先不写。")]

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
        job = ExpressionJob(
            job_id="job-d15",
            created_ts=T0,
            heartbeat_seq=1,
            output=BrainOutput(
                desire=DesireState(),
                feelings=FeelingState(chat=0.6, miss=0.4),
                will=WillOutput(thought_style=0.1),
            ),
            summary={},
            intent="回应",
        )
        result = await service.run_job(job)
        assert result.text == "嗯，先不写。"
        assert not list(writing.root.glob("**/*")), "参数不合法却写了文件"

        rows = await store.execute_raw(
            "SELECT text FROM thought_log WHERE kind = 'tool' ORDER BY id DESC LIMIT 1"
        )
        assert rows, "工具调用必须留痕"
        record = json.loads(rows[0][0])
        assert record["ok"] is False
        assert str(record.get("reason") or "").startswith("schema:")
    finally:
        await store.close()


# ── 8. 第二层兜底：强制轮"200 但没调工具" ⇒ 去掉 response_format 重试 ──
@pytest.mark.asyncio
async def test_forced_round_without_tool_call_drops_format(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """受约束输出若让模型不敢调工具（200 但无 tool_calls）⇒ 去掉它重试并熔断。

    取消测试：去掉 `complete_with_tools` 里这段 200-无工具调用的兜底 ⇒ 本用例红
    （第一次返回文本就结束，write_note 没执行）。
    """
    import elysia.llm.deepseek as deepseek_mod

    monkeypatch.setattr(deepseek_mod, "_RESPONSE_FORMAT_DISABLED", False)
    backend = DeepSeekBackend("test-key", require_key=False)
    payloads: list[dict[str, Any]] = []
    replies = [
        _text_response("我先说说我的想法……"),  # 200 但没有 tool_calls（疑似被约束输出干扰）
        _tool_response("write_note", json.dumps({"text": "她自己写的正文"}, ensure_ascii=False)),
        _text_response("写好了。"),
    ]
    calls: list[tuple[str, dict[str, Any]]] = []

    def _post(payload: dict[str, Any]) -> dict[str, Any]:
        payloads.append(payload)
        return replies.pop(0)

    async def _run_tool(name: str, args: dict[str, Any]) -> str:
        calls.append((name, args))
        return "（已写进你的文件夹：x.md）"

    backend._post = _post  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="elysia.llm.deepseek"):
        text = await backend.complete_with_tools(
            {"directive": "写吧"},
            {"name": "default"},
            tools=[WRITE_NOTE_TOOL],
            run_tool=_run_tool,
            tool_choice={"type": "function", "function": {"name": "write_note"}},
            response_format={"type": "json_object"},
        )
    assert text == "写好了。"
    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1], "疑似干扰时必须去掉该参数重试"
    assert calls == [("write_note", {"text": "她自己写的正文"})]
    warnings = [rec for rec in caplog.records if "response_format" in rec.getMessage()]
    assert len(warnings) == 1, "只警告一次"


# ── 纯函数面：各工具的最小 schema ───────────────────────────────────
def test_schema_minimum_rules() -> None:
    assert set(TOOL_SCHEMAS) >= {
        "write_note",
        "read_url",
        "rename_my_file",
        "set_intention",
        "keep_this_moment",
        "recall",
    }
    assert validate_tool_args("write_note", {"text": "正文"}) == (True, None)
    ok, reason = validate_tool_args("write_note", {})
    assert not ok and "text" in str(reason)
    ok, reason = validate_tool_args("write_note", {"text": "  \n"})
    assert not ok and "空白" in str(reason)
    ok, reason = validate_tool_args("write_note", {"text": "x", "path": "../逃逸.md"})
    assert not ok and ".." in str(reason)
    ok, _ = validate_tool_args("rename_my_file", {"path": "a.md", "new_name": "b.md"})
    assert ok
    ok, _ = validate_tool_args("rename_my_file", {"path": "a.md"})
    assert not ok
    ok, _ = validate_tool_args("rename_my_file", {"path": "a.md", "new_name": "  "})
    assert not ok
    ok, _ = validate_tool_args("set_intention", {"goal": "读一页", "action": "write"})
    assert not ok
    ok, _ = validate_tool_args("set_intention", {"goal": "读一页", "action": "read_url"})
    assert ok
    ok, _ = validate_tool_args("read_url", {"url": "https://zh.wikipedia.org/x"})
    assert ok
    ok, _ = validate_tool_args("read_url", {})
    assert not ok
    # 记忆类只校验类型；缺 topic 是合法输入（执行器有兜底）
    assert validate_tool_args("recall", {}) == (True, None)
    assert validate_tool_args("recall", {"topic": "生日"}) == (True, None)
    ok, _ = validate_tool_args("recall", {"topic": 3})
    assert not ok
    # 未知工具放行（向前兼容）
    assert validate_tool_args("future_tool", {"whatever": 1}) == (True, None)
