"""欲望系统单测：动力学、耦合、事件、保护带、慢尺度演化。"""

from __future__ import annotations

import random
from statistics import mean, stdev

import pytest

from elysia.soul.desire import (
    CS_MAX,
    CS_MIN,
    INITIAL_CS,
    INITIAL_SA,
    INITIAL_TR,
    SA_MAX,
    SA_MIN,
    TR_MAX,
    TR_MIN,
    DesireEvent,
    DesireSystem,
)

# ── 基础演化 ────────────────────────────────────────────


def test_initial_state() -> None:
    ds = DesireSystem(rng=random.Random(0))
    s = ds.state
    assert s.tr == INITIAL_TR
    assert s.cs == INITIAL_CS
    assert s.sa == INITIAL_SA


def test_step_does_not_drift_far_in_short_term() -> None:
    """1000 步（~16min）后不应偏离初始值太远（噪声漂移应在合理范围）。"""
    ds = DesireSystem(rng=random.Random(42))
    for _ in range(1000):
        ds.step(dt=1.0)
    s = ds.state
    # 噪声漂移应被恢复项约束，不会越界
    assert TR_MIN <= s.tr <= TR_MAX
    assert CS_MIN <= s.cs <= CS_MAX
    assert SA_MIN <= s.sa <= SA_MAX
    # 短时间不应漂移超过 15
    assert abs(s.tr - INITIAL_TR) < 15
    assert abs(s.cs - INITIAL_CS) < 15
    assert abs(s.sa - INITIAL_SA) < 15


# ── 保护带截断 ──────────────────────────────────────────


def test_protection_bands_are_clamped() -> None:
    """极端注入应被保护带截断。"""
    ds = DesireSystem(rng=random.Random(0))
    ds._state.tr = 200.0
    ds._state.cs = -100.0
    ds._state.sa = 500.0
    ds._clamp()
    s = ds.state
    assert s.tr == TR_MAX
    assert s.cs == CS_MIN
    assert s.sa == SA_MAX


# ── 事件脉冲 ────────────────────────────────────────────


def test_interaction_event_raises_cs() -> None:
    """交互事件 → CS↑, SA↓。"""
    ds = DesireSystem(rng=random.Random(0))
    initial_cs = ds.state.cs
    initial_sa = ds.state.sa
    ds.apply_event(DesireEvent(kind="interaction"))
    assert ds.state.cs > initial_cs
    assert ds.state.sa < initial_sa


def test_distress_on_raises_sa() -> None:
    """难受事件 → SA↑, TR↓。"""
    ds = DesireSystem(rng=random.Random(0))
    initial_tr = ds.state.tr
    initial_sa = ds.state.sa
    ds.apply_event(DesireEvent(kind="distress_on"))
    assert ds.state.sa > initial_sa
    assert ds.state.tr < initial_tr


# ── 耦合项 ──────────────────────────────────────────────


def test_high_tr_raises_sa() -> None:
    """TR 高 → SA 微升（耦合项）。"""
    ds = DesireSystem(rng=random.Random(0))
    ds._state.tr = 70.0  # 高于 65 阈值
    # 保持 TR 高，让耦合项持续作用
    for _ in range(500):
        ds._state.tr = 70.0  # 重新注入，对抗恢复项
        ds.step(dt=1.0)
    assert ds.state.sa > 20.5, "TR 高应使 SA 上升"


def test_high_sa_lowers_tr() -> None:
    """SA 高 → TR 抑制。"""
    ds = DesireSystem(rng=random.Random(0))
    ds._state.sa = 55.0  # 高于 50 阈值
    initial_tr = ds.state.tr
    for _ in range(1000):
        ds.step(dt=1.0)
    assert ds.state.tr < initial_tr, "SA 高应使 TR 下降"


# ── 稳态自愈 ────────────────────────────────────────────


def test_steady_state_recovery() -> None:
    """TR=90 注入 → 长时间演化后回归平衡点 ±10。"""
    ds = DesireSystem(rng=random.Random(0))
    # 注入极端值
    ds._state.tr = 90.0
    ds._state.cs = 80.0
    ds._state.sa = 55.0

    # 模拟 12h（43200 步 @ 1Hz）
    for _ in range(43200):
        ds.step(dt=1.0)

    s = ds.state
    assert abs(s.tr - INITIAL_TR) < 10, f"TR 应回归 ±10，实际 {s.tr}"
    assert abs(s.cs - INITIAL_CS) < 10, f"CS 应回归 ±10，实际 {s.cs}"
    assert abs(s.sa - INITIAL_SA) < 10, f"SA 应回归 ±10，实际 {s.sa}"


