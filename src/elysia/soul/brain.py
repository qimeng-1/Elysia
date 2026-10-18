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
import random
from dataclasses import dataclass, field

from elysia.soul.desire import DesireEvent, DesireState, DesireSystem
from elysia.soul.dimensions import FeelingMapper, FeelingState

# 意志层参数
WILL_NOISE_AMP = 0.15  # 真随机抖动幅度
NO_ACTION_WEIGHT = 0.10  # 无行动权保底 10%

# ── 方向阈值（P0-R16：分支可达性）────────────────────────────────────
# 旧实现用**绝对阈值**：explore 要 `feeling>0.6 and tr>50`、chat 要 `>0.5`、miss 要 `>0.6`。
# 而她 40,000 拍真实分布是：chat 0.535–0.739、explore 0.378–0.577、miss 0.261–0.465 ——
# explore/miss 的**结构上界**都低于阈值（机制永不触发，只剩噪声抖动），
# chat 0.5 低于整段区间（恒成立 ⇒ direction 恒 reach）。
# 根因不是感受映射错了：感受就是欲望的线性强度（她"想要多少"）；错在阈值拿**绝对刻度**
# 去卡一个**因人而异、还会随经历漂移**的平衡点（她的 setpoints 已从 45/60 漂到 46.3/61.9）。
# 修法：阈值 = 她自己的设定点 + 很小余量 ⇒ "比自己的常态更想要"才行动。
# 阈值随设定点漂移自动重标定；不新增机制，也不改她的感受数值。
WILL_DEVIATION_MARGIN = 0.02  # 0-1 量纲（≈2 个 TR/CS 点）；防在平衡点上抖动
WILL_STYLE_SCALE = 10.0  # thought_style：每偏离设定点 1 点记 0.1（-1..1 量纲）


class _CryptoRNG(random.Random):
    """真随机源：用 os.urandom 提供随机性，覆写 random() 保证不可预测。

    兼容 random.Random 的接口（测试可注入固定 random.Random(seed) 保证确定性）。
    """

    def __init__(self) -> None:
        super().__init__()

    def random(self) -> float:
        return int.from_bytes(os.urandom(4), "big") / (2**32 - 1)


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
        rng: random.Random | None = None,
    ) -> None:
        self._desire = desire_system
        self._feelings = feeling_mapper if feeling_mapper is not None else FeelingMapper()
        # 意志/行动层的随机源：默认用 os.urandom 真随机；测试可注入固定 rng 保证确定性
        self._rng = rng if rng is not None else _CryptoRNG()
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
        memory_gap: float = 0.0,
        dt: float = 1.0,
    ) -> BrainOutput:
        """执行一次大脑循环。

        `memory_gap`（0-1）：本拍记忆缺口强度，**直接驱动好奇心**（T1）——
        "有些事想不起来了"是好奇的真实来源（见 `memory/hooks.py`）。

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
            memory_gap=memory_gap,
            dt=dt,
        )

        # ── 3. 意志层：方向盘模型 ─────────────────────────
        will = self._will_layer(desire_state, feelings, distress, mode)

        # ── 4. 行动层：输出决策 ─────────────────────────
        action = self._action_layer(will, mode)

        output = BrainOutput(
            # ⚠️ 必须拷贝成**快照**：`DesireSystem.step()` 返回 `self._state`、
            # `FeelingMapper.compute()` 返回 `self._current`，两者都是心跳每拍**原地改写**
            # 的内部对象。若 BrainOutput 直接持有它们，交给 worker **异步执行**的
            # `ExpressionJob` 就会读到"执行那一刻"的状态，而不是"决定开口那一刻"的状态
            # （与 `created_ts` 锚定决策时刻的口径矛盾；见 STATUS_MATRIX §12 缺陷 #1）。
            desire=desire_state.copy(),
            feelings=feelings.copy(),
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

    def _will_layer(
        self,
        desire: DesireState,
        feelings: FeelingState,
        distress: bool,
        mode: str,
    ) -> WillOutput:
        """方向盘模型。

        方向 = 状态 + 感受（简化版，P4 才引入五条基石）
        力度 = TR/CS 加权
        抖动 = 随机（默认 os.urandom，测试可注入固定 rng）
        """
        # 方向判定（P0-R16：阈值 = 她自己的设定点 + 小余量，见文件头）
        setpoints = self._desire.setpoints
        sp_tr = setpoints.tr / 100.0
        sp_cs = setpoints.cs / 100.0
        sp_miss = 1.0 - sp_cs
        if distress:
            direction = "retreat"  # 难受 → 收缩
        elif feelings.explore > sp_tr + WILL_DEVIATION_MARGIN and desire.tr > setpoints.tr:
            direction = "explore"  # 探索欲高于自己的常态 → 探索
        elif feelings.chat > sp_cs + WILL_DEVIATION_MARGIN:
            direction = "reach"  # 亲近欲高于常态 → 接近
        elif feelings.miss > sp_miss + WILL_DEVIATION_MARGIN:
            direction = "reach"  # 思念高于常态 → 想接近
        else:
            direction = "still"  # 在/低于常态：静止

        # 力度 = TR/CS 加权（0-1）
        strength = (desire.tr / 100.0 + desire.cs / 100.0) / 2.0

        # 随机抖动（默认 os.urandom → 0-1）
        jitter = self._rng.random()
        if jitter < WILL_NOISE_AMP:
            # 小概率随机转向
            directions = ["still", "reach", "retreat", "explore"]
            direction = directions[int(self._rng.random() * len(directions)) % len(directions)]

        # 念头风格：正值 = 活跃/胡思乱想，负值 = 安静/虚无
        # P0-R16：旧式 `(tr-50)/50` 在真实 TR（37.8–57.7）上只到 0.15，
        # 使 `thought_style>0.3`（think_active）与离线 `>0.2` 都恒不成立；
        # 改成以她的设定点为 0 点、每点 0.1 ⇒ 真实区间覆盖 -0.85..+1.14。
        thought_style = (desire.tr - setpoints.tr) / WILL_STYLE_SCALE
        if feelings.miss > sp_miss + WILL_DEVIATION_MARGIN:
            thought_style += 0.3  # 思念高于常态 → 活跃
        thought_style = max(-1.0, min(1.0, thought_style))

        # 动画基调偏置：负值 = 收缩，正值 = 舒展
        # P0-R16：以她的设定点为 0 点（原先以 50/20 为 0 点，随经历漂移后会长期偏一个方向）；
        # 量纲仍是 ~±0.2 的温和偏置。
        anim_bias = (desire.tr - setpoints.tr) / 100.0
        anim_bias -= (desire.sa - setpoints.sa) / 100.0  # SA 高于常态 → 收缩
        anim_bias = max(-0.5, min(0.5, anim_bias))

        return WillOutput(
            direction=direction,
            strength=min(1.0, max(0.0, strength)),
            thought_style=thought_style,
            anim_bias=anim_bias,
        )

    # ── 行动层 ─────────────────────────────────────────

    def _action_layer(self, will: WillOutput, mode: str) -> str:
        """P1 决策集：{none, think_active, think_quiet, animate}。

        - none: 无行动
        - think_active: 活跃胡思乱想（影响念头频率和内容）
        - think_quiet: 安静虚无发呆
        - animate: 影响动画基调（由桌宠端读取）
        """
        # 无行动权保底 10%
        jitter = self._rng.random()
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
