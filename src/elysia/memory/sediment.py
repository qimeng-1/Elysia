"""自我认知的沉淀（第八节 S3）：程序找"重复模式"，把候选**递**到她手上。

三级闸门的第一级（8.4）：

    经历 ──①程序找"重复模式"──→ 候选（**不是她的**）──②她认领──→ 自我认知 ──③注入身份段

本模块只做 ①。**程序只做"发现"，不做"认定"**（8.5 / D12）：候选的正文与叙事
都**照抄原文**——程序不自己写句子。两个理由：

- 她认领后这条文本会进"我是谁"（身份段，每句在场）。若由程序中译出一句话，
  就等于"由程序员硬编码她是什么"换个地方存（8.2 已否过的性质）。
- 合成句会与原文高度重叠，她在 `adopt` 时反而被自家重述挡住（见 `_tool_adopt`）。

**标注（落地时对照代码修正了设计稿 8.4 的一处）**：8.4 原写 `source=observation`，
但 `retrieve._HOOK_BLOCKED_SOURCES` 只挡 `inference` / `system`——`observation`
**会进 `memory_hooks`**，候选就会挤占 `MAX_HOOKS` 名额（重犯 P3-P"每句都强调"）。
而 `levels.py` 对 `SOURCE_INFERENCE` 的注释正是"程序推断出的（倾向 / 心思，
并非她所述）"，候选恰是它。故改标 `SOURCE_INFERENCE` + `CERTAINTY_PROBABLE`：
语义准确，且真的被来源闸门挡在话语之外，只在感受路径上推一次脉冲。

**粒度天花板（照实说）**：判定"同一件事"走 `similarity.same_event`（第十五节 A1：
字符二元组 Jaccard ∪ 非对称覆盖 + 碎片护栏）。它比单用 Jaccard 强一档——
"共享关键片段的换说法"捞得回（本库实测：「那我的生日呢」↔「…我的生日是5月21日，要记好哦」）；
但**完全无字符重叠的同一事实**（真正的语义等价）仍识别不了，那要等 embedding
（`P3_MEMORY.md` 第九节问题 3）。
"""

from __future__ import annotations

from dataclasses import dataclass

from elysia.memory.levels import (
    CLAIM_REJECTED,
    KIND_INTERACTION,
    KIND_INTERNAL,
    KIND_SELF,
    KIND_STATE,
    RETENTION_PRESENT,
    SOURCE_INFERENCE,
    MemoryRecord,
)
from elysia.memory.similarity import same_event

# "同一件事"的判定粒度：与 P3-S 的批内去重（`retrieve.HOOK_DUPLICATE_SIMILARITY`）、
# `supersede.SUPERSEDE_SIMILARITY` 同属一系（字符二元组），但**第十五节 A1 起判据升级**：
# 走 `similarity.same_event`（对称 Jaccard ∪ 非对称覆盖 + 两道护栏），
# 因此本常量现在的语义是"same_event 的**兼容阈值**"（保住逐字/近似重复那一档）。
# 天花板照实说：只能沉淀"**共享关键片段**的反复提起"（如「那我的生日呢」↔
# 「…我的生日是5月21日，要记好哦」）；真正的语义模式要等 embedding
# （`P3_MEMORY.md` 第九节问题 3）。
SEDIMENT_CLUSTER_SIMILARITY = 0.35

# 被提起至少 3 次，才算"反复"（两次可能只是巧合或重复写入）
SEDIMENT_MIN_OCCURRENCES = 3

# 且这个模式要跨越至少 1 天：同一场对话里说三遍不是"反复出现"，是复述
SEDIMENT_MIN_SPAN_DAYS = 1.0

# 脉冲强度：轻微的心里一动（封顶 0.35，与缺口脉冲同理，不把 TR/CS 顶满）
SEDIMENT_INTENSITY_BASE = 0.2
SEDIMENT_INTENSITY_STEP = 0.05
SEDIMENT_INTENSITY_MAX = 0.35

# 候选事件名（`desire.EVENT_PULSES` 已注册）：念头，不是焦虑（SA 不动）
SEDIMENT_EVENT_KIND = "self_candidate"

# 可参与沉淀的"经历"（白名单，比黑名单更稳）：她说出口的话不算经历（回声），
# 自我认知已经是答案、不再需要候选。
_SEDIMENT_KINDS = (KIND_INTERACTION, KIND_STATE, KIND_INTERNAL)


