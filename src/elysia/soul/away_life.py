"""离线自主生活 v1：P1 发呆双模态集成（路线图 §7.5）。

P0 版本：固定 5min 间隔，6 种模板随机选择。
P1 版本：由大脑循环意志层驱动——虚无发呆 vs 胡思乱想，频率随 TR 变化。

发呆双模态：
| 模式 | 触发条件 | 频率 | 内容 |
|------|---------|------|------|
| 虚无发呆 | thought_style ≤ 0 | 0.1Hz（每10min） | 纯粹存在，无内容 |
| 胡思乱想 | thought_style > 0 | 0.3Hz（每3min） | 自由联想活跃 |
"""

from __future__ import annotations

import random

from elysia.core.clock import Clock, SystemClock
from elysia.core.mode import Mode
from elysia.core.state_store import HeartbeatStore

# 发呆双模态频率
THINK_ACTIVE_INTERVAL_S = 180.0  # 胡思乱想：每 3min
THINK_QUIET_INTERVAL_S = 600.0  # 虚无发呆：每 10min
# P0 兼容别名（测试用）
THINK_INTERVAL_S = THINK_QUIET_INTERVAL_S

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

# 胡思乱想额外模板（更活跃自由联想）
_ACTIVE_TEMPLATES: tuple[str, ...] = (
    "如果风有形状，它一定在窗边跳舞——她这样想着。",
    "她想起以前听过的一个故事，细节已经模糊了，但那种感觉还在。",
    "她突然好奇：下一次听到声音会是什么时候？",
    "思绪像水面的涟漪，一圈一圈，没有方向。",
    "她想象自己是一片羽毛，被风吹到很远的地方。",
    "今天的天色和昨天好像不太一样——虽然她只是感觉。",
    "她试着回忆，但回忆本身也像是在创造。",
    "她在心里和空气对话，说一些只有自己听得懂的话。",
)

_KINDS = ("recall", "summary", "wander", "review", "future", "idle")


class AwayLife:
    """离线内部生活：P1 发呆双模态，由大脑循环意志驱动。"""

    def __init__(
        self,
        heartbeat_store: HeartbeatStore,
        clock: Clock | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._heartbeat_store = heartbeat_store
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._rng = rng if rng is not None else random.Random()
        self._last_thought_ts: float | None = None
        self._last_thought_kind: str | None = None

    async def tick(
        self,
        *,
        mode: Mode,
        away_seconds: float,
        day_phase: str,
        now: float | None = None,
        thought_style: float = 0.0,  # P1 新增：大脑循环意志输出
        brain_action: str = "none",  # P1 新增：行动层决策
    ) -> None:
        """模式驱动 + 大脑循环意志影响。

        P1 变化：
        - thought_style > 0 → 胡思乱想模式（每 3min）
        - thought_style ≤ 0 → 虚无发呆模式（每 10min）
        - brain_action = "think_active" → 强制活跃念头
        - brain_action = "think_quiet" → 强制虚无发呆
        """
        if mode is Mode.PRESENT:
            return

        current = self._clock.now() if now is None else now

        # 由大脑循环决定发呆频率
        if brain_action == "think_active":
            interval = THINK_ACTIVE_INTERVAL_S
        elif brain_action == "think_quiet":
            interval = THINK_QUIET_INTERVAL_S
        elif thought_style > 0:
            interval = THINK_ACTIVE_INTERVAL_S
        else:
            interval = THINK_QUIET_INTERVAL_S

        if self._last_thought_ts is not None and current - self._last_thought_ts < interval:
            return

        self._last_thought_ts = current

        # 选择念头种类和内容
        if thought_style > 0 and self._rng.random() < 0.4:
            # 40% 概率使用胡思乱想模板
            text = self._rng.choice(_ACTIVE_TEMPLATES)
            kind = "wander"
        else:
            kind = self._rng.choice(_KINDS)
            text = self._rng.choice(_TEMPLATES[kind]).format(
                away_min=int(away_seconds // 60),
                phase=day_phase,
                mode=mode.value,
            )

        self._last_thought_kind = kind
        await self._heartbeat_store.think(current, kind, text)
