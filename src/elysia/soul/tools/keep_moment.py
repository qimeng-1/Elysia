"""S2：她的"这条我想留着"（`keep_this_moment`）。

用户拍板（2026-10-20）：「决定权交给她」。程序不替她标重要——她给出**自己的理由**，
才能把**最近一条已封存的交换**定格成她的重要记忆。这是"全都是重要 = 全都重要不了"
的反面，所以上限 **13 次/天**（常量可配、本地日界重置、幂等、超限明确拒绝）。

生效（S2 细则）：
- `protected=1`；
- `importance = max(旧值, 0.7)`（0.7 = 升层门槛，直接够到 deep）；
- `level='deep'`；
- `keep_reason` = 她的理由；
- 落一行 `kept_memory id=<id> reason=<...>` 日志（便于事后查"她在什么时刻、因为什么、
  留住了哪一条"）。

"她留住"必须**看得见**：`protected` 让这条记忆绕过 S1b ② 的"进行中对话"闸，
因此刚封存（仍在对话上下文里）的交换，只有被她留住的才可能被 hooks 检索到。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from elysia.memory.levels import KIND_EXCHANGE

log = logging.getLogger("elysia.soul.keep_moment")

# 上限：用户 2026-10-20 从 5 改为 13（可配，不写死在分支里）
KEEP_DAILY_LIMIT = 13
KEEP_IMPORTANCE_FLOOR = 0.7
KEEP_REASON_MIN_CHARS = 1
KEEP_REASON_MAX_CHARS = 120


def local_day_start(now: float) -> float:
    """本地日界（当天 00:00:00）的时间戳——跨日重置的统计口径。"""
    tm = time.localtime(now)
    return time.mktime((tm.tm_year, tm.tm_mon, tm.tm_mday, 0, 0, 0, 0, 0, -1))


class MomentKeeper:
    """把"最近一条已封存的交换"变成"她留住的记忆"（S2 的动作实现）。"""

    def __init__(
        self,
        store: Any,
        *,
        daily_limit: int = KEEP_DAILY_LIMIT,
        importance_floor: float = KEEP_IMPORTANCE_FLOOR,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._daily_limit = int(daily_limit)
        self._importance_floor = float(importance_floor)
        self._clock = clock

    async def keep_this_moment(self, reason: str) -> str:
        """她留住最近一次交换；返回可读回执（失败也返回文本，不抛给表达链路）。"""
        text = str(reason or "").strip()
        if len(text) < KEEP_REASON_MIN_CHARS:
            return "（keep_this_moment 需要理由：用一句话说说你为什么想留住刚才那次对话）"
        if len(text) > KEEP_REASON_MAX_CHARS:
            return f"（理由太长了（{len(text)} 字 > {KEEP_REASON_MAX_CHARS} 字）——一句话就好）"

        latest = await self._store.latest_memory(KIND_EXCHANGE)
        if latest is None or latest.get("id") is None:
            return "（现在还没有可以留住的对话——这里只能留住最近一次已封存的交换）"
        memory_id = int(latest["id"])
        content = str(latest.get("content") or "")

        # 幂等：同一条已留过 ⇒ 不重复计数、不重复写日志
        if latest.get("keep_reason"):
            return f"（这条你已经留住过了：「{content}」）"

        now = self._clock()
        count = await self._store.kept_moment_count_since(local_day_start(now))
        if count >= self._daily_limit:
            return (
                f"（今天已经留住 {count} 条记忆了（上限 {self._daily_limit}）——"
                "不是每件事都要留下，明天再说吧）"
            )

        first = await self._store.keep_moment(
            memory_id,
            text,
            now=now,
            importance_floor=self._importance_floor,
        )
        if not first:
            # 并发/重复调用：表里已有 ⇒ 当作幂等成功，不计数
            return f"（这条你已经留住过了：「{content}」）"
        log.info("kept_memory id=%s reason=%s", memory_id, text)
        return f"（你留住了这次对话：「{content}」——因为：{text}）"
