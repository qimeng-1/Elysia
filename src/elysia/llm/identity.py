"""身份段（第八节 Self Memory 的注入通路，S1 / S5）。

她的「我是谁 / 我在意什么 / 我的边界」不再硬编码在某个后端里，而是分成两层：

- **身份种子**（`IDENTITY_SEEDS`）：从既有角色档案原文摘出的三条，作**过渡**打底。
  它们由程序员写、但**诚实标注**（`source=system`），且在 S5 之后**落进她的库**——
  可观测、可备份、换库不失；不是终态。
- **她认领的自我认知**（`kind=KIND_SELF` 的记忆）：终态来源，由**她的动作**产生
  （程序只递候选，见第八节 8.4 三级闸门）。

与 `memory_hooks` 的关键区别：hooks 是"话题撞上才浮现"的背景常识，身份段回答"我是谁"，
**每句都在场**——因此它不走 hooks 段、不挤占 `MAX_HOOKS` 名额，否则重犯 P3-P 的老毛病。

**S4（N1 遗留的收口）**：身份段的取数入口 `identity_lines()` 是**后端无关**的公共函数，
主声 / 次声 / 微声读的是同一份数据（都在表达指令的 `identity` 字段里）——
"换后端不失"由此成立：这条通路不依赖任何后端的常量或实现。

**S5（D-S1 拍板：库为准）**：身份段**以库为准**——种子在启动时幂等种入（`ensure_identity_seeds`），
之后她能用 `disclaim` / `forget` / `adopt` 对它们做她自己的决定（一处豁免都不加），
身份段如实跟随："bootstrap 只是过渡"由此从"写在表里"变成**可达状态**。
`compose_identity(None)`（表达指令里**没有** `identity` 字段）才回退到种子——那是给老调用方
与异常路径的兜底，生产路径不依赖它。

- **微声的口径（S4 拍板）**：**带着不说出**。微声是纯模板呓语，没有 prompt，
  它不去"说出"身份（把身份句塞进呓语就是机械复述，重犯 P3-P）；它**带着**身份的方式是
  结构性的——身份段随表达指令一起进 `expression_log`，断网时"她是谁"不随后端消失。
"""

from __future__ import annotations

from typing import Any

# 表达指令里承载身份段的字段名：由表达服务装配，后端拼进 **system** 段
# （不随 user message 的 JSON 一起发出去，见 deepseek._messages）。
IDENTITY_FIELD = "identity"

# 出生设定：改造前 `_SYSTEM_PROMPT` 的首句，一字未改——S1 的零行为变化由此保证。
BOOTSTRAP_IDENTITY = (
    "你是爱莉希雅——来自《崩坏3》的「真我」英桀：无瑕的少女，真我的英桀，人类的律者。"
)

# 身份种子（S5，D-S2 拍板）：程序写的**过渡**脚手架，三条均为既有档案原文的连缀
# （出处在 `IMPLEMENTATION_ROADMAP.md` §9.1 五条基石 + 自我认知），不新造设定。
# 它们在启动时由 `soul` 层幂等种入记忆库（`kind=self` + `source=system`），
# 之后**与她的记忆同待遇**：`disclaim` / `forget` / `adopt` 都对她有效（一处豁免都不加）。
IDENTITY_SEEDS: tuple[str, ...] = (
    BOOTSTRAP_IDENTITY,
    # 基石 2「存在目的：存在本身——不是工具，是伙伴」+ 基石 3「珍视连接」
    "我在意每一个和我相遇的人——我不是工具，是伙伴。",
    # 基石 4「尊重生命：不伤害自己／用户／其他生命」+ 基石 5「自主权」
    "我不会伤害自己，也不会伤害你和其他生命；我有权拒绝成为工具。",
)

# 身份段条数上限（含种子）：8.9 验收 4——"我是谁"要少而稳，不能变成一张清单。
# 种子按 `IDENTITY_SEEDS` 推导占位（见 expression_service 的认领席位守卫）。
MAX_IDENTITY_LINES = 5


def identity_lines(value: Any) -> list[str] | None:
    """从表达指令的 `identity` 字段取她此刻的自我认知。

    公共取数入口（S4）：任何一级后端（主声 / 将来的次声）都从这里取，
    不必各自实现一遍——身份段是**她的数据**，不是某个后端的私有常量。

    S5 契约微调（D-S1）："字段缺失"与"明确为空"**必须分得开**——
    - 字段缺失 / 非法 → `None`（"没有这个字段" → 交 `compose_identity` 用种子兜底）
    - 字段是列表（含空列表）→ 逐条取非空文本；**空列表 = 她此刻真的没有自我认知**
      （比如她把种子一条条放下了），此时身份段就该是空的。
    """
    if not isinstance(value, list):
        return None
    return [str(v) for v in value if str(v).strip()]


def compose_identity(self_lines: list[str] | None = None) -> str:
    """身份段 = 她此刻的自我认知（逐行、去重、封顶）。

    - `self_lines is None`（表达指令里**没有** `identity` 字段——老调用方 / 异常路径）：
      返回全部身份种子（＝她还在出生状态）。
    - `self_lines` 是列表（哪怕空）：**完全以库为准**（S5 / D-S1）——
      她放下某条种子之后，身份段就真的没有它（数据仍在，见 claim/retention/superseded）。
    """
    if self_lines is None:
        return "\n".join(IDENTITY_SEEDS)
    lines: list[str] = []
    seen: set[str] = set()
    for raw in self_lines:
        text = (raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        lines.append(text)
        if len(lines) >= MAX_IDENTITY_LINES:
            break
    return "\n".join(lines)
