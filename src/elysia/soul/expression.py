"""表达指令 Schema（P2 决策层唯一出口，路线图 §7.3）。

表达-决策硬隔离（T2）的"正向半"：大脑循环（BrainLoop）不是直接输出自然语言，
而是输出一条结构化的**表达指令**，交给 LLM 翻译官 / 微声模板去说。

本模块只定义：
1. 表达指令的数据结构（ExpressionInstruction）
2. 从 BrainOutput + 运行环境快照 构造表达指令的纯函数（build_expression）

关键约束：
- 用户原话绝不进入表达指令；只作为"外部刺激"转成 feel_layer 事件
- LLM 只消费本指令，文字由 validator（llm/validator.py）代码级把关
"""

from __future__ import annotations

from typing import Any

from elysia.soul.brain import BrainOutput, WillOutput

# 意图类别（对应提示模板入口）
INTENTS = frozenset(
    {
        "主动问候",
        "回应",
        "拒绝",
        "思念",
        "好奇提问",
        "自检报告",
        "发呆呓语",
    }
)

# 感受维度默认集合（与 dimensions.FeelingState 保持一致）
FEELING_DIMS = ("chat", "miss", "explore", "curiosity", "rest", "self_check")

# brain_action → 默认意图（§3.2 映射表）
ACTION_TO_INTENT: dict[str, str] = {
    "think_active": "发呆呓语",
    "think_quiet": "发呆呓语",
    "animate": "自检报告",
    "none": "发呆呓语",
}


class ExpressionInstruction:
    """表达指令——决策层唯一出口，LLM/微声/校验器共用的契约。

    字段均为只读语义；构造后不应修改（由 validator 依据 constraints 做最终把关）。
    """

    def __init__(
        self,
        *,
        intent: str,
        emotion_vector: dict[str, float],
        state_brief: dict[str, float | str],
        lexical_permits: list[str],
        constraints: dict[str, Any],
        tts: dict[str, Any],
        memory_hooks: list[str] | None = None,
    ) -> None:
        self.intent = intent
        self.emotion_vector = dict(emotion_vector)
        self.state_brief = dict(state_brief)
        self.lexical_permits = list(lexical_permits)
        self.constraints = dict(constraints)
        self.tts = dict(tts)
        self.memory_hooks = list(memory_hooks if memory_hooks is not None else [])

    def to_dict(self) -> dict[str, Any]:
        """序列化为跨进程/落库的 JSON 结构。"""
        return {
            "version": 1,
            "intent": self.intent,
            "emotion_vector": self.emotion_vector,
            "state_brief": self.state_brief,
            "memory_hooks": self.memory_hooks,
            "lexical_permits": self.lexical_permits,
            "constraints": self.constraints,
            "tts": self.tts,
        }


def _default_constraints() -> dict[str, Any]:
    """表达指令的硬约束默认值（validator 依据于此）。"""
    return {
        "max_chars": 120,  # 最长作答字数
        "no_promise": True,  # 禁对未来行为作世界级承诺
        "no_claim_world": True,  # 禁声称具备系统外部能力
    }


def build_expression(
    output: BrainOutput,
    *,
    intent: str | None = None,
    vrram_mb: float = 0.0,
    body_left_h: float = 0.0,
    day_phase: str = "day",
    max_chars: int = 120,
) -> ExpressionInstruction:
    """从大脑循环输出构造表达指令（纯函数）。

    状态 → 表达指令的映射：
    - intent：优先用显式传入的意图；否则按 brain_action 映射（§3.2）
    - emotion_vector：6 维感受透传
    - state_brief：TR/CS/SA + VRAM + 身体离开小时数 + 昼夜相位
    - lexical_permits：由 validator 依据真实状态注入（本函数留空，
      校验器从 words.check 汇总可得）
    """
    will: WillOutput = output.will
    fallback_intent = ACTION_TO_INTENT.get(output.action, "发呆呓语")
    chosen_intent = intent if intent is not None else fallback_intent

    emotion = output.feelings.to_dict()
    state_brief: dict[str, float | str] = {
        "tr": round(output.desire.tr, 1),
        "cs": round(output.desire.cs, 1),
        "sa": round(output.desire.sa, 1),
        "vrram_mb": round(vrram_mb),
        "body_left_h": round(body_left_h, 1),
        "day_phase": day_phase,
    }

    # 词汇许可：由 validator 依据消耗的真实状态自动注入（此处默认空
    # 表示"未授权任何生理/情感词"，LLM 越界即被拦截）
    constraints = _default_constraints()
    constraints["max_chars"] = max_chars

    return ExpressionInstruction(
        intent=chosen_intent,
        emotion_vector=emotion,
        state_brief=state_brief,
        lexical_permits=[],
        constraints=constraints,
        tts=_tts_params(will, emotion),
    )


def _tts_params(will: WillOutput, emotion: dict[str, float]) -> dict[str, Any]:
    """意志 + 感受 → TTS 语音参数（§7.1 情绪调制）。"""
    # 语速：TR 高→稍快，SA 高→稍慢（收缩/舒展 anim_bias 同向）
    speed = 1.0 + will.anim_bias * 0.2
    # 情绪基调：默认中性，感受映射到 GPT-SoVITS 可用标签
    if emotion.get("rest", 0.0) > 0.5:
        e = "tired"
    elif emotion.get("miss", 0.0) > 0.4:
        e = "wistful"
    elif emotion.get("curiosity", 0.0) > 0.4:
        e = "curious"
    elif emotion.get("chat", 0.0) > 0.4:
        e = "happy"
    elif will.anim_bias < -0.3:
        e = "calm"
    else:
        e = "neutral"
    return {"emotion": e, "speed": round(speed, 2)}
