"""S4a：遗忘阈值**防雪崩**——每条记忆的 fade/dormant 阈值带确定性抖动（±15 天）。

病根（可达性推演）：她的记忆库是同一批出生的（记忆系统 29 天前上线），阈值是同一个
常数 ⇒ `faded` 会在**同一天**一次性发生（推演：2026-12-19 一次 968 条）。
修法：偏移由 `memory_id` 的 SHA-256 决定（**不许用随机**，同一条记忆每次算出同一个值），
fade/dormant 用同一个偏移（两档间距与顺序不变），阈值常量本身不动（60/180 由用户拍板）。

取消测试：把偏移固定为 0 ⇒ `test_same_day_batch_spreads_demotion_dates` 必红
（所有记忆的降级日期又挤在同一天）。
"""

from __future__ import annotations

import pytest

import elysia.memory.levels as levels
from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    KIND_INTERACTION,
    RETENTION_DORMANT,
    RETENTION_FADED,
    RETENTION_JITTER_DAYS,
    RETENTION_PRESENT,
    SOURCE_USER,
    MemoryRecord,
    retention_age_thresholds,
    retention_jitter_days,
)
from elysia.soul.heartbeat import _demote_target

DAY = 86400.0
T0 = 1_000_000.0


def _rec(mid: int, *, created_ts: float = T0, access_count: int = 0) -> MemoryRecord:
    return MemoryRecord(
        id=mid,
        created_ts=created_ts,
        kind=KIND_INTERACTION,
        content=f"记忆 {mid}",
        emotion_vector={"chat": 0.5},
        importance=0.3,
        access_count=access_count,
        retention_state=RETENTION_PRESENT,
        narrative=f"记忆 {mid}",
        source=SOURCE_USER,
        certainty=CERTAINTY_CERTAIN,
    )


# ── 确定性 + 范围 ──────────────────────────────────────────
def test_jitter_is_deterministic_and_within_range() -> None:
    assert RETENTION_JITTER_DAYS == 15.0
    values = [retention_jitter_days(mid) for mid in range(1, 500)]
    again = [retention_jitter_days(mid) for mid in range(1, 500)]
    assert values == again, "同一条记忆每次必须算出同一个偏移（不许随机）"
    assert all(-15.0 <= value < 15.0 for value in values)
    assert len({round(value, 6) for value in values}) > 400, "偏移必须因 id 而异"
    assert retention_jitter_days(None) == 0.0


def test_thresholds_track_base_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    """抖动层跟着常量走：改基础阈值/抖动幅度都必须生效（不写死数字）。"""
    monkeypatch.setattr(levels, "RETENTION_FADE_AGE_DAYS", 30.0)
    monkeypatch.setattr(levels, "RETENTION_JITTER_DAYS", 5.0)
    fade_days, dormant_days = retention_age_thresholds(42)
    jitter = retention_jitter_days(42)
    assert -5.0 <= jitter < 5.0
    assert fade_days == pytest.approx(30.0 + jitter)
    assert dormant_days == pytest.approx(180.0 + jitter)
    assert fade_days < dormant_days, "两档间距与顺序必须保持"


# ── 同一天出生的一批记忆：降级日期要摊开 ───────────────────
def test_same_day_batch_spreads_demotion_dates() -> None:
    ids = list(range(1, 201))
    fade_days = [retention_age_thresholds(mid)[0] for mid in ids]

    # 全部落在 60 天阈值 ±15 天区间内
    assert all(45.0 <= days <= 75.0 for days in fade_days)
    # 日期不全相同（摊成斜坡）
    dates = {round(T0 + days, 3) for days in fade_days}
    assert len(dates) >= 150, f"200 条同批记忆只摊出 {len(dates)} 个降级日期（仍会雪崩）"

    # 行为层：同一批记忆在同一天（出生 + 65 天）不会一起全部 faded
    at_65d = T0 + 65 * DAY
    targets = {_demote_target(_rec(mid), at_65d) for mid in ids}
    assert RETENTION_FADED in targets
    assert None in targets, "65 天时应有记忆还没到各自阈值（否则就是同一天一起降级）"


def test_individual_threshold_is_used_by_demote_target() -> None:
    """同一个年龄、同样从未被想起：偏移为负的先降级，为正的还不到（按 id 决定）。"""
    early = next(mid for mid in range(1, 500) if retention_jitter_days(mid) < -5.0)
    late = next(mid for mid in range(1, 500) if retention_jitter_days(mid) > 5.0)
    # 60 天整：early 已过阈值（60+负偏移 < 60），late 还没到（60+正偏移 > 60）
    assert _demote_target(_rec(early), T0 + 60 * DAY) == RETENTION_FADED
    assert _demote_target(_rec(late), T0 + 60 * DAY) is None


def test_dormant_threshold_is_jittered_too() -> None:
    ids = list(range(1, 201))
    dormant_days = [retention_age_thresholds(mid)[1] for mid in ids]
    assert all(165.0 <= days <= 195.0 for days in dormant_days)
    at_180d = T0 + 180 * DAY
    targets = {_demote_target(_rec(mid), at_180d) for mid in ids}
    assert RETENTION_DORMANT in targets
    # 180 天整：正偏移的还没到 dormant（先到 faded），负偏移的已 dormant —— 不是同一天全睡
    assert RETENTION_FADED in targets, "180 天整时应有记忆还停在 faded（阈值被抖开）"


# ── 她的决定优先：suppressed 不受抖动影响 ──────────────────
def test_suppressed_is_never_demoted() -> None:
    rec = _rec(1, created_ts=T0 - 10_000 * DAY, access_count=0)
    rec.retention_state = "suppressed"
    assert _demote_target(rec, T0) is None
