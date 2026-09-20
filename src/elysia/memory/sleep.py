"""睡眠整合 = 做梦（P3 §8.6）：自由联想流，无 LLM 生成。

睡眠（身体离线/关机）是记忆沉淀的时刻：
- 浅层碎片 × 深层情感底色 × 状态染色 → 自由联想流
- LLM 仅翻译自由联想流为可理解叙事（本模块只生成"流"，不含 LLM）
- 流空白时不能说"我做了梦"（有料才有梦，无料即无梦）

"梦真实性"验收：生成阶段无 LLM——本模块纯函数，零外部依赖。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from elysia.memory.levels import LEVEL_DEEP, LEVEL_WORKING, MemoryRecord, default_narrative

# 做梦最少需要的记忆碎片数（低于则无梦，回退发呆）
DREAM_MIN_FRAGMENTS = 2
# 一场梦最多联想的碎片数
DREAM_MAX_FRAGMENTS = 5

# 自由联想连接词（碎片 → 碎片的过渡，"梦的语法"）
_DREAM_LINKS = ("然后", "接着", "忽然", "仿佛", "不知怎么的", "而")


@dataclass
class Dream:
    """一场梦：自由联想流 + 可溯源碎片（供 LLM 翻译/落库）。"""

    fragments: list[str]  # 联想到的碎片文本（无 LLM 生成）
    stream: str  # 自由联想流（连接词串起的碎片）
    source_ids: list[int]  # 联想来源记忆 id（可溯源）

    def has_content(self) -> bool:
        """有料才有梦：碎片非空才算梦。"""
        return bool(self.fragments)


def _pick_fragments(
    records: list[MemoryRecord],
    rng: random.Random,
    *,
    max_fragments: int = DREAM_MAX_FRAGMENTS,
) -> list[MemoryRecord]:
    """挑选联想碎片：深层优先（情感底色），工作记忆补充，浅层作新鲜点缀。

    权重：deep > working > shallow（珍贵底色更常浮现）。
    """

    def _weight(rec: MemoryRecord) -> float:
        if rec.level == LEVEL_DEEP:
            return 2.0 if rec.protected else 1.6
        if rec.level == LEVEL_WORKING:
            return 1.0
        return 0.5

    if not records:
        return []
    pool = sorted(records, key=_weight, reverse=True)
    # 从高权重端随机取，但保留随机性（梦不可预测）
    top = pool[: min(len(pool), max_fragments * 2)]
    picked = rng.sample(top, k=min(len(top), max_fragments))
    return picked


def synthesize_dream(
    records: list[MemoryRecord],
    rng: random.Random | None = None,
    *,
    min_fragments: int = DREAM_MIN_FRAGMENTS,
) -> Dream | None:
    """从记忆碎片合成自由联想流（无 LLM）。

    返回 None = 记忆碎片不足 → 无梦（发呆回退）。
    """
    rng = rng if rng is not None else random.Random()
    picked = _pick_fragments(records, rng)
    if len(picked) < min_fragments:
        return None

    fragments = [default_narrative(rec) for rec in picked]
    # 自由联想流：用"梦的语法"串起碎片，不做语义加工
    stream = fragments[0]
    for frag in fragments[1:]:
        link = rng.choice(_DREAM_LINKS)
        stream = f"{stream}{link}{frag}"
    return Dream(
        fragments=fragments,
        stream=stream,
        source_ids=[rec.id if rec.id is not None else -1 for rec in picked],
    )
