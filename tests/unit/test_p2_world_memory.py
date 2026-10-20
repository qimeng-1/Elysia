"""P2：世界记忆——`read_url` 成功后落一条 `kind='world'`，读到的才记得住。

用户裁定「需要」：她能读（真机证据），但自己没读过（`kind='world'` 记忆为 0），
于是"我翻了页面"全是编的。世界记忆是 P1a 依据②的事实基础。

规则：成功后落一条；失败不落；同 URL 24h 去重；单条 ≤800 字、摘要 ≤400 字；
`source=observation`（不能用 inference/system，那两个被 hooks 闸挡掉）、
`certainty=certain`、`importance=0.25`；hooks 标签是「今天」不是「刚刚」。

取消测试：去掉 `_tool_read_url` 里的落库调用 ⇒ `test_read_success_writes_world_memory` 红。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    KIND_WORLD,
    SOURCE_OBSERVATION,
)
from elysia.memory.retrieve import retrieve_from_store
from elysia.memory.world import (
    WORLD_IMPORTANCE,
    WORLD_MAX_CHARS,
    WORLD_SUMMARY_CHARS,
    world_content,
)
from elysia.soul.expression_service import ExpressionService
from elysia.soul.tools.read_url import ReadResult

T0 = 1_000_000.0
URL = "https://zh.wikipedia.org/wiki/埃舍尔"
SAMPLE = "《埃舍尔》是荷兰版画艺术家，以不可能图形闻名。"
SOURCE = "维基百科（zh.wikipedia.org）· 埃舍尔"


class _FakeLLM:
    async def speak(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            text="嗯。", level="main", validated=ValidationResult(ok=True, reason=None)
        )


def _reader(ok: bool = True, text: str = SAMPLE) -> Any:
    def _read(_url: str) -> ReadResult:
        if not ok:
            return ReadResult(ok=False, kind="error", reason="HTTP 500")
        return ReadResult(ok=True, kind="article", text=text, source=SOURCE, final_url=URL)

    return _read


async def _service(tmp_path: Path, reader: Any) -> ExpressionService:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    return ExpressionService(  # type: ignore[arg-type]
        _FakeLLM(), store, url_reader=reader
    )


# ── 成功落库 / 失败不落 ─────────────────────────────────────
@pytest.mark.asyncio
async def test_read_success_writes_world_memory(tmp_path: Path) -> None:
    service = await _service(tmp_path, _reader())
    try:
        await service._tool_read_url(URL, T0)
        worlds = [m for m in await service._store.iterate_memories() if m["kind"] == KIND_WORLD]
        assert len(worlds) == 1
        memory = worlds[0]
        assert memory["content"].startswith("读了《")
        assert SAMPLE in memory["content"]
        assert memory["source"] == SOURCE_OBSERVATION
        assert memory["certainty"] == CERTAINTY_CERTAIN
        assert memory["importance"] == WORLD_IMPORTANCE == 0.25
        assert memory["protected"] is False
        assert memory["source_ref"] == URL
    finally:
        await service._store.close()


@pytest.mark.asyncio
async def test_read_failure_writes_nothing(tmp_path: Path) -> None:
    service = await _service(tmp_path, _reader(ok=False))
    try:
        await service._tool_read_url(URL, T0)
        assert not [m for m in await service._store.iterate_memories() if m["kind"] == KIND_WORLD]
    finally:
        await service._store.close()


# ── 24h 去重 ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_same_url_within_24h_is_deduped(tmp_path: Path) -> None:
    service = await _service(tmp_path, _reader())
    try:
        await service._tool_read_url(URL, T0)
        await service._tool_read_url(URL, T0 + 3600.0)  # 一小时后重读
        worlds = [m for m in await service._store.iterate_memories() if m["kind"] == KIND_WORLD]
        assert len(worlds) == 1, "同 URL 24h 内不得重复落库"

        # 不同 URL ⇒ 新的一条
        def _other(_url: str) -> ReadResult:
            return ReadResult(
                ok=True,
                kind="article",
                text="《蒙德里安》是荷兰画家。",
                source="维基百科 · 蒙德里安",
                final_url="https://zh.wikipedia.org/wiki/蒙德里安",
            )

        service._read_url = _other  # type: ignore[assignment]
        await service._tool_read_url("https://zh.wikipedia.org/wiki/蒙德里安", T0 + 7200.0)
        assert (
            len([m for m in await service._store.iterate_memories() if m["kind"] == KIND_WORLD])
            == 2
        )

        # 超过 24h ⇒ 允许再留一条
        service._read_url = _reader()  # type: ignore[assignment]
        await service._tool_read_url(URL, T0 + 26 * 3600.0)
        assert (
            len([m for m in await service._store.iterate_memories() if m["kind"] == KIND_WORLD])
            == 3
        )
    finally:
        await service._store.close()


# ── 可召回 + 标签「今天」 ──────────────────────────────────
@pytest.mark.asyncio
async def test_world_memory_is_recalled_with_today_label(tmp_path: Path) -> None:
    service = await _service(tmp_path, _reader())
    try:
        await service._tool_read_url(URL, T0)
        hooks = await retrieve_from_store(service._store, {"chat": 0.4}, now=T0, query="埃舍尔")
        assert hooks, "读过的世界记忆必须能被话题召回"
        assert hooks[0].kind == KIND_WORLD
        assert hooks[0].label.startswith("（今天）"), hooks[0].label
        assert "刚刚" not in hooks[0].label
    finally:
        await service._store.close()


# ── 内容上限 ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_world_memory_content_is_capped(tmp_path: Path) -> None:
    long_text = "埃" * 5000
    service = await _service(tmp_path, _reader(text=long_text))
    try:
        await service._tool_read_url(URL, T0)
        worlds = [m for m in await service._store.iterate_memories() if m["kind"] == KIND_WORLD]
        content = worlds[0]["content"]
        assert len(content) <= WORLD_MAX_CHARS
        assert "埃" * (WORLD_SUMMARY_CHARS + 10) not in content  # 摘要被截断
    finally:
        await service._store.close()


def test_world_content_format() -> None:
    text = world_content("维基百科 · 埃舍尔", SAMPLE)
    assert text == f"读了《维基百科 · 埃舍尔》：{SAMPLE}"
    assert len(world_content("标题", "字" * 5000)) <= WORLD_MAX_CHARS
