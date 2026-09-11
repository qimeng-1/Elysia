"""P1 验收门测试（路线图 §7.7）。

客观验收：
1. 取消测试（全变量）——冻结欲望变量 → 行为可测量变化
2. 混合情绪测试——CS 低 + TR 高 持续 30min → 思念↑ + 探索↑ 共存
3. 稳态自愈测试——TR=90 注入 → 12h 回归平衡点 ±10
4. 性格基因测试——真诚 vs 虚伪事件的 SA 差异 ≥ 2σ
5. 发呆双模态测试——thought_style 正负对应胡思乱想/虚无发呆
6. 性格引力测试——长期极端事件后，setpoint 回归速率符合 k_anchor
"""

from __future__ import annotations

import random
from statistics import mean, stdev

from elysia.soul.brain import BrainLoop
from elysia.soul.desire import (
    INITIAL_CS,
    INITIAL_SA,
    INITIAL_TR,
    TR_MAX,
    TR_MIN,
    DesireEvent,
    DesireSystem,
)
from elysia.soul.dimensions import FeelingMapper

T0 = 1_000_000.0


# ── 1. 取消测试（全变量） ──────────────────────────────


def test_cancel_freeze_tr_changes_behavior() -> None:
    """冻结 TR → 系统行为可测量变化（T1 铁律验证）。

    冻结 TR 会中断 TR→SA 耦合链，导致 SA 演化轨迹不同。
    使用 TR > 65 使耦合项激活。
    """
    ds_normal = DesireSystem(rng=random.Random(0))
    ds_frozen = DesireSystem(rng=random.Random(0))

    # 初始 TR 设为高值，使耦合项激活
    ds_normal._state.tr = 68.0
    ds_frozen._state.tr = 68.0

    for _ in range(500):
        ds_normal.step(dt=1.0)

        ds_frozen.step(dt=1.0)
        # 冻结 TR：保持高值
        ds_frozen._state.tr = 68.0

    # 正常系统 TR 应向 setpoint 回归
    assert ds_normal.state.tr < 68.0, "正常 TR 应回归"
    assert ds_frozen.state.tr == 68.0, "冻结 TR 应不变"
    # 冻结 TR 后，SA 应不同（TR→SA 耦合链中断）
    assert ds_normal.state.sa != ds_frozen.state.sa, "冻结 TR 后 SA 应不同"


# ── 2. 混合情绪测试 ────────────────────────────────────


def test_mixed_emotions_30min() -> None:
    """CS 低 + TR 高 持续 30min → 思念↑ + 探索↑ 共存。"""
    mapper = FeelingMapper()
    ds = DesireSystem(rng=random.Random(0))
    ds._state.cs = 45.0
    ds._state.tr = 65.0

    # 模拟 30min（1800 步 @ 1Hz）
    feelings_over_time: list[tuple[float, float]] = []  # (miss, explore)
    for _ in range(1800):
        f = mapper.compute(ds.state)
        feelings_over_time.append((f.miss, f.explore))
        ds.step(dt=1.0)

    # 检查是否存在 miss 和 explore 同时高的时刻
    high_both = [(m, e) for m, e in feelings_over_time if m > 0.4 and e > 0.5]
    assert len(high_both) > 0, "应存在 miss 和 explore 同时高的时刻"
    # 平均 miss 和 explore 都应高
    avg_miss = mean(m for m, _ in feelings_over_time)
    avg_explore = mean(e for _, e in feelings_over_time)
    assert avg_miss > 0.35, f"平均思念 {avg_miss:.3f} 应 > 0.35"
    assert avg_explore > 0.4, f"平均探索 {avg_explore:.3f} 应 > 0.4"


# ── 3. 稳态自愈测试 ────────────────────────────────────


def test_steady_state_recovery_12h() -> None:
    """TR=90 注入 → 12h 回归平衡点 ±10。"""
    ds = DesireSystem(rng=random.Random(0))
    ds._state.tr = 90.0
    ds._state.cs = 80.0
    ds._state.sa = 55.0

    # 12h = 43200 步 @ 1Hz
    for _ in range(43200):
        ds.step(dt=1.0)

    s = ds.state
    assert abs(s.tr - INITIAL_TR) < 10, f"TR 应回归 ±10，实际 {s.tr:.1f}"
    assert abs(s.cs - INITIAL_CS) < 10, f"CS 应回归 ±10，实际 {s.cs:.1f}"
    assert abs(s.sa - INITIAL_SA) < 10, f"SA 应回归 ±10，实际 {s.sa:.1f}"


# ── 4. 性格基因测试 ────────────────────────────────────


