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

from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    KIND_EXCHANGE,
    LEVEL_SHALLOW,
    SOURCE_USER,
)
from elysia.memory.scorer import importance

log = logging.getLogger("elysia.soul.dialogue")

# ── 注入预算（S1 细则：20 条 / 单条 200 字 / 总长 2000 字）─────────────
MAX_CONTEXT_TURNS = 20
CONTEXT_TEXT_CHARS = 200
CONTEXT_MAX_CHARS = 2000

# ── S1c：她最近几次开口的开头（治"夜深了"开场同质化）──────────────────
RECENT_OPENINGS = 8
OPENING_CHARS = 6

# ── S1d：回复归属窗口（秒）───────────────────────────────────────────
# 她一次回复的多句气泡间隔是 2–8s，180s 足够宽；**超过窗口还开口 = 自发**，
# 不属于任何交换（写 `exchange_id=0`），否则等不到人时的自言自语会被压进
# 上一次交换的记忆（真机缺陷：ex=3 混进 8 句自发言）。常量可配，不写死分支。
REPLY_WINDOW_S = 180.0
SELF_TALK_EXCHANGE_ID = 0

ROLE_USER = "user"
ROLE_HER = "her"


async def record_user_message(
    store: Any, now: float, text: str, *, speaker: str = "user"
) -> int | None:
    """用户/另一个存在的新消息 → 新交换号 + 写一条 `role='user'` 轮次。

    `speaker`（Source 切片）：`user`=人类；其他显示名=另一个电子生命（如 `大肥鱼`）。
    缺省 `user`，旧调用方行为完全不变。失败返回 None，不抛。
    """
    message = str(text or "").strip()
    if not message:
        return None
    try:
        exchange_id = int(await store.max_exchange_id()) + 1
        await store.add_dialogue_turn(now, ROLE_USER, message, exchange_id, speaker=speaker)
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
    """她的每条气泡 → 一条 `role='her'` 轮次。

    S1d 归属窗口：只有在**最近一条用户消息之后 `REPLY_WINDOW_S` 内**的气泡才归
    当前交换号；超出窗口（或从没收到过用户消息）⇒ `exchange_id=0`（自发，不属于任何
    交换）。这样封存交换时不会把"等不到人时的自言自语"也压进那次事件的记忆。
    """
    bubble = str(text or "").strip()
    if not bubble:
        return
    try:
        exchange_id = SELF_TALK_EXCHANGE_ID
        last_user_ts = await store.last_user_turn_ts()
        if last_user_ts is not None and float(now) - float(last_user_ts) <= REPLY_WINDOW_S:
            exchange_id = int(await store.max_exchange_id())
        await store.add_dialogue_turn(
            now,
            ROLE_HER,
            bubble,
            exchange_id,
            emotion_vector=dict(emotion_vector or {}),
            intent=intent,
            speaker="我",
        )
    except Exception:
        log.warning("写对话轮次（her）失败——不影响她说话", exc_info=True)


async def recent_dialogue_window(
    store: Any,
    *,
    now: float,
    limit: int = MAX_CONTEXT_TURNS,
) -> tuple[list[dict[str, Any]], float | None]:
    """最近 `limit` 条轮次 → `(dialogue_context, oldest_ts)`。

    `oldest_ts` = 真正**还在上下文里**的最旧一条轮次的 ts（按字符预算裁剪之后）；
    上下文为空时为 None。记忆检索用它判"这条交互是不是还在正在进行的对话里"
    （S1b ②：在上下文里 ⇒ 是"现在"，不该当记忆钩子）。
    """
    try:
        turns = await store.dialogue_turns(limit=limit)
    except Exception:
        log.warning("读对话轮次失败——本次不带 dialogue_context", exc_info=True)
        return [], None
    items: list[dict[str, Any]] = []
    kept_ts: list[float] = []
    for turn in turns:
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        items.append(
            {
                "role": str(turn.get("role") or ""),
                "speaker": _speaker_label(turn),
                "text": text[:CONTEXT_TEXT_CHARS],
                "age_s": round(max(0.0, float(now) - float(turn.get("ts") or 0.0)), 1),
            }
        )
        kept_ts.append(float(turn.get("ts") or 0.0))
    while items and sum(len(item["text"]) for item in items) > CONTEXT_MAX_CHARS:
        items.pop(0)
        kept_ts.pop(0)
    oldest = min(kept_ts) if kept_ts else None
    return items, oldest


async def build_dialogue_context(
    store: Any,
    *,
    now: float,
    limit: int = MAX_CONTEXT_TURNS,
) -> list[dict[str, Any]]:
    """最近 `limit` 条轮次（时间升序）→ `[{"role","text","age_s"}]`。

    等价于 `recent_dialogue_window` 的第一项；保留给只需要上下文的调用方。
    """
    items, _oldest = await recent_dialogue_window(store, now=now, limit=limit)
    return items


# ── S1b ③：一次交换 = 一条 `kind='exchange'` 记忆 ─────────────────
# 不再把用户每条消息逐条镜像成 `kind='interaction'`（"一定"这种碎片不该单独成记忆）；
# 交换在"下一条用户消息到来时"封存，进程重启时把未封存的直接封存。
EXCHANGE_TEXT_CHARS = 120
SEALED_EXCHANGE_KEY = "dialogue_sealed_exchange_id"


