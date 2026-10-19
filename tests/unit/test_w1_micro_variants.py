"""W1 罐装句改造：变体池 + 按状态分档选句 + 连续不重复（纯函数单测）。

取消测试（还原成旧 `llm/micro.py` ⇒ 本文件必须红）：
旧版每 intent 只有 1 句、尾缀每档 1 句、没有 `pick_variant`——本文件在 import 阶段
就会失败（`ImportError: cannot import name 'pick_variant'`）；即使只把旧版函数名补上，
下面"每 intent ≥3 变体 / 连续不重复 / 分档命中"的断言也必然红。

她为什么需要这一改（真机事实，2026-10-19）：历史 micro 占 18.5%，但
`'……嗯，陪你聊～。'` 一种句式出现 99 次 ⇒ 问题不是"频繁"，是"单调"。
"""

from __future__ import annotations

from typing import Any

import pytest

from elysia.llm.micro import (
    INTENT_VARIANTS,
    SUFFIX_VARIANTS,
    micro_speak,
    mood_tier,
    pick_variant,
)
from elysia.soul.expression import INTENTS
from elysia.soul.words import PROTECTED_WORDS

_TIERS = ("miss", "rest", "chat", "curious", "neutral")


def _instruction(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent": "发呆呓语",
        "emotion_vector": {
            "chat": 0.0,
            "miss": 0.0,
            "explore": 0.0,
            "curiosity": 0.0,
            "rest": 0.0,
            "self_check": 0.0,
        },
    }
    base.update(over)
    return base


# ── 池子规模：每个 intent ≥ 3 个变体、每档尾缀 ≥ 3 句 ─────────────────
def test_every_intent_has_at_least_three_variants() -> None:
    assert set(INTENT_VARIANTS) == set(INTENTS), "8 个 intent 一个都不能少（含暂不回应）"
    for intent, variants in INTENT_VARIANTS.items():
        assert len(variants) >= 3, f"intent={intent} 只有 {len(variants)} 个变体（旧版是 1 个）"
        assert all(opener and register for opener, register in variants)


def test_suffix_pools_are_expanded_per_tier() -> None:
    for tier in _TIERS:
        assert len(SUFFIX_VARIANTS[tier]) >= 3, f"{tier} 尾缀池不足 3 句"
    # 三档核心情绪（旧 _emoji_for 的三个分支）都在，且彼此不共用句子
    assert set(SUFFIX_VARIANTS["miss"]).isdisjoint(SUFFIX_VARIANTS["rest"])
    assert set(SUFFIX_VARIANTS["rest"]).isdisjoint(SUFFIX_VARIANTS["chat"])


def _all_sentences() -> list[str]:
    """所有 intent × 档位 × 尾缀的可产出句子（穷举，用于 T2 与规模审计）。"""
    out: list[str] = []
    for variants in INTENT_VARIANTS.values():
        for opener, _ in variants:
            for suffixes in SUFFIX_VARIANTS.values():
                out.extend(f"{opener}{suffix}。" for suffix in suffixes)
    return out


def test_micro_templates_never_emit_protected_words() -> None:
    """微声绕过校验器（chain 直接判 ok）⇒ 模板本身**一个保护词都不能含**（T2）。

    旧版 `思念` 开头含保护词「想你」——它不会新增越界能力，但微声路径不该靠
    "反正没人校验"活着。本用例把这件事钉成不变量。
    """
    offenders = [text for text in _all_sentences() if any(w in text for w in PROTECTED_WORDS)]
    assert not offenders, f"微声模板里出现了未授权保护词：{offenders[:5]}"


def test_sentence_capacity_is_much_wider_than_old_single_template() -> None:
    """她能产出的句子数：旧版同一状态恒 1 句；新版按池子相乘（自洽式断言，不钉死数字）。

    旧版：7 intent × 1 开头 × 4 档尾缀 = 28 句，而她的真实状态集中在
    `发呆呓语 + chat` ⇒ 实际恒为「……嗯，陪你聊～。」一句。
    """
    # 下限自洽：每个 intent 至少 3 变体 × 每档至少 3 尾缀（两条下限由上面的用例钉住）
    min_suffixes = 3 * len(_TIERS)
    min_total = len(INTENT_VARIANTS) * 3 * min_suffixes
    total = len(set(_all_sentences()))
    assert total >= min_total, f"扩池不足：可产出句子只有 {total} 种（下限 {min_total}）"
    assert total > 28 * 5, f"新池 {total} 句，未明显宽于旧版全池 28 句"

    # 同一状态下「发呆呓语」的说法数 = 变体数 × 该档尾缀数（自然随池子伸缩）
    same_state = {
        f"{opener}{suffix}。"
        for opener, _ in INTENT_VARIANTS["发呆呓语"]
        for suffix in SUFFIX_VARIANTS["chat"]
    }
    expected = len(INTENT_VARIANTS["发呆呓语"]) * len(SUFFIX_VARIANTS["chat"])
    assert len(same_state) == expected, "变体 × 尾缀没有产生预期数量的不同句子"
    assert expected >= 9, f"同一状态只有 {expected} 种说法（旧版恒 1 句；W1 下限 3×3）"


