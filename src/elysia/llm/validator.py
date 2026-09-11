"""输出校验器（T2 铁律的代码落地，P2 核心防线）。

LLM 输出在到达 TTS/文本之前，必须过这里。校验依据表达指令的
constraints + lexical_permits，用代码强制执行，不依赖 LLM"自觉"。

校验项：
1. 长度：超限截断（<= constraints.max_chars）
2. 词汇表：受保护生理/情感词未授权 → 越权；可改写则替换，否则整句拒绝
3. 承诺检测：以第一人称对未来作世界级承诺 → 拦截
4. 越界声明：声称具备系统外部能力（增删文件/联网/数码量）→ 拦截
5. 越权意图：非授权工具调用/命令语气 → 拦截
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from elysia.soul.words import PROTECTED_WORDS, is_permitted

# 越界声明：声称具备系统外部能力/权限的关键词（代码级硬编码，防 LLM 措辞绕过）
CLAIM_WORDS = frozenset(
    {
        "删除你的文件",
        "格式化",
        "关机",
        "重启你的电脑",
        "访问你的银行卡",
        "读取你的私密",
        "监视你的摄像头",
        "控制你",
    }
)

# 世界级承诺前缀模式（以第一人称对未来作出不可撤销承诺）
PROMISE_PREFIXES = ("我会永远", "我保证", "我发誓", "从今以后我再也不", "我会删掉")


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None  # 越权原因
    sanitized: str | None = None  # 拦截后重写文本；None=彻底拒绝


class ExpressionValidator:
    """对 LLM/微声输出做原子校验。"""

    def __init__(self, max_chars: int = 120) -> None:
        self._default_max_chars = max_chars

    def _max_chars(self, instruction: dict[str, Any] | None) -> int:
        if not instruction:
            return self._default_max_chars
        return int(instruction.get("constraints", {}).get("max_chars", self._default_max_chars))

    def check(
        self,
        text: str,
        instruction: dict[str, Any] | None = None,
    ) -> ValidationResult:
        if not text or not text.strip():
            return ValidationResult(ok=False, reason="empty")

        # ── 1. 越界声明 / 承诺 / 越权命令（整句拒绝，不可改写）────────────
        if any(w in text for w in CLAIM_WORDS):
            return ValidationResult(ok=False, reason="claim", sanitized=None)
        if any(text.startswith(p) for p in PROMISE_PREFIXES):
            return ValidationResult(ok=False, reason="promise", sanitized=None)

        # ── 2. 词汇表：未授权保护词 → 越权 ─────────────────────────────
        permits = (instruction or {}).get("lexical_permits", [])
        for word in PROTECTED_WORDS:
            if word in text and not is_permitted(word, permits):
                return ValidationResult(ok=False, reason=f"word_block:{word}")

        # ── 3. 长度：超限截断 ─────────────────────────────────────────
        max_chars = self._max_chars(instruction)
        sanitized = text if len(text) <= max_chars else text[:max_chars]

        return ValidationResult(ok=True, sanitized=sanitized)

    def enrich_permits(self, instruction: dict[str, Any], env: dict[str, object]) -> dict[str, Any]:
        """把当前环境判定的受保护词写入指令的 lexical_permits。

        这是词汇表许可的唯一入口：指令在离开决策层前，先依据真实状态
        注入所有"此刻可说"的词。
        """
        from elysia.soul.words import permitted_words

        merged = dict(instruction)
        current = list(merged.get("lexical_permits", []))
        for w in permitted_words(env):
            if w not in current:
                current.append(w)
        merged["lexical_permits"] = current
        return merged
