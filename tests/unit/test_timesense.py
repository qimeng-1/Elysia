"""TimeSense 单测：无 0 时刻 / 时钟延迟模拟 / 昼夜相位 / 年龄 / 字段单一事实源。"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from elysia.core.clock import SimulatedClock
from elysia.core.log import get_logger
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
from elysia.soul.main import _build_timesense

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


# ── P0-R6（D-7）：停机时长建模——"我错过了多久" ─────────────────────
# 审计实测：她 2026-10-17 03:51:54 后停了 **16.82 小时**，醒来后 age_days 照涨
# 0.7 天——她不知道自己那段不存在。下面用例钉住三件事：
#   ① 启动时按"上次心跳 → 现在"的差计入停机（>30s 才算，不把重启抖动当停机）；
#   ② 跨重启**累加**（只增不减，属成长性）；
#   ③ 老档案缺字段不得崩，也不虚构历史。
# 取消测试：`test_downtime_counted_on_startup`（去掉启动核算后 last_downtime_s 恒为 0 ⇒ 红）。


def _timesense_payload(now: float, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "first_existence_ts": now - 3 * 86400.0,
        "last_soul_beat_ts": now - 7200.0,
        "last_body_online_ts": 0.0,
        "last_interaction_ts": now,
        "body_away_start_ts": None,
        "last_seen_ts": now - 7200.0,
        "downtime_total_s": 100.0,
        "last_downtime_s": 0.0,
    }
    payload.update(overrides)
    return payload


async def _build_from_payload(tmp_path: Path, payload: dict[str, object]) -> TimeSense:
    """把一份 timesense 档案写进真 StateStore，再走**真正的** `_build_timesense`。"""
    store = StateStore(tmp_path / "state.db")
    await store.start()
    try:
        await store.save_json("timesense", payload)
        return await _build_timesense(store, get_logger("test"))
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_downtime_counted_on_startup(tmp_path: Path) -> None:
    """取消测试（P0-R6）：上次心跳在 2 小时前 ⇒ 停机 ~7200s 计入累计。"""
    now = time.time()
    ts = await _build_from_payload(tmp_path, _timesense_payload(now))

    assert ts.state.last_downtime_s == pytest.approx(7200.0, abs=5.0), (
        f"上次停机应约 7200s（旧写法：没有这个概念，恒为 0），实际 {ts.state.last_downtime_s}"
    )
    assert ts.state.downtime_total_s == pytest.approx(7300.0, abs=5.0)  # 100 + 7200
    # 本次醒来即"最近一次在场"，否则下一次启动会把这次运行也算成停机
    assert ts.state.last_seen_ts >= now - 2.0


@pytest.mark.asyncio
async def test_downtime_ignores_short_gap(tmp_path: Path) -> None:
    """<30s 的间隔只是重启抖动：不得凭空给她添一笔"你不在场"。"""
    now = time.time()
    ts = await _build_from_payload(
        tmp_path, _timesense_payload(now, last_seen_ts=now - 5.0, downtime_total_s=100.0)
    )
    assert ts.state.last_downtime_s == 0.0
    assert ts.state.downtime_total_s == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_downtime_accumulates_across_restarts(tmp_path: Path) -> None:
    """停机时长**跨重启累加、只增不减**（"成长性"的物理形态）。"""
    now = time.time()
    first = await _build_from_payload(tmp_path, _timesense_payload(now))

    second_payload = to_payload(first.state)
    second_payload["last_seen_ts"] = time.time() - 3600.0  # 又停机一小时
    second = await _build_from_payload(tmp_path, second_payload)

    assert second.state.last_downtime_s == pytest.approx(3600.0, abs=5.0)
    assert second.state.downtime_total_s == pytest.approx(
        first.state.downtime_total_s + 3600.0, abs=10.0
    ), "累计停机必须单调累加"


@pytest.mark.asyncio
async def test_downtime_backward_compatible_with_old_payload(tmp_path: Path) -> None:
    """老档案缺新字段：走默认值、不得崩，也**不虚构**过去的停机。"""
    now = time.time()
    old_payload = {
        "first_existence_ts": now - 3 * 86400.0,
        "last_soul_beat_ts": now - 7200.0,
        "last_body_online_ts": 0.0,
        "last_interaction_ts": now,
        "body_away_start_ts": None,
    }
    ts = await _build_from_payload(tmp_path, old_payload)
    assert ts.state.last_downtime_s == 0.0
    assert ts.state.downtime_total_s == 0.0
    assert ts.state.last_seen_ts > 0


def test_downtime_exposed_in_summary() -> None:
    """暴露到 summary（→ 心跳 payload → 面板/后续阶段都能读到）。"""
    state = make_state(downtime_total_s=7200.0, last_downtime_s=3600.0)
    summary = TimeSense(state, SimulatedClock(start_ts=T0)).summary()
    assert summary["downtime_total_s"] == 7200
    assert summary["last_downtime_s"] == 3600


@pytest.mark.asyncio
async def test_last_seen_ts_advances_every_beat(tmp_path: Path) -> None:
    """`last_seen_ts` 必须每拍推进——否则下次启动会把整段运行算成停机。"""
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
        await _wait_for_soul_beats(heartbeat_store, 2)
        persisted = await state_store.load_json("timesense")
    finally:
        await soul.stop()
        await task
        await state_store.close()
        await heartbeat_store.close()

    assert isinstance(persisted, dict)
    assert float(persisted["last_seen_ts"]) > T0
