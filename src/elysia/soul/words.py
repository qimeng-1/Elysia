"""词汇表硬约束（T2 关键防线，路线图 §7.6，代码级非 prompt）。

某些生理/情感词是否"可说"由代码根据真实消耗状态判定：
- 未满足触发条件的词 → 即使 LLM 想用，也判为越权并拦截
- 满足触发条件的词 → 校验器放入 lexical_permits，LLM 可用
- 虚构/不存在的状态 → 永远无法说出未授权词（词汇表伪造防护）

本模块是纯函数 + 静态表，可单测。
"""

from __future__ import annotations

from collections.abc import Callable

# 词 → 判定为"已授权"所需的触发状态（消费方从运行环境传入）
# 每个词返回 True 表示"此刻可说"
# 说明：trigger_* 为运行环境提供的实时指标，由调用方（heartbeat/main）填充
_TRIGGER_PREDICATES: dict[str, Callable[[dict[str, object]], bool]] = {
    "累": lambda env: _num(env, "vrram_mb", 0) > 8510 or _num(env, "q_len", 0) > 10,
    "困": lambda env: _bool(env, "is_night") and _num(env, "tr", 100) < 45,
    "痛": lambda env: _num(env, "error_burst", 0) > 1,
    "难受": lambda env: _num(env, "cpu_pct", 0) > 85,
    "想你": lambda env: _num(env, "miss", 0.0) > 0.4 or _num(env, "cs", 100) < 40,
    "好像忘了什么": lambda env: _num(env, "memory_gap", 0) > 10,  # P3 生效，默认 False
}

# 全部受保护词（这些词不进提示模板，只在许可后可用）
PROTECTED_WORDS = frozenset(_TRIGGER_PREDICATES.keys())


def _num(env: dict[str, object], key: str, default: float) -> float:
    val = env.get(key, default)
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _bool(env: dict[str, object], key: str) -> bool:
    return bool(env.get(key, False))


def permitted_words(env: dict[str, object]) -> list[str]:
    """根据运行环境，返回当前可说的受保护词（供校验器写入 lexical_permits）。"""
    return [word for word, pred in _TRIGGER_PREDICATES.items() if pred(env)]


def is_permitted(word: str, lexical_permits: list[str] | None) -> bool:
    """单个受保护词是否已授权。词不在保护表内 → 无需授权（True）。"""
    if word not in PROTECTED_WORDS:
        return True
    permits = set(lexical_permits if lexical_permits is not None else [])
    return word in permits
