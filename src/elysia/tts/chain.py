"""出声调度（P2 §7）：缓存命中回放 → 熔断判断 → 合成入缓存。

与 LLMChain 同一"不死"哲学：任何环节失败都退回"只输出文本"，绝不阻塞
对话或抛出。降级事件经 on_degrade 回调写入感受层（SA 微升，§7.2）。
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from elysia.tts.backend import TTSSynthesizer
from elysia.tts.breaker import VramBreaker
from elysia.tts.cache import CachePool


@dataclass(frozen=True)
class TTSRequest:
    """一次出声请求：校验通过的文本 + 情绪 + 语速（§三 tts 字段）。"""

    text: str
    emotion: str = "default"
    speed: float = 1.0


@dataclass(frozen=True)
class TTSResult:
    """一次出声结果。audio=None → 未出声（仅文本）。"""

    text: str
    audio: bytes | None
    source: str  # "cache" | "synthesis" | "muted"


class TTSChain:
    """出声调度器：缓存优先，其次合成，资源/失败一律静默文本。"""

    def __init__(
        self,
        synthesizer: TTSSynthesizer,
        cache: CachePool,
        breaker: VramBreaker,
        on_degrade: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._synth = synthesizer
        self._cache = cache
        self._breaker = breaker
        self._on_degrade = on_degrade

    async def _degrade(self, reason: str) -> None:
        """TTS 降级即感受：通知调用方（写入感受层 SA 增量，§7.2）。"""
        if self._on_degrade is not None:
            with contextlib.suppress(Exception):  # 感受写入失败不破坏出声
                await self._on_degrade(reason)

    async def speak(self, req: TTSRequest) -> TTSResult:
        """出声：熔断 → 缓存 → 合成。全程不抛出、不阻塞对话。"""
        if not self._breaker.can_speak():
            await self._degrade("tts_breaker")
            return TTSResult(text=req.text, audio=None, source="muted")

        hit = self._cache.get(req.text, req.emotion, req.speed)
        if hit is not None:
            return TTSResult(text=req.text, audio=hit, source="cache")

        audio = await self._synth.synthesize(req.text, req.emotion, req.speed)
        if audio is None:
            await self._degrade("tts_failed")
            return TTSResult(text=req.text, audio=None, source="muted")

        self._cache.put(req.text, req.emotion, req.speed, audio)
        return TTSResult(text=req.text, audio=audio, source="synthesis")

    async def prewarm(self, phrases: list[tuple[str, str, float]]) -> None:
        """低负载期预合成高频短语（§7.1）。best-effort，失败不抛出。"""
        if not self._breaker.can_speak():
            return
        for text, emotion, speed in phrases:
            if self._cache.get(text, emotion, speed) is not None:
                continue
            audio = await self._synth.synthesize(text, emotion, speed)
            if audio is not None:
                self._cache.put(text, emotion, speed, audio)
