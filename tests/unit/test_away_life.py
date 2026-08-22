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
