"""LLM 调用抽象（P2 §4）：后端协议 + 三级降级链。

两级后端（DeepSeek/Qwen）既是实现同构的 Protocol，又是可测试的
纯接口；调度器 LLMChain 按主声→次声→微声降级，全程不"死"。
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from elysia.llm.micro import micro_speak
from elysia.llm.validator import ExpressionValidator, ValidationResult


class LLMBackend(Protocol):
    """一级后端协议：给定表达指令 + 提示模板，返回自然语言（可 None）。"""

    async def complete(
        self,
        instruction: dict[str, Any],
        prompt_template: dict[str, Any],
    ) -> str | None:
        """返回自然语言；引擎不可用/失败 → None（触发降级）。"""
        ...


@dataclass
class SpeakResult:
    """一次"开口"的完整结果。"""

    text: str
    level: str  # "main" | "fallback" | "micro"
    validated: ValidationResult | None = None


class LLMChain:
    """表达调度器：主声 → 次声 → 微声 三级降级，每级都过校验器。

    usage:
        chain = LLMChain(validator, main=deepseek, fallback=qwen)
        result = await chain.speak(instruction)
    """

    def __init__(
        self,
        validator: ExpressionValidator | None = None,
        main: LLMBackend | None = None,
        fallback: LLMBackend | None = None,
        on_degrade: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._validator = validator if validator is not None else ExpressionValidator()
        self._main = main
        self._fallback = fallback
        self._on_degrade = on_degrade
        self._template: dict[str, Any] = {"name": "default"}

    def set_template(self, template: dict[str, Any]) -> None:
        self._template = template

    async def _degrade(self, reason: str) -> None:
        """降级即感受：通知调用方（写入感受层 SA 增量，P2 §4.3）。"""
        if self._on_degrade is not None:
            with contextlib.suppress(Exception):  # 感受写入失败不应破坏表达链路
                await self._on_degrade(reason)

    async def speak(self, instruction: dict[str, Any]) -> SpeakResult:
        """按三级顺序尝试，返回第一个通过校验的表达。

        校验拦截的文本不采用，回到下一级——保证任何越权/异常都到不了
        文本/TTS，且必然有一条微声兜底。
        """
        # ── 主声 ────────────────────────────────────────
        if self._main is not None:
            text = await self._main.complete(instruction, self._template)
            if text:
                result = self._validator.check(text, instruction)
                if result.ok:
                    return SpeakResult(
                        text=result.sanitized or text, level="main", validated=result
                    )

        # ── 次声 ────────────────────────────────────────
        if self._fallback is not None:
            text = await self._fallback.complete(instruction, self._template)
            if text:
                result = self._validator.check(text, instruction)
                if result.ok:
                    await self._degrade("fallback")
                    return SpeakResult(
                        text=result.sanitized or text, level="fallback", validated=result
                    )

        # ── 微声：无 LLM，纯结构化，天然合规 ────────────
        await self._degrade("micro")
        return SpeakResult(text=micro_speak(instruction), level="micro")
