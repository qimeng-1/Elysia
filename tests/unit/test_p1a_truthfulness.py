"""P1a：反编造——无依据的"我做过"就地改写成诚实说法（不是丢气泡）。

真机病根（2026-10-20 22:06）：她编造「页面翻了一堆」「花瓣的页面、旧剧的讨论」，
而她本人没有读过任何页面（`kind='world'` 记忆为 0，也没有后台执行器）。
用户裁定：谎可以撒，但**要有动机**；结构性缺失造成的编造不行。

处置原则（word_block 教训）：只改**主张那一句**里的"我做过/我看到"片段，
不丢整句、不丢同一条气泡其余内容；改不动才降级微声。

取消测试：去掉依据检查（`_span_related` 恒 False）⇒
`test_tool_evidence_passes` / `test_memory_evidence_passes` / `test_dialogue_evidence_passes`
必红（能复现"她把读过的页面说成没读过"）。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.memory.levels import KIND_EXCHANGE, LEVEL_SHALLOW, SOURCE_USER, MemoryRecord
from elysia.memory.retrieve import MemoryHit, select_hooks
from elysia.soul.expression_service import ExpressionService
from elysia.soul.honesty import (
    claim_is_related,
    reset_claim_stats,
    rewrite_unfounded_claims,
)
from elysia.soul.tools.read_url import ReadResult

T0 = 1_000_000.0
CLAIM = "我看了维基上关于埃舍尔的页面。今天天气不错。"
EVIDENCE = "《埃舍尔》是荷兰版画艺术家，以不可能的图形闻名。"


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.payloads: list[dict[str, Any]] = []

    async def speak(self, payload: dict[str, Any]) -> Any:
        self.payloads.append(dict(payload))
        return SimpleNamespace(
            text=self.text,
            level="main",
            validated=ValidationResult(ok=True, reason=None, sanitized=None),
        )


def _job(user_message: str | None = None):
    from elysia.soul.brain import BrainOutput, WillOutput
    from elysia.soul.desire import DesireState
    from elysia.soul.dimensions import FeelingState
    from elysia.soul.expression_types import ExpressionJob

    return ExpressionJob(
        job_id="job-1",
        created_ts=T0,
        heartbeat_seq=1,
        output=BrainOutput(
            desire=DesireState(),
            feelings=FeelingState(chat=0.6, miss=0.4),
            will=WillOutput(thought_style=0.1),
        ),
        summary={},
        user_message=user_message,
        intent="回应",
    )


# ── 纯函数：改写/依据/动机 ────────────────────────────────
def test_unfounded_claim_is_rewritten_but_rest_kept() -> None:
    reset_claim_stats()
    result = rewrite_unfounded_claims(CLAIM)
    assert "我没真的看过" in result.text
    assert "我看了" not in result.text
    assert "今天天气不错" in result.text, "同一气泡的其余内容不得丢"
    assert result.rewritten == 1


def test_tool_evidence_passes() -> None:
    reset_claim_stats()
    result = rewrite_unfounded_claims(CLAIM, evidence_texts=[EVIDENCE])
    assert result.text == CLAIM, "本次成功工具结果就是依据，不该改写"
    assert result.rewritten == 0


def test_memory_evidence_passes() -> None:
    reset_claim_stats()
    result = rewrite_unfounded_claims(CLAIM, evidence_texts=[EVIDENCE])
    assert result.text == CLAIM


def test_claim_relatedness_uses_content_words() -> None:
    # "我/看了/维基/页面"这些套话不能把无关依据算成相关
    assert claim_is_related("我看了维基上关于埃舍尔的页面", EVIDENCE) is True
    assert claim_is_related("我看了维基上关于埃舍尔的页面", "今天原神更新了") is False


def test_motive_allows_fiction() -> None:
    reset_claim_stats()
    result = rewrite_unfounded_claims(CLAIM, motive=(7, "surprise"))
    assert result.text == CLAIM, "有动机 ⇒ 有理由的谎放行"
    assert result.allowed == 1
    assert result.rewritten == 0


# ── 服务接线：无依据 ⇒ 改写；有依据/动机 ⇒ 放行 ────────────
@pytest.mark.asyncio
async def test_run_job_rewrites_fabrication(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        service = ExpressionService(_FakeLLM(CLAIM), store)  # type: ignore[arg-type]
        with caplog.at_level(logging.INFO):
            result = await service.run_job(_job(user_message="你在干嘛"))
        assert "我没真的看过" in result.text
        assert "今天天气不错" in result.text
        assert any("unfounded_claims rewritten=" in r.getMessage() for r in caplog.records)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_evidence_passes_in_service(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        mid = await store.add_memory(
            T0,
            {
                "level": LEVEL_SHALLOW,
                "kind": KIND_EXCHANGE,
                "content": EVIDENCE,
                "emotion_vector": {"chat": 0.5},
                "importance": 0.3,
                "protected": False,
                "narrative": EVIDENCE,
                "source": SOURCE_USER,
            },
        )

        async def _retriever(*_args: Any, **_kwargs: Any) -> list[MemoryHit]:
            return [
                MemoryHit(
                    memory_id=mid,
                    narrative="埃舍尔的版画",
                    level="shallow",
                    score=1.0,
                    protected=False,
                )
            ]

        service = ExpressionService(  # type: ignore[arg-type]
            _FakeLLM(CLAIM), store, retriever=_retriever
        )
        result = await service.run_job(_job(user_message="埃舍尔是谁"))
        assert result.text == CLAIM, "被钩回的 exchange 记忆与主张相关 ⇒ 有据，不改写"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dialogue_evidence_passes(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        from elysia.soul.dialogue import record_user_message

        await record_user_message(store, T0 - 5, "埃舍尔是荷兰版画艺术家")
        service = ExpressionService(_FakeLLM(CLAIM), store)  # type: ignore[arg-type]
        result = await service.run_job(_job(user_message="埃舍尔是谁"))
        assert result.text == CLAIM, "对话上下文（用户说过）⇒ 有据"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_tool_evidence_passes_in_service(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:

        def _reader(_url: str) -> ReadResult:
            return ReadResult(ok=True, kind="article", text=EVIDENCE, source="维基百科 · 埃舍尔")

        service = ExpressionService(_FakeLLM(CLAIM), store, url_reader=_reader)  # type: ignore[arg-type]
        await service._tool_read_url("https://zh.wikipedia.org/wiki/埃舍尔", T0)
        speak = await service._speak_with_retry({})
        verdict = await service._apply_truthfulness(
            speak, payload={}, dialogue_context=[], hook_memory_ids=[], now=T0
        )
        assert verdict.text == CLAIM, "本次成功工具结果 ⇒ 有据"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_motive_allows_and_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        # P3 之前 intentions 表还不存在；这里手工建表模拟"动机源已有数据"
        con = sqlite3.connect(str(tmp_path / "heartbeat.db"))
        con.execute(
            "CREATE TABLE intentions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " created_ts REAL, goal TEXT, action TEXT, target TEXT, kind TEXT,"
            " status TEXT, attempts INTEGER DEFAULT 0, last_attempt_ts REAL, outcome TEXT)"
        )
        con.execute(
            "INSERT INTO intentions (created_ts, goal, action, kind, status)"
            " VALUES (?, '想给她一个惊喜', 'read_url', 'surprise', 'pending')",
            (T0,),
        )
        con.commit()
        con.close()

        service = ExpressionService(_FakeLLM(CLAIM), store)  # type: ignore[arg-type]
        with caplog.at_level(logging.INFO):
            result = await service.run_job(_job(user_message="你在干嘛"))
        assert result.text == CLAIM
        messages = [r.getMessage() for r in caplog.records]
        assert any("claim_fiction_allowed motive=" in m for m in messages)
        assert any("kind=surprise" in m for m in messages)
    finally:
        await store.close()


def test_select_hooks_still_filters_world_kind_by_source() -> None:
    """P1a 只读证据：world 记忆的 source 必须是 observation，否则会被 hooks 闸挡住（P2 承重）。"""
    rec = MemoryRecord(
        id=1,
        created_ts=T0,
        kind="world",
        content="读了《埃舍尔》：荷兰版画艺术家",
        emotion_vector={"chat": 0.4},
        importance=0.25,
        source="observation",
        certainty="certain",
        narrative="读了《埃舍尔》：荷兰版画艺术家",
    )
    assert [h.memory_id for h in select_hooks([rec], {"chat": 0.5})] == [1]
