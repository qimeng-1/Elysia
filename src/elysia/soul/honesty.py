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

T-024（真机取证 2026-10-22 16:43）：她把阅读主张**拆成三句**——「我翻了。」/
「是一页讲海星的，说它们断了腕还能长出新的来。」/「我看着那行字发了会儿呆……」——
按句判定时 (a) 动作与 (b) 页面对象分居不同句，整轮逃逸。修法：在逐句判定**之前**
先做**整轮判定**（见 `claims.CLAIM_KINDS` 的 `reading` 一行）。

T-030（用户裁定 2026-10-22 22:2x：「扩」）：反编造从读类扩到**写类完成主张**。
真机 21:43–21:51 她连说「我刚才把那篇收尾了」「写完了，心里空了一小块」，
而 `data/her_home/` 无新文件、`thought_log` 无 `write_note`、`intentions#3/#4` 仍 pending。
判据必须**窄**：① 她还欠着一篇（payload 的 `own_words` 非空）；② 这一轮**没有**成功的
`write_note`；③ 却声称"写完了/写好了/收尾了/落笔了/搁笔了"。三条同时成立 ⇒ **整轮**
换成「其实我还没真的写完呢。」；不欠账时不拦（那可能是回忆真的旧稿，不是谎）。
进行时不在范围内。有动机（`motive`）仍放行。

T-034（说话规则融合第②步）：两类**整轮**判定的判据/依据/诚实句已收进
`elysia.soul.claims` 的 `CLAIM_KINDS` 注册表，本模块的 `rewrite_unfounded_claims`
= `claims.reconcile`（先整轮、未命中再逐句）；逐句路径（引用型「维基上写着…」）留在
本模块，由 `claims.reconcile` 回调。行为与重构前逐字等价。
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable, Sequence

from elysia.soul.claims import (
    _CITATION_RE,
    _HONEST_SENTENCES,
    ClaimRewrite,
    _content_bigrams,
    _sentence_kind,
    _trailing_punct,
    claim_is_related,
    matches,
    reconcile,
)

log = logging.getLogger("elysia.soul.honesty")

# 经历性主张（P1a-fix）：必须**同时**出现"动作"与"外部来源对象"才算
# "我读了世界"——否则"我看了你一眼/去了趟厨房/看了个视频/读过那本书"这些
# 日常动作会被误伤（用户实测 12 句日常话被改 10 句）。"书/视频/一眼"不是
# 外部信息世界，不在拦截范围。整轮判定的页面名单在 `claims.py`（更宽，但只在
# 完成态阅读动作成立时才用；逐句路径一个字都不放宽）。
_ACTION_RE = re.compile(r"(?:读|查|搜|逛|翻|看)(?:了|过|一下|一遍|到)?")
_EXTERNAL_RE = re.compile(r"(?:维基百科|维基|页面|网页|那页|这页|米游社|论坛|帖子|资料|文章|网上)")

# 句子边界（保留标点，整句替换时沿用原句的收尾标点）
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")

# 每天统计（跨日自动清零；进程内累计）
_STATS_DAY = ""
_STATS_REWRITTEN = 0
_STATS_ALLOWED = 0


def is_external_claim(sentence: str) -> bool:
    """P1a-fix：是否"我读过外部世界"的主张（动作 + 外部来源对象，或引用外部内容）。"""
    if _CITATION_RE.search(sentence):
        return True
    return bool(_ACTION_RE.search(sentence)) and bool(_EXTERNAL_RE.search(sentence))


def is_round_external_claim(text: str) -> bool:
    """T-024：**整轮**是否含"完成态阅读动作 + 页面类对象"（跨句也算）。

    例：「我翻了。是一页讲海星的……」——动作在第一句、页面对象在第二句，
    逐句判定会漏，整轮判定必须命中。判据本体在 `claims.CLAIM_KINDS` 的
    `reading` 一行（排除「我看了你一眼」这类日常动作）。
    """
    return matches("reading", text)


def is_write_completion_claim(text: str) -> bool:
    """T-030：**整轮**是否声称"写完了/写好了/收尾了/落笔了/搁笔了"（跨句也算）。

    只认**完成态**；进行时（在写/写到一半/写下来）与非完成态的裸「写完」都不算。
    是否"无据"由调用方按 `write_completion_unfounded`（她还欠着一篇 + 本轮没写）判定。
    """
    return matches("writing_done", text)


def _sentence_related(sentence: str, evidence_texts: Sequence[str]) -> bool:
    if not evidence_texts:
        return False
    content = _content_bigrams(sentence)
    if not content:  # 全是套话（如"页面翻了一堆"）⇒ 任一次成功读取都算依据
        return True
    return any(claim_is_related(sentence, text) for text in evidence_texts)


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


def rewrite_sentences_unfounded(
    text: str,
    *,
    evidence_texts: Iterable[str] = (),
    motive: tuple[int, str] | None = None,
) -> ClaimRewrite:
    """**逐句**路径：只处理"没有完成态动作"的引用型主张（整轮判定由 claims 负责）。"""
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


def rewrite_unfounded_claims(
    text: str,
    *,
    evidence_texts: Iterable[str] = (),
    motive: tuple[int, str] | None = None,
    write_completion_unfounded: bool = False,
) -> ClaimRewrite:
    """把无依据的事实主张换成诚实句；其余句子原样保留。

    T-034 起等价于 `claims.reconcile`：先去 `CLAIM_KINDS` 注册表做**整轮**判定
    （writing_done / reading，先注册先命中），未命中再走本模块的逐句路径。
    """
    return reconcile(
        text,
        evidence_texts=tuple(evidence_texts),
        motive=motive,
        write_completion_unfounded=write_completion_unfounded,
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
