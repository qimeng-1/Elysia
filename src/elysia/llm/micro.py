"""微声模板（P2 §4.2 第三级，无 LLM 时的结构化呓语）。

当主声（DeepSeek）/次声（本地 Qwen）都不可用时，微声把表达指令
直接序列化成一句简短的结构化呓语——保证"失语不失灵"。

微声只消费表达指令的结构化字段，不拼接任何用户原话，
符合 T2「表达必须消费状态」铁律。
"""

from __future__ import annotations

from typing import Any

# 意图 → 微声开头语气
_INTENT_OPENERS: dict[str, str] = {
    "主动问候": "唔，你回来啦",
    "回应": "嗯嗯",
    "拒绝": "不行哦",
    "思念": "有点想你了",
    "好奇提问": "我在想……",
    "自检报告": "我现在状态是",
    "发呆呓语": "……",
}


def _emoji_for(emotion_vector: dict[str, Any]) -> str:
    """按感受向量挑一句尾缀（轻量的情绪短语）。"""
    chat = float(emotion_vector.get("chat", 0.0))
    miss = float(emotion_vector.get("miss", 0.0))
    rest = float(emotion_vector.get("rest", 0.0))
    if miss > 0.4:
        return "，心口有点满"
    if rest > 0.5:
        return "，想再歇一会儿"
    if chat > 0.3:
        return "，陪你聊"
    return ""


def micro_speak(instruction: dict[str, Any]) -> str:
    """微声成句：把表达指令结构化成一句呓语（纯函数，可单测）。

    返回的句子片段不带用户原话、不承诺、不越界声明，天然通过校验。
    """
    intent = str(instruction.get("intent") or "发呆呓语")
    opener = _INTENT_OPENERS.get(intent, "……")
    emotion = instruction.get("emotion_vector", {}) or {}
    suffix = _emoji_for(emotion)
    return f"{opener}{suffix}。"
