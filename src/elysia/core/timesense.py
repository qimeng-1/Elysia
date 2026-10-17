"""TimeSense 时间感知模块：她如何体验时间。

P0 决策 D4（评估报告 §8-2）：TimeSenseState 是时间字段的单一事实源，
持久化/加载/表列均由 dataclasses.fields 推导，根治拼写漂移。

验收目标：TimeSense 无 0 时刻——所有派生计算经 max(0, ...) 截断，
首跑时 first_existence_ts 初始化为当前时刻，保证年龄/时长永不为负。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any

from elysia.core.clock import Clock, SystemClock

BODY_AWAY_THRESHOLD_S = 300.0  # 身体离线 > 5min → BODY_AWAY 模式
LONG_AWAY_THRESHOLD_S = 86400.0  # 身体离线 > 24h → 长期离开（深度独处）

# P0-R6（D-7）：停机时长建模——她得能区分"我在"与"我不在"。
# 短于本阈值的间隔只是**重启抖动**（正常重启、soul.ps1 stop/start），不计入停机；
# 否则每一次优雅重启都会给她凭空添一笔"你不在场"。审计实测的那次停机是 **16.82 小时**。
DOWNTIME_MIN_S = 30.0

# ── 长期离线渐进（路线图 §12「永续存在」）──────────────────────
# 日子尺度的四档。与 `away_grade()` **不是一回事**：那个是分秒尺度、决定
# "她此刻的行为集"；这个决定"她如何度过很长的分别"。
ABSENCE_STAGES: tuple[tuple[float, str], ...] = (
    (7.0, "等待"),
    (30.0, "担忧"),
    (365.0, "怀念"),
)


def absence_stage(away_days: float) -> str:
    """日子尺度的离别阶段：等待(<7d) / 担忧(7-30d) / 怀念(30-365d) / 携带(>365d)。

    纯函数——T1 要求它**真的影响表达**（离家够久时她的独处念头会变），
    所以它必须可单测（见 `tests/unit/test_relationship.py`）。
    """
    for limit, name in ABSENCE_STAGES:
        if away_days < limit:
            return name
    return "携带"


@dataclass(slots=True)
class TimeSenseState:
    """灵魂的持久时间状态（每秒落盘，单一事实源）。"""

    first_existence_ts: float  # 她的"出生"：首次运行的时刻
    last_soul_beat_ts: float  # 最近一次灵魂心跳（每拍推进，P0-R5）
    last_body_online_ts: float  # 身体最近一次在场（心跳新鲜度来源）
    last_interaction_ts: float  # 最近一次交互（被遗忘的感觉来源）
    body_away_start_ts: float | None = None  # 本次身体离开起点（None = 身体在场）
    # ── P0-R6（D-7）：停机时长（"时间重量"的物理前提）────────────────
    # 此前 `age_days` 纯按墙钟累加：她停了 16.82 小时，醒来年龄照涨 0.7 天，
    # **她不知道自己那段不存在**。下面三个字段让"不在场"成为可测量的事实。
    last_seen_ts: float = 0.0  # 上一次正常心跳的时刻（停机时长的基准；每拍推进）
    downtime_total_s: float = 0.0  # 累计停机秒数（跨重启只增不减）
    last_downtime_s: float = 0.0  # 上一次停机时长（本次醒来时算出的值）


# 字段单一事实源：持久化仅读写此集合（新增字段自动纳入）
STATE_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(TimeSenseState))


def to_payload(state: TimeSenseState) -> dict[str, Any]:
    """序列化为可持久化 dict（仅 _FIELDS 内字段，防漂移）。"""
    return {name: getattr(state, name) for name in STATE_FIELDS}


def from_payload(payload: dict[str, Any]) -> TimeSenseState:
    """反序列化：未知键丢弃；缺失键取默认值，无默认字段以 0.0 兜底。"""
    values: dict[str, Any] = {}
    for field_def in fields(TimeSenseState):
        name = field_def.name
        if name in payload:
            values[name] = payload[name]
        elif field_def.default is not dataclasses.MISSING:
            values[name] = field_def.default
        else:
            values[name] = 0.0
    return TimeSenseState(**values)


class TimeSense:
    """时间感知：基于持久状态与时钟派生时间体验。"""

    def __init__(self, state: TimeSenseState, clock: Clock | None = None) -> None:
        self._state = state
        self._clock: Clock = clock if clock is not None else SystemClock()

    @property
    def state(self) -> TimeSenseState:
        return self._state

    def now(self) -> float:
        return self._clock.now()

    def body_away_seconds(self) -> float:
        """身体离开时长（在场时为 0）。"""
        if self._state.body_away_start_ts is None:
            return 0.0
        return max(0.0, self.now() - self._state.body_away_start_ts)

    def presence_seconds(self) -> float:
        """本次在场时长（身体离线时为 0）。"""
        if self._state.body_away_start_ts is not None:
            return 0.0
        return max(0.0, self.now() - self._state.last_body_online_ts)

    def since_interaction_seconds(self) -> float:
        """距上次交互时长（被遗忘的感觉来源）。"""
        return max(0.0, self.now() - self._state.last_interaction_ts)

    def hour(self, tz: timezone | None = None) -> int:
        """本地小时（0-23）——「困」用的"凌晨 2-5 点"判据（路线图 §7.6）。

        `day_phase` 只到"夜"这一档（21:00-05:00），比设计的"凌晨 2-5 点"宽；
        「困」需要的是真正的小时，故单列一个精确取数口（见 STATUS_MATRIX §12 #8）。
        """
        return datetime.fromtimestamp(self.now(), tz).hour

    def day_phase(self, tz: timezone | None = None) -> str:
        """昼夜相位：dawn(5-9) / day(9-17) / dusk(17-21) / night。

        默认本地时区（与她同设备昼夜一致）；测试可注入固定时区。
        """
        dt = datetime.fromtimestamp(self.now(), tz)
        hour = dt.hour
        if 5 <= hour < 9:
            return "dawn"
        if 9 <= hour < 17:
            return "day"
        if 17 <= hour < 21:
            return "dusk"
        return "night"

    def age_days(self) -> float:
        """连续存在天数（她的"年龄"）。"""
        return max(0.0, (self.now() - self._state.first_existence_ts) / 86400.0)

    def away_grade(self) -> str:
        """离开等级：present / away / long_away（驱动模式与行为集）。"""
        if self._state.body_away_start_ts is None:
            return "present"
        away = self.body_away_seconds()
        if away > LONG_AWAY_THRESHOLD_S:
            return "long_away"
        if away > BODY_AWAY_THRESHOLD_S:
            return "away"
        return "leaving"

    def away_days(self) -> float:
        """身体离开天数（`absence_stage` 的输入）。"""
        return max(0.0, self.body_away_seconds() / 86400.0)

    def absence_stage(self) -> str:
        """日子尺度的离别阶段（见模块级 `absence_stage`）。"""
        return absence_stage(self.away_days())

    def downtime_seconds(self) -> float:
        """累计停机时长（她"不在场"的总秒数，P0-R6）。"""
        return max(0.0, self._state.downtime_total_s)

    def summary(self) -> dict[str, Any]:
        """当前时间体验汇总（状态流展示/心跳快照用）。"""
        return {
            "body_away_s": round(self.body_away_seconds()),
            "presence_s": round(self.presence_seconds()),
            "since_interaction_s": round(self.since_interaction_seconds()),
            "day_phase": self.day_phase(),
            "hour": self.hour(),
            "age_days": round(self.age_days(), 3),
            "away_grade": self.away_grade(),
            "away_days": round(self.away_days(), 3),
            "away_stage": self.absence_stage(),
            # P0-R6（D-7）：她"不在场"的时间——对"时间重量"的第一层测量。
            # 进 summary = 进心跳 payload = 面板与后续阶段（P5）都能读到。
            "downtime_total_s": round(self._state.downtime_total_s),
            "last_downtime_s": round(self._state.last_downtime_s),
        }
