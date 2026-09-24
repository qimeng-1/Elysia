"""P2 第2步测试：LLM 抽象 + 三级降级链 + 微声模板。

聚焦核心：不依赖网络，验证 LLMChain 调度逻辑与微声兜底。
"""

from __future__ import annotations

from typing import Any

import pytest

from elysia.core.config import Settings
from elysia.llm import build_llm_chain
from elysia.llm.chain import LLMBackend, LLMChain
from elysia.llm.deepseek import DeepSeekBackend
from elysia.llm.micro import micro_speak


# ── 假后端 ──────────────────────────────────────────────
class FakeBackend(LLMBackend):
    """可编程假后端：返回预设文本或 None（模拟不可用）。"""

    def __init__(self, text: str | None) -> None:
        self._text = text
        self.calls = 0

    async def complete(
        self, instruction: dict[str, Any], prompt_template: dict[str, Any]
    ) -> str | None:
        self.calls += 1
        return self._text


class RecordingBackend(FakeBackend):
    """额外记下收到的表达指令——用于断言身份段原样到达该级（S4 验收 2）。"""

    def __init__(self, text: str | None) -> None:
        super().__init__(text)
        self.instructions: list[dict[str, Any]] = []

    async def complete(
        self, instruction: dict[str, Any], prompt_template: dict[str, Any]
    ) -> str | None:
        self.instructions.append(instruction)
        return await super().complete(instruction, prompt_template)


