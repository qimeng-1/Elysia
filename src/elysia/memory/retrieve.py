"""记忆检索 + 情绪染色（P3 §9 8.4）：召回"能增强当下表达"的记忆。

检索流程（P2 表达链 P3-D 启用 memory_hooks）：
1. 候选：从 memories 读取可用记忆（按层级/重要性倾向活跃记忆）
2. 衰减过滤：memory_index 强度跌破下限的记忆索引弱化 → 降低候选分
3. 情绪染色：候选记忆的情感向量与"当下感受"点积 → 匹配者优先
   （回忆被当下状态染色——"被感受"原则）
4. 输出：返回 narrative 摘要数组，供表达指令 memory_hooks 注入

T2 铁律不变：检索结果只作为**结构化 memory_hooks** 注入表达指令，
LLM 消费结构而非原始用户文本。
"""

from __future__ import annotations

from dataclasses import dataclass

from elysia.memory.levels import LEVEL_DEEP, LEVEL_WORKING, MemoryRecord, default_narrative

# 表达一次最多注入多少条记忆（防止 LLM 提示过长/分心）
MAX_HOOKS = 3

# 深层/工作记忆的层级权重（越深越值得被提起）
LEVEL_WEIGHT: dict[str, float] = {
    LEVEL_DEEP: 1.0,
    LEVEL_WORKING: 0.8,
    # shallow 默认 0.5（在下面回退）
}

# 越权对抗：narrative 提供的情感词必须当下真实存在，否则 LLM 用词会被拦


def _level_weight(level: str) -> float:
    return LEVEL_WEIGHT.get(level, 0.5)


def mood_similarity(emotion_vector: dict[str, float], current_mood: dict[str, float]) -> float:
    """候选记忆情感向量与"当下感受"的匹配度（点积相似）。

    - 记忆情感向量：该记忆记录时的感受
    - 当下感受：当前 6 维感受（发现"和现在一样的感觉"→ 想让对方知道）
    """
    if not emotion_vector or not current_mood:
        return 0.0
    score = 0.0
    for dim in ("miss", "chat", "curiosity", "explore"):
        score += emotion_vector.get(dim, 0.0) * current_mood.get(dim, 0.0)
    return round(score, 3)


@dataclass
class MemoryHit:
    """一条被检索命中的记忆（供表达注入）。"""

    memory_id: int
    narrative: str
    level: str
    score: float  # 染色 + 层级 + 可用性综合分
    protected: bool


def score_memory(
    record: MemoryRecord,
    current_mood: dict[str, float],
    *,
    index_strength: float = 1.0,
) -> float:
    """单条记忆的检索得分（不包含衰减过滤外的其他条件）。

    分数 = 层级权重 + 情绪染色 + 索引可用性
    """
    level_w = _level_weight(record.level)
    emotion = mood_similarity(record.emotion_vector, current_mood)
    # 索引强度弱 → 降低可达性（仍然可召回，只是优先级低）
    availability = max(0.0, min(1.0, index_strength))
    return round(level_w * 0.6 + emotion * 0.8 + availability * 0.3, 3)


def select_hooks(
    records: list[MemoryRecord],
    current_mood: dict[str, float],
    *,
    index_strengths: dict[int, float] | None = None,
    max_hooks: int = MAX_HOOKS,
) -> list[MemoryHit]:
    """从候选记忆挑选要注入表达的 hooks（按得分降序）。

    index_strengths：memory_id → 索引强度（可选，默认 1.0 视为可用新鲜）。
    低于 STRENGTH_RETRIEVE_FLOOR 的记忆仍可召回（只是 score 被拉低），
    体现"数据永在、索引碎"——这正是缺口信号的来源。
    """
    hits: list[MemoryHit] = []
    for rec in records:
        mid = rec.id if rec.id is not None else -1
        strength = index_strengths.get(mid, 1.0) if index_strengths else 1.0
        score = score_memory(rec, current_mood, index_strength=strength)
        hits.append(
            MemoryHit(
                memory_id=mid,
                narrative=default_narrative(rec),
                level=rec.level,
                score=score,
                protected=rec.protected,
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:max_hooks]


async def retrieve_from_store(
    store: object,
    current_mood: dict[str, float],
    *,
    max_hooks: int = MAX_HOOKS,
) -> list[MemoryHit]:
    """从记忆存储检索几条待注入表达的记忆（异步）。

    只读路径：遍历 memories → 转换 MemoryRecord → 用 select_hooks 挑选。
    store 需提供 iterate_memories() -> list[dict]（HeartbeatStore 已具备）。
    """
    records = await store.iterate_memories()  # type: ignore[attr-defined]
    if not records:
        return []
    mem_records = [MemoryRecord.from_dict(d) for d in records]
    return select_hooks(mem_records, current_mood, max_hooks=max_hooks)
