"""TimeSense 单测：无 0 时刻 / 时钟延迟模拟 / 昼夜相位 / 年龄 / 字段单一事实源。"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from elysia.core.clock import SimulatedClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import (
    STATE_FIELDS,
    TimeSense,
    TimeSenseState,
    from_payload,
    to_payload,
)
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state

T0 = 1_000_000.0  # 固定虚拟起点（模拟时钟用，与真实时间无关）

UTC = UTC


def make_state(**overrides: object) -> TimeSenseState:
    base: dict[str, object] = {
        "first_existence_ts": T0,
        "last_soul_beat_ts": T0,
        "last_body_online_ts": T0,
        "last_interaction_ts": T0,
    }
    base.update(overrides)
    return TimeSenseState(**base)  # type: ignore[arg-type]


def test_no_zero_epoch_in_derived_values() -> None:
    """新生状态 + 时间流逝：所有派生值 >= 0，不出现 0 时刻异常。"""
    clock = SimulatedClock(start_ts=T0)
    ts = TimeSense(make_state(body_away_start_ts=T0), clock)
    clock.jump(86400.0)
    summary = ts.summary()
    assert summary["body_away_s"] >= 0
    assert summary["presence_s"] >= 0
    assert summary["since_interaction_s"] >= 0
    assert summary["age_days"] > 0


def test_clock_delay_simulation_away_2h() -> None:
    """验收门：身体离线 2h → 正确感知"离开 2h"（时钟延迟模拟）。"""
    clock = SimulatedClock(start_ts=T0)
    ts = TimeSense(make_state(body_away_start_ts=T0), clock)
    clock.jump(7200.0)
    assert ts.body_away_seconds() == pytest.approx(7200.0, abs=0.01)
    assert ts.away_grade() == "away"


def test_presence_accumulates_when_body_online() -> None:
    clock = SimulatedClock(start_ts=T0)
    ts = TimeSense(make_state(body_away_start_ts=None), clock)
    clock.jump(3600.0)
    assert ts.presence_seconds() == pytest.approx(3600.0, abs=0.01)
    assert ts.body_away_seconds() == 0.0
    assert ts.away_grade() == "present"


def test_since_interaction_seconds() -> None:
    clock = SimulatedClock(start_ts=T0)
    ts = TimeSense(make_state(last_interaction_ts=T0 - 1800.0), clock)
    clock.jump(600.0)
    assert ts.since_interaction_seconds() == pytest.approx(2400.0, abs=0.01)


def test_day_phase_fixed_utc() -> None:
    """固定 UTC 时区验证四相位（生产默认本地时区，测试不受机器时区影响）。"""
    base = datetime(2026, 8, 22, tzinfo=UTC).timestamp()

    def phase_at(hour: int) -> str:
        clock = SimulatedClock(start_ts=base + hour * 3600.0)
        return TimeSense(make_state(), clock).day_phase(UTC)

    assert phase_at(3) == "night"
    assert phase_at(7) == "dawn"
    assert phase_at(12) == "day"
    assert phase_at(19) == "dusk"
    assert phase_at(23) == "night"


def test_age_days_accumulates() -> None:
    clock = SimulatedClock(start_ts=T0)
    ts = TimeSense(make_state(), clock)
    clock.jump(2 * 86400.0 + 3600.0)
    assert ts.age_days() == pytest.approx(2.0417, abs=0.01)


def test_away_grades() -> None:
    clock = SimulatedClock(start_ts=T0)
    state = make_state(body_away_start_ts=T0)
    # leaving: 离线 4min
    ts = TimeSense(state, clock)
    clock.jump(240.0)
    assert ts.away_grade() == "leaving"
    # away: 离线 6min
    clock.jump(120.0)
    assert ts.away_grade() == "away"
    # long_away: 离线 25h
    clock.jump(25 * 3600.0 - 360.0)
    assert ts.away_grade() == "long_away"


def test_fields_single_source() -> None:
    """D4：持久化键 == dataclass 字段名（单一事实源防漂移）。"""
    assert set(STATE_FIELDS) == {f.name for f in dataclasses.fields(TimeSenseState)}
    assert set(to_payload(make_state()).keys()) == set(STATE_FIELDS)


def test_from_payload_drops_unknown_keys() -> None:
    state = from_payload({**to_payload(make_state()), "evil_key": 1})
    assert not hasattr(state, "evil_key")


def test_from_payload_missing_fields_use_defaults() -> None:
    state = from_payload({})
    assert state.first_existence_ts == 0.0  # 无默认字段以 0.0 兜底
    assert state.body_away_start_ts is None  # 有默认字段取默认值


# ── P0-R5（D-4）：last_soul_beat_ts 必须每拍前进 ──────────────────────
# 审计实测：该字段声称"最近一次灵魂心跳"，但旧写法**只在启动时写一次**
# （state.db 停在 2026-10-14 01:18:35，落后 3.86 天）⇒ "声明了却不维护"。
# 用户裁决：修（不是删），且与 D-3 对齐——看门狗读 heartbeat.db 的 ts，
# 这里写 state.db 的同一事实，二者同拍推进、不得长期背离。


async def _wait_for_soul_beats(store: HeartbeatStore, wanted: int, timeout_s: float = 5.0) -> int:
    """按事实轮询等心跳落库到 N 拍（不按固定虚拟秒数猜）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    count = 0
    while loop.time() < deadline:
        rows = await store.execute_raw("SELECT COUNT(*) FROM heartbeats WHERE beat_type = 'soul'")
        count = int(rows[0][0])
        if count >= wanted:
            return count
        await asyncio.sleep(0.01)
    raise AssertionError(f"{timeout_s}s 内只等到 {count} 拍心跳（期望 >= {wanted}）")


@pytest.mark.asyncio
async def test_last_soul_beat_ts_advances_every_beat(tmp_path: Path) -> None:
    """取消测试（P0-R5）：跑 N 拍后 state.db 的 last_soul_beat_ts 必须前进。

    还原旧写法（每拍不更新）时本用例必红：persisted 会恒等于构造时刻 T0。
    """
    clock = SimulatedClock(start_ts=T0, speed=60.0)
    state_store = StateStore(tmp_path / "state.db")
    heartbeat_store = HeartbeatStore(tmp_path / "heartbeat.db")
    await state_store.start()
    await heartbeat_store.start()
    soul = SoulHeartbeat(
        state_store=state_store,
        heartbeat_store=heartbeat_store,
        timesense=TimeSense(make_soul_state(T0), clock),
        mode_mgr=ModeManager(clock=clock),
        clock=clock,
        interval_s=1.0,
    )
    task = asyncio.create_task(soul.run())
    try:
        await _wait_for_soul_beats(heartbeat_store, 3)
        persisted = await state_store.load_json("timesense")
        rows = await heartbeat_store.execute_raw(
            "SELECT MAX(ts) FROM heartbeats WHERE beat_type = 'soul'"
        )
    finally:
        await soul.stop()
        await task
        await state_store.close()
        await heartbeat_store.close()

    assert isinstance(persisted, dict)
    beat_ts = float(persisted["last_soul_beat_ts"])
    assert beat_ts > T0, f"旧写法：字段停在构造时刻 {T0}（实测 {beat_ts}）"
    # 与 D-3 对齐：state.db 的该字段必须与 heartbeat.db 最后一条 soul 心跳同拍（不背离）
    hb_ts = float(rows[0][0])
    assert abs(beat_ts - hb_ts) <= 2.0, (
        f"state.db 的 last_soul_beat_ts({beat_ts}) 与 heartbeat.db 最后心跳({hb_ts}) 背离"
    )
