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
    """从配置构建表达调度器：主声 DeepSeek → 次声（OpenAI 兼容端点）→ 微声。

    两者都是 `DeepSeekBackend`（同一协议、同一份身份段取数入口）——S6（D-S3）接线
    `llm_fallback_*` 后，"换后端她仍是她"由**数据**保证：人格住在身份段里，
    换的只是嗓门。次声用 `require_key=False`（本地 Ollama/vLLM 一类端点常无 key）。
    未配置（`llm_fallback_base` 为空）时传 `None`，与接线前**完全一致**。

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
    fallback = None
    if settings.llm_fallback_base:
        from elysia.llm.deepseek import DeepSeekBackend

        fallback = DeepSeekBackend(
            api_key=settings.llm_fallback_api_key,
            base=settings.llm_fallback_base,
            model=settings.llm_fallback_model,
            timeout_s=settings.llm_main_timeout_s,
            require_key=False,
        )
    return LLMChain(main=main, fallback=fallback, on_degrade=on_degrade)
