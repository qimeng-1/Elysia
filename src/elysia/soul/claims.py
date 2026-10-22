"""T-034（说话规则融合第②步）：**事实对账**收成一张注册表。

背景：T-024（读类跨句）与 T-030（写类完成主张）此前是两段分别实现的"整轮判定"，
判据、依据、诚实句各散在 `honesty.py` 的不同分支里。本模块把它们收成**一行一类**：

    命中（claim pattern）+ 无据（evidence）+ 无动机（motive）⇒ 整轮换成该类的诚实句。

只做**纯等价重构**：判据正则、依据判定（内容词二元组）、动机放行、诚实句文本、
"整轮替换 + 原收尾标点"语义，全部照旧；逐句路径（引用型「维基上写着…」）仍在
`honesty.py`。以后新增一类主张 = 往 `CLAIM_KINDS` 加一行，不再新增函数/分支。

注册顺序 = 优先级：`writing_done` 在前、`reading` 在后——旧实现刻意让"写完了"优先于
读类（写类判据更窄、且有"她还欠着一篇"这个欠账证据）；同时命中时取先注册者。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from elysia.memory.supersede import bigrams

log = logging.getLogger("elysia.soul.claims")

# 主张与依据"相关"的最小二元组重合数（去掉套话后的内容词）
CLAIM_EVIDENCE_MIN_SHARED_BIGRAMS = 2

# ── 读类（T-024）：「完成态阅读动作 + 页面类对象」同轮出现（跨句也算）────────
# 排除项：紧跟「你/他/她/它/自己/一眼」的完成态动作不算（「我看了你一眼」）。
# 旧实现是"先删掉可排除的动作，再查是否还有动作"；等价写法 = 要求存在一处
# 动作未被这些宾语尾随（负向前瞻）。`.` 要跨行（DOTALL），否则跨句判定会漏。
_READ_ACTION = r"(?:读|看|翻|查|搜|逛|找)(?:了|过)"
_READ_ACTION_NOT_EXCLUDED = rf"{_READ_ACTION}(?!你|他|她|它|自己|一眼)"
_PAGE_OBJECT = (
    r"(?:维基百科|维基|页面|网页|那页|这页|一页|文章|资料|网上|论坛|帖子|米游社|那行字|这行字)"
)
_READING_PATTERN = re.compile(rf"(?=.*{_READ_ACTION_NOT_EXCLUDED})(?=.*{_PAGE_OBJECT})", re.DOTALL)

# ── 写类完成（T-030）：只认完成体「了/好/收尾了」，进行时不算 ─────────────
# 名单里没有「写下来/写到一半/在写」：用户明确否掉进行时守门（21:4x 她确实在写）。
# 也**不收裸「写完」**（真机完成句都带完成体）：裸「写完」会把「我这还没写完」
# 「写完这句我就接着写」误判成"声称写完"，已有 T-020 用例实测会红。
_WRITING_PATTERN = re.compile(r"(?:写完了|写好了|收尾(?:了|完)|落笔了|搁笔了)")

# 引用具体外部内容（"维基上写着/页面上说"）——决定读类诚实句用哪一句
_CITATION_RE = re.compile(
    r"(?:维基百科|维基|页面|网页|论坛|米游社|网上).{0,12}(?:写着|上说|提到|讨论)"
)

# "诚实句"（整句替换，不做短语回填——否则会出现"我没真的看过一下时间"这类语病）
_HONEST_SENTENCES: dict[str, str] = {
    "read": "其实我还没真的去翻过呢",
    "visit": "其实我还没真的去过呢",
    "cite": "其实我也还没真的读到过那页呢",
    "write": "其实我还没真的写完呢",
}

# "套话"：判相关时先去掉这些，只留主张里的**内容词**（如"埃舍尔"）
_SCAFFOLD_RE = re.compile(
    r"(?:我|人家|自己|刚才|刚刚|刚|已经|真的|其实|"
    r"读了|读过|读|看了|看过|看|查了|查|逛了|逛|去了|去|搜了|搜|翻了|翻|"
    r"看到|读到|找到|翻到|写着|上说|提到|讨论|"
    r"维基百科|维基|页面|网页|那页|这页|网上|论坛|米游社|帖子|资料|文章|"
    r"一下|一遍|一堆|几页|一些|好多|几个|了|过|的|在|从|上|里|中)"
)


@dataclass(frozen=True)
class ClaimKind:
    """一类"她的事实主张"：怎么认出来、需要什么证据、无据时换哪句诚实句。"""

    name: str
    pattern: re.Pattern[str]
    # 证据来源：reading = 外部资料/世界记忆/对话；write_note = 本轮成功的 write_note
    evidence: Literal["reading", "write_note"]
    honest_key: str


# 注册表：新增一类主张 = 加一行。顺序即优先级（先注册先命中）。
CLAIM_KINDS: tuple[ClaimKind, ...] = (
    ClaimKind(
        name="writing_done",
        pattern=_WRITING_PATTERN,
        evidence="write_note",
        honest_key="write",
    ),
    ClaimKind(
        name="reading",
        pattern=_READING_PATTERN,
        evidence="reading",
        honest_key="read",  # visit/cite 由 `_sentence_kind()` 细分（旧语义）
    ),
)

_KIND_BY_NAME: dict[str, ClaimKind] = {kind.name: kind for kind in CLAIM_KINDS}


@dataclass(frozen=True)
class ClaimRewrite:
    """一次反编造处理的结果。"""

    text: str
    rewritten: int = 0
    allowed: int = 0
    unrewritable: int = 0


def matches(name: str, text: str) -> bool:
    """某个注册类别的 pattern 是否命中（供 honesty 的两个旧判据函数复用）。"""
    return _KIND_BY_NAME[name].pattern.search(str(text or "")) is not None


def _content_bigrams(claim: str) -> set[str]:
    """主张去掉套话后的内容词二元组（"埃舍尔" → {"埃舍", "舍尔"}）。"""
    return set(bigrams(_SCAFFOLD_RE.sub("", claim)))


def claim_is_related(claim: str, evidence: str) -> bool:
    """这条主张与一段依据是否相关（内容词重合 ≥ 阈值）。"""
    content = _content_bigrams(claim)
    if not content:
        return False
    return len(content & set(bigrams(evidence))) >= CLAIM_EVIDENCE_MIN_SHARED_BIGRAMS


def _round_has_evidence(text: str, evidence_texts: Sequence[str]) -> bool:
    """读类整轮主张与依据是否相关（判定粒度跟着主张走：整轮内容词）。"""
    if not evidence_texts:
        return False
    if not _content_bigrams(text):
        return True
    return any(claim_is_related(text, evidence) for evidence in evidence_texts)


def _sentence_kind(text: str) -> str:
    """读类诚实句的细分口径（与旧实现逐字一致）：cite / visit / read。"""
    if _CITATION_RE.search(text):
        return "cite"
    if re.search(r"(?:去|逛)(?:了|过)", text):
        return "visit"
    return "read"


def _trailing_punct(sentence: str) -> tuple[str, str]:
    match = re.search(r"([。！？!?；;]+)$", sentence)
    if match is None:
        return sentence, "。"
    return sentence[: match.start()], match.group(1)


def _honest_key_for(kind: ClaimKind, text: str) -> str:
    """这一类命中时该用哪句诚实句；读类沿用旧的三分（cite/visit/read）。"""
    if kind.name == "reading":
        return _sentence_kind(text)
    return kind.honest_key


def reconcile_round(
    text: str,
    *,
    evidence_texts: Sequence[str] = (),
    motive: tuple[int, str] | None = None,
    write_completion_unfounded: bool = False,
) -> ClaimRewrite | None:
    """只做**整轮**注册表对账；没有任何一类命中 ⇒ None（调用方继续逐句路径）。

    逐类语义与旧实现逐字等价：
    - `writing_done`：pattern 命中 + `write_completion_unfounded`（她还欠着一篇且本轮没写）
      ⇒ 有动机放行（allowed=1），否则整轮换「其实我还没真的写完呢。」
    - `reading`：pattern 命中 + 依据不相关 ⇒ 有动机放行，否则整轮换该细分诚实句；
      依据相关 ⇒ 整轮放行（不看动机）。
    """
    evidence = [str(item) for item in evidence_texts if str(item or "").strip()]
    for kind in CLAIM_KINDS:
        if not kind.pattern.search(str(text or "")):
            continue
        if kind.evidence == "write_note":
            if not write_completion_unfounded:
                continue  # 不欠账/本轮真写了 ⇒ 这类主张不算无据
        elif _round_has_evidence(text, evidence):
            return ClaimRewrite(text=text)  # 读类有据 ⇒ 整轮放行
        if motive is not None:
            return ClaimRewrite(text=text, allowed=1)  # 有动机 ⇒ 有理由的谎放行
        body, punct = _trailing_punct(text)
        if not body.strip():  # 纯标点：不处理，继续看下一类 / 落逐句
            continue
        log.info("claim_reconcile kind=%s", kind.name)
        return ClaimRewrite(
            text=_HONEST_SENTENCES[_honest_key_for(kind, text)] + punct, rewritten=1
        )
    return None


def reconcile(
    text: str,
    *,
    evidence_texts: Sequence[str] = (),
    motive: tuple[int, str] | None = None,
    write_completion_unfounded: bool = False,
) -> ClaimRewrite:
    """一次完整的对账：先按注册表做整轮判定，未命中再走逐句路径（引用型主张）。

    `honesty.rewrite_unfounded_claims` 保持对外同名同义；`expression_service`
    直接调用本函数。逐句路径仍在 `honesty.py`（这里延迟导入，避免模块环）。
    """
    evidence = [str(item) for item in evidence_texts if str(item or "").strip()]
    verdict = reconcile_round(
        text,
        evidence_texts=evidence,
        motive=motive,
        write_completion_unfounded=write_completion_unfounded,
    )
    if verdict is not None:
        return verdict
    # 延迟导入：honesty 顶层 import 本模块（拿诚实句/依据判定），此处反向取值避开环。
    from elysia.soul.honesty import rewrite_sentences_unfounded

    return rewrite_sentences_unfounded(text, evidence_texts=evidence, motive=motive)
