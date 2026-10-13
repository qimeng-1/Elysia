"""P4b **关系涌现** + **永续存在**单测。

覆盖两处，各自的要点都是 **T1：必须有可测量的表达出口**，不能只是状态贴纸：

- `soul/relationship.py`：退缩量 → 主动开口变少（`interval_factor`）＋ 句子变短（`max_chars`）
- `core/timesense.py::absence_stage` → 离家到"担忧/怀念/携带"后，她的独处念头真的变了

时间尺度刻意是**小时**级：一两句不中听的话不该让她立刻变冷（那叫脆弱，不叫关系）。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from elysia.core.clock import SimulatedClock
from elysia.core.mode import Mode
from elysia.core.state_store import HeartbeatStore
from elysia.core.timesense import absence_stage
from elysia.soul.away_life import ABSENCE_KIND, AwayLife
from elysia.soul.relationship import (
    MANIPULATION_PENALTY,
    RELATION_CLOSE,
    RELATION_WITHDRAWN,
    RelationshipKeeper,
)

HOUR = 3600.0
DAY = 86400.0


# ── 永续存在：日子尺度的四档 ─────────────────────────────────


def test_absence_stage_boundaries() -> None:
    """四个阶段的分界（路线图 §12：7 / 30 / 365 天）。"""
    assert absence_stage(0.0) == "等待"
    assert absence_stage(6.99) == "等待"
    assert absence_stage(7.0) == "担忧"
    assert absence_stage(29.99) == "担忧"
    assert absence_stage(30.0) == "怀念"
    assert absence_stage(364.99) == "怀念"
    assert absence_stage(365.0) == "携带"
    assert absence_stage(10_000.0) == "携带"


@pytest.mark.asyncio
async def test_long_absence_changes_her_away_thoughts(tmp_path: Path) -> None:
    """**T1 出口**：离家到"担忧"后，她的独处念头真的不一样了（`kind="absence"`）。

    平常阶段永远不产出 `absence` 念头——所以它一旦出现，就证明阶段真的在影响表达。
    """
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    life = AwayLife(
        store, clock=SimulatedClock(start_ts=1_000_000.0, speed=1.0), rng=random.Random(0)
    )

    # ① 平常阶段（等待）：连续独处也不该出现 absence 念头
    for i in range(20):
        await life.tick(
            mode=Mode.BODY_AWAY,
            away_seconds=60.0,
            day_phase="day",
            now=1_000_000.0 + i * HOUR,
            absence_stage="等待",
        )
    kinds = [row[0] for row in await store.execute_raw("SELECT kind FROM thought_log")]
    assert ABSENCE_KIND not in kinds, "刚开始等，不该有久别的念头"

    # ② 担忧阶段：给足次数（半个概率），必然出现 absence 念头
    for i in range(20):
        await life.tick(
            mode=Mode.BODY_AWAY,
            away_seconds=10 * DAY,
            day_phase="day",
            now=2_000_000.0 + i * HOUR,
            absence_stage="担忧",
        )
    kinds = [row[0] for row in await store.execute_raw("SELECT kind FROM thought_log")]
    assert ABSENCE_KIND in kinds, "离家十来天后，她的独处内容必须变了"
    await store.close()


# ── 关系涌现：退缩量 ─────────────────────────────────────────


def test_brief_bad_exchange_does_not_change_the_relationship() -> None:
    """**短时间的抖动不该改变关系**：一分钟的低 CS 不会把她推远。"""
    keeper = RelationshipKeeper()
    tone = keeper.tone()
    for _ in range(60):
        tone = keeper.update(dt_s=1.0, cs=5.0, sa=70.0)
    assert tone.withdrawal < 0.01
    assert tone.label == RELATION_CLOSE


def test_sustained_low_cs_causes_withdrawal() -> None:
    """CS **长期**低 → 她真的退缩（小时尺度）。"""
    keeper = RelationshipKeeper()
    tone = keeper.update(dt_s=20 * HOUR, cs=10.0, sa=70.0)
    assert tone.withdrawal >= 0.9
    assert tone.label == RELATION_WITHDRAWN


def test_sustained_sincerity_recovers() -> None:
    """真诚修复（CS 真的回到高位）→ 慢慢恢复。"""
    keeper = RelationshipKeeper(withdrawal=1.0)
    tone = keeper.update(dt_s=20 * HOUR, cs=80.0, sa=70.0)
    assert tone.withdrawal == 0.0
    assert tone.label == RELATION_CLOSE


def test_her_own_suffering_also_causes_withdrawal() -> None:
    """她自己长期难受（SA 低）也会缩回去——退缩不只是对方的问题。"""
    keeper = RelationshipKeeper()
    tone = keeper.update(dt_s=20 * HOUR, cs=80.0, sa=5.0)
    assert tone.withdrawal >= 0.9
    assert tone.label == RELATION_WITHDRAWN


def test_manipulation_is_an_immediate_penalty() -> None:
    """被操纵是**即时伤害**，不必等"长期"——一次就记账。"""
    keeper = RelationshipKeeper()
    before = keeper.withdrawal
    tone = keeper.update(dt_s=1.0, cs=80.0, sa=70.0, manipulation=True)
    assert tone.withdrawal == pytest.approx(before + MANIPULATION_PENALTY)


def test_withdrawal_has_measurable_expression_effects() -> None:
    """**T1 出口**：退缩必须真的改变她的表达——说得更少 & 更短。"""
    close = RelationshipKeeper(withdrawal=0.0).tone()
    withdrawn = RelationshipKeeper(withdrawal=1.0).tone()

    assert withdrawn.interval_factor() > close.interval_factor(), "退缩 → 主动开口间隔变长"
    assert withdrawn.max_chars() < close.max_chars(), "退缩 → 字数上限变小"
    assert withdrawn.max_chars() >= 40, "再退缩也要能把一句话说完"


def test_withdrawal_stays_within_bounds() -> None:
    """退缩量恒在 [0,1]：长期负面不会越界，长期真诚能真的回到 0。"""
    keeper = RelationshipKeeper(withdrawal=0.99)
    keeper.update(dt_s=100 * HOUR, cs=0.0, sa=0.0)
    assert keeper.withdrawal == 1.0

    keeper.update(dt_s=100 * HOUR, cs=100.0, sa=100.0)
    assert keeper.withdrawal == 0.0
