"""P1a：反编造——没有依据的"我读过外部世界"要被**整句**换成诚实说法。

用户裁定（2026-10-20）：谎可以撒，但要有动机；**结构性缺失造成的编造不行**。
她说过「我去翻翻看」「页面翻了一堆」，但 `kind='world'` 记忆为 0、后台也没有执行器——
那些"经历"没有事实依据，属于系统性编造。

P1a-fix（用户实测误伤 12 句日常话后收紧）：
- **只拦外部信息主张**：必须同时出现"动作（读/查/搜/逛/翻/看）"与"外部来源对象
  （维基/页面/网页/米游社/论坛/帖子/资料/文章/网上）"，或引用外部内容（"维基上写着"）；
  「我看了你一眼」「我去趟厨房」「我看了个视频」「我读过那本书」**不拦**（不是"读了世界"）。
- **整句替换**成一句自然的诚实句（「其实我还没真的去翻过呢。」），绝不把短语塞回原句
  （否则出现"我没真的看过一下时间"这类语病）；同一气泡的**其余句子原样保留**。
- 有依据 ⇒ 原句放行；有动机 ⇒ 原句放行并留痕 `claim_fiction_allowed motive=<id> kind=<...>`。
- P1a 阶段（P3 未上线）动机源为空 ⇒ 一律改成诚实句。

依据（三选一即算有据）：① 本次作业内成功工具结果；② 本次被钩回的 `world`/`exchange`
记忆；③ 对话上下文（用户说过）。判"相关"只看句子**去掉套话后的内容词**与依据的
二元组重合（≥ `CLAIM_EVIDENCE_MIN_SHARED_BIGRAMS`）。
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

# 经历性主张（P1a-fix）：必须**同时**出现"动作"与"外部来源对象"才算
# "我读了世界"——否则"我看了你一眼/去了趟厨房/看了个视频/读过那本书"这些
# 日常动作会被误伤（用户实测 12 句日常话被改 10 句）。"书/视频/一眼"不是
# 外部信息世界，不在拦截范围。
_ACTION_RE = re.compile(r"(?:读|查|搜|逛|翻|看)(?:了|过|一下|一遍|到)?")
_EXTERNAL_RE = re.compile(r"(?:维基百科|维基|页面|网页|那页|这页|米游社|论坛|帖子|资料|文章|网上)")
# 引用具体外部内容（"维基上写着/页面上说"）——没有动作动词也算经历主张
_CITATION_RE = re.compile(
    r"(?:维基百科|维基|页面|网页|论坛|米游社|网上).{0,12}(?:写着|上说|提到|讨论)"
)

# 句子边界（保留标点，整句替换时沿用原句的收尾标点）
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")

# "诚实句"（整句替换，不做短语回填——否则会出现"我没真的看过一下时间"这类语病）
_HONEST_SENTENCES: dict[str, str] = {
    "read": "其实我还没真的去翻过呢",
    "visit": "其实我还没真的去过呢",
    "cite": "其实我也还没真的读到过那页呢",
}

# "套话"：判相关时先去掉这些，只留主张里的**内容词**（如"埃舍尔"）
_SCAFFOLD_RE = re.compile(
    r"(?:我|人家|自己|刚才|刚刚|刚|已经|真的|其实|"
    r"读了|读过|读|看了|看过|看|查了|查|逛了|逛|去了|去|搜了|搜|翻了|翻|"
    r"看到|读到|找到|翻到|写着|上说|提到|讨论|"
    r"维基百科|维基|页面|网页|那页|这页|网上|论坛|米游社|帖子|资料|文章|"
    r"一下|一遍|一堆|几页|一些|好多|几个|了|过|的|在|从|上|里|中)"
)

# 每天统计（跨日自动清零；进程内累计）
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


def is_external_claim(sentence: str) -> bool:
    """P1a-fix：是否"我读过外部世界"的主张（动作 + 外部来源对象，或引用外部内容）。"""
    if _CITATION_RE.search(sentence):
        return True
    return bool(_ACTION_RE.search(sentence)) and bool(_EXTERNAL_RE.search(sentence))


def _sentence_related(sentence: str, evidence_texts: Sequence[str]) -> bool:
    if not evidence_texts:
        return False
    content = _content_bigrams(sentence)
    if not content:  # 全是套话（如"页面翻了一堆"）⇒ 任一次成功读取都算依据
        return True
    return any(claim_is_related(sentence, text) for text in evidence_texts)


def _sentence_kind(sentence: str) -> str:
    if _CITATION_RE.search(sentence):
        return "cite"
    if re.search(r"(?:去|逛)(?:了|过)", sentence):
        return "visit"
    return "read"


def _trailing_punct(sentence: str) -> tuple[str, str]:
    match = re.search(r"([。！？!?；;]+)$", sentence)
    if match is None:
        return sentence, "。"
    return sentence[: match.start()], match.group(1)


def _rewrite_sentence(
    sentence: str, evidence_texts: Sequence[str], motive: tuple[int, str] | None
) -> ClaimRewrite:
    """处理一个句子：不是外部主张 ⇒ 原样；有据 ⇒ 原样；无据 ⇒ **整句换成自然诚实句**。"""
    if not is_external_claim(sentence):
        return ClaimRewrite(text=sentence)
    if _sentence_related(sentence, evidence_texts):
        return ClaimRewrite(text=sentence)
    if motive is not None:
        return ClaimRewrite(text=sentence, allowed=1)
    body, punct = _trailing_punct(sentence)
    if not body.strip():  # 纯标点：不处理
        return ClaimRewrite(text=sentence)
    honest = _HONEST_SENTENCES[_sentence_kind(sentence)] + punct
    return ClaimRewrite(text=honest, rewritten=1)


def rewrite_unfounded_claims(
    text: str,
    *,
    evidence_texts: Iterable[str] = (),
    motive: tuple[int, str] | None = None,
) -> ClaimRewrite:
    """把无依据的外部信息主张**整句**换成诚实句；其余句子原样保留。"""
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
