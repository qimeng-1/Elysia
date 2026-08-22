"""ModeManager 单测：三态判定与离线时长。"""

from __future__ import annotations

from elysia.core.clock import SimulatedClock
from elysia.core.mode import Mode, ModeManager

T0 = 1_000_000.0


def make_manager() -> tuple[ModeManager, SimulatedClock]:
    clock = SimulatedClock(start_ts=T0)
    return ModeManager(clock=clock), clock


def test_never_present_is_present() -> None:
    """从未有身体心跳（<=0）：视为在场等待首报，不产生离开恐慌。"""
    manager, _ = make_manager()
    assert manager.mode(0.0) is Mode.PRESENT
    assert manager.mode(-1.0) is Mode.PRESENT


def test_present_when_fresh() -> None:
    manager, clock = make_manager()
    clock.jump(60.0)
    assert manager.mode(T0) is Mode.PRESENT


def test_alone_after_5min() -> None:
    manager, clock = make_manager()
    clock.jump(300.0 + 1.0)
    assert manager.mode(T0) is Mode.ALONE


def test_body_away_after_24h() -> None:
    manager, clock = make_manager()
    clock.jump(86400.0 + 1.0)
    assert manager.mode(T0) is Mode.BODY_AWAY


def test_away_seconds() -> None:
    manager, clock = make_manager()
    clock.jump(7200.0)
    assert manager.away_seconds(T0) == 7200.0
