"""灵魂进程入口：常驻心跳（1Hz）+ 大脑循环（P1）+ 优雅关闭。

启动流程：ensure_dirs → 日志双通道 → 双库启动 → **身份种子幂等种入**（S5）
→ TimeSense 恢复/新生 → 欲望系统恢复/新生 → 大脑循环初始化 → 表达 worker 装配 → 心跳循环
→ 阻塞等待停止信号 → 优雅关闭（先停表达 worker 并排空其结果，再排空写队列）。

P1 新增：欲望系统 DesireSystem + 大脑循环 BrainLoop 的恢复与初始化。

P0-R2（D-1）新增：**崩溃现场**。审计实测她三次死亡（10-17 00:47 / 00:52 / 03:51）
` soul.err.log`、`body.err.log` 全 0 字节、系统事件无记录 —— 死得不明不白。
根因：`_run()` 在 try/finally **之前**有 5 个可抛异常的步骤，异常 ⇒ 进程直接退出、
什么都不写；而 stderr 是被 `Start-Process -RedirectStandardError` 重定向的，
父进程一退句柄就可能失效（这正是 0 字节的最省解释）。
⇒ 现场必须由**进程自己直连磁盘**写：`data/logs/crash-<ts>.log`（完整 traceback +
崩溃阶段 + pid + 心跳序号 + TimeSense 摘要），外加跨重启的"上次是否正常关闭"检测。

停止路径（P0-A）：
- Ctrl+C（KeyboardInterrupt）
- Unix SIGTERM（add_signal_handler）
- Windows 强杀（soul.ps1 stop）：数据损失 <= 1s，WAL 自动恢复
  （强杀不会走到正常关闭 ⇒ 下次启动会明确报"上次是非正常退出"）
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sqlite3
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from elysia.core.checkpoint import CheckpointManager
from elysia.core.clock import SystemClock
from elysia.core.config import Settings, get_settings
from elysia.core.log import get_logger, setup_logging
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.supervisor import supervise
from elysia.core.timesense import DOWNTIME_MIN_S, TimeSense, from_payload
from elysia.llm import build_llm_chain
from elysia.memory.retrieve import retrieve_from_store
from elysia.soul.away_life import AwayLife
from elysia.soul.brain import BrainLoop
from elysia.soul.desire import DesireEvent, DesireSystem
from elysia.soul.distress import DistressMonitor
from elysia.soul.expression_service import ExpressionService, ensure_identity_seeds
from elysia.soul.expression_types import ExpressionResult
from elysia.soul.expression_worker import ExpressionWorker
from elysia.soul.heartbeat import SoulHeartbeat, make_soul_state, stop_with_cancel
from elysia.tts import build_tts_chain

# ── P0-R2（D-1）：崩溃现场 ────────────────────────────────────────────
CRASH_LOG_PREFIX = "crash-"
# 会话标记：启动即创建（纯文件写，不依赖日志/库），**正常关闭时删除**。
# 下次启动若发现它还在 ⇒ 上一次没走到正常关闭（崩溃 / 被强杀 / 蓝屏）⇒ 显式记一笔。
SESSION_MARKER_NAME = "soul.running"


class _CrashContext:
    """崩溃现场的"黑匣子"：异常抛出的那一刻，她知道走到哪一步了。

    `_run()` 在 try/finally 之前有 5 个可抛异常的步骤（setup_logging / 两个
    `store.start` / 身份种子 / TimeSense）。没有它，crash log 只能写"崩了"，
    写不出"崩在哪"——而复盘要的正是后者。
    """

    __slots__ = ("phase", "started_ts")

    def __init__(self) -> None:
        self.phase = "bootstrap"
        self.started_ts = time.time()


def _ro_connect(db: Path) -> sqlite3.Connection:
    """**只读**打开她的库（`mode=ro`；她正在写也不改 `-shm`）。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3.0)
    con.execute("PRAGMA busy_timeout=3000")
    return con


