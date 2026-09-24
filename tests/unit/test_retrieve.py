"""P3-D 记忆检索 + 表达注入单测：染色挑选 + memory_hooks 注入。"""

from __future__ import annotations

from pathlib import Path

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    CERTAINTY_PROBABLE,
    CERTAINTY_SPECULATIVE,
    CLAIM_CLAIMED,
    CLAIM_REJECTED,
    KIND_EXPRESSION,
    KIND_INTERACTION,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    LEVEL_WORKING,
    SOURCE_INFERENCE,
    SOURCE_SELF,
    SOURCE_SYSTEM,
    SOURCE_USER,
    MemoryRecord,
)
from elysia.memory.retrieve import (
    MAX_HOOKS,
    REVIEW_MAX,
    MemoryHit,
    age_phrase,
    is_related,
    mood_similarity,
    recall_for_feeling,
    retrieve_from_store,
    score_breakdown,
    score_memory,
    select_hooks,
    topic_match,
)


def _rec(
    *,
    level: str = LEVEL_WORKING,
    emotion: dict[str, float] | None = None,
    narrative: str = "一段记忆叙事",
    mid: int | None = 1,
    created_ts: float = 1.0,
    importance: float = 0.7,
    content: str = "内容",
    access_count: int = 1,
    last_access_ts: float | None = None,
    source: str = SOURCE_SELF,
    certainty: str = CERTAINTY_CERTAIN,
    claim_status: str = CLAIM_CLAIMED,
) -> MemoryRecord:
    return MemoryRecord(
        id=mid,
        created_ts=created_ts,
        kind=KIND_INTERACTION,
        content=content,
        emotion_vector=emotion if emotion is not None else {"chat": 0.5},
        importance=importance,
        level=level,
        access_count=access_count,
        last_access_ts=last_access_ts,
        protected=False,
        narrative=narrative,
        source=source,
        certainty=certainty,
        claim_status=claim_status,
    )


# ── 情绪染色 ────────────────────────────────────────────
def test_mood_similarity_matches_overlapping_dims() -> None:
    mem = {"miss": 0.8, "chat": 0.2}
    mood = {"miss": 0.9, "chat": 0.0}
    score = mood_similarity(mem, mood)
    assert score > 0.0  # 有共同感受 → 正匹配
    # 无关维度不算分
    assert mood_similarity({"explore": 0.8}, {"miss": 0.9}) == 0.0


def test_mood_similarity_is_cosine_not_magnitude() -> None:
    """情绪强度大 ≠ 更匹配：同一方向应得同一分（否则聊天时全体通吃）。"""
    mood = {"miss": 0.4, "chat": 0.6}
    strong = {"miss": 0.8, "chat": 1.2}
    weak = {"miss": 0.2, "chat": 0.3}
    assert mood_similarity(strong, mood) == pytest.approx(mood_similarity(weak, mood), abs=0.01)


def test_mood_similarity_empty() -> None:
    assert mood_similarity({}, {"miss": 0.9}) == 0.0
    assert mood_similarity({"miss": 0.8}, {}) == 0.0


# ── 检索得分 ────────────────────────────────────────────
def test_score_memory_privildeges_dep_and_mood() -> None:
    deep = score_memory(_rec(level=LEVEL_DEEP, emotion={"miss": 0.9}), {"miss": 1.0})
    shallow = score_memory(_rec(level=LEVEL_SHALLOW, emotion={"chat": 0.2}), {"miss": 1.0})
    assert deep > shallow  # 深层 + 高匹配 → 分更高


def test_score_memory_index_availability() -> None:
    fresh = score_memory(_rec(), current_mood={}, index_strength=0.9)
    faded = score_memory(_rec(), current_mood={}, index_strength=0.1)
    assert fresh > faded  # 索引强的记忆更易召回


def test_score_breakdown_includes_importance() -> None:
    """得分的"本身价值"项必须存在：重要度要真的参与打分，否则形同摆设。"""
    parts = score_breakdown(_rec(importance=0.8), {"chat": 0.5})
    assert set(parts) == {"level", "emotion", "index", "importance", "recency", "review"}
    assert parts["importance"] == pytest.approx(0.4)


