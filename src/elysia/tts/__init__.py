"""P2 TTS 子包：GPT-SoVITS 出声 + 缓存池 + 资源熔断。

分工：
- backend.py: GPT-SoVITS 后端（情绪→参考音频映射）
- cache.py: 磁盘缓存池（文本+情绪+语速 为 key）
- breaker.py: 资源熔断（VRAM 超阈值 → 只输出文本）
- chain.py: 出声调度器（缓存/合成/muted 三态）
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from elysia.core.config import Settings
from elysia.tts.chain import TTSChain, TTSRequest, TTSResult

__all__ = [
    "TTSChain",
    "TTSRequest",
    "TTSResult",
    "backend",
    "breaker",
    "build_tts_chain",
    "cache",
    "chain",
]


def build_tts_chain(
    settings: Settings,
    on_degrade: Callable[[str], Awaitable[None]] | None = None,
) -> TTSChain:
    """从配置构建出声调度器：GPT-SoVITS 后端 + 磁盘缓存 + VRAM 熔断。"""
    from elysia.tts.backend import TTSSynthesizer
    from elysia.tts.breaker import VramBreaker
    from elysia.tts.cache import CachePool

    synthesizer = TTSSynthesizer(
        settings.tts_gpt_sovits_url,
        text_lang=settings.tts_text_lang,
        prompt_lang=settings.tts_prompt_lang,
        timeout_s=settings.tts_timeout_s,
    )
    cache = CachePool(settings.tts_cache_dir)
    breaker = VramBreaker(threshold_mb=settings.tts_vram_threshold_mb)
    return TTSChain(synthesizer, cache, breaker, on_degrade=on_degrade)
