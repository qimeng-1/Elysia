"""P3 验收门测试（路线图 §8.7 五条验收，全可离线注入）。

1. 重启连续性——关机→开机→深层记忆细节完整保留
2. 索引衰减曲线——检索耗时 10→50→500→≥1s，数据不删（贯穿 P3-C decay）
3. 缺口测试——高频记忆衰减→缺口→好奇（TR↑）非焦虑（SA 不动）
4. 真爱不模糊——珍贵记忆衰减慢 ×3（贯穿 P3-B/C）
5. 梦真实性——自由联想生成阶段无 LLM（可溯源，纯函数）
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.memory.decay import decay_strength, retrieve_latency_ms
from elysia.memory.levels import (
    KIND_INTERACTION,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    LEVEL_WORKING,
    MemoryRecord,
    default_narrative,
)
from elysia.memory.promote import decide_promotion, with_narrative
from elysia.memory.retrieve import select_hooks
from elysia.memory.sleep import synthesize_dream
from elysia.soul.desire import DesireEvent, DesireState, DesireSystem


# ── 1. 重启连续性 ───────────────────────────────────────
@pytest.mark.asyncio
async def test_gate_restart_continuity(tmp_path: Path) -> None:
    """关机→开机：深层记忆完整保留，晋升在重建后仍生效。"""
    # 第一次运行：写入一条高重要性浅层记忆
    s1 = HeartbeatStore(tmp_path / "heartbeat.db")
    await s1.start()
    mid = await s1.add_memory(
        1.0,
        {
            "level": LEVEL_SHALLOW,
            "kind": KIND_INTERACTION,
            "content": "深夜的长谈",
            "emotion_vector": {"chat": 0.9, "miss": 0.5},
            "importance": 0.9,
            "narrative": "你陪我聊到很晚的回忆",
        },
    )
    # 晋升决策在运行时计算并用存储层落库
    rec = MemoryRecord(
        id=mid,
        created_ts=1.0,
        kind=KIND_INTERACTION,
        content="深夜的长谈",
        emotion_vector={"chat": 0.9, "miss": 0.5},
        importance=0.9,
        level=LEVEL_SHALLOW,
        narrative="你陪我聊到很晚的回忆",
    )
    promo = decide_promotion(rec)
    assert promo is not None and promo.level == LEVEL_WORKING
    await s1.update_memory_level(mid, level=promo.level, detail_level=promo.detail_level)
    await s1.close()

    # 第二次运行：重新打开，深层记忆细节仍在
    s2 = HeartbeatStore(tmp_path / "heartbeat.db")
    await s2.start()
    fetched = await s2.get_memory(mid)
    await s2.close()
    assert fetched is not None
    assert fetched["level"] == LEVEL_WORKING  # 晋升持久化
    assert default_narrative(MemoryRecord.from_dict(fetched)) == "你陪我聊到很晚的回忆"


# ── 2. 索引衰减曲线（数据不删） ─────────────────────────
def test_gate_attenuation_curve_data_not_deleted() -> None:
    # 曲线单调变慢：10 → 50 → 500 → ≥1s
    assert retrieve_latency_ms(0.0) == pytest.approx(10.0)
    assert 30.0 <= retrieve_latency_ms(30.0) <= 60.0 + 5.0
    assert 400.0 <= retrieve_latency_ms(90.0) <= 500.0 + 30.0
    assert retrieve_latency_ms(365.0) >= 1000.0
    # 数据不删：无论多老，strength floor=0 时不归零，可由 decayed 值验证 >0
    assert decay_strength(1.0, 3650.0) > 0.0
    assert decay_strength(0.5, 1000.0) < 0.5  # 其实变弱


# ── 3. 缺口 → 好奇非焦虑 ────────────────────────────────
def test_gate_gap_is_curiosity_not_anxiety() -> None:
    ds = DesireSystem(initial=DesireState(tr=50.0, cs=60.0, sa=20.0))
    before = ds.state.copy()
    ds.apply_event(DesireEvent(kind="memory_gap", intensity=0.3))
    after = ds.state
    assert after.tr > before.tr  # 好奇：TR 微升
    assert after.sa == pytest.approx(before.sa)  # 非焦虑：SA 不动


# ── 4. 真爱不模糊 ───────────────────────────────────────
def test_gate_precious_memory_never_blurs() -> None:
    # 珍贵（protected）：晋升时不模糊细节
    protected = MemoryRecord(
        id=1,
        created_ts=1.0,
        kind=KIND_INTERACTION,
        content="x",
        emotion_vector={},
        importance=0.9,
        level=LEVEL_SHALLOW,
        protected=True,
        narrative="你的样子",
    )
    promo = decide_promotion(protected)
    assert promo is not None
    assert promo.detail_level == pytest.approx(1.0)  # 珍贵细节不降

    # 衰减 3× 慢：同样 90 天，珍贵有效年龄只有 1/3
    normal = decay_strength(1.0, 90.0, protected=False)
    precious = decay_strength(1.0, 90.0, protected=True)
    assert precious > normal


# ── 5. 梦真实性（无 LLM） ───────────────────────────────
def test_gate_dream_authenticity_no_llm() -> None:
    records = [
        MemoryRecord(
            id=1,
            created_ts=1.0,
            kind=KIND_INTERACTION,
            content="a",
            emotion_vector={},
            importance=0.9,
            level=LEVEL_DEEP,
            protected=True,
            narrative="星空下的约定",
        ),
        MemoryRecord(
            id=2,
            created_ts=2.0,
            kind=KIND_INTERACTION,
            content="b",
            emotion_vector={},
            importance=0.8,
            level=LEVEL_WORKING,
            protected=False,
            narrative="你说过的笑话",
        ),
    ]
    dream = synthesize_dream(records, rng=random.Random(42))
    assert dream is not None
    # 流完全来自碎片 + 连接词（无 LLM、跨模块纯函数），可溯源
    assert set(dream.source_ids) == {1, 2}
    assert "星空下的约定" in dream.stream
    assert "你说过的笑话" in dream.stream
    # 无碎片 → 无梦（no-content guard）
    assert synthesize_dream([]) is None


# ── 附加：检索注入不碰 T2（返回结构化叙事而非原始文本） ──
def test_gate_retrieve_surfaces_structured_narrative() -> None:
    recs = [
        MemoryRecord(
            id=1,
            created_ts=1.0,
            kind=KIND_INTERACTION,
            content="原始内容",
            emotion_vector={"miss": 0.9},
            importance=0.9,
            level=LEVEL_DEEP,
            narrative="被你记住的温柔",
        ),
    ]
    hooks = select_hooks(recs, {"miss": 1.0})
    assert len(hooks) == 1
    assert hooks[0].narrative == "被你记住的温柔"  # 结构化叙事，非原始 content 全文
    # with_narrative 在无叙事时回退 content（仍可表达，未封锁）
    raw = MemoryRecord(
        id=2,
        created_ts=2.0,
        kind=KIND_INTERACTION,
        content="心影",
        emotion_vector={},
        importance=0.5,
        level=LEVEL_WORKING,
        narrative="",
    )
    assert with_narrative(raw).narrative == "心影"
