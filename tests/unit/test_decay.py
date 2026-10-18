"""P3-C 索引衰减 + 缺口信号单测。"""

from __future__ import annotations

import pytest

from elysia.memory.decay import (
    FUZZY_AGE_DAYS,
    LATENCY_30D_MS,
    LATENCY_90D_MS,
    LATENCY_365D_MS,
    STRENGTH_RETRIEVE_FLOOR,
    STRENGTH_TAU_DAYS,
    decay_strength,
    effective_age_days,
    retrieve_latency_ms,
)
from elysia.memory.hooks import (
    GAP_EVENT_KIND,
    GAP_INTENSITY_MAX,
    detect_gap,
)
from elysia.soul.desire import DesireEvent, DesireState, DesireSystem


# ── 衰减曲线 ────────────────────────────────────────────
def test_effective_age_protected_slower() -> None:
    assert effective_age_days(90.0, protected=True) == pytest.approx(30.0)
    assert effective_age_days(90.0, protected=False) == pytest.approx(90.0)


def test_decay_strength_monotonic() -> None:
    fresh = decay_strength(1.0, 0.0)
    old = decay_strength(1.0, 365.0)
    assert fresh == pytest.approx(1.0)
    assert 0.0 < old < fresh  # 越老越弱，但永不为零（默认 floor=0）


def test_decay_strength_respects_floor() -> None:
    floor = 0.3
    value = decay_strength(1.0, 10000.0, floor=floor)
    assert value == pytest.approx(floor)


def test_decay_strength_protected_slower() -> None:
    normal = decay_strength(1.0, 90.0, protected=False)
    protected = decay_strength(1.0, 90.0, protected=True)
    assert protected > normal  # 珍贵记忆衰减更慢


def test_decay_strength_matches_tau() -> None:
    # 有效年龄 = τ → strength ≈ e^-1 ≈ 0.368
    value = decay_strength(1.0, STRENGTH_TAU_DAYS)
    assert value == pytest.approx(0.3679, abs=0.005)


# ── 检索耗时映射（数据不删，只是变慢） ──────────────────
def test_latency_anchor_points() -> None:
    assert retrieve_latency_ms(0.0) == pytest.approx(10.0)  # 新鲜
    assert retrieve_latency_ms(30.0) == pytest.approx(LATENCY_30D_MS, rel=0.2)
    assert retrieve_latency_ms(90.0) == pytest.approx(LATENCY_90D_MS, rel=0.2)
    assert retrieve_latency_ms(365.0) >= LATENCY_365D_MS  # ≥1s


def test_latency_monotonic_and_slow_growth() -> None:
    assert retrieve_latency_ms(10.0) < retrieve_latency_ms(60.0)
    assert retrieve_latency_ms(60.0) < retrieve_latency_ms(200.0)
    assert retrieve_latency_ms(200.0) < retrieve_latency_ms(400.0)


def test_latency_protected_much_faster() -> None:
    # 珍贵记忆有效年龄除以 3：90 天普通 = 30 天珍贵
    normal_90d = retrieve_latency_ms(90.0, protected=False)
    protected_90d = retrieve_latency_ms(90.0, protected=True)
    assert protected_90d < normal_90d


# ── 缺口信号 ────────────────────────────────────────────
def test_detect_gap_none_when_all_strong() -> None:
    idx = [(0.9, 10.0), (0.8, 20.0), (1.0, 5.0)]
    assert detect_gap(idx) is None


def test_detect_gap_detects_lost_memories() -> None:
    idx = [(0.9, 10.0), (0.1, 400.0), (0.15, 300.0)]
    gap = detect_gap(idx)
    assert gap is not None
    assert gap.lost_count == 2
    assert gap.oldest_age_days == pytest.approx(400.0)
    assert 0.0 < gap.strength <= 1.0


def test_detect_gap_empty_input() -> None:
    assert detect_gap([]) is None


def test_gap_intensity_capped() -> None:
    # 深度缺口：strength 可能超过上限，to_event_intensity 封顶
    gap = detect_gap([(0.0, 1000.0)])
    assert gap is not None
    assert gap.to_event_intensity() == pytest.approx(GAP_INTENSITY_MAX)


# ── 缺口 → 欲望系统（好奇↑非焦虑↑） ────────────────────
def test_memory_gap_event_raises_tr_not_sa() -> None:
    ds = DesireSystem(initial=DesireState(tr=50.0, cs=60.0, sa=20.0))
    before = ds.state.copy()
    ds.apply_event(DesireEvent(kind=GAP_EVENT_KIND, intensity=0.3))
    after = ds.state
    assert after.tr > before.tr  # 好奇：TR 微升
    assert after.sa == pytest.approx(before.sa)  # 非焦虑：SA 不动


def test_memory_gap_event_scale_with_intensity() -> None:
    ds1 = DesireSystem(initial=DesireState(tr=50.0, cs=60.0, sa=20.0))
    ds2 = DesireSystem(initial=DesireState(tr=50.0, cs=60.0, sa=20.0))
    ds1.apply_event(DesireEvent(kind=GAP_EVENT_KIND, intensity=0.1))
    ds2.apply_event(DesireEvent(kind=GAP_EVENT_KIND, intensity=0.4))
    assert ds1.state.tr < ds2.state.tr


# ── P0-R16：缺口信号可达性（floor 从年龄推导 + 事件式缺口）───────────────
# 取消测试：把 floor 改回 0.2 ⇒ `test_fuzzy_floor_crossed_within_a_month` 红；
# 把事件式改回"任何丢失都报" ⇒ `test_episodic_gap_does_not_repeat_old_losses` 红。


def test_fuzzy_floor_crossed_within_a_month() -> None:
    """取消测试（P0-R16）：floor 必须能在 1 个月内被跨过（旧 0.2 要 72 天）。"""
    assert FUZZY_AGE_DAYS <= 30.0
    assert decay_strength(1.0, 30.0) < STRENGTH_RETRIEVE_FLOOR
    assert decay_strength(1.0, 14.0) > STRENGTH_RETRIEVE_FLOOR


def test_episodic_gap_counts_only_newly_lost() -> None:
    from elysia.memory.hooks import detect_episodic_gap

    samples = [(1, 0.5), (2, 0.9)]  # 1 号已模糊
    gap, lost = detect_episodic_gap(samples, previously_lost=set())
    assert gap is not None
    assert gap.newly_lost == 1
    assert gap.strength == pytest.approx(0.5)  # 1 条就能越过 0.4 消费阈值
    assert lost == {1}

    # 同一个旧丢失再扫 → 不再报（事件式，不是常驻背景噪声）
    again, lost2 = detect_episodic_gap(samples, previously_lost={1})
    assert again is None
    assert lost2 == {1}


def test_episodic_gap_caps_at_one() -> None:
    from elysia.memory.hooks import detect_episodic_gap

    gap, _ = detect_episodic_gap([(1, 0.1), (2, 0.2), (3, 0.3)], previously_lost=set())
    assert gap is not None
    assert gap.strength == pytest.approx(1.0)
    assert gap.to_event_intensity() == pytest.approx(GAP_INTENSITY_MAX)
