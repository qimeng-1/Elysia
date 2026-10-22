"""P3：她的"打算"与"手"（意图 + 后台执行器）。

用户拍板（2026-10-20）：「批准，且需要动手能力」「不规定次数」。
P3 给她三样东西：**写下打算 → 后台真去做 → 下次开口能讲**。

技术护栏（**不是行为配额**——用户明确否决"每天只能做 N 件事"）：
- 同时只跑 1 个动作（单 worker + asyncio.Lock）；
- 单次动作 ≤ `INTENTION_TIMEOUT_S`（8s）超时；
- 连续失败 `INTENTION_MAX_ATTEMPTS`（3）次 ⇒ `failed`，不再重试。

执行结果：
- `read_url` ⇒ 走 P2 的 `record_world_memory`（成功才落；24h 去重照旧）⇒ `done`，
  `outcome` 存一句"我读到了什么"（≤120 字，取自世界记忆正文）；
- `write_note` ⇒ 走 W3 写作区 ⇒ `done`；
- 失败 ⇒ attempts+1、留原因；未到 3 次保持 `pending`。
每个动作一行日志：`intention_action id=.. action=.. status=.. ms=..`。

证据链口径（T-028 / P-014）：`data/her_home/` 的**每一笔写入**都能在
`thought_log kind='tool'` 里查到——`via='expression'` 来自她表达轮里的工具调用
（`expression_service._log_tool_call`），`via='intention'` 来自本后台执行器；
`intentions.outcome` 是**补充证据，不是唯一证据**。因此 `read_url`/`write_note`
无论成功还是失败（含超时）都落一行同构 JSON：`{tool, ok, args_len, result_head,
elapsed_ms, ts, via}`；落痕失败只记日志，绝不影响执行结果。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from elysia.memory.world import record_world_memory
from elysia.soul.tools.read_url import ReadResult, read_url
from elysia.soul.tools.writing import WritingWorkspace

log = logging.getLogger("elysia.soul.intentions")

# ── 校验常量 ─────────────────────────────────────────────
INTENTION_GOAL_MAX_CHARS = 120
INTENTION_OUTCOME_MAX_CHARS = 120
INTENTION_ACTIONS = ("read_url", "write_note")
INTENTION_KINDS = ("task", "surprise", "conceal", "play")

# ── T-028（P-014）：意图执行也留工具痕迹（与 expression_service 的观测者同构）──
INTENTION_TOOL_KIND = "tool"  # thought_log.kind
INTENTION_TOOL_VIA = "intention"  # 区分表达轮工具调用（via='expression'）
INTENTION_TOOL_RESULT_HEAD_CHARS = 120  # result_head 取 outcome 前多少字

# ── 技术护栏（不是配额：运行时**没有**每天/每小时能做几件）───────────
INTENTION_POLL_S = 30.0  # 检查频率（多久看一次 pending），不是"多久能做一次"
INTENTION_TIMEOUT_S = 8.0  # 单次动作超时
INTENTION_MAX_ATTEMPTS = 3  # 连续失败几次转 failed
INTENTION_MAX_CONCURRENCY = 1  # 同时只跑一个动作

# ── recent_discoveries（P3c）─────────────────────────────
DISCOVERY_WINDOW_S = 24.0 * 3600.0
DISCOVERY_WORLD_LIMIT = 5
DISCOVERY_INTENTION_LIMIT = 3
DISCOVERY_CHARS = 80


def validate_intention(
    goal: str, action: str, target: str | None, kind: str
) -> tuple[str, str, str | None, str] | str:
    """校验意图参数；合法返回规范化四元组，非法返回**给她的明确理由**。"""
    goal_text = str(goal or "").strip()
    if not goal_text:
        return "（set_intention 需要一个 goal：一句话说说你想做什么）"
    if len(goal_text) > INTENTION_GOAL_MAX_CHARS:
        return (
            f"（goal 太长了（{len(goal_text)} 字 > {INTENTION_GOAL_MAX_CHARS} 字）"
            "——一句话说清就好）"
        )
    action_name = str(action or "").strip()
    if action_name not in INTENTION_ACTIONS:
        return f"（action 只能是 {' / '.join(INTENTION_ACTIONS)}，不认「{action_name}」）"
    kind_name = str(kind or "task").strip() or "task"
    if kind_name not in INTENTION_KINDS:
        return f"（kind 只能是 {' / '.join(INTENTION_KINDS)}，不认「{kind_name}」）"
    target_text = str(target).strip() if target is not None else None
    if action_name == "read_url" and not target_text:
        return "（read_url 需要一个 target：要读的 URL；白名单只有维基正文与米游社搜索）"
    return goal_text, action_name, target_text or None, kind_name


class Intentions:
    """意图的声明与执行（P3）。不认"每天做几件"——只有技术护栏。"""

    def __init__(
        self,
        store: Any,
        *,
        read_url_fn: Callable[[str], ReadResult] = read_url,
        writing: WritingWorkspace | None = None,
        world_recorder: Callable[..., Any] = record_world_memory,
        timeout_s: float = INTENTION_TIMEOUT_S,
        max_attempts: int = INTENTION_MAX_ATTEMPTS,
    ) -> None:
        self._store = store
        self._read_url = read_url_fn
        self._writing = writing if writing is not None else WritingWorkspace()
        self._world_recorder = world_recorder
        self._timeout_s = float(timeout_s)
        self._max_attempts = int(max_attempts)
        # 技术护栏：同时只跑一个动作（不是"次数配额"）
        self._action_lock = asyncio.Lock()

    async def set_intention(
        self,
        *,
        goal: str,
        action: str,
        target: str | None,
        kind: str,
        now: float,
    ) -> str:
        """她声明一个打算；成功回执带 pending id，失败明确说原因（不猜、不落库）。"""
        validated = validate_intention(goal, action, target, kind)
        if isinstance(validated, str):
            return validated
        goal_text, action_name, target_text, kind_name = validated
        try:
            intention_id = await self._store.add_intention(
                float(now),
                goal=goal_text,
                action=action_name,
                target=target_text,
                kind=kind_name,
            )
        except Exception:
            log.warning("写意图失败", exc_info=True)
            return "（set_intention 没写成：库写入失败）"
        return f"（你记下了这个打算（#{intention_id}）：{goal_text}——后台会替你去做）"

    async def run_once(self, now: float) -> dict[str, Any] | None:
        """执行**一条** pending 意图（没有则 None）。同时只允许一个动作在跑。"""
        async with self._action_lock:
            intention = await self._store.next_pending_intention()
            if intention is None:
                return None
            return await self._execute(intention, now)

    async def _execute(self, intention: dict[str, Any], now: float) -> dict[str, Any]:
        intention_id = int(intention["id"])
        action = str(intention["action"])
        goal = str(intention["goal"])
        target = intention.get("target")
        started = time.monotonic()
        status = "pending"
        outcome = ""
        # T-028：工具动作的参数序列化（只用于 thought_log 的 args_len；None = 不是工具动作）
        trace_args: str | None = None
        try:
            if action == "read_url":
                url = str(target or "")
                trace_args = json.dumps({"url": url}, ensure_ascii=False)
                result: ReadResult = await asyncio.wait_for(
                    asyncio.to_thread(self._read_url, url),
                    timeout=self._timeout_s,
                )
                if not result.ok:
                    outcome = f"读不到：{result.reason or '未知原因'}"
                else:
                    memory_id = await self._world_recorder(
                        self._store,
                        title=str(result.source or result.final_url or target or ""),
                        summary=result.text,
                        url=str(result.final_url or target or ""),
                        now=float(now),
                    )
                    excerpt = str(result.text or "").strip().replace("\n", " ")
                    outcome = f"我读到了：{excerpt[:INTENTION_OUTCOME_MAX_CHARS]}"
                    status = "done"
                    log.info(
                        "intention_action id=%s action=%s status=%s ms=%d memory=%s",
                        intention_id,
                        action,
                        status,
                        int((time.monotonic() - started) * 1000),
                        memory_id,
                    )
            elif action == "write_note":
                filename = str(target).strip() if target else None
                trace_args = json.dumps({"text": goal, "path": filename}, ensure_ascii=False)
                await asyncio.wait_for(
                    asyncio.to_thread(self._writing.write_note, goal, filename),
                    timeout=self._timeout_s,
                )
                outcome = f"我写下了：{goal[:INTENTION_OUTCOME_MAX_CHARS]}"
                status = "done"
            else:  # 理论不可达（set_intention 已校验）；防御性失败
                outcome = f"不认识的 action：{action}"
        except TimeoutError:
            outcome = f"超时（{self._timeout_s:.0f}s 内没做完）"
        except Exception as exc:
            outcome = f"失败（{type(exc).__name__}: {exc}）"[:INTENTION_OUTCOME_MAX_CHARS]

        elapsed_s = time.monotonic() - started
        elapsed_ms = int(elapsed_s * 1000)
        # T-028：先落工具痕迹（成功/失败/超时都落；落痕失败不影响执行结果），再写意图状态。
        # 放在状态落库之前：即使 finish_intention 写失败，"她做过什么"也仍有证据。
        if trace_args is not None:
            await self._record_tool_trace(
                tool=action,
                args_text=trace_args,
                ok=status == "done",
                outcome=outcome,
                elapsed_ms=elapsed_s * 1000.0,
            )
        if status == "done":
            await self._store.finish_intention(
                intention_id, now, "done", outcome[:INTENTION_OUTCOME_MAX_CHARS]
            )
        else:
            attempts = int(intention.get("attempts") or 0) + 1
            await self._store.record_intention_attempt(
                intention_id, now, outcome[:INTENTION_OUTCOME_MAX_CHARS]
            )
            if attempts >= self._max_attempts:
                await self._store.finish_intention(
                    intention_id, now, "failed", outcome[:INTENTION_OUTCOME_MAX_CHARS]
                )
        log.info(
            "intention_action id=%s action=%s status=%s ms=%d",
            intention_id,
            action,
            status,
            elapsed_ms,
        )
        return {
            "id": intention_id,
            "action": action,
            "status": status,
            "outcome": outcome[:INTENTION_OUTCOME_MAX_CHARS],
            "goal": goal,
        }

    async def _record_tool_trace(
        self,
        *,
        tool: str,
        args_text: str,
        ok: bool,
        outcome: str,
        elapsed_ms: float,
    ) -> None:
        """T-028（P-014）：意图执行也落一行 `thought_log kind='tool'`（`via='intention'`）。

        形状与表达轮的观测者（`expression_service._log_tool_call`）同构：同一套字段名，
        审计时一条查询就能覆盖两条路径。`result_head` 取 outcome 前 120 字。
        **落痕失败绝不能影响执行结果**——这里任何异常都只记日志。
        """
        ts = time.time()
        record: dict[str, Any] = {
            "ts": ts,
            "tool": tool,
            "ok": bool(ok),
            "args_len": len(args_text),
            "result_head": str(outcome or "")[:INTENTION_TOOL_RESULT_HEAD_CHARS],
            "elapsed_ms": round(float(elapsed_ms), 1),
            "via": INTENTION_TOOL_VIA,
        }
        try:
            await self._store.think(
                ts, INTENTION_TOOL_KIND, json.dumps(record, ensure_ascii=False, sort_keys=True)
            )
        except Exception:
            log.warning("意图执行的工具留痕失败（不影响执行结果）", exc_info=True)

    async def recent_discoveries(self, now: float) -> list[str]:
        """最近 24h 她**自己做过/读到**的事（P3c：payload 用文本，不放 id）。"""
        since = float(now) - DISCOVERY_WINDOW_S
        items: list[str] = []
        try:
            worlds = await self._store.recent_world_memories(since, limit=DISCOVERY_WORLD_LIMIT)
        except Exception:
            worlds = []
        for memory in worlds:
            text = str(memory.get("content") or "").strip().replace("\n", " ")
            if text:
                items.append(text[:DISCOVERY_CHARS])
        try:
            finished = await self._store.recent_finished_intentions(
                since, limit=DISCOVERY_INTENTION_LIMIT
            )
        except Exception:
            finished = []
        for intention in finished:
            goal = str(intention.get("goal") or "").strip()
            outcome = str(intention.get("outcome") or "").strip()
            if goal:
                label = f"{goal}——{outcome}" if outcome else goal
                items.append(label[:DISCOVERY_CHARS])
        return items


class IntentionWorker:
    """soul 侧独立 worker：每 `INTENTION_POLL_S` 看一次 pending（不是心跳拍）。"""

    def __init__(
        self,
        intentions: Intentions,
        *,
        clock: Callable[[], float] = time.time,
        poll_s: float = INTENTION_POLL_S,
    ) -> None:
        self._intentions = intentions
        self._clock = clock
        self._poll_s = float(poll_s)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="intention-worker")

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._intentions.run_once(self._clock())
            except Exception:
                log.warning("意图执行器异常（继续下一轮）", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_s)
            except TimeoutError:
                continue