def test_score_memory_fact_beats_small_talk() -> None:
    """回归：同类、同心情、同时间下，有意义的事（高重要度）必须压过闲聊。"""
    mood = {"chat": 0.8, "explore": 0.5}
    now = 10 * 86400.0
    small_talk = _rec(
        mid=1, level=LEVEL_SHALLOW, importance=0.37, created_ts=now - 3600, emotion=mood
    )
    fact = _rec(mid=2, level=LEVEL_DEEP, importance=0.97, created_ts=now - 3600, emotion=mood)
    assert score_memory(fact, mood, now=now) > score_memory(small_talk, mood, now=now)


def test_score_memory_recency_boosts_recent_over_old() -> None:
    """短期记忆可靠召回：同样条件下，昨天的大餐该比一月前的记忆分高。"""
    now = 10 * 86400.0  # 第 10 天
    yesterday = _rec(
        created_ts=now - 1 * 86400.0,
        level=LEVEL_SHALLOW,
        emotion={"chat": 0.5},
        narrative="昨天的大餐",
    )
    month_ago = _rec(
        created_ts=now - 30 * 86400.0,
        level=LEVEL_SHALLOW,
        emotion={"chat": 0.5},
        narrative="一个月前的事",
    )
    recent = score_memory(yesterday, {"chat": 0.5}, now=now)
    stale = score_memory(month_ago, {"chat": 0.5}, now=now)
    assert recent > stale  # 新鲜度让短期记忆被优先召回


# ── 复习加成（被想起过的记忆，更容易再次浮上来）──────────
def test_review_boost_rises_with_access_count() -> None:
    """复习 → 分升：同一条记忆被想起的次数越多，复习加成越高。"""
    now = 10 * 86400.0
    once = _rec(mid=1, access_count=1, last_access_ts=now - 60.0)
    many = _rec(mid=2, access_count=5, last_access_ts=now - 60.0)
    assert score_memory(many, {"chat": 0.5}, now=now) > score_memory(once, {"chat": 0.5}, now=now)
    assert score_breakdown(once, {"chat": 0.5}, now=now)["review"] > 0.0


def test_review_boost_zero_when_never_recalled() -> None:
    """从没被想起过就没有复习加成（access=0，或没有"上次想起"的时刻）。"""
    now = 10 * 86400.0
    assert score_breakdown(_rec(access_count=0), {"chat": 0.5}, now=now)["review"] == 0.0
    assert score_breakdown(_rec(access_count=3), {"chat": 0.5}, now=now)["review"] == 0.0


def test_review_boost_decays_when_not_revisited() -> None:
    """衰减后回落：久不再被想起，复习加成自然消退（防"越被想起越容易想起"滚雪球）。"""
    now = 100 * 86400.0
    recent = score_breakdown(
        _rec(mid=1, access_count=5, last_access_ts=now - 3600.0), {"chat": 0.5}, now=now
    )["review"]
    stale = score_breakdown(
        _rec(mid=2, access_count=5, last_access_ts=now - 60 * 86400.0), {"chat": 0.5}, now=now
    )["review"]
    assert recent > stale


def test_review_boost_is_capped() -> None:
    """硬上限：访问次数再多也不会凭次数独占榜首（正反馈护栏）。"""
    now = 10 * 86400.0
    huge = _rec(access_count=10**6, last_access_ts=now)
    assert score_breakdown(huge, {"chat": 0.5}, now=now)["review"] <= REVIEW_MAX


def test_select_hooks_excludes_own_expression_echo() -> None:
    """检索不应把她自己刚说过的话当"记起你"注入，避免重启重复她上次的话。"""
    own_echo = MemoryRecord(
        id=2,
        created_ts=2.0,
        kind=KIND_EXPRESSION,
        content="上次我说的话",
        emotion_vector={"chat": 0.9},
        importance=0.5,
        level=LEVEL_SHALLOW,
        access_count=1,
        protected=False,
        narrative="上次我说的话",
    )
    hooks = select_hooks([own_echo, _rec(mid=1, narrative="真正的经历")], {"chat": 1.0})
    # 她的发言回声被排除，只保留真实经历
    assert [h.memory_id for h in hooks] == [1]


# ── 挑选 hooks ──────────────────────────────────────────
def test_select_hooks_sorts_by_score_and_caps() -> None:
    recs = [
        _rec(mid=1, level=LEVEL_DEEP, emotion={"miss": 0.9}, narrative="珍视"),
        _rec(mid=2, level=LEVEL_SHALLOW, emotion={"chat": 0.1}, narrative="弱"),
        _rec(mid=3, level=LEVEL_WORKING, emotion={"miss": 0.5}, narrative="中"),
        _rec(mid=4, level=LEVEL_WORKING, emotion={"miss": 0.6}, narrative="中2"),
    ]
    hooks = select_hooks(recs, {"miss": 1.0})
    assert len(hooks) == MAX_HOOKS  # 截断到上限
    assert hooks[0].memory_id == 1  # 最高分排最前
    scores = [h.score for h in hooks]
    assert scores == sorted(scores, reverse=True)


