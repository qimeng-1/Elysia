"""S1a：对话轮次（`dialogue_turns`）——她"接得上话"的物理基础。

真机病根（`docs/LEAD_MEMORY_IDENTITY_DESIGN_2026-10-20.md` §0）：表达指令里只有最后
一条 `user_message`，所以她说「一定」时**不知道它在指向什么**。本模块把你们最近的真实
对话存成轮次，并按"一次交换"分组，供 `expression_service` 注入 `dialogue_context`。

交换边界（确定性、可测，S1 细则 §S1a）：
- **每来一条新的用户消息 ⇒ `exchange_id += 1`**；
- 在那之前她说的所有气泡（含多句拆分的每条气泡）都归**同一** `exchange_id`。

写入失败**不得影响她说话**：所有写入都吞异常 + 记日志（主流程照旧）——
对话上下文是增强，不是表达的前置条件。
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("elysia.soul.dialogue")

# ── 注入预算（S1 细则：20 条 / 单条 200 字 / 总长 2000 字）─────────────
MAX_CONTEXT_TURNS = 20
CONTEXT_TEXT_CHARS = 200
CONTEXT_MAX_CHARS = 2000

ROLE_USER = "user"
ROLE_HER = "her"


async def record_user_message(store: Any, now: float, text: str) -> int | None:
    """用户新消息 → 新交换号 + 写一条 `role='user'` 轮次。失败返回 None，不抛。"""
    message = str(text or "").strip()
    if not message:
        return None
    try:
        exchange_id = int(await store.max_exchange_id()) + 1
        await store.add_dialogue_turn(now, ROLE_USER, message, exchange_id)
        return exchange_id
    except Exception:
        log.warning("写对话轮次（user）失败——不影响她说话", exc_info=True)
        return None


async def record_her_bubble(
    store: Any,
    now: float,
    text: str,
    *,
    emotion_vector: dict[str, float] | None = None,
    intent: str | None = None,
) -> None:
    """她的每条气泡 → 一条 `role='her'` 轮次，归当前交换号（没有用户消息时为 0）。"""
    bubble = str(text or "").strip()
    if not bubble:
        return
    try:
        exchange_id = int(await store.max_exchange_id())
        await store.add_dialogue_turn(
            now,
            ROLE_HER,
            bubble,
            exchange_id,
            emotion_vector=dict(emotion_vector or {}),
            intent=intent,
        )
    except Exception:
        log.warning("写对话轮次（her）失败——不影响她说话", exc_info=True)


async def build_dialogue_context(
    store: Any,
    *,
    now: float,
    limit: int = MAX_CONTEXT_TURNS,
) -> list[dict[str, Any]]:
    """最近 `limit` 条轮次（时间升序）→ `[{"role","text","age_s"}]`。

    - 单条 `text` 截断 `CONTEXT_TEXT_CHARS`；
    - 整段总长 ≤ `CONTEXT_MAX_CHARS`，超了**从最旧的开始丢**（最近一条永远保留）；
    - 读失败返回 `[]`（不影响她说话）。
    """
    try:
        turns = await store.dialogue_turns(limit=limit)
    except Exception:
        log.warning("读对话轮次失败——本次不带 dialogue_context", exc_info=True)
        return []
    items: list[dict[str, Any]] = []
    for turn in turns:
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        items.append(
            {
                "role": str(turn.get("role") or ""),
                "text": text[:CONTEXT_TEXT_CHARS],
                "age_s": round(max(0.0, float(now) - float(turn.get("ts") or 0.0)), 1),
            }
        )
    while items and sum(len(item["text"]) for item in items) > CONTEXT_MAX_CHARS:
        items.pop(0)
    return items
