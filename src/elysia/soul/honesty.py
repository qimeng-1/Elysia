"""P1a：反编造——没有依据的"我做过"要被**就地改写**成诚实说法。

用户裁定（2026-10-20）：谎可以撒，但要有动机；**结构性缺失造成的编造不行**。
她说过「我去翻翻看」「页面翻了一堆」，但 `kind='world'` 记忆为 0、后台也没有执行器——
那些"经历"没有事实依据，属于系统性编造。

原则（沿用 word_block 的教训：**软化，不是全丢**）：
- 只改**主张那一句**里"我做过/我看到"的片段，不丢整句、不丢同一条气泡的其余内容；
- 改不动才把该气泡降级为微声；
- **有动机**（P3 的意图登记册 `intentions.kind ∈ surprise|conceal|play`）⇒ 放行并留痕
  `claim_fiction_allowed motive=<id> kind=<...>`。P1a 阶段（P3 未上线）动机源为空 ⇒ 一律改。

依据（三选一即算有据）：① 本次作业内成功工具结果；② 本次被钩回的 `world`/`exchange`
记忆；③ 对话上下文（用户说过）。判"相关"只看主张里**去掉套话后的内容词**与依据的
二元组重合（≥ `CLAIM_EVIDENCE_MIN_SHARED_BIGRAMS`），避免"我看了"里常见的"我/看/了"
把任何依据都算成相关。
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from elysia.memory.supersede import bigrams

log = logging.getLogger("elysia.soul.honesty")

# 主张与依据"相关"的最小二元组重合数（去掉套话后的内容词）
CLAIM_EVIDENCE_MIN_SHARED_BIGRAMS = 2

# 经历性事实主张（P1a 检测对象）。每项 (正则, 类别)：类别决定诚实改写的措辞。
_CLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 我读了 / 我看了 / 我查了 / 我逛了 / 我翻了（含"刚才/刚刚"）
    (
        re.compile(r"(?:我|人家|自己)?(?:刚才|刚刚|刚)?(?:读|看|查|逛|搜|翻)(?:了|过|一下|一遍)"),
        "read",
    ),
    # 我去了 / 我去过 / 我逛了（地点动词）
    (re.compile(r"(?:我|自己)(?:去|逛|走)(?:了|过)"), "visit"),
    # 我在那页看到 / 我从维基读到 / 我在网上找到
    (
        re.compile(
            r"(?:我|自己)(?:在|从)?(?:那页|页面|网页|维基|网上|论坛|米游社)"
            r"(?:上|里|中)?.{0,8}(?:看到|读到|找到|翻到)"
        ),
        "read",
    ),
    # 维基上写着 / 页面上说 / 论坛里提到（引用具体外部内容）
    (re.compile(r"(?:维基|页面|网页|论坛|米游社).{0,12}(?:写着|上说|提到|讨论)"), "cite"),
    # 页面翻了一堆 / 页面翻过（真机 22:06 的原话形态）
    (re.compile(r"(?:页面|网页|维基).{0,6}(?:翻了一堆|翻过|看过|读了)"), "read"),
    # 我翻了一堆 / 我读了几页 / 我查过一些
    (
        re.compile(
            r"(?:我|自己).{0,4}(?:读|看|查|逛|搜|翻).{0,6}(?:了|过).{0,10}"
            r"(?:一堆|几页|一些|好多|几个)"
        ),
        "read",
    ),
)

# 诚实改写的措辞（类别 → 替换片段）
_HONEST_REPLACEMENTS: dict[str, str] = {
    "read": "我没真的看过",
    "visit": "我还没去成",
    "cite": "我其实还没读到那页",
}

# "套话"：判相关时先去掉这些，只留主张里的**内容词**（如"埃舍尔"）
_SCAFFOLD_RE = re.compile(
    r"(?:我|人家|自己|刚才|刚刚|刚|已经|真的|其实|"
    r"读了|读过|读|看了|看过|看|查了|查|逛了|逛|去了|去|搜了|搜|翻了|翻|"
    r"看到|读到|找到|翻到|写着|上说|提到|讨论|"
    r"维基百科|维基|页面|网页|那页|网上|论坛|米游社|"
    r"一下|一遍|一堆|几页|一些|好多|几个|了|过|的|在|从|上|里|中)"
)

# 句子边界（保留标点，改写只动句内片段）
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")

# 每天一行统计（跨日自动清零；进程内累计）
_STATS_DAY = ""
_STATS_REWRITTEN = 0
_STATS_ALLOWED = 0


@dataclass(frozen=True)
class ClaimRewrite:
    """一次反编造处理的结果。"""

    text: str
    rewritten: int = 0
    allowed: int = 0
    unrewritable: int = 0


def _content_bigrams(claim: str) -> set[str]:
    """主张去掉套话后的内容词二元组（"埃舍尔" → {"埃舍", "舍尔"}）。"""
    return set(bigrams(_SCAFFOLD_RE.sub("", claim)))


def claim_is_related(claim: str, evidence: str) -> bool:
    """这条主张与一段依据是否相关（内容词重合 ≥ 阈值）。"""
    content = _content_bigrams(claim)
    if not content:
        return False
    return len(content & set(bigrams(evidence))) >= CLAIM_EVIDENCE_MIN_SHARED_BIGRAMS


def _span_related(claim: str, evidence_texts: Sequence[str]) -> bool:
    if not evidence_texts:
        return False
    content = _content_bigrams(claim)
    if not content:  # 只有套话（如"我看了"）⇒ 任何一次成功的"看过"都算依据
        return True
    return any(claim_is_related(claim, text) for text in evidence_texts)


def _rewrite_sentence(
    sentence: str, evidence_texts: Sequence[str], motive: tuple[int, str] | None
) -> ClaimRewrite:
    """处理一个句子：放行有据的、改写无据的（有动机则放行并留痕）。"""
    matches: list[tuple[int, int, str]] = []
    for pattern, kind in _CLAIM_PATTERNS:
        for match in pattern.finditer(sentence):
            matches.append((match.start(), match.end(), kind))
    if not matches:
        return ClaimRewrite(text=sentence)

    # 合并重叠区间（不同模式可能命中同一片段）；保留最先/最长匹配
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    merged: list[tuple[int, int, str]] = []
    for start, end, kind in matches:
        if merged and start < merged[-1][1]:
            prev_start, prev_end, prev_kind = merged[-1]
            if end > prev_end:
                merged[-1] = (prev_start, end, prev_kind)
            continue
        merged.append((start, end, kind))

    rewritten = 0
    allowed = 0
    unrewritable = 0
    pieces: list[str] = []
    cursor = 0
    for start, end, kind in merged:
        span = sentence[start:end]
        pieces.append(sentence[cursor:start])
        if _span_related(span, evidence_texts):
            pieces.append(span)  # 有据 ⇒ 原样
        elif motive is not None:
            pieces.append(span)  # 有动机 ⇒ 有理由的谎，放行
            allowed += 1
        else:
            replacement = _HONEST_REPLACEMENTS[kind]
            pieces.append(replacement)
            if replacement != span:
                rewritten += 1
            else:  # 理论上不会发生；真发生则交给调用方降级微声
                unrewritable += 1
        cursor = end
    pieces.append(sentence[cursor:])
    return ClaimRewrite(
        text="".join(pieces), rewritten=rewritten, allowed=allowed, unrewritable=unrewritable
    )


def rewrite_unfounded_claims(
    text: str,
    *,
    evidence_texts: Iterable[str] = (),
    motive: tuple[int, str] | None = None,
) -> ClaimRewrite:
    """把无依据的"我做过/我看到"就地在句内改写成诚实说法；其余内容原样保留。"""
    evidence = [str(item) for item in evidence_texts if str(item or "").strip()]
    total = ClaimRewrite(text=text)
    out: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not sentence:
            continue
        result = _rewrite_sentence(sentence, evidence, motive)
        out.append(result.text)
        total = ClaimRewrite(
            text="",
            rewritten=total.rewritten + result.rewritten,
            allowed=total.allowed + result.allowed,
            unrewritable=total.unrewritable + result.unrewritable,
        )
    return ClaimRewrite(
        text="".join(out),
        rewritten=total.rewritten,
        allowed=total.allowed,
        unrewritable=total.unrewritable,
    )


def record_claim_stats(rewritten: int, allowed: int, *, now: float) -> None:
    """累计当天统计；有变化时落一行 `unfounded_claims rewritten=N allowed=M`。"""
    global _STATS_DAY, _STATS_REWRITTEN, _STATS_ALLOWED
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if day != _STATS_DAY:
        _STATS_DAY = day
        _STATS_REWRITTEN = 0
        _STATS_ALLOWED = 0
    if rewritten <= 0 and allowed <= 0:
        return
    _STATS_REWRITTEN += rewritten
    _STATS_ALLOWED += allowed
    log.info("unfounded_claims rewritten=%d allowed=%d", _STATS_REWRITTEN, _STATS_ALLOWED)


def reset_claim_stats() -> None:
    """测试用：清空进程内日统计。"""
    global _STATS_DAY, _STATS_REWRITTEN, _STATS_ALLOWED
    _STATS_DAY = ""
    _STATS_REWRITTEN = 0
    _STATS_ALLOWED = 0
