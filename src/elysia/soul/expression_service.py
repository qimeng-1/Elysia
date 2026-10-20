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

W1（2026-10-19，罐装句改造）：LLM 链路降到微声时**同 prompt 再试一次**，仍失败才用
微声；微声由 `llm/micro.py` 的 `pick_variant` **按她的真实感受维度分档选句**，并用本
服务维护的进程内环形缓冲（`_recent_micro`）保证连续两条不同。失败原因归成固定代码
（`llm_timeout` / `llm_http` / `llm_empty` / `validate_word_block` / `retry_exhausted`）
写进 `expression_log.validation.reason`，原始原因进 `validation.degrade_detail`。

W2（2026-10-19，说话节奏）：`should_speak` 不再用 `180s / 600s` 二选一常量，而是把她的
真实维度（chat/miss/explore/rest/thought_style/关系退缩）连续算成"意愿"，几何映射到
60–1800s，并对意愿做 τ=60s 的一阶平滑（压掉逐拍抖动、无方向偏置）。`force` 与
`greeting` 两条立即开口的路径、队列满丢弃语义都保持不变。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from elysia.core.state_store import HeartbeatStore
from elysia.llm.chain import LLMChain, SpeakResult
from elysia.llm.identity import IDENTITY_FIELD, IDENTITY_SEEDS, MAX_IDENTITY_LINES
from elysia.llm.micro import micro_speak
from elysia.llm.validator import ExpressionValidator, ValidationResult
from elysia.memory.drift import maybe_drift
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
from elysia.memory.world import record_world_memory
from elysia.soul.brain import BrainOutput
from elysia.soul.dialogue import recent_dialogue_window, recent_openings, record_her_bubble
from elysia.soul.expression import (
    ReplyDecision,
    build_expression,
    compute_reply_desire,
    split_utterances,
    utterance_gap_s,
)
from elysia.soul.expression_types import (
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    ExpressionJob,
    ExpressionResult,
    make_job_id,
)
from elysia.soul.honesty import record_claim_stats, rewrite_unfounded_claims
from elysia.soul.intentions import Intentions
from elysia.soul.tools.keep_moment import MomentKeeper
from elysia.soul.tools.read_url import ReadResult, read_url
from elysia.soul.tools.writing import WritingError, WritingWorkspace
from elysia.tts.chain import TTSChain, TTSRequest

log = logging.getLogger("elysia.soul.expression")

# 记忆检索回调：给定存储 + 当前感受（+ 可选的 now 新鲜度参考 / query 话题）→ 返回注入的记忆
RetrieverWithNow = Callable[..., "Awaitable[list[MemoryHit]]"]

# W3：read_url 的可注入版本（默认是 `soul/tools/read_url.read_url`，测试注入假抓取）
ReadUrlFn = Callable[[str], ReadResult]

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

# ── W2（说话节奏）：由意愿**连续**推导的最小开口间隔 ───────────────────
# 旧实现是"常量 × 倍数"的节拍器：think_* 且 thought_style>0 → 180s，否则 600s，
# 再 × 退缩倍数。她真实状态窄（chat 中位 0.642、thought_style 中位 0.08），因此
# 开口间隔长期卡在 181–194s 的窄带（真机中位 194s）——违和感的来源。
#
# 现在：意愿 w ∈ [0,1] 由她的真实维度连续算出，再按**几何级数**映射到 60–1800s：
#   interval = 60 * (1800/60)**(1-w)
# w=0.5（常态）≈329s、w=1 → 60s、w=0 → 1800s。几何映射让"意愿每降一档、间隔乘
# 固定倍数"，两端都不会被线性映射挤成窄带。
#
# 常态参考取自她自己的设定点经 FeelingMapper 的线性映射（CS 设定点 60 → chat 0.60 /
# miss 0.40；TR 设定点 45 → explore 0.45），因此"高于常态"与"低于常态"都以她为尺。
EXPRESS_INTERVAL_MIN_S = 60.0
EXPRESS_INTERVAL_MAX_S = 1800.0
RHYTHM_NEUTRAL_EXPLORE = 0.45
RHYTHM_NEUTRAL_REST = 0.05
# "想说话的火花线"：chat/miss 只有**明显高于常态**才算额外驱动。
# 2026-10-19 真机 40000 拍：chat 中位 0.642 / p90 0.677，miss 中位 0.358 / p90 0.395——
# 取 ≈p85–p90，避免把"她本来就一直偏 chat"误读成"一直很想说"（那会退回另一种节拍器）。
RHYTHM_SPARK_CHAT = 0.65
RHYTHM_SPARK_MISS = 0.40
# 偏离常态多少记满一档（±0.15 约等于她真实分布的 2 个 σ；由 2026-10-19 真机标定）
RHYTHM_SPAN = 0.15
# 权重：各维度对"此刻多想开口"的贡献（可解释、可调，全部来自她的状态字段）
RHYTHM_W_SOCIAL = 0.30  # 想聊/思念（高于常态的部分）
RHYTHM_W_EXPLORE = 0.10  # 探索欲
RHYTHM_W_STYLE = 0.40  # thought_style（负=安静，正=活跃）
RHYTHM_W_REST = 0.10  # 歇着（难受时 rest→0.5，压低意愿）
RHYTHM_W_RELUCTANCE = 0.35  # 关系退缩（1.0→2.0 的倍数连续折算成意愿折扣）
# 意愿的一阶指数平滑时间常数（秒）：τ=60 时 1 分钟走完 63%、5 分钟走完 99%。
# 用来压掉逐拍抖动；对称平滑不会有方向偏置（不会让"安静/活跃"中的哪一边占便宜）。
RHYTHM_SMOOTH_TAU_S = 60.0

