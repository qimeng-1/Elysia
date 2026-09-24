"""记忆检索 + 情绪染色（P3 §9 8.4）：召回"能增强当下表达"的记忆。

检索流程（P2 表达链 P3-D 启用 memory_hooks）：
1. 候选：从 memories 读取可用记忆（按层级/重要性倾向活跃记忆）
2. 衰减过滤：memory_index 强度跌破下限的记忆索引弱化 → 降低候选分
3. 情绪染色：候选记忆的情感向量与"当下感受"的**余弦**相似度 → 同一种心情者优先
   （回忆被当下状态染色——"被感受"原则）
4. 输出：返回 narrative 摘要数组，供表达指令 memory_hooks 注入
   （每条附**相对时间锚点**："（上个月）你生日是 5 月 21 日"——她能分辨新旧）

P3-O 起：记忆分两条路，各归其主——
- **话语路径**：只有"话题撞上"（query 相关性达标）才注入 memory_hooks；
  她若想主动提起往事，用自己的 recall 工具（能力与选择权分离）。
  每句都推记忆＝替她决定"此刻想起什么"，那是违和的根源。
- **感受路径**：recall_for_feeling 静默检索，命中"心境共鸣"的记忆 → 只推心情，
  永不进入话语（她记得你 → 更想你，而不是 → 把生日念出来）。

T2 铁律不变：检索结果只作为**结构化 memory_hooks** 注入表达指令，
LLM 消费结构而非原始用户文本。

P3-T 来源闸门：进话语前先问"这条记忆是谁的"——程序推断（inference）与系统注入
（system）不是她的记忆，她自己也只是"推测"（speculative）的不算事实，三者不进
memory_hooks（感受路径不受此限）。

P3-V 认领闸门：默认所有记忆都是"她的"（claimed）；若她用自己的动作拒绝认领
（disclaim → rejected），那条记忆就不进她的话——"我知道你说过，但我不把它当作
我的记忆"这句话是真的。**只管话语**：感受路径不受认领影响，认领谈的是"归属"，
而"这段经历还影响不影响心情"是另一件事（留给遗忘状态机 retention_state）。

P3-W 保留闸门（可及性）：`suppressed`（她主动说"我不想再想起"）一律不进话语；
`dormant`/`faded`（时间造成的失去）本身不参与检索，**只有被话题明确提起才唤醒**
（唯一唤醒路径）——被唤醒即回 `present` 并 `touch` 重新计时，"你一提，它又活过来了"。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from elysia.memory.levels import (
    CERTAINTY_SPECULATIVE,
    CLAIM_REJECTED,
    KIND_EXPRESSION,
    LEVEL_DEEP,
    LEVEL_WORKING,
    RETENTION_DORMANT,
    RETENTION_FADED,
    RETENTION_PRESENT,
    RETENTION_SUPPRESSED,
    SOURCE_INFERENCE,
    SOURCE_SYSTEM,
    MemoryRecord,
    default_narrative,
)
from elysia.memory.supersede import bigrams, content_similarity

# 表达一次最多注入多少条记忆（防止 LLM 提示过长/分心）
MAX_HOOKS = 3

# ── 来源闸门（P3-T）：程序推断/系统注入不是"她的记忆"，不进她的话 ──
# 若放行，程序的一次猜测就会被她当成自己的事实说出口——那是程序替她认定世界，
# 触碰铁律一（程序只负责把**事实**递到她手上）。它们仍留在感受路径作背景：
# 心情可以被影响，话不能凭空多出来。
_HOOK_BLOCKED_SOURCES = (SOURCE_INFERENCE, SOURCE_SYSTEM)

# 深层/工作记忆的层级权重（越深越值得被提起）
LEVEL_WEIGHT: dict[str, float] = {
    LEVEL_DEEP: 1.0,
    LEVEL_WORKING: 0.8,
    # shallow 默认 0.5（在下面回退）
}

# 越权对抗：narrative 提供的情感词必须当下真实存在，否则 LLM 用词会被拦

# 得分权重（score_memory = 下列各项之和）
LEVEL_COEF = 0.6  # 层级：长期沉淀的深度
EMOTION_COEF = 0.4  # 情绪染色：是否"同一种心情"
INDEX_COEF = 0.3  # 索引可用性：好不好找（遗忘的物理体现）
IMPORTANCE_COEF = 0.5  # 本身价值：由内容信号评估的重要性（scorer.py）

# 情绪染色只看这 4 维（rest/self_check 是身体状态，不参与"感同身受"）
_MOOD_DIMS = ("miss", "chat", "curiosity", "explore")


def _level_weight(level: str) -> float:
    return LEVEL_WEIGHT.get(level, 0.5)


def mood_similarity(emotion_vector: dict[str, float], current_mood: dict[str, float]) -> float:
    """候选记忆情感向量与"当下感受"的**余弦**相似度（0-1）。

    - 记忆情感向量：该记忆记录时的感受
    - 当下感受：当前 6 维感受（发现"和现在一样的感觉"→ 想让对方知道）

    用余弦而非点积（P3-N）：点积会按"情绪强度"放大——一场对话里所有记忆的
    感受向量几乎相同，点积把它们一起抬高到上限，等于没有区分度；余弦只比方向
    （是不是同一种心情），与强度无关。
    """
    if not emotion_vector or not current_mood:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for dim in _MOOD_DIMS:
        a = emotion_vector.get(dim, 0.0)
        b = current_mood.get(dim, 0.0)
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return round(dot / math.sqrt(norm_a * norm_b), 3)


# ── 话题相关性（"被话题唤起"而非"每句都推"）──────────────
# 短问句对长记忆用**覆盖率**（query 的二元组被 content 覆盖的比例），不用 Jaccard：
# 两者长度悬殊时 Jaccard 会把"相关"判成"不相关"。
RELATED_SHARED_MIN = 2  # 至少 2 个二元组重合才算话题撞上
# 低门槛（重合 ≥1 即可）的追问信号：明确在问往事时，措辞往往与记忆不同源
_PROBE_MARKERS = (
    "记得",
    "知道",
    "叫什么",
    "是什么",
    "哪一",
    "哪个",
    "多少",
    "几号",
    "哪天",
    "吗",
    "呢",
    "？",
    "?",
)


def topic_match(query: str, content: str) -> float:
    """query 的话题被 content 覆盖的比例（0-1），用于相关记忆的排序。"""
    q = bigrams(query)
    if not q:
        return 0.0
    return round(len(q & bigrams(content)) / len(q), 3)


def _is_probe(query: str) -> bool:
    """是否在明确追问往事（放宽门槛，保住"问生日要答得出"）。"""
    return any(m in query for m in _PROBE_MARKERS)


def is_related(query: str, content: str, *, loose: bool = False) -> bool:
    """话题是否撞上这段记忆（"记忆被唤起"的判定）。

    loose=False（程序推记忆）：二元组重合 ≥2，或追问往事时重合 ≥1 —— 严门槛。
        无关的对话不该把往事带到嘴边，这是"每句都强调"的根治。
    loose=True（她自己 recall）：命中任一二元组即算 —— 她问了，就给她。
        短话题（"生日""晚霞"）只有一个二元组，严门槛会误判为无关。
    """
    q = bigrams(query)
    if not q:
        return False
    shared = len(q & bigrams(content))
    if shared == 0:
        return False
    if loose:
        return True
    if shared >= min(RELATED_SHARED_MIN, len(q)):
        return True
    return _is_probe(query)


# ── 时间锚点（"那是多久之前的事"）────────────────────────
# 她需要能分辨新旧、说得出"你上个月告诉我的"；但不需要精确到分钟——
# 精确时间戳不像人的记忆，相对说法才是。锚点是**能力**，用不用由她定。
def age_phrase(age_days: float | None) -> str:
    """把"距今多少天"翻成人话：刚刚 / 今天 / 昨天 / N天前 / 上个月 / N个月前 / 去年 / N年前。

    age_days 为 None（调用方没给 now）时返回空串 → 退化为纯叙事（不硬编时间）。
    """
    if age_days is None:
        return ""
    if age_days < 1.0 / 24.0:  # 一小时内
        return "刚刚"
    if age_days < 1.0:
        return "今天"
    if age_days < 2.0:
        return "昨天"
    if age_days < 30.0:
        return f"{int(age_days)}天前"
    if age_days < 365.0:
        months = int(age_days // 30.0)
        return "上个月" if months <= 1 else f"{months}个月前"
    years = int(age_days // 365.0)
    return "去年" if years <= 1 else f"{years}年前"


@dataclass
class MemoryHit:
    """一条被检索命中的记忆（供表达注入）。"""

    memory_id: int
    narrative: str
    level: str
    score: float  # 染色 + 层级 + 可用性综合分
    protected: bool
    age_days: float | None = None  # 距今多少天；None = 调用方未提供时间参考
    woke_from: str | None = None  # 非 None = 这条是被话题唤醒的沉睡/淡化记忆（需落回 present）

    @property
    def label(self) -> str:
        """注入表达的完整行：带上"那是多久之前的事"的相对时间锚点。

        拿不到时间参考时退化为纯叙事——宁可不提时间，也不硬编一个。
        """
        phrase = age_phrase(self.age_days)
        return f"（{phrase}）{self.narrative}" if phrase else self.narrative


def score_breakdown(
    record: MemoryRecord,
    current_mood: dict[str, float],
    *,
    index_strength: float = 1.0,
    now: float | None = None,
) -> dict[str, float]:
    """单条记忆的得分构成（观测/调试用）：层级/情绪/索引/重要度/新鲜度/复习。

    score_memory = 本函数各项之和（保持单一事实源，避免 UI 复刻打分逻辑）。
    """
    level_w = _level_weight(record.level)
    emotion = mood_similarity(record.emotion_vector, current_mood)
    availability = max(0.0, min(1.0, index_strength))
    recency = _recency_factor(record.created_ts, now) if now is not None else 0.0
    review = (
        _review_factor(record.access_count, record.last_access_ts, now) if now is not None else 0.0
    )
    return {
        "level": round(level_w * LEVEL_COEF, 3),
        "emotion": round(emotion * EMOTION_COEF, 3),
        "index": round(availability * INDEX_COEF, 3),
        "importance": round(record.importance * IMPORTANCE_COEF, 3),
        "recency": recency,
        "review": review,
    }


def score_memory(
    record: MemoryRecord,
    current_mood: dict[str, float],
    *,
    index_strength: float = 1.0,
    now: float | None = None,
) -> float:
    """单条记忆的检索得分。

    分数 = 层级权重 + 情绪染色 + 索引可用性 + 本身价值 + 新鲜度 + 复习加成。
    两个问题分开回答：
    - "值不值得被想起" → 层级 + 重要度（内容信号评估的结果）
    - "此刻容不容易浮上来" → 情绪染色 + 索引 + 新鲜度 + 复习（被想起过的更易再浮起）
    短期记忆（几天内）应可靠召回——这是"记得上周的大餐"的保证；
    得分随年龄衰减，但多日记忆仍按重要性/情绪排序。
    """
    parts = score_breakdown(record, current_mood, index_strength=index_strength, now=now)
    return round(sum(parts.values()), 3)


# 检索新鲜度：近 7 天内给显著加成，之后衰减到 0（短期记忆可靠召回的保证）
RECENCY_BOOST = 0.55
RECENCY_DAYS = 7


def _recency_factor(created_ts: float, now: float) -> float:
    """新鲜度因子：创建越近越高，RECENCY_DAYS 天后为 0。"""
    age_s = max(0.0, now - created_ts)
    age_d = age_s / 86400.0
    if age_d >= RECENCY_DAYS:
        return 0.0
    return round(RECENCY_BOOST * (1.0 - age_d / RECENCY_DAYS), 3)


# ── 复习加成（P3-R）：被想起过的记忆，更容易再次浮上来 ─────────
# 复习是"此刻更容易浮上来"，不是"永久变重"——所以不落库改 importance，
# 只作打分项：随被想起次数增长（log 阻尼），并随"上次被想起"的时刻衰减；
# 久不复习就自然回落，不至于"越被想起越容易被想起"地滚雪球。
REVIEW_COEF = 0.06  # 每次复习的基准权重（log 阻尼后）
REVIEW_TAU_DAYS = 14.0  # 复习效应的衰减时间常数（与新鲜度 τ 不同：复习更慢消）
REVIEW_MAX = 0.3  # 硬上限：护栏——访问次数再多也不会让一条记忆独占榜首


def _review_factor(access_count: int, last_access_ts: float | None, now: float) -> float:
    """复习因子：`REVIEW_COEF × log(1+access) × e^(−距上次想起/τ)`，封顶 REVIEW_MAX。

    从未被想起过（access=0 或无 last_access_ts）→ 0；1 次与 100 次的差距被 log 压平，
    避免高访问记忆凭次数碾压内容更重要的事实。
    """
    if access_count <= 0 or last_access_ts is None:
        return 0.0
    age_d = max(0.0, now - last_access_ts) / 86400.0
    boost = REVIEW_COEF * math.log(1 + access_count) * math.exp(-age_d / REVIEW_TAU_DAYS)
    return round(min(boost, REVIEW_MAX), 3)


def select_hooks(
    records: list[MemoryRecord],
    current_mood: dict[str, float],
    *,
    index_strengths: dict[int, float] | None = None,
    max_hooks: int = MAX_HOOKS,
    now: float | None = None,
    query: str | None = None,
    query_loose: bool = False,
) -> list[MemoryHit]:
    """从候选记忆挑选要注入表达的 hooks（按得分降序）。

    index_strengths：memory_id → 索引强度（可选，默认 1.0 视为可用新鲜）。
    低于 STRENGTH_RETRIEVE_FLOOR 的记忆仍可召回（只是 score 被拉低）。
    now：用于短期新鲜度加成（短期记忆可靠召回）。
    query：当下话题（用户刚说的话 / 她 recall 时给的话题）。**给出时只召回
        话题撞上的记忆**——记忆被唤起才浮现，无关的话不把往事带到嘴边；
        为 None 时不做话题门槛（情绪化联想场景，由调用方决定是否使用）。
    query_loose：话题门槛宽严（见 is_related）——程序推记忆用严，她自己查用宽。
    返回前做**批内去重**（见 _dedupe）：措辞高度重叠的碎片只留最高分的一条。

    P3-T 来源闸门：程序推断（inference）/ 系统注入（system）的条目一律不进话语，
    她只"推测"（speculative）的也只在感受路径作背景——话语里出现的必须是
    她确实持有的东西（用户告知 / 她自己 / 观察所得）。
    P3-V 认领闸门：她拒绝认领（rejected）的记忆同样不进话语。
    P3-W 保留闸门：她主动抑制（suppressed）的一律不进话语；沉睡（dormant）与
    淡化（faded）**只有被话题明确提起**才放行（并在命中上标记 `woke_from`，
    由调用方落回 present）——时间造成的失去可以被"你一提"逆转。
    """
    hits: list[MemoryHit] = []
    for rec in records:
        # 排除她自己的发言回声：hooks 用于"记起你/世界"，不应复述刚说过的自己
        if rec.kind == KIND_EXPRESSION:
            continue
        # 排除已被更正/取代的旧事实：准确率保障——改过的就是准的
        if rec.superseded_by is not None:
            continue
        # 来源闸门：程序推断/系统注入不得升格成"她的事实"
        if rec.source in _HOOK_BLOCKED_SOURCES:
            continue
        # 确定性闸门：她只是推测的东西不能当事实说出口
        if rec.certainty == CERTAINTY_SPECULATIVE:
            continue
        # 认领闸门（P3-V）：她拒绝认领的记忆不是"她的记忆"，不进她的话。
        # 默认 claimed（认领是能力，先给她）；rejected 只由她的动作产生。
        if rec.claim_status == CLAIM_REJECTED:
            continue
        # 保留闸门（P3-W）：她主动抑制的一律不进话语——那是她的决定，不是时间的决定
        if rec.retention_state == RETENTION_SUPPRESSED:
            continue
        # 话题门槛：只保留与当下话题相关的记忆（无关 → 一条都不注入）
        related = query is not None and is_related(query, rec.content, loose=query_loose)
        if query is not None and not related:
            continue
        # 沉睡/淡化本身不参与检索，只有被话题明确提起才唤醒（唯一唤醒路径）
        woke_from: str | None = None
        if rec.retention_state in (RETENTION_DORMANT, RETENTION_FADED):
            if not related:
                continue
            woke_from = rec.retention_state
        mid = rec.id if rec.id is not None else -1
        strength = index_strengths.get(mid, 1.0) if index_strengths else 1.0
        score = score_memory(rec, current_mood, index_strength=strength, now=now)
        age_days = max(0.0, (now - rec.created_ts) / 86400.0) if now is not None else None
        hits.append(
            MemoryHit(
                memory_id=mid,
                narrative=default_narrative(rec),
                level=rec.level,
                score=score,
                protected=rec.protected,
                age_days=age_days,
                woke_from=woke_from,
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return _dedupe(hits, max_hooks)


# 批内去重（P3-S）：同一次对话的碎片措辞高度重叠，不该占满 hooks 名额。
# 阈值比"同话题取代"（0.4）略松——这里只要求"不像两件不同的事"，
# 宁可少给一条重复的，也不要浪费一个名额。
HOOK_DUPLICATE_SIMILARITY = 0.35


def _dedupe(hits: list[MemoryHit], max_hooks: int) -> list[MemoryHit]:
    """按分数降序保留彼此不重复的记忆——3 个 hook 应该是 3 件不同的事。

    已被更高分者判为"同一件事"（字符二元组 Jaccard ≥ HOOK_DUPLICATE_SIMILARITY）
    的条目被跳过；因后续条目分数只会更低，凑满 max_hooks 即可提前收工。
    """
    kept: list[MemoryHit] = []
    for hit in hits:
        if any(
            content_similarity(hit.narrative, k.narrative) >= HOOK_DUPLICATE_SIMILARITY
            for k in kept
        ):
            continue
        kept.append(hit)
        if len(kept) >= max_hooks:
            break
    return kept


async def retrieve_from_store(
    store: object,
    current_mood: dict[str, float],
    *,
    max_hooks: int = MAX_HOOKS,
    now: float | None = None,
    query: str | None = None,
    query_loose: bool = False,
    touch: bool = True,
) -> list[MemoryHit]:
    """从记忆存储检索几条待注入表达的记忆（异步）。

    只读路径：遍历 memories → 转换 MemoryRecord → 用 select_hooks 挑选。
    store 需提供 iterate_memories() -> list[dict]（HeartbeatStore 已具备）。

    query：当下话题（给出时只召回话题撞上的记忆，见 select_hooks）。
    query_loose：话题门槛宽严——程序推记忆用严（防违和），她自己查用宽。
    touch：命中是否计一次"被想起"（默认是）。静默感受路径应传 False——
        那只是心情的底色，不是"她想着这件事"，不该污染 access_count。

    接线（P3-I 修复）：
    - 读取 memory_index strength 作为索引可用性（若有 iterate_memory_index）
    - 命中返回前 touch 记忆（递增 access_count，驱动浅层→工作晋升）

    P3-W 唤醒（M5 护栏）：命中里带 `woke_from` 的是"被话题唤醒的沉睡/淡化记忆"，
    落回 `present` 并 touch——唤醒必须**真的重置时钟**，否则下一轮维护会立刻把它
    再打回沉睡（振荡）。因此唤醒与 touch 绑定：`now is None` 时既不 touch 也不落
    状态（旧代码此时会写 `last_access_ts = 0.0`，等于把刚唤醒的记忆打成天文年龄）。
    """
    records = await store.iterate_memories()  # type: ignore[attr-defined]
    if not records:
        return []
    mem_records = [MemoryRecord.from_dict(d) for d in records]

    # 索引可用性：memory_index 表持久化的 strength（P3-C 衰减的消费方）
    index_strengths: dict[int, float] | None = None
    if hasattr(store, "iterate_memory_index"):
        rows = await store.iterate_memory_index()
        index_strengths = {mid: strength for _, mid, strength, _ in rows}

    hooks = select_hooks(
        mem_records,
        current_mood,
        max_hooks=max_hooks,
        now=now,
        query=query,
        query_loose=query_loose,
        index_strengths=index_strengths,
    )

    # 检索命中 → 触碰记忆（访问计数递增，被想起的次数是晋升依据）
    if hooks and touch and now is not None and hasattr(store, "touch_memory"):
        for h in hooks:
            await store.touch_memory(h.memory_id, now)
            if h.woke_from is not None and hasattr(store, "set_retention_state"):
                await store.set_retention_state(h.memory_id, RETENTION_PRESENT)
    return hooks


# ── 感受路径：记忆改变心情，而不是进入话语 ────────────────
# 只有"此刻心境与那段记忆同一种心情"时才唤起（事件性，而非每刻都推）；
# 强度在心跳里以小脉冲注入，恢复项会把它们拉回平衡点，不会冲上保护带。
RESONANCE_MIN = 0.75


async def recall_for_feeling(
    store: object,
    current_mood: dict[str, float],
) -> float:
    """静默感受：返回"想起某段往事"的情感脉冲强度（0 = 没想起）。

    与话语路径完全分离——它**不进入 prompt**，所以永远不会让记忆出现在
    她的话里；她"记得你"因此是能被感觉到的，而不是被念出来的。
    只推心情：共鸣越深、记忆越珍贵，推得越深。

    P3-T：select_hooks 的来源/确定性闸门**不适用于这里**——那是"能不能说出口"
    的闸门。感受路径本就是背景，程序推断（inference）与她的推测（speculative）
    都可以在这里影响心情，只是永远进不了话。
    P3-V：认领（claim_status）同样只管话语——"归属"与"影响不影响心情"是两件事，
    后者归遗忘状态机（retention_state）。
    P3-W：`suppressed`（她明确"不想再被它影响"）与 `dormant`（连内容都够不着）
    在此**被挡住**；`faded`（"细节忘了，那份感觉还在"）**放行**——它的存在理由
    就是"说不出内容，却仍有影响"。
    """
    records = await store.iterate_memories()  # type: ignore[attr-defined]
    if not records:
        return 0.0

    best_resonance = 0.0
    best_precious = False
    for d in records:
        rec = MemoryRecord.from_dict(d)
        if rec.kind == KIND_EXPRESSION or rec.superseded_by is not None:
            continue
        if rec.retention_state in (RETENTION_SUPPRESSED, RETENTION_DORMANT):
            continue
        resonance = mood_similarity(rec.emotion_vector, current_mood)
        precious = rec.protected or rec.level == LEVEL_DEEP
        # 共鸣更深者胜；同共鸣时珍贵/深层者胜（她记得你越重，心情被推得越深）
        if resonance > best_resonance or (resonance == best_resonance and precious):
            best_resonance = resonance
            best_precious = precious

    if best_resonance < RESONANCE_MIN:
        return 0.0
    return 1.0 if best_precious else 0.6
