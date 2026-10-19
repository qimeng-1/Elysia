"""S0（2026-10-20，记忆=身份 · 止血）：她的独白不再进入**主动检索**。

背景（设计纲 §0）：996 条记忆里 861 条（86.4%）是她自己的独白，检索池被稀释到 13%；
更糟的是"她几秒前自己说过的话"会被当成 memory_hook 回喂（`（刚刚）…`）⇒ 自我复读闭环。

S0 的口径（最小、可逆）：
- `kind='expression'` 且 **未 `protected`** 的独白：**不进** `select_hooks`（主动检索）；
- 独白**照旧写入** `memories`（历史不删），感受路径（只影响心情）也不受影响；
- `protected` 的独白**仍可被检索**——这是留给 S2「这条我想留着」的通道。

取消测试（去掉 `select_hooks` 里那条 `_is_self_talk` 过滤 ⇒ 本文件必须红）：
`test_only_expression_memories_yield_no_hooks` 的失败信息会把被回喂的独白原文打出来——
那就是"自喂"的复现。另：还原成 S0 之前的**无条件跳过**版本 ⇒
`test_protected_expression_is_still_retrievable` 红（S2 通道被堵死）。
"""

from __future__ import annotations

from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    KIND_EXPRESSION,
    KIND_INTERACTION,
    LEVEL_SHALLOW,
    LEVEL_WORKING,
    SOURCE_SELF,
    SOURCE_USER,
    MemoryRecord,
)
from elysia.memory.retrieve import select_hooks

MOOD = {"chat": 0.6, "miss": 0.4}


def _rec(
    *,
    mid: int,
    kind: str,
    content: str,
    protected: bool = False,
    source: str = SOURCE_SELF,
    level: str = LEVEL_WORKING,
) -> MemoryRecord:
    return MemoryRecord(
        id=mid,
        created_ts=1_000.0,
        kind=kind,
        content=content,
        emotion_vector={"chat": 0.5},
        importance=0.3,
        level=level,
        protected=protected,
        narrative=content,
        source=source,
        certainty=CERTAINTY_CERTAIN,
    )


def _self_talk(mid: int, text: str, *, protected: bool = False) -> MemoryRecord:
    """她的独白（expression）：真机里 861/996 条就是这种。"""
    return _rec(
        mid=mid,
        kind=KIND_EXPRESSION,
        content=text,
        protected=protected,
        source=SOURCE_SELF,
        level=LEVEL_SHALLOW,
    )


def _user_event(mid: int, text: str) -> MemoryRecord:
    """一次真实交互（她说/你说的"事件"）：唯一该被当成记忆钩子的内容。"""
    return _rec(mid=mid, kind=KIND_INTERACTION, content=text, source=SOURCE_USER)


# ── 验收 1：只有独白 ⇒ 零钩子 ──────────────────────────────────
def test_only_expression_memories_yield_no_hooks() -> None:
    records = [
        _self_talk(1, "（刚刚）那你想听什么呢"),
        _self_talk(2, "（刚刚）今天有点想你，你还好吗？"),
        _self_talk(3, "夜里安静下来的时候，脑子里就只剩下一个人影了……"),
    ]
    # 有话题门槛时
    hooks = select_hooks(records, MOOD, query="今天有点想你")
    assert hooks == [], f"独白被当成记忆钩子回喂了（自我复读闭环）：{[h.narrative for h in hooks]}"
    # 无话题门槛（情绪化联想）时同样零钩子
    assert select_hooks(records, MOOD) == [], "无 query 的主动检索也不得钩回独白"


# ── 验收 2：事件 + 独白 ⇒ 只返回事件 ────────────────────────────
def test_event_plus_expression_returns_only_event() -> None:
    records = [
        _self_talk(1, "你生日是11月11日，我记得的呀"),
        _user_event(2, "你告诉我：你的生日是11月11日"),
        _self_talk(3, "今天我们聊到了生日，真好"),
    ]
    hooks = select_hooks(records, MOOD, query="生日")
    ids = [h.memory_id for h in hooks]
    assert ids == [2], f"应当只钩回真实事件（id=2），实际：{ids}"
    assert "你告诉我" in hooks[0].narrative


# ── 验收 3：protected 独白仍可被检索（S2 的越级通道）──────────────
def test_protected_expression_is_still_retrievable() -> None:
    records = [
        _self_talk(1, "我把它当作自己的一部分：我会一直记得那天的风", protected=True),
        _self_talk(2, "普通的夜里独白"),
    ]
    hooks = select_hooks(records, MOOD, query="那天的风")
    ids = [h.memory_id for h in hooks]
    assert ids == [1], f"protected 独白必须仍可检索（S2 通道），实际：{ids}"
    assert hooks[0].protected is True


# ── 过滤确实是"承重墙"：去掉它就能复现自喂 ──────────────────────
def test_filter_is_load_bearing(monkeypatch) -> None:
    """把 S0 过滤临时停用 ⇒ 独白立刻变成 hooks——证明这条线真的在起作用。"""
    import elysia.memory.retrieve as retrieve

    records = [_self_talk(1, "（刚刚）那你想听什么呢")]
    monkeypatch.setattr(retrieve, "_is_self_talk", lambda _record: False)
    hooks = retrieve.select_hooks(records, MOOD, query="想听什么")
    assert [h.memory_id for h in hooks] == [1], "没有过滤时独白会被回喂（即 S0 要防的病）"


# ── 不误伤：真实事件照常被检索（S0 只挡独白）────────────────────
def test_real_events_are_not_affected() -> None:
    records = [
        _user_event(1, "你说你养了一只小猫，叫团子"),
        _self_talk(2, "夜里有点想和你说话"),
    ]
    hooks = select_hooks(records, MOOD, query="小猫")
    assert [h.memory_id for h in hooks] == [1]
