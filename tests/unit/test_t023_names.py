"""T-023：名字缺口——只加事实，不教她问。

她已知：自己叫爱莉希雅、另一个电子生命叫大肥鱼；**身边那个人的称呼是未知**。
在 payload 的"关系/身份"事实段里加一行中性事实；用户将来告诉她名字，写进
`kv['names']` 即可更新（`None` 渲染成"还不知道"）。

验收（含取消测试）：
1. payload 含这行事实；2. `他 = None` 时渲染为"还不知道"；3. 给她名字后渲染成名字
（用 kv 注入）；4. 去掉字段 ⇒ 用例红；5. 不得出现任何"去问"的指令。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService, render_known_names
from elysia.soul.expression_types import ExpressionJob
from elysia.soul.tools.writing import WritingWorkspace

T0 = 1_000_000.0


class _FakeLLM:
    def __init__(self, text: str = "嗯。") -> None:
        self.text = text

    async def speak(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            text=self.text,
            level="main",
            validated=ValidationResult(ok=True, reason=None),
        )


def _job() -> ExpressionJob:
    return ExpressionJob(
        job_id="job-t023",
        created_ts=T0,
        heartbeat_seq=1,
        output=BrainOutput(
            desire=DesireState(),
            feelings=FeelingState(chat=0.6, miss=0.4),
            will=WillOutput(thought_style=0.1),
        ),
        summary={},
        intent="回应",
    )


async def _service(
    tmp_path: Path,
) -> tuple[ExpressionService, HeartbeatStore]:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    service = ExpressionService(
        _FakeLLM(), store, writing_workspace=WritingWorkspace(tmp_path / "home")
    )
    return service, store


# ── 纯函数：null ⇒ 还不知道；给了名字 ⇒ 名字 ────────────────────────
def test_render_known_names_default_and_updated() -> None:
    default = render_known_names(None)
    assert "我知道的称呼：" in default
    assert "我 = 爱莉希雅" in default
    assert "大肥鱼 = 另一个电子生命" in default
    assert "还不知道他的称呼" in default

    updated = render_known_names({"他": "阿明"})
    assert "阿明" in updated
    assert "还不知道" not in updated


def test_render_known_names_has_no_imperative() -> None:
    """只加事实，不教她问（不许出现"问/应该/去问"这类指令）。"""
    for names in (None, {"他": "阿明"}, {"他": None}):
        text = render_known_names(names)
        for banned in ("去问", "问他", "你应该", "记得问", "问问"):
            assert banned not in text


# ── payload：含名字事实 ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_payload_carries_names_fact_with_unknown(tmp_path: Path) -> None:
    """默认（kv 里没有名字）⇒ payload 里的"他"是"还不知道"。

    取消测试：去掉 `run_job` 里 `payload["known_names"]` 的装配 ⇒ 本用例红。
    """
    service, store = await _service(tmp_path)
    try:
        payload = (await service.run_job(_job())).instruction
        fact = payload.get("known_names")
        assert isinstance(fact, str) and fact
        assert "我知道的称呼：" in fact
        assert "我 = 爱莉希雅" in fact
        assert "还不知道他的称呼" in fact
        for banned in ("去问", "你应该", "必须"):
            assert banned not in fact
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_payload_uses_name_after_she_is_told(tmp_path: Path) -> None:
    """用户告诉她名字（写入 kv['names']) ⇒ payload 渲染成那个名字。"""
    service, store = await _service(tmp_path)
    try:
        await store.save_json("names", {"我": "爱莉希雅", "大肥鱼": "大肥鱼", "他": "阿明"})
        payload = (await service.run_job(_job())).instruction
        fact = payload.get("known_names")
        assert isinstance(fact, str)
        assert "阿明" in fact
        assert "还不知道" not in fact
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_names_fact_default_is_not_persisted(tmp_path: Path) -> None:
    """kv 里没写名字也能渲染默认值（不需要启动时写库）。"""
    service, store = await _service(tmp_path)
    try:
        assert await store.load_json("names", default=None) is None
        payload = (await service.run_job(_job())).instruction
        assert "还不知道他的称呼" in str(payload.get("known_names"))
    finally:
        await store.close()
