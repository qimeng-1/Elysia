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

# ── 细节模糊化（§8.4：情感核心保留，细节衰减） ────────
# 每降至下一层，细节完整度损失比例（narrative+emotion_vector 永不降）
DETAIL_DECAY_PER_LEVEL = 0.5

# ── 记忆类型 ──────────────────────────────────────────
KIND_INTERACTION = "interaction"  # 与用户交互
KIND_EXPRESSION = "expression"  # 她说出口的话
KIND_STATE = "state"  # 状态快照/经历
KIND_INTERNAL = "internal"  # 内部念头/独处生活

KINDS = (KIND_INTERACTION, KIND_EXPRESSION, KIND_STATE, KIND_INTERNAL)


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
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> MemoryRecord:
        """从 JSON 恢复（异地同步/冷恢复入口）。"""
        return MemoryRecord(
            id=d.get("id"),
            created_ts=d["created_ts"],
            kind=d["kind"],
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
        )


def default_narrative(record: MemoryRecord) -> str:
    """从记录生成一句话叙事（情感核心）——无记录时回退 content。"""
    return record.narrative or record.content


def level_rank(level: str) -> int:
    """深度等级（浅层最低）：用于晋升方向比较。"""
    return LEVELS.index(level) if level in LEVELS else LEVELS.index(LEVEL_SHALLOW)
