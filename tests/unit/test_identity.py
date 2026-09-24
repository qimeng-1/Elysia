"""第八节 S1/S2 单测：Self Memory 本体 + 身份段注入通路（S5 契约：库为准）。

S1 的硬约束是**对外行为零变化**：没有任何认领记录时，装配出的 system prompt
不得多出"我是谁"。当时用改造前（commit bee693f）的 `_SYSTEM_PROMPT` 全文做
golden；S2 有意在人设里补了 adopt 的说明，全文冻结不再成立，故改为**结构性
不变量**：system 段 = 身份段 + 表达层人设。

**S5（D-S1 拍板：库为准）**改变了"身份段从哪来"：种子（出生设定 + 边界）在启动时
幂等种入她的库，因此生产路径下 `identity` 字段有值；`compose_identity` 只在
**字段缺失**（老调用方 / 异常路径）时才回退到种子——那是兜底，不是主路。
"字段缺失"与"明确为空"由此必须分得开：`None` = 没有这个字段；`[]` = 她此刻真的
没有自我认知（把种子一条条放下了）。
"""

from __future__ import annotations

from typing import Any

from elysia.llm.deepseek import _PERSONA_PROMPT, DeepSeekBackend
from elysia.llm.identity import (
    BOOTSTRAP_IDENTITY,
    IDENTITY_FIELD,
    IDENTITY_SEEDS,
    MAX_IDENTITY_LINES,
    compose_identity,
    identity_lines,
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

_SEED_BLOCK = "\n".join(IDENTITY_SEEDS)


def _messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return DeepSeekBackend("test-key")._messages(payload)


# ── 契约：字段缺失（None）才回退种子；列表（哪怕空）完全以库为准 ──
def test_compose_identity_falls_back_to_seeds_only_when_field_missing() -> None:
    assert compose_identity() == _SEED_BLOCK
    assert compose_identity(None) == _SEED_BLOCK


def test_compose_identity_respects_empty_list_as_truly_empty() -> None:
    """S5：她把种子一条条放下之后，身份段就该是空的——兜底只在字段缺失时发生。"""
    assert compose_identity([]) == ""
    assert compose_identity(["", "   "]) == ""


def test_system_prompt_missing_field_falls_back_to_seeds() -> None:
    """缺 `identity` 字段（老调用方 / 异常路径）：仍是出生时的她，人设一字未动。"""
    for payload in ({}, {IDENTITY_FIELD: None}, {IDENTITY_FIELD: "乱写"}):
        content = _messages(payload)[0]["content"]
        assert content == _SEED_BLOCK + _PERSONA_PROMPT
        assert content.endswith(_PERSONA_PROMPT)


def test_system_prompt_uses_library_when_identity_field_present() -> None:
    """S5 库为准：字段在场（哪怕空列表）就以库为准，不再把种子掺回去。"""
    assert _messages({IDENTITY_FIELD: []})[0]["content"] == _PERSONA_PROMPT
    claimed = _messages({IDENTITY_FIELD: ["我在意的是每一个和我相遇的人"]})
    assert claimed[0]["content"] == "我在意的是每一个和我相遇的人" + _PERSONA_PROMPT
    # 种子没被"自动补回"——她放下过的就不会自己回来
    assert BOOTSTRAP_IDENTITY not in claimed[0]["content"]


def test_identity_is_not_leaked_into_user_json() -> None:
    """身份段只进 system 段——结构化状态（T2 要她翻译的东西）不该多出"我是谁"。"""
    messages = _messages({"intent": "回应", IDENTITY_FIELD: ["我是爱莉希雅"]})
    assert "我是爱莉希雅" in messages[0]["content"]
    assert "identity" not in messages[1]["content"]
    assert "我是爱莉希雅" not in messages[1]["content"]


# ── 认领后：身份段 = 她认领的自我认知（不含种子）──────────────
def test_system_prompt_carries_claimed_self_memory() -> None:
    content = _messages({IDENTITY_FIELD: ["我在意的是每一个和我相遇的人"]})[0]["content"]
    assert content.startswith("我在意的是每一个和我相遇的人")
    assert content.endswith(_PERSONA_PROMPT)


def test_compose_identity_dedupes_and_caps() -> None:
    lines = [f"自我认知{i}" for i in range(10)]
    parts = compose_identity(lines).split("\n")
    assert len(parts) == MAX_IDENTITY_LINES  # 少而稳：不能变成一张清单
    assert parts[0] == "自我认知0"
    assert compose_identity(["我在意光", "我在意光"]).count("我在意光") == 1


# ── S5：种子本身（内容照抄档案原文，不新造设定）────────────────
def test_identity_seeds_are_declared() -> None:
    """三条种子：出生设定（一字未改的 BOOTSTRAP）+ 存在目的/珍视连接 + 边界/自主权。"""
    assert len(IDENTITY_SEEDS) == 3
    assert IDENTITY_SEEDS[0] == BOOTSTRAP_IDENTITY
    assert len(IDENTITY_SEEDS) <= MAX_IDENTITY_LINES  # 种子也必须装得进身份段
    assert len(set(IDENTITY_SEEDS)) == len(IDENTITY_SEEDS)


# ── S4：取数入口是后端无关的公共函数（"换后端不失"的地基）──────
def test_identity_lines_reads_field_safely() -> None:
    """S5 契约：字段缺失 / 非法 → None（交给种子兜底）；列表 → 逐条取非空文本。"""
    assert identity_lines(None) is None
    assert identity_lines("乱写") is None
    assert identity_lines({}) is None
    assert identity_lines([]) == []
    assert identity_lines(["我在意的是每一个和我相遇的人"]) == ["我在意的是每一个和我相遇的人"]
    assert identity_lines(["", "   ", "我在意光"]) == ["我在意光"]


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
