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

from elysia.memory.levels import (
    KIND_EXPRESSION,
    LEVEL_DEEP,
    LEVEL_WORKING,
    MemoryRecord,
    default_narrative,
)

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
    now: float | None = None,
) -> float:
    """单条记忆的检索得分。

    分数 = 层级权重 + 情绪染色 + 索引可用性 + 新鲜度（短期优先）。
    短期记忆（几天内）应可靠召回——这是"记得上周的大餐"的保证；
    得分随年龄衰减，但多日记忆仍按重要性/情绪排序。
    """
    level_w = _level_weight(record.level)
    emotion = mood_similarity(record.emotion_vector, current_mood)
    availability = max(0.0, min(1.0, index_strength))
    recency = _recency_factor(record.created_ts, now) if now is not None else 0.0
    return round(level_w * 0.6 + emotion * 0.8 + availability * 0.3 + recency, 3)


# 检索新鲜度：近 7 天内给显著加成，之后衰减到 0（短期记忆可靠召回的保证）
RECENCY_BOOST = 0.55
RECENCY_DAYS = 7


def _recency_factor(created_ts: float, now: float) -> float:
    """新鲜度因子：创建越近越高，RECENCY_DAYS 天后为 0。"""
    age_s = max(0.0, now - created_ts)
    age_d = age_s / 86400.0
    if age_d >= RECENCY_DAYS:
        return 0.0
    return round(RECENCY_BOOST * (1.0 - age_d / RECENCY_DAYS), 3)


def select_hooks(
    records: list[MemoryRecord],
    current_mood: dict[str, float],
    *,
    index_strengths: dict[int, float] | None = None,
    max_hooks: int = MAX_HOOKS,
    now: float | None = None,
) -> list[MemoryHit]:
    """从候选记忆挑选要注入表达的 hooks（按得分降序）。

    index_strengths：memory_id → 索引强度（可选，默认 1.0 视为可用新鲜）。
    低于 STRENGTH_RETRIEVE_FLOOR 的记忆仍可召回（只是 score 被拉低）。
    now：用于短期新鲜度加成（短期记忆可靠召回）。
    """
    hits: list[MemoryHit] = []
    for rec in records:
        # 排除她自己的发言回声：hooks 用于"记起你/世界"，不应复述刚说过的自己
        if rec.kind == KIND_EXPRESSION:
            continue
        mid = rec.id if rec.id is not None else -1
        strength = index_strengths.get(mid, 1.0) if index_strengths else 1.0
        score = score_memory(rec, current_mood, index_strength=strength, now=now)
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
    now: float | None = None,
) -> list[MemoryHit]:
    """从记忆存储检索几条待注入表达的记忆（异步）。

    只读路径：遍历 memories → 转换 MemoryRecord → 用 select_hooks 挑选。
    store 需提供 iterate_memories() -> list[dict]（HeartbeatStore 已具备）。

    接线（P3-I 修复）：
    - 读取 memory_index strength 作为索引可用性（若有 iterate_memory_index）
    - 命中返回前 touch 记忆（递增 access_count，驱动浅层→工作晋升）
    """
    records = await store.iterate_memories()  # type: ignore[attr-defined]
    if not records:
        return []
    mem_records = [MemoryRecord.from_dict(d) for d in records]

    # 索引可用性：memory_index 表持久化的 strength（P3-C 衰减的消费方）
    index_strengths: dict[int, float] | None = None
    if hasattr(store, "iterate_memory_index"):
        rows = await store.iterate_memory_index()
        index_strengths = {mid: strength for _, mid, strength, _ in rows}

    hooks = select_hooks(
        mem_records,
        current_mood,
        max_hooks=max_hooks,
        now=now,
        index_strengths=index_strengths,
    )

    # 检索命中 → 触碰记忆（访问计数递增，被想起的次数是晋升依据）
    if hooks and hasattr(store, "touch_memory"):
        ts = now if now is not None else 0.0
        for h in hooks:
            await store.touch_memory(h.memory_id, ts)
    return hooks
