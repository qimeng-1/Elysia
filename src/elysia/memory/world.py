"""P2：世界记忆——她**真的读到**的东西要落库。

用户裁定（2026-10-20）：「需要」。她的"我读了/我查了"从此有事实依据（P1a 的依据②），
而不是结构性编造。

规则：
- `read_url` **成功后**才落一条；失败不落；
- 内容 = `读了《<标题/来源>》：<摘要 ≤400 字>`，单条 ≤800 字（原始 4000 字不进记忆）；
- 同 URL **24 小时**内只留一条（她反复读同一页不会把库塞满）；
- `kind='world'`、`source=observation`（**不能用 inference/system**——那两个被 hooks 闸
  挡掉，世界记忆就永远想不起来）、`certainty=certain`、`importance=0.25`、`protected=False`。
"""

from __future__ import annotations

import logging
from typing import Any

from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    KIND_WORLD,
    LEVEL_SHALLOW,
    SOURCE_OBSERVATION,
)

log = logging.getLogger("elysia.memory.world")

WORLD_SUMMARY_CHARS = 400
WORLD_MAX_CHARS = 800
WORLD_IMPORTANCE = 0.25
WORLD_DEDUPE_WINDOW_S = 24.0 * 3600.0


def world_content(title: str, summary: str) -> str:
    """把一次成功读取压成世界记忆正文（≤`WORLD_MAX_CHARS`）。"""
    head = str(title or "").strip() or "（未命名来源）"
    body = str(summary or "").strip()
    if len(body) > WORLD_SUMMARY_CHARS:
        body = body[:WORLD_SUMMARY_CHARS] + "…"
    text = f"读了《{head}》：{body}"
    return text[:WORLD_MAX_CHARS]


async def record_world_memory(
    store: Any,
    *,
    title: str,
    summary: str,
    url: str,
    now: float,
) -> int | None:
    """读成功后落一条世界记忆；同 URL 24h 内已有则跳过。返回新记忆 id 或 None。"""
    content = world_content(title, summary)
    if not str(summary or "").strip():
        return None
    key = str(url or "").strip() or str(title or "").strip()
    if not key:
        return None
    try:
        existing = await store.find_recent_memory(
            KIND_WORLD, source_ref=key, since=float(now) - WORLD_DEDUPE_WINDOW_S
        )
        if existing is not None:
            return None
        memory_id = await store.add_memory(
            float(now),
            {
                "level": LEVEL_SHALLOW,
                "kind": KIND_WORLD,
                "content": content,
                "emotion_vector": {},
                "importance": WORLD_IMPORTANCE,
                "protected": False,
                "narrative": content,
                "source": SOURCE_OBSERVATION,
                "certainty": CERTAINTY_CERTAIN,
                "source_ref": key,
            },
        )
        return int(memory_id)
    except Exception:
        # 世界记忆是增强：写失败只记日志，绝不能影响她开口/工具回执
        log.warning("写世界记忆失败——不影响本次读取", exc_info=True)
        return None
