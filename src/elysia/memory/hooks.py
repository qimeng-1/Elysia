"""缺口信号（P3 §8.5）：遗忘的"味道"是好奇，不是焦虑。

高频记忆衰减 → 索引强度跌破检索下限 → 缺口信号：
- 她"感觉有些东西想不起来了"，但数据从未删除（可召回，只是慢）
- 缺口 → TR 轻微上升（想探索/想找回）→ 好奇
- 刻意不让 SA 上升 → 不是焦虑、不是痛苦

接入方式（P3-U 已接线）：soul/heartbeat.py::_feel_memory_gaps —— 感受路径每
MEMORY_FEELING_EVERY_N 拍扫描一次索引强度，检测到缺口即
DesireEvent(kind="memory_gap", intensity=gap.to_event_intensity())。
缺口只推心情（TR 微升=好奇，SA 不动=非焦虑），**不进话语、不新增约束**。
"""

from __future__ import annotations

from dataclasses import dataclass

from elysia.memory.decay import STRENGTH_RETRIEVE_FLOOR

# 单次缺口信号最大强度（避免连续缺口把 TR 顶满）
GAP_INTENSITY_MAX = 0.4

# 缺口事件映射到欲望系统的事件名（desire.py EVENT_PULSES 已注册）
GAP_EVENT_KIND = "memory_gap"


@dataclass(frozen=True)
class GapSignal:
    """一条缺口信号。

    - strength: 0-1 缺口强度（索引强度低于检索下限的累积程度）
    - lost_count: 跌到检索下限以下的记忆索引条数
    - oldest_age_days: 最老一条衰减记忆的年龄（日）
    """

    strength: float
    lost_count: int
    oldest_age_days: float

    def to_event_intensity(self) -> float:
        """映射为 DesireEvent.intensity（0-1，封顶 GAP_INTENSITY_MAX）。"""
        return min(GAP_INTENSITY_MAX, self.strength)


def detect_gap(
    index_strengths: list[tuple[float, float]],
    *,
    floor: float = STRENGTH_RETRIEVE_FLOOR,
) -> GapSignal | None:
    """扫描一组索引（(strength, age_days)），检测缺口。

    索引强度跌破 floor → 记忆仍在但"想不起来"（检索被过滤）。
    返回 GapSignal；无缺口返回 None。

    缺口强度 = 跌破深度累积 / 数量（0-1），并考虑最深缺口。
    """
    lost = [(s, age) for s, age in index_strengths if s < floor]
    if not lost:
        return None

    deficit = sum(floor - s for s, _ in lost)
    # 归一：每一条最多贡献 floor（strength=0 时）
    raw_strength = deficit / (len(lost) * floor)
    strength = min(1.0, raw_strength)
    oldest_age_days = max(age for _, age in lost)

    return GapSignal(
        strength=round(strength, 3),
        lost_count=len(lost),
        oldest_age_days=oldest_age_days,
    )