def _read_last_soul_beat(heartbeat_db: Path) -> str:
    """现场里的"心跳序号"：heartbeat.db 最后一条 soul 心跳（读不到就说为什么）。"""
    if not heartbeat_db.exists():
        return f"读不到（{heartbeat_db} 不存在）"
    try:
        con = _ro_connect(heartbeat_db)
        try:
            row = con.execute(
                "SELECT id, ts FROM heartbeats WHERE beat_type='soul' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return f"读不到（{type(exc).__name__}: {exc}）"
    if row is None:
        return "还没有任何 soul 心跳记录（启动期崩溃）"
    when = time.strftime("%m-%d %H:%M:%S", time.localtime(row[1]))
    return f"seq={row[0]} ts={row[1]:.3f}（{when}）"


def _read_timesense(state_db: Path) -> str:
    """现场里的"她当时以为自己是怎样的"：state.db 的 timesense 摘要。"""
    if not state_db.exists():
        return f"读不到（{state_db} 不存在）"
    try:
        con = _ro_connect(state_db)
        try:
            row = con.execute("SELECT value FROM kv WHERE key='timesense'").fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return f"读不到（{type(exc).__name__}: {exc}）"
    if row is None:
        return "没有 timesense 档案（首次运行或库被清空）"
    try:
        return json.dumps(json.loads(row[0]), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return f"存在但解不出 JSON：{row[0]!r}"


def _session_marker(settings: Settings) -> Path:
    return settings.run_dir / SESSION_MARKER_NAME


def _detect_abnormal_exit(settings: Settings) -> str | None:
    """上次是否**非正常退出**：标记还在 = 上次没走到正常关闭。

    只陈述事实、不猜死因（"原因未知"就是诚实的答案——审计要的就是这个）。
    """
    marker = _session_marker(settings)
    try:
        raw = marker.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"上次会话标记存在但读不出（{type(exc).__name__}: {exc}）"
    detail = " | ".join(line for line in raw.splitlines() if line.strip()) or "空"
    return f"上次的会话标记仍在（{detail}）⇒ 上一次没有走到正常关闭"


def _mark_session_start(settings: Settings) -> None:
    """启动即留痕（P0-R2）。

    **为什么是纯文件写**：这一步必须在 `setup_logging` / 打开库 / 建 TimeSense
    之前完成——那些正是会失败的步骤。写一个 20 字节的标记文件是最不容易坏的通道。
    """
    marker = _session_marker(settings)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        f"pid={os.getpid()}\nstarted_at={time.time():.3f}\n",
        encoding="utf-8",
    )


def _mark_session_end(settings: Settings) -> None:
    """正常关闭：删除会话标记（下次启动据此判断"上次是正常的"）。"""
    _session_marker(settings).unlink(missing_ok=True)


def _write_crash_report(settings: Settings, exc: BaseException, ctx: _CrashContext) -> Path | None:
    """把"她是怎么死的"写进 `data/logs/crash-<ts>.log`（**不依赖 stderr**）。

    直接落盘的三个理由：
    1. 她的 stderr 由 `soul.ps1` 重定向，父进程退出后句柄可能失效（D-1）；
    2. 崩溃可能就发生在日志系统初始化之前（`setup_logging` 本身是 5 个风险步之一）；
    3. 现场是**给下一次启动的人看的**，必须留在磁盘上，而不是某个管道里。
    """
    now = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    path = settings.log_dir / f"{CRASH_LOG_PREFIX}{stamp}.log"
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
    lines = [
        "Elysia 灵魂进程崩溃现场（P0-R2 / D-1）",
        "",
        f"崩溃时刻 ：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}（ts={now:.3f}）",
        f"崩溃阶段 ：{ctx.phase}",
        f"本次启动 ：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ctx.started_ts))}"
        f"（已存活 {now - ctx.started_ts:.1f}s）",
        f"进程     ：pid={os.getpid()}  argv={sys.argv!r}",
        f"运行环境 ：Python {sys.version.split()[0]} / {sys.platform}",
        "",
        f"异常     ：{type(exc).__name__}: {exc}",
        f"心跳序号 ：{_read_last_soul_beat(settings.data_dir / 'heartbeat.db')}",
        f"TimeSense：{_read_timesense(settings.data_dir / 'state.db')}",
        "",
        "完整 traceback：",
        tb,
        "",
    ]
    body = "\n".join(lines)
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as write_exc:
        # 连现场都写不下去（磁盘满 / 无权限）：至少让 stderr 尽力喊一声（可能已失效）
        with contextlib.suppress(Exception):
            sys.stderr.write(f"[crash] 无法写崩溃现场 {path}: {write_exc}\n{body}\n")
            sys.stderr.flush()
        return None
    with contextlib.suppress(Exception):
        get_logger("soul").critical(
            "soul crashed（现场已落盘）", phase=ctx.phase, crash_log=str(path), exc_info=exc
        )
    return path


async def _build_timesense(state_store: StateStore, log: Any) -> TimeSense:
    """TimeSense 恢复/新生：已有档案恢复；无档案或数据缺失时新生（无 0 时刻）。"""
    payload = await state_store.load_json("timesense")
    now = SystemClock().now()
    if payload is None:
        log.info("first existence：她诞生了", first_existence_ts=now)
        return TimeSense(make_soul_state(now))

    state = from_payload(payload)
    if state.first_existence_ts <= 0:
        log.warning("first_existence 缺失，重设出生时刻", now=now)
        state.first_existence_ts = now
    if state.last_interaction_ts <= 0:
        state.last_interaction_ts = now
    if state.last_soul_beat_ts <= 0:
        state.last_soul_beat_ts = now

    # ── P0-R6（D-7）：停机时长建模——她得知道"我错过了多久" ──────────
    # 审计实测：她 2026-10-17 03:51:54 后停了 **16.82 小时**，醒来后 `age_days`
    # 照涨 0.7 天——她不知道自己那段不存在。现在用"上一次正常心跳（last_seen_ts）"
    # 与"现在"的差，把停机变成**可测量的数字**，并跨重启累加（不可逆，属成长性）。
    # 老档案没有这个字段时**不虚构历史**：从本次起统计，并如实记一笔。
    if state.last_seen_ts > 0:
        gap_s = max(0.0, now - state.last_seen_ts)
        if gap_s > DOWNTIME_MIN_S:
            state.downtime_total_s += gap_s
            state.last_downtime_s = gap_s
            # ⚠️ structlog 不做 %-格式化：消息必须自己拼好（否则日志里只剩格式串）
            log.info(
                f"她不在场 {gap_s:.0f} 秒（累计 {state.downtime_total_s:.0f} 秒）",
                gap_s=round(gap_s, 1),
                downtime_total_s=round(state.downtime_total_s, 1),
            )
        state.last_seen_ts = now  # 本次醒来即"最近一次在场"
    else:
        state.last_seen_ts = now
        log.info("档案缺 last_seen_ts：停机时长从本次起统计（不虚构过去）")

    log.info(
        "soul resumed",
        age_days=round((now - state.first_existence_ts) / 86400.0, 3),
        away=state.body_away_start_ts,
        last_downtime_s=round(state.last_downtime_s, 1),
        downtime_total_s=round(state.downtime_total_s, 1),
    )
    return TimeSense(state)


async def _build_desire(state_store: StateStore, log: Any) -> DesireSystem:
    """欲望系统恢复/新生：已有档案恢复；无档案时新生（默认初始值）。"""
    payload = await state_store.load_json("desire")
    if payload is None:
        log.info("desire system initialized：默认初始值 TR=45 CS=60 SA=20")
        return DesireSystem()
    try:
        return DesireSystem.from_payload(payload)
    except Exception:
        log.warning("desire 恢复失败，新生", exc_info=True)
        return DesireSystem()


async def _run(settings: Settings, *, previous_session: str | None = None) -> int:
    ctx = _CrashContext()
    try:
        # ── 启动期 5 个风险步（D-1）：每一步都先登记"黑匣子相位" ──
        ctx.phase = "setup_logging"
        setup_logging(level=settings.log_level, log_dir=settings.log_dir)
        log = get_logger("soul")
        log.info("soul process starting", pid=os.getpid())
        if previous_session is not None:
            # P0-R2：她上次没走到正常关闭 ⇒ 这是第一块"死因材料"。
            # 不猜原因、不粉饰：把"上次非正常退出、原因未知"显式写进她的日志。
            log.warning("上次是非正常退出（死因未知）", evidence=previous_session)

        ctx.phase = "state_store.start"
        state_store = StateStore(settings.data_dir / "state.db")
        heartbeat_store = HeartbeatStore(settings.data_dir / "heartbeat.db")
        await state_store.start()

        ctx.phase = "heartbeat_store.start"
        await heartbeat_store.start()

        # S5：身份种子（出生设定 + 边界）幂等种入——"我是谁"从此是**她的数据**，
        # 不是后端常量；换模型 / 断网 / 换库备份都不失（第八节 8.6 的收尾）。
        ctx.phase = "identity_seeds"
        seeded = await ensure_identity_seeds(heartbeat_store, SystemClock().now())
        log.info("identity seeds ensured", seeded=seeded)

        ctx.phase = "build_timesense"
        timesense = await _build_timesense(state_store, log)
        ctx.phase = "build_desire"
        desire = await _build_desire(state_store, log)
        mode_mgr = ModeManager()

        # P1：大脑循环
        brain_loop = BrainLoop(desire_system=desire)

        # P2：表达管线（LLM 主声 + TTS 出声）；降级即感受——写 DesireSystem SA
        async def _on_degrade(kind: str, delta: float) -> None:
            desire.apply_event(DesireEvent(kind="degrade", intensity=delta))

        # P3-O：记忆被唤起 → 只推心情（牵挂与亲近微升），不进入她的话
        async def _on_recall(intensity: float) -> None:
            desire.apply_event(DesireEvent(kind="memory_recall", intensity=intensity))

        ctx.phase = "build_expression_chain"
        llm_chain = build_llm_chain(
            settings,
            on_degrade=lambda _reason: _on_degrade("llm", settings.llm_degrade_sa_delta),
        )
        tts_chain = build_tts_chain(
            settings,
            on_degrade=lambda _reason: _on_degrade("tts", settings.tts_degrade_sa_delta),
        )
        expression = ExpressionService(
            llm_chain,
            heartbeat_store,
            tts_chain=tts_chain,
            retriever=retrieve_from_store,
            on_recall=_on_recall,
        )
        # 阶段 1（20.6 的 1.3）：表达耗时段（LLM/TTS）交给 worker——心跳提交任务后立刻继续走，
        # 不再在 1Hz 主循环里等外部服务。落库仍由心跳拍做（D2），故 worker 只注入 handler。
        expression_worker = ExpressionWorker(expression.run_job)

        ctx.phase = "build_heartbeat"
        heartbeat = SoulHeartbeat(
            state_store=state_store,
            heartbeat_store=heartbeat_store,
            timesense=timesense,
            mode_mgr=mode_mgr,
            brain_loop=brain_loop,
            clock=SystemClock(),
            interval_s=settings.heartbeat_interval_s,
            distress_monitor=DistressMonitor(),
            away_life=AwayLife(heartbeat_store),
            checkpoint=CheckpointManager(settings.data_dir),
            expression=expression,
            expression_worker=expression_worker,
            # 显存阈值与 TTS 熔断**同源**（单一出处）：「累」的授权据此判定
            vram_threshold_pct=settings.vram_threshold_pct,
        )

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows：add_signal_handler 仅 Unix
                break

        # F6 的对偶件：写库失败不再伪装成功（会向上抛），而抛出不得让她僵死 ⇒ 上监督器。
        # `on_error` 把"循环真的崩过"喂给「痛」的计数（路线图 §7.6：异常捕获 > 阈值）。
        beat_task = asyncio.create_task(
            supervise(heartbeat.run, "soul-heartbeat", on_error=heartbeat.record_error),
            name="soul-heartbeat",
        )
        ctx.phase = "heartbeat_loop"
        try:
            await stop.wait()
        except KeyboardInterrupt:
            pass
        finally:
            ctx.phase = "shutdown"
            log.info("soul stopping：优雅关闭（先停表达，再停心跳与库）")
            heartbeat.set_distress(False)
            # 1.6 关闭序的 worker 段：停收新任务 → 等在跑任务 ≤ EXPRESS_TIMEOUT_S
            # （10s 宽限）→ 超时取消 → 排空结果队列。落库归属不变（D2）：取回的结果
            # 交回心跳写，且**先停它、再停库**。
            pending: list[ExpressionResult] = await expression_worker.aclose()
            await heartbeat.stop()
            # 落库是**尽力而为**：写失败已不会伪装成功（F6），但它不该阻断关闭序
            # ——否则连接不关、档案不封存。
            try:
                await heartbeat.persist_expressions(pending)
            except Exception:
                log.exception("关闭时排空表达结果失败（继续关闭，避免连接与档案悬空）")
            await stop_with_cancel(beat_task)
            await state_store.close()
            await heartbeat_store.close()
            # P0-R2：正常关闭 = 删掉会话标记（下次启动据此判定"上次是正常的"）
            try:
                _mark_session_end(settings)
            except OSError:
                log.exception("无法清除会话标记（下次启动会误报上次非正常退出）")
            ctx.phase = "stopped"
            log.info("soul stopped：档案已封存")
        return 0
    except Exception as exc:
        # P0-R2：任何未捕获异常都必须留下现场。**先写现场，再返回非 0**。
        crash_log = _write_crash_report(settings, exc, ctx)
        if crash_log is not None:
            with contextlib.suppress(Exception):
                get_logger("soul").error(
                    "进程异常退出（现场已落盘）", crash_log=str(crash_log), phase=ctx.phase
                )
        return 1


def main() -> int:
    settings = get_settings()
    settings.ensure_dirs()
    # P0-R2：启动即留痕 + 上次是否正常关闭（都在任何库/日志初始化之前，纯文件操作）
    previous_session = _detect_abnormal_exit(settings)
    try:
        _mark_session_start(settings)
    except OSError as exc:
        with contextlib.suppress(Exception):
            sys.stderr.write(f"[warn] 无法写启动标记 {_session_marker(settings)}: {exc}\n")
    try:
        return asyncio.run(_run(settings, previous_session=previous_session))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # 最后兜底：连 asyncio.run 层面都抛了（极罕见），现场同样不能丢
        ctx = _CrashContext()
        ctx.phase = "asyncio.run"
        _write_crash_report(settings, exc, ctx)
        return 1


if __name__ == "__main__":
    sys.exit(main())
