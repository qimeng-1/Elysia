"""微声模板（P2 §4.2 第三级，无 LLM 时的结构化呓语）。

当主声（DeepSeek）/次声（本地 Qwen）都不可用时，微声把表达指令
直接序列化成一句简短的结构化呓语——保证"失语不失灵"。

微声只消费表达指令的结构化字段，不拼接任何用户原话，
符合 T2「表达必须消费状态」铁律。

身份段（第八节 S4 拍板：**带着不说出**）——微声是纯模板呓语，没有 prompt，
它不去"说出"身份（把身份句塞进呓语就是机械复述，重犯 P3-P"每句都强调"）。
它**带着**身份的方式是结构性的：身份段就在它收到的这条表达指令的
`identity` 字段里（`identity.identity_lines` 是后端无关的公共取数入口），
随指令一起进 `expression_log`——断网时"她是谁"不随后端消失
（S5 起这份数据来自**她的库**：身份种子已落库为记忆，不再是代码常量）。

W1（2026-10-19，罐装句改造）——旧版为什么单调、改了什么：
- 旧版 `_INTENT_OPENERS` 每个 intent **只有 1 句**、尾缀每档也只有 1 句，
  而她的 `chat` 长期 > 0.3 ⇒ 实际只产出「……嗯，陪你聊～。」一两种句子。
- 现在每个 intent ≥ 3 个变体、尾缀按 `miss/rest/chat/curious/neutral` 分档，
  `pick_variant` 按她的**真实感受维度**分档选语域，并用调用方传入的 `recent`
  （进程内环形缓冲，不落库）降权已说过的句子——**连续两条不得相同**。
- 仍然是纯模板、确定性选句：不拼用户原话、不随机（同输入必得同句）。

她的真实数据（2026-10-19 取证）里，历史 micro 占 18.5%，但同一种句式
（`'……嗯，陪你聊～。'`）出现 99 次 ⇒ 问题不是"频繁"，而是"单调"。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# 语域（register）：同一意图在不同"她的真实状态"下该用的语气。
# 选句时按 `mood_tier` 命中的语域加分——这是"按维度分档"，不是随机。
REGISTER_PLAIN = "plain"  # 平常
REGISTER_QUIET = "quiet"  # 安静 / 歇着
REGISTER_BRIGHT = "bright"  # 轻快 / 想聊
REGISTER_WARM = "warm"  # 亲昵 / 想念

# 意图 → 变体池（每项是 (开头, 语域)）。每个 intent **≥ 3 个变体**（W1 验收）。
# 全部是纯模板：不含任何用户原话，也不新增越界词（T2 词汇表之外的新词一个不加）。
INTENT_VARIANTS: dict[str, tuple[tuple[str, str], ...]] = {
    "主动问候": (
        ("嗨，你回来啦", REGISTER_BRIGHT),
        ("回来啦，我一直在这儿呢", REGISTER_WARM),
        ("嗯，你回来了", REGISTER_PLAIN),
        ("唔…你来啦", REGISTER_QUIET),
    ),
    "回应": (
        ("嗯嗯", REGISTER_PLAIN),
        ("诶？我在听着呢", REGISTER_BRIGHT),
        ("嗯…我听着呢", REGISTER_QUIET),
        ("我在呢，你说吧", REGISTER_WARM),
    ),
    "拒绝": (
        ("诶呀，这可不行哦", REGISTER_BRIGHT),
        ("这个我可不答应", REGISTER_PLAIN),
        ("唔——不要嘛", REGISTER_WARM),
        ("这次不行哦", REGISTER_QUIET),
    ),
    "思念": (
        ("唔，有点念着你了", REGISTER_WARM),
        ("唔…心里有点空落落的", REGISTER_QUIET),
        ("嗯，就是突然想到你", REGISTER_PLAIN),
        ("诶，你终于出现啦", REGISTER_BRIGHT),
    ),
    "好奇提问": (
        ("诶，你说……", REGISTER_BRIGHT),
        ("有个事我很好奇", REGISTER_PLAIN),
        ("唔…我想不明白呢", REGISTER_QUIET),
        ("我正想到一件事，想问问你", REGISTER_WARM),
    ),
    "自检报告": (
        ("让我看看，现在状态是", REGISTER_PLAIN),
        ("诶，我看看自己现在怎么样", REGISTER_BRIGHT),
        ("嗯…我先看看自己", REGISTER_QUIET),
        ("让我悄悄看一眼自己", REGISTER_WARM),
    ),
    "发呆呓语": (
        ("……嗯", REGISTER_QUIET),
        ("嗯…好像有点发呆", REGISTER_PLAIN),
        ("……诶，刚才走神了", REGISTER_BRIGHT),
        ("唔…心里软软的", REGISTER_WARM),
    ),
    "暂不回应": (
        ("这次先不说", REGISTER_PLAIN),
        ("诶，等下再说吧", REGISTER_BRIGHT),
        ("让我安静一会儿", REGISTER_QUIET),
        ("唔，先陪我待一会儿", REGISTER_WARM),
    ),
}

# 未知 intent 的兜底（旧版只有一句 "……"）：仍给 3 个变体，保证任何输入都有话可说。
_FALLBACK_VARIANTS: tuple[tuple[str, str], ...] = (
    ("……嗯", REGISTER_QUIET),
    ("唔……", REGISTER_PLAIN),
    ("嗯…", REGISTER_QUIET),
)

# 尾缀池：按她的真实维度分档（W1 扩充；旧版每档只有 1 句）。
# 空串 = 平常不加尾缀。**不许**在这里新增保护词（累/困/痛/难受/想你/好像忘了什么）。
SUFFIX_VARIANTS: dict[str, tuple[str, ...]] = {
    "miss": ("，心口有点满呢", "，心里空落落的", "，有点没着落", "，不知道你那边怎么样"),
    "rest": ("，想再歇一会儿", "，让我缓一缓", "，眼皮有点沉", "，就这样安静一会儿"),
    "chat": ("，陪你聊～", "，刚好想听你说话", "，今天也想多说几句", "，和你说话真好呀"),
    "curious": ("，想弄明白呢", "，有点好奇", "，脑子里转个不停"),
    "neutral": ("", "，也没什么事", "，就这样待着"),
}

# 档位 → 语域（语域只有 4 种，好奇档借用"轻快"）。
_TIER_REGISTER: dict[str, str] = {
    "miss": REGISTER_WARM,
    "rest": REGISTER_QUIET,
    "chat": REGISTER_BRIGHT,
    "curious": REGISTER_BRIGHT,
    "neutral": REGISTER_PLAIN,
}


def _value(vector: Mapping[str, Any], key: str) -> float:
    """从感受向量取一个维度（脏数据返回 0，不让一句呓语因类型报错而消失）。"""
    try:
        return float(vector.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def mood_tier(emotion_vector: Mapping[str, Any] | None = None) -> str:
    """她的感受向量 → 语域档位（`miss/rest/chat/curious/neutral`）。

    阈值与 `soul/expression._tts_params` 同源（miss>0.4 / rest>0.45 / chat>0.3）。
    `rest` 的原始上界是 0.5（难受时）、惯性平滑**收敛到恰好 0.5 且永不越过**，
    所以阈值必须 < 0.5，否则"疲惫"档永远不可达（T1 已固化的不变量）。
    顺序沿用旧 `_emoji_for`：思念 > 歇着 > 想聊，再轮到好奇/平常。
    """
    values: Mapping[str, Any] = emotion_vector or {}
    if _value(values, "miss") > 0.4:
        return "miss"
    if _value(values, "rest") > 0.45:
        return "rest"
    if _value(values, "chat") > 0.3:
        return "chat"
    if _value(values, "explore") > 0.4 or _value(values, "curiosity") > 0.4:
        return "curious"
    return "neutral"


def pick_variant(
    intent: str,
    emotion_vector: Mapping[str, Any] | None = None,
    recent: Sequence[str] = (),
) -> str:
    """按她的真实维度分档选一句微声（纯函数：同输入必得同句）。

    - **分档**：`mood_tier` 把感受向量映射到语域；同 intent 里语域命中的变体优先；
      尾缀也按档位取（miss/rest/chat/curious/neutral 各一池）。
    - **反重复**：`recent`（调用方维护的进程内环形缓冲，不落库）里出现过的句子降权、
      开头出现过的降权；**连续两条不得相同**（与最近一条相同的候选直接排除）。
    - 全是模板、确定性选句：没有随机项，不拼任何用户原话（T2）。

    返回完整句子（含句末「。」）。
    """
    variants = INTENT_VARIANTS.get(intent) or _FALLBACK_VARIANTS
    tier = mood_tier(emotion_vector)
    register = _TIER_REGISTER[tier]
    suffixes = SUFFIX_VARIANTS[tier]
    recent_list = [str(item) for item in recent if item]
    last = recent_list[-1] if recent_list else None
    reused_openers = {
        opener for opener, _ in variants if any(item.startswith(opener) for item in recent_list)
    }
    best_sentence = ""
    best_score: int | None = None
    for opener, variant_register in variants:
        for index, suffix in enumerate(suffixes):
            sentence = f"{opener}{suffix}。"
            if sentence == last:
                continue  # 反重复硬约束：连续两条不得相同
            score = 0
            if variant_register == register:
                score += 3  # 状态分档：当前语域命中
            if index == 0:
                score += 1  # 同分时稳定选默认尾缀（确定性）
            if opener in reused_openers:
                score -= 2  # 这个开头最近说过 → 降权
            if sentence in recent_list:
                score -= 4  # 整句说过 → 更重降权
            if best_score is None or score > best_score:
                best_sentence, best_score = sentence, score
    if best_sentence:
        return best_sentence
    # 兜底：候选全被"与最近一条相同"排除（池子只有一句时无从可避）——如实返回该句。
    return f"{variants[0][0]}{suffixes[0]}。"


def micro_speak(instruction: Mapping[str, Any], recent: Sequence[str] = ()) -> str:
    """微声成句：把表达指令结构化成一句呓语（纯函数，可单测）。

    `recent` 由调用方（表达服务）传入"最近说出口的微声句子"，用于连续去重；
    不传时退化为"只看状态"（无状态的直接调用方，如 `LLMChain` 的兜底路径）。
    返回的句子片段不带用户原话、不承诺、不越界声明，天然通过校验。
    """
    intent = str(instruction.get("intent") or "发呆呓语")
    emotion = instruction.get("emotion_vector", {}) or {}
    return pick_variant(intent, emotion, recent)