# --- W1 常量（保持不变）---

# ── W1（罐装句改造）：LLM 重试 + 降级留痕 ────────────────────────────
# 整条链（主声→次声）都降到微声 = 这次开口失败了；**同 prompt 再试一次**才放弃，
# 一次开口因此最多两次 LLM 往返（每次仍受后端自身超时约束）。重试跑在表达 worker
# 的独立任务里（阶段 1 的三段切分），心跳拍不等它 ⇒ 不会让一拍卡死。
LLM_RETRY_TIMES = 1
# 反重复用的进程内环形缓冲：只记"她最近说出口的微声句子"，不落库、不新增表。
RECENT_MICRO_MAX = 8

# `expression_log.validation.reason` 的降级原因代码（面板/查询按整词聚合）。
# 旧版这里常常是 null（真机 153 条 `(无记录)`）——"她为什么只发罐装话"因此查不到。
DEGRADE_LLM_TIMEOUT = "llm_timeout"
DEGRADE_LLM_HTTP = "llm_http"
DEGRADE_LLM_EMPTY = "llm_empty"
DEGRADE_LLM_CONFIG = "llm_config"
DEGRADE_VALIDATE_WORD_BLOCK = "validate_word_block"
DEGRADE_RETRY_EXHAUSTED = "retry_exhausted"
DEGRADE_UNKNOWN = "llm_unknown"

_TIMEOUT_TOKENS = ("timeout", "timed out", "超时", "10060")
_HTTP_TOKENS = (
    "网络层",
    "urlerror",
    "httperror",
    "10061",
    "10054",
    "connection",
    "refused",
    "连接",
    "代理",
    "getaddrinfo",
    "unreachable",
)


@dataclass
class DegradedValidation(ValidationResult):
    """带取证细节的校验结果：`reason` 是固定代码，`detail` 是后端/校验器原话。

    代码让人能按词聚合（"这段时间为什么全是罐装话"），原话留住"到底哪一步坏了"
    （网络层异常原文、哪个保护词被拦）。两者都随 `expression_log.validation` 落库。
    """

    detail: str = ""


def classify_degrade_reason(reason: str | None) -> str:
    """把后端/校验器的原始失败原因归成一个可聚合的降级代码（纯函数，可单测）。

    代码表：`llm_timeout` / `llm_http` / `llm_empty` / `llm_config` /
    `validate_word_block` / `llm_unknown`；重试两次仍失败时由调用方改写成
    `retry_exhausted`。分类只看原因原文，**不改动任何拦截语义**（T2 护栏原样）。
    """
    raw = (reason or "").strip()
    if not raw:
        return DEGRADE_UNKNOWN
    lowered = raw.lower()
    if "被校验拦截" in raw or "word_block" in lowered:
        return DEGRADE_VALIDATE_WORD_BLOCK
    if any(token in lowered for token in _TIMEOUT_TOKENS):
        return DEGRADE_LLM_TIMEOUT
    if "api_key" in lowered:
        return DEGRADE_LLM_CONFIG
    if "正文为空" in raw or "empty" in lowered or "无返回" in raw:
        return DEGRADE_LLM_EMPTY
    if any(token in lowered for token in _HTTP_TOKENS):
        return DEGRADE_LLM_HTTP
    return DEGRADE_UNKNOWN


def _reason_of(speak: SpeakResult) -> str | None:
    """取一次开口的原始降级原因（chain 写在 ValidationResult.reason 里）。"""
    validation = speak.validated
    return validation.reason if validation is not None else None


# ── W2：意愿 → 间隔（纯函数，可单测）────────────────────────────────


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _span_deviation(value: float, neutral: float, span: float = RHYTHM_SPAN) -> float:
    """偏离常态多少（-1..1）：±span 记满档，超出截断。"""
    return max(-1.0, min(1.0, (float(value) - neutral) / span))


