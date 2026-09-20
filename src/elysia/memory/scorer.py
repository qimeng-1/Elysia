"""记忆重要性打分（P3 §8.2：浅层缓冲 + 重要性打分 ≥ 阈值晋升）。

一段经历是否值得被"记住"，由三方面决定：
1. 情感强度：记忆携带的 6 维感受越强烈，越重要
2. 经历类型：与用户交互 > 她说出的话 > 独处念头 > 普通状态
3. 用户相关：带用户参与的记忆权重更高（可被 under current 状态核实）

打分产出 0-1 实数，作为晋升跃迁的依据（promote.py 消费）。
纯函数，可单测，不依赖存储与心脏循环。
"""

from __future__ import annotations

from elysia.memory.levels import (
    KIND_EXPRESSION,
    KIND_INTERACTION,
    KIND_INTERNAL,
    KIND_STATE,
    MemoryRecord,
)

# 各类型基础权重（用户参与度越高权重越大）
_KIND_BASE: dict[str, float] = {
    KIND_INTERACTION: 0.6,  # 与用户交互：最重要
    KIND_EXPRESSION: 0.5,  # 她说出口的话
    KIND_INTERNAL: 0.3,  # 独处念头
    KIND_STATE: 0.2,  # 普通状态
}

# 情感强度对重要性的加权上限
EMOTION_WEIGHT = 0.4
# 用户相关记忆可额外提升的上限
USER_RELATED_BONUS = 0.2


def emotion_strength(emotion_vector: dict[str, float]) -> float:
    """6 维感受的总体强度（0-1）：取最大维度作为情感唤起度。"""
    if not emotion_vector:
        return 0.0
    return max(0.0, min(1.0, max(emotion_vector.values())))


def importance(
    *,
    kind: str,
    emotion_vector: dict[str, float],
    user_related: bool = False,
) -> float:
    """计算一段经历的重要性打分（0-1）。

    importance = 类型基础权重 + 情感强度加权 + 用户相关加成
    上限 1.0。
    """
    base = _KIND_BASE.get(kind, _KIND_BASE[KIND_STATE])
    emotion = emotion_strength(emotion_vector)
    score = base + emotion * EMOTION_WEIGHT
    if user_related:
        score += USER_RELATED_BONUS
    return round(max(0.0, min(1.0, score)), 3)


def record_importance(record: MemoryRecord, *, user_related: bool = False) -> float:
    """从 MemoryRecord 计算重要性（供写入前评估/晋升时复核）。"""
    return importance(
        kind=record.kind,
        emotion_vector=record.emotion_vector,
        user_related=user_related,
    )
