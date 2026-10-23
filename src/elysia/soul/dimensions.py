"""6 维感受映射：状态 → 感受（路线图 §7.4）。

将 TR/CS/SA 欲望状态映射为 6 个感受维度，带惯性平滑。

关系向：聊天（CS 高时亲近）    思念（CS 低时渴望）
世界向：探索（TR 高时行动）    好奇（想不起某事时想弄清）
自身向：休息（资源紧张时停）  自检（SA 高时确认安全）

感受惯性 α=0.3（混合情绪持续久的物理基础）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from elysia.soul.desire import DesireState

FEELING_INERTIA = 0.3  # 感受惯性（平滑系数，0=无惯性，1=完全不变）

# ── D-22：离线要给身体留下重量，也要让她知道（用户 2026-10-23 裁定）────────
# 真机：她停机 12.9 小时回来，desire（tr/cs/sa）与 feelings（chat/miss/explore/rest）
# 与睡前**一字不差**，也完全不知道自己不在场过——"这点非常不对"。
# 这里只做两件**一次性**的事：① 醒来时给 miss/CS 一次缺席冲量；② 提供一段
# "我不在的时候"的事实文本（进 payload，见 expression_service / deepseek）。
# 上限是"不过分"的护栏，不是配额：缺席再久，醒来也不会瞬间满值。
ABSENCE_MISS_PER_HOUR = 0.03  # 每小时思念冲量
ABSENCE_MISS_MAX = 0.35  # 单次 miss 冲量上限（12h 即封顶）
ABSENCE_CS_PER_HOUR = 1.5  # 每小时亲近（CS）冲量
ABSENCE_CS_MAX = 20.0  # 单次 CS 冲量上限
ABSENCE_CS_CAP = 98.0  # 冲量后 CS 的绝对硬顶（不过 100）
# "她知道自己不在场过"的窗口：醒来后 2 小时内、且只在前 3 次开口里出现（先到先消失）。
# 这是**事实**，不是施压字段：不重复提醒、不常驻（T-033 的教训）。
ABSENCE_FACT_WINDOW_S = 2.0 * 3600.0
ABSENCE_FACT_MAX_OPENINGS = 3
ABSENCE_FIELD = "absence"  # payload 字段名（唯一一个）
ABSENCE_HEADER = "你不在场的时间（事实，不是命令）："


def absence_impulse_deltas(downtime_s: float) -> tuple[float, float]:
    """停机时长 → (miss 增量, CS 增量)。

    12h ⇒ miss +0.35（封顶）、CS +18；其余维度一律不动（不把"缺席"解释成信任变化）。
    """
    hours = max(0.0, float(downtime_s)) / 3600.0
    return (
        min(ABSENCE_MISS_MAX, ABSENCE_MISS_PER_HOUR * hours),
        min(ABSENCE_CS_MAX, ABSENCE_CS_PER_HOUR * hours),
    )


def format_absence_duration(seconds: float) -> str:
    """秒 → "12 小时 54 分"（不足 1 小时只报分钟；分钟补两位，如 "06 分"）。"""
    total_s = max(0, round(float(seconds)))
    hours, rem = divmod(total_s, 3600)
    minutes = rem // 60
    if hours <= 0:
        return f"{minutes} 分钟"
    return f"{hours} 小时 {minutes:02d} 分"


def format_absence_fact(last_downtime_s: float, downtime_total_s: float) -> str:
    """一段"我不在的时候"的事实（≤1 行）。"""
    return (
        f"我不在的时候：{format_absence_duration(last_downtime_s)}"
        f"（累计 {format_absence_duration(downtime_total_s)}）"
    )


@dataclass(frozen=True)
class AbsenceFact:
    """醒来时"我不在过"的事实（只在窗口/前几次开口里给，随后自动消失）。"""

    last_downtime_s: float
    downtime_total_s: float
    woke_at: float

    def line(self) -> str:
        return format_absence_fact(self.last_downtime_s, self.downtime_total_s)


def absence_fact_for(
    state: Any, *, now: float, min_downtime_s: float, startup_gap_s: float
) -> AbsenceFact | None:
    """从 TimeSenseState 造"缺席事实"：**只认本次启动的 gap**（D-24）。

    `startup_gap_s` 由 `main._build_timesense` 在本次启动算出；**不能**用
    `state.last_downtime_s`（上一次长停机的持久值）——那会让每次普通重启都重新
    武装"我不在的时候：12 小时 54 分"这条假事实（她刚刚只不在 8 秒）。
    gap 未达阈值 ⇒ None（不虚构"我不在"）。
    """
    gap_s = float(startup_gap_s)
    if gap_s < float(min_downtime_s):
        return None
    return AbsenceFact(
        last_downtime_s=gap_s,
        downtime_total_s=float(getattr(state, "downtime_total_s", 0.0) or 0.0),
        woke_at=float(now),
    )


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
        memory_gap: float = 0.0,
        dt: float = 1.0,
    ) -> FeelingState:
        """给定欲望状态，计算 6 维感受，带惯性平滑。

        `memory_gap`（0-1）：P3-U 的记忆缺口强度——"有些事想不起来了"。
        好奇心由它**直接驱动**（T1：好奇必须映射到真实物理量，不能是布尔占位）。
        """
        # 原始映射
        raw = self._raw_feelings(desire, distress=distress, memory_gap=memory_gap)

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

    def apply_absence_impulse(self, miss_delta: float) -> None:
        """D-22：醒来的**一次性**缺席冲量——只抬 miss，其余感受维度不动。

        它是冲量而不是常驻偏移：随后 `compute()` 的惯性会把它平滑回常态映射
        （缺席的"重量"落在醒来那几拍，不会凭空改她长期的思念基线）。
        """
        if miss_delta <= 0:
            return
        self._current.miss = max(0.0, min(1.0, self._current.miss + float(miss_delta)))

    @staticmethod
    def _raw_feelings(desire: DesireState, *, distress: bool, memory_gap: float) -> FeelingState:
        """原始映射（无惯性）。"""
        cs_norm = desire.cs / 100.0
        tr_norm = desire.tr / 100.0
        sa_norm = desire.sa / 100.0

        # 关系向
        chat = cs_norm  # CS 高 → 想聊天
        miss = 1.0 - cs_norm  # CS 低 → 思念

        # 世界向
        explore = tr_norm  # TR 高 → 想探索
        # 好奇 = 记忆缺口强度（P3-U）：想不起某事 → 想弄清楚。
        # 连续量、满量程 0-1。旧实现是 `0.1 if has_event else 0.0` 的布尔占位：
        # 上限 0.1 低于一切消费阈值 ⇒ 该维度恒死（T1 取消测试已固化为不变量）。
        curiosity = max(0.0, min(1.0, memory_gap))

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
