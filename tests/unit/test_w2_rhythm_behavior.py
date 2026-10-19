"""W2 节流行为级用例（**只 import 既有符号**，让取消测试的红态是断言失败）。

配套 `test_w2_expression_rhythm.py`（纯映射/接线）在还原旧实现时是 ImportError；
本文件只调用 `ExpressionService.should_speak`——旧实现下它照样能跑，于是
`test_observed_thresholds_are_not_two_constants` 会以**可见的断言失败**变红：

    旧：只有 180/600 两档 ⇒ 观测到的间隔种类 = 1–2
    新：随状态连续变化 ⇒ 观测到 ≥4 种，且安静态明显更久、活跃态更快

取消测试（还原旧 `should_speak` ⇒ 本文件必须红）：`test_observed_thresholds_are_not_two_constants`。
`force` / `greeting` / 退缩三条是**回归护栏**（新旧都应绿）。
"""

from __future__ import annotations

from itertools import pairwise

from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService

MAX_INTERVAL_S = 1800.0  # W2 上限（与实现同值；本文件刻意不 import 新常量）


def _output(*, style: float = 0.08, rest: float = 0.05, action: str = "none") -> BrainOutput:
    """她真实的中位状态（2026-10-19 真机 40000 拍）为默认值。"""
    return BrainOutput(
        desire=DesireState(tr=48.0, cs=64.0, sa=20.0),
        feelings=FeelingState(
            chat=0.642,
            miss=0.358,
            explore=0.477,
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


def _measured_interval(output: BrainOutput, *, reluctance: float = 1.0) -> float:
    """行为测量：先 force 一次定基线，再逐秒问"现在该开口了吗"，第一个 True 即间隔。"""
    service = _service()
    assert service.should_speak(output, 0.0, True, reluctance=reluctance), "force 必须立即开口"
    for offset in range(0, int(MAX_INTERVAL_S) + 120):
        if service.should_speak(output, float(offset), False, reluctance=reluctance):
            return float(offset)
    raise AssertionError(f"超过 {MAX_INTERVAL_S:.0f}s+120 仍不开口 ⇒ 间隔映射越界")


def test_observed_thresholds_are_not_two_constants() -> None:
    """取消测试（W2 核心）：旧 should_speak 只能测出 180/600 两档 ⇒ 本用例红。"""
    styles = [-0.8, -0.4, 0.0, 0.4, 0.8]
    measured = [_measured_interval(_output(style=s)) for s in styles]
    distinct = len({round(v) for v in measured})
    assert distinct >= 4, f"只观测到 {distinct} 种间隔 {measured} ⇒ 仍是固定节拍器"
    assert all(b < a for a, b in pairwise(measured)), f"间隔没有随活跃度变短：{measured}"
    assert measured[0] > 500, "安静态应该明显更久才开口"
    assert measured[-1] < 300, "活跃态应该更快开口"


def test_force_and_greeting_bypass_any_interval() -> None:
    """回归护栏：force（你对她说）与 greeting（她刚回来）永远立即开口（W2 前后不变）。"""
    service = _service()
    output = _output(style=-1.0, rest=0.5)  # 最安静的状态
    assert service.should_speak(output, 1.0, True), "force 必须立即开口"
    assert service.should_speak(output, 2.0, False, greeting=True), "greeting 必须越过节流"
    assert not service.should_speak(output, 3.0, False), "内部冲动仍然要被节流"


def test_reluctance_lengthens_but_never_breaks_bounds() -> None:
    """回归护栏：退缩 → 间隔变长（关系可测量的表达效果），且不顶穿 1800s。"""
    close = _measured_interval(_output(style=0.3), reluctance=1.0)
    withdrawn = _measured_interval(_output(style=0.3), reluctance=2.0)
    assert withdrawn > close, "关系退缩必须让她说得更少"
    assert withdrawn <= MAX_INTERVAL_S, "退缩不得把间隔顶穿 1800s 上限"
