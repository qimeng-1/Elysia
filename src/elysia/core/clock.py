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
    """模拟时钟：虚拟时间推进，支持变速与跳时。

    - speed：虚拟时间相对真实时间的流速（如 60.0 = 60 倍速）
    - jump(seconds)：直接推进虚拟时间（模拟离线/时钟延迟）
    - sleep(seconds)：按速度折算为真实等待，虚拟时间同步推进
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
