"""T-018：出声归属——她的声音只播她这一句（桌宠不猜 mtime）。

后端：TTS 成功后 `kv['expression_audio'] = {ts, path, text_head, voice}`；
桌宠：`_play_her_audio()` 只读 kv，按 ts 去重；**不再 glob 最新 wav**。
前提（注释已写进两端）：桌宠不是她的身体，只是视觉形象；出声是她自己的决定，
桌宠只负责同步播放。桌宠**永不写** `kv['interaction']`。

取消测试：
- 删掉 `_publish_expression_audio` 调用 ⇒ `test_backend_publishes_expression_audio` 红；
- 恢复"取 mtime 最新" ⇒ `test_pet_plays_the_attributed_file_not_newest` 红。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from elysia.body.pet import PetWindow
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import STATUS_SUCCESS, ExpressionResult
from elysia.soul.heartbeat import SoulHeartbeat
from elysia.tts.cache import CachePool
from elysia.tts.chain import TTSChain, TTSRequest

T0 = 1_000_000.0


class _Synth:
    async def synthesize(self, text: str, emotion: str, speed: float) -> bytes:
        return b"RIFF....WAVEfake"


class _Breaker:
    async def can_speak(self) -> bool:
        return True


class _FakeLLM:
    async def speak(self, payload: dict) -> object:  # pragma: no cover - persist 不用
        raise AssertionError("本测试不走 LLM")


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.mark.asyncio
async def test_backend_publishes_expression_audio(tmp_path: Path) -> None:
    """TTS 成功 ⇒ 心跳落库时写 kv['expression_audio']，path 指向真实文件。"""
    state = StateStore(tmp_path / "state.db")
    hb = HeartbeatStore(tmp_path / "heartbeat.db")
    await state.start()
    await hb.start()
    try:
        cache = CachePool(tmp_path / "cache")
        tts = TTSChain(_Synth(), cache, _Breaker())  # type: ignore[arg-type]
        tts_result = await tts.speak(
            TTSRequest(text="今晚的风很轻。", emotion="default", speed=1.0)
        )
        assert tts_result.audio_path is not None
        assert Path(tts_result.audio_path).is_file(), "缓存路径必须是真实存在的 wav"

        service = ExpressionService(_FakeLLM(), hb)  # type: ignore[arg-type]
        soul = SoulHeartbeat(
            state_store=state,
            heartbeat_store=hb,
            timesense=None,  # type: ignore[arg-type]  # 本测试只调 persist
            mode_mgr=None,  # type: ignore[arg-type]
            clock=None,  # type: ignore[arg-type]
            expression=service,
        )
        soul._clock = type("C", (), {"now": lambda self: T0 + 5.0})()  # type: ignore[assignment]
        result = ExpressionResult(
            job_id="job-1",
            status=STATUS_SUCCESS,
            order_key=(1, T0),
            text="今晚的风很轻。",
            level="main",
            tts=tts_result,
        )
        await soul.persist_expressions([result], now=T0 + 5.0)

        payload = await state.load_json("expression_audio", default=None)
        assert isinstance(payload, dict)
        assert payload["voice"] is True
        assert Path(payload["path"]).is_file()
        assert payload["text_head"] == "今晚的风很轻。"
        assert float(payload["ts"]) == T0
    finally:
        await state.close()
        await hb.close()


def _seed_state_db(tmp_path: Path, payload: dict) -> tuple[Path, Path]:
    import sqlite3

    state_db = tmp_path / "state.db"
    hb_db = tmp_path / "heartbeat.db"
    con = sqlite3.connect(state_db)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute(
            "INSERT OR REPLACE INTO kv (key, value) VALUES ('expression_audio', ?)",
            (__import__("json").dumps(payload, ensure_ascii=False),),
        )
        con.commit()
    finally:
        con.close()
    con = sqlite3.connect(hb_db)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS heartbeats (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts REAL NOT NULL, beat_type TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        con.commit()
    finally:
        con.close()
    return state_db, hb_db


def test_pet_does_not_replay_old_audio_on_open(tmp_path: Path, qapp: QApplication) -> None:
    """T-018b：开窗时旧的那句只记账、不播放；之后 ts 变新才播。"""
    import json
    import sqlite3

    old = tmp_path / "old.wav"
    new = tmp_path / "new.wav"
    old.write_bytes(b"OLD")
    new.write_bytes(b"NEW")

    state_db, hb_db = _seed_state_db(
        tmp_path,
        {"ts": T0 + 1, "path": str(old), "text_head": "上一次那句", "voice": True},
    )
    pet = PetWindow(state_db, hb_db)
    played: list[str] = []
    pet._play_audio_path = lambda p: played.append(p)  # type: ignore[method-assign]
    try:
        pet._play_her_audio()
        assert played == [], "开窗不得重播上一次的旧音频（只记账）"
        assert pet._last_played_audio_ts == T0 + 1

        # 新的一句落 kv（ts 变新）⇒ 播一次
        con = sqlite3.connect(state_db)
        try:
            con.execute(
                "UPDATE kv SET value = ? WHERE key = 'expression_audio'",
                (
                    json.dumps(
                        {"ts": T0 + 2, "path": str(new), "text_head": "新的一句", "voice": True},
                        ensure_ascii=False,
                    ),
                ),
            )
            con.commit()
        finally:
            con.close()
        pet._play_her_audio()
        assert played == [str(new)]
        pet._play_her_audio()
        assert played == [str(new)], "同一条 ts 只播一次"
    finally:
        pet.close()


def test_pet_plays_the_attributed_file_not_newest(tmp_path: Path, qapp: QApplication) -> None:
    """取消测试：恢复"取 mtime 最新" ⇒ 会播错文件（本例断言播的是 kv 指定文件）。"""
    her = tmp_path / "her.wav"
    other = tmp_path / "other.wav"
    her.write_bytes(b"HER")
    other.write_bytes(b"NEWEST")
    # 让 other 成为"最新 mtime"：她的文件更旧
    os.utime(her, (1000.0, 1000.0))
    os.utime(other, (2000.0, 2000.0))

    state_db, hb_db = _seed_state_db(
        tmp_path,
        {"ts": T0 + 1, "path": str(her), "text_head": "她这一句", "voice": True},
    )
    pet = PetWindow(state_db, hb_db)
    played: list[str] = []
    pet._play_audio_path = lambda p: played.append(p)  # type: ignore[method-assign]
    try:
        # T-018b 起开窗不再重播旧句；这里把 kv 换成**新 ts** 模拟"她刚说出的这一句"
        import json
        import sqlite3

        con = sqlite3.connect(state_db)
        try:
            con.execute(
                "UPDATE kv SET value = ? WHERE key = 'expression_audio'",
                (
                    json.dumps(
                        {
                            "ts": T0 + 2,
                            "path": str(her),
                            "text_head": "她这一句",
                            "voice": True,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            con.commit()
        finally:
            con.close()
        pet._play_her_audio()
        assert played == [str(her)], "必须播 kv 指定她那句的文件，而不是最新 mtime"
        # 同一条 ts 再调 ⇒ 只播一次
        pet._play_her_audio()
        assert played == [str(her)]
        assert pet._last_played_audio_ts == T0 + 2
    finally:
        pet.close()
