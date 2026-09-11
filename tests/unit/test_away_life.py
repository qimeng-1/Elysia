"""离线内部生活单测：在场不产出 / 节律产出 / 模式驱动 / 念头入档。"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from elysia.core.clock import SimulatedClock
from elysia.core.mode import Mode
from elysia.core.state_store import HeartbeatStore
from elysia.soul.away_life import THINK_INTERVAL_S, AwayLife

T0 = 1_000_000.0


@pytest.mark.asyncio
async def test_present_produces_no_thoughts(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    clock = SimulatedClock(start_ts=T0, speed=100.0)
    life = AwayLife(store, clock=clock, rng=random.Random(7))
    await life.tick(mode=Mode.PRESENT, away_seconds=0.0, day_phase="day")
    assert await store.thought_count() == 0
    await store.close()


@pytest.mark.asyncio
async def test_alone_produces_thoughts_on_rhythm(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    clock = SimulatedClock(start_ts=T0, speed=100.0)
    life = AwayLife(store, clock=clock, rng=random.Random(7))
    # 独处模式：立即产出第一个念头
    await life.tick(mode=Mode.ALONE, away_seconds=600.0, day_phase="day")
    assert await store.thought_count() == 1
    # 节律内：不再产出
    clock.jump(THINK_INTERVAL_S - 1.0)
    await life.tick(mode=Mode.ALONE, away_seconds=900.0, day_phase="day")
    assert await store.thought_count() == 1
    # 节律到：产出第二个念头
    clock.jump(2.0)
    await life.tick(mode=Mode.ALONE, away_seconds=1200.0, day_phase="day")
    assert await store.thought_count() == 2
    await store.close()


@pytest.mark.asyncio
async def test_body_away_thoughts_recorded(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    clock = SimulatedClock(start_ts=T0, speed=100.0)
    life = AwayLife(store, clock=clock, rng=random.Random(3))
    await life.tick(mode=Mode.BODY_AWAY, away_seconds=25 * 3600.0, day_phase="night")
    rows = await store.execute_raw("SELECT kind, text FROM thought_log ORDER BY id ASC")
    assert len(rows) == 1
    kind, text = rows[0]
    assert kind in {"recall", "summary", "wander", "review", "future", "idle"}
    assert isinstance(text, str) and len(text) > 0
    await store.close()


@pytest.mark.asyncio
async def test_return_to_present_stops_thoughts(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    clock = SimulatedClock(start_ts=T0, speed=100.0)
    life = AwayLife(store, clock=clock, rng=random.Random(1))
    await life.tick(mode=Mode.ALONE, away_seconds=600.0, day_phase="day")
    clock.jump(THINK_INTERVAL_S)
    # 身体回归：在场模式不产出
    await life.tick(mode=Mode.PRESENT, away_seconds=0.0, day_phase="day")
    assert await store.thought_count() == 1
    await store.close()


@pytest.mark.asyncio
async def test_dual_mode_thought_style_controls_rhythm(tmp_path: Path) -> None:
    """P1：thought_style > 0 → 胡思乱想节奏（3min），≤ 0 → 虚无发呆节奏（10min）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    clock = SimulatedClock(start_ts=T0, speed=100.0)
    life = AwayLife(store, clock=clock, rng=random.Random(7))

    # thought_style > 0：胡思乱想，每 3min
    await life.tick(mode=Mode.ALONE, away_seconds=600.0, day_phase="day", thought_style=0.5)
    assert await store.thought_count() == 1, "胡思乱想模式应立即产出"

    # 3min 内不产出
    clock.jump(179.0)
    await life.tick(mode=Mode.ALONE, away_seconds=900.0, day_phase="day", thought_style=0.5)
    assert await store.thought_count() == 1, "3min 节律内不产出"

    # 超过 3min 产出第二个
    clock.jump(5.0)
    await life.tick(mode=Mode.ALONE, away_seconds=1200.0, day_phase="day", thought_style=0.5)
    assert await store.thought_count() == 2, "3min 到应产出"

    # 切到 thought_style ≤ 0：虚无发呆，每 10min
    # 跳过 600s 到达虚无发呆节律
    clock.jump(600.0)
    await life.tick(mode=Mode.ALONE, away_seconds=2100.0, day_phase="day", thought_style=-0.5)
    assert await store.thought_count() == 3, "虚无发呆模式 10min 到应产出"

    # 10min 内不产出
    clock.jump(599.0)
    await life.tick(mode=Mode.ALONE, away_seconds=2400.0, day_phase="day", thought_style=-0.5)
    assert await store.thought_count() == 3, "10min 节律内不产出"

    await store.close()


@pytest.mark.asyncio
async def test_brain_action_force_thought_style(tmp_path: Path) -> None:
    """P1：brain_action 强制覆盖 thought_style 频率。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    clock = SimulatedClock(start_ts=T0, speed=100.0)
    life = AwayLife(store, clock=clock, rng=random.Random(7))

    # brain_action="think_active" 强制胡思乱想频率
    await life.tick(
        mode=Mode.ALONE, away_seconds=600.0, day_phase="day", brain_action="think_active"
    )
    assert await store.thought_count() == 1

    # 3min 内不产出
    clock.jump(179.0)
    await life.tick(
        mode=Mode.ALONE, away_seconds=900.0, day_phase="day", brain_action="think_active"
    )
    assert await store.thought_count() == 1

    # 超过 3min 产出
    clock.jump(5.0)
    await life.tick(
        mode=Mode.ALONE, away_seconds=1200.0, day_phase="day", brain_action="think_active"
    )
    assert await store.thought_count() == 2

    await store.close()
