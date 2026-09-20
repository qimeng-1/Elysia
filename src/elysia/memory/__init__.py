"""P3 记忆子包：让"生命的重量"落地。

分工（路线图 §九）：
- levels.py: 三层记忆定义 + MemoryRecord 数据模型（可导出 JSON）
- scorer.py: 重要性打分（晋升依据）
- store.py:  记忆存储集成（表结构/读写在 core.state_store 扩展）
- promote.py:三层晋升 + 细节模糊化 + 珍贵保护（P3-B）
- decay.py:  索引衰减（P3-C）
- retrieve.py:检索 + memory_hooks 注入（P3-D）
- sleep.py:  睡眠整合 = 做梦（P3-E）

当前进度：P3-A（levels + scorer + store 落库）已就绪。
"""

from __future__ import annotations

from elysia.memory.levels import (
    DETAIL_DECAY_PER_LEVEL,
    KIND_EXPRESSION,
    KIND_INTERACTION,
    KIND_INTERNAL,
    KIND_STATE,
    KINDS,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    LEVEL_WORKING,
    LEVELS,
    PROMOTE_SHALLOW_ACCESS,
    PROMOTE_SHALLOW_IMPORTANCE,
    PROMOTE_WORKING_IMPORTANCE,
    MemoryRecord,
    default_narrative,
    level_rank,
)
from elysia.memory.scorer import emotion_strength, importance, record_importance

__all__ = [
    "DETAIL_DECAY_PER_LEVEL",
    "KINDS",
    "KIND_EXPRESSION",
    "KIND_INTERACTION",
    "KIND_INTERNAL",
    "KIND_STATE",
    "LEVELS",
    "LEVEL_DEEP",
    "LEVEL_SHALLOW",
    "LEVEL_WORKING",
    "PROMOTE_SHALLOW_ACCESS",
    "PROMOTE_SHALLOW_IMPORTANCE",
    "PROMOTE_WORKING_IMPORTANCE",
    "MemoryRecord",
    "default_narrative",
    "emotion_strength",
    "importance",
    "level_rank",
    "record_importance",
]
