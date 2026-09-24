"""表达服务（P2 Step 5 桌宠集成）：把表达指令 → LLM → 校验 → TTS → 落库串成服务。

挂接在灵魂心跳上：每个心跳拍检查"是否该开口"，该开口时构造表达指令
（build_expression）→ 词汇许可注入（enrich_permits）→ LLMChain.speak
→ TTSChain.speak（可选出声）→ 写 thought_log（桌宠气泡）与 expression_log
（全链路取证）。降级事件经 on_degrade 写感受层（SA 微升）。

表达触发（§3.2）= 内部冲动（think 行动）∪ 外部交互（force=True）。

P3-O 记忆接线（两条路，各归其主）：
- **话语路径**：记忆只在"话题撞上"（query 相关）时注入 memory_hooks；
  她想主动提起往事，用自己的 recall 工具（由本服务装配执行器）。
  每句都推记忆＝替她决定"此刻想起什么"，那是违和的根源。
- **感受路径**：被唤起的记忆只推心情（on_recall → 欲望脉冲），不进入她的话。

P3-V：工具增至两个——`recall`（想起）+ `disclaim`（拒绝认领某段记忆）。
后者是她的**权力**：程序不代她拒绝，只保证"叫得动"。

P3-W2：工具增至四个——再加 `forget`（不想再想起）+ `restore`（又愿意想起了）。
忘与不忘同样是**她的权力**；`forget` 门槛加严（避免误伤），`restore` 只在
"被忘掉的"记忆里找（恢复是善意动作，门槛从宽）。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from elysia.core.state_store import HeartbeatStore
from elysia.llm.chain import LLMChain
from elysia.llm.validator import ExpressionValidator, ValidationResult
from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    CLAIM_REJECTED,
    KIND_EXPRESSION,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    RETENTION_PRESENT,
    RETENTION_SUPPRESSED,
    SOURCE_SELF,
)
from elysia.memory.retrieve import MemoryHit, topic_match
from elysia.memory.scorer import importance
from elysia.soul.brain import BrainOutput
from elysia.soul.expression import build_expression
from elysia.tts.chain import TTSChain, TTSRequest, TTSResult

log = logging.getLogger("elysia.soul.expression")

# 记忆检索回调：给定存储 + 当前感受（+ 可选的 now 新鲜度参考 / query 话题）→ 返回注入的记忆
RetrieverWithNow = Callable[..., "Awaitable[list[MemoryHit]]"]

# 想起往事的情感脉冲强度（珍贵/深层记忆推得更深）
RECALL_INTENSITY_PLAIN = 0.6
RECALL_INTENSITY_PRECIOUS = 1.0

# `forget` 命中判据（P3-W2/M4）：遗忘会连感受路径一起切断，判据必须比 disclaim 严——
# 既要"够贴题"（覆盖率 ≥ 0.5），又要"足够突出"（第一名至少是第二名的 2 倍），
# 否则并列/低分时宁可如实回"没找到"，也不误伤一条无关的记忆。
FORGET_MIN_SCORE = 0.5
FORGET_DOMINANCE = 2.0

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
        retriever: RetrieverWithNow | None = None,
        on_recall: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._llm = llm_chain
        self._store = heartbeat_store
        self._tts = tts_chain
        self._validator = validator if validator is not None else ExpressionValidator()
        self.retriever = retriever
        # 记忆被唤起 → 感受回路（只推心情，不进入话语）
        self._on_recall = on_recall
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
        user_message: str | None = None,
    ) -> ExpressionOutcome | None:
        """每心跳拍调用：到开口时机则执行表达全链路，否则返回 None。

        user_message：桌宠发来的用户输入（可选）。非空且触发开口时，
        作为"话题"注入表达指令（T2 保持：只是话题，不做指令），并作为
        一次经历写入记忆（P3 运行时接线）。
        """
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

        # ── 1a. 用户输入作为"话题"注入（T2：非指令，仅话题）────
        if user_message:
            payload["user_message"] = user_message

        # ── 1b. 记忆注入：只在"话题撞上"时（被唤起才浮现，不再每句都推）──
        if self.retriever is not None and user_message:
            hooks = await self.retriever(
                self._store, output.feelings.to_dict(), now=now, query=user_message
            )
            if hooks:
                payload["memory_hooks"] = [h.label for h in hooks]
                await self._feel_recall(hooks)

        # ── 1c. 装配工具：能力由程序保证，用不用由她决定 ────
        set_runner = getattr(self._llm, "set_tool_runner", None)
        if self.retriever is not None and callable(set_runner):
            set_runner(self._make_tool_runner(output, now, user_message))

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
        # ── 4b. P3 运行时接线：她说的话成为经历 → 写入记忆 ──
        if speak.text:
            feelings = output.feelings.to_dict()
            # 重要性按内容信号评估（不再是固定值）：呓语留浅层，有分量的话才沉淀
            await self._store.add_memory(
                now,
                {
                    "level": LEVEL_SHALLOW,
                    "kind": KIND_EXPRESSION,
                    "content": speak.text,
                    "emotion_vector": feelings,
                    "importance": importance(
                        kind=KIND_EXPRESSION,
                        emotion_vector=feelings,
                        content=speak.text,
                    ),
                    "protected": False,
                    "narrative": speak.text,
                    # P3-T 来源标注：这是她自己说出口的话
                    "source": SOURCE_SELF,
                    "certainty": CERTAINTY_CERTAIN,
                },
            )
        log.debug(
            "表达完成 intent=%s level=%s tts_source=%s",
            payload.get("intent"),
            speak.level,
            outcome.tts.source if outcome.tts else None,
        )
        return outcome

    def _make_tool_runner(
        self,
        output: BrainOutput,
        now: float,
        user_message: str | None,
    ) -> Callable[[str, dict[str, Any]], Awaitable[str]]:
        """构造工具执行器：她把意图递过来，程序只把能力兑现。

        - `recall`：想起往事（能力由程序保证，事实递到手）
        - `disclaim`：拒绝认领某段记忆（**她的动作**，程序不得代她拒绝）
        - `forget`：不想再想起某件事（**她的动作**，程序不得代她忘）
        - `restore`：又愿意想起它了（遗忘必须可逆，随时能收回）
        """

        async def _run(name: str, args: dict[str, Any]) -> str:
            topic = str(args.get("topic") or "")
            if name == "recall":
                return await self._tool_recall(topic, output, now, user_message)
            if name == "disclaim":
                return await self._tool_disclaim(topic)
            if name == "forget":
                return await self._tool_forget(topic)
            if name == "restore":
                return await self._tool_restore(topic)
            return ""

        return _run

    async def _tool_recall(
        self,
        topic: str,
        output: BrainOutput,
        now: float,
        user_message: str | None,
    ) -> str:
        """想起工具：她把话题递过来，程序只把事实递回去。

        她没给话题时以当下对话为话题；两者皆空则当作"没想起"
        （保守：宁可想不起来，也不塞一堆不相干的往事）。
        """
        retriever = self.retriever
        query = topic.strip() or (user_message or "")
        if retriever is None or not query:
            return ""
        # 她自己查：宽门槛——她问了，就把相关的递给她（短话题也能命中）
        hooks = await retriever(
            self._store,
            output.feelings.to_dict(),
            now=now,
            query=query,
            query_loose=True,
        )
        if not hooks:
            return ""
        await self._feel_recall(hooks)
        return "；".join(h.label for h in hooks)

    async def _tool_disclaim(self, topic: str) -> str:
        """拒绝认领工具（P3-V）：把最贴题的那条记忆标为 rejected。

        只由**她自己的动作**触发——程序不代她拒绝（铁律一）。
        匹配用宽松的话题覆盖度（她自己提的事往往与原文措辞不同源）；
        没找到对应记忆就如实告诉她"没找到"，不猜、不误伤。
        """
        query = topic.strip()
        if not query:
            return ""
        best_id: int | None = None
        best_score = 0.0
        best_text = ""
        for d in await self._store.iterate_memories():
            text = str(d.get("content") or "")
            score = max(topic_match(query, text), topic_match(query, str(d.get("narrative") or "")))
            if score > best_score:
                best_score = score
                best_id = int(d["id"])
                best_text = text
        if best_id is None or best_score <= 0.0:
            return "（你没找到想拒绝认领的那件事）"
        await self._store.set_claim_status(best_id, CLAIM_REJECTED)
        return f"（你不再把「{best_text[:30]}」当作自己的记忆）"

    async def _tool_forget(self, topic: str) -> str:
        """遗忘工具（P3-W2）：把最贴题的那条记忆推入 `suppressed`。

        只由**她自己的动作**触发——程序不代她忘（铁律一）。
        判据比 `disclaim` 严（M4）：遗忘会连感受路径一起切断，
        因此要求"够贴题"（覆盖率 ≥ `FORGET_MIN_SCORE`）**且**"足够突出"
        （第一名 ≥ 第二名的 `FORGET_DOMINANCE` 倍）；并列或低分时宁可如实回
        "没找到"，也不误伤一条无关的记忆。
        **只动 `retention_state`，不碰 `claim_status`**（两个维度正交）。
        """
        query = topic.strip()
        if not query:
            return ""
        scored: list[tuple[float, int, str]] = []
        for d in await self._store.iterate_memories():
            text = str(d.get("content") or "")
            score = max(topic_match(query, text), topic_match(query, str(d.get("narrative") or "")))
            if score > 0.0:
                scored.append((score, int(d["id"]), text))
        if not scored:
            return "（你没找到想忘记的那件事）"
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_id, best_text = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if best_score < FORGET_MIN_SCORE or best_score < FORGET_DOMINANCE * runner_up:
            return "（你没找到想忘记的那件事）"
        await self._store.set_retention_state(best_id, RETENTION_SUPPRESSED)
        return f"（你不再想想起「{best_text[:30]}」）"

    async def _tool_restore(self, topic: str) -> str:
        """收回工具（P3-W2）：把被忘掉的那条记忆放回 `present`。

        **只在 `suppressed` 里找**（M3）——否则她会"恢复"一条从未被忘掉的记忆。
        恢复是善意动作，判据从宽（任意一个二元组重合即可），
        没找到就如实回"没找到"。**只动 `retention_state`，不碰 `claim_status`**。
        """
        query = topic.strip()
        if not query:
            return ""
        best_id: int | None = None
        best_score = 0.0
        best_text = ""
        for d in await self._store.iterate_memories():
            if str(d.get("retention_state") or "") != RETENTION_SUPPRESSED:
                continue
            text = str(d.get("content") or "")
            score = max(topic_match(query, text), topic_match(query, str(d.get("narrative") or "")))
            if score > best_score:
                best_score = score
                best_id = int(d["id"])
                best_text = text
        if best_id is None or best_score <= 0.0:
            return "（你没有想收回来的那件事）"
        await self._store.set_retention_state(best_id, RETENTION_PRESENT)
        return f"（你愿意重新想起「{best_text[:30]}」了）"

    async def _feel_recall(self, hooks: list[MemoryHit]) -> None:
        """记忆被唤起 → 只推心情（牵挂与亲近微升），不进入她的话。

        珍贵/深层记忆推得更深；感受写入失败不应破坏表达链路。
        """
        if self._on_recall is None or not hooks:
            return
        intensity = (
            RECALL_INTENSITY_PRECIOUS
            if any(h.protected or h.level == LEVEL_DEEP for h in hooks)
            else RECALL_INTENSITY_PLAIN
        )
        with contextlib.suppress(Exception):
            await self._on_recall(intensity)
