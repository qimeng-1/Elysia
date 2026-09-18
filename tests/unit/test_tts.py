"""TTS 层单测（P2 第 4 步）：缓存命中/未命中、合成失败、熔断降级。

不依赖真实 GPT-SoVITS：合成用 fake 后端、熔断用可注入采样器、缓存用临时目录，
保证在 CI 无 GPU/无服务时全绿。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elysia.tts.breaker import VramBreaker
from elysia.tts.cache import CachePool
from elysia.tts.chain import TTSChain, TTSRequest


class _FakeSynth:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []
        self.result: bytes | None = b"\x00\x01RIFF-wav-fake"

    async def synthesize(self, text: str, emotion: str, speed: float) -> bytes | None:
        self.calls.append((text, emotion, speed))
        return self.result


def _breaker(used_mb: int | None) -> VramBreaker:
    return VramBreaker(threshold_mb=10240, sampler=lambda: used_mb)


def _chain(
    tmp_path: Path,
    synth: _FakeSynth,
    breaker: VramBreaker,
    degrade: list[str] | None = None,
) -> TTSChain:
    cache = CachePool(tmp_path / "tts")
    events = [] if degrade is None else degrade

    async def on_degrade(reason: str) -> None:
        events.append(reason)

    return TTSChain(synth, cache, breaker, on_degrade=on_degrade or None)


# ── 缓存命中 / 未命中 ────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_hit_does_not_call_synth(tmp_path: Path) -> None:
    synth = _FakeSynth()
    chain = _chain(tmp_path, synth, _breaker(1000))
    req = TTSRequest(text="我在", emotion="default", speed=1.0)

    first = await chain.speak(req)
    second = await chain.speak(req)

    assert first.source == "synthesis"
    assert second.source == "cache"
    assert second.audio == first.audio
    assert len(synth.calls) == 1  # 第二次命中缓存，不再合成


@pytest.mark.asyncio
async def test_cache_key_differs_by_speed_and_emotion(tmp_path: Path) -> None:
    synth = _FakeSynth()
    chain = _chain(tmp_path, synth, _breaker(1000))

    await chain.speak(TTSRequest(text="嗯嗯", emotion="default", speed=1.0))
    await chain.speak(TTSRequest(text="嗯嗯", emotion="default", speed=1.5))
    await chain.speak(TTSRequest(text="嗯嗯", emotion="playful", speed=1.0))

    assert len(synth.calls) == 3  # 情绪或语速不同 → 独立缓存条目


# ── 熔断：只输出文本，不合成 ──────────────────────────────


@pytest.mark.asyncio
async def test_breaker_high_vram_returns_muted_no_synth(tmp_path: Path) -> None:
    degrade: list[str] = []
    synth = _FakeSynth()
    chain = _chain(tmp_path, synth, _breaker(99999), degrade)
    req = TTSRequest(text="我在")

    result = await chain.speak(req)

    assert result.source == "muted"
    assert result.audio is None
    assert synth.calls == []
    assert degrade == ["tts_breaker"]


@pytest.mark.asyncio
async def test_breaker_unknown_vram_allows_synthesis(tmp_path: Path) -> None:
    synth = _FakeSynth()
    chain = _chain(tmp_path, synth, _breaker(None))
    result = await chain.speak(TTSRequest(text="我在"))
    assert result.source == "synthesis"


# ── 合成失败 → 静默仅文本 ────────────────────────────────


@pytest.mark.asyncio
async def test_synth_failure_returns_muted_no_block(tmp_path: Path) -> None:
    degrade: list[str] = []
    synth = _FakeSynth()
    synth.result = None  # 模拟 GPT-SoVITS 不可用
    chain = _chain(tmp_path, synth, _breaker(1000), degrade)

    result = await chain.speak(TTSRequest(text="好呀"))

    assert result.source == "muted"
    assert result.audio is None
    assert degrade == ["tts_failed"]


# ── 降级回调异常被吞掉，不破坏出声 ────────────────────────


@pytest.mark.asyncio
async def test_degrade_exception_is_swallowed(tmp_path: Path) -> None:
    synth = _FakeSynth()
    synth.result = None
    cache = CachePool(tmp_path / "tts")

    async def boom(reason: str) -> None:
        raise RuntimeError(reason)

    chain = TTSChain(synth, cache, _breaker(99999), on_degrade=boom)
    result = await chain.speak(TTSRequest(text="嗯嗯"))
    assert result.source == "muted"


# ── 低负载预合成 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_prewarm_populates_cache_best_effort(tmp_path: Path) -> None:
    synth = _FakeSynth()
    chain = _chain(tmp_path, synth, _breaker(1000))

    await chain.prewarm([("我在", "default", 1.0), ("好呀", "default", 1.0)])

    assert len(synth.calls) == 2
    # 预合成后直接命中缓存
    result = await chain.speak(TTSRequest(text="我在"))
    assert result.source == "cache"


@pytest.mark.asyncio
async def test_prewarm_skipped_when_breaker_engaged(tmp_path: Path) -> None:
    synth = _FakeSynth()
    chain = _chain(tmp_path, synth, _breaker(99999))

    await chain.prewarm([("我在", "default", 1.0)])

    assert synth.calls == []
