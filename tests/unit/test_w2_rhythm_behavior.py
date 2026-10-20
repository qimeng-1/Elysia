"""W2/P4 行为级用例：意愿→**响应延迟**仍连续；但时间不再是开口触发器。

P4（2026-10-20）：`should_speak` 里"距上次开口 ≥ interval ⇒ 开口"的路径整条删除。
`rhythm_interval_s` 保留为"事件发生后多久回应"的响应延迟——它仍随状态连续变化
（旧实现只有 180/600 两档），但**不再决定何时轮到说**。

取消测试：
- 把"经过时间"触发加回去 ⇒ `test_internal_impulse_never_fires_by_elapsed_time` 红；
- 把响应延迟压回两个常量 ⇒ `test_response_delay_is_continuous_not_two_constants` 红。
"""

from __future__ import annotations

from itertools import pairwise

from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService

MAX_INTERVAL_S = 1800.0  # 响应延迟上限（与实现同值；本文件刻意不 import 新常量）


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
    """只测节奏：LLM/存储都不参与，传 None 即可（与 test_panel_channel 同手法）。"""
    return ExpressionService(None, None)  # type: ignore[arg-type]


def test_response_delay_is_continuous_not_two_constants() -> None:
    """取消测试（W2 核心）：响应延迟仍随状态连续变化，不是 180/600 两档。"""
    styles = [-0.8, -0.4, 0.0, 0.4, 0.8]
    measured = [_service().rhythm_interval_s(_output(style=s), now=0.0) for s in styles]
    distinct = len({round(v) for v in measured})
    assert distinct >= 4, f"只观测到 {distinct} 种响应延迟 {measured} ⇒ 又成固定节拍器"
    assert all(b < a for a, b in pairwise(measured)), f"延迟没有随活跃度变短：{measured}"
    assert measured[0] > 500, "安静态应该明显更慢回应"
    assert measured[-1] < 300, "活跃态应该更快回应"
    assert all(60.0 <= v <= MAX_INTERVAL_S for v in measured)


def test_internal_impulse_never_fires_by_elapsed_time() -> None:
    """P4 核心：没有事件/跨越，无论过多久都不开口；force/greeting 仍立即。"""
    service = _service()
    output = _output(style=-1.0, rest=0.5)  # 最安静的状态
    assert service.should_speak(output, 1.0, True), "force 必须立即开口"
    assert service.should_speak(output, 2.0, False, greeting=True), "greeting 必须立即开口"
    # 关键：隔一天再问，没有事件 ⇒ 不开口（旧实现这里会 True）
    assert not service.should_speak(output, 1.0 + 86400.0, False), (
        "内部冲动不得由经过时间触发（P4 已删除该路径）"
    )


def test_reluctance_lengthens_response_delay_but_never_breaks_bounds() -> None:
    """退缩 → 响应更慢（关系可测量），但不顶穿 1800s；仍是同一条连续映射。"""
    close = _service().rhythm_interval_s(_output(style=0.3), now=0.0, reluctance=1.0)
    withdrawn = _service().rhythm_interval_s(_output(style=0.3), now=0.0, reluctance=2.0)
    assert withdrawn > close, "关系退缩必须让她回应更慢"
    assert withdrawn <= MAX_INTERVAL_S, "退缩不得把响应延迟顶穿 1800s 上限"
