"""S3：允许记错——回忆可改写，**原件保真**（用户拍板 C：当然允许记错）。

机制（S3 细则）：
- 对**本次作业里被钩回来的每条记忆**，把她这次说的所有气泡拼起来，与 `memory.content`
  做**确定性**比对（复用 `memory/similarity.py::coverage`，不新写相似度算法）；
- 命中阈值 `RETELL_OVERLAP_MIN = 0.5` ⇒ 视为"她重述了这条记忆"：只改 `narrative`
  （她的版本，截断 200 字），**`content` 永不改**；
- `certainty` 降一档：`certain → probable → uncertain` 到底就停（**地板**：不许降成
  speculative —— 那等于她否认这件事真实发生过）；
- 每次改写写一行 `memory_narrative_history`（旧/新 narrative + 前后 certainty）+
  日志 `memory_drift id=<id> certainty=<before>-><after>`。

豁免：`kind='self'`（身份种子/她的自我认知）**永不被重述改写**——身份不能被她一句
随口话改掉。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    CERTAINTY_HEARD,
    CERTAINTY_PROBABLE,
    CERTAINTY_SPECULATIVE,
    CERTAINTY_UNCERTAIN,
    KIND_SELF,
)
from elysia.memory.similarity import coverage

log = logging.getLogger("elysia.memory.drift")

# 重述判定阈值（可配常量，不写死在分支里）
RETELL_OVERLAP_MIN = 0.5
MAX_NARRATIVE_CHARS = 200

# certainty 降级阶梯：**地板到 uncertain 为止**；heard 也收敛到 uncertain；
# speculative 已经是最低档，不再下降（保持原样）。
_CERTAINTY_DOWNGRADE: dict[str, str] = {
    CERTAINTY_CERTAIN: CERTAINTY_PROBABLE,
    CERTAINTY_PROBABLE: CERTAINTY_UNCERTAIN,
    CERTAINTY_UNCERTAIN: CERTAINTY_UNCERTAIN,
    CERTAINTY_HEARD: CERTAINTY_UNCERTAIN,
    CERTAINTY_SPECULATIVE: CERTAINTY_SPECULATIVE,
}


def next_certainty(certainty: str) -> str:
    """降一档后的 certainty（certain→probable→uncertain；uncertain 是地板）。"""
    return _CERTAINTY_DOWNGRADE.get(str(certainty), CERTAINTY_UNCERTAIN)


def join_bubbles(bubbles: Sequence[str] | str) -> str:
    """同一次作业里她说的所有气泡拼起来（去空白）。"""
    if isinstance(bubbles, str):
        return bubbles.strip()
    return "".join(str(bubble or "").strip() for bubble in bubbles).strip()


def is_retell(
    bubbles: Sequence[str] | str,
    content: str,
    *,
    threshold: float = RETELL_OVERLAP_MIN,
) -> bool:
    """她这次说的话是否"重述"了这条记忆（纯函数，可单测）。"""
    text = join_bubbles(bubbles)
    if not text or not isinstance(content, str) or not content.strip():
        return False
    return coverage(text, content) >= threshold


async def maybe_drift(
    store: Any,
    memory_id: int,
    bubbles: Sequence[str] | str,
    *,
    now: float,
) -> dict[str, Any] | None:
    """命中则改写 `narrative` + 降 certainty + 留历史；返回漂移记录，未命中返回 None。

    调用方保证 `memory_id` 是**本次作业里被钩回来的**记忆；`content` 为脏数据/空、
    或 `kind='self'` 时一律跳过（不崩、不改身份）。
    """
    record = await store.get_memory(int(memory_id))
    if not record:
        return None
    if str(record.get("kind") or "") == KIND_SELF:
        return None
    content = record.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    text = join_bubbles(bubbles)
    if not text or not is_retell(text, content):
        return None
    certainty_before = str(record.get("certainty") or CERTAINTY_UNCERTAIN)
    certainty_after = next_certainty(certainty_before)
    narrative = text[:MAX_NARRATIVE_CHARS]
    outcome = await store.rewrite_narrative(
        int(memory_id),
        new_narrative=narrative,
        certainty_after=certainty_after,
        now=float(now),
    )
    if outcome is None:
        return None
    log.info(
        "memory_drift id=%s certainty=%s->%s",
        int(memory_id),
        certainty_before,
        certainty_after,
    )
    return {
        "memory_id": int(memory_id),
        "old_narrative": outcome["old_narrative"],
        "new_narrative": narrative,
        "certainty_before": certainty_before,
        "certainty_after": certainty_after,
    }