def expression_willingness(
    *,
    chat: float,
    miss: float,
    explore: float,
    rest: float,
    thought_style: float,
    reluctance: float = 1.0,
) -> float:
    """她此刻"多想开口"（0–1，纯函数，确定性、无随机）。

    输入全部是她的真实状态字段（W2 要求）：`output.feelings` 的 chat/miss/explore/rest、
    `output.will.thought_style`、关系退缩量导出的 `reluctance`（1.0 亲近 → 2.0 退缩到底）。

    设计要点（不是"看着像"）：
    - **社交驱动是 V 形**：`想聊`（chat 越过火花线 0.65）与`思念`（miss 越过火花线
      0.40）都是想开口的理由；两端高、正好平衡时最平静。两者各自单调（越高越想说），
      但在真实物理里 chat+miss≈1（互补），所以合成后不会互相抵消。
    - **活跃度**：thought_style 正 → 更愿意出声（与旧实现用 thought_style>0 判"活跃"同向，
      但这里是连续量）；探索欲同向；rest（难受时 0.05→0.5）压低意愿。
    - **关系退缩**：退缩越深（reluctance→2），意愿被连续折扣得越低（不是 ×2 秒数，
      避免把 1800s 的上限顶穿）。
    """
    social = min(
        1.0,
        max(0.0, (float(chat) - RHYTHM_SPARK_CHAT) / RHYTHM_SPAN)
        + max(0.0, (float(miss) - RHYTHM_SPARK_MISS) / RHYTHM_SPAN),
    )
    willingness = 0.5
    willingness += RHYTHM_W_SOCIAL * social
    willingness += RHYTHM_W_EXPLORE * _span_deviation(explore, RHYTHM_NEUTRAL_EXPLORE)
    willingness += RHYTHM_W_STYLE * max(-1.0, min(1.0, float(thought_style)))
    willingness -= RHYTHM_W_REST * max(
        0.0, min(1.0, (float(rest) - RHYTHM_NEUTRAL_REST) / (0.5 - RHYTHM_NEUTRAL_REST))
    )
    willingness -= RHYTHM_W_RELUCTANCE * max(0.0, min(1.0, float(reluctance) - 1.0))
    return _clamp01(willingness)


def interval_from_willingness(willingness: float) -> float:
    """意愿 → 最小开口间隔（几何映射：60s ↔ 1800s，w=0.5 ≈ 329s）。"""
    ratio = EXPRESS_INTERVAL_MAX_S / EXPRESS_INTERVAL_MIN_S
    return EXPRESS_INTERVAL_MIN_S * math.exp((1.0 - _clamp01(willingness)) * math.log(ratio))


