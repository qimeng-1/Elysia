"""P0-R19：表达节奏架构单测（选择不回复 + 多句拆句 + 可解释落库）。

取消测试：
- 把 `force` 改回硬短路（收到话必定"回应"）⇒ 意愿/restraint 用例红；
- 去掉句间排队（一口气全写）⇒ `test_multi_sentence_uses_one_call_with_delays` 红；
- 去掉 `MAX_UTTERANCES_PER_TURN` 截断 ⇒ `test_split_utterances_caps_at_3` 红；
- 去掉 reason 落库 ⇒ `test_restraint_is_logged_with_reason` 红。
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression import (
    MAX_UTTERANCES_PER_TURN,
    RESTRAINT_INTENT,
    compute_reply_desire,
    split_utterances,
    utterance_gap_s,
)
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import ExpressionJob


class _FakeLLM:
    """记录调用次数，返回固定多句文本（用于证明"一次调用拆多句"）。"""

    def __init__(self, text: str = "甲。乙。丙。丁。") -> None:
        self.text = text
        self.calls = 0

    async def speak(self, payload: dict[str, Any]) -> Any:
        self.calls += 1
        return SimpleNamespace(
            text=self.text,
            level="main",
            validated=ValidationResult(ok=True, reason=None, sanitized=None),
        )


def _output(*, chat: float = 0.62, miss: float = 0.38) -> BrainOutput:
    return BrainOutput(
        desire=DesireState(tr=46.0, cs=62.0, sa=20.0),
        feelings=FeelingState(
            chat=chat, miss=miss, explore=0.46, curiosity=0.0, rest=0.05, self_check=0.2
        ),
        will=WillOutput(direction="still", strength=0.54, thought_style=0.0, anim_bias=0.0),
        action="none",
    )


# ── 意愿公式：确定性 + 可解释 ────────────────────────────────
def test_reply_desire_is_deterministic_and_explainable() -> None:
    a = compute_reply_desire(chat=0.62, miss=0.38)
    b = compute_reply_desire(chat=0.62, miss=0.38)
    assert a == b, "同一输入 + 同一状态必须得到同一决策（不许抛硬币）"
    assert a.speak is True

    quiet = compute_reply_desire(chat=0.30, miss=0.05, interval_factor=2.0, distress=True)
    assert quiet.speak is False
    assert "拖低它的分量" in quiet.reason
    assert quiet.parts["难受"] < 0 and quiet.parts["被越界"] == 0


def test_greeting_bypasses_threshold() -> None:
    d = compute_reply_desire(chat=0.2, miss=0.0, interval_factor=2.0, greeting=True)
    assert d.speak is True
    assert "问候" in d.reason


def test_boundary_refusal_lowers_desire() -> None:
    normal = compute_reply_desire(chat=0.62, miss=0.38)
    refused = compute_reply_desire(chat=0.62, miss=0.38, boundary_refused=True)
    assert refused.reply_desire < normal.reply_desire
    assert refused.speak is False
    assert "被越界" in refused.reason


# ── 多句 = 一次调用拆句；≤3 句；间隔 2–8s ──────────────────────
def test_split_utterances_caps_at_3() -> None:
    parts = split_utterances("一。二！三？四。")
    assert MAX_UTTERANCES_PER_TURN == 3
    assert parts == ["一。", "二！", "三？"], "超出的句子必须被硬上限截掉（刷屏保护）"
    assert split_utterances("只有一句。") == ["只有一句。"]
    assert split_utterances("") == []


def test_utterance_gap_depends_on_state() -> None:
    assert utterance_gap_s(1.0) == pytest.approx(2.0)
    assert utterance_gap_s(0.0) == pytest.approx(8.0)
    mid = utterance_gap_s(0.5)
    assert 2.0 < mid < 8.0, "句间间隔必须由状态决定，不是固定值"


@pytest.mark.asyncio
async def test_multi_sentence_uses_one_call_with_delays(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        llm = _FakeLLM("甲。乙。丙。丁。")
        svc = ExpressionService(llm, store)
        job = ExpressionJob(
            job_id="1",
            created_ts=1.0,
            heartbeat_seq=1,
            output=_output(),
            summary={},
            intent="回应",
            reply_desire=0.4,
        )
        result = await svc.run_job(job)
        await svc.persist(result, 1.0)

        assert llm.calls == 1, "多句必须来自**一次** LLM 调用（成本门）"
        rows = await store.execute_raw("SELECT text FROM thought_log ORDER BY id")
        assert [r[0] for r in rows] == ["甲。"]
        assert len(svc._pending_utterances) == 2, "其余句子必须排队等间隔，不能一口气写完"

        await svc.flush_utterances(1.0 + utterance_gap_s(0.4) + 0.001)
        rows = await store.execute_raw("SELECT text FROM thought_log ORDER BY id")
        assert [r[0] for r in rows] == ["甲。", "乙。"], "到点才冒下一句"

        detail = json.loads(
            (await store.execute_raw("SELECT validation FROM expression_log"))[0][0]
        )
        assert detail["utterance_count"] == 3
        assert detail["reply_desire"] == pytest.approx(0.4)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_restraint_is_logged_with_reason(tmp_path: Path) -> None:
    """取消测试（P0-R19）：每条"选择不回"都必须能从库里读出 reason。"""
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        svc = ExpressionService(_FakeLLM("我现在不太想说话。"), store)
        decision = compute_reply_desire(chat=0.30, miss=0.05, interval_factor=2.0, distress=True)
        job = ExpressionJob(
            job_id="2",
            created_ts=1.0,
            heartbeat_seq=1,
            output=_output(chat=0.3, miss=0.05),
            summary={},
            intent=RESTRAINT_INTENT,
            reply_desire=decision.reply_desire,
            restraint_reason=decision.reason,
            restraint_logged=True,
        )
        result = await svc.run_job(job)
        await svc.persist(result, 1.0)

        rows = await store.execute_raw("SELECT intent, llm_text FROM expression_log")
        assert rows == [], "restraint 已由心跳在决定那一刻落库，persist 不得重复写"
        thoughts = await store.execute_raw("SELECT text FROM thought_log ORDER BY id")
        assert thoughts and thoughts[0][0], "b 方案：她仍要开口说明，不是完全沉默"
    finally:
        await store.close()


# ── 需求累积闭环（取消测试 #5：去掉 silence 结算 ⇒ 单调性/复开口失败）────────
def test_restraint_accumulates_demand_until_she_speaks_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续选择不回 ⇒ 社交落空(-CS) ⇒ miss 上升 ⇒ reply_desire 单调上升 ⇒ 她重新开口。

    这不是外挂兜底（没有"连续 N 次就强制回"的代码），而是欲望系统自己的物理：
    每一次没回成的交流都让思念更重一点。
    """
    from elysia.soul.desire import DesireEvent, DesireState, DesireSystem

    monkeypatch.setattr("elysia.soul.desire.NOISE_AMP", 0.0)  # 只看累积趋势，不看噪声
    ds = DesireSystem(initial=DesireState(tr=46.0, cs=62.0, sa=20.0))
    desires: list[float] = []
    speaks: list[bool] = []
    for _ in range(300):
        state = ds.state
        decision = compute_reply_desire(
            chat=state.cs / 100.0,
            miss=1.0 - state.cs / 100.0,
            interval_factor=1.5,  # 关系有退缩，但没到彻底不理
        )
        desires.append(decision.reply_desire)
        speaks.append(decision.speak)
        if decision.speak:
            break  # 她重新开口了，累积到此结束
        # 这一轮她选择不回 ⇒ 社交落空（silence：CS 下降、miss 上升）
        ds.apply_event(DesireEvent(kind="silence", intensity=1.0))
        ds.step(dt=1.0)

    assert len(desires) >= 3, "用例本身要跨多轮选择不回"
    assert all(b >= a - 1e-9 for a, b in pairwise(desires)), (
        f"连续选择不回后 reply_desire 必须单调上升（实际 {desires[:8]}...）"
    )
    assert desires[-1] > desires[0]
    assert speaks[0] is False, "起点必须是'选择不回'，否则用例没有测到累积"
    assert speaks[-1] is True, "她不会永久沉默：需求累积到阈值后自己重新开口"