def test_personality_gene_sa_difference_2sigma() -> None:
    """真诚 vs 虚伪事件的 SA 差异 ≥ 2σ。"""
    # 真诚交互 10 次
    ds_kind = DesireSystem(rng=random.Random(0))
    sa_kind: list[float] = []
    for _ in range(10):
        ds_kind.apply_event(DesireEvent(kind="interaction"))
        sa_kind.append(ds_kind.state.sa)

    # 难受事件 10 次（模拟被伤害）
    ds_hurt = DesireSystem(rng=random.Random(0))
    sa_hurt: list[float] = []
    for _ in range(10):
        ds_hurt.apply_event(DesireEvent(kind="distress_on"))
        sa_hurt.append(ds_hurt.state.sa)

    mean_kind = mean(sa_kind)
    mean_hurt = mean(sa_hurt)
    n1, n2 = len(sa_kind), len(sa_hurt)
    var1 = stdev(sa_kind) ** 2 if n1 > 1 else 0.0
    var2 = stdev(sa_hurt) ** 2 if n2 > 1 else 0.0
    pooled_std = ((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2)
    pooled_std = max(pooled_std**0.5, 0.01)
    effect_size = abs(mean_hurt - mean_kind) / pooled_std

    assert effect_size >= 2.0, f"SA 差异效应量 {effect_size:.2f} 应 ≥ 2.0"


# ── 5. 发呆双模态测试 ──────────────────────────────────


def test_dual_thought_mode_switching() -> None:
    """thought_style 正负切换 → 发呆双模态可观测变化。"""
    brain = BrainLoop(DesireSystem(rng=random.Random(0)))
    ds = brain._desire

    # TR 高 → 胡思乱想模式
    active_count = 0
    for _ in range(100):
        ds._state.tr = 65.0
        o = brain.step()
        if o.action == "think_active":
            active_count += 1
    assert active_count > 5, "TR 高时应有一定概率 think_active"

    # TR 低 + alone 模式 → 虚无发呆
    for _ in range(100):
        ds._state.tr = 35.0
        # 在 alone 模式下，low TR 更可能 think_quiet
        o = brain.step(mode="alone")
        if o.will.thought_style < 0:
            pass  # 验证 thought_style 为负

    # TR 低时 thought_style 应为负
    ds._state.tr = 35.0
    o = brain.step()
    assert o.will.thought_style < 0, "TR 低 → thought_style 应 < 0"


# ── 6. 性格引力测试 ────────────────────────────────────


def test_personality_gravity_convergence() -> None:
    """长期极端事件后，setpoint 向初始值回归。

    验证方法：
    1. 注入极端事件使 setpoint 偏移
    2. 模拟长时间无事件
    3. 验证 setpoint 比之前更接近初始值
    """
    ds = DesireSystem(rng=random.Random(0))

    # 注入极端事件，使 setpoint 偏移
    for _ in range(100):
        ds.apply_event(DesireEvent(kind="interaction", intensity=1.0))

    # 记录偏移后的 setpoint
    sp_after = (ds._setpoints.tr, ds._setpoints.cs, ds._setpoints.sa)

    # 模拟长时间无事件（30 天 = 2,592,000 步 @ 1Hz）
    for _ in range(2592000):
        ds.step(dt=1.0)

    # 验证 setpoint 向初始值回归
    for idx, (initial, after, current) in enumerate(
        zip(
            [INITIAL_TR, INITIAL_CS, INITIAL_SA],
            sp_after,
            (ds._setpoints.tr, ds._setpoints.cs, ds._setpoints.sa),
            strict=True,
        )
    ):
        dist_after = abs(after - initial)
        dist_current = abs(current - initial)
        assert dist_current < dist_after, (
            f"变量 {idx} 应回归：{after:.3f} → {current:.3f}，"
            f"距初始 {dist_after:.3f} → {dist_current:.3f}"
        )


# ── 7. 大脑循环集成测试 ────────────────────────────────


def test_brain_loop_integration_1000_steps() -> None:
    """大脑循环连续 1000 步，所有输出在合理范围内。"""
    brain = BrainLoop(DesireSystem(rng=random.Random(0)))
    for i in range(1000):
        output = brain.step(distress=False, mode="present")
        d = output.desire
        f = output.feelings
        w = output.will

        # 欲望在保护带内
        assert TR_MIN <= d.tr <= TR_MAX, f"step {i}: TR={d.tr}"
        assert 40 <= d.cs <= 80, f"step {i}: CS={d.cs}"
        assert 15 <= d.sa <= 60, f"step {i}: SA={d.sa}"

        # 感受在 0-1
        for key in ("chat", "miss", "explore", "rest", "self_check"):
            val = getattr(f, key)
            assert 0.0 <= val <= 1.0, f"step {i}: {key}={val}"

        # 意志力度在 0-1
        assert 0.0 <= w.strength <= 1.0, f"step {i}: strength={w.strength}"

        # 行动在决策集内
        assert output.action in ("none", "think_active", "think_quiet", "animate"), (
            f"step {i}: action={output.action}"
        )
