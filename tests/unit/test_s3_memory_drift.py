"""S3：允许记错——回忆可改写、原件保真、漂移留痕。

用户拍板 C：「当然允许记错」。两条硬约束：
1. `content` **永不改**（原件是考古证据）；
2. 每次改写都留历史（旧/新 narrative + 前后 certainty），certainty 有**地板**
   （certain→probable→uncertain，不再降成 speculative）。

`kind='self'`（身份种子/她的自我认知）**永不被重述改写**——身份不能被她一句随口话改掉。

取消测试（逐条能红，见交付报告）：
- 去掉阈值判定 ⇒ `test_persist_miss_changes_nothing` / `test_is_retell_threshold` 红；
- 顺手写 `content` ⇒ `test_persist_drift_rewrites_narrative_keeps_content` 红；
- 去掉 certainty 地板 ⇒ `test_three_retells_floor_at_uncertain` 红；
- 去掉 `kind='self'` 豁免 ⇒ `test_self_memory_is_exempt` 红。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.memory.drift import (
    MAX_NARRATIVE_CHARS,
    RETELL_OVERLAP_MIN,
    is_retell,
    maybe_drift,
    next_certainty,
)
from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    CERTAINTY_HEARD,
    CERTAINTY_PROBABLE,
    CERTAINTY_SPECULATIVE,
    CERTAINTY_UNCERTAIN,
    KIND_INTERACTION,
    KIND_SELF,
    LEVEL_SHALLOW,
    SOURCE_SYSTEM,
    SOURCE_USER,
)
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import (
    STATUS_SUCCESS,
    ExpressionResult,
)

T0 = 1_000_000.0
ORIGINAL = "你的生日是11月11日，我的生日是5月21日"
RETELL = "你的生日是11月11日，我记住了呀"
UNRELATED = "今天咖啡洒在键盘上了，键盘好像报废了"


async def _add_memory(
    store: HeartbeatStore,
    content: str = ORIGINAL,
    *,
    kind: str = KIND_INTERACTION,
    certainty: str = CERTAINTY_CERTAIN,
    source: str = SOURCE_USER,
) -> int:
    return await store.add_memory(
        T0,
        {
            "level": LEVEL_SHALLOW,
            "kind": kind,
            "content": content,
            "emotion_vector": {"chat": 0.6, "miss": 0.4},
            "importance": 0.5,
            "narrative": content,
            "source": source,
            "certainty": certainty,
        },
    )


def _result(text: str, hook_ids: list[int]) -> ExpressionResult:
    return ExpressionResult(
        job_id="job-1",
        status=STATUS_SUCCESS,
        order_key=(1, T0),
        text=text,
        level="main",
        validation=ValidationResult(ok=True, reason=None),
        instruction={},
        intent="回应",
        feelings={"chat": 0.6, "miss": 0.4},
        sentences=[text],
        hook_memory_ids=list(hook_ids),
    )


async def _persist(store: HeartbeatStore, text: str, hook_ids: list[int]) -> None:
    service = ExpressionService(None, store)  # type: ignore[arg-type]
    await service.persist(_result(text, hook_ids), now=T0 + 10.0)


# ── 纯函数：阈值 + certainty 阶梯 ────────────────────────────
def test_is_retell_threshold() -> None:
    assert RETELL_OVERLAP_MIN == 0.5
    assert is_retell(RETELL, ORIGINAL) is True
    assert is_retell(UNRELATED, ORIGINAL) is False
    assert is_retell("", ORIGINAL) is False
    assert is_retell(RETELL, "") is False
    assert is_retell([RETELL], ORIGINAL) is True, "多气泡拼接后也应能命中"


def test_certainty_ladder_has_floor() -> None:
    assert next_certainty(CERTAINTY_CERTAIN) == CERTAINTY_PROBABLE
    assert next_certainty(CERTAINTY_PROBABLE) == CERTAINTY_UNCERTAIN
    assert next_certainty(CERTAINTY_UNCERTAIN) == CERTAINTY_UNCERTAIN  # 地板
    assert next_certainty(CERTAINTY_HEARD) == CERTAINTY_UNCERTAIN
    assert next_certainty(CERTAINTY_SPECULATIVE) == CERTAINTY_SPECULATIVE  # 不再往下降


# ── 验收 1：命中 ⇒ 改 narrative、content 不变、降 certainty、留史 ──
@pytest.mark.asyncio
async def test_persist_drift_rewrites_narrative_keeps_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        mid = await _add_memory(store)
        with caplog.at_level(logging.INFO):
            await _persist(store, RETELL, [mid])

        memory = await store.get_memory(mid)
        assert memory is not None
        assert memory["narrative"] == RETELL, "narrative 应变成她自己的版本"
        assert memory["content"] == ORIGINAL, "content 原件必须逐字节不变"
        assert memory["certainty"] == CERTAINTY_PROBABLE, "certain 应降一档到 probable"
        history = await store.narrative_drift(mid)
        assert len(history) == 1
        assert history[0]["old_narrative"] == ORIGINAL
        assert history[0]["new_narrative"] == RETELL
        assert (history[0]["certainty_before"], history[0]["certainty_after"]) == (
            CERTAINTY_CERTAIN,
            CERTAINTY_PROBABLE,
        )
        assert any("memory_drift id=" in r.getMessage() for r in caplog.records)
    finally:
        await store.close()


# ── 验收 2：未命中 ⇒ 什么都不改 ─────────────────────────────
@pytest.mark.asyncio
async def test_persist_miss_changes_nothing(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        mid = await _add_memory(store)
        await _persist(store, UNRELATED, [mid])
        memory = await store.get_memory(mid)
        assert memory is not None
        assert memory["narrative"] == ORIGINAL
        assert memory["content"] == ORIGINAL
        assert memory["certainty"] == CERTAINTY_CERTAIN
        assert await store.narrative_drift(mid) == []
    finally:
        await store.close()


# ── 验收 3：连续 3 次重述 ⇒ 地板 uncertain，历史 3 行 ─────────
@pytest.mark.asyncio
async def test_three_retells_floor_at_uncertain(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        mid = await _add_memory(store)
        for step in range(3):
            outcome = await maybe_drift(store, mid, [RETELL], now=T0 + 10.0 * (step + 1))
            assert outcome is not None
        memory = await store.get_memory(mid)
        assert memory is not None
        assert memory["certainty"] == CERTAINTY_UNCERTAIN, "降到底就停，不许降成 speculative"
        assert memory["content"] == ORIGINAL
        history = await store.narrative_drift(mid)
        assert [h["certainty_after"] for h in history] == [
            CERTAINTY_PROBABLE,
            CERTAINTY_UNCERTAIN,
            CERTAINTY_UNCERTAIN,
        ]
    finally:
        await store.close()


# ── 验收 4：kind='self' 豁免 ────────────────────────────────
@pytest.mark.asyncio
async def test_self_memory_is_exempt(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        mid = await _add_memory(
            store, "我在意的是每一个和我相遇的人", kind=KIND_SELF, source=SOURCE_SYSTEM
        )
        await _persist(store, "我在意的是每一个和我相遇的人", [mid])
        memory = await store.get_memory(mid)
        assert memory is not None
        assert memory["narrative"] == "我在意的是每一个和我相遇的人", "身份不许被随口话改写"
        assert memory["certainty"] == CERTAINTY_CERTAIN
        assert await store.narrative_drift(mid) == []
        assert await maybe_drift(store, mid, ["我在意的是每一个和我相遇的人"], now=T0) is None
    finally:
        await store.close()


# ── 验收 5：hook_memory_ids 为空 ⇒ 不做漂移 ──────────────────
@pytest.mark.asyncio
async def test_empty_hook_ids_no_drift(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        mid = await _add_memory(store)
        await _persist(store, RETELL, [])
        memory = await store.get_memory(mid)
        assert memory is not None
        assert memory["narrative"] == ORIGINAL
        assert memory["certainty"] == CERTAINTY_CERTAIN
        assert await store.narrative_drift(mid) == []
    finally:
        await store.close()


# ── 验收 6：漂移史与 summary ────────────────────────────────
@pytest.mark.asyncio
async def test_narrative_drift_and_summary(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        first = await _add_memory(store)
        second = await _add_memory(store, "我养了一只小猫，叫团子")
        await maybe_drift(store, first, [RETELL], now=T0 + 10.0)
        await maybe_drift(store, second, ["我养了一只小猫，叫团子呀"], now=T0 + 20.0)

        history = await store.narrative_drift(first)
        assert [h["ts"] for h in history] == sorted(h["ts"] for h in history)
        summary = await store.memory_drift_summary(limit=5)
        assert [item["memory_id"] for item in summary] == [second, first]  # 新的在前
    finally:
        await store.close()


# ── 长原话截断到 200 字 ────────────────────────────────────
@pytest.mark.asyncio
async def test_narrative_is_truncated_to_200(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        mid = await _add_memory(store)
        long_retell = ORIGINAL + "嗯" * 400
        await maybe_drift(store, mid, [long_retell], now=T0)
        memory = await store.get_memory(mid)
        assert memory is not None
        assert len(memory["narrative"]) == MAX_NARRATIVE_CHARS
    finally:
        await store.close()


# ── 接线：run_job 把命中 id 随作业带回（内部传递）──────────────
@pytest.mark.asyncio
async def test_run_job_carries_hook_memory_ids(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from elysia.memory.retrieve import MemoryHit
    from elysia.soul.brain import BrainOutput, WillOutput
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState
    from elysia.soul.expression_types import ExpressionJob

    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        mid = await _add_memory(store)

        async def _retriever(*_args: Any, **_kwargs: Any) -> list[MemoryHit]:
            return [
                MemoryHit(
                    memory_id=mid,
                    narrative="你的生日是11月11日",
                    level="shallow",
                    score=1.0,
                    protected=False,
                )
            ]

        class _LLM:
            async def speak(self, payload: dict[str, Any]) -> Any:
                return SimpleNamespace(
                    text="你的生日是11月11日，我记住了呀",
                    level="main",
                    validated=ValidationResult(ok=True, reason=None),
                )

        service = ExpressionService(_LLM(), store, retriever=_retriever)  # type: ignore[arg-type]
        job = ExpressionJob(
            job_id="job-1",
            created_ts=T0,
            heartbeat_seq=1,
            output=BrainOutput(
                desire=DesireState(),
                feelings=FeelingState(chat=0.6, miss=0.4),
                will=WillOutput(thought_style=0.1),
            ),
            summary={},
            user_message="生日是哪天来着",
            intent="回应",
        )
        result = await service.run_job(job)
        assert result.hook_memory_ids == [mid]
        # 只内部传递：进 prompt 的仍只有文本标签，id 不进 prompt/指令
        hooks_in_prompt = result.instruction.get("memory_hooks") or []
        assert hooks_in_prompt and all(isinstance(hook, str) for hook in hooks_in_prompt)
        assert "hook_memory_ids" not in result.instruction
    finally:
        await store.close()
