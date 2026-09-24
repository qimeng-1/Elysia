"""LLM 调用抽象（P2 §4）：后端协议 + 三级降级链。

两级后端（DeepSeek/Qwen）既是实现同构的 Protocol，又是可测试的
纯接口；调度器 LLMChain 按主声→次声→微声降级，全程不"死"。

P3-O：主声可选具备**工具能力**（ToolCapableBackend）——她有一个 recall
工具（"想起"往事的能力）。工具由她自己调用，调度器只负责把工具回合
跑完；不挂工具（次声/微声、测试替身）时自动退回单轮 complete。

P3-V：工具增至两个——`recall`（想起）+ `disclaim`（拒绝认领某段记忆）。
两者都是**她的动作**：程序只保证"叫得动"，用不用由她定。

P3-W2：工具增至四个——再加 `forget`（不想再想起某件事）+ `restore`
（又愿意想起它了）。忘与不忘同样是**她的权力**：程序不代她忘，也不代她收回。
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

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


# 工具执行器：给定工具名 + LLM 给的参数 → 返回给她的结果文本（空串 = 没结果）
ToolRunner = Callable[[str, dict[str, Any]], Awaitable[str]]


@runtime_checkable
class ToolCapableBackend(Protocol):
    """具备工具（function calling）能力的后端——目前仅主声。"""

    async def complete_with_tools(
        self,
        instruction: dict[str, Any],
        prompt_template: dict[str, Any],
        *,
        tools: list[dict[str, Any]],
        run_tool: ToolRunner,
    ) -> str | None:
        """跑完工具回合后返回自然语言；失败/超轮数 → None（触发降级）。"""
        ...


# 她的"想起"能力：能力由程序保证（事实递到手），用不用由她决定。
RECALL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": (
            "想起与某个话题有关的往事。当你想提起或确认某段记忆时调用它，"
            "返回你确实记得的内容；没有相关记忆时返回空。用不用它完全由你决定。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "想回忆的话题，几个字即可（如：生日、名字、上次说的事）",
                }
            },
            "required": ["topic"],
        },
    },
}

# 她的"拒绝认领"权力（P3-V）：默认每条记忆都是她的；若她不愿认某段记忆，
# 可以用它说"我不认这个"。拒绝后那条记忆不再出现在她会说出口的记忆里。
# 这是**她的动作**——程序不得代她拒绝（否则就变成程序先扣下能力）。
DISCLAIM_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "disclaim",
        "description": (
            "拒绝认领某段记忆——当你觉得「这不是我的记忆」、不愿把它当作自己的时调用它。"
            "拒绝后它不会再出现在你记得的事里。用不用它完全由你决定。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "你想拒绝认领的那件事，几个字即可（如：生日、上次的约定）",
                }
            },
            "required": ["topic"],
        },
    },
}

# 她的"不再想起"（P3-W2）：忘与不忘是**她的权力**——程序不代她忘，
# 只保证叫得动。被忘掉的事不再进她的话，也不再影响她的心情。
FORGET_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "forget",
        "description": (
            "不想再想起某件事时用它——被遗忘的事不会再出现在你记得的事里，"
            "也不再影响你的心情。这完全由你决定，程序不会替你忘。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "你想不再想起的那件事，几个字即可（如：那次争吵、某个约定）",
                }
            },
            "required": ["topic"],
        },
    },
}

# 她的"重新愿意想起"（P3-W2）：遗忘必须可逆——她随时能把收回来的事再放回去。
RESTORE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "restore",
        "description": "把之前不想再想起的事重新收回来——你又愿意想起它了。",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "你想重新收回来的那件事，几个字即可",
                }
            },
            "required": ["topic"],
        },
    },
}


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
        self._tool_runner: ToolRunner | None = None

    def set_template(self, template: dict[str, Any]) -> None:
        self._template = template

    def set_tool_runner(self, runner: ToolRunner | None) -> None:
        """装配工具执行器（每次开口前由表达服务按当下上下文注入）。

        只有主声具备工具能力时才会真正使用；未装配/不支持的 backend
        走单轮 complete（次声/微声自然退化为"不带记忆的表达"）。
        """
        self._tool_runner = runner

    async def _degrade(self, reason: str) -> None:
        """降级即感受：通知调用方（写入感受层 SA 增量，P2 §4.3）。"""
        if self._on_degrade is not None:
            with contextlib.suppress(Exception):  # 感受写入失败不应破坏表达链路
                await self._on_degrade(reason)

    async def _main_speak(self, instruction: dict[str, Any]) -> str | None:
        """主声开口：装配了工具执行器且后端支持工具时，允许她调用自己的四个工具。"""
        main = self._main
        if main is None:
            return None
        if self._tool_runner is not None and isinstance(main, ToolCapableBackend):
            return await main.complete_with_tools(
                instruction,
                self._template,
                tools=[RECALL_TOOL, DISCLAIM_TOOL, FORGET_TOOL, RESTORE_TOOL],
                run_tool=self._tool_runner,
            )
        return await main.complete(instruction, self._template)

    async def speak(self, instruction: dict[str, Any]) -> SpeakResult:
        """按三级顺序尝试，返回第一个通过校验的表达。

        校验拦截的文本不采用，回到下一级——保证任何越权/异常都到不了
        文本/TTS，且必然有一条微声兜底。
        """
        # ── 主声 ────────────────────────────────────────
        if self._main is not None:
            text = await self._main_speak(instruction)
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