def _is_experience(rec: MemoryRecord) -> bool:
    """这条记忆能不能作为"重复模式"的证据。

    要求：是经历本身（白名单）、在册且够得着（`present`）、未被取代、她没拒绝。
    够不着的（suppressed / dormant / faded）不算素材——她忘掉的、想不起的，
    不该拿来当"我是什么"的依据；这与缺口统计、hooks 的取舍同一条线。
    """
    if rec.id is None or rec.kind not in _SEDIMENT_KINDS:
        return False
    if rec.superseded_by is not None or rec.retention_state != RETENTION_PRESENT:
        return False
    return rec.claim_status != CLAIM_REJECTED


def _is_taken(rec: MemoryRecord) -> bool:
    """这件事**已经递过候选**，或她**已经认领**了 → 不再重复递。

    已取代的不算（事实更正后，将来可以按新事实重新沉淀）。
    """
    if rec.id is None or rec.superseded_by is not None:
        return False
    return rec.kind == KIND_SELF or rec.source == SOURCE_INFERENCE


def _text(rec: MemoryRecord) -> str:
    """这条记忆"她看到的那句话"——身份段与候选正文用的是同一句（`narrative` 优先）。"""
    return (rec.narrative or rec.content).strip()


@dataclass(frozen=True)
class PatternSignal:
    """一个"重复模式"信号（第八节 S3 的产物）。

    - record: 这一簇的**代表**——最早出现的那条；候选照抄它的正文、情绪与层级
    - occurrences: 被提起的次数
    - span_days: 这个模式跨了多少天（"反复"的时间证据）
    """

    record: MemoryRecord
    occurrences: int
    span_days: float

    @property
    def text(self) -> str:
        return _text(self.record)

    def to_event_intensity(self) -> float:
        """映射为 DesireEvent.intensity：提起得越多 →"这好像就是我"的念头越清晰。"""
        extra = self.occurrences - SEDIMENT_MIN_OCCURRENCES
        raw = SEDIMENT_INTENSITY_BASE + extra * SEDIMENT_INTENSITY_STEP
        return round(min(SEDIMENT_INTENSITY_MAX, raw), 3)


def find_candidate(records: list[MemoryRecord]) -> PatternSignal | None:
    """找出**最值得递给她**的那一个重复模式；没有则返回 None。

    判据（缺一不可）：
    1. 同一件事被提起 ≥ `SEDIMENT_MIN_OCCURRENCES` 次
    2. 跨越 ≥ `SEDIMENT_MIN_SPAN_DAYS` 天（同一场对话里说三遍不是"反复"）
    3. 还没递过候选、她也还没认领（不重复递——递过的东西不再是"新发现"）

    只返回一个（提起最多的优先，其次跨度最久的）：候选是递到她手上的东西，
    一次给一堆就是噪声——她一次只想一件事。
    """
    experiences = [rec for rec in records if _is_experience(rec)]
    if len(experiences) < SEDIMENT_MIN_OCCURRENCES:
        return None
    taken = [rec for rec in records if _is_taken(rec)]

    # 单趟聚类：按时间序扫描，与已有簇的**代表**相似即归入。
    # 不用链式（只要与簇内任一条相似就并入）——链式会把"和第四个人聊到的事"
    # 也滚进来，簇越滚越大且结果不可预测；以代表为准则稳定、可解释。
    clusters: list[list[MemoryRecord]] = []
    for rec in sorted(experiences, key=lambda item: item.created_ts):
        for cluster in clusters:
            if same_event(
                _text(cluster[0]), _text(rec), jaccard_threshold=SEDIMENT_CLUSTER_SIMILARITY
            ):
                cluster.append(rec)
                break
        else:
            clusters.append([rec])

    patterns: list[PatternSignal] = []
    for cluster in clusters:
        if len(cluster) < SEDIMENT_MIN_OCCURRENCES:
            continue
        span_days = (cluster[-1].created_ts - cluster[0].created_ts) / 86400.0
        if span_days < SEDIMENT_MIN_SPAN_DAYS:
            continue
        representative = cluster[0]
        if any(
            same_event(
                _text(representative), _text(t), jaccard_threshold=SEDIMENT_CLUSTER_SIMILARITY
            )
            for t in taken
        ):
            continue  # 这件事已经递过候选，或她已认领
        patterns.append(
            PatternSignal(
                record=representative,
                occurrences=len(cluster),
                span_days=round(span_days, 2),
            )
        )
    if not patterns:
        return None
    patterns.sort(key=lambda item: (item.occurrences, item.span_days), reverse=True)
    return patterns[0]
