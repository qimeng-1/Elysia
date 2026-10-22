"""T-022a：承诺记忆化 + 成文自我证据 + 提醒降级。

用户判断（2026-10-22）：「承诺的事只要她能做到，她应该记得、应该能自发去做，
被推着做不符合期望。」——提醒的线索要从系统字段搬进**她自己的记忆**。

验收（每条都能取消测试变红）：
1. 捕获后有对应的"我答应过要写…还没写"记忆（kind=internal、source_ref=promise:<id>、
   source=self、certainty=certain、importance≥0.6、正文无系统指令字样）；
2. 同一承诺不重复落记忆；
3. 成文（自己写的 / 第二拍写的）⇒ 落一条"我把〈文件名〉写完了，是我自己写的"，
   旧记忆 `superseded_by` 指向它（原件保真）；
4. 正常回合 payload 不含 `pending_promises` / `pending_promises_hint`；
   `unfinished_things` 只放她的原句（≤3、无祈使句/工具名）；
5. 承诺 >24h 或之后已开口 ≥2 次 ⇒ 兜底问句出现一次（12h 冷却）；
6. 强制轮只在"即时意图"（马上/这就/今晚就写）时触发。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.chain import LLMChain
from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import STATUS_SUCCESS, ExpressionJob, ExpressionResult
from elysia.soul.promises import record_promise_memory
from elysia.soul.tools.writing import WritingWorkspace

T0 = 1_000_000.0
USER_ASKS_WRITE = "写一下爱莉希雅到底是个什么样的人"


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text

    async def speak(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            text=self.text,
            level="main",
            validated=ValidationResult(ok=True, reason=None),
        )


class _FakeBackend:
    """主声替身：第二拍走强制 write_note（正文来自它，证明程序不代写）。"""

    def __init__(self, reply: str = "我今晚就写。", *, forced_text: str = "她写的正文。") -> None:
        self.reply = reply
        self.forced_text = forced_text
        self.tool_rounds = 0

    async def complete(self, instruction: dict[str, Any], prompt_template: dict[str, Any]) -> str:
        return self.reply

    async def complete_with_tools(
        self,
        instruction: dict[str, Any],
        prompt_template: dict[str, Any],
        *,
        tools: list[dict[str, Any]],
        run_tool: Any,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str | None:
        self.tool_rounds += 1
        await asyncio.sleep(0)
        await run_tool("write_note", {"text": self.forced_text, "path": "爱莉希雅.md"})
        return "写好了。"


def _job(user_message: str | None = None) -> ExpressionJob:
    return ExpressionJob(
        job_id="job-t022a",
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


def _result(text: str) -> ExpressionResult:
    return ExpressionResult(
        job_id="job-t022a",
        status=STATUS_SUCCESS,
        order_key=(1, T0),
        text=text,
        level="main",
        validation=ValidationResult(ok=True, reason=None),
        instruction={},
        intent="回应",
        feelings={"chat": 0.6, "miss": 0.4},
        sentences=[text],
    )


async def _service(
    tmp_path: Path, llm: Any
) -> tuple[ExpressionService, HeartbeatStore, WritingWorkspace]:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    workspace = WritingWorkspace(tmp_path / "home")
    service = ExpressionService(llm, store, writing_workspace=workspace)
    return service, store, workspace


async def _memory_rows(store: HeartbeatStore) -> list[dict[str, Any]]:
    rows = await store.execute_raw(
        "SELECT id, kind, content, source, certainty, importance, source_ref, superseded_by"
        " FROM memories ORDER BY id"
    )
    keys = (
        "id",
        "kind",
        "content",
        "source",
        "certainty",
        "importance",
        "source_ref",
        "superseded_by",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


# ── 1. 捕获 ⇒ 她自己的记忆 ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_capture_records_her_own_memory(tmp_path: Path) -> None:
    """捕获后有一条她的"还没写"记忆：第一人称、可召回、无系统指令。

    取消测试：删掉 `capture_promise` 里的 `record_promise_memory` 调用 ⇒ 本用例红。
    """
    llm = _FakeLLM("算数。说过的就写，我这会儿还没写完")
    service, store, _workspace = await _service(tmp_path, llm)
    try:
        await service.persist(await service.run_job(_job(USER_ASKS_WRITE)), now=T0 + 1)
        rows = await _memory_rows(store)
        assert rows, "捕获承诺后没有落她自己的记忆"
        rec = rows[-1]
        assert rec["source_ref"].startswith("promise:")
        assert rec["kind"] == "internal", "要用能被正常召回的 kind（不是 world/self）"
        assert rec["source"] == "self"
        assert rec["certainty"] == "certain"
        assert float(rec["importance"]) >= 0.6
        content = str(rec["content"])
        assert content.startswith("我答应过要写")
        assert "还没写" in content
        for banned in ("write_note", "现在用", "系统", "指令"):
            assert banned not in content, f"记忆正文不该出现系统字样：{banned}"

        unfinished = await store.unfinished_promises(limit=3)
        assert unfinished and unfinished[0]["content"] == content
    finally:
        await store.close()


# ── 2. 同一承诺不重复落记忆 ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_same_promise_does_not_duplicate_memory(tmp_path: Path) -> None:
    """同一请求连问两轮 ⇒ 承诺与记忆都只有一条（source_ref 幂等）。"""
    llm = _FakeLLM("算数。说过的就写，我这会儿还没写完")
    service, store, _workspace = await _service(tmp_path, llm)
    try:
        await service.persist(await service.run_job(_job(USER_ASKS_WRITE)), now=T0 + 1)
        await service.persist(await service.run_job(_job(USER_ASKS_WRITE)), now=T0 + 2)
        rows = [
            row
            for row in await _memory_rows(store)
            if str(row["source_ref"] or "").startswith("promise:")
        ]
        assert len(rows) == 1, f"同一承诺落了 {len(rows)} 条记忆"
    finally:
        await store.close()


# ── 3a. 自己写成 ⇒ 自我证据 + 旧记忆 superseded ─────────────────────
@pytest.mark.asyncio
async def test_own_write_leaves_evidence_and_supersedes(tmp_path: Path) -> None:
    """她自己写成功 ⇒ "我写完了"记忆；旧的"还没写"被 superseded（原件保留）。

    取消测试：删掉 `settle_after_reply` 里 wrote 分支的 `record_written_memory`
    ⇒ 本用例红（没有成文记忆、旧记忆仍 pending）。
    """
    llm = _FakeLLM("写好了。")
    service, store, _workspace = await _service(tmp_path, llm)
    try:
        pid = int(
            await store.add_intention(
                T0 - 100, goal="爱莉希雅那篇", action="write_note", target=None, kind="promise"
            )
        )
        await record_promise_memory(store, pid, "爱莉希雅那篇", T0 - 100)
        before = [
            row
            for row in await _memory_rows(store)
            if str(row["source_ref"] or "") == f"promise:{pid}"
        ]
        assert before

        service._job_write_note_ok = True
        service._job_written_path = "爱莉希雅.md"
        await service.persist(_result("写好了，你看看。"), now=T0 + 1)

        rows = await _memory_rows(store)
        done = [row for row in rows if "写完了" in str(row["content"])]
        assert done, "成文后没有'我写完了'的自我证据"
        assert "我自己写的" in str(done[0]["content"])
        old = [row for row in rows if str(row["source_ref"] or "") == f"promise:{pid}"]
        assert old, "旧的'还没写'记忆被删了（必须保真）"
        assert old[0]["superseded_by"] == done[0]["id"]
    finally:
        await store.close()


# ── 3b. 第二拍写成 ⇒ 同样有自我证据 ─────────────────────────────────
@pytest.mark.asyncio
async def test_second_beat_leaves_evidence(tmp_path: Path) -> None:
    """第二拍写成功 ⇒ 同样落"我写完了"并 supersede 旧记忆。"""
    backend = _FakeBackend(reply="我今晚就写。")
    chain = LLMChain(main=backend)  # type: ignore[arg-type]
    service, store, _workspace = await _service(tmp_path, chain)
    try:
        pid = int(
            await store.add_intention(
                T0 - 100, goal="爱莉希雅那篇", action="write_note", target=None, kind="promise"
            )
        )
        await record_promise_memory(store, pid, "爱莉希雅那篇", T0 - 100)
        await service.persist(await service.run_job(_job()), now=T0 + 1)
        await service.wait_forced_write()

        rows = await _memory_rows(store)
        done = [row for row in rows if "写完了" in str(row["content"])]
        assert done and "爱莉希雅.md" in str(done[0]["content"])
        old = [row for row in rows if str(row["source_ref"] or "") == f"promise:{pid}"]
        assert old and old[0]["superseded_by"] == done[0]["id"]
    finally:
        await store.close()


# ── 4. 正常回合 payload：只有她的原句，没有系统提醒字段 ─────────────
@pytest.mark.asyncio
async def test_payload_only_has_her_own_unfinished_things(tmp_path: Path) -> None:
    """payload 不含 `pending_promises*`；`unfinished_things` 只有她的原句、无工具名/祈使句。

    取消测试：把 payload 装配改回 `pending_promises` + `promise_hint`（或往
    `unfinished_things` 里塞系统话术）⇒ 本用例红。
    """
    llm = _FakeLLM("嗯。")
    service, store, _workspace = await _service(tmp_path, llm)
    try:
        pid = int(
            await store.add_intention(
                T0 - 100, goal="爱莉希雅那篇", action="write_note", target=None, kind="promise"
            )
        )
        await record_promise_memory(store, pid, "爱莉希雅那篇", T0 - 100)

        payload = (await service.run_job(_job())).instruction
        assert "pending_promises" not in payload
        assert "pending_promises_hint" not in payload
        things = payload.get("unfinished_things")
        assert isinstance(things, list) and things
        joined = "；".join(things)
        assert "我答应过要写" in joined
        for banned in ("write_note", "现在用", "你应该", "必须", "系统"):
            assert banned not in joined
        assert len(things) <= 3
        # 正常回合（<24h、之后没开口过）不出现兜底问句
        assert "promise_reminder" not in payload
    finally:
        await store.close()


# ── 5. 兜底提醒：24h 或之后开口 ≥2 次；12h 冷却 ─────────────────────
@pytest.mark.asyncio
async def test_fallback_reminder_after_24h_and_cooldown(tmp_path: Path) -> None:
    """>24h ⇒ 出现一次问句；同一承诺 12h 内不再出现（冷却）。"""
    llm = _FakeLLM("嗯。")
    service, store, _workspace = await _service(tmp_path, llm)
    try:
        pid = int(
            await store.add_intention(
                T0 - 25 * 3600,
                goal="爱莉希雅那篇",
                action="write_note",
                target=None,
                kind="promise",
            )
        )
        await record_promise_memory(store, pid, "爱莉希雅那篇", T0 - 25 * 3600)

        first = (await service.run_job(_job())).instruction
        reminder = first.get("promise_reminder")
        assert isinstance(reminder, str) and "这件还算数吗？" in reminder
        assert "不写也跟我说一声" in reminder
        # 换一个任务（同一承诺）：12h 冷却内不再出现
        second = (await service.run_job(_job())).instruction
        assert "promise_reminder" not in second
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_fallback_reminder_after_two_openings(tmp_path: Path) -> None:
    """承诺之后她已开口 ≥2 次仍未动 ⇒ 兜底问句（不等 24h）。"""
    llm = _FakeLLM("嗯。")
    service, store, _workspace = await _service(tmp_path, llm)
    try:
        pid = int(
            await store.add_intention(
                T0 - 100, goal="爱莉希雅那篇", action="write_note", target=None, kind="promise"
            )
        )
        await record_promise_memory(store, pid, "爱莉希雅那篇", T0 - 100)
        await store.think(T0 - 50, "expression", "嗯。")
        await store.think(T0 - 40, "expression", "好。")

        payload = (await service.run_job(_job())).instruction
        reminder = payload.get("promise_reminder")
        assert isinstance(reminder, str) and "这件还算数吗？" in reminder
    finally:
        await store.close()


# ── 6. 强制轮只认"即时意图" ────────────────────────────────────────
@pytest.mark.asyncio
async def test_forced_only_for_immediate_intent(tmp_path: Path) -> None:
    """「我去写」不强制；「我今晚就写」才强制（T-022a 收窄）。

    取消测试：去掉 `_maybe_force_write` 里的 `has_immediate_write_intent` 闸
    ⇒ 第一拍就强制（`tool_rounds == 1`，本用例红）。
    """
    backend = _FakeBackend()
    chain = LLMChain(main=backend)  # type: ignore[arg-type]
    service, store, _workspace = await _service(tmp_path, chain)
    try:
        await store.add_intention(
            T0 - 100, goal="爱莉希雅那篇", action="write_note", target=None, kind="promise"
        )
        # 第一拍：「我去写」——不是即时意图 ⇒ 不强制
        backend.reply = "我去写。"
        await service.persist(await service.run_job(_job()), now=T0 + 1)
        await service.wait_forced_write()
        assert backend.tool_rounds == 0, "『我去写』不该触发强制轮"

        # 第二拍：「我今晚就写」——即时意图 ⇒ 强制
        backend.reply = "我今晚就写。"
        await service.persist(await service.run_job(_job()), now=T0 + 2)
        await service.wait_forced_write()
        assert backend.tool_rounds == 1, "即时意图没有触发强制轮"
    finally:
        await store.close()
