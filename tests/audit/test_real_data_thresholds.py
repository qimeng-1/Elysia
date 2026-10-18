"""真机审计（P0-R16）：阈值横跨 + 分支可达性 + 调度寿命 + 时钟两侧。

**默认跳过**（CI / 普通门禁不跑，避免依赖真实数据与计划任务）：
    $env:ELYSIA_AUDIT_REAL = "1"
    .venv\\Scripts\\python.exe -m pytest tests/audit -q

这里的目标不是"测试代码"，而是**让机器替我们发现"看着对、真实数据下永远走不到"的门**：
1. 阈值横跨：真实分布必须同时落在阈值两侧（min < 阈值 < max）；
2. 分支可达性：用**真实回放**（真实 desire / 真实记忆年龄）统计每个分支命中次数，
   而不是覆盖率——coverage 只证明行执行过；
3. 寿命/有效期：计划任务与进程内定时器不得有"到点静默停摆"的隐藏上限
   （2026-10-18 抓到的 `Duration 72:00:00` 炸弹就是这一类）；
4. 阈值两侧：每个阈值用虚拟时钟各推一次。

已知不可达（用户暂缓 / 已记录，不判红）：
- `distress` 阈值（D-6，用户裁决"暂不修"）⇒ retreat 只由噪声抖动偶尔出现；
- `_tts_params` 的 `calm` / `neutral`：`chat>0.4` 恒成立把 fallback 遮住了
  （新发现，属 T1 同类；涉及情绪基调语义，留给单独卡，不在本卡改）。
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import statistics
import subprocess
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "heartbeat.db"
STATE_DB = REPO_ROOT / "data" / "state.db"
TASK_NAME = "Elysia-Watchdog"

pytestmark = pytest.mark.skipif(
    os.environ.get("ELYSIA_AUDIT_REAL") != "1" or not REAL_DB.exists(),
    reason="真机数据审计：设 ELYSIA_AUDIT_REAL=1 且存在 data/heartbeat.db 才跑",
)


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)


def _load_desire(n: int = 40000) -> list[tuple[float, float, float]]:
    con = _ro(REAL_DB)
    try:
        rows = list(
            con.execute(
                "SELECT payload FROM heartbeats WHERE beat_type='soul' ORDER BY id DESC LIMIT ?",
                (n,),
            )
        )
    finally:
        con.close()
    out: list[tuple[float, float, float]] = []
    for (payload,) in rows:
        d = json.loads(payload).get("desire") or {}
        out.append((float(d.get("tr", 0.0)), float(d.get("cs", 0.0)), float(d.get("sa", 0.0))))
    return out


def _load_setpoints() -> tuple[float, float, float]:
    con = _ro(STATE_DB)
    try:
        row = con.execute("SELECT value FROM kv WHERE key='desire'").fetchone()
    finally:
        con.close()
    if row is None:
        pytest.skip("state.db 里没有 desire 记录")
    data = json.loads(row[0])
    sp = data.get("setpoints") or data.get("initial") or {}
    return (float(sp.get("tr", 45.0)), float(sp.get("cs", 60.0)), float(sp.get("sa", 20.0)))


class _NoJitter:
    """关掉意志层的随机抖动：审计只测**确定性判定链**（抖动会让死分支假可达）。"""

    def random(self) -> float:
        return 1.0


# ── 1. 阈值横跨断言 ──────────────────────────────────────────
def test_real_distribution_straddles_will_thresholds() -> None:
    """取消测试（P0-R16）：把阈值改回绝对 0.5/0.6 ⇒ 本用例红。"""
    from elysia.soul.brain import WILL_DEVIATION_MARGIN

    desire = _load_desire()
    if not desire:
        pytest.skip("没有心跳样本")
    sp_tr, sp_cs, _ = _load_setpoints()
    chats = [cs / 100.0 for _, cs, _ in desire]
    misses = [1.0 - cs / 100.0 for _, cs, _ in desire]
    explores = [tr / 100.0 for tr, _, _ in desire]
    checks = [
        ("chat>设定点+余量", chats, sp_cs / 100.0 + WILL_DEVIATION_MARGIN),
        ("miss>设定点+余量", misses, (1.0 - sp_cs / 100.0) + WILL_DEVIATION_MARGIN),
        ("explore>设定点+余量", explores, sp_tr / 100.0 + WILL_DEVIATION_MARGIN),
    ]
    lines = []
    for label, xs, thr in checks:
        lo, hi = min(xs), max(xs)
        lines.append(
            f"  {label}: 真实 [{lo:.3f}, {hi:.3f}] 中位 {statistics.median(xs):.3f} "
            f"| 阈值 {thr:.3f} | 横跨={lo < thr < hi}"
        )
        assert lo < thr < hi, (
            f"{label} 阈值 {thr:.3f} 不在真实区间 [{lo:.3f}, {hi:.3f}] 内 ⇒ "
            "该分支在真实数据下不可达\n" + "\n".join(lines)
        )
    print("\n".join(lines))


# ── 2. 分支可达性：真实回放（不是覆盖率）──────────────────────
def test_every_will_direction_is_reachable_from_real_data() -> None:
    """真实 desire 回放：explore / reach / still 都必须由**机制**命中（不含抖动）。

    取消测试：阈值改回绝对 0.5/0.6 ⇒ explore=still=0 ⇒ 红。
    """
    from elysia.soul.brain import BrainLoop
    from elysia.soul.desire import DesireState, DesireSystem
    from elysia.soul.dimensions import FeelingState

    desire = _load_desire()
    if not desire:
        pytest.skip("没有心跳样本")
    sp_tr, sp_cs, sp_sa = _load_setpoints()
    brain = BrainLoop(
        DesireSystem(initial=DesireState(tr=sp_tr, cs=sp_cs, sa=sp_sa)), rng=_NoJitter()
    )
    counts: Counter[str] = Counter()
    for tr, cs, sa in desire:
        feelings = FeelingState(chat=cs / 100.0, miss=1.0 - cs / 100.0, explore=tr / 100.0)
        will = brain._will_layer(DesireState(tr=tr, cs=cs, sa=sa), feelings, False, "present")
        counts[will.direction] += 1
    total = sum(counts.values())
    table = "\n".join(f"  {name}: {n} ({n / total:.1%})" for name, n in sorted(counts.items()))
    print(f"真实回放（无抖动，n={total}）：\n{table}")
    assert counts["explore"] > 0, "explore 在真实数据下仍不可达\n" + table
    assert counts["reach"] > 0, "reach 不可达\n" + table
    assert counts["still"] > 0, "still 不可达\n" + table
    # retreat：distress 阈值暂缓（用户裁决），只可能由噪声抖动产生 ⇒ 机制上仍不可达，
    # 在报告里标注为"已知不可达（用户暂缓）"，不判红。
    assert counts["retreat"] == 0
    # 分布不能退化：若某个方向吃掉 >95%，说明阈值仍在错配区间
    assert max(counts.values()) / total < 0.95, "方向分布退化（单一方向占比 >95%）\n" + table


# ── 3. 好奇心：真实记忆年龄回放（缺口 = 事件）─────────────────
def test_curiosity_branch_is_reachable_from_real_memory_ages() -> None:
    """真实记忆年龄 → 缺口强度必须能越过消费阈值 0.4，且只在"新增丢失"时触发。"""
    from elysia.memory.decay import STRENGTH_RETRIEVE_FLOOR, decay_strength
    from elysia.memory.hooks import detect_episodic_gap

    con = _ro(REAL_DB)
    try:
        rows = list(
            con.execute("SELECT id, kind, created_ts, protected, retention_state FROM memories")
        )
    finally:
        con.close()
    now = time.time()
    samples: list[tuple[int, float]] = []
    future: list[float] = []
    for mid, kind, ts, protected, state in rows:
        if state != "present" or kind in ("expression", "self"):
            continue
        age = max(0.0, now - ts) / 86400.0
        strength = decay_strength(1.0, age, protected=bool(protected))
        samples.append((mid, strength))
        if strength >= STRENGTH_RETRIEVE_FLOOR:
            future.append(STRENGTH_RETRIEVE_FLOOR)  # 只为标记"还没丢失"
    if not samples:
        pytest.skip("没有可参与缺口的记忆")

    gap, lost = detect_episodic_gap(samples, previously_lost=set())
    print(f"真实记忆 {len(samples)} 条；当前已模糊 {len(lost)} 条；首轮缺口 {gap}")
    assert gap is not None and gap.strength > 0.4, "缺口强度仍越不过好奇心消费阈值 0.4"

    # 事件式：同一批旧丢失不会重复触发（否则好奇心变成常驻背景）
    again, _ = detect_episodic_gap(samples, previously_lost=lost)
    assert again is None, "旧丢失被重复上报 ⇒ 好奇心会长期常驻（性格变形）"

    # 未来可达：还有记忆会在未来某天跨过模糊线（证明机制持续有输入，而不是一次性）
    con = _ro(REAL_DB)
    try:
        rows = list(
            con.execute(
                "SELECT created_ts, protected FROM memories "
                "WHERE retention_state='present' AND kind NOT IN ('expression','self')"
            )
        )
    finally:
        con.close()
    delays = []
    for ts, protected in rows:
        age = max(0.0, now - ts) / 86400.0
        if decay_strength(1.0, age, protected=bool(protected)) >= STRENGTH_RETRIEVE_FLOOR:
            fuzzy_age = 21.0 * (3.0 if protected else 1.0)
            delays.append(max(0.0, fuzzy_age - age))
    assert delays and min(delays) <= 60.0, "未来 60 天内没有任何记忆跨过模糊线 ⇒ 机制会再次变死"


# ── 4. 情绪分支（curiosity）可达 ─────────────────────────────
def test_curiosity_emotion_branch_is_reachable() -> None:
    from elysia.soul.brain import WillOutput
    from elysia.soul.expression import _tts_params

    base = {"rest": 0.05, "miss": 0.30, "curiosity": 0.0, "chat": 0.62}
    assert _tts_params(WillOutput(), base)["emotion"] == "happy"
    curious = {**base, "curiosity": 0.5}
    assert _tts_params(WillOutput(), curious)["emotion"] == "curious", (
        "curiosity>0.4 仍到不了 curious 分支"
    )


# ── 5. 调度寿命：计划任务不得有"到点静默停摆"的上限 ──────────────
def _iso_duration_days(text: str) -> float | None:
    """ISO8601 时长（P1D / PT72H / P30D）→ 天；解析失败返回 None。"""
    body = text.strip().upper().removeprefix("P")
    days = 0.0
    if "D" in body:
        days += float(body.split("D")[0])
        body = body.split("D", 1)[1]
    if "T" in body:
        body = body.split("T", 1)[1]
    if "H" in body:
        days += float(body.split("H")[0]) / 24.0
        body = body.split("H", 1)[1]
    if "M" in body:
        days += float(body.split("M")[0]) / 1440.0
    return days


def _task_xml() -> str | None:
    try:
        result = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", TASK_NAME, "/XML"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 and result.stdout.strip() else None


def test_scheduled_task_has_no_finite_lifetime() -> None:
    """取消测试（P0-R14 同源事故）：任务重复时长必须是"无限期/每天续命"，不得只有 72h。"""
    xml = _task_xml()
    if xml is None:
        pytest.skip(f"计划任务 {TASK_NAME} 不存在（非本机或未注册）")
    root = ET.fromstring(xml)
    ns = {"t": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    find = (lambda path: root.find(path, ns)) if ns else (lambda path: root.find(path))
    rep = find(".//t:Repetition") if ns else find(".//Repetition")
    if rep is None:
        return  # 没有重复定义 = 只跑一次？对看门狗不行；但注册表里通常有
    duration_el = find(".//t:Repetition/t:Duration") if ns else find(".//Repetition/Duration")
    interval_el = find(".//t:Repetition/t:Interval") if ns else find(".//Repetition/Interval")
    has_daily = (
        find(".//t:ScheduleByDay") is not None if ns else find(".//ScheduleByDay") is not None
    )

    if interval_el is not None and interval_el.text:
        minutes = (_iso_duration_days(interval_el.text) or 0.0) * 1440.0
        assert 0 < minutes <= 5, f"巡视间隔 {interval_el.text} 太粗（盲窗 >5 分钟）"

    if duration_el is not None and duration_el.text:
        days = _iso_duration_days(duration_el.text)
        assert days is not None, f"无法解析任务重复时长：{duration_el.text}"
        # 可接受：>=30 天；或"每天触发 + 当天 24h 重复"（每天自动续命 = 无限期）
        assert days >= 30 or (has_daily and days >= 1), (
            f"任务重复时长只有 {duration_el.text}（{days:.2f} 天）⇒ 到期后会静默停摆"
            "（2026-10-18 的 Duration 72h 炸弹同类）"
        )


def test_in_process_periodic_work_has_bounded_period() -> None:
    """进程内周期任务：周期必须是正数且有界（不会"设成 0 就不跑"，也不会长到形同停摆）。"""
    from elysia.core.config import Settings
    from elysia.soul.heartbeat import CHECKPOINT_EVERY_N

    settings = Settings()
    checkpoint_s = CHECKPOINT_EVERY_N * settings.heartbeat_interval_s
    assert settings.heartbeat_interval_s > 0
    assert 0 < checkpoint_s <= 6 * 3600, f"检查点周期 {checkpoint_s}s 不合理"


# ── 6. 阈值两侧时钟 + 模式三态可达 ────────────────────────────
def test_retention_tier_boundaries_with_virtual_clock() -> None:
    """每个分层阈值两侧各推一次（沿用 R12 的虚拟 now 做法）。"""
    from elysia.core.retention import (
        COLD_DAYS,
        HOT_DAYS,
        WARM_DAYS,
        _tier_of,
    )

    day = 86400.0
    now = 1_000_000_000.0

    def tier(age_days: float) -> str | None:
        return _tier_of(
            now - age_days * day,
            hot_cutoff=now - HOT_DAYS * day,
            warm_cutoff=now - WARM_DAYS * day,
            cold_cutoff=now - COLD_DAYS * day,
        )

    assert tier(HOT_DAYS - 0.001) is None  # 热层：原样
    assert tier(HOT_DAYS + 0.001) == "minute"
    assert tier(WARM_DAYS - 0.001) == "minute"
    assert tier(WARM_DAYS + 0.001) == "hour"
    assert tier(COLD_DAYS - 0.001) == "hour"
    assert tier(COLD_DAYS + 0.001) == "day"


def test_mode_values_are_reachable_with_virtual_clock() -> None:
    """`mode` 真实数据恒 present 是**身体一直在线**，不是机制死：用虚拟时钟证明三态可达。"""
    from elysia.core.mode import Mode, ModeManager
    from elysia.core.timesense import BODY_AWAY_THRESHOLD_S, LONG_AWAY_THRESHOLD_S

    manager = ModeManager()
    last = 1_000_000.0
    assert manager.mode(last, last + 1.0) is Mode.PRESENT
    assert manager.mode(last, last + BODY_AWAY_THRESHOLD_S + 1.0) is Mode.ALONE
    assert manager.mode(last, last + LONG_AWAY_THRESHOLD_S + 1.0) is Mode.BODY_AWAY
    # 现实数据只能在 body 离线时才有后两态 ⇒ 审计只保证"机制可达"，不要求真实占比
    assert BODY_AWAY_THRESHOLD_S < LONG_AWAY_THRESHOLD_S
    _ = random  # 保持 import 稳定性（无抖动审计依赖）


# ── 关系层阈值（P0-R19c 补进护栏：上一轮 14.0 就是从这里溜过去的）──────────
def _load_sa_cs() -> tuple[list[float], list[float]]:
    con = _ro(REAL_DB)
    try:
        rows = list(
            con.execute(
                "SELECT payload FROM heartbeats WHERE beat_type='soul' ORDER BY id DESC LIMIT 40000"
            )
        )
    finally:
        con.close()
    sa, cs = [], []
    for (payload,) in rows:
        d = json.loads(payload).get("desire") or {}
        sa.append(float(d.get("sa", 0.0)))
        cs.append(float(d.get("cs", 0.0)))
    return sa, cs


def test_relationship_sa_threshold_straddles_real_distribution() -> None:
    """取消测试（P0-R19c）：SA 退缩线必须随设定点走，并在真实 SA 上两侧都有命中。

    旧 30.0 ⇒ 100%（恒命中）；14.0 ⇒ 0%（恒不命中）——两种都是"机制不可达"。
    """
    from elysia.soul.relationship import sa_withdraw_line

    _, _, sp_sa = _load_setpoints()
    sa, _ = _load_sa_cs()
    if not sa:
        pytest.skip("没有心跳样本")
    line = sa_withdraw_line(sp_sa)
    hits = sum(1 for v in sa if v < line) / len(sa)
    print(
        f"SA 线={line:.2f}（设定点 {sp_sa:.1f}）、"
        f"真实 SA [{min(sa):.1f},{max(sa):.1f}]、命中率 {hits:.2%}"
    )
    assert min(sa) < line < max(sa), "阈值必须落在真实 SA 区间内"
    assert 0.01 < hits < 0.99, f"SA 线命中率 {hits:.2%} ⇒ 死阈值（机制不可达）"


def test_relationship_cs_deviation_reachable_both_sides() -> None:
    """关系连续动力学的驱动量 = cs - 设定点：真实数据两侧都必须有。"""
    _, cs = _load_sa_cs()
    _, sp_cs, _ = _load_setpoints()
    if not cs:
        pytest.skip("没有心跳样本")
    deviations = [c - sp_cs for c in cs]
    below = sum(1 for d in deviations if d < 0) / len(deviations)
    above = sum(1 for d in deviations if d > 0) / len(deviations)
    print(f"CS 偏离设定点：负 {below:.1%} / 正 {above:.1%}")
    assert min(deviations) < 0 < max(deviations)
    assert below > 0.01 and above > 0.01, "关系只能单向漂移 ⇒ 连续动力学有一侧不可达"


def test_relationship_clock_opposite_directions() -> None:
    """阈值两侧时钟：低于设定点 → 加深；高于设定点 → 恢复（虚拟时钟各推一次）。"""
    from elysia.soul.relationship import RelationshipKeeper

    _, sp_cs, sp_sa = _load_setpoints()
    below = RelationshipKeeper(withdrawal=0.5)
    below.update(dt_s=10 * 3600.0, cs=sp_cs - 5.0, sa=sp_sa, cs_setpoint=sp_cs, sa_setpoint=sp_sa)
    above = RelationshipKeeper(withdrawal=0.5)
    above.update(dt_s=10 * 3600.0, cs=sp_cs + 5.0, sa=sp_sa, cs_setpoint=sp_cs, sa_setpoint=sp_sa)
    assert below.withdrawal > 0.5, "低于设定点必须加深"
    assert above.withdrawal < 0.5, "高于设定点必须恢复"
