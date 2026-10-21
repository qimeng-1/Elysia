"""T-019：心情外化（层 1）——只读快照 + 慢半拍的变换级微动。

前提：桌宠**不是她的身体**（她看不见它），只是给用户的视觉形象。
后端把 `BrainOutput` 的心情快照写进 `kv['her_mood']`（只读、零行为改变）；
桌宠用纯函数 `mood_to_pose` 映射成渲染参数，**限速 + 惯性**（绝不抢在她情绪前面），
并且**只读 kv、永不写 `kv['interaction']`**（红线）。

取消测试：
- 去掉限速（直接用目标值）⇒ `test_mood_pose_is_rate_limited` 红；
- 删掉心跳里的快照写入 ⇒ `test_heartbeat_publishes_her_mood` 红；
- 删掉桌宠读取 mood 的路径 ⇒ `test_pet_applies_mood_without_writing_interaction` 红。
"""

from __future__ import annotations

import json
import os
import sqlite3
from itertools import pairwise
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from elysia.body.pet import MOOD_POSE_DEFAULTS, MOOD_POSE_RATES, PetWindow, mood_to_pose
from elysia.core.mode import Mode
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.heartbeat import SoulHeartbeat

T0 = 1_000_000.0


def _mood() -> dict:
    return {
        "ts": T0,
        "feelings": {
            "chat": 0.9,
            "miss": 0.7,
            "explore": 0.0,
            "curiosity": 0.0,
            "rest": 0.5,
            "self_check": 0.2,
        },
        "will": {"direction": "reach", "strength": 0.5, "thought_style": 0.0, "anim_bias": 0.0},
        "desire": {"tr": 50.0, "cs": 70.0, "sa": 10.0},
        "mode": "present",
        "day_phase": "night",
    }


# ── 纯函数：限速 + 单调逼近 ───────────────────────────────
def test_mood_pose_is_rate_limited() -> None:
    mood = _mood()
    # dt 很大时直接到目标（用来取目标值）
    target = mood_to_pose(mood, None, 1000.0)
    pose = mood_to_pose(mood, None, 0.1)
    for key, rate in MOOD_POSE_RATES.items():
        assert abs(pose[key] - MOOD_POSE_DEFAULTS[key]) <= rate * 0.1 + 1e-6, (
            f"第一步就超过限速：{key}={pose[key]}"
        )
    history = [pose]
    for _ in range(20):
        pose = mood_to_pose(mood, pose, 0.1)
        history.append(pose)
    for key, rate in MOOD_POSE_RATES.items():
        deltas = [abs(b[key] - a[key]) for a, b in pairwise(history)]
        assert deltas, key
        assert max(deltas) <= rate * 0.1 + 1e-6, f"{key} 超过限速：{max(deltas)} > {rate * 0.1}"
    # 单调逼近目标（不越过、不来回摆）
    lean_target = target["lean"]
    leans = [item["lean"] for item in history]
    assert all(b >= a - 1e-9 for a, b in pairwise(leans)), leans
    assert abs(leans[-1] - lean_target) <= 0.1, f"没有逼近目标：{leans[-1]} vs {lean_target}"


def test_mood_to_pose_handles_missing_fields() -> None:
    pose = mood_to_pose({}, None, 0.5)
    for key in MOOD_POSE_RATES:
        assert key in pose
    # rest 高 ⇒ 低头/慢呼吸；miss 高 ⇒ 靠近（lean 更大）
    calm = mood_to_pose({"feelings": {"rest": 0.9}}, None, 1000.0)
    eager = mood_to_pose({"feelings": {"chat": 0.95, "miss": 0.9}}, None, 1000.0)
    assert calm["gaze_y"] > 0
    assert eager["lean"] > calm["lean"]


# ── 后端：心跳写 kv['her_mood']（只读快照）────────────────
@pytest.mark.asyncio
async def test_heartbeat_publishes_her_mood(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.db")
    hb = HeartbeatStore(tmp_path / "heartbeat.db")
    await state.start()
    await hb.start()
    try:
        soul = SoulHeartbeat(
            state_store=state,
            heartbeat_store=hb,
            timesense=None,  # type: ignore[arg-type]
            mode_mgr=None,  # type: ignore[arg-type]
            clock=None,  # type: ignore[arg-type]
        )
        output = BrainOutput(
            desire=DesireState(),
            feelings=FeelingState(
                chat=0.61, miss=0.42, explore=0.47, curiosity=0.1, rest=0.07, self_check=0.2
            ),
            will=WillOutput(thought_style=0.2),
        )
        await soul._write_her_mood(
            T0 + 1.0,
            output,
            mode=Mode.PRESENT,
            summary={"day_phase": "night"},
        )
        mood = await state.load_json("her_mood", default=None)
        assert isinstance(mood, dict)
        assert float(mood["ts"]) == T0 + 1.0
        assert set(mood["feelings"]) == {
            "chat",
            "miss",
            "explore",
            "curiosity",
            "rest",
            "self_check",
        }
        assert "desire" in mood and "will" in mood
        assert mood["mode"] == "present" and mood["day_phase"] == "night"
    finally:
        await state.close()
        await hb.close()


# ── 桌宠：读 mood → 渲染参数；不写 interaction（红线）──────
def _seed_dbs(tmp_path: Path) -> tuple[Path, Path]:
    state_db = tmp_path / "state.db"
    hb_db = tmp_path / "heartbeat.db"
    con = sqlite3.connect(state_db)
    try:
        con.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute(
            "INSERT INTO kv (key, value) VALUES ('her_mood', ?)",
            (json.dumps(_mood(), ensure_ascii=False),),
        )
        con.commit()
    finally:
        con.close()
    payload = {"mode": "present", "distress": False, "desire": {"tr": 45, "cs": 60, "sa": 20}}
    con = sqlite3.connect(hb_db)
    try:
        con.execute(
            "CREATE TABLE heartbeats (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts REAL NOT NULL, beat_type TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO heartbeats (ts, beat_type, payload) VALUES (?, 'soul', ?)",
            (T0, json.dumps(payload)),
        )
        con.commit()
    finally:
        con.close()
    return state_db, hb_db


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_pet_applies_mood_without_writing_interaction(tmp_path: Path, qapp: QApplication) -> None:
    state_db, hb_db = _seed_dbs(tmp_path)
    pet = PetWindow(state_db, hb_db)
    writes: list[tuple[str | None, str]] = []
    pet._write_interaction = lambda text, *, kind: writes.append((text, kind))  # type: ignore[method-assign]
    try:
        pet._tick()
        assert pet._mood_pose is not None, "桌宠没有读 her_mood 并算出渲染参数"
        assert pet._canvas._pose["lean"] != 0.0
        pet._tick()
        assert writes == [], "心情外化路径绝不许写 kv['interaction']（红线）"
    finally:
        pet.close()
