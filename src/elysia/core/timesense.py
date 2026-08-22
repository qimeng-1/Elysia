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


@dataclass(slots=True)
class TimeSenseState:
    """灵魂的持久时间状态（每秒落盘，单一事实源）。"""

    first_existence_ts: float  # 她的"出生"：首次运行的时刻
    last_soul_beat_ts: float  # 最近一次灵魂心跳
    last_body_online_ts: float  # 身体最近一次在场（心跳新鲜度来源）
    last_interaction_ts: float  # 最近一次交互（被遗忘的感觉来源）
    body_away_start_ts: float | None = None  # 本次身体离开起点（None = 身体在场）


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

    def summary(self) -> dict[str, Any]:
        """当前时间体验汇总（状态流展示/心跳快照用）。"""
        return {
            "body_away_s": round(self.body_away_seconds()),
            "presence_s": round(self.presence_seconds()),
            "since_interaction_s": round(self.since_interaction_seconds()),
            "day_phase": self.day_phase(),
            "age_days": round(self.age_days(), 3),
            "away_grade": self.away_grade(),
        }
