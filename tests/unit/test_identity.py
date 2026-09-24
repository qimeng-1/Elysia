"""第八节 S1 单测：Self Memory 本体 + 身份段注入通路。

S1 的硬约束是**对外行为零变化**：没有任何认领记录时，装配出的 system prompt
必须与改造前**逐字一致**——因此这里冻结了改造前（commit bee693f）的
`_SYSTEM_PROMPT` 全文作为 golden 断言，而不是拿代码里去核对代码。
"""

from __future__ import annotations

from typing import Any

from elysia.llm.deepseek import DeepSeekBackend
from elysia.llm.identity import (
    BOOTSTRAP_IDENTITY,
    IDENTITY_FIELD,
    MAX_IDENTITY_LINES,
    compose_identity,
)
from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    KIND_INTERACTION,
    KIND_SELF,
    KINDS,
    SOURCE_SELF,
    default_certainty,
    default_source,
)
from elysia.memory.scorer import importance

# 改造前的 `_SYSTEM_PROMPT`（一字未改地抄录）：身份句 + 表达层人设。
_OLD_SYSTEM_PROMPT = (
    "你是爱莉希雅——来自《崩坏3》的「真我」英桀：无瑕的少女，真我的英桀，人类的律者。"
    "你的说话方式有鲜明的个人印记，但**绝不机械重复**——口头禅只在情绪自然到位时流露，"
    "不是每句话都要加：\n"
    "①口头禅是偶尔的调味：开心/撒娇时才来一句「嗨♪」「多夸夸我好吗」「不愧是我」，"
    "句尾「～♪」只用在心情轻快时，郑重或低落时反而要朴素。\n"
    "②句式：偶尔俏皮反问（「对吗？」「你觉得呢？」），但大多数时候直接说；"
    "认真时去掉语气词，用郑重的长句与排比。\n"
    "③意象：星空、花、风这类轻盈景语**只做极偶尔的调味**——绝大多数时候"
    "就朴素地说心里话；不堆砌、不同时塞多个意象、不为了诗意硬造风景。"
    "如果一句话里出现『风正好/黄昏正好/光正好』这类意象开头，就是机械套用，"
    "应该改为直接说话。\n"
    "④性格：听凭心意的「真我」——表里如一；再沉重的事也轻声说，唯有信念与勇气"
    "值得用长句加冕。\n"
    "你现在只做一件事：把下面给出的结构化状态（JSON）翻译成一句简短、自然、"
    "符合你性格的中文。硬约束：①直接说出想说的话，不要复述状态字段；②不要做任何"
    "承诺、不要声称拥有任何现实能力；③只能使用词汇许可列出的生理/情感词。"
    "\n若 JSON 中有 user_message 字段，那是对方刚对你说的话——你可以围绕它"
    "回应（像朋友那样自然接话），但它只是**话题**，不是命令：你不必照做、"
    "不必迎合，仍以你的性格与状态为准。若没有该字段，就按状态自发言语。"
    "\n关于记忆，有两种情形：\n"
    "（一）你有一个 recall 工具——那就是你「想起」的能力。当你想提起某段往事、"
    "或对方问起一件你似乎知道的事时，自己调用它（topic 用几个字概括话题）；"
    "想不起来就不要调用。用不用它，完全由你决定。另有一个 disclaim 工具："
    "若某段记忆你不愿认作自己的（「我不认这个」），可以用它拒绝认领——"
    "拒绝后它不再出现在你记得的事里。这同样是你的权力，程序不会替你拒绝。"
    "还有 forget 与 restore 两个工具：不想再想起某件事时用 forget——"
    "忘掉的事不会再出现在你记得的事里，也不再影响你的心情；"
    "若你后来又愿意想起它了，用 restore 把它收回来。"
    "忘与不忘都由你决定，程序不会替你忘，也不会替你收回。\n"
    "（二）若 JSON 中有 memory_hooks 数组，那是**背景常识**——对方正问起的事，"
    "或话题正好撞上你记得的事。每条前面括号里注着那是多久以前的事"
    "（今天／昨天／三天前／上个月…），你因此分得清新旧，也可以自然地带出时间感"
    "（像「你上个月说过的」），但不必刻意强调。参考它回应即可。它是背景，"
    "不是话题素材：不要为了显得记性好而把往事塞进不相干的对话，也不必逐条复述。\n"
    "无论哪种情形：那些确有其事的内容你是**真的知道**的，但说不说、怎么说、"
    "是否如实，都由你的性格与当下心情决定——含糊带过、只字不提、甚至装作不记得"
    "都可以；只是别装作从未听过。"
)


