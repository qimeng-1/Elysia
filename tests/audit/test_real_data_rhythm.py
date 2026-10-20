"""P4 真机审计：真实状态序列回放 ⇒ 必须出现 >2 小时静默，且每次开口可追溯原因。

**默认跳过**（与 `test_real_data_thresholds.py` 同一开关）：
    $env:ELYSIA_AUDIT_REAL = "1"
    .venv\\Scripts\\python.exe -m pytest tests/audit/test_real_data_rhythm.py

P4（2026-10-20）把"经过时间"从触发条件里删掉：触发只来自离散事件/状态跨越。
本审计用她**真实心跳序列**（感受 + 意愿）回放，验证：
- 没有事件时她真的会沉默很久（>2h 静默必须出现）；
- 每一条开口都带 `speech_cause`（discovery/threshold/presence/write/response），
  **不出现 `none` 却开口**。

取消测试：把"经过时间 ⇒ 开口"加回 `SpeechTrigger` ⇒ 本文件红
（静默上限塌回分钟级）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService
from elysia.soul.speech_trigger import ALLOWED_CAUSES, CAUSE_NONE, SpeechTrigger

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "heartbeat.db"

pytestmark = pytest.mark.skipif(
    os.environ.get("ELYSIA_AUDIT_REAL") != "1" or not REAL_DB.exists(),
    reason="真机数据审计：设 ELYSIA_AUDIT_REAL=1 且存在 data/heartbeat.db 才跑",
)


def _ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True, timeout=5.0)


def _load_beats(n: int = 40000) -> list[dict[str, Any]]:
    """最近 n 拍真实心跳，按时间升序：feelings / will / desire 全取。"""
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


def _quantile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
    return ordered[index]


def test_real_state_replay_has_long_silence_and_traceable_causes() -> None:
    """取消测试：把"经过时间 ⇒ 开口"加回去 ⇒ 本用例红（最长静默 <10 分钟）。"""
    beats = _load_beats()
    if len(beats) < 1000:
        pytest.skip("心跳样本不足")
    window_s = beats[-1]["ts"] - beats[0]["ts"]
    if window_s <= 0:
        pytest.skip("心跳时间窗异常")

    service = ExpressionService(None, None)  # type: ignore[arg-type]
    trigger = SpeechTrigger()
    speak_ts: list[float] = []
    causes: list[str] = []
    for beat in beats:
        output = _to_output(beat)
        # 意愿只决定"事件发生后多久回应"（响应延迟），不是触发器
        delay_s = service.rhythm_interval_s(output, beat["ts"])
        cause = trigger.observe(output.feelings.to_dict(), beat["ts"], delay_s=delay_s)
        if cause is not None:
            speak_ts.append(beat["ts"])
            causes.append(cause)

    assert speak_ts, "回放里一次都没开口（尺子失效）"
    gaps = [b - a for a, b in pairwise(speak_ts)]
    max_silence = max(gaps) if gaps else window_s
    daily = len(speak_ts) / window_s * 86400.0
    print(
        f"\n真实状态回放（{window_s / 3600.0:.2f}h，n={len(beats)} 拍）："
        f"\n  开口 {len(speak_ts)} 次（估算 {daily:.0f}/日）；"
        f"最长静默 {max_silence / 3600.0:.2f}h；"
        f"中位间隔 {_quantile(gaps, 0.5) / 60.0:.1f}min；"
        f"p90 间隔 {_quantile(gaps, 0.9) / 60.0:.1f}min"
        f"\n  causes={sorted(set(causes))}"
    )

    # P4 硬指标：没有事件时她真的会沉默很久（>2 小时必须出现）
    assert max_silence > 2 * 3600.0, (
        f"最长静默只有 {max_silence / 60.0:.1f} 分钟 ⇒ 仍是节拍器（时间触发没删干净）"
    )
    # 每一次开口都能追溯到离散原因；不得出现 none 却开口
    assert all(cause in ALLOWED_CAUSES for cause in causes), f"非法 cause：{set(causes)}"
    assert all(cause != CAUSE_NONE for cause in causes), "出现 speech_cause=none 却开口"
