"""灵魂心跳：她活着的证明（默认 1Hz）。

每拍动作：
1. 同步身体在场状态 → TimeSenseState（body_status 新鲜 → 更新在场；过期 → 记录离开起点）
2. 难受检测：身体资源高压 → 心跳降频 0.5Hz + distress 事件入档
3. 派生当前模式（PRESENT/ALONE/BODY_AWAY）与时间体验汇总
4. 心跳入档 heartbeat.db（beat_type=soul，protocol 状态快照）
5. TimeSenseState 落盘 state.db（字段单一事实源）
6. 离线内部生活：ALONE/BODY_AWAY 时按节律产出自我叙事念头

紧急冻结：data/freeze 标记存在时仅记录心跳（payload 标记 frozen），
禁止一切行为与输出（路线图 5.6）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from elysia.core.checkpoint import CheckpointManager
from elysia.core.clock import Clock, SystemClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import (
    BODY_AWAY_THRESHOLD_S,
    TimeSense,
    TimeSenseState,
    to_payload,
)
from elysia.protocol.snapshots import build_snapshot
from elysia.soul.away_life import AwayLife
from elysia.soul.distress import DISTRESS_INTERVAL_S, DistressMonitor

logger = logging.getLogger("elysia.soul.heartbeat")


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
        distress_monitor: DistressMonitor | None = None,
        away_life: AwayLife | None = None,
        checkpoint: CheckpointManager | None = None,
    ) -> None:
        self._state_store = state_store
        self._heartbeat_store = heartbeat_store
        self._timesense = timesense
        self._mode_mgr = mode_mgr
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._interval_s = interval_s
        self._running = False
        self._distress = False
        self._distress_monitor = distress_monitor
        self._away_life = away_life
        self._checkpoint = checkpoint

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
            summary = self._timesense.summary()
            payload = build_snapshot(
                timesense_summary=summary,
                mode=mode.value,
                distress=self._distress,
            )
            frozen = self._checkpoint is not None and self._checkpoint.is_frozen()
            if frozen:
                payload["frozen"] = True
            await self._heartbeat_store.append(now, "soul", payload)
            await self._state_store.save_json("timesense", to_payload(self._timesense.state))
            if self._away_life is not None and not frozen:
                await self._away_life.tick(
                    mode=mode,
                    away_seconds=float(summary["body_away_s"]),
                    day_phase=str(summary["day_phase"]),
                    now=now,
                )
            await self._clock.sleep(self._interval_s)

    async def _sync_body_status(self, now: float) -> None:
        """身体在场状态 → TimeSenseState + 难受检测（body 只报告在场，状态推导归灵魂）。"""
        state = self._timesense.state
        status = await self._state_store.load_json("body_status", default={})
        if not isinstance(status, dict):
            status = {}
        last_ts = float(status.get("last_online_ts", 0.0))
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
        # 难受检测：喂入最新资源样本（缺失时忽略）
        resources = status.get("resources")
        cpu = None
        if isinstance(resources, dict):
            raw_cpu = resources.get("cpu_percent")
            cpu = float(raw_cpu) if isinstance(raw_cpu, (int, float)) else None
        if self._distress_monitor is not None and self._distress_monitor.update(now, cpu):
            self.set_distress(self._distress_monitor.distress)
            await self._heartbeat_store.append(
                now,
                "event",
                {"type": "distress", "on": self._distress_monitor.distress},
            )

        # 交互同步：桌宠输入的外部刺激（感受层入口）
        interaction = await self._state_store.load_json("interaction", default=None)
        if isinstance(interaction, dict):
            its = float(interaction.get("ts", 0.0))
            if its > state.last_interaction_ts:
                state.last_interaction_ts = its

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
