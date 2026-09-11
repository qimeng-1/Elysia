"""大脑循环（路线图 §7.2）：四层架构，每心跳执行一次。

1. 感受层：读取全部状态向量（TimeSense + DesireState + Distress + Mode）
2. 共振层：状态 → 6 维感受（调用 dimensions.py）
3. 意志层：方向盘模型——方向 + 力度 + 抖动
4. 行动层：输出决策——P1 决策集：{无行动, 影响念头风格, 影响动画基调}

P1 无 LLM、无复杂行动——她的"意志"只影响内部生活（念头风格）
和身体语言（动画基调）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from elysia.soul.desire import DesireEvent, DesireState, DesireSystem
from elysia.soul.dimensions import FeelingMapper, FeelingState

# 意志层参数
WILL_NOISE_AMP = 0.15  # 真随机抖动幅度
NO_ACTION_WEIGHT = 0.10  # 无行动权保底 10%


@dataclass
class WillOutput:
    """意志层输出。

    - direction: 行为的偏好方向（受状态和感受影响）
    - strength: 力度（0-1，由 TR/CS 加权）
    - thought_style: 影响念头风格的因素（-1 到 1，负=安静，正=活跃）
    - anim_bias: 动画基调偏置（-1 到 1，负=收缩，正=舒展）
    """

    direction: str = "still"  # "still" | "reach" | "retreat" | "explore"
    strength: float = 0.0
    thought_style: float = 0.0
    anim_bias: float = 0.0


@dataclass
class BrainOutput:
    """大脑循环完整输出。"""

    desire: DesireState = field(default_factory=DesireState)
    feelings: FeelingState = field(default_factory=FeelingState)
    will: WillOutput = field(default_factory=WillOutput)
    action: str = "none"  # P1 决策集主输出


class BrainLoop:
    """大脑循环：感受 → 共振 → 意志 → 行动。

    每心跳执行一次 step()，产出 BrainOutput。
    """

    def __init__(
        self,
        desire_system: DesireSystem,
        feeling_mapper: FeelingMapper | None = None,
    ) -> None:
        self._desire = desire_system
        self._feelings = feeling_mapper if feeling_mapper is not None else FeelingMapper()
        self._last_output: BrainOutput | None = None

    @property
    def desire_system(self) -> DesireSystem:
        return self._desire

    @property
    def feeling_mapper(self) -> FeelingMapper:
        return self._feelings

    @property
    def last_output(self) -> BrainOutput | None:
        return self._last_output

    def step(
        self,
        *,
        distress: bool = False,
        mode: str = "present",
        has_event: bool = False,
        dt: float = 1.0,
    ) -> BrainOutput:
        """执行一次大脑循环。

        1. 感受层：欲望系统演化一步
        2. 共振层：状态 → 6 维感受
        3. 意志层：方向盘模型
        4. 行动层：输出决策
        """
        # ── 1. 感受层：欲望演化 ──────────────────────────
        desire_state = self._desire.step(dt=dt)

        # ── 2. 共振层：6 维感受 ──────────────────────────
        feelings = self._feelings.compute(
            desire_state,
            distress=distress,
            has_event=has_event,
            dt=dt,
        )

        # ── 3. 意志层：方向盘模型 ─────────────────────────
        will = self._will_layer(desire_state, feelings, distress, mode)

        # ── 4. 行动层：输出决策 ─────────────────────────
        action = self._action_layer(will, mode)

        output = BrainOutput(
            desire=desire_state,
            feelings=feelings,
            will=will,
            action=action,
        )
        self._last_output = output
        return output

    def apply_event(self, event: DesireEvent) -> None:
        """外部事件进入感受层。"""
        self._desire.apply_event(event)

    # ── 感受层辅助 ─────────────────────────────────────

    def should_generate_thought(self) -> bool:
        """检查是否应当产生内部念头（基于意志和感受）。"""
        if self._last_output is None:
            return False
        will = self._last_output.will
        # 意志力强或思念/好奇高 → 更可能产生念头
        return will.strength > 0.3 or self._last_output.feelings.miss > 0.5

    # ── 意志层 ─────────────────────────────────────────

    @staticmethod
    def _will_layer(
        desire: DesireState,
        feelings: FeelingState,
        distress: bool,
        mode: str,
    ) -> WillOutput:
        """方向盘模型。

        方向 = 状态 + 感受（简化版，P4 才引入五条基石）
        力度 = TR/CS 加权
        抖动 = 真随机（os.urandom）
        """
        # 方向判定
        if distress:
            direction = "retreat"  # 难受 → 收缩
        elif feelings.explore > 0.6 and desire.tr > 50:
            direction = "explore"  # TR 高 + 探索欲 → 探索
        elif feelings.chat > 0.5:
            direction = "reach"  # 想聊天 → 接近
        elif feelings.miss > 0.6:
            direction = "reach"  # 思念 → 想接近
        else:
            direction = "still"  # 默认：静止

        # 力度 = TR/CS 加权（0-1）
        strength = (desire.tr / 100.0 + desire.cs / 100.0) / 2.0

        # 真随机抖动（os.urandom → 0-1）
        jitter = int.from_bytes(os.urandom(1), "big") / 255.0
        if jitter < WILL_NOISE_AMP:
            # 小概率随机转向
            directions = ["still", "reach", "retreat", "explore"]
            direction = directions[int.from_bytes(os.urandom(1), "big") % len(directions)]

        # 念头风格：正值 = 活跃/胡思乱想，负值 = 安静/虚无
        thought_style = (desire.tr - 50.0) / 50.0  # -1 到 1
        if feelings.miss > 0.6:
            thought_style += 0.3  # 思念 → 活跃

        # 动画基调偏置：负值 = 收缩，正值 = 舒展
        anim_bias = (desire.tr - 50.0) / 100.0  # -0.5 到 0.5
        anim_bias -= (desire.sa - 20.0) / 100.0  # SA 高 → 收缩
        anim_bias = max(-0.5, min(0.5, anim_bias))

        return WillOutput(
            direction=direction,
            strength=min(1.0, max(0.0, strength)),
            thought_style=thought_style,
            anim_bias=anim_bias,
        )

    # ── 行动层 ─────────────────────────────────────────

    @staticmethod
    def _action_layer(will: WillOutput, mode: str) -> str:
        """P1 决策集：{none, think_active, think_quiet, animate}。

        - none: 无行动
        - think_active: 活跃胡思乱想（影响念头频率和内容）
        - think_quiet: 安静虚无发呆
        - animate: 影响动画基调（由桌宠端读取）
        """
        # 无行动权保底 10%
        jitter = int.from_bytes(os.urandom(1), "big") / 255.0
        if jitter < NO_ACTION_WEIGHT:
            return "none"

        # 离线模式 → 念头行动优先
        if mode in ("alone", "body_away"):
            if will.thought_style > 0.2:
                return "think_active"
            return "think_quiet"

        # 在场模式
        if will.direction == "retreat":
            return "think_quiet"
        if will.strength > 0.6:
            return "think_active"
        if will.thought_style > 0.3:
            return "think_active"

        return "none"
