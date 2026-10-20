"""档 1 世界源扩展：维基随机条目 / GitHub Trending / 天气（都沿用 W3 硬约束）。

三个新源共用同一套：注入标记、8s 超时、≤4000 字截断、最终 URL 再校验、失败明确说
读不到、成功才写 `kind='world'`（24h 去重）。

取消测试：
- 去掉某一主机白名单（如 github.com）⇒ 该源用例红；
- 去掉"重定向后校验最终 URL" ⇒ 随机条目跳到白名单外的用例红。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore
from elysia.llm.validator import ValidationResult
from elysia.memory.levels import KIND_WORLD, SOURCE_OBSERVATION
from elysia.soul.expression_service import ExpressionService
from elysia.soul.tools.read_url import (
    EXTERNAL_DATA_MARKER,
    ReadResult,
    ReadUrlHttpError,
    is_allowed_url,
    read_url,
)

T0 = 1_000_000.0
WIKI_RANDOM = "https://zh.wikipedia.org/wiki/Special:Random"
WIKI_LANDING = "https://zh.wikipedia.org/wiki/%E6%98%9F%E7%A9%BA"
GITHUB_TRENDING = "https://github.com/trending"
WEATHER = "https://wttr.in/Beijing?format=3"


def _wiki_body(extract: str) -> bytes:
    return (
        "<html><body><div id='mw-content-text'><div class='mw-parser-output'>"
        f"<p>{extract}</p></div></div>"
        "<div id='footer'>导航垃圾</div></body></html>"
    ).encode()


GITHUB_HTML = """<html><body>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/microsoft/markitdown">microsoft / markitdown</a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">Python tool for converting files to Markdown.</p>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/rust-lang/rust">rust-lang / rust</a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">Empowering everyone to build reliable software.</p>
</article>
<script>alert('不该进正文')</script>
</body></html>"""


def _fetch(final_url: str, body: bytes, status: int = 200) -> Any:
    def _f(url: str, timeout_s: float) -> tuple[str, int, bytes]:
        return final_url, status, body

    return _f


# ── 维基随机条目：跟随 302 后落回白名单 ─────────────────────
def test_wikipedia_random_follows_redirect_into_whitelist() -> None:
    result = read_url(
        WIKI_RANDOM, fetch=_fetch(WIKI_LANDING, _wiki_body("星空可以指郭沫若的诗集。"))
    )
    assert result.ok is True
    assert result.kind == "article"
    assert "星空可以指" in result.text
    assert result.source.startswith("维基百科") and "星空" in result.source
    assert result.as_tool_text().startswith(EXTERNAL_DATA_MARKER)


def test_wikipedia_random_redirect_outside_whitelist_is_rejected() -> None:
    result = read_url(
        WIKI_RANDOM, fetch=_fetch("https://evil.example.com/star", _wiki_body("不该读到的正文"))
    )
    assert result.ok is False
    assert result.reason is not None and "重定向到白名单外" in result.reason
    assert result.text == ""


# ── GitHub Trending：只要仓库名 + 一句简介 ───────────────────
def test_github_trending_extracts_repos_and_descriptions() -> None:
    result = read_url(GITHUB_TRENDING, fetch=_fetch(GITHUB_TRENDING, GITHUB_HTML.encode()))
    assert result.ok is True
    assert result.kind == "trending"
    assert "microsoft/markitdown" in result.text
    assert "Python tool for converting files to Markdown." in result.text
    assert "rust-lang/rust" in result.text
    assert "Empowering everyone" in result.text
    assert "不该进正文" not in result.text
    assert len(result.text) <= 4000
    assert result.as_tool_text().startswith(EXTERNAL_DATA_MARKER)


def test_github_trending_http_error_is_explicit() -> None:
    def _boom(url: str, timeout_s: float) -> tuple[str, int, bytes]:
        raise ReadUrlHttpError(500)

    result = read_url(GITHUB_TRENDING, fetch=_boom)
    assert result.ok is False
    assert result.reason is not None and "HTTP 500" in result.reason


# ── 天气：纯文本 ────────────────────────────────────────────
def test_weather_returns_plain_text() -> None:
    result = read_url(WEATHER, fetch=_fetch("https://wttr.in/Beijing?format=3", b"Beijing: +12C"))
    assert result.ok is True
    assert result.kind == "weather"
    assert "Beijing" in result.text
    assert result.as_tool_text().startswith(EXTERNAL_DATA_MARKER)


def test_weather_other_city_rejected_without_fetch() -> None:
    def _boom(url: str, timeout_s: float) -> tuple[str, int, bytes]:
        raise AssertionError("白名单外的 URL 不得发起请求")

    assert is_allowed_url("https://wttr.in/Shanghai?format=3") is False
    result = read_url("https://wttr.in/Shanghai?format=3", fetch=_boom)
    assert result.ok is False
    assert result.reason is not None and "白名单" in result.reason


# ── 世界记忆：每次成功 ⇒ 一条 world（去重生效）──────────────
class _FakeLLM:
    async def speak(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            text="嗯。", level="main", validated=ValidationResult(ok=True, reason=None)
        )


async def _service(tmp_path: Path, reader: Any) -> ExpressionService:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    return ExpressionService(_FakeLLM(), store, url_reader=reader)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_successful_source_read_writes_one_world_memory(tmp_path: Path) -> None:
    service = await _service(
        tmp_path,
        lambda url: read_url(GITHUB_TRENDING, fetch=_fetch(GITHUB_TRENDING, GITHUB_HTML.encode())),
    )
    try:
        await service._tool_read_url(GITHUB_TRENDING, T0)
        await service._tool_read_url(GITHUB_TRENDING, T0 + 60)  # 24h 内重复
        worlds = [m for m in await service._store.iterate_memories() if m["kind"] == KIND_WORLD]
        assert len(worlds) == 1, "同 URL 24h 内只留一条"
        assert worlds[0]["source"] == SOURCE_OBSERVATION
        assert "microsoft/markitdown" in worlds[0]["content"]
    finally:
        await service._store.close()


@pytest.mark.asyncio
async def test_failed_source_read_writes_no_world_memory(tmp_path: Path) -> None:
    def _failed(url: str) -> ReadResult:
        return ReadResult(ok=False, kind="error", reason="HTTP 503")

    service = await _service(tmp_path, _failed)
    try:
        await service._tool_read_url(GITHUB_TRENDING, T0)
        assert not [m for m in await service._store.iterate_memories() if m["kind"] == KIND_WORLD]
    finally:
        await service._store.close()