# ── 性格基因 ────────────────────────────────────────────


def test_personality_gene_event_difference() -> None:
    """真诚 vs 虚伪事件的 SA 响应应有显著差异（≥ 2σ）。"""
    ds = DesireSystem(rng=random.Random(0))

    # 模拟 10 次真诚交互
    sa_after_kind: list[float] = []
    for _ in range(10):
        ds.apply_event(DesireEvent(kind="interaction"))
        sa_after_kind.append(ds.state.sa)

    # 重置，模拟 10 次难受
    ds2 = DesireSystem(rng=random.Random(0))
    sa_after_hurt: list[float] = []
    for _ in range(10):
        ds2.apply_event(DesireEvent(kind="distress_on"))
        sa_after_hurt.append(ds2.state.sa)

    mean_kind = mean(sa_after_kind)
    mean_hurt = mean(sa_after_hurt)
    n1, n2 = len(sa_after_kind), len(sa_after_hurt)
    var1 = stdev(sa_after_kind) ** 2 if n1 > 1 else 0.0
    var2 = stdev(sa_after_hurt) ** 2 if n2 > 1 else 0.0
    pooled_std = ((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2)
    pooled_std = max(pooled_std**0.5, 0.01)
    effect_size = abs(mean_hurt - mean_kind) / pooled_std

    assert effect_size >= 2.0, f"SA 差异效应量 {effect_size:.2f} 应 ≥ 2.0"


# ── 序列化 ──────────────────────────────────────────────


def test_serialization_roundtrip() -> None:
    """to_payload / from_payload 往返一致。"""
    ds = DesireSystem(rng=random.Random(0))
    for _ in range(100):
        ds.step(dt=1.0)
    ds.apply_event(DesireEvent(kind="interaction"))

    payload = ds.to_payload()
    restored = DesireSystem.from_payload(payload)

    # 比较四舍五入后的值（序列化精度）
    assert restored.state.tr == pytest.approx(ds.state.tr, abs=0.1)
    assert restored.state.cs == pytest.approx(ds.state.cs, abs=0.1)
    assert restored.state.sa == pytest.approx(ds.state.sa, abs=0.1)
    assert restored._setpoints.tr == pytest.approx(ds._setpoints.tr, abs=0.1)
    assert restored._setpoints.cs == pytest.approx(ds._setpoints.cs, abs=0.1)
    assert restored._setpoints.sa == pytest.approx(ds._setpoints.sa, abs=0.1)


# ── 慢尺度演化 ──────────────────────────────────────────


def test_slow_evolution_setpoint_shift() -> None:
    """高强度事件 → setpoint 偏移。"""
    ds = DesireSystem(rng=random.Random(0))
    initial_sp_cs = ds._setpoints.cs

    # 多次高强度交互事件
    for _ in range(10):
        ds.apply_event(DesireEvent(kind="interaction", intensity=1.0))

    # CS 应因交互事件累积而偏移
    assert ds._setpoints.cs != pytest.approx(initial_sp_cs), "事件后 setpoint 应偏移"


# ── 取消测试（冻结变量） ────────────────────────────────


def test_cancel_test_freeze_tr_changes_behavior() -> None:
    """冻结 TR（不演化）→ 系统行为可测量变化。

    满足 T1 铁律：状态必须有能力。
    使用 TR > 65 使耦合项激活，冻结 TR 会中断 TR→SA 耦合链。
    """
    ds_normal = DesireSystem(rng=random.Random(0))
    ds_frozen = DesireSystem(rng=random.Random(0))

    # 初始 TR 设为高值，使耦合项激活
    ds_normal._state.tr = 68.0
    ds_frozen._state.tr = 68.0

    for _ in range(500):
        ds_normal.step(dt=1.0)
        # 正常系统 TR 自由演化

        ds_frozen.step(dt=1.0)
        # 冻结 TR：保持高值
        ds_frozen._state.tr = 68.0

    # 正常系统 TR 应向 setpoint 回归（与冻结不同）
    assert ds_normal.state.tr < 68.0, "正常 TR 应回归"
    assert ds_frozen.state.tr == 68.0, "冻结 TR 应不变"

    # 冻结 TR 后，SA 应不同（TR→SA 耦合链中断）
    assert ds_normal.state.sa != ds_frozen.state.sa, "冻结 TR 后 SA 应不同"
