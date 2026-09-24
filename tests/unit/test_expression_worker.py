"""表达 worker 单测（《运行时可靠性收口》步 1.2）。

纯调度语义：队列容量 / D1 丢弃与优先级 / 单飞 / 过期丢弃 / 异常兜住 / 关闭宽限。
**用注入的假 handler**，不依赖真实 LLM/TTS（worker 本身不接服务）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from elysia.soul.brain import BrainOutput
from elysia.soul.expression_types import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    ExpressionJob,
    ExpressionResult,
    make_job_id,
)
from elysia.soul.expression_worker import ExpressionWorker

Handler = Callable[[ExpressionJob], Awaitable[ExpressionResult]]


def _job(seq: int, *, force: bool = False, ts: float | None = None) -> ExpressionJob:
    created = float(seq) if ts is None else ts
    return ExpressionJob(
        job_id=make_job_id(seq, created),
        created_ts=created,
        heartbeat_seq=seq,
        output=BrainOutput(),
        summary={},
        force=force,
    )


def _ok(job: ExpressionJob, text: str = "嗯") -> ExpressionResult:
    return ExpressionResult(
        job_id=job.job_id,
        status=STATUS_SUCCESS,
        order_key=job.order_key,
        text=text,
    )


async def _until(pred: Callable[[], bool], *, tries: int = 200) -> None:
    """等条件成立（只用 sleep(0) 让出，确定性足够——handler 全是纯 await）。"""
    for _ in range(tries):
        if pred():
            return
        await asyncio.sleep(0)
    raise AssertionError("条件未在预期内成立")


# ── 基本执行与 drain ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_runs_handler_and_drain_returns_result() -> None:
    async def handler(job: ExpressionJob) -> ExpressionResult:
        return _ok(job, "晚上好")

    worker = ExpressionWorker(handler)
    assert worker.submit(_job(1)) is True
    await _until(lambda: worker.depth == 0)
    results = worker.drain()
    assert len(results) == 1
    assert results[0].text == "晚上好"
    assert results[0].status == STATUS_SUCCESS
    assert worker.drain() == []  # 取走即清空


@pytest.mark.asyncio
async def test_single_flight_never_concurrent() -> None:
    active = 0
    max_active = 0

    async def handler(job: ExpressionJob) -> ExpressionResult:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        active -= 1
        return _ok(job)

    worker = ExpressionWorker(handler)
    for seq in (1, 2, 3):
        worker.submit(_job(seq, force=True))
    await _until(lambda: worker.depth == 0)
    assert max_active == 1
    assert len(worker.drain()) == 3


# ── D1：容量 / 丢弃 / 优先级 ───────────────────────────────────


@pytest.mark.asyncio
async def test_internal_thought_dropped_when_full() -> None:
    seen: list[int] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(job: ExpressionJob) -> ExpressionResult:
        seen.append(job.heartbeat_seq)
        started.set()
        await release.wait()
        return _ok(job)

    worker = ExpressionWorker(handler)
    assert worker.submit(_job(1)) is True  # 在跑（容量内）
    await started.wait()
    assert worker.submit(_job(2)) is True  # 等待位（容量 2 满）
    assert worker.depth == 2
    # 第三个内部念头：遇满即丢 ⇒「她这次决定不说」
    assert worker.submit(_job(3)) is False
    assert worker.depth == 2

    release.set()
    await _until(lambda: worker.depth == 0)
    assert seen == [1, 2]  # 第 3 条从未执行
    assert len(worker.drain()) == 2


@pytest.mark.asyncio
async def test_user_input_preempts_oldest_internal_thought() -> None:
    seen: list[int] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(job: ExpressionJob) -> ExpressionResult:
        seen.append(job.heartbeat_seq)
        started.set()
        await release.wait()
        return _ok(job)

    worker = ExpressionWorker(handler)
    worker.submit(_job(1))  # 在跑
    await started.wait()
    worker.submit(_job(2))  # 等待位（内部念头）
    assert worker.depth == 2
    # 用户输入永不丢：挤掉最旧的内部念头
    assert worker.submit(_job(3, force=True)) is True
    assert worker.depth == 2

    release.set()
    await _until(lambda: worker.depth == 0)
    assert seen == [1, 3]  # 2 被挤掉，从未执行


@pytest.mark.asyncio
async def test_user_input_never_dropped_even_when_no_internal_to_preempt() -> None:
    seen: list[int] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(job: ExpressionJob) -> ExpressionResult:
        seen.append(job.heartbeat_seq)
        started.set()
        await release.wait()
        return _ok(job)

    worker = ExpressionWorker(handler)
    worker.submit(_job(1, force=True))  # 在跑
    await started.wait()
    worker.submit(_job(2, force=True))  # 等待位
    # 队列里全是用户输入（无可挤者）⇒ 照样入队（丢一句＝没听见）
    assert worker.submit(_job(3, force=True)) is True
    assert worker.depth == 3

    release.set()
    await _until(lambda: worker.depth == 0)
    assert seen == [1, 2, 3]


# ── 过期结果 / 异常 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_result_dropped_newest_wins() -> None:
    a, b = _job(1), _job(2)
    orders = {a.job_id: (1, 1.0), b.job_id: (0, 0.5)}  # b 的结果"更旧"

    async def handler(job: ExpressionJob) -> ExpressionResult:
        return ExpressionResult(
            job_id=job.job_id,
            status=STATUS_SUCCESS,
            order_key=orders[job.job_id],
            text=job.job_id,
        )

    worker = ExpressionWorker(handler)
    worker.submit(a)
    worker.submit(b)
    await _until(lambda: worker.depth == 0)
    results = worker.drain()
    assert [r.job_id for r in results] == [a.job_id]  # 旧结果不覆盖新结果


@pytest.mark.asyncio
async def test_handler_exception_becomes_failed() -> None:
    async def handler(job: ExpressionJob) -> ExpressionResult:
        raise ValueError("boom")

    worker = ExpressionWorker(handler)
    worker.submit(_job(1))
    await _until(lambda: worker.depth == 0)
    results = worker.drain()
    assert len(results) == 1
    assert results[0].status == STATUS_FAILED
    assert results[0].error is not None and "boom" in results[0].error


# ── 关闭（1.6 关闭顺序的 worker 段）────────────────────────────


@pytest.mark.asyncio
async def test_aclose_waits_for_inflight_and_returns_remaining() -> None:
    async def handler(job: ExpressionJob) -> ExpressionResult:
        return _ok(job)

    worker = ExpressionWorker(handler)
    worker.submit(_job(1))
    await _until(lambda: worker.depth == 0)
    assert len(worker.drain()) == 1  # 先取走
    assert await worker.aclose() == []  # 无剩余
    assert worker.closed is True


@pytest.mark.asyncio
async def test_aclose_collects_unpersisted_results() -> None:
    async def handler(job: ExpressionJob) -> ExpressionResult:
        return _ok(job)

    worker = ExpressionWorker(handler)
    worker.submit(_job(1))
    await _until(lambda: worker.depth == 0)
    results = await worker.aclose()  # 未 drain 就走关闭：结果仍交回，供落库
    assert len(results) == 1
    assert results[0].status == STATUS_SUCCESS


@pytest.mark.asyncio
async def test_aclose_marks_timeout_when_grace_exceeded() -> None:
    never = asyncio.Event()
    started = asyncio.Event()

    async def handler(job: ExpressionJob) -> ExpressionResult:
        started.set()
        await never.wait()  # 永不返回：模拟外部服务卡死
        return _ok(job)

    worker = ExpressionWorker(handler, close_grace_s=0.01)
    worker.submit(_job(1))
    await started.wait()
    results = await worker.aclose()
    assert len(results) == 1
    assert results[0].status == STATUS_TIMEOUT
    assert results[0].text == ""  # 无已校验文本（D3 澄清：取消不携带中间态）


@pytest.mark.asyncio
async def test_aclose_rejects_new_submissions_and_drops_pending() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    seen: list[int] = []

    async def handler(job: ExpressionJob) -> ExpressionResult:
        seen.append(job.heartbeat_seq)
        started.set()
        await release.wait()
        return _ok(job)

    worker = ExpressionWorker(handler)
    worker.submit(_job(1))  # 在跑
    await started.wait()
    worker.submit(_job(2))  # 等待位
    closing = asyncio.create_task(worker.aclose())
    await asyncio.sleep(0)
    # 停收新任务
    assert worker.submit(_job(3, force=True)) is False
    release.set()
    await closing
    assert seen == [1]  # 等待中的第 2 条未执行（关停中再开口没有意义）
    assert worker.closed is True
