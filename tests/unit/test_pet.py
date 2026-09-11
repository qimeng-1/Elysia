"""桌宠冒烟：offscreen 模式测试 PetWindow 状态读取与交互提交。"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

# 必须在任何 PySide6 import 前设置
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

from elysia.body.pet import PetWindow
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.protocol.snapshots import build_snapshot

T0 = 1_000_000.0


@pytest.fixture
def qapp() -> QApplication:
    """共享 QApplication（每个进程只允许一个）。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


async def _seed_data(tmp_path: Path) -> None:
    """写入初始 soul 心跳数据供 pet 读取。"""
    ss = StateStore(tmp_path / "state.db")
    hs = HeartbeatStore(tmp_path / "heartbeat.db")
    await ss.start()
    await hs.start()
    for i in range(5):
        payload = build_snapshot(
            timesense_summary={
                "body_away_s": 0,
                "presence_s": i * 10,
                "since_interaction_s": 300 - i,
                "day_phase": "day",
                "age_days": 3.5,
                "away_grade": "present",
            },
            mode="present",
            distress=False,
        )
        await hs.append(T0 + i, "soul", payload)
    await ss.close()
    await hs.close()


async def _seed_p1_data(tmp_path: Path) -> None:
    """写入含 P1 字段（desire/feelings/will/brain_action）的 soul 心跳。"""
    ss = StateStore(tmp_path / "state.db")
    hs = HeartbeatStore(tmp_path / "heartbeat.db")
    await ss.start()
    await hs.start()
    payload = build_snapshot(
        timesense_summary={
            "body_away_s": 0,
            "presence_s": 120,
            "since_interaction_s": 30,
            "day_phase": "day",
            "age_days": 3.5,
            "away_grade": "present",
        },
        mode="present",
        distress=False,
        desire={"tr": 52.0, "cs": 61.0, "sa": 18.0},
        feelings={
            "chat": 0.4,
            "miss": 0.1,
            "explore": 0.5,
            "curiosity": 0.3,
            "rest": 0.2,
            "self_check": 0.05,
        },
        will={"direction": "reach", "strength": 0.56, "thought_style": -0.124, "anim_bias": 0.2},
        brain_action="think_quiet",
    )
    await hs.append(T0 + 100, "soul", payload)
    await ss.close()
    await hs.close()


@pytest.mark.asyncio
async def test_pet_reads_latest_heartbeat(tmp_path: Path, qapp: QApplication) -> None:
    """验证 PetWindow 能读取 soul 心跳并解析 payload。"""
    await _seed_data(tmp_path)
    pet = PetWindow(tmp_path / "state.db", tmp_path / "heartbeat.db")
    try:
        pet._tick()
        payload = pet._last_soul_payload
        assert payload is not None
        assert payload.get("mode") == "present"
        time_data = payload.get("time", {})
        assert time_data.get("age_days", 0) == 3.5
    finally:
        pet.close()


@pytest.mark.asyncio
async def test_pet_submit_interaction(tmp_path: Path, qapp: QApplication) -> None:
    """验证 submit_interaction 写入 DB。"""
    ss = StateStore(tmp_path / "state.db")
    await ss.start()
    await ss.close()

    pet = PetWindow(tmp_path / "state.db", tmp_path / "heartbeat.db")
    try:
        pet._submit_interaction("你好爱莉")
        conn = sqlite3.connect(tmp_path / "state.db")
        rows = conn.execute("SELECT value FROM kv WHERE key = 'interaction'").fetchall()
        conn.close()
        assert len(rows) == 1
        data = json.loads(rows[0][0])
        assert data["text"] == "你好爱莉"
        assert isinstance(data["ts"], float)
    finally:
        pet.close()


@pytest.mark.asyncio
async def test_pet_reads_p1_fields(tmp_path: Path, qapp: QApplication) -> None:
    """验证 PetWindow 能读取并保留 P1 字段（desire/feelings/will/brain_action）。"""
    await _seed_p1_data(tmp_path)
    pet = PetWindow(tmp_path / "state.db", tmp_path / "heartbeat.db")
    try:
        pet._tick()
        payload = pet._last_soul_payload
        assert payload is not None

        # desire：TR/CS/SA 透传到宠物层
        desire = payload.get("desire", {})
        assert abs(desire.get("tr", 0) - 52.0) < 1e-6
        assert abs(desire.get("cs", 0) - 61.0) < 1e-6
        assert abs(desire.get("sa", 0) - 18.0) < 1e-6

        # feelings：6 维感受透传
        feelings = payload.get("feelings", {})
        for dim in ("chat", "miss", "explore", "curiosity", "rest", "self_check"):
            assert isinstance(feelings.get(dim), float), f"感受维度缺失: {dim}"

        # will：意志层透传
        will = payload.get("will", {})
        assert will.get("direction") == "reach"
        assert isinstance(will.get("strength"), float)
        assert isinstance(will.get("thought_style"), float)

        # brain_action：行动层透传
        assert payload.get("brain_action") == "think_quiet"
    finally:
        pet.close()
