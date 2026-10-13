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

第八节 S1：开口前装配**身份段**（"我是谁"）——来自她认领的自我认知（`kind=self`），
尚无认领时为空、后端退化为出生设定。身份段每句在场，与"话题撞上才浮现"的
`memory_hooks` 是两件事，因此不占 `MAX_HOOKS` 名额。

第八节 S2：工具增至五个——再加 `adopt`（把某段经历认作"这就是我"）。
认领是**她的动作**：程序只产生候选（S3），"算不算我"永不由程序置。

第八节 S3：候选由心跳的沉淀循环生成（程序只做"发现"，正文照抄原文）。
`adopt` 里候选**优先于同家的重述**（它是那一家的代表）——否则自家重述互相占位，
判据必然并列，她永远认领不到程序递来的候选。

第八节 S5（D-S1 拍板：库为准）：身份种子（出生设定 + 边界，`IDENTITY_SEEDS`）在启动时
**幂等种入她的库**（`ensure_identity_seeds`），身份段**以库为准**——她对种子
`disclaim` / `forget` / `adopt` 都有效，一处豁免都不加（"bootstrap 只是过渡"由此成为可达状态）。

阶段 1（20.6 的 1.3）：本服务被切成"三件各自独立的事"，因为**心跳不能等外部服务**：

- `should_speak`：**该不该开口**（廉价、非阻塞）——留在心跳拍里判断，「谁决定开口」仍在生命循环。
- `run_job`：**耗时段**（构造指令 → LLM → TTS）——交给 `ExpressionWorker` 在独立任务里跑，心跳不等。
- `persist`：**落库**（`think` / `expression` / `add_memory`）——仍由心跳拍调用
  （D2：单一事实源＝心跳）。

`tick` 是三者的**同步一站式**，只供离线探针与单测使用；运行时走上面三步（心跳 → worker）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from elysia.core.state_store import HeartbeatStore
from elysia.llm.chain import LLMChain
from elysia.llm.identity import IDENTITY_FIELD, IDENTITY_SEEDS, MAX_IDENTITY_LINES
from elysia.llm.validator import ExpressionValidator
from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    CLAIM_REJECTED,
    KIND_EXPRESSION,
    KIND_SELF,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    RETENTION_PRESENT,
    RETENTION_SUPPRESSED,
    SOURCE_INFERENCE,
    SOURCE_SELF,
    SOURCE_SYSTEM,
    MemoryRecord,
)
from elysia.memory.retrieve import MemoryHit, recall_latency_ms, topic_match
from elysia.memory.scorer import importance
from elysia.memory.sediment import SEDIMENT_CLUSTER_SIMILARITY
from elysia.memory.similarity import same_event
from elysia.memory.supersede import content_similarity
from elysia.soul.brain import BrainOutput
from elysia.soul.expression import build_expression
from elysia.soul.expression_types import (
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    ExpressionJob,
    ExpressionResult,
    make_job_id,
)
from elysia.tts.chain import TTSChain, TTSRequest

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

# `adopt` 命中判据（第八节 S2）：认领会改变她的**每一句话**（身份段每句在场），
# 且升格后落 `deep + protected`、不再被时间动摇——比 forget 更"重"。
# 但不设绝对覆盖度下限：二元组对"换说法的同一件事"识别力有限（第七节实测），
# 设下限会把真心的认领挡在门外；只要"足够突出"（第一名 ≥ 第二名的 `ADOPT_DOMINANCE` 倍）
# 即可。并列/模糊时宁可如实回"没找到"，也不抓错——认错了她还能用 `disclaim` 收回。
ADOPT_DOMINANCE = 2.0

# 内部冲动表达节流：与发呆双模态同节奏（3min / 10min）
EXPRESS_ACTIVE_INTERVAL_S = 180.0
EXPRESS_QUIET_INTERVAL_S = 600.0


