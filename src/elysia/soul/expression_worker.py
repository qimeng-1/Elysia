"""表达 worker：有界队列 + 单执行器，让心跳不必等外部服务（《运行时可靠性收口》步 1.2）。

解的真问题（20.1）：**心跳不能等外部服务——现在它等**。表达全链路原本同步跑在 1Hz
主循环里，LLM 慢 30s ⇒ 心跳停 30 拍、`heartbeats` 出现空洞、期间新交互全丢。
本模块把"耗时段"移出主循环：心跳提交任务（`submit`，非阻塞）并在每拍取走结果
（`drain`），**落库仍由心跳自己写**（D2）。

职责边界（D2 裁决）：
- 本模块**只做调度**——排队、单飞、过期丢弃、关停。真正的耗时工作（构造指令 →
  `llm.speak` → `tts.speak`）由注入的 `handler` 提供（步 1.3 由 `ExpressionService`
  的耗时段充当）。**不落库**：结果经 `drain` 交回心跳，由心跳写
  `think` / `expression` / `add_memory`，**单一事实源＝心跳**。
- 本模块**不判断"该不该开口"**：`_should_speak` 仍在心跳（触发判断廉价、非阻塞，
  留在心跳＝「谁决定开口」仍在生命循环里）。

队列语义（D1 裁决）：
- 容量 `EXPRESS_QUEUE_CAPACITY`（1 在跑 + 1 等待）。
- **内部念头**（`force=False`）遇满即丢并 `log.debug`——「她这次决定不说」，不说也是一种回应。
- **用户输入**（`force=True`）**永不丢**：可挤掉队列里最旧的内部念头；即使队列里全是
  用户输入（无可挤者），也照样入队——丢一句＝没听见。故容量是**内部念头的**上限，
  而非硬上限（"连打 3 句不丢"由此成立）。

超时（D3 澄清，2026-09-24 用户裁决）：`EXPRESS_TIMEOUT_S` **只作关闭宽限**。
正常运行**不给单任务设上限**（LLM 主声自身 30s 的超时会先被误判），任务跑到
handler 自己返回为止；只有 `aclose()` 等在跑任务超过宽限时才取消它，并如实登记一条
`status=timeout`（她这次没说成）。此时结果没有文本——"若有已校验文本则气泡仍显示"
需要 handler 暴露中间态，本阶段不为此加机制。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable

from elysia.core.config import EXPRESS_QUEUE_CAPACITY, EXPRESS_TIMEOUT_S
from elysia.soul.expression_types import (
    STATUS_FAILED,
    STATUS_TIMEOUT,
    ExpressionJob,
    ExpressionResult,
    JobOrder,
    is_fresh,
)

log = logging.getLogger("elysia.soul.expression_worker")

# 耗时段的执行体：由调用方注入（步 1.3 ＝ ExpressionService 的构造指令 → LLM → TTS）
JobHandler = Callable[[ExpressionJob], Awaitable[ExpressionResult]]


class ExpressionWorker:
    """表达任务的调度器：有界队列 + 单执行器 + 关闭宽限。"""

    def __init__(
        self,
        handler: JobHandler,
        *,
        queue_capacity: int = EXPRESS_QUEUE_CAPACITY,
        close_grace_s: float = EXPRESS_TIMEOUT_S,
    ) -> None:
        self._handler = handler
        self._capacity = max(1, queue_capacity)
        self._close_grace_s = close_grace_s
        self._pending: deque[ExpressionJob] = deque()
        self._running: ExpressionJob | None = None
        self._results: deque[ExpressionResult] = deque()
        self._latest_accepted: JobOrder | None = None
        self._task: asyncio.Task[None] | None = None
        self._wakeup = asyncio.Event()
        self._closed = False

    # ── 观测（测试与运行时可读）────────────────────────────

    @property
    def depth(self) -> int:
        """当前在飞任务数（在跑 1 + 等待中）。"""
        return len(self._pending) + (1 if self._running is not None else 0)

    @property
    def closed(self) -> bool:
        return self._closed

    # ── 生命周期 ──────────────────────────────────────────

    def start(self) -> None:
        """启动执行器（幂等）。也可不显式调用——`submit` 会按需启动。"""
        if self._task is None and not self._closed:
            self._task = asyncio.create_task(self._run(), name="expression-worker")

    def submit(self, job: ExpressionJob) -> bool:
        """提交任务（**非阻塞**）。返回 False ＝ 本次未入队（已丢，见模块 docstring）。"""
        if self._closed:
            log.debug("worker 已关闭，拒收任务 job_id=%s", job.job_id)
            return False
        self.start()
        if self.depth >= self._capacity:
            if not job.force:
                # D1：内部念头遇满即丢＝「她这次决定不说」
                log.debug("表达队列已满，丢弃内部念头 job_id=%s", job.job_id)
                return False
            # 用户输入永不丢：挤掉最旧的内部念头（若无，则照样入队）
            dropped = next((q for q in self._pending if not q.force), None)
            if dropped is not None:
                self._pending.remove(dropped)
                log.debug("用户输入挤掉内部念头 job_id=%s", dropped.job_id)
        self._pending.append(job)
        self._wakeup.set()
        return True

    def drain(self) -> list[ExpressionResult]:
        """取走已就绪的结果（**非阻塞**）。心跳每拍调用一次并自行落库。"""
        out = list(self._results)
        self._results.clear()
        return out

    async def aclose(self) -> list[ExpressionResult]:
        """关闭（1.6 关闭顺序的 worker 段）：停收新任务 → 等在跑 ≤ 宽限 → 超时取消。

        返回**尚未取走的结果**（含取消时的 `status=timeout`），供心跳排空落库。
        未开跑的等待任务直接丢弃——关停中再开口没有意义。
        """
        self._closed = True  # 停收新任务
        self._pending.clear()  # 停产生新任务：等待中的不再执行
        self._wakeup.set()  # 唤醒 runner（若它正在空转）以便退出
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=self._close_grace_s)
            except TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                if self._running is not None:
                    # 她这次没说成：如实登记一条 timeout（无文本，见模块 docstring）
                    self._accept(self._timeout_result(self._running))
            self._task = None
        self._running = None
        return self.drain()

    # ── 执行器 ────────────────────────────────────────────

    async def _run(self) -> None:
        """单执行器：一次只跑一个任务，跑完再取下一个。"""
        while True:
            await self._wakeup.wait()
            self._wakeup.clear()
            while self._pending:
                job = self._pending.popleft()
                self._running = job
                result = await self._execute(job)
                self._running = None
                self._accept(result)
            if self._closed:
                return

    async def _execute(self, job: ExpressionJob) -> ExpressionResult:
        """跑一个任务：异常一律兜住（外部服务的失败不能拖垮执行器）。"""
        try:
            return await self._handler(job)
        except asyncio.CancelledError:
            raise  # 关停取消：交给 aclose 登记 timeout
        except Exception as exc:
            log.warning("表达任务失败 job_id=%s: %s", job.job_id, exc)
            return ExpressionResult(
                job_id=job.job_id,
                status=STATUS_FAILED,
                order_key=job.order_key,
                error=str(exc),
            )

    def _accept(self, result: ExpressionResult) -> None:
        """收下一个结果：旧结果不覆盖新结果（20.6 的 1.4）。"""
        if not is_fresh(result.order_key, self._latest_accepted):
            log.debug("丢弃过期表达结果 job_id=%s", result.job_id)
            return
        self._latest_accepted = result.order_key
        self._results.append(result)

    @staticmethod
    def _timeout_result(job: ExpressionJob) -> ExpressionResult:
        return ExpressionResult(
            job_id=job.job_id,
            status=STATUS_TIMEOUT,
            order_key=job.order_key,
            error="close grace exceeded",
        )
