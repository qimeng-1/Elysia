"""P3：意图 + 动手——写下打算、后台真去做、下次能讲。

用户拍板：「批准，且需要**动手能力**」「**不规定次数**」。
运行逻辑只有三条技术护栏：**并发 1 / 单次 8s 超时 / 连续失败 3 次转 failed**；
**没有**"每天只能做 N 件"的配额（本文件用断言把这条守住）。
P1b 联动：`surprise/conceal/play` 的**待办**意图是撒谎动机；意图完成后不再放行
（旧动机不永久免疫）。

取消测试：去掉执行器调用 ⇒ `test_executor_read_url` 红；去掉 3 次熔断 ⇒
`test_failure_attempts_and_fuse` 红；动机查询改成含 done ⇒ P1b 后半红。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.memory.levels import KIND_WORLD
from elysia.soul import intentions as intentions_module
from elysia.soul.expression_service import ExpressionService
from elysia.soul.intentions import (
    INTENTION_MAX_ATTEMPTS,
    INTENTION_MAX_CONCURRENCY,
    INTENTION_TIMEOUT_S,
    Intentions,
    IntentionWorker,
    validate_intention,
)
from elysia.soul.tools.read_url import ReadResult
from elysia.soul.tools.writing import WritingWorkspace

T0 = 1_000_000.0
URL = "https://zh.wikipedia.org/wiki/埃舍尔"
TEXT = "《埃舍尔》是荷兰版画艺术家，以不可能图形闻名。"
SOURCE = "维基百科·埃舍尔"

CLAIM = "我读了维基上关于埃舍尔的页面。今天天气不错。"
# T-024：整轮判定生效后，无据的跨句阅读主张整轮换成单句诚实句
HONEST = "其实我还没真的去翻过呢。"


def _reader(ok: bool = True, *, delay: float = 0.0) -> Any:
    def _read(_url: str) -> ReadResult:
        if delay:
            time.sleep(delay)
        if not ok:
            return ReadResult(ok=False, kind="error", reason="HTTP 500")
        return ReadResult(ok=True, kind="article", text=TEXT, source=SOURCE, final_url=URL)

    return _read


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text

    async def speak(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            text=self.text,
            level="main",
            validated=ValidationResult(ok=True, reason=None, sanitized=None),
        )


def _job(user_message: str | None = None):
    from elysia.soul.brain import BrainOutput, WillOutput
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState
    from elysia.soul.expression_types import ExpressionJob

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


# ── 验收 1：声明（合法/非法）──────────────────────────────
@pytest.mark.asyncio
async def test_set_intention_creates_pending(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        intentions = Intentions(store, read_url_fn=_reader())
        receipt = await intentions.set_intention(
            goal="我想去看看维基上关于埃舍尔的页面",
            action="read_url",
            target=URL,
            kind="task",
            now=T0,
        )
        assert "记下了" in receipt
        pending = await store.next_pending_intention()
        assert pending is not None
        assert pending["goal"] == "我想去看看维基上关于埃舍尔的页面"
        assert pending["action"] == "read_url"
        assert pending["target"] == URL
        assert pending["kind"] == "task"
        assert pending["status"] == "pending"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_set_intention_rejects_invalid(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        intentions = Intentions(store, read_url_fn=_reader())
        cases = [
            ("", "read_url", URL, "task"),  # 空 goal
            ("字" * 121, "read_url", URL, "task"),  # 超长
            ("想读", "browse", URL, "task"),  # 未知 action
            ("想读", "read_url", URL, "lie"),  # 未知 kind
            ("想读", "read_url", None, "task"),  # read_url 缺 target
        ]
        for goal, action, target, kind in cases:
            receipt = await intentions.set_intention(
                goal=goal, action=action, target=target, kind=kind, now=T0
            )
            assert receipt.startswith("（"), receipt
        assert await store.pending_intention_count() == 0

        # 纯函数校验也可直接断言
        assert isinstance(validate_intention("想读", "read_url", None, "task"), str)
    finally:
        await store.close()


# ── 验收 2/3：执行 read_url / write_note ───────────────────
@pytest.mark.asyncio
async def test_executor_read_url(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        intentions = Intentions(store, read_url_fn=_reader())
        await intentions.set_intention(
            goal="想读埃舍尔", action="read_url", target=URL, kind="task", now=T0
        )
        with caplog.at_level(logging.INFO):
            result = await intentions.run_once(T0 + 5)
        assert result is not None and result["status"] == "done"
        intention = await store.get_intention(int(result["id"]))
        assert intention is not None
        assert intention["status"] == "done"
        assert intention["outcome"]
        worlds = [m for m in await store.iterate_memories() if m["kind"] == KIND_WORLD]
        assert len(worlds) == 1
        assert TEXT in worlds[0]["content"]
        assert any("intention_action" in r.getMessage() for r in caplog.records)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_executor_write_note(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    workspace = WritingWorkspace(tmp_path / "writing")
    try:
        intentions = Intentions(store, read_url_fn=_reader(), writing=workspace)
        await intentions.set_intention(
            goal="把今天的想法写下来",
            action="write_note",
            target="today.md",
            kind="task",
            now=T0,
        )
        result = await intentions.run_once(T0 + 5)
        assert result is not None and result["status"] == "done"
        assert (tmp_path / "writing" / "today.md").exists()
    finally:
        await store.close()


# ── 验收 4：失败计数 + 3 次熔断 / 超时 ─────────────────────
@pytest.mark.asyncio
async def test_failure_attempts_and_fuse(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        intentions = Intentions(store, read_url_fn=_reader(ok=False))
        await intentions.set_intention(
            goal="读不到也要试", action="read_url", target=URL, kind="task", now=T0
        )
        for attempt in (1, 2):
            result = await intentions.run_once(T0 + attempt)
            assert result is not None and result["status"] == "pending"
            intention = await store.get_intention(int(result["id"]))
            assert intention is not None
            assert intention["attempts"] == attempt
            assert intention["status"] == "pending"

        result = await intentions.run_once(T0 + 3)
        assert result is not None
        intention = await store.get_intention(int(result["id"]))
        assert intention is not None
        assert intention["attempts"] == INTENTION_MAX_ATTEMPTS == 3
        assert intention["status"] == "failed"
        assert "读不到" in str(intention["outcome"])

        # 熔断后不再重试
        assert await intentions.run_once(T0 + 4) is None
        assert (await store.get_intention(int(intention["id"])))["attempts"] == 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_timeout_is_a_failure(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        intentions = Intentions(store, read_url_fn=_reader(delay=0.05), timeout_s=0.01)
        await intentions.set_intention(
            goal="太慢了", action="read_url", target=URL, kind="task", now=T0
        )
        result = await intentions.run_once(T0 + 1)
        assert result is not None and result["status"] == "pending"
        intention = await store.get_intention(int(result["id"]))
        assert intention is not None
        assert intention["attempts"] == 1
        assert "超时" in str(intention["outcome"])
    finally:
        await store.close()


# ── 验收 5：并发 ≤ 1 ──────────────────────────────────────
@pytest.mark.asyncio
async def test_concurrency_is_one(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        active = 0
        max_active = 0

        def _slow_reader(_url: str) -> ReadResult:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            time.sleep(0.02)
            active -= 1
            return ReadResult(ok=True, kind="article", text=TEXT, source=SOURCE, final_url=URL)

        intentions = Intentions(store, read_url_fn=_slow_reader)
        for index in range(2):
            await intentions.set_intention(
                goal=f"第{index}件", action="read_url", target=URL, kind="task", now=T0
            )
        results = await asyncio.gather(intentions.run_once(T0 + 1), intentions.run_once(T0 + 2))
        assert all(result is not None for result in results)
        assert max_active == 1, "同时只能有一个动作在执行"
        assert INTENTION_MAX_CONCURRENCY == 1
        assert await store.pending_intention_count() == 0
    finally:
        await store.close()


# ── 验收 6：recent_discoveries 注入 ────────────────────────
@pytest.mark.asyncio
async def test_recent_discoveries_in_payload(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        intentions = Intentions(store, read_url_fn=_reader())
        await intentions.set_intention(
            goal="想读埃舍尔", action="read_url", target=URL, kind="task", now=T0
        )
        await intentions.run_once(T0 + 5)
        service = ExpressionService(  # type: ignore[arg-type]
            _FakeLLM("嗯。"), store, intentions=intentions
        )
        result = await service.run_job(_job(user_message="在吗"))
        discoveries = result.instruction.get("recent_discoveries")
        assert isinstance(discoveries, list) and discoveries
        assert any("想读埃舍尔" in item for item in discoveries)
        assert any("埃舍尔" in item for item in discoveries)

        # 没有发现时是 []，不是 None（换一个空库）
        empty_store = HeartbeatStore(tmp_path / "empty.db")
        await empty_store.start()
        try:
            empty_intentions = Intentions(empty_store, read_url_fn=_reader())
            empty_service = ExpressionService(  # type: ignore[arg-type]
                _FakeLLM("嗯。"), empty_store, intentions=empty_intentions
            )
            empty = await empty_service.run_job(_job(user_message="在吗"))
            assert empty.instruction.get("recent_discoveries") == []
        finally:
            await empty_store.close()
    finally:
        await store.close()


# ── 验收 7：P1b 联动（动机只在该意图存在期间有效）──────────
@pytest.mark.asyncio
async def test_motive_only_while_intention_pending(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        intention_id = await store.add_intention(
            T0, goal="想给她一个惊喜", action="write_note", target=None, kind="surprise"
        )
        service = ExpressionService(_FakeLLM(CLAIM), store)  # type: ignore[arg-type]
        with caplog.at_level(logging.INFO):
            allowed = await service.run_job(_job(user_message="你在干嘛"))
        assert allowed.text == CLAIM, "pending surprise 意图存在 ⇒ 有动机，放行"
        assert any("claim_fiction_allowed" in r.getMessage() for r in caplog.records)

        # 意图完成后不再放行（旧动机不永久免疫）
        await store.finish_intention(intention_id, T0 + 1, "done", "已经给她惊喜了")
        rewritten = await service.run_job(_job(user_message="你在干嘛"))
        assert rewritten.text == HONEST, "意图 done 后旧动机失效，必须改写成诚实"
    finally:
        await store.close()


# ── 没有"每天 N 件"的配额（用户明确否决）──────────────────
@pytest.mark.asyncio
async def test_no_daily_quota(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        intentions = Intentions(store, read_url_fn=_reader())
        for index in range(20):
            await intentions.set_intention(
                goal=f"今天第{index}件", action="read_url", target=URL, kind="task", now=T0
            )
            result = await intentions.run_once(T0 + index)
            assert result is not None and result["status"] == "done"
        assert await store.pending_intention_count() == 0

        # 运行逻辑里不许出现"每天/每小时做几件"的配额常量
        quota_names = [
            name
            for name in dir(intentions_module)
            if "DAILY" in name or "QUOTA" in name or "PER_DAY" in name
        ]
        assert quota_names == [], f"不许有行为配额：{quota_names}"
        # 技术护栏恰好这三条
        assert INTENTION_MAX_CONCURRENCY == 1
        assert INTENTION_TIMEOUT_S == 8.0
        assert INTENTION_MAX_ATTEMPTS == 3
    finally:
        await store.close()


# ── 工具接线 ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_set_intention_tool_wiring(tmp_path: Path) -> None:
    from elysia.llm.chain import SET_INTENTION_TOOL
    from elysia.llm.deepseek import _TOOL_NAMES
    from elysia.soul.brain import BrainOutput

    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        intentions = Intentions(store, read_url_fn=_reader())
        service = ExpressionService(  # type: ignore[arg-type]
            _FakeLLM("嗯。"), store, intentions=intentions
        )
        runner = service._make_tool_runner(BrainOutput(), now=T0, user_message=None)
        receipt = await runner(
            "set_intention",
            {"goal": "想读埃舍尔", "action": "read_url", "target": URL, "kind": "task"},
        )
        assert "记下了" in receipt
        pending = await store.next_pending_intention()
        assert pending is not None and pending["goal"] == "想读埃舍尔"
        assert SET_INTENTION_TOOL["function"]["name"] == "set_intention"
        assert "set_intention" in _TOOL_NAMES

        bad = await runner("set_intention", {"goal": "", "action": "read_url"})
        assert bad.startswith("（")
    finally:
        await store.close()


# ── 后台 worker 可启停（不是心跳拍）─────────────────────────
@pytest.mark.asyncio
async def test_worker_polls_and_executes(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        intentions = Intentions(store, read_url_fn=_reader())
        await intentions.set_intention(
            goal="后台去读", action="read_url", target=URL, kind="task", now=T0
        )
        worker = IntentionWorker(intentions, clock=lambda: T0 + 1, poll_s=0.01)
        worker.start()
        try:
            for _ in range(100):
                await asyncio.sleep(0.01)
                if await store.pending_intention_count() == 0:
                    break
        finally:
            await worker.aclose()
        assert await store.pending_intention_count() == 0
        worlds = [m for m in await store.iterate_memories() if m["kind"] == KIND_WORLD]
        assert len(worlds) == 1
    finally:
        await store.close()
