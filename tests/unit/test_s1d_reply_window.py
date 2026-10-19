"""S1d：回复归属窗口——自发气泡不得混进上一次交换的记忆。

真机缺陷（2026-10-20 03:0x）：用户 02:47 之后 20 分钟没说话，她自发开口 9 次，
`dialogue_turns` 里全部 `exchange_id=3`（属于 02:4x 那次交换）。⇒ 封存 ex=3 时
`build_exchange_text` 会把「你一句 + 她 9 句（8 句自言自语）」压成一条事件记忆，
记忆内容失真。

修法（确定性、可测）：
- `record_her_bubble`：仅当 `now - 最近一条 user turn 的 ts <= REPLY_WINDOW_S(180s)`
  才归当前交换号；超出窗口 ⇒ `exchange_id=0`（自发，不属于任何交换）；
- 封存/正文构建一律忽略 `exchange_id <= 0`。

取消测试：去掉窗口判断（总是归当前交换号）⇒ `test_late_self_talk_gets_exchange_zero`
与 `test_sealed_exchange_excludes_late_self_talk` 必红（能复现"自言自语被写进交换记忆"）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import elysia.soul.dialogue as dialogue
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.memory.levels import KIND_EXCHANGE
from elysia.soul.dialogue import (
    REPLY_WINDOW_S,
    SELF_TALK_EXCHANGE_ID,
    build_exchange_text,
    record_her_bubble,
    record_user_message,
    seal_pending,
)

T0 = 1_000_000.0


def _turns_by_role(turns: list[dict]) -> list[tuple[str, int, str]]:
    return [(t["role"], t["exchange_id"], t["text"]) for t in turns]


@pytest.mark.asyncio
async def test_late_self_talk_gets_exchange_zero(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        assert REPLY_WINDOW_S == 180.0
        await record_user_message(store, T0, "杯子离键盘那么近")
        await record_her_bubble(store, T0 + 300.0, "还没有回音，你是在忙吗？")  # 5 分钟后
        turns = await store.dialogue_turns()
        assert _turns_by_role(turns) == [
            ("user", 1, "杯子离键盘那么近"),
            ("her", SELF_TALK_EXCHANGE_ID, "还没有回音，你是在忙吗？"),
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reply_within_window_keeps_exchange_id(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        await record_user_message(store, T0, "你在吗")
        await record_her_bubble(store, T0 + 10.0, "我在呢")
        turns = await store.dialogue_turns()
        assert [(t["role"], t["exchange_id"]) for t in turns] == [("user", 1), ("her", 1)]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_window_is_configurable_by_constant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        monkeypatch.setattr(dialogue, "REPLY_WINDOW_S", 1.0)
        await record_user_message(store, T0, "短窗口")
        await record_her_bubble(store, T0 + 2.0, "超出配置窗口")
        turns = await store.dialogue_turns()
        assert turns[-1]["exchange_id"] == SELF_TALK_EXCHANGE_ID
    finally:
        await store.close()


def test_build_exchange_text_ignores_exchange_zero() -> None:
    turns = [
        {"role": "user", "text": "杯子离键盘那么近", "exchange_id": 1},
        {
            "role": "her",
            "text": "问这些已经晚了呀",
            "exchange_id": 1,
            "emotion_vector": {"miss": 0.3},
        },
        {"role": "her", "text": "没回音这么久，大概在收拾咖啡吧", "exchange_id": 0},
        {"role": "her", "text": "我不催了", "exchange_id": 0},
    ]
    content, _emotion = build_exchange_text(turns)
    assert "杯子离键盘那么近" in content
    assert "问这些已经晚了呀" in content
    assert "没回音这么久" not in content, "自发气泡不得进交换记忆"
    assert "我不催了" not in content


@pytest.mark.asyncio
async def test_sealed_exchange_excludes_late_self_talk(tmp_path: Path) -> None:
    """真机复现：一条用户消息 + 她的回复 + 5 分钟后的自言自语 ⇒ 封存只含回复。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    state = StateStore(tmp_path / "state.db")
    await store.start()
    await state.start()
    try:
        await record_user_message(store, T0, "杯子离键盘那么近")
        await record_her_bubble(
            store, T0 + 5.0, "该不该的，现在问也晚了呀", emotion_vector={"miss": 0.3}
        )
        await record_her_bubble(store, T0 + 300.0, "还没有回音，你是在忙吗？")  # 自发
        await record_her_bubble(store, T0 + 600.0, "我不催了，就安静待一会儿。")  # 自发

        await seal_pending(store, state, T0 + 700.0)

        exchanges = [m for m in await store.iterate_memories() if m["kind"] == KIND_EXCHANGE]
        assert len(exchanges) == 1
        content = exchanges[0]["content"]
        assert "杯子离键盘那么近" in content and "该不该的" in content
        assert "还没有回音" not in content, "5 分钟后的自言自语被压进了交换记忆（S1d 的病根）"
        assert "我不催了" not in content

        turns = await store.dialogue_turns()
        assert [t["exchange_id"] for t in turns if t["role"] == "her"] == [1, 0, 0]
    finally:
        await state.close()
        await store.close()


@pytest.mark.asyncio
async def test_self_talk_alone_is_never_sealed(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    state = StateStore(tmp_path / "state.db")
    await store.start()
    await state.start()
    try:
        await record_her_bubble(store, T0, "夜深了，我一个人待着")
        await record_her_bubble(store, T0 + 300.0, "还在就好。")
        await seal_pending(store, state, T0 + 700.0)
        await seal_pending(store, state, T0 + 900.0)  # 幂等：再来一次也不封
        assert not [m for m in await store.iterate_memories() if m["kind"] == KIND_EXCHANGE]
    finally:
        await state.close()
        await store.close()
