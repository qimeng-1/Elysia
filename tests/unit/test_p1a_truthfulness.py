"""P1a（fix 后）：反编造——只拦"我读过外部世界"，且整句换成自然诚实句。

用户实测（2026-10-20）：旧实现把 12 句日常话改了 10 句，还会产出"我没真的看过一下时间"
这类语病。P1a-fix 两条：
1. **只拦外部信息主张**：动作（读/查/搜/逛/翻/看）**且**外部来源对象（维基/页面/网页/
   米游社/论坛/帖子/资料/文章/网上），或引用外部内容（"维基上写着"）；
   「我看了你一眼」「我去趟厨房」「我看了个视频」「我读过那本书」**不拦**。
2. **整句替换**成自然诚实句（「其实我还没真的去翻过呢。」），同一气泡其余句子原样保留。

依据判定/动机放行/日志/统计保持原样。

T-024（2026-10-22 真机 16:43）：阅读主张被拆成三句时按句判定会漏（动作与页面对象
分居不同句）⇒ 新增**整轮判定**：整轮 (a) 完成态阅读动作 + (b) 页面类对象 + (c) 无据
⇒ 整轮换成一句诚实句；日常保护（书/视频/一眼/自己的记忆）与引用型逐句路径不变。

取消测试：把检测放宽成"有动作就算"（去掉外部来源对象要求）⇒
`test_daily_sentences_are_never_rewritten` 必红（12 句日常话被误伤）。
去掉整轮判定 ⇒ `test_real_machine_cross_sentence_claim_...` 必红。
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.memory.levels import (
    KIND_EXCHANGE,
    KIND_WORLD,
    LEVEL_SHALLOW,
    SOURCE_OBSERVATION,
    SOURCE_USER,
)
from elysia.memory.retrieve import MemoryHit
from elysia.soul.expression_service import ExpressionService
from elysia.soul.honesty import (
    claim_is_related,
    is_external_claim,
    is_round_external_claim,
    reset_claim_stats,
    rewrite_unfounded_claims,
)
from elysia.soul.tools.read_url import ReadResult

T0 = 1_000_000.0
CLAIM = "我读了维基上关于埃舍尔的页面。今天天气不错。"
HONEST = "其实我还没真的去翻过呢。"
EVIDENCE = "《埃舍尔》是荷兰版画艺术家，以不可能的图形闻名。"

# T-024 真机原文（2026-10-22 16:43:25）：她把阅读主张拆成三句，当天无 read_url 调用、
# 无 world 记忆、hook_memory_ids=[]。期望整轮换成一句诚实句，不留页面细节。
REAL_MACHINE_CLAIM = (
    "我翻了。是一页讲海星的，说它们断了腕还能长出新的来。"
    "我看着那行字发了会儿呆——原来有些东西不靠记着以前，也能自己长回来。"
)
SEA_STAR_EVIDENCE = "海星断了腕还能长出新的来，这是它们的再生能力。"

# 用户实测的"误伤尺子"（12 句日常话：一句都不许改）
DAILY_SENTENCES = [
    "我看了你一眼，你好像有点累。",
    "我看了一下时间，已经三点了。",
    "我读过那本书，挺喜欢的。",
    "我去了趟厨房，倒了杯水。",
    "我查了一下，你的生日是11月11日。",
    "我搜了搜，没找到。",
    "我看了个视频，特别好笑。",
    "我去过海边，风很大。",
    "我翻了翻手机，没什么消息。",
    "我睡了一觉，好多了。",
    "我在厨房待了一会儿。",
    "我看了会儿书，困了。",
    # T-024 补：不许因为"翻了"就把她翻自己的记忆当成阅读世界
    "我翻了翻自己的记忆，没找到。",
]

# 真正该拦的外部信息主张
EXTERNAL_CLAIMS = [
    "我读了维基上关于埃舍尔的页面。",
    "页面翻了一堆。",
    "我在那页看到埃舍尔。",
    "维基上写着埃舍尔是荷兰人。",
]


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text

    async def speak(self, payload: dict[str, Any]) -> Any:
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


# ── 日常语料回归集（P1a-fix 的验收尺子）────────────────────
def test_daily_sentences_are_never_rewritten() -> None:
    reset_claim_stats()
    for sentence in DAILY_SENTENCES:
        assert is_external_claim(sentence) is False, f"日常动作被误判成编造：{sentence}"
        assert is_round_external_claim(sentence) is False, f"整轮判定误伤日常话：{sentence}"
        result = rewrite_unfounded_claims(sentence)
        assert result.text == sentence, f"日常话被改写：{sentence} → {result.text}"
        assert result.rewritten == 0


def test_external_claims_are_detected_and_rewritten_as_whole_sentence() -> None:
    reset_claim_stats()
    for sentence in EXTERNAL_CLAIMS:
        assert is_external_claim(sentence) is True
        result = rewrite_unfounded_claims(sentence)
        assert result.rewritten == 1
        assert result.text.startswith("其实"), result.text
        assert "我没真的看过一下" not in result.text, "不许把短语塞回原句造语病"


def test_rest_of_bubble_is_kept() -> None:
    """T-024 起：无据的**整轮**主张整轮替换（不只换那一句）。

    旧的"同一气泡其余句子原样保留"只对**没有完成态动作**的引用型主张成立；
    跨句阅读主张整轮换，否则会留下「是一页讲海星的」这类编造细节。
    """
    reset_claim_stats()
    result = rewrite_unfounded_claims(CLAIM)
    assert result.text == HONEST
    assert "今天天气不错" not in result.text, "跨句编造整轮替换，不留残余句子"
    assert "我读了维基" not in result.text
    assert result.rewritten == 1

    # 引用型（没有"读了/看过"这类完成态动作）仍走逐句路径，其余句子保留
    citation_only = "维基上写着埃舍尔是荷兰人。今天天气不错。"
    result2 = rewrite_unfounded_claims(citation_only)
    assert "今天天气不错" in result2.text
    assert "维基上写着" not in result2.text


# ── T-024：跨句阅读主张（整轮判定）────────────────────────────
def test_real_machine_cross_sentence_claim_is_rewritten_as_one_honest_sentence() -> None:
    """真机 16:43 三句 ⇒ 单句诚实句（无海星/那行字等页面细节）。

    取消测试：去掉 `rewrite_unfounded_claims` 的整轮判定 ⇒ 本用例红
    （逐句判定：动作句无页面对象、页面细节句无完成态动作 ⇒ 整轮原样逃逸）。
    """
    reset_claim_stats()
    assert is_round_external_claim(REAL_MACHINE_CLAIM) is True
    result = rewrite_unfounded_claims(REAL_MACHINE_CLAIM)
    assert result.text == "其实我还没真的去翻过呢。"
    assert result.rewritten == 1
    assert "海星" not in result.text
    assert "那行字" not in result.text
    assert "一页" not in result.text


def test_round_claim_with_related_evidence_passes() -> None:
    """有据放行：依据（world 记忆/工具结果/用户原话）与整轮内容相关 ⇒ 原样。

    取消测试：去掉 `_round_has_evidence` 依据判定 ⇒ 本用例红。
    """
    reset_claim_stats()
    result = rewrite_unfounded_claims(REAL_MACHINE_CLAIM, evidence_texts=[SEA_STAR_EVIDENCE])
    assert result.text == REAL_MACHINE_CLAIM
    assert result.rewritten == 0


def test_round_claim_with_unrelated_evidence_is_still_rewritten() -> None:
    """依据与整轮内容不相关 ⇒ 仍按无据处理（不拿无关资料当挡箭牌）。"""
    result = rewrite_unfounded_claims(REAL_MACHINE_CLAIM, evidence_texts=["今天原神更新了"])
    assert result.text == "其实我还没真的去翻过呢。"


def test_lookup_without_page_object_passes() -> None:
    """「我查了下，确实是那样。」没有页面类对象 ⇒ 不是外部阅读主张，原样。"""
    sentence = "我查了下，确实是那样。"
    assert is_round_external_claim(sentence) is False
    assert rewrite_unfounded_claims(sentence).text == sentence


def test_excluded_action_does_not_make_round_claim() -> None:
    """跟随宾语代词的完成态动作不算：不许把「我看了你一眼」所在整轮当阅读主张。"""
    text = "我看了你一眼。那页纸还摊在桌上。"
    assert is_round_external_claim(text) is False
    assert rewrite_unfounded_claims(text).text == text


# ── 依据 / 动机（保持原行为）──────────────────────────────
def test_tool_evidence_passes() -> None:
    reset_claim_stats()
    result = rewrite_unfounded_claims(CLAIM, evidence_texts=[EVIDENCE])
    assert result.text == CLAIM
    assert result.rewritten == 0


def test_memory_evidence_passes() -> None:
    reset_claim_stats()
    result = rewrite_unfounded_claims(CLAIM, evidence_texts=[EVIDENCE])
    assert result.text == CLAIM


def test_claim_relatedness_uses_content_words() -> None:
    assert claim_is_related("我读了维基上关于埃舍尔的页面", EVIDENCE) is True
    assert claim_is_related("我读了维基上关于埃舍尔的页面", "今天原神更新了") is False


def test_motive_allows_fiction() -> None:
    reset_claim_stats()
    result = rewrite_unfounded_claims(CLAIM, motive=(7, "surprise"))
    assert result.text == CLAIM, "有动机 ⇒ 有理由的谎放行"
    assert result.allowed == 1
    assert result.rewritten == 0


# ── 服务接线 ──────────────────────────────────────────────
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
        assert result.text == HONEST
        assert any("unfounded_claims rewritten=" in r.getMessage() for r in caplog.records)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_daily_sentence_passes_through_service(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        daily = "我看了你一眼，你好像有点累。"
        service = ExpressionService(_FakeLLM(daily), store)  # type: ignore[arg-type]
        result = await service.run_job(_job(user_message="你在干嘛"))
        assert result.text == daily
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
async def test_world_memory_evidence_passes_cross_sentence(tmp_path: Path) -> None:
    """T-024 验收③：钩回的 world 记忆含「海星」且该轮提到海星 ⇒ 整轮原样。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        mid = await store.add_memory(
            T0,
            {
                "level": LEVEL_SHALLOW,
                "kind": KIND_WORLD,
                "content": SEA_STAR_EVIDENCE,
                "emotion_vector": {"chat": 0.5},
                "importance": 0.3,
                "protected": False,
                "narrative": SEA_STAR_EVIDENCE,
                "source": SOURCE_OBSERVATION,
            },
        )

        async def _retriever(*_args: Any, **_kwargs: Any) -> list[MemoryHit]:
            return [
                MemoryHit(
                    memory_id=mid,
                    narrative="海星是怎么再生的",
                    level="shallow",
                    score=1.0,
                    protected=False,
                )
            ]

        service = ExpressionService(  # type: ignore[arg-type]
            _FakeLLM(REAL_MACHINE_CLAIM), store, retriever=_retriever
        )
        result = await service.run_job(_job(user_message="海星是什么"))
        assert result.text == REAL_MACHINE_CLAIM, "world 记忆与整轮内容相关 ⇒ 有据，不改写"
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
        # P3 已上线：intentions 表由 store schema 建好，直接写一条 pending surprise
        await store.add_intention(
            T0, goal="想给她一个惊喜", action="read_url", target=None, kind="surprise"
        )

        service = ExpressionService(_FakeLLM(CLAIM), store)  # type: ignore[arg-type]
        with caplog.at_level(logging.INFO):
            result = await service.run_job(_job(user_message="你在干嘛"))
        assert result.text == CLAIM
        messages = [r.getMessage() for r in caplog.records]
        assert any("claim_fiction_allowed motive=" in m for m in messages)
        assert any("kind=surprise" in m for m in messages)
    finally:
        await store.close()