def test_select_hooks_returns_memoryhit_shape() -> None:
    hooks = select_hooks([_rec(narrative="想你了")], {"miss": 1.0})
    assert len(hooks) == 1
    h = hooks[0]
    assert isinstance(h, MemoryHit)
    assert h.narrative == "想你了"
    assert h.level == LEVEL_WORKING
    assert h.protected is False


def test_select_hooks_empty() -> None:
    assert select_hooks([], {"miss": 1.0}) == []


# ── 批内去重（3 个 hook 应该是 3 件不同的事）─────────────
def test_select_hooks_dedupes_same_conversation_fragments() -> None:
    """同一次对话的碎片措辞高度重叠 → 只留最高分的一条，名额让给别的记忆。"""
    kept_frag = _rec(
        mid=1,
        level=LEVEL_DEEP,
        content="你的生日是5月21日，要记好哦",
        narrative="你的生日是5月21日，要记好哦",
    )
    dupe_frag = _rec(
        mid=2,
        level=LEVEL_DEEP,
        content="你的生日是5月21日，要记好",
        narrative="你的生日是5月21日，要记好",
    )
    other = _rec(mid=3, level=LEVEL_WORKING, content="你喜欢看晚霞", narrative="你喜欢看晚霞")
    hooks = select_hooks([kept_frag, dupe_frag, other], {"chat": 0.5})
    assert [h.memory_id for h in hooks] == [1, 3]


def test_select_hooks_dedupe_keeps_distinct_memories() -> None:
    """去重不能误伤：说的是不同的事，就该各自占一个名额。"""
    recs = [
        _rec(mid=1, level=LEVEL_DEEP, content="你的生日是5月21日", narrative="你的生日是5月21日"),
        _rec(mid=2, level=LEVEL_DEEP, content="你喜欢看晚霞", narrative="你喜欢看晚霞"),
        _rec(mid=3, level=LEVEL_DEEP, content="你昨天去吃了顿大餐", narrative="你昨天去吃了顿大餐"),
    ]
    hooks = select_hooks(recs, {"chat": 0.5})
    assert len(hooks) == MAX_HOOKS  # 三件不同的事，一条都不该被去重掉
    assert [h.memory_id for h in hooks] == [1, 2, 3]


# ── 时间锚点（她能分辨新旧、说得出"你上个月告诉我的"）──────
def test_age_phrase_buckets() -> None:
    """相对时间分档：刚刚 / 今天 / 昨天 / N天前 / 上个月 / N个月前 / 去年 / N年前。"""
    assert age_phrase(None) == ""  # 没给时间参考 → 不提时间
    assert age_phrase(0.0) == "刚刚"
    assert age_phrase(1.0 / 24.0 - 1e-6) == "刚刚"  # 一小时内
    assert age_phrase(0.5) == "今天"
    assert age_phrase(1.0) == "昨天"
    assert age_phrase(1.9) == "昨天"
    assert age_phrase(2.0) == "2天前"
    assert age_phrase(29.9) == "29天前"
    assert age_phrase(30.0) == "上个月"
    assert age_phrase(59.0) == "上个月"
    assert age_phrase(60.0) == "2个月前"
    assert age_phrase(200.0) == "6个月前"
    assert age_phrase(365.0) == "去年"
    assert age_phrase(700.0) == "去年"
    assert age_phrase(730.0) == "2年前"


def test_select_hooks_carries_relative_time_anchor() -> None:
    """hooks 注入时带相对时间锚点：她因此判断得出"这是新事还是旧事"。"""
    now = 10 * 86400.0
    recs = [
        _rec(mid=1, created_ts=now - 0.5 * 3600, narrative="刚发生的事"),
        _rec(mid=2, created_ts=now - 3 * 86400.0, narrative="三天前的事"),
    ]
    hooks = select_hooks(recs, {"chat": 0.5}, now=now)
    labels = {h.memory_id: h.label for h in hooks}
    assert labels[1] == "（刚刚）刚发生的事"
    assert labels[2] == "（3天前）三天前的事"