def _speaker_label(turn: dict[str, Any]) -> str:
    """对话轮次的说话人显示名（Source 切片）。

    - 人类（缺省/旧行 NULL/`user`）⇒ `你`
    - 她的气泡 ⇒ `我`
    - 另一个存在 ⇒ 其 `actor` 显示名（如 `大肥鱼`）
    """
    speaker = str(turn.get("speaker") or "").strip()
    if str(turn.get("role") or "") == ROLE_HER:
        return "我"
    if speaker in ("", "user"):
        return "你"
    return speaker


def build_exchange_text(turns: list[dict[str, Any]]) -> tuple[str, dict[str, float]]:
    """把一次交换的轮次压成一条事件记忆的正文 + 她当时的感受（纯函数，可单测）。

    形如：`你："<原话截断120字>"／大肥鱼："<原话>"／我（当时 miss=0.33）："<她的回复>"`。
    **按轮次分别标说话人**（Source 切片）：人类=`你`，另一个存在=`actor`；
    不再把所有用户侧的话都压成"你"。**忽略 `exchange_id <= 0` 的轮次**（S1d）。
    """
    turns = [turn for turn in turns if int(turn.get("exchange_id") or 0) > 0]
    user_parts: list[str] = []
    for turn in turns:
        if turn.get("role") != ROLE_USER:
            continue
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        label = _speaker_label(turn)
        user_parts.append(f'{label}："{text[:EXCHANGE_TEXT_CHARS]}"')
    her_text = " ".join(
        str(turn.get("text") or "").strip() for turn in turns if turn.get("role") == ROLE_HER
    ).strip()
    her_turns = [turn for turn in turns if turn.get("role") == ROLE_HER]
    emotion: dict[str, float] = {}
    for turn in reversed(her_turns):
        payload = turn.get("emotion_vector") or {}
        if payload:
            emotion = {str(k): float(v) for k, v in payload.items() if isinstance(v, (int, float))}
            break
    miss = float(emotion.get("miss", 0.0))
    segments = [
        *user_parts,
        f'我（当时 miss={miss:.2f}）："{her_text[:EXCHANGE_TEXT_CHARS]}"',
    ]
    return "／".join(segments), emotion


async def seal_exchange(
    store: Any,
    state_store: Any,
    exchange_id: int,
    *,
    now: float,
) -> int | None:
    """封存一次交换为一条记忆（幂等：同一 exchange_id 只写一次）。返回记忆 id。

    只由 **下一条用户消息到来 / 进程重启** 触发（确定性、可测）。
    没有用户轮次的交换不算"交换"（纯独白不进记忆）；写失败向上抛，由 `seal_pending` 兜。
    """
    exchange_id = int(exchange_id)
    if exchange_id <= 0:
        return None
    sealed = int(await state_store.load_json(SEALED_EXCHANGE_KEY, 0) or 0)
    if exchange_id <= sealed:
        return None
    turns = [
        t for t in await store.dialogue_turns() if int(t.get("exchange_id") or 0) == exchange_id
    ]
    if not turns:
        await state_store.save_json(SEALED_EXCHANGE_KEY, exchange_id)
        return None
    if not any(t.get("role") == ROLE_USER for t in turns):
        await state_store.save_json(SEALED_EXCHANGE_KEY, exchange_id)
        return None
    content, emotion = build_exchange_text(turns)
    memory_id = await store.add_memory(
        now,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_EXCHANGE,
            "content": content,
            "emotion_vector": emotion,
            "importance": importance(
                kind=KIND_EXCHANGE,
                emotion_vector=emotion,
                content=content,
                user_related=True,
            ),
            "protected": False,
            "narrative": content,
            "source": SOURCE_USER,
            "certainty": CERTAINTY_CERTAIN,
        },
    )
    await state_store.save_json(SEALED_EXCHANGE_KEY, exchange_id)
    return int(memory_id)


async def seal_pending(store: Any, state_store: Any, now: float) -> int | None:
    """封存当前**未封存**的那次交换（重启/新消息时调用；任何失败只记日志，不抛）。"""
    try:
        current = int(await store.max_exchange_id())
        return await seal_exchange(store, state_store, current, now=now)
    except Exception:
        log.warning("封存交换失败——不影响她说话", exc_info=True)
        return None


# ── S1c：开场去重（她最近 8 条气泡的开头片段）────────────────────
async def recent_openings(store: Any, *, limit: int = RECENT_OPENINGS) -> list[str]:
    """她最近 `limit` 条气泡的**开头片段**（每条前 `OPENING_CHARS` 字，按时间升序）。

    供表达指令的 `recent_openings`：让模型看到自己最近怎么开口的，避免句句
    「夜深了/夜里」。读失败返回 []（不影响她说话）。
    """
    try:
        # 多取一些 turn 再筛 her（用户轮次会夹杂其间），保证凑够 limit 条气泡
        turns = await store.dialogue_turns(limit=max(limit * 5, limit))
    except Exception:
        log.warning("读最近开口失败——本次不带 recent_openings", exc_info=True)
        return []
    her_texts = [
        str(turn.get("text") or "").strip()
        for turn in turns
        if turn.get("role") == ROLE_HER and str(turn.get("text") or "").strip()
    ]
    return [text[:OPENING_CHARS] for text in her_texts[-limit:]]