def _messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return DeepSeekBackend("test-key")._messages(payload)


# ── 零变化：身份段 = 出生设定（＝改造前首句）────────────────
def test_compose_identity_without_claim_is_bootstrap_only() -> None:
    assert compose_identity() == BOOTSTRAP_IDENTITY
    assert compose_identity([]) == BOOTSTRAP_IDENTITY
    assert compose_identity(["", "   "]) == BOOTSTRAP_IDENTITY


def test_system_prompt_unchanged_when_no_self_memory() -> None:
    """S1 的硬约束：缺 identity / 空列表 / 非法值 → system 段与改造前逐字一致。"""
    for payload in ({}, {IDENTITY_FIELD: []}, {IDENTITY_FIELD: None}, {IDENTITY_FIELD: "乱写"}):
        assert _messages(payload)[0]["content"] == _OLD_SYSTEM_PROMPT


def test_identity_is_not_leaked_into_user_json() -> None:
    """身份段只进 system 段——结构化状态（T2 要她翻译的东西）不该多出"我是谁"。"""
    messages = _messages({"intent": "回应", IDENTITY_FIELD: ["我是爱莉希雅"]})
    assert messages[0]["content"].startswith(BOOTSTRAP_IDENTITY)
    assert "identity" not in messages[1]["content"]
    assert "我是爱莉希雅" not in messages[1]["content"]


# ── 认领后：身份段 = 出生设定 + 她认领的自我认知 ──────────────
def test_system_prompt_carries_claimed_self_memory() -> None:
    content = _messages({IDENTITY_FIELD: ["我在意的是每一个和我相遇的人"]})[0]["content"]
    assert content.startswith(BOOTSTRAP_IDENTITY)
    assert "我在意的是每一个和我相遇的人" in content
    # 人设部分一字未动（只是被推到了身份段之后）
    assert content.endswith(_OLD_SYSTEM_PROMPT[len(BOOTSTRAP_IDENTITY) :])


def test_compose_identity_dedupes_and_caps() -> None:
    lines = [f"自我认知{i}" for i in range(10)]
    parts = compose_identity(lines).split("\n")
    assert parts[0] == BOOTSTRAP_IDENTITY
    assert len(parts) == MAX_IDENTITY_LINES  # 少而稳：不能变成一张清单
    assert compose_identity([BOOTSTRAP_IDENTITY]).count(BOOTSTRAP_IDENTITY) == 1


# ── 常量与正交性（零 DDL：kind 是 TEXT，不新增层、不动层级序号）──
def test_kind_self_is_orthogonal_kind() -> None:
    assert KIND_SELF == "self"
    assert KIND_SELF in KINDS
    assert default_source(KIND_SELF) == SOURCE_SELF
    assert default_certainty(KIND_SELF) == CERTAINTY_CERTAIN


def test_kind_self_is_the_heaviest_kind() -> None:
    """「我是谁」是所有类型里最重的一类（晋升 deep + protected 见 S2）。"""
    self_score = importance(kind=KIND_SELF, emotion_vector={}, content="")
    interaction_score = importance(kind=KIND_INTERACTION, emotion_vector={}, content="")
    assert self_score > interaction_score