def test_select_hooks_no_anchor_without_now() -> None:
    """没给 now 就不硬编时间：退化为纯叙事（老调用方行为不变）。"""
    hooks = select_hooks([_rec(narrative="一段记忆")], {"chat": 0.5})
    assert hooks[0].age_days is None
    assert hooks[0].label == "一段记忆"


# ── P3-T 来源 / 确定性闸门（进话语前先问"这是谁的记忆"）──────
def test_select_hooks_blocks_inference_and_system() -> None:
    """程序推断 / 系统注入不是她的记忆：分再高也不进 hooks。"""
    records = [
        _rec(mid=1, narrative="用户说过的往事", source=SOURCE_USER),
        _rec(mid=2, narrative="程序推断她很想你", source=SOURCE_INFERENCE),
        _rec(mid=3, narrative="系统注入的初始设定", source=SOURCE_SYSTEM),
    ]
    assert [h.memory_id for h in select_hooks(records, {"chat": 0.5})] == [1]


def test_select_hooks_blocks_speculative_only() -> None:
    """推测不能当事实说出口；大概/听说仍可说（只是不如确信硬）。"""
    records = [
        _rec(mid=1, narrative="说不准的猜测", certainty=CERTAINTY_SPECULATIVE),
        _rec(mid=2, narrative="大致可信的推想", certainty=CERTAINTY_PROBABLE),
    ]
    assert [h.memory_id for h in select_hooks(records, {"chat": 0.5})] == [2]


@pytest.mark.asyncio
async def test_feeling_path_keeps_inference_as_background(tmp_path: Path) -> None:
    """感受路径是背景，不受话语闸门限制：推断+推测的记忆不进话，但仍能影响心情。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    await store.add_memory(
        1.0,
        {
            "level": LEVEL_DEEP,
            "kind": KIND_INTERACTION,
            "content": "推断出的牵挂",
            "emotion_vector": {"miss": 0.95, "chat": 0.1},
            "importance": 0.6,
            "protected": True,
            "narrative": "程序推断她很想你",
            "source": SOURCE_INFERENCE,
            "certainty": CERTAINTY_SPECULATIVE,
        },
    )
    hooks = await retrieve_from_store(store, {"miss": 1.0})
    feeling = await recall_for_feeling(store, {"miss": 1.0})
    await store.close()
    assert hooks == []  # 不进话语
    assert feeling == 1.0  # 但作为背景推心情（珍贵深层记忆 → 强脉冲）


# ── P3-V 认领闸门（默认是她的；她拒绝认领的不进话语）────────
def test_select_hooks_default_claim_is_usable() -> None:
    """默认 claimed：没标注认领状态的记忆照常进话语——能力先递到她手上。"""
    assert [h.memory_id for h in select_hooks([_rec(mid=1)], {"chat": 0.5})] == [1]


def test_select_hooks_blocks_rejected_claim() -> None:
    """她拒绝认领的记忆不是"她的记忆"：不进她的话。"""
    records = [
        _rec(mid=1, narrative="她认下的往事", claim_status=CLAIM_CLAIMED),
        _rec(mid=2, narrative="她拒绝认领的往事", claim_status=CLAIM_REJECTED),
    ]
    assert [h.memory_id for h in select_hooks(records, {"chat": 0.5})] == [1]


@pytest.mark.asyncio
async def test_feeling_path_ignores_claim(tmp_path: Path) -> None:
    """认领只管话语：拒绝认领的记忆仍可作背景推心情（归属 ≠ 影响心情）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    await store.add_memory(
        1.0,
        {
            "level": LEVEL_DEEP,
            "kind": KIND_INTERACTION,
            "content": "被拒绝认领的往事",
            "emotion_vector": {"miss": 0.95, "chat": 0.1},
            "importance": 0.6,
            "protected": True,
            "narrative": "被拒绝认领的往事",
            "claim_status": CLAIM_REJECTED,
        },
    )
    hooks = await retrieve_from_store(store, {"miss": 1.0})
    feeling = await recall_for_feeling(store, {"miss": 1.0})
    await store.close()
    assert hooks == []  # 不进话语
    assert feeling == 1.0  # 但作为背景推心情


