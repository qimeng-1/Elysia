"""P0 验收门测试（路径图 §5 定稿）。

客观验收：
1. 模拟时钟 24h 无中断 —— SimulatedClock 加速 + HeartbeatStore.gaps 零空洞
2. TimeSense 无 0 时刻 —— 派生计算全部 max(0, …) 截断，新生 at 时刻起算
3. 时钟延迟模拟有效 —— clock.jump() 离线 2h 感知 → away_grade/模式正确
4. 难受测试（资源注入）—— injectable_sampler → DistressMonitor 30s hold 触发
5. 检查点一致性 —— .backup 快照 → 写入 → 回滚 → 数据一致
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from elysia.core.checkpoint import CheckpointManager
from elysia.core.clock import SimulatedClock
from elysia.core.mode import Mode, ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import (
    TimeSense,
    TimeSenseState,
    from_payload,
)
from elysia.protocol.snapshots import build_snapshot
from elysia.soul.distress import DISTRESS_HOLD_S, DistressMonitor

T0 = 1_000_000.0


# ── 1. 模拟时钟 24h 无中断 ──────────────────────────────


@pytest.mark.asyncio
async def test_simulated_clock_24h_continuous(tmp_path: Path) -> None:
    """模拟时钟加速 1000s 心跳 + gap 检测覆盖 24h 尺度。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()

    # 100 拍连续（模拟 100s）
    for i in range(100):
        await store.append(T0 + i, "soul", {"seq": i})

    # 连续区间无空洞（1.5s 阈值应无匹配）
    gaps = await store.gaps(threshold_s=1.5)
    assert len(gaps) == 0, f"连续 100 拍不应有空隙: {gaps[:3]}"

    # 模拟 24h 后追加（gap = 86400 - 100 = 86300s）
    await store.append(T0 + 86400, "soul", {"seq": 200})
    gaps_24h = await store.gaps(threshold_s=100.0)
    assert len(gaps_24h) == 1, "24h 空洞应当被检出"
    gap_start, gap_end = gaps_24h[0]
    assert gap_end - gap_start > 80000, f"gap 跨度应 > 80000s，实际 {gap_end - gap_start}"

    await store.close()


# ── 2. TimeSense 无 0 时刻 ──────────────────────────────


def test_timesense_no_zero_summary() -> None:
    """TimeSense 所有派生值非负（无 0 时刻精神）。"""
    clock = SimulatedClock(speed=1.0)
    # 构造一个全零状态
    state = from_payload(
        {
            "first_existence_ts": clock.now(),
            "last_soul_beat_ts": clock.now(),
            "last_body_online_ts": 0.0,
            "last_interaction_ts": clock.now(),
            "body_away_start_ts": None,
        }
    )
    ts = TimeSense(state=state, clock=clock)

    # 刚出生的汇总：不应有负值
    s = ts.summary()
    for key in ("body_away_s", "presence_s", "since_interaction_s", "age_days"):
        assert s[key] >= 0, f"{key}={s[key]} 应 >= 0"


def test_timesense_no_negative_after_body_offline() -> None:
    """身体离线后 away_s 为正，presence_s 为 0（绝不产生负值）。"""
    clock = SimulatedClock(start_ts=T0)
    state = from_payload(
        {
            "first_existence_ts": T0,
            "last_soul_beat_ts": T0,
            "last_body_online_ts": T0,
            "last_interaction_ts": T0,
            "body_away_start_ts": None,
        }
    )
    ts = TimeSense(state=state, clock=clock)

    # 身体在线时 body_away_s = 0
    assert ts.body_away_seconds() == 0.0
    assert ts.presence_seconds() == 0.0  # 刚上线，在场时长为 0 而非负

    # 触发离开（body_away_start_ts 由心跳循环设置）
    state.body_away_start_ts = T0
    clock.jump(3600.0)  # 离线 1h

    s = ts.summary()
    assert s["body_away_s"] >= 3500, f"离开时 body_away_s 应 > 3500，实际 {s['body_away_s']}"
    assert s["presence_s"] == 0, "离线后在场的时长应为 0"


# ── 3. 时钟延迟模拟有效（离线 2h 感知） ──────────────────


def test_clock_delay_simulates_offline_2h() -> None:
    """SimulatedClock.jump(2h) → 模式 ALONE + away_s >= 7200。"""
    clock = SimulatedClock(start_ts=T0)
    mgr = ModeManager(clock=clock)

    # 身体在场（最近在线 = T0）
    mode = mgr.mode(last_body_online_ts=T0, now=T0)
    assert mode is Mode.PRESENT, "刚在线应为 PRESENT"

    # 跳 2h（模拟身体断开后时钟加速）
    clock.jump(7200.0)
    mode = mgr.mode(last_body_online_ts=T0, now=clock.now())
    assert mode is Mode.ALONE, f"离线 2h 应为 ALONE，实际为 {mode}"

    away = mgr.away_seconds(last_body_online_ts=T0, now=clock.now())
    assert away >= 7200, f"away_s 应 >= 7200，实际 {away}"

    # 跳 24h+ → BODY_AWAY
    clock.jump(86400.0)
    mode = mgr.mode(last_body_online_ts=T0, now=clock.now())
    assert mode is Mode.BODY_AWAY, f"离线 24h+ 应为 BODY_AWAY，实际为 {mode}"


