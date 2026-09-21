"""记忆修正 / 覆盖（P3 §8.2 准确率保障）：同一话题的新事实取代旧事实。

问题：记忆只增不改。用户先说"生日是 11 月 11 日"，次日更正为"12 月 12 日"，
两条并存且无失效标记——新鲜度归零后，旧的错误事实可能排到前面被说出，
准确率不可控。

方案（最小侵入，不引入语义模型）：
- 用"字符二元组 Jaccard 相似度"判定同话题（无需 NLP，中文友好）
- 新记忆写入时，与更早记忆比对，相似度达标者标记 superseded_by = 新 id
- 被取代的记忆数据保留（可溯源/可复原），但检索不再召回

纯函数模块，可单测，不依赖存储；落库由调用方（心跳写入）执行。
"""

from __future__ import annotations

from elysia.memory.levels import KIND_EXPRESSION, MemoryRecord

# 同话题判定阈值：二元组 Jaccard ≥ 此值视为"在说同一件事"
# 生日更正例：「我的生日是11月11日」vs「其实我的生日是12月12日」≈ 0.43
# 无关例：「我吃了一顿大餐」vs「我吃了火锅」≈ 0.25（不误判）
SUPERSEDE_SIMILARITY = 0.4


def _normalize(text: str) -> str:
    """归一：去掉空白与标点，只留字母/数字/CJK（降低标点噪声）。"""
    return "".join(ch for ch in text if ch.isalnum())


def _bigrams(text: str) -> set[str]:
    """字符二元组集合（单字符则取自身）。"""
    t = _normalize(text)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i : i + 2] for i in range(len(t) - 1)}


def content_similarity(a: str, b: str) -> float:
    """两段内容的二元组 Jaccard 相似度（0-1）。"""
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    union = ga | gb
    return round(len(ga & gb) / len(union), 3) if union else 0.0


def find_superseded(
    new_content: str,
    new_ts: float,
    candidates: list[MemoryRecord],
    *,
    threshold: float = SUPERSEDE_SIMILARITY,
) -> list[int]:
    """找出应被新记忆取代的旧记忆 id 列表。

    条件（全部满足）：
    - 确有 id、尚未被取代、非她自己的发言（KIND_EXPRESSION）
    - 创建时间早于新记忆（只取代更旧的）
    - 内容与新记忆同话题（相似度 ≥ threshold）
    """
    ids: list[int] = []
    for rec in candidates:
        if rec.id is None or rec.superseded_by is not None:
            continue
        if rec.kind == KIND_EXPRESSION:
            continue
        if rec.created_ts >= new_ts:
            continue
        if content_similarity(new_content, rec.content) >= threshold:
            ids.append(rec.id)
    return ids