# ── 基于存储检索（异步，落库往返） ──────────────────────
@pytest.mark.asyncio
async def test_retrieve_from_store(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    # 期待的情感匹配记忆
    await store.add_memory(
        2.0,
        {
            "level": LEVEL_DEEP,
            "kind": KIND_INTERACTION,
            "content": "深深想念的回忆",
            "emotion_vector": {"miss": 0.9, "chat": 0.2},
            "importance": 0.9,
            "protected": True,
            "narrative": "你在深夜陪我聊天的回忆",
        },
    )
    await store.add_memory(
        3.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "普通事",
            "emotion_vector": {"chat": 0.1, "miss": 0.05},
            "importance": 0.3,
            "narrative": "普通日常",
        },
    )
    hooks = await retrieve_from_store(store, {"miss": 1.0})
    await store.close()
    assert len(hooks) == 2
    # 深层次高匹配的排最前
    assert hooks[0].narrative == "你在深夜陪我聊天的回忆"
    # 校验 hook 命中确实带叙事
    assert {"你在深夜陪我聊天的回忆", "普通日常"} == {h.narrative for h in hooks}


@pytest.mark.asyncio
async def test_retrieve_touches_access_count(tmp_path: Path) -> None:
    """检索命中要递增 access_count（P3-B 浅层→工作晋升依据）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "昨天的经历",
            "emotion_vector": {"chat": 0.9},
            "importance": 0.3,
            "protected": False,
            "narrative": "昨天的经历",
        },
    )
    hooks = await retrieve_from_store(store, {"chat": 1.0}, now=10.0)
    rec = await store.get_memory(mid)
    assert hooks  # 有命中
    assert rec is not None
    assert rec["access_count"] == 1  # 命中一次 → 计数 +1
    assert rec["last_access_ts"] == 10.0
    await store.close()


@pytest.mark.asyncio
async def test_repeated_recall_raises_score(tmp_path: Path) -> None:
    """端到端：同一条记忆被想起越多，它下次的得分越高（复习反哺检索）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "一起看晚霞",
            "emotion_vector": {"chat": 0.9},
            "importance": 0.5,
            "protected": False,
            "narrative": "一起看晚霞",
        },
    )
    first = await retrieve_from_store(store, {"chat": 1.0}, now=1000.0)
    second = await retrieve_from_store(store, {"chat": 1.0}, now=1000.0)
    third = await retrieve_from_store(store, {"chat": 1.0}, now=1000.0)
    await store.close()
    assert first[0].score < second[0].score < third[0].score


