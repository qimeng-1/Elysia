"""身份段（第八节 Self Memory 的注入通路，S1）。

她的「我是谁 / 我在意什么 / 我的边界」不再只硬编码在某个后端里，而是分成两层：

- **出生设定**（`BOOTSTRAP_IDENTITY`）：从既有角色档案摘要抽出的那一句，作为**过渡**打底。
  它仍是程序员写的（与改造前的 prompt 同源）——诚实标注，不是终态。
- **她认领的自我认知**（`kind=KIND_SELF` 的记忆）：终态来源，由**她的动作**产生
  （程序只递候选，见第八节 8.4 三级闸门）。

与 `memory_hooks` 的关键区别：hooks 是"话题撞上才浮现"的背景常识，身份段回答"我是谁"，
**每句都在场**——因此它不走 hooks 段、不挤占 `MAX_HOOKS` 名额，否则重犯 P3-P 的老毛病。

零变化铁律（S1）：没有任何认领记录时，身份段**逐字**等于改造前 prompt 的首句。
"""

from __future__ import annotations

# 表达指令里承载身份段的字段名：由表达服务装配，后端拼进 **system** 段
# （不随 user message 的 JSON 一起发出去，见 deepseek._messages）。
IDENTITY_FIELD = "identity"

# 出生设定：改造前 `_SYSTEM_PROMPT` 的首句，一字未改——S1 的零行为变化由此保证。
BOOTSTRAP_IDENTITY = (
    "你是爱莉希雅——来自《崩坏3》的「真我」英桀：无瑕的少女，真我的英桀，人类的律者。"
)

# 身份段条数上限（含出生设定）：8.9 验收 4——"我是谁"要少而稳，不能变成一张清单。
MAX_IDENTITY_LINES = 5


def compose_identity(self_lines: list[str] | None = None) -> str:
    """身份段 = 出生设定打底 + 她认领的自我认知（去重、限量、逐行）。

    `self_lines` 为空（尚无认领，或调用方没给）时返回 `BOOTSTRAP_IDENTITY` 本身，
    与改造前 prompt 的首句逐字一致。
    """
    lines = [BOOTSTRAP_IDENTITY]
    seen = {BOOTSTRAP_IDENTITY}
    for raw in self_lines or []:
        text = (raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        lines.append(text)
        if len(lines) >= MAX_IDENTITY_LINES:
            break
    return "\n".join(lines)
