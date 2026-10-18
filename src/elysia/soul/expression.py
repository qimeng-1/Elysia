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

import re
from dataclasses import dataclass
from typing import Any

from elysia.soul.brain import BrainOutput, WillOutput

# 意图类别（对应提示模板入口）
INTENTS = frozenset(
    {
        "主动问候",
        "回应",
        "拒绝",
        "暂不回应",
        "思念",
        "好奇提问",
        "自检报告",
        "发呆呓语",
    }
)

# ── P0-R19：表达意愿层（"选择权"必须可解释，不是抛硬币）──────────────
# 收到你的话时她**总会开口**（b 方案）：要么回应，要么说明"现在不太想说话"。
# 回不回由下面这个确定性公式决定；每一分量都会落库（reason），可回放。
REPLY_THRESHOLD = 0.59
W_REL = 0.30  # 关系：退缩量越低越愿意开口
# P0-R19b：关系项**不许归零**——interval_factor=2（退缩到底）时旧式子给 0，
# 那等于把"关系最差"判成永久拒绝说话。保留 50% 底线：最差关系仍能开口，
# 只是更难（需要思念/聊天来补）。
REL_FLOOR = 0.5
W_CHAT = 0.30  # 想聊天（比思念弱：错过交流会更多地累积为思念）
W_MISS = 0.55  # 想她（P0-R16 已让 miss 分支可达；需求累积的主通道）
W_DIST = 0.55  # 难受时不想开口（distress 语义沿用既有，不改）
W_BOUND = 0.55  # 被当工具/被越界时可拒绝
RESTRAINT_INTENT = "暂不回应"
MAX_UTTERANCES_PER_TURN = 3  # 硬上限（多句靠拆句，不靠多次调用）
UTTERANCE_GAP_MIN_S = 2.0
UTTERANCE_GAP_MAX_S = 8.0


@dataclass(frozen=True)
class ReplyDecision:
    """一次"要不要回应"的决策（确定性、可解释、可回放）。"""

    reply_desire: float
    threshold: float
    speak: bool
    reason: str
    parts: dict[str, float]


def compute_reply_desire(
    *,
    chat: float,
    miss: float,
    interval_factor: float = 1.0,
    distress: bool = False,
    boundary_refused: bool = False,
    greeting: bool = False,
) -> ReplyDecision:
    """意愿公式（路线图 §7.3 的"选择权"）：分值越高越想开口。

    `interval_factor` 是关系退缩导出的间隔倍数（1.0 = 关系好，2.0 = 退缩到底）；
    `(2.0 - interval_factor)` 因此是"关系亲密"的 0-1 量。所有输入都来自她的真实
    状态，没有任何随机项 —— 同一输入 + 同一状态必定得到同一决策。
    """
    intimacy = max(0.0, min(1.0, 2.0 - min(2.0, max(1.0, float(interval_factor)))))
    parts = {
        "关系亲密": round(W_REL * (REL_FLOOR + (1.0 - REL_FLOOR) * intimacy), 4),
        "想聊天": round(W_CHAT * float(chat), 4),
        "想她": round(W_MISS * float(miss), 4),
        "难受": round(-W_DIST if distress else 0.0, 4),
        "被越界": round(-W_BOUND if boundary_refused else 0.0, 4),
    }
    desire = max(0.0, min(1.0, sum(parts.values())))
    speak = bool(greeting) or desire >= REPLY_THRESHOLD
    if greeting:
        reason = "问候：刚从不在场回到在场，值得一声"
    elif speak:
        reason = f"reply_desire={desire:.3f} ≥ {REPLY_THRESHOLD:.2f}，愿意开口"
    else:
        # 只有**负分量**才算"拖低"；全是正分量时指出最弱的一项（如实说清原因）
        negative = [name for name, value in parts.items() if value < 0]
        if negative:
            worst = negative
        else:
            real_parts = {
                name: value
                for name, value in parts.items()
                if name in ("关系亲密", "想聊天", "想她")
            }
            # 列出最弱的两项（一项往往不足以解释；具体分值随 parts 一起落库）
            weakest_two = [item[0] for item in sorted(real_parts.items(), key=lambda kv: kv[1])[:2]]
            labels = {
                "关系亲密": "关系退缩",
                "想聊天": "想聊天欲低",
                "想她": "思念不足",
            }
            worst = [labels.get(name, name) for name in weakest_two]
        reason = (
            f"reply_desire={desire:.3f} < {REPLY_THRESHOLD:.2f}；拖低它的分量：{'、'.join(worst)}"
        )
    return ReplyDecision(
        reply_desire=round(desire, 4),
        threshold=REPLY_THRESHOLD,
        speak=speak,
        reason=reason,
        parts=parts,
    )


