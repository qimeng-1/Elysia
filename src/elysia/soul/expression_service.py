"""表达服务（P2 Step 5 桌宠集成）：把表达指令 → LLM → 校验 → TTS → 落库串成服务。

挂接在灵魂心跳上：每个心跳拍检查"是否该开口"，该开口时构造表达指令
（build_expression）→ 词汇许可注入（enrich_permits）→ LLMChain.speak
→ TTSChain.speak（可选出声）→ 写 thought_log（桌宠气泡）与 expression_log
（全链路取证）。降级事件经 on_degrade 写感受层（SA 微升）。

表达触发（§3.2）= 内部冲动（think 行动）∪ 外部交互（force=True）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from elysia.core.state_store import HeartbeatStore
from elysia.llm.chain import LLMChain
from elysia.llm.validator import ExpressionValidator, ValidationResult
from elysia.memory.retrieve import MemoryHit
from elysia.soul.brain import BrainOutput
from elysia.soul.expression import build_expression
from elysia.tts.chain import TTSChain, TTSRequest, TTSResult

log = logging.getLogger("elysia.soul.expression")

# 记忆检索回调：给定存储 + 当前感受 → 返回待注入表达的记忆
MemoryRetriever = Callable[[HeartbeatStore, dict[str, float]], "Awaitable[list[MemoryHit]]"]

# 内部冲动表达节流：与发呆双模态同节奏（3min / 10min）
EXPRESS_ACTIVE_INTERVAL_S = 180.0
EXPRESS_QUIET_INTERVAL_S = 600.0


@dataclass
class ExpressionOutcome:
    """一次"开口"的完整结果（供心跳拍/测试断言）。"""

    text: str
    level: str  # "main" | "fallback" | "micro"
    validation: ValidationResult | None
    tts: TTSResult | None = None


class ExpressionService:
    """灵魂侧的表达式调度：触发判断 + LLM/TTS 全链路 + 落库取证。"""

    def __init__(
        self,
        llm_chain: LLMChain,
        heartbeat_store: HeartbeatStore,
        *,
        tts_chain: TTSChain | None = None,
        validator: ExpressionValidator | None = None,
        llm_degrade_sa_delta: float = 2.0,
        tts_degrade_sa_delta: float = 1.0,
        retriever: MemoryRetriever | None = None,
    ) -> None:
        self._llm = llm_chain
        self._store = heartbeat_store
        self._tts = tts_chain
        self._validator = validator if validator is not None else ExpressionValidator()
        self.retriever = retriever
        self._last_express_ts: float | None = None
        # 降级 SA 增量（由 DesireSystem 消费方注入 on_degrade 时生效）
        self.llm_degrade_sa_delta = llm_degrade_sa_delta
        self.tts_degrade_sa_delta = tts_degrade_sa_delta

    @property
    def last_express_ts(self) -> float | None:
        return self._last_express_ts

    def _should_speak(self, output: BrainOutput, now: float, force: bool) -> bool:
        """触发判断：外部交互立即开口；内部冲动按双模态节流。"""
        if force:
            return True
        interval = (
            EXPRESS_ACTIVE_INTERVAL_S
            if output.action in ("think_active", "think_quiet") and output.will.thought_style > 0
            else EXPRESS_QUIET_INTERVAL_S
        )
        return self._last_express_ts is None or now - self._last_express_ts >= interval

    async def tick(
        self,
        output: BrainOutput,
        *,
        now: float,
        vrram_mb: float = 0.0,
        body_left_h: float = 0.0,
        day_phase: str = "day",
        force: bool = False,
        env: dict[str, object] | None = None,
    ) -> ExpressionOutcome | None:
        """每心跳拍调用：到开口时机则执行表达全链路，否则返回 None。"""
        if not self._should_speak(output, now, force):
            return None

        self._last_express_ts = now

        # ── 1. 构造表达指令（决策层唯一出口）────────────
        instruction = build_expression(
            output,
            intent="回应" if force else None,
            vrram_mb=vrram_mb,
            body_left_h=body_left_h,
            day_phase=day_phase,
        )
        payload = self._validator.enrich_permits(
            instruction.to_dict(), env if env is not None else {}
        )

        # ── 1b. 记忆检索 → 注入 memory_hooks（P3-D）─────
        if self.retriever is not None:
            hooks = await self.retriever(self._store, output.feelings.to_dict())
            if hooks:
                payload["memory_hooks"] = [h.narrative for h in hooks]

        # ── 2. LLM 翻译 → 校验 ───────────────────────────
        speak = await self._llm.speak(payload)
        outcome = ExpressionOutcome(
            text=speak.text,
            level=speak.level,
            validation=speak.validated,
        )

        # ── 3. TTS 出声（可选）───────────────────────────
        if self._tts is not None and speak.text:
            tts_params = payload.get("tts", {})
            tts_req = TTSRequest(
                text=speak.text,
                emotion=str(tts_params.get("emotion", "default")),
                speed=float(tts_params.get("speed", 1.0)),
            )
            outcome.tts = await self._tts.speak(tts_req)

        # ── 4. 落库：thought_log（桌宠气泡）+ expression_log（取证）──
        await self._store.think(now, "expression", speak.text)
        validation = (
            {"ok": speak.validated.ok, "reason": speak.validated.reason}
            if speak.validated is not None
            else {"ok": True, "reason": None}
        )
        await self._store.expression(
            ts=now,
            intent=str(payload.get("intent", "")),
            instruction=payload,
            llm_text=speak.text,
            validation=validation,
            level=speak.level,
        )
        log.debug(
            "表达完成 intent=%s level=%s tts_source=%s",
            payload.get("intent"),
            speak.level,
            outcome.tts.source if outcome.tts else None,
        )
        return outcome
