"""三层晋升机制（P3 §8.2）：浅层 → 工作 → 深层 + 细节模糊化 + 珍贵保护。

晋升规则：
- 浅层 → 工作：重要性 ≥ PROMOTE_SHALLOW_IMPORTANCE 或 访问 ≥ PROMOTE_SHALLOW_ACCESS
- 工作 → 深层：重要性 ≥ PROMOTE_WORKING_IMPORTANCE
- 细节模糊化：每晋升一层，detail_level × (1 - DETAIL_DECAY_PER_LEVEL)
  （narrative + emotion_vector 永不降——情感核心保留）
- 珍贵记忆（protected）永不模糊化

纯函数模块：只做决策，不碰存储；落库由调用方（心跳循环/睡眠整合）
按 Promotion 结果调用 store.update_memory_level 完成。
"""

from __future__ import annotations

from dataclasses import dataclass

from elysia.memory.levels import (
    DETAIL_DECAY_PER_LEVEL,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    LEVEL_WORKING,
    PROMOTE_SHALLOW_ACCESS,
    PROMOTE_SHALLOW_IMPORTANCE,
    PROMOTE_WORKING_IMPORTANCE,
    MemoryRecord,
    default_narrative,
    level_rank,
)


@dataclass(frozen=True)
class Promotion:
    """一条晋升决策：目标层级 + 模糊化后的细节完整度。"""

    memory_id: int | None
    level: str
    detail_level: float


def decide_promotion(record: MemoryRecord) -> Promotion | None:
    """判断一段记忆是否应晋升，返回晋升决策；无需晋升返回 None。

    - 浅层 → 工作：重要性达标 或 被访问足够多次
    - 工作 → 深层：重要性达标
    - 深层不再晋升（但可在此复核 detail_level 不变）
    """
    if record.level == LEVEL_SHALLOW:
        if (
            record.importance >= PROMOTE_SHALLOW_IMPORTANCE
            or record.access_count >= PROMOTE_SHALLOW_ACCESS
        ):
            return Promotion(
                memory_id=record.id,
                level=LEVEL_WORKING,
                detail_level=_decayed_detail(record),
            )
        return None

    if record.level == LEVEL_WORKING:
        if record.importance >= PROMOTE_WORKING_IMPORTANCE:
            return Promotion(
                memory_id=record.id,
                level=LEVEL_DEEP,
                detail_level=_decayed_detail(record),
            )
        return None

    # 深层已是永久层，不再晋升
    return None


def _decayed_detail(record: MemoryRecord) -> float:
    """晋升后的细节完整度：标准记忆逐层损失，珍贵记忆永不模糊。"""
    if record.protected:
        return record.detail_level
    return round(max(0.0, record.detail_level * (1.0 - DETAIL_DECAY_PER_LEVEL)), 3)


def promote_batch(records: list[MemoryRecord]) -> list[Promotion]:
    """批量晋升：扫描全部记忆，返回需要晋升的决策列表（供落库）。

    同时为晋升到深层的记忆补全一句话叙事（情感核心）——narrative 为空
    时回退 content，保证深层记忆永远有可诉说的"核心"。
    """
    promotions: list[Promotion] = []
    for rec in records:
        promo = decide_promotion(rec)
        if promo is not None:
            promotions.append(promo)
    return promotions


def with_narrative(record: MemoryRecord) -> MemoryRecord:
    """返回叙事已补全的记忆副本（情感核心永不空）。

    深层记忆晋升时调用：narrative 为空则回退 content。
    其余字段不变。
    """
    if record.narrative:
        return record
    rec = MemoryRecord(
        id=record.id,
        created_ts=record.created_ts,
        kind=record.kind,
        content=record.content,
        emotion_vector=dict(record.emotion_vector),
        importance=record.importance,
        level=record.level,
        access_count=record.access_count,
        last_access_ts=record.last_access_ts,
        protected=record.protected,
        detail_level=record.detail_level,
        narrative=default_narrative(record),
    )
    return rec


def can_reach_deep(record: MemoryRecord) -> bool:
    """该记忆是否可能升到深层（importance 达标但当前层未满足完整晋升链）。

    用于睡眠整合筛选"值得沉淀"的记忆候选。纯查询辅助，不做决策。
    """
    return record.importance >= PROMOTE_WORKING_IMPORTANCE or level_rank(
        record.level
    ) >= level_rank(LEVEL_WORKING)
