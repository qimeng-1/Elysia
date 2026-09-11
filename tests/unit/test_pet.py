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
