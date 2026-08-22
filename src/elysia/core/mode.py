"""模式管理 v0：她的存在状态机。

P0 三态（按身体离线时长分档，阈值与 TimeSense 共享常量）：
- PRESENT    身体在场（心跳新鲜，<= 5min）
- ALONE      身体离线 > 5min（独处：回忆/总结等内部生活）
- BODY_AWAY  身体离线 > 24h（长期离开：深度自由联想）

P1+ 扩展：发呆（在场但无交互）/ 睡眠整合（关机）。
"""

from __future__ import annotations

from enum import StrEnum

from elysia.core.clock import Clock, SystemClock
from elysia.core.timesense import BODY_AWAY_THRESHOLD_S, LONG_AWAY_THRESHOLD_S


class Mode(StrEnum):
    PRESENT = "present"
    ALONE = "alone"
    BODY_AWAY = "body_away"


class ModeManager:
    """由身体心跳新鲜度派生当前模式（纯函数，无副作用）。"""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()

    def mode(self, last_body_online_ts: float, now: float | None = None) -> Mode:
        """根据身体最近在线时刻判定模式。

        last_body_online_ts <= 0 表示从未有身体心跳（新生/数据缺失）：
        视为在场等待首报，不产生"离开"恐慌（无 0 时刻精神）。
        """
        if last_body_online_ts <= 0:
            return Mode.PRESENT
        current = self._clock.now() if now is None else now
        away = max(0.0, current - last_body_online_ts)
        if away > LONG_AWAY_THRESHOLD_S:
            return Mode.BODY_AWAY
        if away > BODY_AWAY_THRESHOLD_S:
            return Mode.ALONE
        return Mode.PRESENT

    def away_seconds(self, last_body_online_ts: float, now: float | None = None) -> float:
        """身体离线时长（秒）。"""
        current = self._clock.now() if now is None else now
        return max(0.0, current - last_body_online_ts)