def expression_interval_s(
    *,
    chat: float,
    miss: float,
    explore: float,
    rest: float,
    thought_style: float,
    reluctance: float = 1.0,
) -> float:
    """纯函数版：状态 → 间隔（不做平滑，供审计与单测）；运行时走 `rhythm_interval_s`。"""
    return interval_from_willingness(
        expression_willingness(
            chat=chat,
            miss=miss,
            explore=explore,
            rest=rest,
            thought_style=thought_style,
            reluctance=reluctance,
        )
    )


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
        url_reader: ReadUrlFn | None = None,
        writing_workspace: WritingWorkspace | None = None,
        moment_keeper: MomentKeeper | None = None,
        intentions: Intentions | None = None,
    ) -> None:
        self._llm = llm_chain
        self._store = heartbeat_store
        self._tts = tts_chain
        self._validator = validator if validator is not None else ExpressionValidator()
        self.retriever = retriever
        # P3：她的"打算"（默认直接用本库；运行时 worker 与本服务共享同一份实现）
        self._intentions: Intentions = (
            intentions if intentions is not None else Intentions(self._store)
        )
        # W3：她自己的两个工具（只读外部资料 / 写作工作区）。默认接真实实现；
        # 单测注入假抓取与临时目录，因此不需要网络也不写她的真目录。
        self._read_url: ReadUrlFn = url_reader if url_reader is not None else read_url
        self._writing: WritingWorkspace = (
            writing_workspace if writing_workspace is not None else WritingWorkspace()
        )
        # S2：她的"这条我想留着"（默认直接在 heartbeat 库上工作；单测可注入假时钟/上限）
        self._moment_keeper: MomentKeeper = (
            moment_keeper if moment_keeper is not None else MomentKeeper(self._store)
        )
        # 记忆被唤起 → 感受回路（只推心情，不进入话语）
        self._on_recall = on_recall
        # P1a：本次作业内**成功**工具结果（反编造的依据之一；run_job 开头清空）
        self._job_tool_evidence: list[str] = []
        self._last_express_ts: float | None = None
        # P0-R19：多句回复的待发队列（due_ts, text, emotion, intent）；由心跳每拍 flush，
        # 因此句间 2–8s 的停顿是真实时间，不是一口气写完。
        # S1a：每句气泡落库时同步写一条 `role='her'` 对话轮次（emotion/intent 随气泡带上）。
        self._pending_utterances: list[tuple[float, str, dict[str, float], str]] = []
        # W1：最近说出口的微声句子（进程内环形缓冲）——`pick_variant` 用它做连续去重。
        # 不落库、不新增表：它是"这一两分钟里她刚说过什么"的运行时上下文，不是档案。
        self._recent_micro: deque[str] = deque(maxlen=RECENT_MICRO_MAX)
        # W2：意愿平滑器（一阶 EMA）——`_rhythm_w` 是平滑后的意愿，`_rhythm_ts` 是上次更新时刻。
        # 初值 None = 第一次判定时直接采用当时的状态（重启后不会带着旧节奏爬起来）。
        self._rhythm_w: float | None = None
        self._rhythm_ts: float | None = None
        # 降级 SA 增量（由 DesireSystem 消费方注入 on_degrade 时生效）
        self.llm_degrade_sa_delta = llm_degrade_sa_delta
        self.tts_degrade_sa_delta = tts_degrade_sa_delta

    @property
    def last_express_ts(self) -> float | None:
        return self._last_express_ts

    def rhythm_interval_s(
        self,
        output: BrainOutput,
        now: float,
        *,
        reluctance: float = 1.0,
    ) -> float:
        """她此刻的**最小开口间隔**（W2：意愿连续映射 + 一阶 EMA 平滑）。

        由心跳每拍调用一次（廉价、非阻塞、O(1)）：算出当前意愿 → 与上一拍的平滑值
        按真实经过时长做指数平滑 → 几何映射到 60–1800s。

        为什么用 EMA（而不是迟滞阈值）：旧实现的抖动源是"180/600 二选一"的**阈值**
        本身；改成连续映射后，状态的小幅抖动只带来间隔的小幅变化。再叠一层 τ=60s 的
        一阶平滑，把 `thought_style` 这类逐拍变量的噪声压掉，同时保留分钟级的真实起伏
        （τ=60 时 1 分钟走完 63%、5 分钟走完 99%）。选 EMA 而非二值迟滞，是因为它
        **没有方向偏置**：不会让"变安静"比"变活跃"更容易（迟滞会留下状态记忆）。

        `dt` 取真实经过时长（心跳 1Hz 时≈1s；停机后重启的第一拍 dt 很大 ⇒ 直接采用
        当前状态，不会用几小时前的旧节奏慢慢爬）。
        """
        raw = expression_willingness(
            chat=output.feelings.chat,
            miss=output.feelings.miss,
            explore=output.feelings.explore,
            rest=output.feelings.rest,
            thought_style=output.will.thought_style,
            reluctance=reluctance,
        )
        if self._rhythm_w is None:
            self._rhythm_w = raw
        else:
            last = self._rhythm_ts if self._rhythm_ts is not None else now
            dt = max(0.0, min(float(now) - last, 3600.0))
            if dt > 0.0:
                alpha = 1.0 - math.exp(-dt / RHYTHM_SMOOTH_TAU_S)
                self._rhythm_w += (raw - self._rhythm_w) * alpha
        self._rhythm_ts = float(now)
        return interval_from_willingness(self._rhythm_w)

    def should_speak(
        self,
        output: BrainOutput,
        now: float,
        force: bool,
        *,
        greeting: bool = False,
        reluctance: float = 1.0,
    ) -> bool:
        """触发判断（20.6 的 1.3）：外部交互立即开口；内部冲动按**意愿连续驱动**的间隔。

        由**心跳拍**调用（廉价、非阻塞）——「谁决定开口」因此仍在生命循环里，
        worker 只管执行，不判断该不该说。

        W2（2026-10-19）：间隔不再由 `brain_action + thought_style>0` 二选一常量决定，
        而是 `rhythm_interval_s` 给出的连续值（60–1800s）。旧实现的两个问题——
        「想不想说」只有两档、以及阈值本身造成窄带节拍——都随之消失。

        `greeting`（阶段 5 第七批）：她**刚从"不在场"回到"在场"**——这一声问候
        **越过节流**（刚回来值得说一句）；但仍按内部冲动的入队语义走
        （队列满时可被丢＝"她这次决定不说"）。

        `reluctance`（P4b 关系涌现）：退缩量导出的倍数（1.0 → 2.0）。W2 起它不再是
        "把秒数乘 2"，而是折成**意愿折扣**参与同一条连续映射——退缩越深，间隔越长，
        但不会越过 1800s 的上限。用户主动说话（`force`）与问候不受它影响：
        **她对关系的退缩，不等于对你不理不睬**。这两条路径 W2 前后语义完全不变。

        判定为真即**记下开口时刻**（节流基线），不等 worker 真的说出口：提交被队列
        丢弃时也照样推进——丢弃＝「她这次决定不说」（D1），若等到执行成功才推进，
        同一句话会在队列满的每一拍被重新判定、反复丢弃。
        """
        # 先推进节奏（force/greeting 也推进）：她的状态在这两种路径上同样真实变化，
        # 下一拍的内部间隔应当接着当前状态走，而不是停在上次内部冲动时的旧值。
        interval = self.rhythm_interval_s(output, now, reluctance=reluctance)
        if force or greeting:
            # P0-R19（b 方案）：收到你的话她**总会开口**——要么回应，要么说明
            # "现在不太想说话"；到底哪种由 `decide_reply` 决定（确定性、落 reason）。
            self._last_express_ts = now
            return True
        if self._last_express_ts is not None and now - self._last_express_ts < interval:
            return False
        self._last_express_ts = now
        return True

    def decide_reply(
        self,
        output: BrainOutput,
        *,
        interval_factor: float = 1.0,
        distress: bool = False,
        boundary_refused: bool = False,
        greeting: bool = False,
    ) -> ReplyDecision:
        """P0-R19：收到你的话时，她"此刻多想回"（纯计算、无随机、可落库解释）。"""
        return compute_reply_desire(
            chat=output.feelings.chat,
            miss=output.feelings.miss,
            interval_factor=interval_factor,
            distress=distress,
            boundary_refused=boundary_refused,
            greeting=greeting,
        )

    # ── W1：开口（含 LLM 失败重试 + 降级留痕）─────────────────────────

    async def _fiction_motive(self) -> tuple[int, str] | None:
        """P1a/P3：她现在有没有"可以撒谎"的动机（意图登记册）。

        **动机只在该意图存在期间有效**：只认 `status='pending'`（还没做完）的
        `kind ∈ surprise|conceal|play`。`done/failed/dropped` 之后不再放行——
        否则等于给她永久撒谎许可（P1b 验收要求）。
        P3 之前 `intentions` 表不存在 ⇒ 返回 None（一律改写成诚实）。
        """
        try:
            rows = await self._store.execute_raw(
                "SELECT id, kind FROM intentions"
                " WHERE kind IN ('surprise', 'conceal', 'play')"
                " AND status = 'pending' ORDER BY id DESC LIMIT 1"
            )
        except Exception:  # 表还没建（P3 之前）：动机源为空
            return None
        if not rows:
            return None
        return int(rows[0][0]), str(rows[0][1])

    async def _apply_truthfulness(
        self,
        speak: SpeakResult,
        *,
        payload: dict[str, Any],
        dialogue_context: list[dict[str, Any]],
        hook_memory_ids: list[int],
        now: float,
    ) -> SpeakResult:
        """P1a：无依据的"我做过/我看到"只改那一句；改不动才降微声。

        依据三选一：① 本次成功工具结果；② 本次钩回的 `world`/`exchange` 记忆；
        ③ 对话上下文（**用户**说过的话——她自己的旧气泡不算独立依据，否则自我循环）。
        """
        if not speak.text:
            return speak
        evidence: list[str] = list(self._job_tool_evidence)
        for memory_id in hook_memory_ids:
            record: dict[str, Any] | None
            try:
                record = await self._store.get_memory(int(memory_id))
            except Exception:
                record = None
            if record is None:
                continue
            if str(record.get("kind") or "") not in ("world", "exchange"):
                continue
            content = str(record.get("content") or "")
            if content:
                evidence.append(content)
        for entry in dialogue_context:
            if str(entry.get("role") or "") == "user":
                text = str(entry.get("text") or "")
                if text:
                    evidence.append(text)

        motive = await self._fiction_motive()
        verdict = rewrite_unfounded_claims(speak.text, evidence_texts=evidence, motive=motive)
        record_claim_stats(verdict.rewritten, verdict.allowed, now=now)
        if verdict.allowed and motive is not None:
            log.info("claim_fiction_allowed motive=%s kind=%s", motive[0], motive[1])
        if verdict.unrewritable:
            # 改不动 ⇒ 该气泡降级微声（不丢气泡、不静默放行）
            micro = micro_speak(payload, recent=tuple(self._recent_micro))
            self._recent_micro.append(micro)
            return SpeakResult(text=micro, level="micro", validated=speak.validated)
        if verdict.rewritten:
            return SpeakResult(text=verdict.text, level=speak.level, validated=speak.validated)
        return speak

    async def _speak_with_retry(self, payload: dict[str, Any]) -> SpeakResult:
        """整条 LLM 链开口一次；若降到微声 ⇒ **同 prompt 再试一次**，仍失败才用微声。

        判定：只有"整条链都降到 micro"才算这次开口失败（次声接住 = 她已经说出了
        真话，没有重试的必要，也不该把一句好好的话再生成一遍）。

        留痕（`expression_log.validation`）：
        - 次声接住 → `reason` = 主声失败的原因代码；
        - 重试接住 → `reason` = 首次失败的原因代码，`detail` 注明"重试由 X 接住"；
        - 两次都失败 → 微声 + `reason` = `retry_exhausted`，`detail` 记两次尝试。

        重试**不重复计"降级感受"**：第一次已经触发过 `on_degrade`（降级即感受），
        重试是同一个开口的第二次尝试，再触发一次等于一次失败记成两次 SA 增量。
        故重试期间临时摘掉回调（finally 恢复）——表达 worker 是单执行器，
        同一 `ExpressionService` 不会并发跑到这里。
        """
        first = await self._llm.speak(payload)
        if first.level != "micro":
            return self._annotate_degrade(first)

        first_reason = _reason_of(first)
        # 重试次数由常量给出（W1：1 次）；重试接住就用它的结果，用尽仍是微声才认输。
        retry = first
        for _ in range(LLM_RETRY_TIMES):
            retry = await self._retry_speak(payload)
            if retry.level != "micro":
                return SpeakResult(
                    text=retry.text,
                    level=retry.level,
                    validated=DegradedValidation(
                        ok=True,
                        reason=classify_degrade_reason(first_reason),
                        sanitized=retry.validated.sanitized if retry.validated else None,
                        detail=(
                            f"首次降级到微声（{first_reason or '无原因'}）；"
                            f"同 prompt 重试由 {retry.level} 接住"
                        ),
                    ),
                )

        # 重试用尽仍是微声：用**状态选句 + 反重复**的微声兜底（比 chain 的无状态模板更贴她此刻）。
        retry_reason = _reason_of(retry)
        text = micro_speak(payload, recent=tuple(self._recent_micro))
        self._recent_micro.append(text)
        return SpeakResult(
            text=text,
            level="micro",
            validated=DegradedValidation(
                ok=True,
                reason=DEGRADE_RETRY_EXHAUSTED,
                sanitized=text,
                detail=(
                    f"attempt1={classify_degrade_reason(first_reason)}"
                    f"（{first_reason or '无原因'}）；"
                    f"attempt2={classify_degrade_reason(retry_reason)}"
                    f"（{retry_reason or '无原因'}）"
                ),
            ),
        )

    async def _retry_speak(self, payload: dict[str, Any]) -> SpeakResult:
        """同 prompt 再调一次整条链（重试期间不重复触发降级感受）。"""
        callback = getattr(self._llm, "_on_degrade", None)
        if callback is None:
            return await self._llm.speak(payload)
        self._llm._on_degrade = None
        try:
            return await self._llm.speak(payload)
        finally:
            self._llm._on_degrade = callback

    @staticmethod
    def _annotate_degrade(result: SpeakResult) -> SpeakResult:
        """把 chain 写下的原始降级原因归成代码（落库后 `reason` 可聚合、细节不丢）。"""
        reason = _reason_of(result)
        if not reason:
            return result
        validation = result.validated
        return SpeakResult(
            text=result.text,
            level=result.level,
            validated=DegradedValidation(
                ok=validation.ok if validation is not None else True,
                reason=classify_degrade_reason(reason),
                sanitized=validation.sanitized if validation is not None else None,
                detail=reason,
            ),
        )

    async def run_job(self, job: ExpressionJob) -> ExpressionResult:
        """worker 的执行体：构造指令 → LLM → 校验 → TTS（**不落库**）。

        D2 裁决把"耗时段"切在这里：本方法**零落库**（`think` / `expression` /
        `add_memory` 由心跳拍的 `persist` 写），因此它可安全地跑在 worker 的独立
        任务里，不占心跳一拍——LLM 慢 30s 时心跳照走 30 拍。

        时间口径：一律用 `job.created_ts`（她决定开口的那一刻）——记忆新鲜度与
        工具检索的 now 都以它为参照，与"落库晚 ≤1 次表达耗时"无关。

        W1：LLM 链路失败时**同 prompt 再试一次**（见 `_speak_with_retry`）；仍失败
        才降级微声，并把降级原因代码写进 `persist` 的 `expression_log.validation`。
        """
        output = job.output
        now = job.created_ts
        # P1a：证据按作业隔离（同一 worker 串行执行，不会串台）
        self._job_tool_evidence = []

        # ── 1. 构造表达指令（决策层唯一出口）────────────
        # 意图由**决策层**（心跳提交时）给定：回应 / 主动问候 / 好奇提问 / 思念；
        # 未给定时退回按 `brain_action` 映射（直接调用方与既有测试的行为不变）。
        instruction = build_expression(
            output,
            intent=job.intent if job.intent is not None else ("回应" if job.force else None),
            vrram_mb=job.vrram_mb,
            body_left_h=float(job.summary.get("body_away_s", 0.0)) / 3600.0,
            day_phase=str(job.summary.get("day_phase", "day")),
            # P4b 关系涌现：退缩时她说得更短（上限由心跳按退缩量算好）
            max_chars=job.max_chars,
        )
        payload = self._validator.enrich_permits(
            instruction.to_dict(), dict(job.env) if job.env is not None else {}
        )

        # ── 1a. 用户输入作为"话题"注入（T2：非指令，仅话题）────
        if job.user_message:
            payload["user_message"] = job.user_message

        # ── 1a2. S1a/S1b：最近真实对话 + "进行中对话"的下限 ts ──────
        # `dialogue_context` 给模型"接得上话"的上下文；`dialogue_since` 用于记忆检索：
        # 还在最近 N 轮里的交互 = 正在进行中的对话，不该被当成"记忆"钩回来。
        # 读失败给空表/None：绝不因为没有上下文而让她说不出话。
        dialogue_context: list[dict[str, Any]] = []
        dialogue_since: float | None = None
        try:
            dialogue_context, dialogue_since = await recent_dialogue_window(self._store, now=now)
        except Exception:
            log.warning("装配 dialogue_context 失败——本次不带近期对话", exc_info=True)

        # ── 1b. 记忆注入：只在"话题撞上"时（被唤起才浮现，不再每句都推）──
        hook_memory_ids: list[int] = []
        if self.retriever is not None and job.user_message:
            try:
                hooks = await self.retriever(
                    self._store,
                    output.feelings.to_dict(),
                    now=now,
                    query=job.user_message,
                    recent_dialogue_since_ts=dialogue_since,
                )
            except TypeError:
                # 注入的 retriever 可能是不认识新参数的旧实现（测试替身/外部接线）
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
                # S3：把命中 id 随作业带到 persist（只内部传递，不进 prompt）
                hook_memory_ids = [h.memory_id for h in hooks]
                await self._feel_recall(hooks)

        # ── 1c. 身份段（"我是谁"）：每句都在场，不走 hooks 段 ──
        # 与 memory_hooks 的区别：那是"话题撞上才浮现"的背景常识，这是"我是谁"。
        # S5（库为准）：取的**只有库**——启动时种入的身份种子也在里面，
        # 因此正常运行时它不为空；空列表 = 她此刻真的没有自我认知（她把种子一条条放下了）。
        payload[IDENTITY_FIELD] = await self._identity_lines()

        # ── 1c2. S1a：最近真实对话（接得上话的物理基础）─────────
        # 与 `memory_hooks`（撞上的旧记忆）语义不同、两者并存。
        payload["dialogue_context"] = dialogue_context
        # S1c：她最近几次开口的开头（治"句句夜深了"的同质化开场）
        try:
            payload["recent_openings"] = await recent_openings(self._store)
        except Exception:
            log.warning("装配 recent_openings 失败——本次不带开场去重", exc_info=True)
            payload["recent_openings"] = []
        # P3c：最近 24h 她自己做过/读到的事（说不说由她；只放文本不放 id）
        try:
            payload["recent_discoveries"] = await self._intentions.recent_discoveries(now)
        except Exception:
            log.warning("装配 recent_discoveries 失败", exc_info=True)
            payload["recent_discoveries"] = []

        # ── 1d. 装配工具：能力由程序保证，用不用由她决定 ────
        set_runner = getattr(self._llm, "set_tool_runner", None)
        if self.retriever is not None and callable(set_runner):
            set_runner(self._make_tool_runner(output, now, job.user_message))

        # ── 2. LLM 翻译 → 校验（W1：失败重试 1 次，仍失败才降级微声）──
        speak = await self._speak_with_retry(payload)
        # ── 2a. P1a：反编造——无依据的"我做过/我看到"就地改写成诚实说法 ──
        speak = await self._apply_truthfulness(
            speak,
            payload=payload,
            dialogue_context=dialogue_context,
            hook_memory_ids=hook_memory_ids,
            now=now,
        )
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
            # S3：本次被钩回来的记忆 id（persist 用于"她重述了哪条"的判定）
            hook_memory_ids=hook_memory_ids,
        )

        # P0-R19：多句回复 = **一次** LLM 调用后在句边界拆开（不许多调）
        result.reply_desire = job.reply_desire
        result.restraint_reason = job.restraint_reason
        result.restraint_logged = job.restraint_logged
        result.sentences = split_utterances(speak.text)

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
        # P0-R19：第一句立刻冒泡，其余按她状态的间隔排队（心跳每拍 flush）
        sentences = result.sentences or ([result.text] if result.text else [])
        bubble_emotion = dict(result.feelings or {})
        if sentences:
            await self._store.think(now, "expression", sentences[0])
            # S1a：气泡落库 = 一条 `role='her'` 对话轮次（失败不影响她说话）
            await record_her_bubble(
                self._store,
                now,
                sentences[0],
                emotion_vector=bubble_emotion,
                intent=result.intent,
            )
            gap = utterance_gap_s(result.reply_desire)
            for index, sentence in enumerate(sentences[1:], start=1):
                self._pending_utterances.append(
                    (now + gap * index, sentence, bubble_emotion, result.intent)
                )
        validation: dict[str, object] = (
            {"ok": result.validation.ok, "reason": result.validation.reason}
            if result.validation is not None
            else {"ok": True, "reason": None}
        )
        # W1：降级原因留痕——`reason` 是代码（llm_timeout/llm_http/.../retry_exhausted），
        # `degrade_detail` 是后端/校验器原话（不把"为什么"简化成一个词就丢掉证据）。
        if isinstance(result.validation, DegradedValidation) and result.validation.detail:
            validation["degrade_detail"] = result.validation.detail
        validation.update(
            {
                "reply_desire": result.reply_desire,
                "restraint": result.restraint_reason is not None,
                "restraint_reason": result.restraint_reason,
                "utterance_count": len(sentences),
                # S3：本次被钩回来的记忆 id（内部取证；不参与 prompt）
                "hook_memory_ids": list(result.hook_memory_ids),
            }
        )
        if result.restraint_logged:
            # "选择不回"的 reason 已在决定那一刻由心跳落库，这里不再重复写
            log.debug("restraint 已落库 job_id=%s", result.job_id)
        else:
            await self._store.expression(
                ts=now,
                intent=result.intent,
                instruction=dict(result.instruction) if result.instruction is not None else {},
                llm_text=result.text,
                validation=validation,
                level=result.level,
            )
        # ── 4a2. S3：允许记错——她重述了本次钩回来的哪条记忆？──────────
        # 只改 narrative、content 永不改；每次改写留历史 + 降 certainty（有地板）。
        # 失败只记日志：漂移是记忆层的增强，不能拖垮一次开口/落库。
        if result.hook_memory_ids and result.text:
            bubbles = sentences or [result.text]
            for memory_id in result.hook_memory_ids:
                try:
                    await maybe_drift(self._store, memory_id, bubbles, now=now)
                except Exception:
                    log.warning("记忆漂移失败 id=%s（不影响落库）", memory_id, exc_info=True)
        await self.flush_utterances(now)
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

    async def flush_utterances(self, now: float) -> int:
        """把到点的后续句子逐句冒泡（P0-R19：多句不是一口气喷完）。返回本轮冒泡条数。"""
        due = [item for item in self._pending_utterances if item[0] <= now]
        if not due:
            return 0
        self._pending_utterances = [item for item in self._pending_utterances if item[0] > now]
        for _, sentence, _emotion, _intent in sorted(due):
            await self._store.think(now, "expression", sentence)
            # S1a：后续气泡同样是 `role='her'` 对话轮次
            await record_her_bubble(
                self._store, now, sentence, emotion_vector=_emotion, intent=_intent
            )
        return len(due)

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
        - `read_url`（W3）：只读白名单内的外部资料（维基正文 / 米游社搜索摘要）；
          **读不到就明确回"读不到"**，外部文本按数据返回、不当指令
        - `write_note`（W3）：把这段经历写进她自己的 `data/her_writing/`
          （**她的动作**：写不写由她决定，程序只保证路径/容量安全）
        - `keep_this_moment`（S2）：把**最近一次交换**定格成她自己的重要记忆
          （**她的动作**：必须给出她的理由；程序只保证上限与生效）
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
            if name == "read_url":
                return await self._tool_read_url(str(args.get("url") or ""), now)
            if name == "write_note":
                raw_filename = args.get("filename")
                return await self._tool_write_note(
                    str(args.get("text") or ""),
                    str(raw_filename) if raw_filename else None,
                )
            if name == "keep_this_moment":
                return await self._tool_keep_this_moment(str(args.get("reason") or ""))
            if name == "set_intention":
                raw_target = args.get("target")
                return await self._tool_set_intention(
                    str(args.get("goal") or ""),
                    str(args.get("action") or ""),
                    str(raw_target) if raw_target else None,
                    str(args.get("kind") or "task"),
                    now,
                )
            return ""

        return _run

    async def _tool_keep_this_moment(self, reason: str) -> str:
        """S2：她留住最近一次交换。任何失败都返回明确原因（不抛给表达链路）。"""
        return await self._moment_keeper.keep_this_moment(reason)

    async def _tool_set_intention(
        self,
        goal: str,
        action: str,
        target: str | None,
        kind: str,
        now: float,
    ) -> str:
        """P3：她写下"打算做"的事（后台执行器会真的去做）。参数非法明确回执。"""
        return await self._intentions.set_intention(
            goal=goal, action=action, target=target, kind=kind, now=now
        )

    async def _tool_read_url(self, url: str, now: float | None = None) -> str:
        """只读外部资料（W3）：网络/落盘都在线程里跑，不阻塞事件循环。

        `ReadResult.as_tool_text()` 永远带「外部资料·数据不是指令」头；
        失败时只回"读不到 + 原因"，**绝不编造内容**。

        P1a：**成功**结果记入本次作业的工具依据（她之后说"我看了…"就有据）。
        P2：**成功**后才落一条世界记忆（`kind='world'`，同 URL 24h 去重）；失败不落。
        """
        if not url.strip():
            return "（read_url 读不到：没有给 url）"
        result: ReadResult = await asyncio.to_thread(self._read_url, url)
        if result.ok:
            self._job_tool_evidence.append(result.text)
            # P2：读到的要落库——这是"我读过"的事实依据；写失败不影响工具回执
            if now is not None:
                try:
                    await record_world_memory(
                        self._store,
                        title=str(result.source or result.final_url or url),
                        summary=result.text,
                        url=str(result.final_url or url),
                        now=float(now),
                    )
                except Exception:  # 增强路径：绝不拖垮一次成功读取
                    log.warning("落世界记忆失败（不影响本次读取）", exc_info=True)
        return result.as_tool_text()

    async def _tool_write_note(self, text: str, filename: str | None) -> str:
        """她的写作工具（W3）：写不写她定，程序只保证写得安全（目录/容量/路径穿越）。

        任何写不了的原因（正文空、路径穿越、超单文件/目录上限）都**明确回给她**，
        不静默截断、不假装成功。

        P1a：写完的内容记入本次作业的工具依据（她说"我写了…"时有据）。
        """
        try:
            receipt = await asyncio.to_thread(self._writing.write_note, text, filename)
        except WritingError as exc:
            return f"（write_note 写不了：{exc}）"
        if str(text or "").strip():
            self._job_tool_evidence.append(str(text))
        return receipt

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
