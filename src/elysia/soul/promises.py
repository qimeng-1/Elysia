"""T-020（N1）：她说的"我这就写"要变成她自己的承诺，下一次开口递回她手上。

用户 2026-10-21 批准。**核心原则：系统绝不代写、绝不计配额、绝不改她的话**——
程序只把她自己说过的承诺记下来，并在她下一次开口时提醒她；写不写、推迟、说
"不写了"，三种都合法。

四段：
1. 捕获：回复里出现「第一人称 + 写作动词 + 将来/即时语气」，且本回合**没有**
   成功的 `write_note` ⇒ 记一条 `kind='promise'`（`action='write_note'`、`status='pending'`、
   `goal=她的原话`），并落一行 `thought_log kind='promise'`。
2. 提醒：表达 payload 的 `pending_promises`（最近 24h、≤3 条、只放文本不带 id）
   + 一行明确指令，告诉她"你可以写，也可以说不写了"。
3. 兑现/关闭：本回合成功 `write_note` ⇒ 最近 24h 的 pending 承诺标 `done`；
   她明确放弃 ⇒ 标 `expired`；48h 未兑现 ⇒ 读取时惰性 `expired`。
4. Worker 跳过 `kind='promise'`（只能由她本人兑现）。
"""

from __future__ import annotations

import re
from typing import Any

PROMISE_WINDOW_S = 24.0 * 3600.0  # 提醒窗口
PROMISE_TTL_S = 48.0 * 3600.0  # 惰性过期
MAX_PENDING_PROMISES = 3
PROMISE_GOAL_CHARS = 120

# 承诺检测（T-020b 收紧；T-020c 子句级 + 否定只作用到写作动词）：
# (A) 时间/意愿线索 + 写：「我这就写」「我现在去写」「我待会儿动笔」
# (B) 写 + 结果承诺：「我…写下来/写完/写好/写一篇/写给你」
# (C) 「写完喊你 / 写好给你」
# **否定/过去时只认这些"写作动词附近"的形态**，不再用整句 `[不没]` 否决
# （真机：「不催我正好～那我就慢慢写，写完喊你」不能被前面的"不"误杀）。
_TIME_HINT = r"(?:这就|现在|马上|立刻|待会儿|待会|一会儿|一会|晚点|回头|之后)"
_PROMISE_TIME = re.compile(rf"我{_TIME_HINT}(?:就)?(?:去)?(?:写|动笔|码字)")
_PROMISE_RESULT = re.compile(r"我.{0,12}写(?:下来|完|好|一篇|一段|一页|给你)")
_PROMISE_FOLLOWUP = re.compile(r"写(?:完|好)(?:了)?(?:就|再)?(?:给|喊|告诉|拿给)你")
# 拒绝用：写作动词附近的否定/过去时（本句含这些即不是承诺；保守，宁可不记）
_REJECT_RE = re.compile(
    r"(?:写不|写没|没写|不想写|不写了|不肯写|懒得写|写不动|写完了|写好了|写过了|已经写了)"
)

# 明确放弃（同句出现写作语境，或封闭式说法）；普通「算了」不算
_ABANDON_CLOSED = re.compile(r"(?:不写了|不写啦|写不动了|懒得写|不想写了|先搁着|先放着|这事算了)")
_ABANDON_WRITE_CTX = re.compile(r"(?:写|稿|文|笔)")
# 先按句切（用于"返回整句原话"），再在句内按子句/破折号切（承诺常在逗号之后）
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")
_CLAUSE_SPLIT = re.compile(r"[，,；;、～~…—]+")


def detect_promise(text: str) -> str | None:
    """从她这次说的话里找一句"我要写"的承诺原话；没有返回 None。

    T-020c：否定/过去时按**写作动词附近**的形态判定（不在整句里见「不」就否决）；
    模式在**子句级**匹配（支持「…不催我正好～那我就慢慢写，写完喊你」），
    但返回**整句**（≤120 字）作为承诺原话。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    for sentence in _SENTENCE_SPLIT.split(raw):
        sentence = sentence.strip()
        if not sentence:
            continue
        # 句内出现"写作动词附近的否定/过去时" ⇒ 这一句不算承诺（保守）
        if _REJECT_RE.search(sentence):
            continue
        for clause in _CLAUSE_SPLIT.split(sentence):
            clause = clause.strip()
            if not clause:
                continue
            if (
                _PROMISE_TIME.search(clause)
                or _PROMISE_RESULT.search(clause)
                or _PROMISE_FOLLOWUP.search(clause)
            ):
                return sentence[:PROMISE_GOAL_CHARS]
    return None


def is_abandonment(text: str) -> bool:
    """她这次是否**明确说不写了**（只用于关闭提醒；不替她做决定）。

    T-020b：普通「算了」不算——必须同句有写作语境，或是「不写了/写不动了/
    懒得写/不想写了/先搁着」这类封闭式表达。
    """
    for sentence in _SENTENCE_SPLIT.split(str(text or "")):
        sentence = sentence.strip()
        if not sentence:
            continue
        if _ABANDON_CLOSED.search(sentence):
            return True
        if "算了" in sentence and _ABANDON_WRITE_CTX.search(sentence):
            return True
    return False


async def capture_promise(
    store: Any,
    text: str,
    now: float,
    *,
    wrote: bool,
) -> int | None:
    """捕获承诺并落库；本回合已经真写了 ⇒ 不记。返回新意图 id 或 None。"""
    if wrote:
        return None
    goal = detect_promise(text)
    if goal is None:
        return None
    intention_id = await store.add_intention(
        float(now), goal=goal, action="write_note", target=None, kind="promise"
    )
    await store.think(float(now), "promise", f"承诺：{goal}")
    return int(intention_id)


async def settle_after_reply(
    store: Any,
    text: str,
    now: float,
    *,
    wrote: bool,
    window_s: float = PROMISE_WINDOW_S,
) -> dict[str, Any]:
    """一次开口落库后的承诺处理：兑现 / 放弃 / 捕获（三者互斥，不代写）。"""
    since = float(now) - float(window_s)
    if wrote:
        await store.close_pending_promises(float(now), since, status="done", outcome="她写了")
        return {"closed": "done", "captured": None}
    if is_abandonment(text):
        await store.close_pending_promises(
            float(now), since, status="expired", outcome="她自己说不写了", limit=1
        )
        return {"closed": "expired", "captured": None}
    intention_id = await capture_promise(store, text, now, wrote=False)
    return {"closed": None, "captured": intention_id}


async def list_pending_promises(
    store: Any,
    now: float,
    *,
    window_s: float = PROMISE_WINDOW_S,
    ttl_s: float = PROMISE_TTL_S,
) -> list[str]:
    """最近 24h 的 pending 承诺原话（≤3；先做 48h 惰性过期）。"""
    await store.expire_stale_promises(float(now), ttl_s=ttl_s)
    rows = await store.recent_pending_promises(
        float(now) - float(window_s), limit=MAX_PENDING_PROMISES
    )
    return [str(row.get("goal") or "").strip() for row in rows if row.get("goal")]


def promise_hint(promises: list[str]) -> str:
    """提醒指令（写进 payload 文本；不是命令：写/推迟/说不写都合法）。"""
    joined = "；".join(f"「{item}」" for item in promises)
    return (
        f"（你答应过的事还没做：{joined}。"
        "这一轮你可以用 write_note 写完它；也可以直接说你不写了——两条都算数。）"
    )
