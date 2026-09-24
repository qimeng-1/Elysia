"""同话题 / "同一件事" 的判定口径（第十五节 A1）：**单一入口**，供三处复用。

背景是 P3-S 的诚实结论：**对称 Jaccard 对"换说法的同一事实"识别力有限**——
本库实测同义重述仅 0.26~0.33，且**调阈值无法根治**（调低即误杀共享措辞的不同事件）。

本模块的解法不是新模型，而是**换口径**：在对称 Jaccard 之外补一条**非对称覆盖**通路——
短的一方（"那我的生日呢"这类关键词骨架）被长的一方覆盖到，就算同一件事。
本库实测："覆盖 ≥0.7 且 Jaccard <0.35" 的夹缝里只有 12 对，正是 Jaccard 漏掉、
覆盖能捞回的那一类（`docs/P3_MEMORY_WORKLOG.md` 15.1 表）。

**但只补这一条会出事**：任何碎片都会被"完全包含"判成同话题（同库实测"不对哦" ⊂ 长句
→ 覆盖恒 1.0）。故覆盖通路另加**两道护栏**：绝对重合二元组数 ≥ `MIN_SHARED_BIGRAMS`、
较短一方二元组数 ≥ `MIN_SHORTER_BIGRAMS`——"完全包含"不再自动成立。

**消费者各持阈值**（本模块只统一「实现」，不统一「阈值」）：误判代价不对称——
`retrieve._dedupe` / `sediment` 误判只是"少给一条内容"，而 `supersede` 误判会
**作废一条真实事实**，故 `supersede` 明确不参与本口径（仍用对称 Jaccard 0.4）。

**零依赖、纯本地启发式、确定性可测**——与 `scorer.py` 同一自律；
真正的语义级判定要等 embedding（`docs/P3_MEMORY.md` 第九节问题 3，**未引入**）。
字符二元组的粒度与归一由 `supersede.bigrams` 提供（同粒度、同局限，避免两处实现分叉）。
"""

from __future__ import annotations

from elysia.memory.supersede import bigrams, content_similarity

# ── 覆盖通路阈值（第十五节 A1；数值由本库只读实测定，见 worklog 15.1）──
# 覆盖门槛：α 类样本实测 0.70~0.83 全部落在门槛之上
COVERAGE_THRESHOLD = 0.7
# 绝对重合护栏：β 类"不对哦"仅重合 2 个二元组 → 挡在门外
MIN_SHARED_BIGRAMS = 3
# 体量护栏：短方二元组数 < 此值即视为"碎片"，不构成"一件事"
# （"不对哦" 2 个、"我今天吃了火锅" 6 个、"那我的生日呢" 5 个）
MIN_SHORTER_BIGRAMS = 5


def _overlap(a: str, b: str) -> tuple[int, int] | None:
    """（较短一方的二元组数, 两方重合的二元组数）；任一为空文本 → None。

    覆盖口径的**唯一算式来源**：`coverage` 与 `same_event` 都从这里取数，
    避免"同一个口径两处实现"（与抽出 `bigrams` 同一理由）。
    """
    ga, gb = bigrams(a), bigrams(b)
    if not ga or not gb:
        return None
    short, long_ = (ga, gb) if len(ga) <= len(gb) else (gb, ga)
    return len(short), len(short & long_)


def coverage(a: str, b: str) -> float:
    """非对称覆盖（Szymkiewicz–Simpson）：较短一方的二元组被另一方覆盖的比例。

    与 Jaccard 的差别：长度悬殊时 Jaccard 会把"相关"判成"不相关"
    （分母含长方的全部内容），覆盖只看"短的那方说过的东西有没有被接住"。
    """
    got = _overlap(a, b)
    if got is None:
        return 0.0
    short_n, shared = got
    return round(shared / short_n, 3)


def query_coverage(query: str, text: str) -> float:
    """query 的话题被 text 覆盖的比例（**固定按 query 归一**）。

    与 `coverage` 的方向差别是有意的：这里问的是"这句话在说的事，这段记忆覆盖了多少"，
    所以分母只能是 query（问句比记忆短是常态，但语义方向不能反过来）。
    `retrieve.topic_match` 的落点即此。
    """
    q = bigrams(query)
    if not q:
        return 0.0
    return round(len(q & bigrams(text)) / len(q), 3)


def same_event(
    a: str,
    b: str,
    *,
    jaccard_threshold: float,
    coverage_threshold: float = COVERAGE_THRESHOLD,
    min_shared: int = MIN_SHARED_BIGRAMS,
    min_shorter: int = MIN_SHORTER_BIGRAMS,
) -> bool:
    """两段内容是否在说"同一件事"（第十五节 A1 的单一判据）。

    三条通路，命中任一即为是：
    1. **对称 Jaccard ≥ `jaccard_threshold`**：保住"逐字/近似重复"（现状行为不退化）
    2. **覆盖 ≥ `coverage_threshold`**：捞回"共享关键片段的换说法"（Jaccard 漏掉的那类）
    3. 覆盖通路的两道护栏（`min_shorter` / `min_shared`）：挡住"碎片被完全包含"

    阈值由调用方给（各消费者误判代价不同，见模块 docstring）——
    本题只统一**实现**，不统一**阈值**。
    """
    if content_similarity(a, b) >= jaccard_threshold:
        return True
    got = _overlap(a, b)
    if got is None:
        return False
    short_n, shared = got
    if short_n < min_shorter or shared < min_shared:
        return False
    return shared / short_n >= coverage_threshold
