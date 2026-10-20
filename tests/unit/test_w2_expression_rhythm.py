"""W2 说话节奏：由意愿**连续**驱动（60–1800s）+ 平滑 + force/greeting 不变。

旧实现是"常量 × 倍数"的节拍器：`think_* 且 thought_style>0 → 180s，否则 600s`，
再 × 退缩倍数。她的真实开口间隔因此长期卡在 181–194s 的窄带（真机中位 194s）。

取消测试（还原旧 `should_speak` ⇒ 本文件必须红）：
- 纯函数/接线用例：旧文件没有 `rhythm_interval_s` / `expression_interval_s` ⇒ 红
  （ImportError，见取消测试留档）；
- 行为级红态（`should_speak` 只测得出 180/600）在配套文件
  `test_w2_rhythm_behavior.py`——它只 import 既有符号，还原旧实现时是**断言失败**，
  而不是 import 报错，红态证据更可读。
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import pytest

from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import (
    EXPRESS_INTERVAL_MAX_S,
    EXPRESS_INTERVAL_MIN_S,
    ExpressionService,
    expression_interval_s,
    expression_willingness,
)

BANDS: tuple[tuple[float, float], ...] = (
    (60.0, 120.0),
    (120.0, 180.0),
    (180.0, 300.0),
    (300.0, 600.0),
    (600.0, 1200.0),
    (1200.0, 1800.0),
)


def _output(
    *,
    chat: float = 0.642,
    miss: float = 0.358,
    explore: float = 0.477,
    rest: float = 0.05,
    style: float = 0.08,
    action: str = "none",
) -> BrainOutput:
    """她真实的中位状态（2026-10-19 真机 40000 拍）作为默认值。"""
    return BrainOutput(
        desire=DesireState(tr=48.0, cs=64.0, sa=20.0),
        feelings=FeelingState(
            chat=chat,
            miss=miss,
            explore=explore,
            curiosity=0.0,
            rest=rest,
            self_check=0.2,
        ),
        will=WillOutput(direction="reach", strength=0.55, thought_style=style, anim_bias=0.0),
        action=action,
    )


def _service() -> ExpressionService:
    """只测节流：LLM/存储都不参与，传 None 即可（与 test_panel_channel 同手法）。"""
    return ExpressionService(None, None)  # type: ignore[arg-type]


def _interval(**over: Any) -> float:
    kwargs: dict[str, Any] = {
        "chat": 0.642,
        "miss": 0.358,
        "explore": 0.477,
        "rest": 0.05,
        "thought_style": 0.08,
        "reluctance": 1.0,
    }
    kwargs.update(over)
    return expression_interval_s(**kwargs)


# ── 纯映射：范围 / 常态 / 单调 ────────────────────────────────────
def test_pure_mapping_covers_full_range() -> None:
    eager = _interval(chat=1.0, miss=1.0, explore=1.0, rest=0.0, thought_style=1.0)
    lazy = _interval(chat=0.0, miss=0.0, explore=0.0, rest=0.5, thought_style=-1.0)
    assert eager == pytest.approx(EXPRESS_INTERVAL_MIN_S), "意愿拉满必须能到 60s"
    assert lazy == pytest.approx(EXPRESS_INTERVAL_MAX_S), "意愿到底必须能到 1800s"
    # 常态（她的设定点经 FeelingMapper 映射：chat .60 / miss .40 / explore .45 / style 0）
    neutral = _interval(chat=0.60, miss=0.40, explore=0.45, rest=0.05, thought_style=0.0)
    assert neutral == pytest.approx(60.0 * 30.0**0.5, rel=0.01), "常态应落在几何中点 ≈329s"


def test_interval_is_monotone_in_willingness() -> None:
    """W2 验收：意愿越高 ⇒ 间隔越短（严格单调，不是"看起来像"）。"""
    styles = [-1.0, -0.7, -0.4, -0.1, 0.2, 0.5, 0.8, 1.0]
    ws = [
        expression_willingness(
            chat=0.642,
            miss=0.358,
            explore=0.477,
            rest=0.05,
            thought_style=s,
        )
        for s in styles
    ]
    ivs = [_interval(thought_style=s) for s in styles]
    assert all(b > a for a, b in pairwise(ws)), f"意愿不随 thought_style 上升：{ws}"
    assert all(b < a for a, b in pairwise(ivs)), f"间隔没有随意愿严格变短：{ivs}"

    # 网格级：按意愿排序后，间隔必须单调不增（同意愿同间隔）
    grid = [
        {
            "chat": c,
            "miss": 1.0 - c,
            "explore": e,
            "rest": r,
            "thought_style": s,
        }
        for c in (0.55, 0.60, 0.642, 0.68, 0.72)
        for e in (0.37, 0.477, 0.57)
        for r in (0.05, 0.5)
        for s in (-1.0, -0.5, 0.0, 0.5, 1.0)
    ]
    pairs = sorted((expression_willingness(**kw), expression_interval_s(**kw)) for kw in grid)
    for (w1, i1), (w2, i2) in pairwise(pairs):
        assert i1 >= i2 - 1e-9, f"意愿 {w1:.3f}→{w2:.3f} 却间隔 {i1:.1f}→{i2:.1f}"


def test_each_state_dimension_moves_interval_in_expected_direction() -> None:
    # 社会驱动是 V 形：想聊（chat 高于 0.65）与思念（miss 高于 0.40）都让间隔变短
    assert _interval(chat=0.72, miss=0.28) < _interval(chat=0.64, miss=0.36)
    assert _interval(chat=0.58, miss=0.42) < _interval(chat=0.64, miss=0.36)
    # 想聊线以下（chat<0.65）不额外驱动：间隔不变
    assert _interval(chat=0.55, miss=0.36) == pytest.approx(_interval(chat=0.62, miss=0.36))
    # 探索 / 活跃度同向；歇着 / 退缩反向
    assert _interval(explore=0.60) < _interval(explore=0.40)
    assert _interval(thought_style=0.6) < _interval(thought_style=-0.2)
    assert _interval(rest=0.5) > _interval(rest=0.05)
    assert _interval(reluctance=2.0) > _interval(reluctance=1.0)


def test_interval_always_stays_within_bounds() -> None:
    for chat in (0.0, 0.3, 0.55, 0.642, 0.75, 1.0):
        for style in (-1.0, -0.4, 0.0, 0.4, 1.0):
            for rest in (0.05, 0.5):
                for reluctance in (1.0, 1.5, 2.0):
                    value = _interval(
                        chat=chat,
                        miss=1.0 - chat,
                        rest=rest,
                        thought_style=style,
                        reluctance=reluctance,
                    )
                    assert EXPRESS_INTERVAL_MIN_S - 1e-9 <= value <= EXPRESS_INTERVAL_MAX_S + 1e-9


def test_mapping_spans_multiple_bands_on_plausible_states() -> None:
    """她真实状态范围内必须横跨多个档，而不是又一个窄带（T1：造好了要插电）。"""
    values = [
        _interval(
            chat=c,
            miss=1.0 - c,
            explore=e,
            thought_style=s,
            rest=r,
            reluctance=rel,
        )
        for c in (0.55, 0.60, 0.642, 0.68, 0.72)
        for e in (0.37, 0.477, 0.57)
        for s in (-1.0, -0.6, -0.2, 0.0, 0.2, 0.5, 0.8, 1.0)
        for r in (0.05, 0.5)
        for rel in (1.0, 2.0)
    ]
    hit = {band for band in BANDS if any(band[0] <= v < band[1] for v in values)}
    assert len(hit) >= 4, f"只覆盖 {len(hit)} 个间隔档：{sorted(hit)}"
    ordered = sorted(values)
    p10, p90 = ordered[int(0.1 * (len(ordered) - 1))], ordered[int(0.9 * (len(ordered) - 1))]
    assert p90 / p10 > 3.0, f"p90/p10 只有 {p90 / p10:.2f} 倍 ⇒ 仍是窄带"


# ── P4：运行时不再用"经过时间"触发（interval 只作事件后的响应延迟）────
def test_should_speak_no_longer_triggers_on_elapsed_time() -> None:
    """取消测试（P4）：把"经过时间 ⇒ 开口"加回去 ⇒ 本用例红。"""
    service = _service()
    output = _output(style=0.5)
    interval = service.rhythm_interval_s(output, now=0.0)
    assert EXPRESS_INTERVAL_MIN_S <= interval <= EXPRESS_INTERVAL_MAX_S

    assert service.should_speak(output, 0.0, True), "force 立即开口"
    # 再过 10 天：没有事件也不该开口（时间不再是触发器）
    assert not service.should_speak(output, 10 * 86400.0, False), "经过时间不得触发开口"
    assert service.should_speak(output, 10 * 86400.0 + 1.0, False, greeting=True), "问候仍立即"


# 行为级取消测试（只看 should_speak，不 import 任何 W2 新符号）在配套文件
# `test_w2_rhythm_behavior.py`：旧实现下那边是断言失败，这边是 import 失败。


# ── 防抖：EMA 平滑（压抖、无方向偏置、不压掉持续状态）─────────────
def test_smoothing_damps_jitter_without_flapping() -> None:
    """状态在快/慢两端逐拍抖动时，间隔不得跟着在 60↔1800 间狂跳。"""
    service = _service()
    eager, lazy = _output(style=1.0), _output(style=-1.0)
    values: list[float] = []
    for beat in range(600):
        values.append(service.rhythm_interval_s(eager if beat % 2 == 0 else lazy, float(beat)))
    steps = [abs(b - a) for a, b in pairwise(values)]
    assert max(steps) < 100.0, f"单拍最大跳变 {max(steps):.1f}s ⇒ 平滑没有起作用"
    assert max(values) - min(values) < 400.0, "抖动被放大成了大范围摆动"
    # ^ 抖动应被压到中间带附近，而不是在两端之间来回

    # 状态只是小幅抖动（±0.02 的 style）时，间隔几乎不动
    near = _output(style=0.2)
    quiet: list[float] = []
    for beat in range(300):
        jitter = 0.02 if beat % 2 == 0 else -0.02
        quiet.append(service.rhythm_interval_s(_output(style=0.2 + jitter), float(1000 + beat)))
    assert max(quiet) / min(quiet) < 1.05, "小幅状态抖动被放大成大间隔摆动"
    assert near is not None


def test_smoothing_preserves_sustained_state() -> None:
    """平滑不能压掉持续状态：盯住一个状态足够久，间隔要回到该状态的映射值。"""
    eager = _output(style=1.0)
    lazy = _output(style=-1.0)
    for output in (eager, lazy):
        service = _service()
        target = expression_interval_s(
            chat=output.feelings.chat,
            miss=output.feelings.miss,
            explore=output.feelings.explore,
            rest=output.feelings.rest,
            thought_style=output.will.thought_style,
        )
        latest = 0.0
        for beat in range(400):
            latest = service.rhythm_interval_s(output, float(beat))
        assert latest == pytest.approx(target, rel=0.02), "平滑把持续状态也压掉了（有偏）"
