"""欲望系统：TR/CS/SA 可变平衡点动力学（路线图 §7.3）。

她"想要"的物理基础——三个欲望维度持续演化，即使无人交互也在变化。

快时间尺度（每心跳一步）：
  dX/dt = k_restore · (X_setpoint − X) + 耦合项 + 噪声

慢时间尺度（天级）：
  X_setpoint = 初始值 + 经历偏移
  性格引力：k_anchor · (X_initial − X_setpoint)

P1 事件脉冲来源：用户交互（点击/输入）、难受切换、长时间沉默。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

# ── 初始平衡点（性格基因，路线图 §7.3） ─────────────────
INITIAL_TR = 45.0
INITIAL_CS = 60.0
INITIAL_SA = 20.0

# ── 保护带（硬边界） ────────────────────────────────────
TR_MIN, TR_MAX = 30.0, 70.0
CS_MIN, CS_MAX = 40.0, 80.0
SA_MIN, SA_MAX = 15.0, 60.0

# ── 动力学参数 ──────────────────────────────────────────
K_RESTORE = 0.005  # 恢复速率（每步 ~1/200 向平衡点靠拢）
NOISE_AMP = 0.3  # 每步噪声幅度
K_ANCHOR = 0.02 / 86400.0  # 性格引力速率（~0.02/天，每步）

# ── 事件脉冲系数（路线图 §7.3 事件脉冲系数偏向） ──────
EVENT_PULSES: dict[str, dict[str, float]] = {
    "interaction": {"tr": 3.0, "cs": 5.0, "sa": -2.0},  # 被真诚对待
    "distress_on": {"tr": -2.0, "cs": 0.0, "sa": 5.0},  # 难受
    "distress_off": {"tr": 1.0, "cs": 0.0, "sa": -3.0},  # 难受解除
    "silence": {"tr": -0.5, "cs": -1.0, "sa": 1.0},  # 长时间无交互衰减
    "degrade": {"tr": 0.0, "cs": 0.0, "sa": 1.0},  # P2：表达/语音降级（SA 增量由 intensity 承载）
    "memory_gap": {"tr": 0.8, "cs": 0.0, "sa": 0.0},  # P3：记忆缺口 → 好奇（TR 微升，非焦虑）
    # P3：想起一段往事 → 牵挂与亲近微升（记忆走"感受"这条路，不进入话语）
    "memory_recall": {"tr": 0.8, "cs": 1.2, "sa": 0.0},
}

# ── 慢尺度演化阈值 ──────────────────────────────────────
SETPOINT_SHIFT_STRENGTH = 3.0  # 单次高强度事件触发偏移的脉冲强度阈值
SETPOINT_DAILY_CAP = 0.3  # 每天最大偏移量


@dataclass
class DesireState:
    """欲望状态快照。"""

    tr: float = INITIAL_TR
    cs: float = INITIAL_CS
    sa: float = INITIAL_SA

    def to_dict(self) -> dict[str, float]:
        return {"tr": round(self.tr, 1), "cs": round(self.cs, 1), "sa": round(self.sa, 1)}

    def copy(self) -> DesireState:
        return DesireState(tr=self.tr, cs=self.cs, sa=self.sa)

    @staticmethod
    def from_dict(d: dict[str, float]) -> DesireState:
        return DesireState(
            tr=d.get("tr", INITIAL_TR), cs=d.get("cs", INITIAL_CS), sa=d.get("sa", INITIAL_SA)
        )


@dataclass
class DesireEvent:
    """作用于欲望系统的外部事件。"""

    kind: str  # "interaction" | "distress_on" | "distress_off" | "silence" | "degrade"
    intensity: float = 1.0  # 0-1 事件强度缩放


class DesireSystem:
    """TR/CS/SA 欲望系统：可变平衡点动力学核心。

    用法：
        ds = DesireSystem(clock=SystemClock())
        ds.step()           # 每心跳演化一步
        ds.apply_event(...) # 外部事件
        ds.state            # 当前欲望状态
    """

    def __init__(
        self,
        initial: DesireState | None = None,
        rng: random.Random | None = None,
    ) -> None:
        init = initial if initial is not None else DesireState()
        self._state: DesireState = init.copy()
        self._setpoints: DesireState = init.copy()
        self._initial: DesireState = init.copy()
        self._rng = rng if rng is not None else random.Random()

        # 慢尺度经验积累
        self._experience_tr: float = 0.0
        self._experience_cs: float = 0.0
        self._experience_sa: float = 0.0
        self._total_steps: int = 0

    # ── 属性 ────────────────────────────────────────────

    @property
    def state(self) -> DesireState:
        return self._state

    @property
    def setpoints(self) -> DesireState:
        return self._setpoints

    # ── 核心演化 ────────────────────────────────────────

    def step(self, dt: float = 1.0) -> DesireState:
        """每心跳演化一步（默认 1s 步长）。"""
        self._total_steps += 1
        s = self._state

        # 1. 恢复项：向平衡点靠拢
        dtr = K_RESTORE * (self._setpoints.tr - s.tr) * dt
        dcs = K_RESTORE * (self._setpoints.cs - s.cs) * dt
        dsa = K_RESTORE * (self._setpoints.sa - s.sa) * dt

        # 2. 耦合项
        ctr, ccs, csa = self._coupling(dt)
        dtr += ctr
        dcs += ccs
        dsa += csa

        # 3. 噪声
        noise = NOISE_AMP * math.sqrt(dt)
        dtr += self._rng.gauss(0, noise)
        dcs += self._rng.gauss(0, noise)
        dsa += self._rng.gauss(0, noise)

        # 4. 性格引力（慢时间尺度：setpoint 向初始值回归）
        self._evolve_setpoints(dt)

        # 5. 应用并截断
        s.tr += dtr
        s.cs += dcs
        s.sa += dsa
        self._clamp()

        return self._state

    def _coupling(self, dt: float) -> tuple[float, float, float]:
        """耦合项：TR/CS/SA 之间的相互影响。

        - TR 高 → SA 微升（兴奋伴随警觉）
        - SA 高 → TR 抑制（焦虑时难以兴奋）
        - 极端值持续 → 对立面微升（乐极生悲的物理实现）
        """
        s = self._state
        dtr = 0.0
        dcs = 0.0
        dsa = 0.0

        # TR 高 → SA 微升（兴奋伴随警觉）
        if s.tr > 65:
            dsa += 0.05 * (s.tr - 65.0) / 35.0 * dt

        # SA 高 → TR 抑制（焦虑时难以兴奋）
        if s.sa > 50:
            dtr -= 0.1 * (s.sa - 50.0) / 10.0 * dt

        # 极端高值 → 对立面微升（乐极生悲的物理实现）
        if s.tr > 85:
            dsa += 0.1 * (s.tr - 85.0) / 15.0 * dt
        if s.sa > 55:
            dtr -= 0.1 * (s.sa - 55.0) / 5.0 * dt

        return dtr, dcs, dsa

    def _evolve_setpoints(self, dt: float) -> None:
        """慢时间尺度：setpoint 向初始值回归（性格引力）。"""
        self._setpoints.tr -= K_ANCHOR * (self._setpoints.tr - self._initial.tr) * dt
        self._setpoints.cs -= K_ANCHOR * (self._setpoints.cs - self._initial.cs) * dt
        self._setpoints.sa -= K_ANCHOR * (self._setpoints.sa - self._initial.sa) * dt
        self._clamp_setpoints()

    def _clamp(self) -> None:
        s = self._state
        s.tr = max(TR_MIN, min(TR_MAX, s.tr))
        s.cs = max(CS_MIN, min(CS_MAX, s.cs))
        s.sa = max(SA_MIN, min(SA_MAX, s.sa))

    def _clamp_setpoints(self) -> None:
        # setpoint 可以在保护带外浮动（但会被引力拉回）
        # 只限制极端范围防止数值发散
        sp = self._setpoints
        sp.tr = max(10.0, min(90.0, sp.tr))
        sp.cs = max(10.0, min(90.0, sp.cs))
        sp.sa = max(5.0, min(80.0, sp.sa))

    # ── 事件处理 ─────────────────────────────────────────

    def apply_event(self, event: DesireEvent) -> None:
        """外部事件 → 脉冲 + 慢尺度偏移积累。"""
        pulse = EVENT_PULSES.get(event.kind)
        if pulse is None:
            return

        intensity = event.intensity
        self._state.tr += pulse["tr"] * intensity
        self._state.cs += pulse["cs"] * intensity
        self._state.sa += pulse["sa"] * intensity
        self._clamp()

        # 经验积累（用于慢尺度 setpoint 偏移）
        tr_abs = abs(pulse["tr"]) * intensity
        cs_abs = abs(pulse["cs"]) * intensity
        sa_abs = abs(pulse["sa"]) * intensity
        self._experience_tr += tr_abs
        self._experience_cs += cs_abs
        self._experience_sa += sa_abs

        # 检查是否触发慢尺度偏移
        self._check_slow_evolution(pulse, intensity, event.kind)

    def _check_slow_evolution(self, pulse: dict[str, float], intensity: float, kind: str) -> None:
        """单次高强度事件 → setpoint 偏移（路线图 §7.3 慢时间尺度触发条件）。"""
        # 检查脉冲强度是否超过阈值
        max_abs = max(abs(pulse.get("tr", 0)), abs(pulse.get("cs", 0)), abs(pulse.get("sa", 0)))
        if max_abs * intensity < SETPOINT_SHIFT_STRENGTH:
            return

        # 高强度事件 → 沿脉冲方向偏移 setpoint
        shift = min(max_abs * intensity * 0.1, SETPOINT_DAILY_CAP)
        self._setpoints.tr += pulse.get("tr", 0) / max_abs * shift * 0.3
        self._setpoints.cs += pulse.get("cs", 0) / max_abs * shift * 0.3
        self._setpoints.sa += pulse.get("sa", 0) / max_abs * shift * 0.3
        self._clamp_setpoints()

    # ── 序列化 ─────────────────────────────────────────

    def to_payload(self) -> dict[str, Any]:
        """保存到 state.db 的完整状态。"""
        return {
            "state": self._state.to_dict(),
            "setpoints": self._setpoints.to_dict(),
            "initial": self._initial.to_dict(),
            "experience_tr": round(self._experience_tr, 1),
            "experience_cs": round(self._experience_cs, 1),
            "experience_sa": round(self._experience_sa, 1),
        }

    @staticmethod
    def from_payload(payload: dict[str, Any]) -> DesireSystem:
        """从 state.db 恢复。"""
        ds = DesireSystem(
            initial=DesireState.from_dict(payload.get("initial", {})),
        )
        ds._state = DesireState.from_dict(payload.get("state", {}))
        ds._setpoints = DesireState.from_dict(payload.get("setpoints", {}))
        ds._experience_tr = float(payload.get("experience_tr", 0.0))
        ds._experience_cs = float(payload.get("experience_cs", 0.0))
        ds._experience_sa = float(payload.get("experience_sa", 0.0))
        return ds