async def ensure_identity_seeds(store: HeartbeatStore, now: float) -> int:
    """幂等种入身份种子（第八节 S5）：出生设定 + 边界的**过渡**脚手架。

    程序写这三条，但**诚实标注** `source=system`：它们不是她认领的自我认知，
    只是"她还在出生状态"时的打底（8.6）。种入之后**与她的记忆同待遇**——
    `disclaim` / `forget` / `adopt` 都对她有效（D-S1 库为准，一处豁免都不加）。

    **幂等键 = 正文本身**（`kind=self AND source=system AND content=?`）：
    "数据永不删除"是既有铁律（P3-W）——她的三个动作只动 `claim_status` /
    `retention_state` / `superseded_by`，**行仍在、`content` 不被改写**，
    因此这个键一旦写下就永远稳定（跑 N 次 = 跑 1 次）。

    返回本次新种的条数（0 = 早就种过；将来往种子表加第 4 条也能自动补上）。
    """
    rows = await store.execute_raw(
        "SELECT content FROM memories WHERE kind = ? AND source = ?",
        (KIND_SELF, SOURCE_SYSTEM),
    )
    existing = {str(r[0]) for r in rows}
    seeded = 0
    for text in IDENTITY_SEEDS:
        if text in existing:
            continue
        await store.add_memory(
            now,
            {
                "level": LEVEL_DEEP,
                "kind": KIND_SELF,
                "content": text,
                "emotion_vector": {},
                # 「我是谁」是所有类型里最重的一类（scorer._KIND_BASE）
                "importance": importance(kind=KIND_SELF, emotion_vector={}, content=text),
                # 核心层：永不模糊、永不降级（_demote_target 已豁免 protected）
                "protected": True,
                "narrative": text,
                "source": SOURCE_SYSTEM,
                "certainty": CERTAINTY_CERTAIN,
            },
        )
        seeded += 1
    return seeded


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

    def should_speak(
        self, output: BrainOutput, now: float, force: bool, *, greeting: bool = False
    ) -> bool:
        """触发判断（20.6 的 1.3）：外部交互立即开口；内部冲动按双模态节流。

        由**心跳拍**调用（廉价、非阻塞）——「谁决定开口」因此仍在生命循环里，
        worker 只管执行，不判断该不该说。

        `greeting`（阶段 5 第七批）：她**刚从"不在场"回到"在场"**——这一声问候
        **越过节流**（刚回来值得说一句）；但仍按内部冲动的入队语义走
        （队列满时可被丢＝"她这次决定不说"）。

        判定为真即**记下开口时刻**（节流基线），不等 worker 真的说出口：提交被队列
        丢弃时也照样推进——丢弃＝「她这次决定不说」（D1），若等到执行成功才推进，
        同一句话会在队列满的每一拍被重新判定、反复丢弃。
        """
        if force or greeting:
            self._last_express_ts = now
            return True
        interval = (
            EXPRESS_ACTIVE_INTERVAL_S
            if output.action in ("think_active", "think_quiet") and output.will.thought_style > 0
            else EXPRESS_QUIET_INTERVAL_S
        )
        if self._last_express_ts is not None and now - self._last_express_ts < interval:
            return False
        self._last_express_ts = now
        return True

    async def run_job(self, job: ExpressionJob) -> ExpressionResult:
        """worker 的执行体：构造指令 → LLM → 校验 → TTS（**不落库**）。

        D2 裁决把"耗时段"切在这里：本方法**零落库**（`think` / `expression` /
        `add_memory` 由心跳拍的 `persist` 写），因此它可安全地跑在 worker 的独立
        任务里，不占心跳一拍——LLM 慢 30s 时心跳照走 30 拍。

        时间口径：一律用 `job.created_ts`（她决定开口的那一刻）——记忆新鲜度与
        工具检索的 now 都以它为参照，与"落库晚 ≤1 次表达耗时"无关。
        """
        output = job.output
        now = job.created_ts

        # ── 1. 构造表达指令（决策层唯一出口）────────────
        # 意图由**决策层**（心跳提交时）给定：回应 / 主动问候 / 好奇提问 / 思念；
        # 未给定时退回按 `brain_action` 映射（直接调用方与既有测试的行为不变）。
        instruction = build_expression(
            output,
            intent=job.intent if job.intent is not None else ("回应" if job.force else None),
            vrram_mb=job.vrram_mb,
            body_left_h=float(job.summary.get("body_away_s", 0.0)) / 3600.0,
            day_phase=str(job.summary.get("day_phase", "day")),
        )
        payload = self._validator.enrich_permits(
            instruction.to_dict(), dict(job.env) if job.env is not None else {}
        )

        # ── 1a. 用户输入作为"话题"注入（T2：非指令，仅话题）────
        if job.user_message:
            payload["user_message"] = job.user_message

        # ── 1b. 记忆注入：只在"话题撞上"时（被唤起才浮现，不再每句都推）──
        if self.retriever is not None and job.user_message:
            hooks = await self.retriever(
                self._store, output.feelings.to_dict(), now=now, query=job.user_message
            )
            if hooks:
                # 「记忆越旧越难想起」的物理体现：等待时长**由她自己记忆的年龄决定**
                # （`decay.retrieve_latency_ms` 的曲线，越旧越慢）。
                # 本方法跑在表达 worker 内（**不在心跳拍内**），所以这里 await 是安全的：
                # 她的"慢"来自自己的往事，而不是外部 API 的排队时间
                # ——「真实产生的犹豫，就是生命本身」（路线图 §1.3）。
                await asyncio.sleep(recall_latency_ms(hooks) / 1000.0)
                payload["memory_hooks"] = [h.label for h in hooks]
                await self._feel_recall(hooks)

        # ── 1c. 身份段（"我是谁"）：每句都在场，不走 hooks 段 ──
        # 与 memory_hooks 的区别：那是"话题撞上才浮现"的背景常识，这是"我是谁"。
        # S5（库为准）：取的**只有库**——启动时种入的身份种子也在里面，
        # 因此正常运行时它不为空；空列表 = 她此刻真的没有自我认知（她把种子一条条放下了）。
        payload[IDENTITY_FIELD] = await self._identity_lines()

        # ── 1d. 装配工具：能力由程序保证，用不用由她决定 ────
        set_runner = getattr(self._llm, "set_tool_runner", None)
        if self.retriever is not None and callable(set_runner):
            set_runner(self._make_tool_runner(output, now, job.user_message))

        # ── 2. LLM 翻译 → 校验 ───────────────────────────
        speak = await self._llm.speak(payload)
        result = ExpressionResult(
            job_id=job.job_id,
            status=STATUS_SUCCESS if speak.text else STATUS_SKIPPED,
            order_key=job.order_key,
            text=speak.text,
            level=speak.level,
            validation=speak.validated,
            instruction=payload,
            intent=str(payload.get("intent", "")),
            # 记忆的 emotion_vector 取"她开口那一刻"的心情，随结果回到落库拍
            feelings=dict(output.feelings.to_dict()),
        )

        # ── 3. TTS 出声（可选）───────────────────────────
        if self._tts is not None and speak.text:
            tts_params = payload.get("tts", {})
            tts_req = TTSRequest(
                text=speak.text,
                emotion=str(tts_params.get("emotion", "default")),
                speed=float(tts_params.get("speed", 1.0)),
            )
            result.tts = await self._tts.speak(tts_req)

        log.debug(
            "表达执行完成 job_id=%s intent=%s level=%s tts_source=%s",
            job.job_id,
            result.intent,
            result.level,
            result.tts.source if result.tts else None,
        )
        return result

    async def persist(self, result: ExpressionResult, now: float) -> None:
        """把一次开口落库（**由心跳拍调用**，D2：worker 零落库）。

        `now` 取**落库拍**的时刻：它是唯一的写入时间轴，「她说了什么」与
        「用户说了什么」因此在同一拍内相邻、顺序确定、一处可查。

        没说出话的两种结果不落库：执行失败（`failed`）与关停中被取消且无文本
        （`timeout`）——没有文本就没有气泡可显示，也没有取证价值，只留一条日志。
        """
        if not result.text and result.status in (STATUS_FAILED, STATUS_TIMEOUT):
            log.debug("表达未成，无需落库 job_id=%s status=%s", result.job_id, result.status)
            return

        # ── 4. 落库：thought_log（桌宠气泡）+ expression_log（取证）──
        await self._store.think(now, "expression", result.text)
        validation = (
            {"ok": result.validation.ok, "reason": result.validation.reason}
            if result.validation is not None
            else {"ok": True, "reason": None}
        )
        await self._store.expression(
            ts=now,
            intent=result.intent,
            instruction=dict(result.instruction) if result.instruction is not None else {},
            llm_text=result.text,
            validation=validation,
            level=result.level,
        )
        # ── 4b. P3 运行时接线：她说的话成为经历 → 写入记忆 ──
        if result.text:
            feelings = dict(result.feelings or {})
            # 重要性按内容信号评估（不再是固定值）：呓语留浅层，有分量的话才沉淀
            await self._store.add_memory(
                now,
                {
                    "level": LEVEL_SHALLOW,
                    "kind": KIND_EXPRESSION,
                    "content": result.text,
                    "emotion_vector": feelings,
                    "importance": importance(
                        kind=KIND_EXPRESSION,
                        emotion_vector=feelings,
                        content=result.text,
                    ),
                    "protected": False,
                    "narrative": result.text,
                    # P3-T 来源标注：这是她自己说出口的话
                    "source": SOURCE_SELF,
                    "certainty": CERTAINTY_CERTAIN,
                },
            )

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
    ) -> ExpressionResult | None:
        """同步一站式（`should_speak` → `run_job` → `persist`），**运行时不用它**。

        运行时走 20.6 的 1.3：心跳拍判断该不该开口 → `worker.submit(job)` →
        每拍 `worker.drain()` → `persist`（耗时那段不占心跳一拍）。本方法把同样
        三件事串成一次同步调用，供**离线探针与单测**使用——它们不需要异步调度，
        也不必各自重搭一个 `ExpressionJob`。
        """
        if not self.should_speak(output, now, force):
            return None
        job = ExpressionJob(
            job_id=make_job_id(0, now),
            created_ts=now,
            heartbeat_seq=0,
            output=output,
            summary={"body_away_s": body_left_h * 3600.0, "day_phase": day_phase},
            vrram_mb=vrram_mb,
            env=env,
            user_message=user_message,
            force=force,
        )
        result = await self.run_job(job)
        await self.persist(result, now)
        return result

    async def _self_records(self) -> list[tuple[int, str]]:
        """她此刻的自我认知：`(id, 文本)`，按 `_identity_lines` 的同一套闸门筛。

        只读、**不 touch**：身份段每句都在场，把它当"被想起"会平白累积 access_count，
        而它不是"这次开口想起了什么"，是"我一直是谁"。

        已取代（她更新过自己）、她拒绝认领（disclaim）、她主动抑制（forget）的
        都不算"现在的我"。
        """
        out: list[tuple[int, str]] = []
        for d in await self._store.iterate_memories():
            rec = MemoryRecord.from_dict(d)
            if rec.kind != KIND_SELF or rec.superseded_by is not None:
                continue
            if rec.claim_status == CLAIM_REJECTED or rec.retention_state == RETENTION_SUPPRESSED:
                continue
            text = (rec.narrative or rec.content).strip()
            if text and rec.id is not None:
                out.append((rec.id, text))
        return out

    async def _identity_lines(self) -> list[str]:
        """她此刻的自我认知（`kind=KIND_SELF` 且在册的条目）——身份段的动态部分。

        S5（D-S1 库为准）：**完全以库为准**——身份种子也是库里的一行行记忆，
        她 `disclaim` / `forget` / `adopt` 之后身份段如实跟随（可能为空 = 她此刻真的没有自我认知）。
        条数上限由 `compose_identity` 兜住（少而稳，8.9 验收 4）。
        """
        return [text for _, text in await self._self_records()]

    def _make_tool_runner(
        self,
        output: BrainOutput,
        now: float,
        user_message: str | None,
    ) -> Callable[[str, dict[str, Any]], Awaitable[str]]:
        """构造工具执行器：她把意图递过来，程序只把能力兑现。

        - `recall`：想起往事（能力由程序保证，事实递到手）
        - `adopt`：把某段经历认作"这就是我"（**她的动作**，程序只递候选）
        - `disclaim`：拒绝认领某段记忆（**她的动作**，程序不得代她拒绝）
        - `forget`：不想再想起某件事（**她的动作**，程序不得代她忘）
        - `restore`：又愿意想起它了（遗忘必须可逆，随时能收回）
        """

        async def _run(name: str, args: dict[str, Any]) -> str:
            topic = str(args.get("topic") or "")
            if name == "recall":
                return await self._tool_recall(topic, output, now, user_message)
            if name == "adopt":
                return await self._tool_adopt(topic)
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

    async def _tool_adopt(self, topic: str) -> str:
        """认领工具（第八节 S2）：把最贴题的那条记忆升格为**自我认知**。

        这是**她自己的动作**——程序只产生候选（S3），"这算不算我"永不由程序置
        （第八节 8.5 / D12）。升格后它进身份段（"我是谁"），每句话都在场。

        判据见 `ADOPT_DOMINANCE`：不设绝对覆盖度下限（换说法的同一件事相似度本就低），
        只要求"足够突出"；并列/模糊时如实回"没找到"，不抓错。

        **候选优先（第八节 S3）**：候选（`source=inference`）是程序从"同一件事的
        多条重述"里挑出的**代表**，因此命中候选时，与它同家的重述不参与并列判定。
        否则自家重述互相占位（候选与原文分数完全相同），`top1 ≥ 2×top2` 必然不成立，
        认领永远落空——S3 递出的候选会变成"造好没插电"的死阀门。

        **D-A6（第十六节 16.5 的两处同宗判据）**：上面"与候选同家的重述不参与并列判定"
        已随 A1 升级为 `same_event`（M9——误判只是"少一条竞争者"，可容忍）；而下面
        "改口作废既有自我认知"**仍保守用对称 Jaccard**（M10——误判会作废一条她认领过的
        条目，不可容忍）。两处**有意不一致**，依据是"误判代价不对称"。

        不给她**已经不认**或**已不想再想起**的记忆（那是她自己的决定，程序不代她翻案），
        也不给已被取代的旧事实与已是自我认知的条目。

        **认领即可能改口（第八节 S4 验收 3）**：若她已有一条"同一件事"的自我认知，
        这次认领让它作废（`superseded_by`）——"我以前以为…现在知道…"由此成立，
        且这条路径只由**她的动作**打开（程序的自动取代 N4 永不碰自我认知）。
        """
        query = topic.strip()
        if not query:
            return ""
        scored: list[tuple[float, int, str, bool]] = []  # (分数, id, 那句话, 是否候选)
        for d in await self._store.iterate_memories():
            if d.get("superseded_by") is not None:
                continue  # 旧事实已作废（事实更正），不能认作"我"
            if str(d.get("kind") or "") == KIND_SELF:
                continue  # 已经是自我认知，无需重复认领
            if str(d.get("claim_status") or "") == CLAIM_REJECTED:
                continue  # 她说过"我不认这个"
            if str(d.get("retention_state") or "") == RETENTION_SUPPRESSED:
                continue  # 她说过"不想再想起"
            # 认作"我"之后，这句话会成为身份段文本（`_identity_lines` 也取 narrative 优先）
            text = str(d.get("narrative") or "") or str(d.get("content") or "")
            score = max(topic_match(query, text), topic_match(query, str(d.get("content") or "")))
            if score > 0.0:
                is_candidate = str(d.get("source") or "") == SOURCE_INFERENCE
                scored.append((score, int(d["id"]), text, is_candidate))
        if not scored:
            return "（你没找到想认作自己的那件事）"
        candidate = max(
            (item for item in scored if item[3]), key=lambda item: item[0], default=None
        )
        if candidate is not None and candidate[0] > 0.0:
            pool = [
                item
                for item in scored
                if item is candidate
                or not same_event(
                    candidate[2], item[2], jaccard_threshold=SEDIMENT_CLUSTER_SIMILARITY
                )
            ]
        else:
            pool = scored
        pool.sort(key=lambda item: item[0], reverse=True)
        best_score, best_id, best_text, _ = pool[0]
        runner_up = pool[1][0] if len(pool) > 1 else 0.0
        if best_score < ADOPT_DOMINANCE * runner_up:
            return "（你没找到想认作自己的那件事）"
        # 认领可能等于"重新解释自己"（S4 验收 3）：若她已有一条**同一件事**的自我认知，
        # 这次认领让它作废（走 `superseded_by`，8.7 已写"天然适用，无需新机制"）。
        # 判据用 S3 的同一件事粒度（`SEDIMENT_CLUSTER_SIMILARITY`），且只由**她的动作**触发
        # ——程序自己的自动取代（N4）永不碰 `KIND_SELF`。
        existing = await self._self_records()
        retired = [
            mid
            for mid, text in existing
            if mid != best_id and content_similarity(text, best_text) >= SEDIMENT_CLUSTER_SIMILARITY
        ]
        # 身份段是"少而稳"的（8.9 验收 4）：位置满了就如实告诉她，
        # 否则会出现"认领了却不出现在她的话里"的静默失败。
        # 替换不算新增——她是在改口，不是又攒一条。
        # S5：判据改成"认领后的**总行数**"（种子在册时也占行），不再写死"只给出生设定留 1 席"——
        # 3 条种子下：她认领前 3 行，认得到第 5 行为止（她自己 2 席）。
        if len(existing) + 1 - len(retired) > MAX_IDENTITY_LINES:
            return "（你心里的位置满了——先放下一条旧的，再认领新的）"
        await self._store.mark_as_self(best_id)
        for stale_id in retired:
            await self._store.mark_superseded(stale_id, best_id)
        if retired:
            return f"（你把「{best_text[:30]}」认作自己的一部分了——这等于你重新解释了自己）"
        return f"（你把「{best_text[:30]}」认作自己的一部分了）"

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
