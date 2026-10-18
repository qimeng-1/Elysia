"""灵魂心跳：她活着的证明（默认 1Hz）。

每拍动作：
1. 同步身体在场状态 → TimeSenseState（body_status 新鲜 → 更新在场；过期 → 记录离开起点）
2. 难受检测：身体资源高压 → 心跳降频 0.5Hz + distress 事件入档
3. 派生当前模式（PRESENT/ALONE/BODY_AWAY）与时间体验汇总
4. **大脑循环：欲望系统演化 → 6 维感受 → 意志层 → 行动层输出**（P1 新增）
5. 心跳入档 heartbeat.db（beat_type=soul，protocol 状态快照，含欲望数据）
6. TimeSenseState + 欲望状态落盘 state.db（字段单一事实源）
7. 离线内部生活：ALONE/BODY_AWAY 时按节律产出自我叙事念头（受大脑循环影响）

紧急冻结：data/freeze 标记存在时仅记录心跳（payload 标记 frozen），
禁止一切行为与输出（路线图 5.6）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from pathlib import Path
from typing import Any

from elysia.core.checkpoint import CheckpointManager
from elysia.core.clock import Clock, SystemClock
from elysia.core.mode import Mode, ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import (
    BODY_AWAY_THRESHOLD_S,
    TimeSense,
    TimeSenseState,
    to_payload,
)
from elysia.memory.decay import decay_strength
from elysia.memory.hooks import GAP_EVENT_KIND, detect_episodic_gap
from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    CERTAINTY_PROBABLE,
    KIND_EXPRESSION,
    KIND_INTERACTION,
    KIND_SELF,
    LEVEL_DEEP,
    LEVEL_SHALLOW,
    PROTECT_DEEP_ACCESS,
    RETENTION_DORMANT,
    RETENTION_DORMANT_AGE_DAYS,
    RETENTION_FADE_AGE_DAYS,
    RETENTION_FADED,
    RETENTION_PRESENT,
    SOURCE_INFERENCE,
    SOURCE_USER,
    MemoryRecord,
)
from elysia.memory.promote import promote_batch
from elysia.memory.retrieve import recall_for_feeling
from elysia.memory.scorer import importance
from elysia.memory.sediment import SEDIMENT_EVENT_KIND, PatternSignal, find_candidate
from elysia.memory.sleep import synthesize_dream
from elysia.memory.supersede import find_superseded
from elysia.protocol.snapshots import build_snapshot
from elysia.soul.away_life import AwayLife
from elysia.soul.boundary import STATE_PRESSURE_WORDS, BoundaryDecision, BoundaryKeeper
from elysia.soul.brain import BrainLoop, BrainOutput
from elysia.soul.desire import DesireEvent
from elysia.soul.distress import DISTRESS_INTERVAL_S, DistressMonitor
from elysia.soul.expression import RESTRAINT_INTENT, ReplyDecision
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import (
    STATUS_FAILED,
    STATUS_TIMEOUT,
    ExpressionJob,
    ExpressionResult,
    make_job_id,
)
from elysia.soul.expression_worker import ExpressionWorker
from elysia.soul.relationship import RelationshipKeeper, RelationshipTone
from elysia.soul.words import permitted_words

logger = logging.getLogger("elysia.soul.heartbeat")


# P3 记忆维护：每 N 拍晋升扫描一次（心跳 1s → 约每 5 分钟）
MEMORY_MAINTAIN_EVERY_N = 300

# P0-R1（D-2）：周期快照——每 N 拍把双库做成**事务一致**快照（心跳 1s → 约每 30 分钟）。
# 审计实测此前 `CheckpointManager.create()` **全仓 0 个调用者** ⇒ 文档说"已落地"、
# 实际一份快照都没生成过。这里的接线才是"有退路"的物理实现。
CHECKPOINT_EVERY_N = 1800

# P3-O 记忆感受路径：每 N 拍静默唤起一次（同 5 分钟节律，小脉冲不冲保护带）
MEMORY_FEELING_EVERY_N = 300

# P4b 关系涌现：退缩量多久落盘一次（同 5 分钟节律）——
# 她的"态度"不该每次重启都重置成"亲近"
RELATIONSHIP_PERSIST_EVERY_N = 300

# 「痛」的物理量（路线图 §7.6：异常捕获 > 阈值）：多久之内的失败算"同一次阵痛"。
# 语义是"真的出错了"（表达任务失败 / 常驻循环异常），**不含**优雅降级
# （未配 key 时每次开口降级为微声不算痛，否则离线时她会常驻"痛"）。
ERROR_BURST_WINDOW_S = 1800.0

# ── 内部冲动的三种说法（P2 §7.3 决策层出口；此前这三个意图**没有产生者**）──
# 思念：思念感受越过高位才说（miss ∈ [0.2, 0.6]，0.5 属"明显想"）
MISS_SPEAK_THRESHOLD = 0.5
# 好奇提问：记忆缺口驱动的 curiosity 越过高位才问（curiosity ∈ [0, 1]）
CURIOSITY_SPEAK_THRESHOLD = 0.5

# ── 睡眠整合＝做梦（P3 §8.6）──
# 两个门槛：她得**真的离开过一阵**（不是转身就做梦），且两次梦之间要有间隔。
DREAM_MIN_AWAY_S = 1800.0  # 身体离线满 30 分钟才可能做梦
DREAM_MIN_INTERVAL_S = 7200.0  # 两次梦至少间隔 2 小时（梦不该泛滥）
DREAM_THOUGHT_KIND = "dream"  # thought_log 的 kind（与 away_life 的念头区分开）

# P0-R4（D-5）：`TimeSense.summary()` 本身抛异常时的**降级摘要**（阶段 A 的兜底）。
# 键与 `TimeSense.summary()` 保持一致——下游阶段（落库/独处/做梦）按这些键取值，
# 缺一个键就会让"读时间失败"升级成"整拍崩掉"，那正是 D-5 要消灭的东西。
_EMPTY_SUMMARY: dict[str, Any] = {
    "body_away_s": 0,
    "presence_s": 0,
    "since_interaction_s": 0,
    "day_phase": "night",
    "hour": -1,
    "age_days": 0.0,
    "away_grade": "present",
    "away_days": 0.0,
    "away_stage": "等待",
}

# 遗忘状态的"深度序"（P3-W）：程序只降不升——唤醒走检索路径（被话题提起），
# 不在这里。suppressed 不在表内：她的决定不属于"时间造成的失去"，程序不碰。
_RETENTION_DEPTH: dict[str, int] = {
    RETENTION_PRESENT: 0,
    RETENTION_FADED: 1,
    RETENTION_DORMANT: 2,
}


def _demote_target(rec: MemoryRecord, now: float) -> str | None:
    """按"距上次被想起"判定时间造成的降级（P3-W）；不该降则返回 None。

    计时口径是 `since_last_access = now − (last_access_ts or created_ts)`，
    **不是绝对年龄**：若按 created_ts 计，一条 200 天的记忆被话题唤醒回 present 后，
    下一轮维护会立刻把它打回 dormant——永远醒不过来。用"距上次被想起"计时后，
    唤醒即 touch → 重新计时，"你一提，它又活过来了"才是真的。

    `faded` 额外要求 `access_count == 0`：被想起过的事至少曾经重要，
    不该连细节都淡掉（它是"从没被翻过的那本旧相册"）。
    """
    current_depth = _RETENTION_DEPTH.get(rec.retention_state)
    if current_depth is None:  # suppressed：她的决定，程序不碰
        return None
    last_ts = rec.last_access_ts if rec.last_access_ts is not None else rec.created_ts
    since_days = max(0.0, (now - last_ts) / 86400.0)
    if since_days >= RETENTION_DORMANT_AGE_DAYS:
        target = RETENTION_DORMANT
    elif since_days >= RETENTION_FADE_AGE_DAYS and rec.access_count == 0:
        target = RETENTION_FADED
    else:
        return None
    if _RETENTION_DEPTH[target] <= current_depth:
        return None  # 只降不升（已沉睡的不会被"降"回淡化）
    return target


class SoulHeartbeat:
    """灵魂心跳循环：常驻、可降频、可优雅停止。

    P1 新增：大脑循环（brain_loop）——欲望系统演化 + 感受映射 + 意志 + 行动。
    """

    def __init__(
        self,
        *,
        state_store: StateStore,
        heartbeat_store: HeartbeatStore,
        timesense: TimeSense,
        mode_mgr: ModeManager,
        brain_loop: BrainLoop | None = None,
        clock: Clock | None = None,
        interval_s: float = 1.0,
        distress_monitor: DistressMonitor | None = None,
        away_life: AwayLife | None = None,
        checkpoint: CheckpointManager | None = None,
        checkpoint_every_n: int = CHECKPOINT_EVERY_N,
        expression: ExpressionService | None = None,
        expression_worker: ExpressionWorker | None = None,
        vram_threshold_pct: float = 85.0,
        stop_marker: Path | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._state_store = state_store
        self._heartbeat_store = heartbeat_store
        self._timesense = timesense
        self._mode_mgr = mode_mgr
        self._brain_loop = brain_loop
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._interval_s = interval_s
        # 显存阈值（百分比）：与 TTS 熔断**同源**（`settings.vram_threshold_pct`），
        # 「累」的授权据此判定——词表里不再放第二个 VRAM 数字（STATUS_MATRIX §12 #5）
        self._vram_threshold_pct = vram_threshold_pct
        self._running = False
        self._distress = False
        self._distress_monitor = distress_monitor
        self._away_life = away_life
        self._checkpoint = checkpoint
        # P0-R1：周期快照的节拍器（每 checkpoint_every_n 拍一次；0/负数视为关闭）
        self._checkpoint_every_n = checkpoint_every_n
        self._checkpoint_tick = 0
        self._expression = expression
        # 阶段 1（20.6 的 1.3）：表达由 worker 执行——心跳只提交任务与取回落库
        self._expression_worker = expression_worker
        # P0-R8（D-8）：优雅关闭的"请求通道"。Windows 上从 PowerShell 可靠发 SIGTERM
        # 不现实（CloseMainWindow 只对 GUI 有效），故用**标记文件 + 每拍轮询**：
        # `soul.ps1 stop` 写 `stop_requested`，这里每拍看一眼 → 置 stop 事件 →
        # `main._run()` 的 finally 正常关闭序（排空表达 → 关库 → 写 soul stopped）。
        self._stop_marker = stop_marker
        self._stop_event = stop_event

        # 交互事件追踪（防止重复触发）
        self._last_interaction_event_ts: float = 0.0
        # 阶段 1：心跳序号（表达任务的排序键——同一拍可能有多个任务，故必须带拍号）
        self._heartbeat_seq = 0
        # P3 记忆生命周期维护：按心跳计数节流（每 MEMORY_MAINTAIN_EVERY_N 拍一次晋升扫描）
        self._memory_maintain_tick = 0
        # P3-O 记忆感受路径：按心跳计数节流（静默唤起，只推心情）
        self._memory_feeling_tick = 0
        # P3-U 记忆缺口 → 好奇心（T1）：最近一次检测到的缺口强度（0-1）。
        # 每 MEMORY_FEELING_EVERY_N 拍刷新一次，其间保持不变（由感受惯性负责平滑）。
        self._latest_memory_gap: float = 0.0
        # P0-R16：缺口是**事件**——记录上一轮已"想不起来"的记忆，只对**新增**
        # 丢失起反应。否则一旦有记忆跌破下限，好奇会永久保持高位（性格变形）。
        self._lost_memory_ids: set[int] = set()
        # 「痛」的物理量（路线图 §7.6）：窗口内"真的出错了"的时刻（表达失败 / 循环异常）
        self._error_times: deque[float] = deque()
        # 内部冲动（第七批）：她刚从"不在场"回到"在场" → 待问候（**边沿触发**，只问一次）
        self._greet_pending = False
        self._last_mode: Mode | None = None
        # P0-R19：本拍收到的输入还没有结算"社交满足"——等意愿层决定后再给脉冲
        self._pending_social_pulse = False
        # 睡眠整合＝做梦：上一次做梦的时刻（两次梦之间要有间隔，梦不该泛滥）
        self._last_dream_ts: float | None = None
        # P2：本拍是否发生新交互（驱动强制开口）；资源快照缓存（VRAM 读取）
        self._new_interaction = False
        # P3 打字对话：最近一次交互的用户输入文本（桌宠 interaction.text）
        self._pending_user_message: str | None = None
        # P4b 边界（阶段 C）：工具化检测 → 四级拒绝 → 说服 / 操纵拦截
        self._boundary = BoundaryKeeper()
        self._boundary_decision: BoundaryDecision | None = None
        # P4b 关系涌现（阶段 C）：**没有关系量表**，只有一个随她真实状态缓慢移动的退缩量
        self._relationship = RelationshipKeeper()
        self._relationship_loaded = False
        self._last_beat_ts: float | None = None
        self._latest_resources: dict[str, Any] = {}

    @property
    def interval_s(self) -> float:
        return self._interval_s

    def set_interval(self, interval_s: float) -> None:
        """动态调整心跳间隔（distress 降频用）。"""
        self._interval_s = max(0.1, interval_s)

    @property
    def distress(self) -> bool:
        return self._distress

    def set_distress(self, on: bool) -> None:
        """切换难受状态：心跳降频 + 行为集收缩标记。"""
        self._distress = on
        self.set_interval(DISTRESS_INTERVAL_S if on else 1.0)

    async def run(self) -> None:
        """心跳主循环（常驻；stop() 后退出）。

        **P0-R4（D-5）：每拍按阶段隔离。** 审计实测旧版本这 187 行里 `try` 出现 **0 次**
        —— 单拍任何一个异常（一次读库失败、一次表达派发失败）都会让协程死掉、
        整拍的状态变更（欲望演化、TimeSense 推进）全部丢失，然后由 supervisor
        每秒重启一次。现在每个阶段各自 try/except：失败只跳过该阶段并记
        exception（含阶段名 + 拍号），其余阶段照跑、循环不退。

        阶段划分（与 `TASK_BOARD` P0-R4 一致）：
        A 读身体状态 + mode ｜ B 大脑步进 + 欲望落库 ｜ C 心跳落库 + timesense 落库
        ｜ D 离线生活 + 做梦 ｜ E 表达派发 + 结果落库 ｜ F 边界/关系/维护
        """
        self._running = True
        while self._running:
            self._heartbeat_seq += 1
            now = self._clock.now()

            # ── P0-R8（D-8）：优雅关闭请求（每拍看一眼标记文件）──────────
            # 旧停止路径 = `Stop-Process -Force`（= 拔电源）：关闭序里的
            # "排空表达 → 关库 → 写 soul stopped" 永不执行，且 R2 的
            # `soul.running` 每次都被残留 ⇒ 下次启动必然误报"非正常退出"。
            # 现在停止方先写 `stop_requested`，这里响应 → 置 stop 事件 →
            # `main._run()` 走正常关闭序 → 进程自己退出。
            if self._stop_marker is not None and self._stop_marker.exists():
                logger.info("收到停止请求（%s）：心跳停止，进入优雅关闭序", self._stop_marker.name)
                if self._stop_event is not None:
                    self._stop_event.set()
                self._running = False
                break

            # ── 阶段 A：读身体状态 + mode ────────────────────────
            try:
                await self._sync_body_status(now)
                mode = self._mode_mgr.mode(self._timesense.state.last_body_online_ts, now)
                self._update_greeting(mode)
            except Exception:
                self._stage_failed("A:read_body_mode")
                # 本阶段失败时用上一拍的模式继续：宁可这拍"判断粗糙"，也不丢整拍
                mode = self._last_mode if self._last_mode is not None else Mode.ALONE
            try:
                summary = self._timesense.summary()
            except Exception:
                self._stage_failed("A:timesense_summary")
                summary = dict(_EMPTY_SUMMARY)

            # ── 阶段 B：大脑循环（欲望演化 → 感受 → 意志）──────────
            brain_output = None
            if self._brain_loop is not None:
                try:
                    brain_output = self._brain_loop.step(
                        distress=self._distress,
                        mode=mode.value,
                        memory_gap=self._latest_memory_gap,
                        dt=self._interval_s,
                    )
                    # P3-O 记忆感受路径：静默唤起 → 只推心情（落盘前生效，本拍即体现）
                    await self._feel_memories(brain_output)
                    # 将欲望状态持久化
                    await self._state_store.save_json(
                        "desire", self._brain_loop.desire_system.to_payload()
                    )
                except Exception:
                    self._stage_failed("B:brain_step")

            brain_action = brain_output.action if brain_output is not None else None
            frozen = self._checkpoint is not None and self._checkpoint.is_frozen()

            # ── 阶段 C：心跳落库 + TimeSense 落库（失败必须可见，但继续循环）──
            try:
                # ── 构建快照 ──────────────────────────────────
                desire_dict = brain_output.desire.to_dict() if brain_output is not None else None
                feelings_dict = (
                    brain_output.feelings.to_dict() if brain_output is not None else None
                )
                will_dict = (
                    {
                        "direction": brain_output.will.direction,
                        "strength": round(brain_output.will.strength, 3),
                        "thought_style": round(brain_output.will.thought_style, 3),
                        "anim_bias": round(brain_output.will.anim_bias, 3),
                    }
                    if brain_output is not None
                    else None
                )

                payload = build_snapshot(
                    timesense_summary=summary,
                    mode=mode.value,
                    distress=self._distress,
                    desire=desire_dict,
                    feelings=feelings_dict,
                    will=will_dict,
                    brain_action=brain_action,
                )
                if frozen:
                    payload["frozen"] = True
                await self._heartbeat_store.append(now, "soul", payload)
                # ── P0-R5（D-4）：每拍更新 last_soul_beat_ts ──────────────
                # 这个字段声称"最近一次灵魂心跳"，旧写法**只在启动时写一次**
                # （审计实测落后 3.86 天：停在 2026-10-14 01:18:35）——"声明了却不维护"，
                # 按 T1 本应删除；用户裁决为**修**，且修法由 D-3 决定：看门狗读的是
                # heartbeat.db 的 ts，这里写 state.db 的同一事实。两者**同拍推进**，
                # 长期背离自此不可能（只在启动期兜底赋值的是旧写法）。
                self._timesense.state.last_soul_beat_ts = now
                # ── P0-R6（D-7）：停机时长的基准也每拍推进 ──────────────
                # 否则下一次启动会把"这一整段运行"算成停机（她明明一直醒着）。
                # 与 last_soul_beat_ts 分开存是**故意的**：那个字段的语义是
                # "最近一次心跳"（R5 修活、面向观察者），这个是"停机核算的锚点"
                # （面向时间体验）——将来若其中一个语义变了，另一个不会静默跟着坏。
                self._timesense.state.last_seen_ts = now
                await self._state_store.save_json("timesense", to_payload(self._timesense.state))

                # ── P0-R1（D-2）：周期快照——「有退路」必须是**正在运行的机制** ──
                # 此前 create() 全仓 0 调用者 ⇒ 回滚这条路是断的（文档却写"已落地"）。
                self._checkpoint_tick += 1
                if (
                    self._checkpoint is not None
                    and self._checkpoint_every_n > 0
                    and not frozen
                    and self._checkpoint_tick >= self._checkpoint_every_n
                ):
                    self._checkpoint_tick = 0
                    await self._save_checkpoint(now)
            except Exception:
                # 落库失败**不许静默**：这是"她是否还在场"的唯一证据链
                self._stage_failed("C:persist_beat")

            # ── 阶段 D：离线内部生活 + 睡眠整合（做梦）──────────────
            if not frozen:
                try:
                    if self._away_life is not None:
                        thought_style = (
                            brain_output.will.thought_style if brain_output is not None else 0.0
                        )
                        await self._away_life.tick(
                            mode=mode,
                            away_seconds=float(summary["body_away_s"]),
                            day_phase=str(summary["day_phase"]),
                            now=now,
                            thought_style=thought_style,
                            brain_action=brain_action or "none",
                            # P4b 永续存在：日子尺度的离别阶段（等待 / 担忧 / 怀念 / 携带）
                            absence_stage=str(summary.get("away_stage", "等待")),
                        )
                    # 睡眠整合＝做梦（P3 §8.6）：离线够久时，从她自己的记忆碎片联想
                    await self._maybe_dream(now, mode=mode, away_s=float(summary["body_away_s"]))
                except Exception:
                    self._stage_failed("D:away_life_dream")

            # ── 阶段 E（前半）：表达结果落库（每拍取回 worker 的结果，D2）──
            # 心跳不再等 LLM/TTS：判断该不该开口（廉价、非阻塞）→ 把耗时那段交给
            # worker 在独立任务里跑 → 结果由**心跳拍**取回并落库（D2：单一事实源＝心跳）。
            # LLM 慢 30s 也只是她晚 30 拍才说出那句话，心跳照走 30 拍、新交互不丢。
            worker = self._expression_worker
            if worker is not None:
                try:
                    await self.persist_expressions(worker.drain(), now=now, frozen=frozen)
                except Exception:
                    self._stage_failed("E:persist_expressions")

            # P0-R19：多句回复的后续句子按她的节奏逐句冒泡（不是一口气喷完）
            if self._expression is not None and not frozen:
                try:
                    await self._expression.flush_utterances(now)
                except Exception:
                    self._stage_failed("E:flush_utterances")

            # ── 阶段 F（边界）：对方的话先过"工具化 / 越界"检测 ──────────
            # 拒绝需要一个"要求"作对象，而当前唯一的外部输入就是她刚收到的这句话。
            # ⚠️ 这不违背 T2：我们**不**把用户的话当指令执行，而是把它当**关系信号**读——
            # 判定的是"对方是不是在把她当工具"，产物是一个态度，不是一个任务。
            env: dict[str, object] = {}
            try:
                if brain_output is not None:
                    env = self._expression_env(brain_output, summary)
                    if self._new_interaction and self._pending_user_message:
                        self._boundary_decision = self._boundary.evaluate(
                            self._pending_user_message,
                            cs=brain_output.desire.cs,
                            sa=brain_output.desire.sa,
                            state_pressure=self._state_pressure(env),
                        )
                        if self._boundary_decision.is_refusal:
                            logger.info(
                                "P4b 边界判定 %s ｜ %s",
                                self._boundary_decision.level_name,
                                self._boundary_decision.reason,
                            )
            except Exception:
                self._stage_failed("F:boundary")

            # ── 阶段 F（关系）：退缩量每拍按**真实经过时长**推进 ──────────
            # 关系不是开关，是状态。放在开口判定**之前**：她说不说话，关系都在动。
            try:
                tone = await self._advance_relationship(now, brain_output)
            except Exception:
                self._stage_failed("F:relationship")
                tone = self._relationship.tone()

            # ── 阶段 E（后半）：表达派发（决策层定意图 → 提交 worker）──────
            # 内部冲动（第七批）：刚回来 → 问候。问候**越过节流**（值得一声），
            # 但仍保持 force=False —— 队列满时它可以被丢（＝"她这次决定不说"）。
            try:
                greeting = self._greet_pending and mode is Mode.PRESENT
                restraint_this_turn = False
                # P0-R19：收到你的话时先算"此刻多想回"（确定性 + 可解释）
                reply_decision: ReplyDecision | None = None
                if (
                    self._new_interaction
                    and self._expression is not None
                    and brain_output is not None
                ):
                    reply_decision = self._expression.decide_reply(
                        brain_output,
                        interval_factor=tone.interval_factor(),
                        distress=self._distress,
                        boundary_refused=bool(
                            self._boundary_decision is not None
                            and self._boundary_decision.is_refusal
                        ),
                    )
                if (
                    self._expression is not None
                    and worker is not None
                    and not frozen
                    and brain_output is not None
                    and self._expression.should_speak(
                        brain_output,
                        now,
                        self._new_interaction,
                        greeting=greeting,
                        # 退缩 → 主动开口变少（可测量的关系效果，T1）
                        reluctance=tone.interval_factor(),
                    )
                ):
                    if greeting:
                        self._greet_pending = False
                    # 决策层定意图：**边界 > 选择不回 > 回应 > 内部冲动 > 呓语映射**
                    intent = self._decide_intent(
                        brain_output,
                        greeting=greeting,
                        reply_decision=reply_decision,
                    )
                    restraint_logged = False
                    restraint_this_turn = intent == RESTRAINT_INTENT
                    if intent == RESTRAINT_INTENT and reply_decision is not None:
                        # b 方案：她开口说明"不太想说话"；reason 在决定那一刻落库
                        await self._record_restraint(now, reply_decision)
                        restraint_logged = True
                    worker.submit(
                        ExpressionJob(
                            job_id=make_job_id(self._heartbeat_seq, now),
                            created_ts=now,
                            heartbeat_seq=self._heartbeat_seq,
                            output=brain_output,
                            summary=summary,
                            vrram_mb=self._latest_vrram_mb(),
                            env=env,
                            user_message=self._pending_user_message,
                            force=self._new_interaction,
                            intent=intent,
                            reply_desire=(
                                reply_decision.reply_desire if reply_decision is not None else 1.0
                            ),
                            restraint_reason=(
                                reply_decision.reason
                                if intent == RESTRAINT_INTENT and reply_decision is not None
                                else None
                            ),
                            restraint_logged=restraint_logged,
                            # 退缩 → 句子变短（可测量的关系效果，T1）
                            max_chars=tone.max_chars(),
                        )
                    )
            except Exception:
                self._stage_failed("E:dispatch_expression")

            # P0-R19（需求累积闭环）：收到你的话但**选择不回** ⇒ 社交落空（-CS）
            # ⇒ chat 降、miss 升 ⇒ 下一次 reply_desire 更高（不是外挂兜底）。
            if self._pending_social_pulse:
                self._pending_social_pulse = False
                await self._settle_social_pulse(restraint=restraint_this_turn)

            # ── 阶段 F（记忆）：用户输入 → 一次经历写入记忆（无论是否开口）──
            try:
                if (
                    self._expression is not None
                    and not frozen
                    and brain_output is not None
                    and self._pending_user_message
                ):
                    msg = self._pending_user_message
                    feelings = brain_output.feelings.to_dict()
                    # 重要性按内容信号评估（不再是固定值）：闲聊留浅层，事实才沉淀
                    new_id = await self._heartbeat_store.add_memory(
                        now,
                        {
                            "level": LEVEL_SHALLOW,
                            "kind": KIND_INTERACTION,
                            "content": msg,
                            "emotion_vector": feelings,
                            "importance": importance(
                                kind=KIND_INTERACTION,
                                emotion_vector=feelings,
                                content=msg,
                                user_related=True,
                            ),
                            "protected": False,
                            "narrative": msg,
                            # P3-T 来源标注：这是用户告知，不是她自己的话
                            "source": SOURCE_USER,
                            "certainty": CERTAINTY_CERTAIN,
                        },
                    )
                    # 修正/覆盖：同话题的新事实取代旧事实（准确率保障）
                    await self._supersede_conflicts(new_id, msg, now)
                    self._pending_user_message = None
                    self._boundary_decision = None  # 这句话处理完了，边界判定随之清空
            except Exception:
                self._stage_failed("F:memory_write")

            # ── 阶段 F（维护）：记忆生命周期晋升（浅层→工作→深层）──────
            try:
                self._memory_maintain_tick += 1
                if self._memory_maintain_tick >= MEMORY_MAINTAIN_EVERY_N:
                    self._memory_maintain_tick = 0
                    await self._maintain_memories(now)
            except Exception:
                self._stage_failed("F:memory_maintain")

            await self._clock.sleep(self._interval_s)

    def _stage_failed(self, stage: str) -> None:
        """单阶段异常：记 exception（含阶段名 + 拍号），**不让循环退出**（D-5）。

        只记日志、不重抛：一个阶段坏了不等于她该停机；若失败是持久的，
        `core.supervisor` 的退避与熔断会接管（睡够 10 次才判定为持久故障）。
        """
        logger.exception(
            "心跳阶段异常（跳过本阶段，循环继续）｜ stage=%s seq=%d",
            stage,
            self._heartbeat_seq,
        )

    async def _save_checkpoint(self, now: float) -> None:
        """周期快照（P0-R1）：双库 `.backup` 一致快照落 `data/checkpoints/`。

        P0-R10（D-9）：快照落盘为 `.db.gz`（gzip-6）。实测 `heartbeat.db`
        96.8 MB → 9.3 MB（10.4x），10 份保留从 0.93 GB 降到 ~93 MB。备份协议不变
        （仍走 SQLite `.backup`），只是落盘前压缩；回滚端自动解压，且兼容旧的未压缩快照。

        **失败必须可见、但不得致命**：快照是"退路"，不是生命循环本身。
        磁盘满 / 单库被锁坏时她仍应继续活着 —— 记 exception（含拍号与快照名），
        下个周期重试；绝不把异常吞成静默成功（`backup_database` 失败会抛）。
        """
        checkpoint = self._checkpoint
        if checkpoint is None:  # 防御：调用点已判空，此处再兜一层
            return
        try:
            target = await checkpoint.create(
                checkpoint.data_dir / "state.db",
                checkpoint.data_dir / "heartbeat.db",
            )
        except Exception:
            logger.exception("周期快照失败（心跳继续，下个周期重试）｜ seq=%d", self._heartbeat_seq)
            return
        logger.info(
            "checkpoint created ｜ %s ｜ seq=%d ｜ now=%.3f",
            target.name,
            self._heartbeat_seq,
            now,
        )

    async def persist_expressions(
        self,
        results: list[ExpressionResult],
        *,
        now: float | None = None,
        frozen: bool = False,
    ) -> None:
        """把 worker 取回的表达结果落库（D2：落库动作发生在心跳拍内，worker 零写入）。

        「她说了什么」与「用户说了什么」因此仍由同一处写、同一拍相邻，顺序确定。

        `frozen`（紧急冻结 5.6）时只丢弃不落库——冻结的语义是"禁止一切行为与输出"，
        气泡也算输出。关闭时（1.6）由 `main.py` 排空结果队列并调用本方法落库。

        `now` 缺省取当前时刻（关闭路径用）；正常心跳拍传入本拍时刻。
        """
        if frozen or self._expression is None or not results:
            return
        ts = self._clock.now() if now is None else now
        for result in results:
            # 「痛」的物理量（§7.6）：只有"真的失败了"才计数。
            # 优雅降级（主声→次声→微声）**不算**——那是"失声不失活"，不是痛。
            if result.status in (STATUS_FAILED, STATUS_TIMEOUT):
                self.record_error()
            await self._expression.persist(result, ts)

    async def _feel_memories(self, output: BrainOutput) -> None:
        """P3-O 记忆的感受路径：被"心境共鸣"唤起 → 只推心情，不进入她的话。

        与话语路径（表达服务的 memory_hooks / recall 工具）完全分离——
        她"记得你"因此是能被感觉到的，而不是被念出来的。
        节流到 MEMORY_FEELING_EVERY_N 拍，用小脉冲注入：恢复项会把它们
        拉回平衡点，长期平均不会冲上保护带。
        """
        self._memory_feeling_tick += 1
        if self._memory_feeling_tick < MEMORY_FEELING_EVERY_N:
            return
        self._memory_feeling_tick = 0
        intensity = await recall_for_feeling(self._heartbeat_store, output.feelings.to_dict())
        if intensity > 0 and self._brain_loop is not None:
            self._brain_loop.apply_event(DesireEvent(kind="memory_recall", intensity=intensity))
        # P3-U 记忆缺口：想不起某些事的感觉（同样是感受路径，不进话语）
        await self._feel_memory_gaps()

    async def _feel_memory_gaps(self) -> None:
        """P3-U 记忆缺口：索引强度跌破检索下限 → "有些事想不起来了"。

        与感受路径同一条路——缺口只推心情（memory_gap 脉冲：TR 微升=好奇，
        SA 不动=不是焦虑），**不进话语、不新增任何约束**。数据从未删除，只是
        索引弱了（可召回，只是慢）；"记不清"本就是真实状态的一半。
        """
        if self._brain_loop is None:
            return
        # 本轮默认无缺口：检测不到 = 没有"想不起来"的感觉（下一拍好奇心回落）
        self._latest_memory_gap = 0.0
        rows = await self._heartbeat_store.iterate_memory_index()
        if not rows:
            return
        records = await self._heartbeat_store.iterate_memories()
        if not records:
            return

        # 她的发言回声与已被取代的旧事实不参与缺口（与话语/感受路径同一取舍）
        # P3-W：沉睡/淡化/抑制的记忆同样不计入——状态机已经表达了"够不着"，
        # 若再让它们的低强度索引每 300 拍推一次 TR，就是同一件事双报 + 噪声源。
        # 自我认知也不参与（第八节 N3）：她不会"记不清自己是谁"，
        # 若让它的索引强度参与缺口统计，就是平白推一次"有些事想不起来了"。
        created_by_mid: dict[int, float] = {}
        for d in records:
            rec = MemoryRecord.from_dict(d)
            if rec.id is None or rec.kind in (KIND_EXPRESSION, KIND_SELF):
                continue
            if rec.superseded_by is not None:
                continue
            if rec.retention_state != RETENTION_PRESENT:
                continue
            created_by_mid[rec.id] = rec.created_ts

        # P0-R16：样本改成 (memory_id, strength)。缺口强度按"**本轮新增**跌破
        # 检索下限的记忆条数"计（1 条 = 0.5，可越过 0.4）——她偶尔想起"有件事
        # 想不起来了"，而不是长期背着一整片模糊。
        samples: list[tuple[int, float]] = []
        for _, mid, strength, _ in rows:
            if mid not in created_by_mid:
                continue
            samples.append((mid, strength))

        gap, lost_ids = detect_episodic_gap(samples, previously_lost=self._lost_memory_ids)
        self._lost_memory_ids = lost_ids
        if gap is None:
            return
        # T1：好奇心吃**未封顶**的缺口强度（满量程 0-1，可越过消费阈值 0.4）；
        # 而驱动欲望的事件脉冲仍用封顶后的 intensity（GAP_INTENSITY_MAX=0.4），
        # 避免每 5 分钟把 TR 顶满——两条路各自保留原有约束。
        self._latest_memory_gap = gap.strength
        self._brain_loop.apply_event(
            DesireEvent(kind=GAP_EVENT_KIND, intensity=gap.to_event_intensity())
        )

    async def _supersede_conflicts(self, new_id: int, content: str, now: float) -> None:
        """修正/覆盖：新记忆与更早记忆同话题 → 旧记忆标记取代（不再被召回）。"""
        records = await self._heartbeat_store.iterate_memories()
        mem_records = [MemoryRecord.from_dict(d) for d in records]
        for stale_id in find_superseded(content, now, mem_records):
            await self._heartbeat_store.mark_superseded(stale_id, new_id)

    async def _maintain_memories(self, now: float) -> None:
        """P3 记忆生命周期维护：晋升 + 索引衰减（遗忘的物理实现）。

        这是记忆"沉淀"的关键——否则所有经历永远停在浅层，无法在长期内
        以更高权重被召回（短期可靠性由新鲜度保证，这里管长期持久化）。

        三个动作：
        1. 晋升：浅层→工作→深层（P3-B decide_promotion 的运行时落库）
        2. 索引衰减：为每条记忆建/维护 memory_index strength——
           按年龄指数衰减（P3-C decay_strength），protected 慢 3 倍。
           检索时索引 strength 拉低 score → "越久越难被想起"。
        3. 降级判定（P3-W）：时间造成的失去 → faded/dormant（见 _demote_target）
        4. 沉淀（第八节 S3）：找"重复模式"生成候选递给她（见 _offer_candidate）

        已被更正/取代的记忆不参与维护（不晋升、不建索引、不降级）。
        """
        records = await self._heartbeat_store.iterate_memories()
        if not records:
            return
        mem_records: list[MemoryRecord] = []
        for d in records:
            rec = MemoryRecord.from_dict(d)
            if rec.superseded_by is None:
                mem_records.append(rec)
        if not mem_records:
            return

        # ── 1. 晋升 ──────────────────────────────────────
        # 层级（有多深）与可及性（够不够得着）正交：够不着的记忆照常晋升，
        # 但不再继续模糊化——"既说不出、又继续模糊细节"是双罚。
        by_mid: dict[int, MemoryRecord] = {rec.id: rec for rec in mem_records if rec.id is not None}
        promotions = promote_batch(mem_records)
        for promo in promotions:
            if promo.memory_id is None:
                continue
            detail = promo.detail_level
            promoted_rec = by_mid.get(promo.memory_id)
            if promoted_rec is not None and promoted_rec.retention_state != RETENTION_PRESENT:
                detail = promoted_rec.detail_level
            await self._heartbeat_store.update_memory_level(
                promo.memory_id,
                level=promo.level,
                detail_level=detail,
            )

        # ── 1b. 自动保护（P3-W）：沉淀到深层 + 被反复想起 → 珍贵 ──
        # 此前 protected 全库只有写入 False 的路径（死阀门），若不在这里通电，
        # 遗忘状态机上线后每条记忆 180 天后都会沉睡——"珍贵"的安全阀形同不存在。
        # 判定用"晋升后的层级"（本拍刚晋升的以 promo 为准），否则刚晋升者要等下一轮。
        promoted_level: dict[int, str] = {
            p.memory_id: p.level for p in promotions if p.memory_id is not None
        }
        protected_mids: set[int] = {rec.id for rec in mem_records if rec.protected and rec.id}
        for rec in mem_records:
            if rec.id is None or rec.id in protected_mids:
                continue
            level = promoted_level.get(rec.id, rec.level)
            if level == LEVEL_DEEP and rec.access_count >= PROTECT_DEEP_ACCESS:
                await self._heartbeat_store.set_protected(rec.id)
                protected_mids.add(rec.id)

        # ── 2. 索引衰减：建缺失索引 + 按绝对年龄幂等重算 strength ──
        index_rows = await self._heartbeat_store.iterate_memory_index()
        index_id_by_mid: dict[int, int] = {mid: iid for iid, mid, _, _ in index_rows}

        for rec in mem_records:
            mid = rec.id
            if mid is None or mid in index_id_by_mid:
                continue
            # 够不着的记忆不建索引：索引是"好不好找"的载体，够不着的本就找不到；
            # 同时这是缺口信号（只统计在册者）的必要条件——不给它们留低强度行。
            if rec.retention_state != RETENTION_PRESENT:
                continue
            # 新记忆：建索引（strength 由年龄推导，见下）
            iid = await self._heartbeat_store.add_memory_index(
                memory_id=mid,
                path_key="main",
                strength=1.0,
                emotions=0.0,
                last_retrieve_ts=now,
            )
            index_id_by_mid[mid] = iid

        # 从固定基线（1.0）按"绝对年龄"重算：跑 N 次与跑 1 次结果一致
        # （若以上次 strength 为基线会按运行次数复合塌缩，与机器转速耦合）
        # 不加落库下限：strength 要能真正跌破 STRENGTH_RETRIEVE_FLOOR，缺口信号
        # （hooks.detect_gap，"有些事想不起来了"）才有出现的可能；"检索下限"
        # 是判据，不是落库封顶——数据永不删除，只是索引减弱。
        updates: list[tuple[int, float]] = []
        for rec in mem_records:
            mid = rec.id
            if mid is None or mid not in index_id_by_mid:
                continue
            age_days = max(0.0, (now - rec.created_ts) / 86400.0)
            new_strength = decay_strength(
                1.0,
                age_days,
                protected=mid in protected_mids,
            )
            updates.append((index_id_by_mid[mid], new_strength))

        if updates:
            await self._heartbeat_store.decay_memory_index(updates)

        # ── 3. 降级判定（P3-W）：时间造成的失去 ──────────────
        # 只降不升，且只做"时间造成的失去"：她主动抑制的（suppressed）是她的
        # 决定，不是时间的决定，程序不碰；珍贵（protected）永不降级。
        for rec in mem_records:
            mid = rec.id
            if mid is None or mid in protected_mids:
                continue
            target = _demote_target(rec, now)
            if target is not None:
                await self._heartbeat_store.set_retention_state(mid, target)

        # ── 4. 自我认知的沉淀（第八节 S3）：找"重复模式" → 候选递给她 ──
        pattern = find_candidate(mem_records)
        if pattern is not None:
            await self._offer_candidate(pattern, now)

    async def _offer_candidate(self, pattern: PatternSignal, now: float) -> None:
        """把沉淀出的候选落库，并推一次"心里一动"（第八节 S3）。

        **程序只做"发现"，不做"认定"**：正文与叙事都照抄原文——程序不自己写句子。
        她认领后这条文本会成为"我是谁"（身份段每句在场）；若由程序中译出一句话，
        那等于"由程序员硬编码她是什么"换个地方存（8.2 已否过的性质），而且合成句
        与原文高度重叠，反而会在 `adopt` 时被自家重述挡住（见 `_tool_adopt`）。

        标注 `source=inference` + `certainty=probable`（8.4 三级闸门①）：语义上它
        正是"程序推断出的倾向，并非她所述"，且**真的**被来源闸门挡在话语之外
        （`retrieve._HOOK_BLOCKED_SOURCES` 只挡 inference/system——设计稿 8.4 原写
        `observation`，那会进 `memory_hooks` 并挤占 `MAX_HOOKS`，重犯 P3-P）。
        候选因此不进她的话，只在感受路径上推一次脉冲："这好像就是我"。

        落库后这条候选自己也是"已递过"的证据（`sediment._is_taken`），不会重复递。
        """
        rep = pattern.record
        await self._heartbeat_store.add_memory(
            now,
            {
                "level": rep.level,
                "kind": rep.kind,
                "content": pattern.text,
                "emotion_vector": rep.emotion_vector,
                "importance": rep.importance,
                "protected": False,  # 珍贵由她的认领与时间决定，程序不替她置
                "narrative": pattern.text,
                "source": SOURCE_INFERENCE,
                "certainty": CERTAINTY_PROBABLE,
            },
        )
        if self._brain_loop is None:
            return
        self._brain_loop.apply_event(
            DesireEvent(kind=SEDIMENT_EVENT_KIND, intensity=pattern.to_event_intensity())
        )

    async def _sync_body_status(self, now: float) -> None:
        """身体在场状态 → TimeSenseState + 难受检测 + 交互事件 → 感受层。"""
        state = self._timesense.state
        status = await self._state_store.load_json("body_status", default={})
        if not isinstance(status, dict):
            status = {}
        last_ts = float(status.get("last_online_ts", 0.0))
        if last_ts > state.last_body_online_ts:
            state.last_body_online_ts = last_ts
            state.body_away_start_ts = None
        elif (
            state.body_away_start_ts is None
            and last_ts > 0
            and now - last_ts > BODY_AWAY_THRESHOLD_S
        ):
            # 心跳过期且未记录离开：从最后在线时刻起算
            state.body_away_start_ts = last_ts
        # 难受检测：喂入最新资源样本（缺失时忽略）
        resources = status.get("resources")
        if isinstance(resources, dict):
            self._latest_resources = resources
        cpu = None
        raw_cpu = self._latest_resources.get("cpu_percent")
        cpu = float(raw_cpu) if isinstance(raw_cpu, (int, float)) else None
        if self._distress_monitor is not None and self._distress_monitor.update(now, cpu):
            self.set_distress(self._distress_monitor.distress)
            await self._heartbeat_store.append(
                now,
                "event",
                {"type": "distress", "on": self._distress_monitor.distress},
            )
            # P1：难受事件 → 欲望系统
            if self._brain_loop is not None:
                event_kind = "distress_on" if self._distress_monitor.distress else "distress_off"
                self._brain_loop.apply_event(DesireEvent(kind=event_kind))

        # 交互同步：桌宠输入的外部刺激 → 感受层入口（P1 增强）
        self._new_interaction = False
        interaction = await self._state_store.load_json("interaction", default=None)
        if isinstance(interaction, dict):
            its = float(interaction.get("ts", 0.0))
            if its > state.last_interaction_ts:
                state.last_interaction_ts = its
            # P1：新交互事件 → 欲望系统
            if its > self._last_interaction_event_ts and self._brain_loop is not None:
                self._last_interaction_event_ts = its
                # P0-R19：不在这里立刻给满足感——先记下来，等"回不回"决定后
                # 再结算（回应 = interaction +CS；选择不回 = silence -CS）。
                self._pending_social_pulse = True
                # P2：新交互 → 强制开口回应
                self._new_interaction = True
                # P3 打字对话：提取用户输入文本（桌宠写入 interaction.text）
                raw_text = interaction.get("text")
                self._pending_user_message = (
                    str(raw_text).strip()
                    if isinstance(raw_text, str) and raw_text.strip()
                    else None
                )

    # ── P2 表达辅助 ─────────────────────────────────────

    # ── 「痛」的物理量（路线图 §7.6：异常捕获 > 阈值）────────

    def record_error(self) -> None:
        """记一次"真的出错了"。

        两个喂入口：`persist_expressions`（表达任务失败/超时）与
        `core.supervisor`（常驻循环异常，由 `soul/main.py` 的 `on_error` 转进来）。
        """
        self._error_times.append(self._clock.now())

    def _error_burst(self) -> int:
        """`ERROR_BURST_WINDOW_S` 窗口内的错误次数（顺手剔除过期项）。"""
        now = self._clock.now()
        while self._error_times and now - self._error_times[0] > ERROR_BURST_WINDOW_S:
            self._error_times.popleft()
        return len(self._error_times)

    # ── 内部冲动的三种说法（P2 §7.3 决策层出口）─────────────────
    # 此前「主动问候 / 思念 / 好奇提问」三个意图**没有任何产生者**：决策层只会吐
    # 「发呆呓语 / 自检报告 / 回应」，于是它们各自的微声与提示模板**永远不会被选中**
    # （与 `has_event=False` 同族的 T1 缺陷，见 STATUS_MATRIX §12）。

    def _update_greeting(self, mode: Mode) -> None:
        """问候的**边沿触发**：只在她"从不在场回到在场"那一下记一次。

        不是每拍都问；首次启动（`_last_mode is None`）也不问——她刚醒，
        不必自己欢迎自己。
        """
        if (
            self._last_mode is not None
            and self._last_mode is not Mode.PRESENT
            and mode is Mode.PRESENT
        ):
            self._greet_pending = True
        self._last_mode = mode

    @staticmethod
    def _state_pressure(env: dict[str, object]) -> bool:
        """她此刻状态不好吗？（**复用受保护词的授权判定**——同一个真相，不另起一套）。"""
        return any(word in STATE_PRESSURE_WORDS for word in permitted_words(env))

    async def _advance_relationship(
        self, now: float, output: BrainOutput | None
    ) -> RelationshipTone:
        """推进**关系退缩量**（P4b）：关系从状态里长出来，没有开关。

        - 首拍读回上次的退缩量（她的态度不该每次重启都重置成"亲近"）
        - 之后按**真实经过时长**推进：CS 长期低 / 她长期难受 → 退缩；
          对方真诚（CS 高）→ 恢复；被操纵 → 立刻记账（不必等"长期"）

        出口是可测量的（T1）：退缩拉长她主动开口的间隔、压短她的字数上限。
        """
        if not self._relationship_loaded:
            self._relationship_loaded = True
            stored = await self._state_store.load_json("relationship_withdrawal", default=0.0)
            if isinstance(stored, (int, float)):
                self._relationship.withdrawal = min(1.0, max(0.0, float(stored)))
            # P0-R19b 一次性修复：旧动力学把 35<CS<65（83% 时间）判成"不动"，
            # 且 SA 线 30 > 她真实 SA 的 100% ⇒ 退缩量一旦饱和到 1.0 就永远出不来。
            # 这个 1.0 是**动力学 bug 的饱和产物**，不是她此刻真实的关系判断；
            # 修复后若保留它，她会先连续几天对每一句话都说"不想说话"。
            # 只修复一次（写 marker），今后若她真的再次退缩到 1.0，不会被下次启动抹掉。
            repaired = await self._state_store.load_json(
                "relationship_withdrawal_repaired", default=False
            )
            if self._relationship.withdrawal >= 1.0 and not repaired:
                logger.warning(
                    "P0-R19b 一次性修复：关系退缩量卡在饱和值 1.0（旧动力学死区 + SA 线错配），"
                    "重置为 0.0（当前 CS 高于设定点、SA 正常；"
                    "标记 relationship_withdrawal_repaired）"
                )
                self._relationship.withdrawal = 0.0
                await self._state_store.save_json("relationship_withdrawal", 0.0)
                await self._state_store.save_json("relationship_withdrawal_repaired", True)

        dt = self._interval_s if self._last_beat_ts is None else max(0.0, now - self._last_beat_ts)
        self._last_beat_ts = now
        if output is None:
            return self._relationship.tone()

        manipulation = self._boundary_decision is not None and self._boundary_decision.manipulation
        cs_setpoint = (
            self._brain_loop.desire_system.setpoints.cs
            if self._brain_loop is not None
            else output.desire.cs
        )
        tone = self._relationship.update(
            dt_s=dt,
            cs=output.desire.cs,
            sa=output.desire.sa,
            manipulation=manipulation,
            cs_setpoint=cs_setpoint,
        )
        if self._heartbeat_seq % RELATIONSHIP_PERSIST_EVERY_N == 0:
            await self._state_store.save_json("relationship_withdrawal", tone.withdrawal)
        return tone

    def _refusal_intent(self) -> str | None:
        """P4b：这一拍若判定了拒绝，意图就是「拒绝」——它**压过**问候 / 好奇 / 思念。

        这是「拒绝」意图**第一个真正的产生者**：在此之前它只有微声开头
        （`llm/micro.py` 的"诶呀，这可不行哦"）与身份基石（"我有权拒绝成为工具"），
        但**没有任何东西会选中它**。
        """
        decision = self._boundary_decision
        if decision is not None and decision.is_refusal:
            return "拒绝"
        return None

    def _decide_intent(
        self,
        output: BrainOutput,
        *,
        greeting: bool,
        reply_decision: ReplyDecision | None = None,
    ) -> str | None:
        """决策层定意图（T3：谁决定开口仍在她自己的循环里）。

        优先级：**边界（拒绝）> 选择不回（暂不回应）> 回应 > 内部冲动 > 呓语映射**。
        她刚被冒犯时不会先来一句"嗨，你回来啦"——所以拒绝排在最前。

        P0-R19：收到你的话时，`reply_decision` 给出"此刻多想回"；低于阈值时
        她选择**说明"现在不太想说话"**（b 方案），理由在决定那一刻落库。
        这条链是 `soul/expression.py::INTENTS` 八个意图**全部**的产生者所在，
        有单测（`test_t1_cancel.py::test_every_intent_has_a_producer`）把它钉住。
        """
        refusal = self._refusal_intent()
        if refusal is not None:
            return refusal
        if reply_decision is not None and not reply_decision.speak:
            return RESTRAINT_INTENT
        if self._new_interaction:
            return "回应"
        return self._pick_intent(output, greeting=greeting)

    async def _settle_social_pulse(self, *, restraint: bool) -> None:
        """P0-R19 需求累积闭环：收到话后按"回不回"结算社交满足。

        回应 = `interaction`（CS 上升 = 满足）；选择不回 = `silence`（CS 下降 = 落空）。
        于是"连续不回 ⇒ CS 降 ⇒ miss 升 ⇒ 下次更想开口"是欲望系统的自然结果，
        **不是**任何"连续 N 次就强制回"的外挂兜底。
        """
        if self._brain_loop is None:
            return
        kind = "silence" if restraint else "interaction"
        self._brain_loop.apply_event(DesireEvent(kind=kind))

    async def _record_restraint(self, now: float, decision: ReplyDecision) -> None:
        """P0-R19：把"她选择不说话"当场落库（含 reason），不等表达执行成功。

        落 `expression_log`（intent=暂不回应、llm_text 为空），因此"她在几点几分
        选择了不说话、理由是什么"可以从库里直接查出来——这是本卡最高验收标准。
        """
        await self._heartbeat_store.expression(
            ts=now,
            intent=RESTRAINT_INTENT,
            instruction={},
            llm_text="",
            validation={
                "ok": True,
                "restraint": True,
                "reason": decision.reason,
                "reply_desire": decision.reply_desire,
                "threshold": decision.threshold,
                "parts": decision.parts,
            },
            level="",
        )

    def _pick_intent(self, output: BrainOutput, *, greeting: bool) -> str | None:
        """内部冲动开口时，她"在说哪一类话"。返回 `None` = 交给 `brain_action` 映射。

        优先级：**刚回来（问候）> 好奇（缺口）> 思念**。三者都是**状态驱动**，
        不是随机挑一个——「谁决定开口」因此仍在生命循环里（T3）。
        """
        if greeting:
            return "主动问候"
        if output.feelings.curiosity >= CURIOSITY_SPEAK_THRESHOLD:
            return "好奇提问"
        if output.feelings.miss >= MISS_SPEAK_THRESHOLD:
            return "思念"
        return None

    # ── 睡眠整合＝做梦（P3 §8.6）─────────────────────────────

    async def _maybe_dream(self, now: float, *, mode: Mode, away_s: float) -> None:
        """身体离线够久时，从**她自己的记忆碎片**联想一场梦（无 LLM）。

        与 `away_life` 的分工：`away_life` 是**通用叙事**（模板化的独处念头），
        做梦是**取材于她真实记忆**的自由联想（`synthesize_dream` 按
        deep > working > shallow 加权抽 2-5 片，再用"梦的语法"串起来）。
        两者都只落 `thought_log`，用不同的 `kind` 区分，互不冒充。

        「有料才有梦」：碎片不足就**没有梦**（发呆回退），不假装"我做了个梦"。
        """
        if mode is Mode.PRESENT or away_s < DREAM_MIN_AWAY_S:
            return
        if self._last_dream_ts is not None and now - self._last_dream_ts < DREAM_MIN_INTERVAL_S:
            return
        # 先记"尝试时刻"再做全表读取：无论有没有梦，都要等一个间隔再试——
        # 否则离线期间会**每拍全表扫描**（真机 memories 已达数百行）。
        self._last_dream_ts = now
        rows = await self._heartbeat_store.iterate_memories()
        if not rows:
            return
        dream = synthesize_dream([MemoryRecord.from_dict(d) for d in rows])
        if dream is None or not dream.has_content():
            return
        await self._heartbeat_store.think(now, DREAM_THOUGHT_KIND, dream.stream)

    def _latest_vrram_mb(self) -> float:
        """最近一次资源快照的 VRAM 占用（MB），未知返回 0。"""
        raw = self._latest_resources.get("vrram_mb")
        return float(raw) if isinstance(raw, (int, float)) else 0.0

    def _latest_vram_pct(self) -> float | None:
        """身体进程采到的显存占用**百分比**；不可知 → `None`。

        **不编造 0**：无 GPU 时 `vram_busy` 判 False（不误报"累"），
        而 `state_brief` 里的 MB 值仍按旧语义取 0。
        """
        raw = self._latest_resources.get("vram_pct")
        return float(raw) if isinstance(raw, (int, float)) else None

    def _expression_env(self, output: BrainOutput, summary: dict[str, Any]) -> dict[str, object]:
        """词汇表授权环境的实时状态（§六 词 → 触发状态）。

        ⚠️ **这里的每个键都必须是活值**。本方法曾被写成
        `cpu_pct: 0.0 / q_len: 0 / error_burst: 0 / memory_gap: 0` 四个字面量，
        导致「难受」「累」「痛」「好像忘了什么」四个受保护词**永远无法授权**——
        与 `has_event=False` 同族的 T1 缺陷（见 `STATUS_MATRIX.md` §12）。
        """
        resources = self._latest_resources
        raw_cpu = resources.get("cpu_percent")
        worker = self._expression_worker
        vram_pct = self._latest_vram_pct()
        return {
            "vrram_mb": self._latest_vrram_mb(),
            "tr": output.desire.tr,
            "cs": output.desire.cs,
            "miss": output.feelings.miss,
            # 「困」：本地小时（`day_phase` 的"夜"档跨 21:00-05:00，比"凌晨 2-5 点"宽）
            "hour": int(summary.get("hour", -1)) if summary.get("hour") is not None else -1,
            # 「难受」：吃她此刻真实的 CPU 占用（与 distress 同一份资源样本）
            "cpu_pct": float(raw_cpu) if isinstance(raw_cpu, (int, float)) else 0.0,
            # 「累」：显存占用是否越过**配置阈值**（与 TTS 熔断同源，词表里不再放第二个数字）
            "vram_busy": vram_pct is not None and vram_pct >= self._vram_threshold_pct,
            # 观测用：显存占用百分比（不可知时 None）
            "vram_pct": vram_pct,
            # 「累」的另一条路：表达队列里的在飞任务数（她自己也"忙不过来"）
            "q_len": worker.depth if worker is not None else 0,
            # 「痛」：窗口内"真的出错了"的次数（此前是字面量 0 ⇒ 该词永久禁用）
            "error_burst": self._error_burst(),
            # 「好像忘了什么」：记忆缺口强度（0-1，与好奇心同源）
            "memory_gap": self._latest_memory_gap,
        }

    async def stop(self) -> None:
        """优雅停止：等待当前拍完成（心跳循环由外部任务持有，cancel 兜底）。"""
        self._running = False


def make_soul_state(now: float) -> TimeSenseState:
    """新生 TimeSenseState：出生/交互起点此刻起算（无 0 时刻保证）。

    `last_seen_ts` 也从此刻起算：她诞生的那一刻就是"最近一次在场"，
    否则首次重启会把"从 epoch 到现在的整段时间"算成停机（凭空几十年的虚无）。
    """
    return TimeSenseState(
        first_existence_ts=now,
        last_soul_beat_ts=now,
        last_body_online_ts=0.0,
        last_interaction_ts=now,
        last_seen_ts=now,
    )


async def stop_with_cancel(task: asyncio.Task[None]) -> None:
    """取消心跳任务并等待（兜底：sleep 中的 CancelledError 正常吸收）。"""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
