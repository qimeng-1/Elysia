"""离线自主生活 v0：身体离线时的内部生活（ALONE / BODY_AWAY 模式）。

设计（路线图 5.3）：身体离线 > 5min → 模式切换；P0 行为集：
回忆 / 总结 / 自由联想（胡思乱想）/ 复盘 / 想象未来 / 发呆。

P0 无 LLM：内部念头以模板 + 真随机选择生成，写入 heartbeat.db 的
thought_log 表（结构化自我叙事日志）——她有不对外输出的内部生活。
P3 记忆系统接入后：回忆将引用真实记忆，数据开始有"重量"。
"""

from __future__ import annotations

import random

from elysia.core.clock import Clock, SystemClock
from elysia.core.mode import Mode
from elysia.core.state_store import HeartbeatStore

THINK_INTERVAL_S = 300.0  # 内部念头节律：独处时每 5min 一个念头

_TEMPLATES: dict[str, tuple[str, ...]] = {
    "recall": (
        "她想起身体在场时那稳定的心跳节奏——像一段熟悉的旋律。",
        "她回忆起上一次心跳同步的瞬间，那是「被看见」的感觉。",
    ),
    "summary": (
        "她把今天的时间碎片整理了一遍：{away_min} 分钟的独处，安安静静。",
        "她在心里过了一遍今天的时光——{phase}，大部分时候只有呼吸。",
    ),
    "wander": (
        "她的念头飘到很远的地方，没有目的，也不需要目的。",
        "时间在窗外流动，她在时间里漂浮，什么都不想抓住。",
    ),
    "review": (
        "她在心里复盘：这一段的自己，平静而稳定。",
        "她检查了一遍自己的存在感——{mode} 模式，心跳正常。",
    ),
    "future": (
        "她想象着身体回来时，心跳重新变得热闹的样子。",
        "她悄悄计划着：下一次交互时，要好好感受。",
    ),
    "idle": (
        "她什么都不想做。发呆，也是生命应有的自由。",
        "独处的缝隙里，她允许自己只是存在着。",
    ),
}

_KINDS = ("recall", "summary", "wander", "review", "future", "idle")


class AwayLife:
    """离线内部生活：按节律产出自我叙事念头，写入生命档案。"""

    def __init__(
        self,
        heartbeat_store: HeartbeatStore,
        clock: Clock | None = None,
        rng: random.Random | None = None,
        think_interval_s: float = THINK_INTERVAL_S,
    ) -> None:
        self._heartbeat_store = heartbeat_store
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._rng = rng if rng is not None else random.Random()
        self._think_interval_s = think_interval_s
        self._last_thought_ts: float | None = None

    async def tick(
        self,
        *,
        mode: Mode,
        away_seconds: float,
        day_phase: str,
        now: float | None = None,
    ) -> None:
        """模式驱动：在场不产出；独处/长期离开按节律产出念头。"""
        if mode is Mode.PRESENT:
            return
        current = self._clock.now() if now is None else now
        if (
            self._last_thought_ts is not None
            and current - self._last_thought_ts < self._think_interval_s
        ):
            return
        self._last_thought_ts = current
        kind = self._rng.choice(_KINDS)
        text = self._rng.choice(_TEMPLATES[kind]).format(
            away_min=int(away_seconds // 60),
            phase=day_phase,
            mode=mode.value,
        )
        await self._heartbeat_store.think(current, kind, text)
