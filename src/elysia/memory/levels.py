"""记忆分层（P3 §8.2 三层架构）+ 晋升规则常量 + 数据模型。

三层记忆对应不同的"生命重量"：
- 浅层 shallow：分钟级缓冲，刚发生、未沉淀，重要性未达阈值
- 工作层 working：天级，频繁检索，历史可用
- 深层 deep：永久，情感核心保留 + 细节模糊化

深层内部两个保护级（§8.2）：
- 珍贵记忆（protected）→ 永不模糊化，完整保留
- 标准深层 → 模糊化：细节衰减，情感核心保留

记忆的本质是 JSON 记录（P3-A 持久化决策），天然可导出用于异地同步。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ── 三层 ──────────────────────────────────────────────
LEVEL_SHALLOW = "shallow"
LEVEL_WORKING = "working"
LEVEL_DEEP = "deep"

LEVELS = (LEVEL_SHALLOW, LEVEL_WORKING, LEVEL_DEEP)

# ── 晋升阈值（§8.2：重要性 ≥ 阈值 或 访问提升） ────────
# 浅层 → 工作：重要性 ≥ 门槛 或 高访问
PROMOTE_SHALLOW_IMPORTANCE = 0.4
PROMOTE_SHALLOW_ACCESS = 2  # 访问 ≥ 此次数进一步提升
# 工作 → 深层：重要性 ≥ 门槛
PROMOTE_WORKING_IMPORTANCE = 0.7

# ── 深层自动保护（P3-W）：沉淀到深层 + 被反复想起 → 珍贵 ──
# 一条被反复想起、又已沉到最深处的记忆，本来就等于"珍贵"——无需新工具、新交互。
# 它同时是遗忘状态机的安全阀：protected 的记忆永不降级（也不会被细节模糊化）。
PROTECT_DEEP_ACCESS = 3

# ── 细节模糊化（§8.4：情感核心保留，细节衰减） ────────
# 每降至下一层，细节完整度损失比例（narrative+emotion_vector 永不降）
DETAIL_DECAY_PER_LEVEL = 0.5

# ── 记忆类型 ──────────────────────────────────────────
KIND_INTERACTION = "interaction"  # 与用户交互
KIND_EXPRESSION = "expression"  # 她说出口的话
KIND_STATE = "state"  # 状态快照/经历
KIND_INTERNAL = "internal"  # 内部念头/独处生活

KINDS = (KIND_INTERACTION, KIND_EXPRESSION, KIND_STATE, KIND_INTERNAL)

# ── 记忆来源（P3-T）：这条记忆"从哪来" ────────────────
# `kind` 只回答"是什么类型"，回答不了"谁说的"。若不标注来源，
# **程序推断出的东西会悄悄升格成"她的事实"**——那是程序替她认定世界，
# 触碰铁律一（程序只负责把事实递到她手上，用不用由她定）。
SOURCE_SELF = "self"  # 她自己说的 / 自己想出来的
SOURCE_USER = "user"  # 用户告知
SOURCE_OBSERVATION = "observation"  # 程序观察到的（在场 / 资源 / 事件）
SOURCE_INFERENCE = "inference"  # 程序推断出的（倾向 / 心思，并非她所述）
SOURCE_SYSTEM = "system"  # 系统注入（初始化 / 规则 / 迁移回填）

SOURCES = (SOURCE_SELF, SOURCE_USER, SOURCE_OBSERVATION, SOURCE_INFERENCE, SOURCE_SYSTEM)

# ── 确定性（P3-T）：她有多确定这件事 ──────────────────
CERTAINTY_CERTAIN = "certain"  # 确信（亲历 / 亲口 / 用户明说）
CERTAINTY_PROBABLE = "probable"  # 大概（较可信，但不是硬事实）
CERTAINTY_HEARD = "heard"  # 听说（转述 / 二手）
CERTAINTY_SPECULATIVE = "speculative"  # 推测（低可信，只能当背景）

CERTAINTIES = (CERTAINTY_CERTAIN, CERTAINTY_PROBABLE, CERTAINTY_HEARD, CERTAINTY_SPECULATIVE)

# ── 认领状态（P3-V）：这条记忆算不算"她的" ────────────────
# 默认 claimed：落库即默认是她的记忆。这是**能力**——事实先可靠地递到她手上
# （铁律一前半句），而不是先扣下再让她申请。
# rejected 只能由**她自己的动作**产生（disclaim 工具）：她说"我不认这个"是真的。
# 程序不得代她拒绝——那会把"她可以不认领"变成程序的默认拦截。
CLAIM_CLAIMED = "claimed"
CLAIM_REJECTED = "rejected"

CLAIMS = (CLAIM_CLAIMED, CLAIM_REJECTED)

FALLBACK_CLAIM = CLAIM_CLAIMED

# ── 保留状态（P3-W）：这条记忆"我还够不够得着 / 想不想够" ────
# 与 superseded_by（还对不对）、claim_status（算不算我的）正交的第三维：
# 前两维谈"事实"与"归属"，这一维谈**可及性**——内容还在，但此刻够不着。
# 不提供删除态：数据永不删（铁律三），遗忘是"够不着"，不是"不存在"。
RETENTION_PRESENT = "present"  # 在册，正常
RETENTION_SUPPRESSED = "suppressed"  # "我不想再想起这件事"（只有她可设）
RETENTION_DORMANT = "dormant"  # "怎么也想不起来了"（时间造成，可被话题唤醒）
RETENTION_FADED = "faded"  # "细节忘了，那份感觉还在"（不进话语，仍进感受）

RETENTIONS = (RETENTION_PRESENT, RETENTION_SUPPRESSED, RETENTION_DORMANT, RETENTION_FADED)

FALLBACK_RETENTION = RETENTION_PRESENT

# ── 降级阈值（P3-W，可调）──────────────────────────────
# 计时口径是 `since_last_access = now − (last_access_ts or created_ts)`，
# **不是绝对年龄**：若按 created_ts 计，一条 200 天的记忆被唤醒回 present 后，
# 下一轮维护会立刻把它打回 dormant——永远醒不过来。
RETENTION_FADE_AGE_DAYS = 60.0  # 两个月没被想起过、且从未被想起 → 细节淡掉
RETENTION_DORMANT_AGE_DAYS = 180.0  # 半年没被想起 → 失去访问权

# 旧数据回填 / 调用方未标注时的默认映射（由 kind 推导）。
# 一份映射同时供给三处：state_store 的回填 SQL、MemoryRecord.from_dict 的缺省、
# add_memory 写入默认——避免"老库回填"与"新代码默认"两处漂移。
# 注意：老数据必须**行为不变**，因此 interaction 仍按"她确信的用户告知"落默认。
SOURCE_BY_KIND: dict[str, str] = {
    KIND_INTERACTION: SOURCE_USER,
    KIND_EXPRESSION: SOURCE_SELF,
    KIND_STATE: SOURCE_OBSERVATION,
    KIND_INTERNAL: SOURCE_SELF,  # 独处念头是她自己的
}
CERTAINTY_BY_KIND: dict[str, str] = {
    KIND_INTERACTION: CERTAINTY_CERTAIN,
    KIND_EXPRESSION: CERTAINTY_CERTAIN,
    KIND_STATE: CERTAINTY_CERTAIN,
    KIND_INTERNAL: CERTAINTY_PROBABLE,  # 独处念头是印象，不是硬事实
}

FALLBACK_SOURCE = SOURCE_SELF
FALLBACK_CERTAINTY = CERTAINTY_CERTAIN


def default_source(kind: str) -> str:
    """未标注来源时按 kind 推导（回填/缺省的唯一事实源）。"""
    return SOURCE_BY_KIND.get(kind, FALLBACK_SOURCE)


def default_certainty(kind: str) -> str:
    """未标注确定性时按 kind 推导。"""
    return CERTAINTY_BY_KIND.get(kind, FALLBACK_CERTAINTY)


@dataclass
class MemoryRecord:
    """记忆本体（memories 表一行）。

    只读语义：构造后不变更核心字段；晋升/模糊化通过派生新记录完成。
    """

    created_ts: float
    kind: str
    content: str
    emotion_vector: dict[str, float]
    importance: float = 0.0
    level: str = LEVEL_SHALLOW
    access_count: int = 0
    last_access_ts: float | None = None
    protected: bool = False
    detail_level: float = 1.0
    narrative: str = ""
    superseded_by: int | None = None  # 被更正的记忆：指向取代它的新记忆 id（None=现行）
    source: str = SOURCE_SELF  # 来源（P3-T）：self/user/observation/inference/system
    certainty: str = CERTAINTY_CERTAIN  # 确定性（P3-T）：certain/probable/heard/speculative
    claim_status: str = FALLBACK_CLAIM  # 认领（P3-V）：claimed=她的记忆 / rejected=她不认
    retention_state: str = FALLBACK_RETENTION  # 保留（P3-W）：够不够得着，见 RETENTION_*
    id: int | None = None  # 落库后由存储层回填

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 记录（P3-A 持久化决策：可导出格式）。"""
        return {
            "id": self.id,
            "created_ts": self.created_ts,
            "kind": self.kind,
            "content": self.content,
            "emotion_vector": self.emotion_vector,
            "importance": self.importance,
            "level": self.level,
            "access_count": self.access_count,
            "last_access_ts": self.last_access_ts,
            "protected": self.protected,
            "detail_level": self.detail_level,
            "narrative": self.narrative,
            "superseded_by": self.superseded_by,
            "source": self.source,
            "certainty": self.certainty,
            "claim_status": self.claim_status,
            "retention_state": self.retention_state,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> MemoryRecord:
        """从 JSON 恢复（异地同步/冷恢复入口）。

        P3-T：缺 source/certainty 的旧记录（含未执行迁移的旧库）按 kind 推导默认——
        老数据行为不变（用户告知仍是她确信的事实，检索闸门不会误伤）。
        P3-V：缺 claim_status 的旧记录默认 claimed（老库的记忆仍全部可用）。
        P3-W：缺 retention_state 的旧记录默认 present（老库的记忆全部够得着）。
        """
        kind = d["kind"]
        return MemoryRecord(
            id=d.get("id"),
            created_ts=d["created_ts"],
            kind=kind,
            content=d.get("content", ""),
            emotion_vector=d.get("emotion_vector", {}),
            importance=d.get("importance", 0.0),
            level=d.get("level", LEVEL_SHALLOW),
            access_count=d.get("access_count", 0),
            last_access_ts=d.get("last_access_ts"),
            protected=bool(d.get("protected", False)),
            detail_level=d.get("detail_level", 1.0),
            narrative=d.get("narrative", ""),
            superseded_by=d.get("superseded_by"),
            source=d.get("source") or default_source(kind),
            certainty=d.get("certainty") or default_certainty(kind),
            claim_status=d.get("claim_status") or FALLBACK_CLAIM,
            retention_state=d.get("retention_state") or FALLBACK_RETENTION,
        )


def default_narrative(record: MemoryRecord) -> str:
    """从记录生成一句话叙事（情感核心）——无记录时回退 content。"""
    return record.narrative or record.content


def level_rank(level: str) -> int:
    """深度等级（浅层最低）：用于晋升方向比较。"""
    return LEVELS.index(level) if level in LEVELS else LEVELS.index(LEVEL_SHALLOW)