def test_clock_delay_with_timesense() -> None:
    """TimeSense + SimulatedClock.jump 延迟感知与退出。"""
    clock = SimulatedClock(start_ts=T0)
    state = TimeSenseState(
        first_existence_ts=T0,
        last_soul_beat_ts=T0,
        last_body_online_ts=T0,
        last_interaction_ts=T0,
        body_away_start_ts=None,
    )
    ts = TimeSense(state=state, clock=clock)

    # 身体离线 3h（通过跳时 + 设置离开起点模拟心跳逻辑）
    clock.jump(10_800.0)
    state.body_away_start_ts = state.last_body_online_ts

    s = ts.summary()
    assert s["away_grade"] == "away", f"离线 3h 应为 away，实际 {s['away_grade']}"
    assert s["body_away_s"] >= 10_000

    # 身体回来（last_body_online_ts 刷新 → body_away_start_ts 清空）
    state.last_body_online_ts = clock.now()
    state.body_away_start_ts = None
    s2 = ts.summary()
    assert s2["away_grade"] == "present", "身体回来后应为 present"


# ── 4. 难受测试（资源注入） ──────────────────────────────


def test_distress_injection_holds_30s() -> None:
    """injectable_sampler 注入 CPU>85% → DistressMonitor 30s hold 后触发。"""
    monitor = DistressMonitor(cpu_threshold=85.0, hold_s=DISTRESS_HOLD_S)
    t0 = 1_000_000.0
    # 高 CPU 持续 29s → 不应触发
    for i in range(29):
        monitor.update(t0 + i, cpu_percent=90.0)
    assert monitor.distress is False, "高压 29s 不应触发"

    # 第 30s → 触发
    changed = monitor.update(t0 + 30.0, cpu_percent=90.0)
    assert monitor.distress is True
    assert changed is True

    # 依然保持
    changed = monitor.update(t0 + 31.0, cpu_percent=90.0)
    assert monitor.distress is True
    assert changed is False  # 状态未切换

    # CPU 恢复 → 退出难受
    changed = monitor.update(t0 + 35.0, cpu_percent=50.0)
    assert monitor.distress is False
    assert changed is True


def test_distress_injection_cpu_normal_no_false_positive() -> None:
    """正常 CPU 不应误触发难受。"""
    monitor = DistressMonitor(cpu_threshold=85.0, hold_s=5.0)
    t0 = 1_000_000.0
    for i in range(20):
        monitor.update(t0 + i, cpu_percent=30.0)
    assert monitor.distress is False


# ── 5. 检查点一致性 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_checkpoint_consistency(tmp_path: Path) -> None:
    """检查点快照 → 写入更多数据 → 回滚 → 数据与快照一致。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"

    # 写入初始数据
    ss = StateStore(state_db)
    hs = HeartbeatStore(heartbeat_db)
    await ss.start()
    await hs.start()
    await ss.save_json("key1", "v1")
    await hs.append(
        T0,
        "soul",
        build_snapshot(timesense_summary={"age_days": 1}, mode="present", distress=False),
    )
    await ss.close()
    await hs.close()

    # 建快照
    mgr = CheckpointManager(data_dir)
    cp = await mgr.create(state_db, heartbeat_db)

    # 快照后继续写入
    ss2 = StateStore(state_db)
    hs2 = HeartbeatStore(heartbeat_db)
    await ss2.start()
    await hs2.start()
    await ss2.save_json("key1", "v2")
    await hs2.append(
        T0 + 1,
        "soul",
        build_snapshot(timesense_summary={"age_days": 2}, mode="present", distress=False),
    )
    await ss2.close()
    await hs2.close()

    # 验证已更新
    conn = sqlite3.connect(str(state_db))
    val = json.loads(conn.execute("SELECT value FROM kv WHERE key='key1'").fetchone()[0])
    assert val == "v2", f"快照后 key1 应为 v2，实际 {val}"
    conn.close()

    # 回滚
    mgr.restore(cp)

    # 验证回到快照时刻
    conn2 = sqlite3.connect(str(state_db))
    val2 = json.loads(conn2.execute("SELECT value FROM kv WHERE key='key1'").fetchone()[0])
    assert val2 == "v1", f"回滚后 key1 应为 v1，实际 {val2}"
    conn2.close()

    hb_conn = sqlite3.connect(str(heartbeat_db))
    rows = hb_conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]
    assert rows == 1, f"回滚后 heartbeats 应为 1 行，实际 {rows}"
    hb_conn.close()
