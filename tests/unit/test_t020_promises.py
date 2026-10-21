"""T-020（N1）：她说"我这就写"→ 记成她自己的承诺 → 下一次开口提醒 → 兑现/关闭。

原则：系统绝不代写、绝不计配额、绝不改她的话。写、推迟、说不写了，三种都合法。
取消测试（每条都能红）：
- 去掉捕获 ⇒ 用例 1 红；去掉"已真写就不记" ⇒ 用例 2 红；
- 去掉 payload 提醒 ⇒ 用例 3 红；去掉"成功写 ⇒ done" ⇒ 用例 4 红；
- 去掉 48h 惰性过期 ⇒ 用例 5 红；去掉 worker 跳过 promies ⇒ 用例 6 红。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import (
    STATUS_SUCCESS,
    ExpressionJob,
    ExpressionResult,
)
from elysia.soul.intentions import Intentions
from elysia.soul.promises import (
    detect_promise,
    is_abandonment,
)
from elysia.soul.tools.writing import WritingWorkspace

T0 = 1_000_000.0


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text

    async def speak(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            text=self.text,
            level="main",
            validated=ValidationResult(ok=True, reason=None),
        )


def _job(text: str | None = None) -> ExpressionJob:
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
        user_message=text,
        intent="回应",
    )


def _result(text: str) -> ExpressionResult:
    return ExpressionResult(
        job_id="job-1",
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
    tmp_path: Path, *, text: str = "嗯。"
) -> tuple[ExpressionService, HeartbeatStore, StateStore]:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    state = StateStore(tmp_path / "state.db")
    await store.start()
    await state.start()
    service = ExpressionService(
        _FakeLLM(text),  # type: ignore[arg-type]
        store,
        writing_workspace=WritingWorkspace(tmp_path / "home"),
    )
    return service, store, state


# ── 1. 捕获 ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_capture_promise_after_reply(tmp_path: Path) -> None:
    service, store, state = await _service(tmp_path)
    try:
        await service.persist(_result("我这就写下来给你看。"), now=T0 + 1)
        rows = await store.execute_raw(
            "SELECT goal, action, kind, status, target FROM intentions ORDER BY id DESC LIMIT 1"
        )
        assert rows, "说出口的承诺没有被记下来"
        goal, action, kind, status, target = rows[0]
        assert kind == "promise" and status == "pending" and action == "write_note"
        assert target is None
        assert "我这就写下来给你看" in goal
        promise_logs = await store.execute_raw(
            "SELECT text FROM thought_log WHERE kind = 'promise'"
        )
        assert promise_logs and "我这就写下来给你看" in promise_logs[0][0]
    finally:
        await state.close()
        await store.close()


# ── 2. 已真写 ⇒ 不记 ─────────────────────────────────────
@pytest.mark.asyncio
async def test_no_capture_when_write_note_succeeded(tmp_path: Path) -> None:
    service, store, state = await _service(tmp_path)
    try:
        service._job_write_note_ok = True
        await service.persist(_result("我这就写下来给你看。"), now=T0 + 1)
        assert not [
            row
            for row in await store.execute_raw("SELECT kind FROM intentions")
            if row[0] == "promise"
        ], "已经真写了，却还记了一条承诺"
    finally:
        await state.close()
        await store.close()


# ── 3. payload 提醒（≤3、无 id） ─────────────────────────
@pytest.mark.asyncio
async def test_payload_carries_pending_promises(tmp_path: Path) -> None:
    service, store, state = await _service(tmp_path, text="嗯。")
    try:
        await store.add_intention(
            T0 - 100,
            goal="我待会儿把那篇写完给你看",
            action="write_note",
            target=None,
            kind="promise",
        )
        result = await service.run_job(_job("在吗"))
        promises = result.instruction.get("pending_promises")
        assert isinstance(promises, list) and promises
        assert any("我待会儿把那篇写完给你看" in item for item in promises)
        assert all(isinstance(item, str) for item in promises)
        hint = result.instruction.get("pending_promises_hint")
        assert isinstance(hint, str) and "不写" in hint and "write_note" in hint
        assert len(promises) <= 3
    finally:
        await state.close()
        await store.close()


# ── 4. 成功写 ⇒ done ─────────────────────────────────────
@pytest.mark.asyncio
async def test_successful_write_closes_promise_done(tmp_path: Path) -> None:
    service, store, state = await _service(tmp_path)
    try:
        pid = await store.add_intention(
            T0 - 100, goal="我这就写下来", action="write_note", target=None, kind="promise"
        )
        service._job_write_note_ok = True
        await service.persist(_result("写好了，你看看。"), now=T0 + 1)
        intention = await store.get_intention(int(pid))
        assert intention is not None
        assert intention["status"] == "done"
        assert intention["outcome"] == "她写了"
        assert intention["finished_ts"] == T0 + 1
    finally:
        await state.close()
        await store.close()


# ── 5. 48h 惰性过期 ─────────────────────────────────────
@pytest.mark.asyncio
async def test_promises_expire_lazily_after_48h(tmp_path: Path) -> None:
    _service_unused, store, state = await _service(tmp_path)
    try:
        old = await store.add_intention(
            T0 - 49 * 3600,
            goal="很久以前说要写",
            action="write_note",
            target=None,
            kind="promise",
        )
        fresh = await store.add_intention(
            T0 - 47 * 3600,
            goal="还不到 48 小时",
            action="write_note",
            target=None,
            kind="promise",
        )
        # 24h 提醒窗口外（47h）不会出现在 payload；但“48h 惰性过期”只按 48h 判定
        await store.expire_stale_promises(T0)
        old_row = await store.get_intention(int(old))
        fresh_row = await store.get_intention(int(fresh))
        assert old_row is not None and old_row["status"] == "expired"
        assert fresh_row is not None and fresh_row["status"] == "pending"
    finally:
        await state.close()
        await store.close()


# ── 6. Worker 跳过 promise ───────────────────────────────
@pytest.mark.asyncio
async def test_worker_does_not_execute_promises(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    calls: list[str] = []
    try:
        await store.add_intention(
            T0, goal="我这就写", action="write_note", target="x.md", kind="promise"
        )

        async def _read_url(_url: str) -> Any:
            calls.append("read")
            return None

        intentions = Intentions(store, read_url_fn=_read_url)  # type: ignore[arg-type]
        assert await intentions.run_once(T0 + 1) is None
        assert calls == [], "worker 把她的口头承诺当任务执行了"
        pending = await store.get_intention(1)
        assert pending is not None and pending["status"] == "pending"
    finally:
        await store.close()


# ── 明确放弃 ⇒ expired ───────────────────────────────────
@pytest.mark.asyncio
async def test_abandonment_expires_promise(tmp_path: Path) -> None:
    service, store, state = await _service(tmp_path)
    try:
        pid = await store.add_intention(
            T0 - 100, goal="我这就写下来", action="write_note", target=None, kind="promise"
        )
        await service.persist(_result("算了，不写了。"), now=T0 + 1)
        intention = await store.get_intention(int(pid))
        assert intention is not None
        assert intention["status"] == "expired"
        assert intention["outcome"] == "她自己说不写了"
    finally:
        await state.close()
        await store.close()


def test_detect_promise_and_abandonment() -> None:
    assert detect_promise("我这就写下来给你看。") is not None
    assert detect_promise("我待会儿写好给你") is not None
    assert detect_promise("今天天气不错。") is None
    assert is_abandonment("算了，不写了。") is True
    assert is_abandonment("我这就写") is False
