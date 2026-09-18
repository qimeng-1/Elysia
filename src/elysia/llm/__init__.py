"""P2 表达生成子包：LLM 调用抽象、输出校验器、微声模板。

分工：
- validator.py: 输出校验器（T2 防线）
- micro.py: 微声模板（无 LLM 时的结构化呓语）
- chain.py: 三级降级调度器（主声→次声→微声）
- deepseek.py: 主声 DeepSeek API 后端
- words.py 位于 soul 层，供校验器消费词汇表
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from elysia.core.config import Settings
from elysia.llm.chain import LLMChain

__all__ = ["build_llm_chain", "chain", "deepseek", "micro", "validator"]


def build_llm_chain(
    settings: Settings,
    on_degrade: Callable[[str], Awaitable[None]] | None = None,
) -> LLMChain:
    """从配置构建表达调度器：主声 DeepSeek（有 key 时启用）。

    次声本地 Qwen 待 Step 4 接入（backend 未就绪时传 None 走降级）。
    VRAM 熔断阈值由调用方依据资源判断后决定是否挂载主声。
    """
    main = None
    if settings.llm_main_api_key:
        from elysia.llm.deepseek import DeepSeekBackend

        main = DeepSeekBackend(
            api_key=settings.llm_main_api_key,
            base=settings.llm_main_base,
            model=settings.llm_main_model,
            timeout_s=settings.llm_main_timeout_s,
        )
    return LLMChain(main=main, fallback=None, on_degrade=on_degrade)