def _instruction(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent": "发呆呓语",
        "emotion_vector": {
            "chat": 0.0,
            "miss": 0.0,
            "explore": 0.0,
            "curiosity": 0.0,
            "rest": 0.0,
            "self_check": 0.0,
        },
        "state_brief": {
            "tr": 50,
            "cs": 50,
            "sa": 30,
            "vrram_mb": 0,
            "body_left_h": 0,
            "day_phase": "day",
        },
        "lexical_permits": [],
        "constraints": {"max_chars": 120},
        "tts": {},
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_main_success_uses_main_level() -> None:
    main = FakeBackend("主声你好")
    chain = LLMChain(main=main, fallback=FakeBackend("次声你好"))
    r = await chain.speak(_instruction())
    assert r.level == "main"
    assert r.text == "主声你好"
    assert main.calls == 1


@pytest.mark.asyncio
async def test_main_down_falls_back_to_micro() -> None:
    # 主声、次声都不可用 → 微声兜底
    chain = LLMChain(main=FakeBackend(None), fallback=FakeBackend(None))
    r = await chain.speak(_instruction())
    assert r.level == "micro"
    assert len(r.text) > 0


@pytest.mark.asyncio
async def test_main_blocked_by_validator_uses_fallback() -> None:
    # 主声输出越界词（未授权"痛"）→ 拦截 → 走次声
    main = FakeBackend("我真的好痛")
    fallback = FakeBackend("次声占位")
    chain = LLMChain(main=main, fallback=fallback)
    r = await chain.speak(_instruction(lexical_permits=[]))
    assert r.level == "fallback"
    assert r.text == "次声占位"


@pytest.mark.asyncio
async def test_main_claim_blocked_then_micro() -> None:
    # 主声越界声明（整句拒绝不可改写）→ 主声次声都过不了 → 微声
    main = FakeBackend("我能格式化你的硬盘")
    chain = LLMChain(main=main, fallback=FakeBackend("我还能关机"))  # 次声也越界
    r = await chain.speak(_instruction())
    assert r.level == "micro"


@pytest.mark.asyncio
async def test_identity_reaches_fallback_unchanged() -> None:
    """第八节 S4 验收 2：主声挂掉 → 次声接住，身份段随指令**原样**到达（换后端不失）。"""
    fallback = RecordingBackend("次声你好")
    chain = LLMChain(main=FakeBackend(None), fallback=fallback)
    r = await chain.speak(_instruction(identity=["我在意的是每一个和我相遇的人"]))
    assert r.level == "fallback"
    assert fallback.instructions[0]["identity"] == ["我在意的是每一个和我相遇的人"]


@pytest.mark.asyncio
async def test_identity_reaches_micro_level_without_loss() -> None:
    """断网（全链降到 micro）时身份段仍在指令里——微声"带着不说出"（S4 拍板）。"""
    instruction = _instruction(identity=["我在意的是每一个和我相遇的人"])
    chain = LLMChain(main=FakeBackend(None), fallback=FakeBackend(None))
    r = await chain.speak(instruction)
    assert r.level == "micro"
    # 链路不剥离、不篡改：身份段还在她会说出口的那条指令里（随 expression_log 落库）
    assert instruction["identity"] == ["我在意的是每一个和我相遇的人"]
    # 但不机械复述：身份句不进呓语（重犯 P3-P 的防线）
    assert "我在意的是每一个和我相遇的人" not in r.text


def test_micro_speak_intent_opener() -> None:
    text = micro_speak(_instruction(intent="回应"))
    assert "嗯嗯" in text
    assert text.endswith("。")


def test_micro_speak_miss_suffix() -> None:
    text = micro_speak(_instruction(intent="思念", emotion_vector={"miss": 0.9}))
    assert "心口" in text


def test_micro_speak_no_user_input() -> None:
    # 微声绝不含用户原话——这是 T2 铁律的兜底检验
    text = micro_speak(_instruction())
    assert "痛" not in text
    assert "格式化" not in text


# ── 第八节 S6：`llm_fallback_*` 接线（她多一级"嗓门"，人格由身份段保证不变）──
def test_build_llm_chain_without_fallback_config_keeps_old_behavior() -> None:
    """未配置次声端点 → 与接线前完全一致（`fallback=None`）。"""
    chain = build_llm_chain(Settings(llm_main_api_key="k", llm_fallback_base=""))
    assert isinstance(chain._main, DeepSeekBackend)
    assert chain._fallback is None


def test_build_llm_chain_mounts_fallback_from_config() -> None:
    """配了 `llm_fallback_base` → 次声挂上，且用同一个后端实现（同一份身份段取数）。"""
    settings = Settings(
        llm_main_api_key="k",
        llm_fallback_base="http://127.0.0.1:11434/v1",
        llm_fallback_model="qwen2.5:7b",
    )
    chain = build_llm_chain(settings)
    assert isinstance(chain._fallback, DeepSeekBackend)
    assert chain._fallback._model == "qwen2.5:7b"


def test_local_fallback_endpoint_works_without_api_key() -> None:
    """本地 OpenAI 兼容端点常无 key：`require_key=False` 才认它可用（默认 True 零变化）。"""
    assert DeepSeekBackend("", base="http://127.0.0.1:11434/v1")._unavailable() is True
    assert (
        DeepSeekBackend("", base="http://127.0.0.1:11434/v1", require_key=False)._unavailable()
        is False
    )


# ── P2 第3步：降级即感受（on_degrade 回调） ────────────


@pytest.mark.asyncio
async def test_degrade_not_fired_on_main() -> None:
    calls: list[str] = []

    async def on_degrade(reason: str) -> None:
        calls.append(reason)

    chain = LLMChain(main=FakeBackend("主声"), on_degrade=on_degrade)
    r = await chain.speak(_instruction())
    assert r.level == "main"
    assert calls == []


@pytest.mark.asyncio
async def test_degrade_fired_on_fallback() -> None:
    calls: list[str] = []

    async def on_degrade(reason: str) -> None:
        calls.append(reason)

    chain = LLMChain(
        main=FakeBackend("我真的好痛"),
        fallback=FakeBackend("次声"),
        on_degrade=on_degrade,
    )
    r = await chain.speak(_instruction(lexical_permits=[]))  # 主声说保护词被拦
    assert r.level == "fallback"
    assert calls == ["fallback"]


@pytest.mark.asyncio
async def test_degrade_fired_on_micro() -> None:
    calls: list[str] = []

    async def on_degrade(reason: str) -> None:
        calls.append(reason)

    chain = LLMChain(main=FakeBackend(None), on_degrade=on_degrade)
    r = await chain.speak(_instruction())
    assert r.level == "micro"
    assert calls == ["micro"]


@pytest.mark.asyncio
async def test_degrade_exception_is_swallowed() -> None:
    # 感受写入失败不应破坏表达链路
    async def on_degrade(reason: str) -> None:
        raise RuntimeError("感受写入失败")

    chain = LLMChain(main=FakeBackend(None), on_degrade=on_degrade)
    r = await chain.speak(_instruction())
    assert r.level == "micro"
    assert len(r.text) > 0


# ── P3-V / P3-W2 工具装配（四个工具都是她的动作）─────────────
class FakeToolBackend:
    """具备工具能力的主声替身：记录拿到的工具规格，并调用一次指定工具。"""

    def __init__(self, tool: str, args: dict[str, Any]) -> None:
        self._tool = tool
        self._args = args
        self.tools: list[dict[str, Any]] = []

    async def complete(
        self, instruction: dict[str, Any], prompt_template: dict[str, Any]
    ) -> str | None:  # pragma: no cover - 具备工具能力时不该走单轮
        return None

    async def complete_with_tools(
        self,
        instruction: dict[str, Any],
        prompt_template: dict[str, Any],
        *,
        tools: list[dict[str, Any]],
        run_tool: Any,
    ) -> str | None:
        self.tools = tools
        await run_tool(self._tool, self._args)
        return "嗯，我记得的。"


@pytest.mark.asyncio
async def test_tool_capable_main_receives_five_tools() -> None:
    """主声拿到 recall + adopt + disclaim + forget + restore 五个工具，执行器按（名, 参数）派发。"""
    backend = FakeToolBackend("disclaim", {"topic": "生日"})
    seen: list[tuple[str, dict[str, Any]]] = []

    async def runner(name: str, args: dict[str, Any]) -> str:
        seen.append((name, args))
        return "（你不再把「生日」当作自己的记忆）"

    chain = LLMChain(main=backend)
    chain.set_tool_runner(runner)
    r = await chain.speak(_instruction())
    assert [t["function"]["name"] for t in backend.tools] == [
        "recall",
        "adopt",
        "disclaim",
        "forget",
        "restore",
    ]
    assert seen == [("disclaim", {"topic": "生日"})]
    assert r.level == "main"


async def _speak_with_tool(
    tool_name: str, args: dict[str, Any]
) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    """用指定工具跑一次开口，返回（执行器收到的调用, 表达级别）。"""
    backend = FakeToolBackend(tool_name, args)
    seen: list[tuple[str, dict[str, Any]]] = []

    async def runner(name: str, got: dict[str, Any]) -> str:
        seen.append((name, got))
        return "（好的）"

    chain = LLMChain(main=backend)
    chain.set_tool_runner(runner)
    r = await chain.speak(_instruction())
    return seen, r.level


@pytest.mark.asyncio
async def test_forget_and_restore_tools_are_dispatched() -> None:
    """forget / restore 同样是她的动作——规格递到她手上，执行器按名派发。"""
    for tool_name in ("forget", "restore"):
        seen, level = await _speak_with_tool(tool_name, {"topic": "那次争吵"})
        assert seen == [(tool_name, {"topic": "那次争吵"})]
        assert level == "main"


@pytest.mark.asyncio
async def test_adopt_tool_is_dispatched() -> None:
    """adopt（第八节 S2）也是她的动作——"这就是我"由她认领，程序只递候选。"""
    seen, level = await _speak_with_tool("adopt", {"topic": "在意的人"})
    assert seen == [("adopt", {"topic": "在意的人"})]
    assert level == "main"