@pytest.mark.asyncio
async def test_retrieve_uses_index_strength(tmp_path: Path) -> None:
    """索引 strength 参与打分：同记忆索引弱 → 分更低（P3-C 遗忘消费方）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "一段往事",
            "emotion_vector": {"chat": 0.9},
            "importance": 0.5,
            "protected": False,
            "narrative": "一段往事",
        },
    )
    idx_id = await store.add_memory_index(
        memory_id=mid, path_key="main", strength=1.0, emotions=0.0
    )
    # 未衰减：默认可用性 1.0
    hooks = await retrieve_from_store(store, {"chat": 1.0}, now=10.0)
    assert hooks[0].score > 0.8
    # 索引衰减到 floor 以下：可用性被拉低
    await store.decay_memory_index([(idx_id, 0.1)])
    hooks2 = await retrieve_from_store(store, {"chat": 1.0}, now=10.0)
    assert hooks2[0].score < hooks[0].score
    await store.close()


# ── 话题相关性（记忆被唤起才浮现，不再每句都推）──────────
_BIRTHDAY = "我在告诉你哦，我的生日是5月21日，要记好哦"


def test_topic_match_uses_query_coverage() -> None:
    """短问句对长记忆用覆盖率：问生日必须判为相关（Jaccard 会低估）。"""
    assert topic_match("我的生日是哪天", _BIRTHDAY) > 0.3


def test_is_related_rejects_unrelated_topic() -> None:
    """无关的闲聊不该把往事带到嘴边——违和感的根源就在这里。"""
    assert is_related("今天天气怎么样", _BIRTHDAY) is False


def test_is_related_accepts_topic_overlap() -> None:
    assert is_related("我的生日是哪天", _BIRTHDAY) is True


def test_is_related_accepts_probe_wording() -> None:
    """措辞不同源的追问（"你还记得…吗"）也要能唤起——保住"问生日答得出"。"""
    assert is_related("你还记得我什么时候生日吗", _BIRTHDAY) is True


def test_select_hooks_query_gates_related_only() -> None:
    birthday = _rec(mid=1, level=LEVEL_DEEP, content=_BIRTHDAY, narrative=_BIRTHDAY)
    dinner = _rec(
        mid=2,
        level=LEVEL_DEEP,
        content="你昨天说去吃了顿大餐",
        narrative="你昨天说去吃了顿大餐",
    )
    recs = [birthday, dinner]
    # 无 query：不做话题门槛（情绪化联想场景）
    assert len(select_hooks(recs, {"chat": 0.5})) == 2
    # 话题是生日 → 只唤起生日那条
    assert [h.memory_id for h in select_hooks(recs, {"chat": 0.5}, query="我的生日是哪天")] == [1]
    # 话题无关 → 一条都不注入（宁可不提，也不硬塞）
    assert select_hooks(recs, {"chat": 0.5}, query="今天天气怎么样") == []


def test_select_hooks_loose_query_accepts_wordy_topic() -> None:
    """她自己 recall 时用宽门槛：话题措辞不同源（"问问晚霞那件事"）也要能唤起。"""
    sunset = _rec(mid=1, level=LEVEL_DEEP, content="你上次说喜欢晚霞", narrative="你上次说喜欢晚霞")
    # 程序推记忆的严门槛：单一重合不算撞上（避免违和）
    assert select_hooks([sunset], {"chat": 0.5}, query="问问晚霞那件事") == []
    loose = select_hooks([sunset], {"chat": 0.5}, query="问问晚霞那件事", query_loose=True)
    assert [h.memory_id for h in loose] == [1]


def test_select_hooks_query_skips_own_echo_and_superseded() -> None:
    """话题门控不改变既有排除规则：她的回声与已被取代的旧事实仍不召回。"""
    echo = MemoryRecord(
        id=9,
        created_ts=1.0,
        kind=KIND_EXPRESSION,
        content="我记得你的生日是5月21日",
        emotion_vector={"chat": 0.5},
        importance=0.9,
        level=LEVEL_DEEP,
        access_count=1,
        protected=False,
        narrative="我记得你的生日是5月21日",
    )
    stale = MemoryRecord(
        id=8,
        created_ts=1.0,
        kind=KIND_INTERACTION,
        content="我的生日是11月11日",
        emotion_vector={"chat": 0.5},
        importance=0.9,
        level=LEVEL_DEEP,
        access_count=1,
        protected=False,
        narrative="我的生日是11月11日",
        superseded_by=10,
    )
    hooks = select_hooks([echo, stale], {"chat": 0.5}, query="我的生日是哪天")
    assert hooks == []


# ── 感受路径：记忆改变心情，而不是进入话语 ───────────────
@pytest.mark.asyncio
async def test_recall_for_feeling_needs_resonance(tmp_path: Path) -> None:
    """心境不合的记忆不推心情（事件性唤起，而非每刻都推）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "一起看晚霞",
            "emotion_vector": {"chat": 0.9},
            "importance": 0.5,
            "protected": False,
            "narrative": "一起看晚霞",
        },
    )
    # 当下心境与记忆同向 → 唤起
    assert await recall_for_feeling(store, {"chat": 1.0}) > 0.0
    # 心境不合（不同维度）→ 不推
    assert await recall_for_feeling(store, {"miss": 1.0}) == 0.0
    await store.close()


@pytest.mark.asyncio
async def test_recall_for_feeling_precious_pushes_deeper(tmp_path: Path) -> None:
    """珍贵/深层记忆推得更深（她记得你越重，心情被推得越深）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "普通日常",
            "emotion_vector": {"chat": 0.9},
            "importance": 0.3,
            "protected": False,
            "narrative": "普通日常",
        },
    )
    plain = await recall_for_feeling(store, {"chat": 1.0})
    await store.add_memory(
        2.0,
        {
            "level": LEVEL_DEEP,
            "kind": KIND_INTERACTION,
            "content": "被你记住的温柔",
            "emotion_vector": {"chat": 0.9},
            "importance": 0.9,
            "protected": True,
            "narrative": "被你记住的温柔",
        },
    )
    precious = await recall_for_feeling(store, {"chat": 1.0})
    await store.close()
    assert precious > plain


@pytest.mark.asyncio
async def test_recall_for_feeling_does_not_touch_access(tmp_path: Path) -> None:
    """静默感受不是"她想着这件事"：不该污染 access_count（晋升依据）。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    mid = await store.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "一起看晚霞",
            "emotion_vector": {"chat": 0.9},
            "importance": 0.5,
            "protected": False,
            "narrative": "一起看晚霞",
        },
    )
    assert await recall_for_feeling(store, {"chat": 1.0}) > 0.0
    rec = await store.get_memory(mid)
    await store.close()
    assert rec is not None
    assert rec["access_count"] == 0
