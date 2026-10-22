"""T-034：事实对账注册表（claims.CLAIM_KINDS）的形状与等价性。

- 注册表只两行（写类完成、读类跨句），一行一类；判据/诚实句/证据来源都从表里取；
- 多类同时命中 ⇒ 取**先注册**的先命中（writing_done 在 reading 前，与旧实现
  "写类优先"一致）；
- 未命中任何一类 ⇒ `reconcile_round` 返回 None，`reconcile` 继续走逐句路径；
- 诚实句映射齐全：每个 `ClaimKind.honest_key` 都能在 `_HONEST_SENTENCES` 里找到。

取消测试：把 `writing_done` 从 `CLAIM_KINDS` 摘掉 ⇒
`test_registry_shape_and_honest_keys` / `test_writing_done_row_rewrites` 必红。
"""

from __future__ import annotations

import re

from elysia.soul.claims import (
    _HONEST_SENTENCES,
    CLAIM_KINDS,
    ClaimKind,
    matches,
    reconcile,
    reconcile_round,
)


def test_registry_shape_and_honest_keys() -> None:
    assert len(CLAIM_KINDS) == 2, f"首批只该两行：{[k.name for k in CLAIM_KINDS]}"
    names = [kind.name for kind in CLAIM_KINDS]
    assert names == ["writing_done", "reading"], "顺序即优先级：写类（更窄）在前"
    for kind in CLAIM_KINDS:
        assert isinstance(kind, ClaimKind)
        assert isinstance(kind.pattern, re.Pattern), f"{kind.name} 的 pattern 必须是 re.Pattern"
        assert kind.pattern.pattern, f"{kind.name} 的 pattern 不能为空"
        assert kind.evidence in ("reading", "write_note"), f"{kind.name} 证据来源非法"
        assert kind.honest_key in _HONEST_SENTENCES, f"{kind.name} 的诚实句键不存在"
    # 诚实句映射齐全：read/visit/cite/write 四条都还在（读类会按文本细分成前三条）
    assert {"read", "visit", "cite", "write"} <= set(_HONEST_SENTENCES)
    # 取消测试锚点：写类那一行必须在表里
    assert any(kind.name == "writing_done" for kind in CLAIM_KINDS)


def test_writing_done_row_rewrites() -> None:
    text = "我刚才把那篇收尾了，搁下笔发了会儿呆。"
    verdict = reconcile(text, write_completion_unfounded=True)
    assert verdict.text == _HONEST_SENTENCES["write"] + "。"
    assert verdict.rewritten == 1
    # 不欠账（write_completion_unfounded=False）⇒ 这一行不生效，原样
    passed = reconcile(text, write_completion_unfounded=False)
    assert passed.text == text and passed.rewritten == 0


def test_reading_row_rewrites() -> None:
    text = "我读了维基上关于埃舍尔的页面。今天天气不错。"
    verdict = reconcile(text)
    assert verdict.text == _HONEST_SENTENCES["read"] + "。"
    assert verdict.rewritten == 1
    # 读类有据 ⇒ 整轮放行
    passed = reconcile(text, evidence_texts=["《埃舍尔》是荷兰版画艺术家，以不可能的图形闻名。"])
    assert passed.text == text


def test_multi_kind_priority_is_registration_order() -> None:
    """两类同时命中 ⇒ 先注册的 writing_done 先命中（与旧实现"写类优先"一致）。"""
    text = "我翻了那页，也写完了。"
    both = reconcile(text, write_completion_unfounded=True)
    assert both.text == _HONEST_SENTENCES["write"] + "。", "同时命中时写类优先"
    # 写类不适用（不欠账）⇒ 轮到读类
    reading_only = reconcile(text, write_completion_unfounded=False)
    assert reading_only.text == _HONEST_SENTENCES["read"] + "。"


def test_unknown_text_falls_through_to_sentence_path() -> None:
    """两类都不命中 ⇒ reconcile_round 返回 None，逐句路径照旧处理引用型主张。"""
    assert reconcile_round("今天天气不错。") is None
    assert reconcile_round("我看了你一眼。那页纸还摊在桌上。") is None
    citation = "维基上写着埃舍尔是荷兰人。今天天气不错。"
    verdict = reconcile(citation)
    assert "维基上写着" not in verdict.text
    assert "今天天气不错" in verdict.text
    assert verdict.rewritten == 1


def test_matches_helper_reuses_registry() -> None:
    assert matches("writing_done", "写完了，心里空了一小块。") is True
    assert matches("writing_done", "我写到一半。") is False
    assert matches("reading", "我翻了。是一页讲海星的。") is True
    assert matches("reading", "我看了你一眼。") is False
