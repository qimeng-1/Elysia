"""T-021：承诺第二拍——答应的事当场写完（强制 write_note 轮）。

用户硬要求（2026-10-22）：「答应的事就动手；答应了不做，比不做更糟。」

链路：一次开口落库（`settle_after_reply`）之后，若 ① 她还有 pending 的写作承诺、
② 本回合没真写、③ 她没明确说"不写了"，就当场再发一次 LLM 调用：只递
`write_note` 一个工具 + `tool_choice` 强制它，正文由她在工具参数里自己写。
成文 ⇒ promise `done`（outcome='她自己写的'）+ `thought_log kind='promise'`
`已成文：<path>`；失败 ⇒ 不标 done、attempts+1、10 分钟冷却、下一轮再试。

取消测试（每条都能红）：
- 去掉 `persist` 里的第二拍调用 ⇒ 用例 1 红（`tool_rounds` 0 且 promise 仍 pending）；
- 去掉 `is_abandonment` 入门闸 ⇒ 用例 2 红（她说了不写还会被强制写）；
- 去掉 `wrote` 入口闸 ⇒ 用例 4 红；
- 去掉 `record_intention_attempt` ⇒ 用例 5 红（attempts 不加）；
- 去掉 `next_forced_write_promise` 的冷却判据 ⇒ 用例 6 红（10 分钟内又调一次）。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.chain import WRITE_NOTE_TOOL, LLMChain
from elysia.llm.deepseek import DeepSeekBackend
from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import FORCED_WRITE_TOOL_CHOICE, ExpressionService
from elysia.soul.expression_types import STATUS_SUCCESS, ExpressionJob, ExpressionResult
from elysia.soul.promises import FORCED_WRITE_COOLDOWN_S
from elysia.soul.tools.writing import WritingWorkspace

T0 = 1_000_000.0
HER_TEXT = "她写的那一篇正文：爱莉希雅会笑，也会把心事藏起来。"


class _FakeBackend:
    """主声替身：常规开口走 `complete`；第二拍走 `complete_with_tools`（记录拿到什么）。

    `forced_text` 就是"她自己在工具参数里写的正文"——测试用它证明程序没有代写。
    `delay_s`：第二拍的假延迟（T-021b 用它证明 persist 从不等 LLM）。
    `forced_writes`：按顺序给出多次第二拍的（正文, 路径）——并发/多承诺用例用。
    `forced_gate`：单飞用例的确定性闸门（第二拍进入后先 set `forced_started`，再等它放行）。
    """

    def __init__(
        self,
        reply: str = "我去写。",
        *,
        forced_text: str | None = HER_TEXT,
        forced_path: str | None = "爱莉希雅.md",
        forced_raises: bool = False,
        delay_s: float = 0.0,
        forced_writes: list[tuple[str, str | None]] | None = None,
        forced_gate: asyncio.Event | None = None,
    ) -> None:
        self.reply = reply
        self.forced_text = forced_text
        self.forced_path = forced_path
        self.forced_raises = forced_raises
        self.delay_s = delay_s
        self.forced_writes = list(forced_writes or [])
        self.forced_gate = forced_gate
        self.forced_started = asyncio.Event()
        self.complete_calls = 0
        self.tool_rounds = 0
        self.seen_tools: list[list[dict[str, Any]]] = []
        self.seen_tool_choice: list[dict[str, Any] | None] = []
        self.seen_response_format: list[dict[str, Any] | None] = []
        self.seen_instructions: list[dict[str, Any]] = []

    async def complete(self, instruction: dict[str, Any], prompt_template: dict[str, Any]) -> str:
        self.complete_calls += 1
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
        self.seen_tools.append(tools)
        self.seen_tool_choice.append(tool_choice)
        self.seen_response_format.append(response_format)
        self.seen_instructions.append(instruction)
        self.forced_started.set()
        if self.forced_gate is not None:
            await self.forced_gate.wait()
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.forced_raises:
            raise RuntimeError("第二拍网络层炸了")
        if self.forced_writes:
            text, path = self.forced_writes.pop(0)
        else:
            text, path = self.forced_text, self.forced_path
        if text is None:
            return None  # 空正文：模型没有写
        args: dict[str, Any] = {"text": text}
        if path:
            args["path"] = path
        await run_tool("write_note", args)
        return "写好了，你去看看。"


def _job(user_message: str | None = None) -> ExpressionJob:
    return ExpressionJob(
        job_id="job-t021",
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
        job_id="job-t021",
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
    tmp_path: Path, backend: _FakeBackend
) -> tuple[ExpressionService, HeartbeatStore, WritingWorkspace]:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    workspace = WritingWorkspace(tmp_path / "home")
    chain = LLMChain(main=backend)  # type: ignore[arg-type]
    service = ExpressionService(chain, store, writing_workspace=workspace)
    return service, store, workspace


async def _pending_promise(store: HeartbeatStore, goal: str = "我这就写爱莉希雅那篇") -> int:
    return int(
        await store.add_intention(
            T0 - 100, goal=goal, action="write_note", target=None, kind="promise"
        )
    )


async def _turn(
    service: ExpressionService,
    *,
    reply: str,
    now: float,
    user_message: str | None = None,
    wrote: bool = False,
    wait_forced: bool = True,
) -> None:
    """真实链路：worker 段（run_job，一次 LLM 开口）→ 心跳落库段（persist）。

    T-021b 之后第二拍跑在后台任务里：默认等它结束（"这一回合真正走完"）；
    要验证 persist 立即返回/单飞时传 `wait_forced=False`。
    """
    backend = service._llm._main  # type: ignore[attr-defined]
    assert isinstance(backend, _FakeBackend)
    backend.reply = reply
    result = await service.run_job(_job(user_message))
    assert result.text, "第一拍没有说出话"
    if wrote:
        service._job_write_note_ok = True
    await service.persist(result, now=now)
    if wait_forced:
        await service.wait_forced_write()


# ── 1. 有 pending + 她说「我去写」+ 没写 ⇒ 第二拍强制 write_note ─────
@pytest.mark.asyncio
async def test_second_beat_forces_write_note_and_fulfills_promise(tmp_path: Path) -> None:
    """第二拍发生：tools 只有 write_note、tool_choice 强制它；成文后 promise done + 文件落盘。

    取消测试：去掉 `persist` 里的 `_maybe_force_write` 调用 ⇒ `tool_rounds == 0`、
    promise 仍 pending（本用例红）。
    """
    backend = _FakeBackend()
    service, store, workspace = await _service(tmp_path, backend)
    try:
        pid = await _pending_promise(store)
        await _turn(service, reply="我去写。", now=T0 + 1)

        assert backend.tool_rounds == 1, "有 pending 承诺、她答『我去写』，却没有补第二拍"
        assert [t["function"]["name"] for t in backend.seen_tools[0]] == ["write_note"]
        assert backend.seen_tool_choice[0] == {
            "type": "function",
            "function": {"name": "write_note"},
        }, "第二拍必须强制指定 write_note"
        assert backend.seen_instructions[0].get("directive"), "第二拍没有给她那一句指令"
        assert backend.seen_response_format[0] == {"type": "json_object"}, (
            "第二拍应尝试受约束输出（D-15；后端不支持会自动退化）"
        )

        intention = await store.get_intention(pid)
        assert intention is not None and intention["status"] == "done"
        assert intention["outcome"] == "她自己写的"
        logs = await store.execute_raw("SELECT text FROM thought_log WHERE kind = 'promise'")
        assert any("已成文：" in text and "爱莉希雅.md" in text for (text,) in logs)
        written = workspace.read_file("爱莉希雅.md")
        assert written.strip() == HER_TEXT.strip()
    finally:
        await store.close()


# ── 2. 她说「不写了」⇒ 不补第二拍，promise expired ─────────────────
@pytest.mark.asyncio
async def test_second_beat_skipped_when_she_says_no(tmp_path: Path) -> None:
    """明确拒绝 ⇒ 不补第二拍；最新那条承诺按 T-020 语义变 expired。

    这里刻意留一条**更早的** pending（放弃只关最新一条，旧条目仍在册）：
    第二拍绝不能拿它继续逼她写——拒绝就是拒绝。

    取消测试：去掉 `_maybe_force_write` 的 `is_abandonment` 入门闸 ⇒ 旧的 pending
    仍会被拿去强制写（`tool_rounds == 1`，本用例红）。
    """
    backend = _FakeBackend()
    service, store, _workspace = await _service(tmp_path, backend)
    try:
        older = await store.add_intention(
            T0 - 200, goal="早上说要写的那篇", action="write_note", target=None, kind="promise"
        )
        newer = await _pending_promise(store, goal="刚才说要写的那篇")
        await _turn(service, reply="不写了。", now=T0 + 1)
        assert backend.tool_rounds == 0, "她已明确说不写了，却还强制她写"
        newer_row = await store.get_intention(newer)
        older_row = await store.get_intention(older)
        assert newer_row is not None and newer_row["status"] == "expired"
        assert older_row is not None and older_row["status"] == "pending"
    finally:
        await store.close()


# ── 3. 没有 pending 承诺 ⇒ 永不补第二拍 ─────────────────────────────
@pytest.mark.asyncio
async def test_second_beat_never_without_pending_promise(tmp_path: Path) -> None:
    """没有 pending 承诺 ⇒ 第二拍永不发生（LLM 只被调一次 = 第一拍）。

    取消测试：把 `_maybe_force_write` 的"必须存在 pending 承诺"判据去掉
    （`next_forced_write_promise` 永远返回最新一条空承诺）⇒ 本用例红。
    """
    backend = _FakeBackend()
    service, store, _workspace = await _service(tmp_path, backend)
    try:
        await _turn(service, reply="我去写。", now=T0 + 1)
        assert backend.tool_rounds == 0, "没有 pending 承诺，却补了第二拍"
        assert backend.complete_calls == 1, "LLM 只该被调一次（第一拍）"
        rows = await store.execute_raw(
            "SELECT id FROM intentions WHERE kind = 'promise' AND status = 'pending'"
        )
        assert not rows
    finally:
        await store.close()


# ── 4. 本回合已真写 ⇒ 不补第二拍 ───────────────────────────────────
@pytest.mark.asyncio
async def test_second_beat_skipped_when_already_wrote(tmp_path: Path) -> None:
    """本回合已经 write_note 成功（wrote=True）⇒ 不补第二拍。

    近 24h 内的 pending 被 settle 标 done（outcome='她写了'）；刻意再留一条
    **30 小时前**的 pending（结算窗口外，仍在册）——本回合已经动过笔，就不该
    再拿旧承诺逼她写第二遍。

    取消测试：去掉 `_maybe_force_write` 的 `wrote` 入口闸 ⇒ 30h 前那条仍会被
    强制写（`tool_rounds == 1`，本用例红）。
    """
    backend = _FakeBackend()
    service, store, workspace = await _service(tmp_path, backend)
    try:
        pid = await _pending_promise(store)
        old = await store.add_intention(
            T0 - 30 * 3600,
            goal="更早以前说要写的那篇",
            action="write_note",
            target=None,
            kind="promise",
        )
        await _turn(service, reply="写好了，你看看。", now=T0 + 1, wrote=True)
        assert backend.tool_rounds == 0, "本回合已经真写了，却又补第二拍"
        intention = await store.get_intention(pid)
        assert intention is not None and intention["status"] == "done"
        assert intention["outcome"] == "她写了"
        old_row = await store.get_intention(int(old))
        assert old_row is not None and old_row["status"] == "pending"
        assert not any(workspace.root.glob("**/*")), "本回合不该再有第二拍写出的文件"
    finally:
        await store.close()


# ── 5. 强制轮失败 ⇒ pending 不变、attempts+1、不抛异常 ─────────────
@pytest.mark.asyncio
async def test_second_beat_failure_keeps_pending_and_counts_attempt(tmp_path: Path) -> None:
    """强制轮抛错/空正文 ⇒ promise 仍 pending、attempts+1、异常不外溢。

    取消测试：去掉失败分支的 `record_intention_attempt` ⇒ attempts 停在 0（本用例红）。
    """
    backend = _FakeBackend(forced_raises=True)
    service, store, _workspace = await _service(tmp_path, backend)
    try:
        pid = await _pending_promise(store)
        await _turn(service, reply="我去写。", now=T0 + 1)
        intention = await store.get_intention(pid)
        assert intention is not None and intention["status"] == "pending"
        assert intention["attempts"] == 1
        assert intention["last_attempt_ts"] == T0 + 1
    finally:
        await store.close()

    # 空正文（模型没给 text）同样只记一次尝试，不标 done
    backend2 = _FakeBackend(forced_text=None)
    service2, store2, _workspace2 = await _service(tmp_path / "empty", backend2)
    try:
        pid2 = await _pending_promise(store2)
        await _turn(service2, reply="我去写。", now=T0 + 1)
        intention2 = await store2.get_intention(pid2)
        assert intention2 is not None and intention2["status"] == "pending"
        assert intention2["attempts"] == 1
    finally:
        await store2.close()


# ── 6. 冷却：同一 promise 10 分钟内只尝试一次 ───────────────────────
@pytest.mark.asyncio
async def test_second_beat_cooldown_10min(tmp_path: Path) -> None:
    """同一 promise 两次强制尝试间隔 ≥10 分钟；冷却外才再试。

    取消测试：去掉 `next_forced_write_promise` 的 `last_attempt_ts` 冷却判据 ⇒
    第二次 persist（T0+2）就会再调一次（本用例红）。
    """
    backend = _FakeBackend(forced_raises=True)
    service, store, _workspace = await _service(tmp_path, backend)
    try:
        pid = await _pending_promise(store)
        await _turn(service, reply="我去写。", now=T0 + 1)
        assert backend.tool_rounds == 1

        # 冷却内：她的下一句再提"我去写"，也不补第二拍
        await _turn(service, reply="我去写了。", now=T0 + 2)
        assert backend.tool_rounds == 1, "10 分钟冷却内又强制了一次"

        # 冷却外：允许再试；这次成功 ⇒ done
        backend.forced_raises = False
        later = T0 + 1 + FORCED_WRITE_COOLDOWN_S + 1.0
        await _turn(service, reply="我去写。", now=later)
        assert backend.tool_rounds == 2, "冷却结束后必须可以再试一次"
        intention = await store.get_intention(pid)
        assert intention is not None and intention["status"] == "done"
    finally:
        await store.close()


# ── 7. 第二拍绝不代写正文 ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_second_beat_writes_her_own_text_only(tmp_path: Path) -> None:
    """落盘的正文 == 她那次 LLM 工具参数里的 text（不是程序常量/承诺原文）。

    取消测试：把 `_make_forced_write_runner` 的 text 换成程序常量
    （如 `forced_write_prompt(goal)`）⇒ 文件内容与本用例断言不符（红）。
    """
    backend = _FakeBackend()
    service, store, workspace = await _service(tmp_path, backend)
    try:
        pid = await _pending_promise(store, goal="我这就写爱莉希雅到底是个什么样的人")
        await _turn(service, reply="我去写。", now=T0 + 1)

        written = workspace.read_file("爱莉希雅.md").strip()
        assert written == HER_TEXT.strip(), "写进去的正文不是她自己写的那一段"
        assert "我这就写爱莉希雅到底是个什么样的人" not in written, "程序把承诺原文当正文写了"
        assert "你答应过要写" not in written, "程序把提示词当正文写了"
        intention = await store.get_intention(pid)
        assert intention is not None and intention["status"] == "done"
    finally:
        await store.close()


# ── 后端层：第二拍的 HTTP payload（tools 只一个 / tool_choice 强制）──
@pytest.mark.asyncio
async def test_deepseek_forced_payload_has_only_write_note_and_choice() -> None:
    """强制轮的 payload：`tools` 只有 write_note、`tool_choice` 指定它。

    取消测试：把 `_payload` 里的 `tool_choice` 写回删掉 ⇒ 断言红。
    """
    backend = DeepSeekBackend("test-key", require_key=False)
    payloads: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = [
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "write_note",
                                    "arguments": json.dumps(
                                        {"text": HER_TEXT, "path": "爱莉希雅.md"},
                                        ensure_ascii=False,
                                    ),
                                },
                            }
                        ],
                    },
                }
            ]
        },
        {"choices": [{"finish_reason": "stop", "message": {"content": "写好了。"}}]},
    ]

    def _post(payload: dict[str, Any]) -> dict[str, Any]:
        payloads.append(payload)
        return replies.pop(0)

    seen: list[tuple[str, dict[str, Any]]] = []

    async def _run_tool(name: str, args: dict[str, Any]) -> str:
        seen.append((name, args))
        return "（已写进你的文件夹：爱莉希雅.md；本次 10 字，文件 30 字节）"

    backend._post = _post  # type: ignore[method-assign]
    text = await backend.complete_with_tools(
        {"directive": "写吧"},
        {"name": "default"},
        tools=[WRITE_NOTE_TOOL],
        run_tool=_run_tool,
        tool_choice=FORCED_WRITE_TOOL_CHOICE,
    )
    assert payloads[0]["tools"] == [WRITE_NOTE_TOOL]
    assert payloads[0]["tool_choice"] == FORCED_WRITE_TOOL_CHOICE
    assert seen == [("write_note", {"text": HER_TEXT, "path": "爱莉希雅.md"})]
    assert text == "写好了。"
    assert "tool_choice" not in payloads[1], "工具回执回来后不该继续强制调用"


# ── 后端层：常规开口（tool_choice=None）行为零变化 ──────────────────
@pytest.mark.asyncio
async def test_deepseek_regular_tool_round_has_no_tool_choice() -> None:
    """常规工具回合不许带 `tool_choice`（T-021 只影响第二拍）。"""
    backend = DeepSeekBackend("test-key", require_key=False)
    payloads: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = [
        {"choices": [{"finish_reason": "stop", "message": {"content": "嗯。"}}]},
    ]

    def _post(payload: dict[str, Any]) -> dict[str, Any]:
        payloads.append(payload)
        return replies.pop(0)

    async def _run_tool(name: str, args: dict[str, Any]) -> str:
        return "（好的）"

    backend._post = _post  # type: ignore[method-assign]
    await backend.complete_with_tools(
        {"intent": "回应"},
        {"name": "default"},
        tools=[WRITE_NOTE_TOOL],
        run_tool=_run_tool,
    )
    assert "tools" in payloads[0]
    assert "tool_choice" not in payloads[0]


# ── 提示语文案：提醒从"你可以"改成"现在就动手" ──────────────────────
def test_promise_hint_tells_her_to_act_now() -> None:
    from elysia.soul.promises import promise_hint

    hint = promise_hint(["我这就写爱莉希雅"])
    assert "现在就用 write_note 写完它" in hint
    assert "不想写就明说不写了" in hint
    assert "你可以用 write_note" not in hint


def test_forced_write_cooldown_is_ten_minutes() -> None:
    assert FORCED_WRITE_COOLDOWN_S == 600.0


# ══════════════════════════════════════════════════════════════════════
# T-021b：第二拍移出心跳拍（persist 只投递；后台单飞）
#
# 取消测试（每条都能红）：
# - 把 `_maybe_force_write` 改回同步 await ⇒ 用例 1 红（persist 被 1s 延迟拖住）；
# - 去掉单飞判据（每次都 create_task）⇒ 用例 3 红（后端被调 2 次）；
# - 去掉任务内 try/except ⇒ 用例 4 红（task.exception() 非 None）；
# - 第二拍改回复用作业级 `_job_write_note_ok`/`_job_written_path` ⇒ 用例 5 红。
# ══════════════════════════════════════════════════════════════════════


async def _pending_result(
    service: ExpressionService, *, reply: str = "我去写。", now: float = T0 + 1
) -> ExpressionResult:
    backend = service._llm._main  # type: ignore[attr-defined]
    assert isinstance(backend, _FakeBackend)
    backend.reply = reply
    result = await service.run_job(_job())
    assert result.text
    return result


# ── 1. persist 立即返回（不等第二拍）────────────────────────────────
@pytest.mark.asyncio
async def test_persist_returns_immediately_while_second_beat_runs(tmp_path: Path) -> None:
    """假后端给第二拍 1s 延迟 ⇒ persist 用时 <0.2s，随后能等到任务完成。

    取消测试：把 `_maybe_force_write` 改回同步 `await speaker(...)` ⇒ persist 被拖
    1s（本用例红）。
    """
    backend = _FakeBackend(delay_s=1.0)
    service, store, workspace = await _service(tmp_path, backend)
    try:
        pid = await _pending_promise(store)
        result = await _pending_result(service)

        started = time.monotonic()
        await service.persist(result, now=T0 + 1)
        elapsed = time.monotonic() - started
        assert elapsed < 0.2, f"persist 在等第二拍：{elapsed:.2f}s（心跳会被拖住）"
        assert service.forced_write_task is not None, "第二拍没有被投递"

        await service.wait_forced_write()
        intention = await store.get_intention(pid)
        assert intention is not None and intention["status"] == "done"
        written = workspace.read_file("爱莉希雅.md")
        assert written.strip() == HER_TEXT.strip()
    finally:
        await store.close()


# ── 2. 后台任务可被 await（文件落盘 + promise done）─────────────────
@pytest.mark.asyncio
async def test_background_task_handle_can_be_awaited(tmp_path: Path) -> None:
    """用可注入的 task 句柄等第二拍（不 sleep 猜）：文件落盘、promise done。"""
    backend = _FakeBackend(delay_s=0.05)
    service, store, workspace = await _service(tmp_path, backend)
    try:
        pid = await _pending_promise(store)
        result = await _pending_result(service)
        await service.persist(result, now=T0 + 1)
        task = service.forced_write_task
        assert task is not None
        await task  # 直接 await 句柄，不 sleep
        assert task.done()
        intention = await store.get_intention(pid)
        assert intention is not None and intention["status"] == "done"
        assert workspace.read_file("爱莉希雅.md").strip() == HER_TEXT.strip()
    finally:
        await store.close()


# ── 3. 单飞：连续两次触发只起一个任务 ───────────────────────────────
@pytest.mark.asyncio
async def test_single_flight_only_one_second_beat_at_a_time(tmp_path: Path) -> None:
    """第二拍在跑时又触发一次 ⇒ 跳过（不排队、不堆积）：后端只被调 1 次。

    用 `asyncio.Event` 闸门让"第一拍还在跑"成为确定状态（不靠 sleep 猜时序）。

    取消测试：去掉 `_maybe_force_write` 里"已有任务在跑就跳过"的判据 ⇒ 第二个任务
    也会跑（`tool_rounds == 2`，本用例红）。
    """
    gate = asyncio.Event()
    backend = _FakeBackend(forced_gate=gate)
    service, store, _workspace = await _service(tmp_path, backend)
    try:
        await _pending_promise(store)
        await _turn(service, reply="我去写。", now=T0 + 1, wait_forced=False)
        await backend.forced_started.wait()  # 第一拍确定已经在跑（卡在闸门上）
        task = service.forced_write_task
        assert task is not None and not task.done()

        await _turn(service, reply="我去写。", now=T0 + 2, wait_forced=False)
        assert backend.tool_rounds == 1, "第二拍没有单飞（又起了一个任务）"

        gate.set()
        await service.wait_forced_write()
        assert backend.tool_rounds == 1, "第二拍没有单飞（触发了多次）"
    finally:
        gate.set()
        await store.close()


# ── 4. 后台任务抛异常 ⇒ 不影响 persist / 后续心跳 ────────────────────
@pytest.mark.asyncio
async def test_background_failure_does_not_leak(tmp_path: Path) -> None:
    """假后端在第二拍抛错 ⇒ persist 正常返回、任务异常被吞、后续 tick 正常。"""
    backend = _FakeBackend(forced_raises=True)
    service, store, _workspace = await _service(tmp_path, backend)
    try:
        pid = await _pending_promise(store)
        result = await _pending_result(service)
        await service.persist(result, now=T0 + 1)  # 不抛
        task = service.forced_write_task
        assert task is not None
        await service.wait_forced_write()
        assert task.done()
        assert task.exception() is None, "后台任务异常没有被吞掉"

        intention = await store.get_intention(pid)
        assert intention is not None and intention["status"] == "pending"
        assert intention["attempts"] == 1

        # 后续心跳拍照常：再跑一次开口不炸
        next_result = await service.run_job(_job())
        assert next_result.text
        await service.persist(next_result, now=T0 + 30)
    finally:
        await store.close()


# ── 5. 并发安全：第二拍用独立上下文，不碰作业级字段 ─────────────────
@pytest.mark.asyncio
async def test_second_beat_context_does_not_touch_job_state(tmp_path: Path) -> None:
    """两条不同 pending 承诺先后成文 ⇒ 各自写对、互不覆盖，作业级字段不被污染。

    取消测试：把 `_forced_write_note` 改回用 `self._job_write_note_ok` /
    `self._job_written_path` 记录 ⇒ 本用例对"作业级字段仍为 False/None"的断言红。
    """
    backend = _FakeBackend(
        forced_writes=[("A 的正文：早上那篇", "a.md"), ("B 的正文：刚才那篇", "b.md")]
    )
    service, store, workspace = await _service(tmp_path, backend)
    try:
        older = await store.add_intention(
            T0 - 200, goal="早上说要写的那篇", action="write_note", target=None, kind="promise"
        )
        newer = await _pending_promise(store, goal="刚才说要写的那篇")

        await _turn(service, reply="我去写。", now=T0 + 1)  # 先补旧的
        await _turn(service, reply="我去写。", now=T0 + 2)  # 再补新的

        assert backend.tool_rounds == 2
        older_row = await store.get_intention(int(older))
        newer_row = await store.get_intention(int(newer))
        assert older_row is not None and older_row["status"] == "done"
        assert newer_row is not None and newer_row["status"] == "done"
        assert workspace.read_file("a.md").strip() == "A 的正文：早上那篇"
        assert workspace.read_file("b.md").strip() == "B 的正文：刚才那篇"
        # 第二拍绝不写作业级状态（它属于心跳拍上的"这一次开口"）
        assert service._job_write_note_ok is False
        assert service._job_written_path is None
    finally:
        await store.close()