# 句边界：句末标点/波浪号/kaomoji 结尾后断句（换行也算）
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?…～~♪♡])\s*|\n+")
# "实义字符"：CJK / 假名 / 拉丁字母 / 数字。标点、空白、kaomoji、emoji 都不算。
_MEANINGFUL_CHAR = re.compile(r"[\u4e00-\u9fff\u3040-\u30ffA-Za-z0-9]")
MIN_MEANINGFUL_CHARS = 2  # 去标点后少于 2 个实义字符的段，不单独成句


def _meaningful_len(text: str) -> int:
    return len(_MEANINGFUL_CHAR.findall(text))


def split_utterances(text: str) -> list[str]:
    """一次 LLM 回复 → 最多 `MAX_UTTERANCES_PER_TURN` 句。

    上限由安全门定，**下限由语义定**：只有标点/kaomoji 的碎片（`…`、`♪`）会把发言切成结巴，
    因此实义字符 < `MIN_MEANINGFUL_CHARS` 的段**并入前一段**（没有前一段则挂到下一段）；
    整句都不足时仍保留原文（"嗯。"也是她的一句话，不能吞掉）。
    """
    raw = [
        piece.strip() for piece in _SENTENCE_BOUNDARY.split(text or "") if piece and piece.strip()
    ]
    merged: list[str] = []
    prefix = ""
    for piece in raw:
        if _meaningful_len(piece) < MIN_MEANINGFUL_CHARS:
            if merged:
                merged[-1] += piece
            else:
                prefix += piece  # 开头就是碎片：挂到下一段，不丢
            continue
        merged.append(prefix + piece)
        prefix = ""
    if not merged:
        stripped = (text or "").strip()
        return [stripped] if stripped else []
    if prefix:
        merged[-1] += prefix
    return merged[:MAX_UTTERANCES_PER_TURN]


def utterance_gap_s(reply_desire: float = 1.0) -> float:
    """句间间隔（2–8s）：越不想开口，句子之间停得越久（不是固定值、不是一口气喷完）。"""
    desire = max(0.0, min(1.0, float(reply_desire)))
    return round(
        UTTERANCE_GAP_MIN_S + (UTTERANCE_GAP_MAX_S - UTTERANCE_GAP_MIN_S) * (1.0 - desire), 2
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
    # 阈值必须**严格小于**该维度的可达上界，否则分支恒不可达：
    # rest 原始值上界是 0.5（难受时），惯性平滑**收敛到恰好 0.5、永不越过**，
    # 而判定是严格大于 ⇒ 原写的 `> 0.5` 恒不成立（T1 取消测试已固化为不变量）。
    if emotion.get("rest", 0.0) > 0.45:
        e = "tired"
    elif emotion.get("miss", 0.0) > 0.4:
        e = "wistful"
    elif emotion.get("curiosity", 0.0) > 0.4:
        e = "curious"
    elif emotion.get("chat", 0.0) > 0.4:
        e = "happy"
    elif will.anim_bias < -0.15:
        # P0-R16：真实 anim_bias 区间约 -0.18..+0.21（旧阈值 -0.3 恒不成立，
        # "calm" 分支是死码）。-0.15 落在分布左侧尾部 ⇒ 只有真的收缩时才 calm。
        e = "calm"
    else:
        e = "neutral"
    return {"emotion": e, "speed": round(speed, 2)}
