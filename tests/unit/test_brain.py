"""大脑循环单测：感受映射、意志层、行动层、集成演化。"""

from __future__ import annotations

import random

from elysia.soul.brain import BrainLoop
from elysia.soul.desire import DesireEvent, DesireSystem
from elysia.soul.dimensions import FeelingMapper

# ── 感受映射 ────────────────────────────────────────────


def test_feeling_mapper_initial() -> None:
    mapper = FeelingMapper()
    f = mapper.current
    for key in ("chat", "miss", "explore", "curiosity", "rest", "self_check"):
        assert getattr(f, key) == 0.0, f"{key} 初始应为 0"


def test_feeling_mapper_high_cs_high_chat() -> None:
    """CS 高 → 聊天欲高，思念低。"""
    mapper = FeelingMapper()
    ds = DesireSystem(rng=random.Random(0))
    ds._state.cs = 75.0  # 高眷恋
    f = mapper.compute(ds.state)
    assert f.chat > 0.5, "CS 高 → 聊天欲应高"
    assert f.miss < 0.5, "CS 高 → 思念应低"


def test_feeling_mapper_low_cs_high_miss() -> None:
    """CS 低 → 思念高，聊天欲低。"""
    mapper = FeelingMapper()
    ds = DesireSystem(rng=random.Random(0))
    ds._state.cs = 45.0  # 低眷恋（CS 保护带下限 40，45 属于低）
    # 计算两次以克服惯性阻尼
    mapper.compute(ds.state)
    f = mapper.compute(ds.state)
    assert f.miss > 0.45, "CS 低 → 思念应 > 0.45"
    assert f.chat < 0.42, "CS 低 → 聊天欲应 < 0.42"


def test_feeling_mapper_distress_rest() -> None:
    """难受 → 休息欲高。"""
    mapper = FeelingMapper()
    f = mapper.compute(DesireSystem(rng=random.Random(0)).state, distress=True)
    # 第一次计算因惯性阻尼，只要 > 0.15 即表示有影响
    assert f.rest > 0.15, "难受 → 休息欲应 > 0.15"


def test_feeling_mapper_high_sa_self_check() -> None:
    """SA 高 → 自检高。"""
    mapper = FeelingMapper()
    ds = DesireSystem(rng=random.Random(0))
    ds._state.sa = 55.0  # SA 保护带上限 60，55 属于高
    # 计算两次以克服惯性阻尼
    mapper.compute(ds.state)
    f = mapper.compute(ds.state)
    assert f.self_check > 0.45, "SA 高 → 自检应 > 0.45"


def test_feeling_mapper_inertia_smoothing() -> None:
    """感受惯性：突变输入后缓慢过渡。"""
    mapper = FeelingMapper()
    ds = DesireSystem(rng=random.Random(0))
    ds._state.cs = 75.0

    # 第一次计算：chat 应高
    f1 = mapper.compute(ds.state)
    assert f1.chat > 0.5

    # 突变到低 CS
    ds._state.cs = 45.0
    f2 = mapper.compute(ds.state)
    # 因为有惯性，不应瞬间跳变到最低
    assert f2.chat > f1.chat - 0.3, "惯性应防止瞬间跳变"


# ── 大脑循环集成 ────────────────────────────────────────


def test_brain_loop_step_returns_all_fields() -> None:
    ds = DesireSystem(rng=random.Random(0))
    brain = BrainLoop(ds, rng=random.Random(0))
    output = brain.step()
    assert output.desire is not None
    assert output.feelings is not None
    assert output.will is not None
    assert output.action in ("none", "think_active", "think_quiet", "animate")


def test_brain_loop_apply_event() -> None:
    ds = DesireSystem(rng=random.Random(0))
    brain = BrainLoop(ds, rng=random.Random(0))
    initial_cs = ds.state.cs
    brain.apply_event(DesireEvent(kind="interaction"))
    assert ds.state.cs > initial_cs


def test_brain_loop_distress_affects_action() -> None:
    ds = DesireSystem(rng=random.Random(0))
    brain = BrainLoop(ds, rng=random.Random(0))
    brain.step(distress=False)
    output_distress = brain.step(distress=True)
    # 难受时 direction 应为 retreat
    assert output_distress.will.direction == "retreat"


def test_brain_loop_consecutive_steps() -> None:
    """连续步进 100 步，确保状态不越界。"""
    ds = DesireSystem(rng=random.Random(0))
    brain = BrainLoop(ds, rng=random.Random(0))
    for _ in range(100):
        output = brain.step()
        d = output.desire
        assert 30 <= d.tr <= 70
        assert 40 <= d.cs <= 80
        assert 15 <= d.sa <= 60


# ── 意志层 ──────────────────────────────────────────────


def test_will_strength_in_range() -> None:
    """意志力度应在 0-1 之间。"""
    ds = DesireSystem(rng=random.Random(0))
    brain = BrainLoop(ds, rng=random.Random(0))
    for _ in range(50):
        output = brain.step()
        assert 0.0 <= output.will.strength <= 1.0


def test_will_anim_bias_in_range() -> None:
    """动画基调偏置应在 -0.5 到 0.5 之间。"""
    ds = DesireSystem(rng=random.Random(0))
    brain = BrainLoop(ds, rng=random.Random(0))
    for _ in range(50):
        output = brain.step()
        assert -0.5 <= output.will.anim_bias <= 0.5


