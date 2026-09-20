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

from elysia.memory.decay import (
    LATENCY_30D_MS,
    LATENCY_90D_MS,
    LATENCY_365D_MS,
    LATENCY_DAYS_BASE,
    PROTECTED_DECAY_SLOWDOWN,
    STRENGTH_RETRIEVE_FLOOR,
    STRENGTH_TAU_DAYS,
    decay_strength,
    effective_age_days,
    retrieve_latency_ms,
)
from elysia.memory.hooks import (
    GAP_EVENT_KIND,
    GAP_INTENSITY_MAX,
    GapSignal,
    detect_gap,
)
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
from elysia.memory.promote import (
    Promotion,
    can_reach_deep,
    decide_promotion,
    promote_batch,
    with_narrative,
)
from elysia.memory.retrieve import (
    MAX_HOOKS as MAX_HOOKS,
)
from elysia.memory.retrieve import (
    MemoryHit as MemoryHit,
)
from elysia.memory.retrieve import (
    mood_similarity,
    retrieve_from_store,
    score_memory,
    select_hooks,
)
from elysia.memory.scorer import emotion_strength, importance, record_importance
from elysia.memory.sleep import (
    DREAM_MAX_FRAGMENTS as DREAM_MAX_FRAGMENTS,
)
from elysia.memory.sleep import (
    DREAM_MIN_FRAGMENTS as DREAM_MIN_FRAGMENTS,
)
from elysia.memory.sleep import (
    Dream as Dream,
)
from elysia.memory.sleep import (
    synthesize_dream,
)

__all__ = [
    "DETAIL_DECAY_PER_LEVEL",
    "DREAM_MAX_FRAGMENTS",
    "DREAM_MIN_FRAGMENTS",
    "GAP_EVENT_KIND",
    "GAP_INTENSITY_MAX",
    "KINDS",
    "KIND_EXPRESSION",
    "KIND_INTERACTION",
    "KIND_INTERNAL",
    "KIND_STATE",
    "LATENCY_30D_MS",
    "LATENCY_90D_MS",
    "LATENCY_365D_MS",
    "LATENCY_DAYS_BASE",
    "LEVELS",
    "LEVEL_DEEP",
    "LEVEL_SHALLOW",
    "LEVEL_WORKING",
    "PROMOTE_SHALLOW_ACCESS",
    "PROMOTE_SHALLOW_IMPORTANCE",
    "PROMOTE_WORKING_IMPORTANCE",
    "PROTECTED_DECAY_SLOWDOWN",
    "STRENGTH_RETRIEVE_FLOOR",
    "STRENGTH_TAU_DAYS",
    "Dream",
    "GapSignal",
    "MemoryRecord",
    "Promotion",
    "can_reach_deep",
    "decay_strength",
    "decide_promotion",
    "default_narrative",
    "detect_gap",
    "effective_age_days",
    "emotion_strength",
    "importance",
    "level_rank",
    "mood_similarity",
    "promote_batch",
    "record_importance",
    "retrieve_from_store",
    "retrieve_latency_ms",
    "score_memory",
    "select_hooks",
    "synthesize_dream",
    "with_narrative",
]
