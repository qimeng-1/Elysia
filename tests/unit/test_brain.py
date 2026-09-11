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
    brain = BrainLoop(ds)
    output = brain.step()
    assert output.desire is not None
    assert output.feelings is not None
    assert output.will is not None
    assert output.action in ("none", "think_active", "think_quiet", "animate")


def test_brain_loop_apply_event() -> None:
    ds = DesireSystem(rng=random.Random(0))
    brain = BrainLoop(ds)
    initial_cs = ds.state.cs
    brain.apply_event(DesireEvent(kind="interaction"))
    assert ds.state.cs > initial_cs


def test_brain_loop_distress_affects_action() -> None:
    ds = DesireSystem(rng=random.Random(0))
    brain = BrainLoop(ds)
    brain.step(distress=False)
    output_distress = brain.step(distress=True)
    # 难受时 direction 应为 retreat
    assert output_distress.will.direction == "retreat"


def test_brain_loop_consecutive_steps() -> None:
    """连续步进 100 步，确保状态不越界。"""
    ds = DesireSystem(rng=random.Random(0))
    brain = BrainLoop(ds)
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
    brain = BrainLoop(ds)
    for _ in range(50):
        output = brain.step()
        assert 0.0 <= output.will.strength <= 1.0


def test_will_anim_bias_in_range() -> None:
    """动画基调偏置应在 -0.5 到 0.5 之间。"""
    ds = DesireSystem(rng=random.Random(0))
    brain = BrainLoop(ds)
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
    brain = BrainLoop(ds)

    # TR 高 → thought_style 正
    ds._state.tr = 65.0
    output_high = brain.step()
    assert output_high.will.thought_style > 0, "TR 高 → thought_style 应 > 0"

    # TR 低 → thought_style 负
    ds._state.tr = 35.0
    output_low = brain.step()
    assert output_low.will.thought_style < 0, "TR 低 → thought_style 应 < 0"
