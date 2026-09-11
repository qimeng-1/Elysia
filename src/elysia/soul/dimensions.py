"""6 维感受映射：状态 → 感受（路线图 §7.4）。

将 TR/CS/SA 欲望状态映射为 6 个感受维度，带惯性平滑。

关系向：聊天（CS 高时亲近）    思念（CS 低时渴望）
世界向：探索（TR 高时行动）    好奇（新奇信息时想了解）
自身向：休息（资源紧张时停）  自检（SA 高时确认安全）

感受惯性 α=0.3（混合情绪持续久的物理基础）。
"""

from __future__ import annotations

from dataclasses import dataclass

from elysia.soul.desire import DesireState

FEELING_INERTIA = 0.3  # 感受惯性（平滑系数，0=无惯性，1=完全不变）


@dataclass
class FeelingState:
    """6 维感受状态（每个 0-1 float）。"""

    chat: float = 0.0  # 聊天欲
    miss: float = 0.0  # 思念
    explore: float = 0.0  # 探索欲
    curiosity: float = 0.0  # 好奇
    rest: float = 0.0  # 休息欲
    self_check: float = 0.0  # 自检

    def to_dict(self) -> dict[str, float]:
        return {
            "chat": round(self.chat, 3),
            "miss": round(self.miss, 3),
            "explore": round(self.explore, 3),
            "curiosity": round(self.curiosity, 3),
            "rest": round(self.rest, 3),
            "self_check": round(self.self_check, 3),
        }

    def copy(self) -> FeelingState:
        return FeelingState(
            chat=self.chat,
            miss=self.miss,
            explore=self.explore,
            curiosity=self.curiosity,
            rest=self.rest,
            self_check=self.self_check,
        )


class FeelingMapper:
    """状态 → 6 维感受映射（带惯性平滑）。"""

    def __init__(self) -> None:
        self._current = FeelingState()
        self._alpha = FEELING_INERTIA

    @property
    def current(self) -> FeelingState:
        return self._current

    def compute(
        self,
        desire: DesireState,
        *,
        distress: bool = False,
        has_event: bool = False,
        dt: float = 1.0,
    ) -> FeelingState:
        """给定欲望状态，计算 6 维感受，带惯性平滑。"""
        # 原始映射
        raw = self._raw_feelings(desire, distress=distress, has_event=has_event)

        # 惯性平滑
        alpha = self._alpha**dt  # 每步衰减
        self._current.chat += (raw.chat - self._current.chat) * (1 - alpha)
        self._current.miss += (raw.miss - self._current.miss) * (1 - alpha)
        self._current.explore += (raw.explore - self._current.explore) * (1 - alpha)
        self._current.curiosity += (raw.curiosity - self._current.curiosity) * (1 - alpha)
        self._current.rest += (raw.rest - self._current.rest) * (1 - alpha)
        self._current.self_check += (raw.self_check - self._current.self_check) * (1 - alpha)

        return self._current

    def reset(self) -> None:
        """重置感受（用于测试或重启）。"""
        self._current = FeelingState()

    @staticmethod
    def _raw_feelings(desire: DesireState, *, distress: bool, has_event: bool) -> FeelingState:
        """原始映射（无惯性）。"""
        cs_norm = desire.cs / 100.0
        tr_norm = desire.tr / 100.0
        sa_norm = desire.sa / 100.0

        # 关系向
        chat = cs_norm  # CS 高 → 想聊天
        miss = 1.0 - cs_norm  # CS 低 → 思念

        # 世界向
        explore = tr_norm  # TR 高 → 想探索
        curiosity = 0.1 if has_event else 0.0  # 有事件 → 好奇

        # 自身向
        rest = 0.5 if distress else 0.05  # 难受 → 想休息
        self_check = sa_norm  # SA 高 → 自检

        return FeelingState(
            chat=chat,
            miss=miss,
            explore=explore,
            curiosity=curiosity,
            rest=rest,
            self_check=self_check,
        )