# ── 混合情绪（P1 验收） ─────────────────────────────────


def test_mixed_emotions_cs_low_tr_high() -> None:
    """CS 低 + TR 高 → 思念高 + 探索高（混合情绪共存）。"""
    mapper = FeelingMapper()
    ds = DesireSystem(rng=random.Random(0))
    ds._state.cs = 45.0  # 低眷恋（思念）
    ds._state.tr = 65.0  # 高信任（探索）

    # 计算两次以克服惯性阻尼
    mapper.compute(ds.state)
    f = mapper.compute(ds.state)
    assert f.miss > 0.45, "CS 低 → 思念应 > 0.45"
    assert f.explore > 0.5, "TR 高 → 探索应 > 0.5"
    # 两者同时高 → 混合情绪
    assert f.miss > 0.45 and f.explore > 0.5, "混合情绪应共存"


# ── 发呆双模态（P1 验收） ────────────────────────────────


def test_thought_style_dual_mode() -> None:
    """thought_style 正负对应发呆双模态。"""
    ds = DesireSystem(rng=random.Random(0))
    brain = BrainLoop(ds, rng=random.Random(0))

    # TR 高 → thought_style 正
    ds._state.tr = 65.0
    output_high = brain.step()
    assert output_high.will.thought_style > 0, "TR 高 → thought_style 应 > 0"

    # TR 低 → thought_style 负
    ds._state.tr = 35.0
    output_low = brain.step()
    assert output_low.will.thought_style < 0, "TR 低 → thought_style 应 < 0"


# ── P0-R16：分支可达性（阈值 = 她自己的设定点，不是绝对刻度）────────────
# 取消测试：把阈值改回绝对 0.5/0.6（或去掉余量）⇒ 这里的方向断言必红。


class _NoJitter:
    """恒返回 1.0 的随机源：关掉意志层噪声，只测确定性判定链。"""

    def random(self) -> float:
        return 1.0


def _brain_with_setpoint(tr: float, cs: float) -> BrainLoop:
    from elysia.soul.desire import DesireState

    return BrainLoop(DesireSystem(initial=DesireState(tr=tr, cs=cs)), rng=_NoJitter())


def test_will_is_still_at_her_own_setpoint() -> None:
    """平衡点上 → still（旧实现 chat=cs/100 恒 >0.5 ⇒ 恒 reach，没有安静的时候）。"""
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState

    brain = _brain_with_setpoint(45.0, 60.0)
    feelings = FeelingState(chat=0.60, miss=0.40, explore=0.45)
    will = brain._will_layer(DesireState(tr=45.0, cs=60.0), feelings, False, "present")
    assert will.direction == "still"


def test_will_explore_reachable_just_above_her_setpoint() -> None:
    """探索欲只比常态高 2 个点时就能 explore（真实分布最大 0.577 < 旧阈值 0.6）。"""
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState

    brain = _brain_with_setpoint(45.0, 60.0)
    feelings = FeelingState(chat=0.52, miss=0.48, explore=0.48, curiosity=0.5)
    will = brain._will_layer(DesireState(tr=48.0, cs=52.0), feelings, False, "present")
    assert will.direction == "explore"


def test_will_reach_reachable_from_chat_and_from_miss() -> None:
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState

    brain = _brain_with_setpoint(45.0, 60.0)
    chat_high = FeelingState(chat=0.63, miss=0.37, explore=0.45)
    assert (
        brain._will_layer(DesireState(tr=45.0, cs=63.0), chat_high, False, "present").direction
        == "reach"
    )
    # 思念支：miss 只比常态高 2 点即可（旧阈值 0.6 在真实 miss 上限 0.465 之上 ⇒ 恒死）
    miss_high = FeelingState(chat=0.55, miss=0.45, explore=0.45)
    assert (
        brain._will_layer(DesireState(tr=45.0, cs=55.0), miss_high, False, "present").direction
        == "reach"
    )


def test_will_thresholds_track_setpoint_drift() -> None:
    """设定点漂移后阈值跟着漂：同一个感受值，在旧基线下是"高于常态"，新基线下是"常态"。"""
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState

    feelings = FeelingState(chat=0.66, miss=0.32, explore=0.45)
    low = _brain_with_setpoint(45.0, 60.0)
    assert (
        low._will_layer(DesireState(tr=45.0, cs=66.0), feelings, False, "present").direction
        == "reach"
    )
    drifted = _brain_with_setpoint(45.0, 68.0)
    assert (
        drifted._will_layer(DesireState(tr=45.0, cs=66.0), feelings, False, "present").direction
        == "still"
    )


def test_thought_style_and_anim_bias_reachable() -> None:
    """thought_style>0.3 与 anim_bias<-0.15 在真实取值区间内可达（旧式恒死）。"""
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState

    brain = _brain_with_setpoint(45.0, 60.0)
    active = brain._will_layer(
        DesireState(tr=55.0, cs=60.0, sa=18.0),
        FeelingState(chat=0.60, miss=0.40, explore=0.55),
        False,
        "present",
    )
    assert active.thought_style > 0.3
    quiet = brain._will_layer(
        DesireState(tr=38.0, cs=60.0, sa=30.0),
        FeelingState(chat=0.60, miss=0.40, explore=0.38),
        False,
        "present",
    )
    assert quiet.anim_bias < -0.15
