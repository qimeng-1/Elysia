"""可控时钟协议：真实时钟与模拟时钟的统一接口。

设计（P0 决策 D3）：所有时间计算与等待经 Clock 注入，支撑验收门
"时钟延迟模拟有效"——SimulatedClock.jump() 可模拟身体离线、时钟延迟等场景。
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    """时间源协议：提供当前时间戳与异步等待。"""

    def now(self) -> float:
        """当前时间戳（Unix 秒）。"""
        ...

    async def sleep(self, seconds: float) -> None:
        """等待指定秒数（可被模拟时钟折算）。"""
        ...


class SystemClock:
    """真实时钟：墙钟时间 + asyncio 等待。"""

    def now(self) -> float:
        return time.time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class SimulatedClock:
    """模拟时钟：虚拟时间以 `speed` 倍速跟随真实时间，另支持跳时。

    **语义（自洽的一对）**：
      - `now()`    ：`virtual += (距上次调用真实耗时) × speed`——"时间在流逝"
      - `sleep(s)` ：真实等待 `s / speed`，虚拟推进 `s`
      两者正好吻合：`(s / speed) × speed = s`，所以等待与读时不会互相矛盾。
      - `jump(s)`  ：立即推进 `s`（模拟离线/时钟延迟）

    ⚠️ **写测试要注意**：因为 `now()` 会跟着真实耗时走，同一个时钟上
    "构造后隔了多久才读"会影响读数。所以**不要断言精确相等**——
    实测 `test_timesense_no_negative_after_body_offline` 里的
    `assert ts.presence_seconds() == 0.0` 单独跑通过、整套跑（机器有负载、
    真实耗时变长）就失败。要断言"约等于 0"请用容差（见该测试的修法）。

    本类只用于测试（生产走 `SystemClock`）。
    """

    def __init__(self, start_ts: float | None = None, *, speed: float = 1.0) -> None:
        self._virtual: float = start_ts if start_ts is not None else time.time()
        self._real: float = time.monotonic()
        self.speed: float = speed

    def now(self) -> float:
        self._advance()
        return self._virtual

    async def sleep(self, seconds: float) -> None:
        self._advance()
        await asyncio.sleep(seconds / self.speed)
        self._virtual += seconds

    def jump(self, seconds: float) -> None:
        """跳时：立即推进虚拟时间（模拟离线/时钟延迟）。"""
        self._advance()
        self._virtual += seconds

    def _advance(self) -> None:
        real_now = time.monotonic()
        self._virtual += (real_now - self._real) * self.speed
        self._real = real_now
