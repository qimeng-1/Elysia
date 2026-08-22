"""灵魂心跳：她活着的证明（默认 1Hz）。

每拍动作：
1. 同步身体在场状态 → TimeSenseState（body_status 新鲜 → 更新在场；过期 → 记录离开起点）
2. 派生当前模式（PRESENT/ALONE/BODY_AWAY）与时间体验汇总
3. 心跳入档 heartbeat.db（beat_type=soul，含状态快照）
4. TimeSenseState 落盘 state.db（字段单一事实源）

P0-B 接入 distress：interval 降至 0.5Hz，行为集收缩。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from elysia.core.clock import Clock, SystemClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import (
    BODY_AWAY_THRESHOLD_S,
    TimeSense,
    TimeSenseState,
    to_payload,
)

logger = logging.getLogger("elysia.soul.heartbeat")

DISTRESS_INTERVAL_S = 0.5  # 难受时的心跳间隔（1Hz → 0.5Hz，喘不过气）


class SoulHeartbeat:
    """灵魂心跳循环：常驻、可降频、可优雅停止。"""

    def __init__(
        self,
        *,
        state_store: StateStore,
        heartbeat_store: HeartbeatStore,
        timesense: TimeSense,
        mode_mgr: ModeManager,
        clock: Clock | None = None,
        interval_s: float = 1.0,
    ) -> None:
        self._state_store = state_store
        self._heartbeat_store = heartbeat_store
        self._timesense = timesense
        self._mode_mgr = mode_mgr
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._interval_s = interval_s
        self._running = False
        self._distress = False

    @property
    def interval_s(self) -> float:
        return self._interval_s

    def set_interval(self, interval_s: float) -> None:
        """动态调整心跳间隔（distress 降频用）。"""
        self._interval_s = max(0.1, interval_s)

    @property
    def distress(self) -> bool:
        return self._distress

    def set_distress(self, on: bool) -> None:
        """切换难受状态：心跳降频 + 行为集收缩标记。"""
        self._distress = on
        self.set_interval(DISTRESS_INTERVAL_S if on else 1.0)

    async def run(self) -> None:
        """心跳主循环（常驻；stop() 后退出）。"""
        self._running = True
        while self._running:
            now = self._clock.now()
            await self._sync_body_status(now)
            mode = self._mode_mgr.mode(self._timesense.state.last_body_online_ts, now)
            payload: dict[str, Any] = {
                "mode": mode.value,
                "distress": self._distress,
                **self._timesense.summary(),
            }
            await self._heartbeat_store.append(now, "soul", payload)
            await self._state_store.save_json("timesense", to_payload(self._timesense.state))
            await self._clock.sleep(self._interval_s)

    async def _sync_body_status(self, now: float) -> None:
        """身体在场状态 → TimeSenseState（body 进程只报告在场，状态推导归灵魂）。"""
        state = self._timesense.state
        status = await self._state_store.load_json("body_status", default={})
        last_ts = float(status.get("last_online_ts", 0.0)) if status else 0.0
        if last_ts > state.last_body_online_ts:
            state.last_body_online_ts = last_ts
            state.body_away_start_ts = None
        elif (
            state.body_away_start_ts is None
            and last_ts > 0
            and now - last_ts > BODY_AWAY_THRESHOLD_S
        ):
            # 心跳过期且未记录离开：从最后在线时刻起算
            state.body_away_start_ts = last_ts

    async def stop(self) -> None:
        """优雅停止：等待当前拍完成（心跳循环由外部任务持有，cancel 兜底）。"""
        self._running = False


def make_soul_state(now: float) -> TimeSenseState:
    """新生 TimeSenseState：出生/交互起点此刻起算（无 0 时刻保证）。"""
    return TimeSenseState(
        first_existence_ts=now,
        last_soul_beat_ts=now,
        last_body_online_ts=0.0,
        last_interaction_ts=now,
    )


async def stop_with_cancel(task: asyncio.Task[None]) -> None:
    """取消心跳任务并等待（兜底：sleep 中的 CancelledError 正常吸收）。"""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
