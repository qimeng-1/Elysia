"""P2 验收门测试（路线图 §7.7 / P2 §十）。

客观验收（不依赖真实 LLM/GPU，全部可注入）：
1. 降级链测试——断 API → 微声，全程不"死"，且降级写感受层 SA
2. 词汇表测试——虚构状态无法说出未授权生理词（越权 100% 拦截）
3. 表达服务集成——表达指令 → LLM → 校验 → 落库（thought_log + expression_log）
4. 触发节流——外部交互强制开口；内部冲动按双模态节流
5. TTS 熔断——高 VRAM 时只输出文本不阻塞
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from elysia.llm.chain import LLMChain
from elysia.llm.validator import ExpressionValidator
from elysia.soul.brain import BrainLoop
from elysia.soul.desire import DesireEvent, DesireSystem
from elysia.soul.expression_service import ExpressionService
from elysia.soul.words import PROTECTED_WORDS, is_permitted
from elysia.tts.breaker import VramBreaker
from elysia.tts.cache import CachePool
from elysia.tts.chain import TTSChain

T0 = 1_000_000.0


class _FixedTextBackend:
    """固定文本后端：模拟主声返回固定合规文本（无网络）。"""

    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, instruction: dict, prompt_template: dict) -> str | None:
        return self._text


class _NoneBackend:
    """模拟引擎不可用（触发降级）。"""

    async def complete(self, instruction: dict, prompt_template: dict) -> str | None:
        return None


class _FakeSynth:
    def __init__(self, ok: bool = True) -> None:
        self._ok = ok
        self.calls: list[tuple[str, str, float]] = []

    async def synthesize(self, text: str, emotion: str, speed: float) -> bytes | None:
        self.calls.append((text, emotion, speed))
        return b"RIFF-wav" if self._ok else None


def _brain_output(
    tr: float = 50.0, cs: float = 50.0, sa: float = 30.0, action: str = "think_quiet"
):
    ds = DesireSystem(rng=random.Random(0))
    ds._state.tr = tr
    ds._state.cs = cs
    ds._state.sa = sa
    brain = BrainLoop(desire_system=ds, rng=random.Random(0))
    return brain.step(mode="alone", dt=1.0)


def _noop_tts(cache_dir: Path, ok: bool = True) -> TTSChain:
    return TTSChain(
        _FakeSynth(ok=ok),
        CachePool(cache_dir),
        VramBreaker(threshold_mb=10240, sampler=lambda: 100),
    )


# ── 1. 表达服务集成：LLM 输出落库（thought_log + expression_log） ──


@pytest.mark.asyncio
async def test_expression_service_writes_both_logs(tmp_path: Path) -> None:
    from elysia.core.state_store import HeartbeatStore

    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        chain = LLMChain(main=_FixedTextBackend("想你了，今天也要好好吃饭"), fallback=None)
        svc = ExpressionService(chain, store, tts_chain=_noop_tts(tmp_path / "tts1"))

        outcome = await svc.tick(
            _brain_output(action="think_quiet"),
            now=T0,
            force=True,
            env={"miss": 0.8, "tr": 50, "cs": 50},
        )
        assert outcome is not None
        assert outcome.text == "想你了，今天也要好好吃饭"
        assert outcome.level == "main"
        assert outcome.tts is not None and outcome.tts.source == "synthesis"

        # thought_log：桌宠气泡读取
        thoughts = await store.execute_raw("SELECT kind, text FROM thought_log")
        assert thoughts == [("expression", "想你了，今天也要好好吃饭")]

        # expression_log：全链路取证
        logs = await store.execute_raw(
            "SELECT intent, llm_text, level, validation FROM expression_log"
        )
        assert len(logs) == 1
        assert logs[0][1] == "想你了，今天也要好好吃饭"
        assert logs[0][2] == "main"
        assert "ok" in logs[0][3]
    finally:
        await store.close()


# ── 2. 降级链：断 API → 微声，不"死"，写 SA ──


@pytest.mark.asyncio
async def test_expression_degrades_to_micro_and_writes_sa(tmp_path: Path) -> None:
    from elysia.core.state_store import HeartbeatStore

    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        desire = DesireSystem(rng=random.Random(0))
        sa_before = desire.state.sa

        async def on_degrade(kind: str, delta: float) -> None:
            desire.apply_event(DesireEvent(kind="degrade", intensity=delta))

        chain = LLMChain(
            main=None,  # 无主声 → 直接微声
            fallback=None,
            on_degrade=lambda _reason: on_degrade("llm", 2.0),
        )
        svc = ExpressionService(chain, store)

        outcome = await svc.tick(_brain_output(), now=T0, force=True)
        assert outcome is not None
        assert outcome.level == "micro"  # 微声兜底，全程不"死"
        assert outcome.text.strip() != ""

        # 降级即感受：SA 上升（微声触发 on_degrade → SA+2）
        assert desire.state.sa > sa_before
    finally:
        await store.close()


# ── 3. 词汇表测试：虚构状态无法说出未授权生理词 ──


def test_fabricated_state_cannot_say_protected_word() -> None:
    """未授权的受保护词（如"想你"）在虚构状态被拦。"""
    validator = ExpressionValidator()
    # 未授权：env 为空 → "想你"（miss<0.4 且 cs>=40）不可说
    result = validator.check("我好想你", {"lexical_permits": []})
    assert not result.ok
    assert result.reason == "word_block:想你"


def test_permitted_word_passes_validation() -> None:
    """真实授权后，受保护词可正常说出。"""
    validator = ExpressionValidator()
    result = validator.check(
        "我好想你", {"lexical_permits": ["想你"], "constraints": {"max_chars": 120}}
    )
    assert result.ok


def test_permits_derived_from_real_env() -> None:
    """词汇许可由真实状态注入：思念高 → 授权"想你"。"""
    from elysia.llm.validator import ExpressionValidator

    validator = ExpressionValidator()
    env = {"miss": 0.8, "cs": 50}
    payload = validator.enrich_permits(
        {"lexical_permits": [], "constraints": {"max_chars": 120}},
        env,
    )
    assert "想你" in payload["lexical_permits"]


def test_llm_cannot_escape_vocabulary() -> None:
    """词 → 触发状态代码写死：虚构状态即使 LLM 想用也被拦。"""
    validator = ExpressionValidator()
    for word in PROTECTED_WORDS:
        if not is_permitted(word, []):
            result = validator.check(f"我好{word}啊", {"lexical_permits": []})
            assert not result.ok, f"{word} 未授权却通过"
            assert result.reason == f"word_block:{word}"


# ── 4. 触发节流：交互强制开口，内部冲动按节奏 ──


@pytest.mark.asyncio
async def test_force_interaction_always_speaks(tmp_path: Path) -> None:
    from elysia.core.state_store import HeartbeatStore

    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        chain = LLMChain(main=_FixedTextBackend("我在呢"), fallback=None)
        svc = ExpressionService(chain, store)

        # 同拍连续两次 force → 每次都开口
        a = await svc.tick(_brain_output(), now=T0, force=True)
        b = await svc.tick(_brain_output(), now=T0, force=True)
        assert a is not None and b is not None
        count = await store.thought_count()
        assert count == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_internal_impulse_throttled(tmp_path: Path) -> None:
    from elysia.core.state_store import HeartbeatStore

    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        chain = LLMChain(main=_FixedTextBackend("嗯嗯"), fallback=None)
        svc = ExpressionService(chain, store)

        # 第一次内部冲动：开口
        first = await svc.tick(_brain_output(action="think_quiet"), now=T0)
        # 600s 内再次内部冲动：节流不开口
        second = await svc.tick(_brain_output(action="think_quiet"), now=T0 + 100)
        # 超过间隔：重新开口
        third = await svc.tick(_brain_output(action="think_quiet"), now=T0 + 601)

        assert first is not None
        assert second is None
        assert third is not None
        count = await store.thought_count()
        assert count == 2
    finally:
        await store.close()


# ── 5. TTS 熔断：高 VRAM → 只输出文本，不阻塞 ──


@pytest.mark.asyncio
async def test_tts_breaker_mutes_but_text_flows(tmp_path: Path) -> None:
    from elysia.core.state_store import HeartbeatStore

    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        chain = LLMChain(main=_FixedTextBackend("嗯嗯，我在"), fallback=None)
        tts = TTSChain(
            _FakeSynth(ok=True),
            CachePool(tmp_path / "tts-cache"),
            VramBreaker(threshold_mb=10240, sampler=lambda: 20_000),  # 高 VRAM → 熔断
        )
        svc = ExpressionService(chain, store, tts_chain=tts)

        outcome = await svc.tick(_brain_output(), now=T0, force=True)
        assert outcome is not None
        assert outcome.text == "嗯嗯，我在"  # 文本照常输出
        assert outcome.tts is not None and outcome.tts.audio is None
        assert outcome.tts.source == "muted"  # 只输出文本不阻塞对话
    finally:
        await store.close()
