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
# 每"新想不起来一件事"给的缺口强度（1 条就越过消费阈值 0.4；多条封顶 1.0）。
# P0-R16：缺口是**事件**（有些事刚刚变模糊），不是常驻背景——若用"平均模糊度"做常驻
# 信号，她会永久处于高好奇，那不是机制可达，而是性格变形。
GAP_PER_NEW_MEMORY = 0.5

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


@dataclass(frozen=True)
class EpisodicGap:
    """一轮"刚刚想不起来"的缺口事件（P0-R16）。

    - strength: 0-1 缺口强度（按**新增**丢失记忆计，1 条即 0.5）
    - newly_lost: 本轮新跌破检索下限的记忆条数
    - lost_total: 当前累计跌破下限的记忆条数
    """

    strength: float
    newly_lost: int
    lost_total: int

    def to_event_intensity(self) -> float:
        return min(GAP_INTENSITY_MAX, self.strength)


def detect_episodic_gap(
    samples: list[tuple[int, float]],
    *,
    previously_lost: set[int],
    floor: float = STRENGTH_RETRIEVE_FLOOR,
) -> tuple[EpisodicGap | None, set[int]]:
    """检测"这一轮新增了哪些想不起来的事"（事件式缺口）。

    Args:
        samples: `[(memory_id, strength)]`（只含在册、可参与缺口的记忆）
        previously_lost: 上一轮已跌破下限的 memory_id 集合
        floor: 检索下限（默认按"3 周开始模糊"推导）

    Returns:
        (缺口事件或 None, 本轮最新的丢失集合)。调用方负责保存集合并让
        `_latest_memory_gap` 在没有新事件时回落到 0（好奇心只对新缺口起反应）。
    """
    lost = {mid for mid, strength in samples if strength < floor}
    newly = lost - previously_lost
    if not newly:
        return None, lost
    strength = min(1.0, GAP_PER_NEW_MEMORY * len(newly))
    return (
        EpisodicGap(strength=round(strength, 3), newly_lost=len(newly), lost_total=len(lost)),
        lost,
    )


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
