"""W2 真机审计：她的真实状态 → 开口间隔必须覆盖**多个档**，而不是又一个窄带。

**默认跳过**（与 `test_real_data_thresholds.py` 同一开关）：
    $env:ELYSIA_AUDIT_REAL = "1"
    .venv\\Scripts\\python.exe -m pytest tests/audit/test_real_data_rhythm.py

审计两件事（T1「造好了没插电」）：
1. **覆盖宽度**：把近 40000 拍真实状态输入新映射，间隔必须跨多个档；同一批数据
   交给旧实现（180/600 二选一）只会有 2 个值（约 3/4 压在 600s 那一档）。
2. **对照回放**：同一批真实心跳序列分别喂给旧/新判定，统计"她会多久开一次口"，
   用来回答"改完她会变安静吗、一天大约开口多少次"。

取消测试：还原旧 `should_speak` ⇒ 本文件（需 `ELYSIA_AUDIT_REAL=1` 运行）红。
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
from pathlib import Path
from typing import Any

import pytest

from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import (
    ExpressionService,
    expression_interval_s,
    expression_willingness,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "heartbeat.db"

pytestmark = pytest.mark.skipif(
    os.environ.get("ELYSIA_AUDIT_REAL") != "1" or not REAL_DB.exists(),
    reason="真机数据审计：设 ELYSIA_AUDIT_REAL=1 且存在 data/heartbeat.db 才跑",
)

BANDS: tuple[tuple[float, float], ...] = (
    (60.0, 120.0),
    (120.0, 180.0),
    (180.0, 300.0),
    (300.0, 600.0),
    (600.0, 1200.0),
    (1200.0, 1800.0),
)

# 旧实现的"常量 × 倍数"（reluctance 在真实回放里按 1.0——当前退缩量 0.0）
OLD_ACTIVE_S = 180.0
OLD_QUIET_S = 600.0


def _ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True, timeout=5.0)


def _load_beats(n: int = 40000) -> list[dict[str, Any]]:
    """最近 n 拍真实心跳，按时间升序：feelings / will / brain_action / desire 全取。"""
    con = _ro()
    try:
        rows = list(
            con.execute(
                "SELECT ts, payload FROM heartbeats WHERE beat_type='soul' "
                "ORDER BY id DESC LIMIT ?",
                (n,),
            )
        )
    finally:
        con.close()
    rows.reverse()
    beats: list[dict[str, Any]] = []
    for ts, payload in rows:
        data = json.loads(payload)
        feelings = data.get("feelings") or {}
        will = data.get("will") or {}
        desire = data.get("desire") or {}
        beats.append(
            {
                "ts": float(ts),
                "chat": float(feelings.get("chat", 0.0)),
                "miss": float(feelings.get("miss", 0.0)),
                "explore": float(feelings.get("explore", 0.0)),
                "curiosity": float(feelings.get("curiosity", 0.0)),
                "rest": float(feelings.get("rest", 0.0)),
                "self_check": float(feelings.get("self_check", 0.0)),
                "style": float(will.get("thought_style", 0.0)),
                "direction": str(will.get("direction", "still")),
                "strength": float(will.get("strength", 0.0)),
                "anim_bias": float(will.get("anim_bias", 0.0)),
                "action": str(data.get("brain_action") or "none"),
                "tr": float(desire.get("tr", 45.0)),
                "cs": float(desire.get("cs", 60.0)),
                "sa": float(desire.get("sa", 20.0)),
            }
        )
    return beats


def _to_output(beat: dict[str, Any]) -> BrainOutput:
    return BrainOutput(
        desire=DesireState(tr=beat["tr"], cs=beat["cs"], sa=beat["sa"]),
        feelings=FeelingState(
            chat=beat["chat"],
            miss=beat["miss"],
            explore=beat["explore"],
            curiosity=beat["curiosity"],
            rest=beat["rest"],
            self_check=beat["self_check"],
        ),
        will=WillOutput(
            direction=beat["direction"],
            strength=beat["strength"],
            thought_style=beat["style"],
            anim_bias=beat["anim_bias"],
        ),
        action=beat["action"],
    )


def _willingness(beat: dict[str, Any]) -> float:
    return expression_willingness(
        chat=beat["chat"],
        miss=beat["miss"],
        explore=beat["explore"],
        rest=beat["rest"],
        thought_style=beat["style"],
    )


def _new_interval(beat: dict[str, Any]) -> float:
    return expression_interval_s(
        chat=beat["chat"],
        miss=beat["miss"],
        explore=beat["explore"],
        rest=beat["rest"],
        thought_style=beat["style"],
    )


def _old_interval(beat: dict[str, Any]) -> float:
    active = beat["action"] in ("think_active", "think_quiet") and beat["style"] > 0
    return OLD_ACTIVE_S if active else OLD_QUIET_S


def _bands(values: list[float]) -> dict[str, float]:
    total = len(values)
    return {
        f"{lo:.0f}-{hi:.0f}s": round(100.0 * sum(1 for x in values if lo <= x < hi) / total, 1)
        for lo, hi in BANDS
    }


def _quantile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
    return ordered[index]


def test_real_states_cover_multiple_rhythm_bands() -> None:
    """同一批真实状态：旧实现只有 2 个间隔值且集中一档；新映射跨多个档。"""
    beats = _load_beats()
    if len(beats) < 1000:
        pytest.skip("心跳样本不足")
    new_values = [_new_interval(b) for b in beats]
    old_values = [_old_interval(b) for b in beats]
    new_bands = _bands(new_values)
    old_bands = _bands(old_values)
    willingness = [_willingness(b) for b in beats]
    new_range = (
        f"[{min(new_values):.0f}, {max(new_values):.0f}] 中位={statistics.median(new_values):.0f}"
    )
    print(
        f"\n真实状态映射（n={len(beats)}）："
        f"\n  新 瞬时分布 %: {new_bands}"
        f"\n  旧 瞬时分布 %: {old_bands}"
        f"\n  新 range={new_range}；旧 distinct={len({round(v, 1) for v in old_values})}"
        f"\n  willingness: 中位={statistics.median(willingness):.3f} "
        f"p10={_quantile(willingness, 0.1):.3f} p90={_quantile(willingness, 0.9):.3f}"
    )

    # 取消测试：旧实现被还原时，这一行必红（旧只有 180/600 两个值）
    assert len({round(v, 1) for v in old_values}) == 2, "对照口径变了？旧实现应只有 180/600"
    assert max(old_bands.values()) > 70.0, "旧实现应集中在单一档（600s）"
    # 新映射：横跨多档、且没有哪一档吃掉六成以上
    covered = [name for name, share in new_bands.items() if share >= 2.0]
    assert len(covered) >= 4, f"新映射只覆盖 {len(covered)} 个档（≥2%）：{new_bands}"
    assert max(new_bands.values()) < 60.0, f"新映射仍集中在单档：{new_bands}"
    assert min(new_values) < 120.0, "快端不可达（真实状态下从没有 ≤2 分钟的间隔）"
    assert max(new_values) > 1200.0, "慢端不可达（真实状态下从没有 >20 分钟的间隔）"


def test_real_state_replay_predicts_varied_gaps() -> None:
    """同一批真实心跳回放：旧/新各自的开口间隔与"每天约多少次"对照。"""
    beats = _load_beats()
    if len(beats) < 1000:
        pytest.skip("心跳样本不足")
    window_s = beats[-1]["ts"] - beats[0]["ts"]
    if window_s <= 0:
        pytest.skip("心跳时间窗异常")

    # 新实现：真的走运行时 `should_speak`（含 EMA 平滑），不是重写一份公式
    service = ExpressionService(None, None)  # type: ignore[arg-type]
    new_gaps: list[float] = []
    last: float | None = None
    for beat in beats:
        if service.should_speak(_to_output(beat), beat["ts"], False):
            if last is not None:
                new_gaps.append(beat["ts"] - last)
            last = beat["ts"]

    # 旧实现：按它的阈值公式在**同一批时间戳**上回放
    old_intervals = [_old_interval(b) for b in beats]
    old_gaps: list[float] = []
    last = None
    for interval, beat in zip(old_intervals, beats, strict=True):
        if last is None or beat["ts"] - last >= interval:
            if last is not None:
                old_gaps.append(beat["ts"] - last)
            last = beat["ts"]

    assert new_gaps and old_gaps
    new_n = len(new_gaps) + 1
    old_n = len(old_gaps) + 1
    new_daily = new_n / window_s * 86400.0
    old_daily = old_n / window_s * 86400.0
    fast_share = sum(1 for g in new_gaps if g < 180.0) / len(new_gaps)
    slow_share = sum(1 for g in new_gaps if g > 300.0) / len(new_gaps)
    print(
        f"\n真实回放（{window_s / 3600.0:.2f}h，n={len(beats)} 拍）："
        f"\n  旧：中位={statistics.median(old_gaps):.0f}s p10={_quantile(old_gaps, 0.1):.0f} "
        f"p90={_quantile(old_gaps, 0.9):.0f} 开口/日≈{old_daily:.0f} "
        f"distinct={len({round(v / 10.0) for v in old_gaps})}"
        f"\n  新：中位={statistics.median(new_gaps):.0f}s p10={_quantile(new_gaps, 0.1):.0f} "
        f"p90={_quantile(new_gaps, 0.9):.0f} 开口/日≈{new_daily:.0f} "
        f"distinct(10s)={len({round(v / 10.0) for v in new_gaps})}"
        f"\n  新：<180s 快档占 {fast_share:.1%}；>300s 慢档占 {slow_share:.1%}"
    )

    # 旧实现的最小间隔就是 180s ⇒ 不可能有更快的开口；新映射必须有一批"她真想说"的快档
    assert all(g >= OLD_ACTIVE_S - 1e-6 for g in old_gaps), "对照口径变了？旧实现不应 <180s"
    assert fast_share > 0.05, f"新实现没有快档（<180s 只占 {fast_share:.1%}）⇒ 仍是被拉长的节拍器"
    assert slow_share > 0.05, f"新实现没有慢档（>300s 只占 {slow_share:.1%}）⇒ 仍是窄带"
    assert len({round(v / 10.0) for v in new_gaps}) >= 10, "开口间隔的种类仍然太少"
    assert 100.0 < statistics.median(new_gaps) < 600.0, "中位间隔落在不合理区间"
    # 量化给用户的估算：她在真实状态分布下一天大约开口多少次（内部冲动；用户触发另行）
    assert 50.0 < new_daily < 1200.0, f"每日开口估算 {new_daily:.0f} 次明显不合理"
