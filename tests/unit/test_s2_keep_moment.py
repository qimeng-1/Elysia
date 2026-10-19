"""S2：她的"这条我想留着"（keep_this_moment）。

用户拍板：「决定权交给她」。程序不替她标重要——她给出**自己的理由**，才能把
**最近一条已封存的交换**定格成重要记忆；上限 **13 次/天**（可配、本地日界重置、幂等、
超限明确拒绝）。第 7 条验收是灵魂：她留住的那条**必须绕过**"进行中对话"闸、能被
hooks 检索到，否则"留住"是个看不见的动作。

取消测试（逐条能红，见交付报告）：
- 去掉上限 ⇒ `test_daily_limit_14th_rejected` / `test_cross_day_reset` 红；
- 去掉 reason 必填/长度校验 ⇒ `test_reason_required_and_bounded` 红；
- 去掉 protected/deep/importance 任一效果 ⇒ `test_keep_success_effects_and_log` 红；
- 去掉"protected 绕过闸" ⇒ `test_kept_memory_bypasses_dialogue_guard` 红。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    KIND_EXCHANGE,
    LEVEL_SHALLOW,
    SOURCE_USER,
    MemoryRecord,
)
from elysia.memory.retrieve import select_hooks
from elysia.soul.tools.keep_moment import (
    KEEP_DAILY_LIMIT,
    KEEP_IMPORTANCE_FLOOR,
    MomentKeeper,
    local_day_start,
)

T0 = 1_000_000.0
MOOD = {"chat": 0.6, "miss": 0.4}


async def _add_exchange(store: HeartbeatStore, ts: float, content: str) -> int:
    return await store.add_memory(
        ts,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_EXCHANGE,
            "content": content,
            "emotion_vector": {"chat": 0.6, "miss": 0.35},
            "importance": 0.4,
            "protected": False,
            "narrative": content,
            "source": SOURCE_USER,
            "certainty": CERTAINTY_CERTAIN,
        },
    )


@pytest.fixture()
def store(tmp_path: Path) -> HeartbeatStore:
    return HeartbeatStore(tmp_path / "heartbeat.db")


# ── 验收 1：成功生效 ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_keep_success_effects_and_log(
    store: HeartbeatStore, caplog: pytest.LogCaptureFixture
) -> None:
    await store.start()
    try:
        mid = await _add_exchange(store, T0, "你说咖啡洒键盘上了，我更担心你的手")
        keeper = MomentKeeper(store, clock=lambda: T0 + 100)
        with caplog.at_level(logging.INFO):
            receipt = await keeper.keep_this_moment("那句担心是真的，我想记住")

        memory = await store.get_memory(mid)
        assert memory is not None
        assert receipt.startswith("（你留住了")
        assert "咖啡洒键盘" in receipt, "回执必须复述那条记忆的内容"
        assert memory["protected"] is True
        assert memory["level"] == "deep"
        assert memory["importance"] >= KEEP_IMPORTANCE_FLOOR
        assert memory["keep_reason"] == "那句担心是真的，我想记住"
        assert any("kept_memory" in record.getMessage() for record in caplog.records), (
            "落库后必须写 kept_memory 日志"
        )
    finally:
        await store.close()


# ── 验收 2：上限（第 14 次拒绝，且不改记忆）──────────────────
@pytest.mark.asyncio
async def test_daily_limit_14th_rejected(store: HeartbeatStore) -> None:
    assert KEEP_DAILY_LIMIT == 13
    await store.start()
    try:
        keeper = MomentKeeper(store, clock=lambda: T0)
        for i in range(KEEP_DAILY_LIMIT):
            await _add_exchange(store, T0 + i, f"第{i}次交换")
            assert "你留住了" in await keeper.keep_this_moment(f"理由{i}")

        blocked_id = await _add_exchange(store, T0 + 100, "第14次交换不该被留住")
        receipt = await keeper.keep_this_moment("我还想再留一条")
        memory = await store.get_memory(blocked_id)
        assert memory is not None
        assert "今天已经留住 13 条" in receipt, f"超限必须明确拒绝：{receipt}"
        assert memory["protected"] is False, "超限时不得改动任何记忆"
        assert memory["keep_reason"] is None
        assert await store.kept_moment_count_since(0.0) == KEEP_DAILY_LIMIT
    finally:
        await store.close()


# ── 验收 3：同一条重复留 ⇒ 只计 1 次 ─────────────────────────
@pytest.mark.asyncio
async def test_duplicate_keep_is_idempotent(
    store: HeartbeatStore, caplog: pytest.LogCaptureFixture
) -> None:
    await store.start()
    try:
        await _add_exchange(store, T0, "同一段交换")
        keeper = MomentKeeper(store, clock=lambda: T0 + 10)
        with caplog.at_level(logging.INFO):
            first = await keeper.keep_this_moment("第一次理由")
            second = await keeper.keep_this_moment("第二次理由")
        assert "你留住了" in first
        assert "已经留住过" in second
        assert await store.kept_moment_count_since(0.0) == 1, "重复留不得重复计数"
        assert sum("kept_memory" in r.getMessage() for r in caplog.records) == 1
    finally:
        await store.close()


# ── 验收 4/5：没有交换 / 理由不合法 ─────────────────────────
@pytest.mark.asyncio
async def test_no_exchange_fails_clearly(store: HeartbeatStore) -> None:
    await store.start()
    try:
        keeper = MomentKeeper(store, clock=lambda: T0)
        receipt = await keeper.keep_this_moment("想留住点什么")
        assert "没有可以留住的对话" in receipt
        assert (await store.iterate_memories()) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reason_required_and_bounded(store: HeartbeatStore) -> None:
    await store.start()
    try:
        mid = await _add_exchange(store, T0, "一段值得留的交换")
        keeper = MomentKeeper(store, clock=lambda: T0)
        receipt_empty = await keeper.keep_this_moment("   ")
        receipt_long = await keeper.keep_this_moment("字" * 121)
        memory = await store.get_memory(mid)
        assert memory is not None
        assert "需要理由" in receipt_empty
        assert "理由太长" in receipt_long
        assert memory["protected"] is False, "理由不合法时不得生效"
        assert memory["keep_reason"] is None
    finally:
        await store.close()


# ── 验收 6：跨日重置 ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_cross_day_reset(store: HeartbeatStore) -> None:
    await store.start()
    try:
        now = {"ts": T0}
        keeper = MomentKeeper(store, daily_limit=2, clock=lambda: now["ts"])
        for i in range(2):
            await _add_exchange(store, now["ts"], f"当天第{i}次交换")
            assert "你留住了" in await keeper.keep_this_moment(f"理由{i}")
        await _add_exchange(store, now["ts"], "当天第三次")
        assert "今天已经留住" in await keeper.keep_this_moment("当天第三次理由")

        now["ts"] = local_day_start(T0) + 86400.0 + 10.0  # 次日
        await _add_exchange(store, now["ts"], "次日的交换")
        assert "你留住了" in await keeper.keep_this_moment("新的一天，再留一条")
    finally:
        await store.close()


# ── 验收 7：她留住的那条能被 hooks 看到（绕过进行中对话闸）────
@pytest.mark.asyncio
async def test_kept_memory_bypasses_dialogue_guard(store: HeartbeatStore) -> None:
    await store.start()
    try:
        now = T0 + 1000.0
        keeper = MomentKeeper(store, clock=lambda: now)
        kept_id = await _add_exchange(store, now - 1200.0, "你说咖啡洒键盘上了，我更担心你")
        await keeper.keep_this_moment("我在意的是你，不是键盘")
        # 一条同类但**没有**留住的近期交换（同一次对话上下文里）
        plain_id = await _add_exchange(store, now - 1100.0, "键盘坏了还能换，你手没事才要紧")

        records = [MemoryRecord.from_dict(d) for d in await store.iterate_memories()]
        hooks = select_hooks(
            records,
            MOOD,
            now=now,
            query="键盘",
            recent_dialogue_since_ts=now - 3600.0,  # 两条都还在进行中的对话里
        )
        ids = [h.memory_id for h in hooks]
        assert kept_id in ids, f"她留住的记忆必须能被想起（否则动作看不见）：{ids}"
        assert plain_id not in ids, "未留住的近期交换仍应被进行中对话闸挡住"
    finally:
        await store.close()


# ── 工具接线：链上工具表 + ExpressionService 派发 ─────────────
@pytest.mark.asyncio
async def test_tool_wiring(store: HeartbeatStore) -> None:
    from elysia.llm.chain import KEEP_THIS_MOMENT_TOOL
    from elysia.llm.deepseek import _TOOL_NAMES
    from elysia.soul.brain import BrainOutput
    from elysia.soul.expression_service import ExpressionService

    await store.start()
    try:
        await _add_exchange(store, T0, "要留住的交换内容")
        keeper = MomentKeeper(store, clock=lambda: T0 + 1)
        service = ExpressionService(
            None,  # type: ignore[arg-type]
            store,
            moment_keeper=keeper,
        )
        runner = service._make_tool_runner(BrainOutput(), now=T0 + 1, user_message=None)
        receipt = await runner("keep_this_moment", {"reason": "我想记住这次"})
        assert "你留住了" in receipt and "要留住的交换内容" in receipt
        assert KEEP_THIS_MOMENT_TOOL["function"]["name"] == "keep_this_moment"
        assert "keep_this_moment" in _TOOL_NAMES
    finally:
        await store.close()
