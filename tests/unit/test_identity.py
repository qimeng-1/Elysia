"""第八节 S1/S2 单测：Self Memory 本体 + 身份段注入通路。

S1 的硬约束是**对外行为零变化**：没有任何认领记录时，装配出的 system prompt
不得多出"我是谁"。当时用改造前（commit bee693f）的 `_SYSTEM_PROMPT` 全文做
golden；S2 有意在人设里补了 adopt 的说明，全文冻结不再成立，故改为**结构性
不变量**：system 段 = 出生设定 + 表达层人设，无认领时不增不减。

（S1 期的逐字 golden 存于 `f2a9242` 历史；"人设文本本身有没有被误改"仍由
`content.endswith(_PERSONA_PROMPT)` 把关——改人设必须是有意为之。）
"""

from __future__ import annotations

from typing import Any

from elysia.llm.deepseek import _PERSONA_PROMPT, DeepSeekBackend
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


def _messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return DeepSeekBackend("test-key")._messages(payload)


# ── 零变化：身份段 = 出生设定（＝改造前首句）────────────────
def test_compose_identity_without_claim_is_bootstrap_only() -> None:
    assert compose_identity() == BOOTSTRAP_IDENTITY
    assert compose_identity([]) == BOOTSTRAP_IDENTITY
    assert compose_identity(["", "   "]) == BOOTSTRAP_IDENTITY


def test_system_prompt_unchanged_when_no_self_memory() -> None:
    """无认领时 system 段不增不减：出生设定 + 人设，缺 identity / 空列表 / 非法值都一样。"""
    for payload in ({}, {IDENTITY_FIELD: []}, {IDENTITY_FIELD: None}, {IDENTITY_FIELD: "乱写"}):
        content = _messages(payload)[0]["content"]
        assert content.startswith(BOOTSTRAP_IDENTITY)
        assert content.endswith(_PERSONA_PROMPT)
        assert len(content) == len(BOOTSTRAP_IDENTITY) + len(_PERSONA_PROMPT)


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
    assert content.endswith(_PERSONA_PROMPT)


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