# ── 按真实维度分档：阈值与旧 `_emoji_for` / TTS 情绪同源 ──────────────
def test_mood_tier_thresholds_match_real_dimension_ranges() -> None:
    assert mood_tier({"miss": 0.41}) == "miss"
    assert mood_tier({"miss": 0.4}) == "neutral"  # 严格大于
    assert mood_tier({"rest": 0.46}) == "rest"
    assert mood_tier({"rest": 0.45}) == "neutral"  # rest 上界 0.5，阈值必须 < 0.5
    assert mood_tier({"chat": 0.31}) == "chat"
    assert mood_tier({"chat": 0.3}) == "neutral"
    assert mood_tier({"explore": 0.5}) == "curious"
    assert mood_tier({"curiosity": 0.5}) == "curious"
    assert mood_tier(None) == "neutral"
    # 优先级沿用旧 _emoji_for：思念 > 歇着 > 想聊
    assert mood_tier({"miss": 0.5, "rest": 0.5, "chat": 0.5}) == "miss"


def test_pick_variant_state_tier_selects_matching_sentence() -> None:
    """同一 intent，不同状态 → 不同语域的开头 + 不同档的尾缀（不是随机）。"""
    chat = pick_variant("回应", {"chat": 0.6})
    assert "我在听着呢" in chat and "陪你聊" in chat

    rest = pick_variant("回应", {"rest": 0.6})
    assert "我听着呢" in rest and "歇" in rest

    miss = pick_variant("思念", {"miss": 0.6})
    assert "念着你" in miss and "心口" in miss

    curious = pick_variant("好奇提问", {"explore": 0.6})
    assert "你说" in curious and "弄明白" in curious

    # 平常档：语域命中 plain、默认尾缀为空 ⇒ 与旧版行为一致（回归保护）
    assert pick_variant("回应", {}) == "嗯嗯。"


def test_pick_variant_is_deterministic() -> None:
    kwargs: dict[str, Any] = {"miss": 0.5}
    a = pick_variant("思念", kwargs, ())
    b = pick_variant("思念", kwargs, ())
    assert a == b, "同一状态必须选同一句（不许抛硬币）"


def test_pick_variant_works_with_empty_recent() -> None:
    for intent in INTENT_VARIANTS:
        text = pick_variant(intent, {"chat": 0.5}, [])
        assert text.endswith("。") and len(text) > 1


# ── 反重复：recent 由调用方传入（不落库）───────────────────────────
def test_pick_variant_downweights_a_recent_variant() -> None:
    first = pick_variant("回应", {"chat": 0.6}, [])
    second = pick_variant("回应", {"chat": 0.6}, [first])
    assert second != first, "刚说过的句子必须降权到别的说法"


def test_consecutive_micro_sentences_never_repeat() -> None:
    """模拟表达服务维护的环形缓冲：连续两条**不得相同**，且能轮出多种说法。"""
    recent: list[str] = []
    produced: list[str] = []
    for _ in range(24):
        text = pick_variant("发呆呓语", {"chat": 0.6}, tuple(recent))
        if produced:
            assert text != produced[-1], f"连续两条相同：{text!r}"
        produced.append(text)
        recent.append(text)
        recent = recent[-8:]
    assert len(set(produced)) >= 8, f"24 次只说出了 {len(set(produced))} 种句子：{set(produced)}"


def test_unknown_intent_still_speaks_with_pure_template() -> None:
    text = micro_speak(_instruction(intent="不存在的意图"), ())
    assert text.endswith("。")
    assert "痛" not in text and "格式化" not in text and "{" not in text


def test_micro_speak_accepts_recent_for_de_duplication() -> None:
    recent = ["嗯嗯。"]
    text = micro_speak(_instruction(intent="回应"), recent)
    assert text != "嗯嗯。", "调用方传入 recent 时，微声必须避开它"
    assert text.endswith("。")


@pytest.mark.parametrize("tier", _TIERS)
def test_every_tier_is_reachable_through_documents(tier: str) -> None:
    """每个档位都能被真实的感受向量触发（防"造好了没插电"的阈值死分支）。"""
    samples = {
        "miss": {"miss": 0.9},
        "rest": {"rest": 0.5},
        "chat": {"chat": 0.9},
        "curious": {"explore": 0.9},
        "neutral": {},
    }
    assert mood_tier(samples[tier]) == tier
