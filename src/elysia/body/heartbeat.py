"""身体心跳：她"感觉到"你（默认 5s）。

每拍动作：
1. 心跳入档 heartbeat.db（beat_type=body）
2. 报告在场 → state.db body_status（灵魂据此推导 TimeSense 在场/离开）

P0-B 接入资源感知：payload 含 CPU/内存采样（可注入，供难受测试）。
"""

from __future__ import annotations

from typing import Any

from elysia.core.clock import Clock, SystemClock
from elysia.core.state_store import HeartbeatStore, StateStore

BODY_STATUS_KEY = "body_status"


class BodyHeartbeat:
    """身体心跳循环：随设备在场，可优雅停止。"""

    def __init__(
        self,
        *,
        state_store: StateStore,
        heartbeat_store: HeartbeatStore,
        clock: Clock | None = None,
        interval_s: float = 5.0,
    ) -> None:
        self._state_store = state_store
        self._heartbeat_store = heartbeat_store
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._interval_s = interval_s
        self._running = False
        self._resource_sampler: Any = None  # P0-B 注入资源采样器

    def attach_resource_sampler(self, sampler: Any) -> None:
        """绑定资源采样器（P0-B）：返回 dict 供心跳快照。"""
        self._resource_sampler = sampler

    async def run(self) -> None:
        """心跳主循环（常驻；stop() 后退出）。"""
        self._running = True
        while self._running:
            now = self._clock.now()
            payload: dict[str, Any] = {}
            if self._resource_sampler is not None:
                payload["resources"] = self._resource_sampler()
            await self._heartbeat_store.append(now, "body", payload)
            await self._state_store.save_json(
                BODY_STATUS_KEY, {"last_online_ts": now, "status": "online"}
            )
            await self._clock.sleep(self._interval_s)

    async def stop(self) -> None:
        """优雅停止：等待当前拍完成。"""
        self._running = False
