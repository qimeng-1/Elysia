"""T1 取消测试（架构铁律一）——把"原则"变成"闸门"。

T1 原文：「每个状态变量至少映射一个物理性影响（资源优先级/循环频率/检索深度/语速），
**否则删除该变量**。取消测试：停用任意变量后行为/表达必须可测量变化。」

本文件回答两类问题：

1. **可达性（活性）**：每个"声明过的输出"都必须能从合法状态空间到达——否则它是死代码。
2. **取消性（T1 本义）**：停用某个维度后结果必须发生可测量变化——否则该维度只是装饰。

建立动机（两个真实历史样本，**都通过了 ruff / mypy strict / 397 项回归**）：

- `curiosity` 旧实现是 `0.1 if has_event else 0.0` 的布尔占位，上限 0.1，
  而 `expression._tts_params` 要求 `> 0.4` ⇒「好奇」语音情绪**数学上不可达**；
- `rest` 原始值上界 0.5（难受时），惯性平滑**收敛到恰好 0.5、永不越过**，
  而阈值判定是严格大于（`> 0.5`）⇒「疲惫」语音情绪**同样不可达**；
- `_expression_env` 把 `cpu_pct` / `q_len` / `memory_gap` 写成字面量 `0`，
  导致「难受」「累」「好像忘了什么」等受保护词**永远无法授权**（且 `memory_gap`
  的量纲还写错成 `> 10`，而它是 0-1 的强度）。

⇒ 这类"语义死角"不会让类型检查或普通回归失败，**只有本闸门能拦住**。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from elysia.core.clock import SimulatedClock
from elysia.core.mode import ModeManager
from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.core.timesense import TimeSense
from elysia.memory.levels import LEVEL_SHALLOW
from elysia.memory.retrieve import RECALL_LATENCY_CAP_MS, MemoryHit, recall_latency_ms
from elysia.soul.brain import BrainLoop, BrainOutput, WillOutput
from elysia.soul.desire import DesireState, DesireSystem
from elysia.soul.dimensions import FeelingMapper, FeelingState
from elysia.soul.expression import build_expression
from elysia.soul.heartbeat import ERROR_BURST_WINDOW_S, SoulHeartbeat, make_soul_state
from elysia.soul.words import PROTECTED_WORDS, permitted_words

# ── 感受维度的可达上界 ──────────────────────────────────────────
# 由 desire 硬边界 + `dimensions._raw_feelings` 的原始映射推出（非实测，可复核）：
#   chat       = CS/100        CS∈[40,80] → 上界 0.80
#   miss       = 1 − CS/100                → 上界 0.60
#   explore    = TR/100        TR∈[30,70] → 上界 0.70
#   self_check = SA/100        SA∈[15,60] → 上界 0.60
#   rest       = 0.5（难受）/ 0.05         → 上界 0.50（且**不取等**：惯性平滑只逼近）
#   curiosity  = 记忆缺口强度   0-1        → 上界 1.00
ACHIEVABLE_MAX: dict[str, float] = {
    "chat": 0.80,
    "miss": 0.60,
    "explore": 0.70,
    "curiosity": 1.00,
    "rest": 0.50,
    "self_check": 0.60,
}

# 各维度的"停用"值（≈ 基线，用于取消测试的对照侧）
BYPASS: dict[str, float] = {
    "chat": 0.10,
    "miss": 0.20,
    "curiosity": 0.00,
    "rest": 0.05,
}

# 一条"安静"的感受基线：任何优先级分支都不会被它触发
_QUIET = {
    "chat": 0.10,
    "miss": 0.20,
    "explore": 0.30,
    "curiosity": 0.00,
    "rest": 0.05,
    "self_check": 0.05,
}


def _feelings(**overrides: float) -> FeelingState:
    return FeelingState(**{**_QUIET, **overrides})


def _output(feelings: FeelingState, *, anim_bias: float = 0.2) -> BrainOutput:
    """直接构造 BrainOutput（避开随机演化，与本仓库既有测法一致）。"""
    return BrainOutput(
        desire=DesireState(tr=50.0, cs=60.0, sa=30.0),
        feelings=feelings,
        will=WillOutput(direction="reach", strength=0.5, thought_style=0.0, anim_bias=anim_bias),
        action="none",
    )


def _emotion(feelings: FeelingState, *, anim_bias: float = 0.2) -> str:
    """感受 → 最终语音情绪（走真实的 build_expression 管线）。"""
    return str(build_expression(_output(feelings, anim_bias=anim_bias)).to_dict()["tts"]["emotion"])


def _converged_feelings(
    *,
    distress: bool = False,
    memory_gap: float = 0.0,
    cs: float = 70.0,
    steps: int = 40,
) -> FeelingState:
    """让 FeelingMapper 在给定条件下收敛（惯性平滑需要多拍才逼近原始值）。"""
    mapper = FeelingMapper()
    desire = DesireState(tr=45.0, cs=cs, sa=20.0)
    feelings = mapper.compute(desire, distress=distress, memory_gap=memory_gap, dt=1.0)
    for _ in range(steps):
        feelings = mapper.compute(desire, distress=distress, memory_gap=memory_gap, dt=1.0)
    return feelings


# ── 1. 可达性：声明的语音情绪必须都够得到 ────────────────────────


def test_tts_emotions_all_reachable() -> None:
    """6 种语音情绪，每种都要能从合法感受状态到达。

    旧代码里 `tired` 与 `curious` **永远够不到**——本用例正是拦这个的。
    """
    reached = {
        "tired": _emotion(_feelings(rest=ACHIEVABLE_MAX["rest"])),
        "wistful": _emotion(_feelings(miss=ACHIEVABLE_MAX["miss"])),
        "curious": _emotion(_feelings(curiosity=ACHIEVABLE_MAX["curiosity"])),
        "happy": _emotion(_feelings(chat=ACHIEVABLE_MAX["chat"])),
        "calm": _emotion(_feelings(), anim_bias=-0.4),
        "neutral": _emotion(_feelings()),
    }
    unreachable = [name for name, got in reached.items() if got != name]
    assert not unreachable, (
        f"这些语音情绪不可达（声明了却永远拿不到）：{unreachable}；实测映射={reached}"
    )


# ── 2. 取消性（T1 本义）：停用某维度 → 结果必须变化 ──────────────


@pytest.mark.parametrize("dim", ["rest", "miss", "curiosity", "chat"])
def test_cancel_feeling_dimension_changes_expression(dim: str) -> None:
    """取消测试：把某个维度推到上界 vs 停用，语音情绪必须不同。

    若两者相同，说明该维度对表达**没有可测量影响** —— 按 T1 应当删除它。
    """
    active = _emotion(_feelings(**{dim: ACHIEVABLE_MAX[dim]}))
    cancelled = _emotion(_feelings(**{dim: BYPASS[dim]}))
    assert active != cancelled, (
        f"维度 `{dim}` 停用后表达毫无变化（{active} vs {cancelled}）⇒ 它是装饰，按 T1 应删除"
    )


def test_cancel_thresholds_stay_inside_achievable_range() -> None:
    """不变量：消费端的阈值必须**严格小于**该维度的可达上界。

    `rest` 的可达上界是 0.5 且不取等（惯性只逼近），所以阈值必须 < 0.5；
    `curiosity` 的可达上界是 1.0，阈值 0.4 留有余量。
    阈值一旦取到或越过上界 ⇒ 死分支，且不会有任何类型检查或普通回归报错。
    """
    # rest：推到上界必须真的能触发"疲惫"
    assert _emotion(_feelings(rest=ACHIEVABLE_MAX["rest"])) == "tired"
    # curiosity：推到上界必须真的能触发"好奇"
    assert _emotion(_feelings(curiosity=ACHIEVABLE_MAX["curiosity"])) == "curious"


# ── 3. 端到端：状态 → 大脑循环 → 表达（真正的 T1 链路）───────────


def test_memory_gap_drives_curiosity_end_to_end() -> None:
    """端到端：记忆缺口 → 好奇心 → 语音情绪。

    走真实的 `BrainLoop.step`（含欲望演化 + 感受惯性），而不是手搓 FeelingState——
    这条链路才是"有些事想不起来了 → 她好奇"的完整回路。
    """
    loop = BrainLoop(
        DesireSystem(initial=DesireState(tr=45.0, cs=70.0, sa=20.0)),
        rng=random.Random(0),
    )

    # ⚠️ 必须**立刻取成 float**：`FeelingMapper.compute()` 返回的是内部对象的活引用，
    # 下一拍会被原地改写（本文件建立时即发现此别名缺陷，见 STATUS_MATRIX §12）。
    with_gap = loop.step(distress=False, mode="present", memory_gap=1.0, dt=1.0)
    curiosity_with_gap = with_gap.feelings.curiosity
    assert curiosity_with_gap > 0.4, "缺口强度 1.0 应把好奇推过消费阈值"

    without_gap = loop.step(distress=False, mode="present", memory_gap=0.0, dt=1.0)
    curiosity_without_gap = without_gap.feelings.curiosity
    assert curiosity_without_gap < curiosity_with_gap, "缺口消失后好奇应回落"


def test_distress_drives_tired_end_to_end() -> None:
    """端到端：难受 → 休息欲 → "疲惫"语音情绪（收敛后必须够得到阈值）。"""
    tired = _converged_feelings(distress=True)
    assert tired.rest > 0.45, f"难受收敛后 rest 应越过 0.45，实测 {tired.rest}"
    assert _emotion(tired) == "tired"

    calm = _converged_feelings(distress=False)
    assert _emotion(calm) != "tired", "不难受时不应出'疲惫'情绪"


def test_converged_rest_never_exceeds_raw_ceiling() -> None:
    """文档化旧缺陷的成因：rest 收敛到**恰好** 0.5（原始上界），且永不越过它。

    而旧阈值判定是**严格大于**（`rest > 0.5`）⇒ 恒不成立（死分支）。
    保留此用例，防止有人把阈值改回 0.5。
    """
    converged = _converged_feelings(distress=True)
    assert converged.rest <= 0.5
    assert converged.rest == pytest.approx(0.5, abs=0.01)
    assert not (converged.rest > 0.5), "严格大于 0.5 永不成立——这正是旧 tired 分支死掉的原因"


# ── 4. 快照性：BrainOutput 不得持有活引用 ────────────────────────


def test_brain_output_is_snapshot_not_live_reference() -> None:
    """`BrainOutput` 必须是**快照**：后续拍的演化不得改写已交付的那一份。

    真缺陷（STATUS_MATRIX §12 #1）：`DesireSystem.step()` 返回 `self._state`、
    `FeelingMapper.compute()` 返回 `self._current`，心跳每拍**原地改写**；
    若 `BrainOutput` 直接持有它们，交给 worker 异步执行的 `ExpressionJob`
    会读到"执行那一刻"的状态，而不是"决定开口那一刻"的状态。
    """
    loop = BrainLoop(
        DesireSystem(initial=DesireState(tr=45.0, cs=70.0, sa=20.0)),
        rng=random.Random(0),
    )
    first = loop.step(distress=False, mode="present", memory_gap=1.0, dt=1.0)
    # 立刻记录第一拍交付时的真实值
    first_curiosity = first.feelings.curiosity
    first_tr = first.desire.tr

    # 第二拍用完全不同的条件推一步
    second = loop.step(distress=True, mode="present", memory_gap=0.0, dt=1.0)

    assert first.feelings is not second.feelings, "两拍的感受必须是各自独立的对象"
    assert first.desire is not second.desire, "两拍的欲望必须是各自独立的对象"
    assert first.feelings.curiosity == pytest.approx(first_curiosity), (
        "第二拍改写了第一拍已交付的快照 —— BrainOutput 又变回活引用了"
    )
    assert first.desire.tr == pytest.approx(first_tr), (
        "第二拍改写了第一拍已交付的欲望快照 —— BrainOutput 又变回活引用了"
    )


# ── 5. 受保护词（T2 防线）也要过 T1：每个词都必须说得出口 ─────────


def test_protected_words_all_reachable() -> None:
    """每个受保护词都必须存在**可满足的触发环境**，否则它是个永久禁用词。

    真实缺陷样本：`memory_gap` 是 0-1 的缺口强度，旧谓词却写成 `> 10`
    （量纲错了）⇒ 该词在任何真实状态下都不可说。
    """
    # 每个词各给一份"真实可能出现的"环境（量纲与真实来源一致）
    envs: dict[str, dict[str, object]] = {
        "累": {"vrram_mb": 12000.0},  # 大显存机器上的真实高压
        "困": {"is_night": True, "tr": 30.0},
        "痛": {"error_burst": 2},
        "难受": {"cpu_pct": 92.0},
        "想你": {"miss": 0.5},
        "好像忘了什么": {"memory_gap": 0.6},  # 0-1 量纲
    }
    assert set(envs) == set(PROTECTED_WORDS), "受保护词表变了，本用例需同步"
    dead = [word for word, env in envs.items() if word not in permitted_words(env)]
    assert not dead, f"这些受保护词没有任何可满足的触发环境（永久不可说）：{dead}"


def _make_soul(tmp_path: Path) -> SoulHeartbeat:
    """最小 SoulHeartbeat（与 `test_heartbeat.py` 同法），用于验证授权环境接线。"""
    clock = SimulatedClock(start_ts=1_000_000.0, speed=60.0)
    return SoulHeartbeat(
        state_store=StateStore(tmp_path / "state.db"),
        heartbeat_store=HeartbeatStore(tmp_path / "heartbeat.db"),
        timesense=TimeSense(make_soul_state(1_000_000.0), clock),
        mode_mgr=ModeManager(clock=clock),
        brain_loop=BrainLoop(DesireSystem(), rng=random.Random(0)),
        clock=clock,
        interval_s=1.0,
    )


@pytest.mark.asyncio
async def test_expression_env_carries_live_values(tmp_path: Path) -> None:
    """授权环境里每个键都必须是**活值**，不能是字面量。

    真实缺陷样本：`_expression_env` 曾把 `cpu_pct` / `q_len` / `memory_gap`
    / `error_burst` 全写成 `0`，导致「难受」「好像忘了什么」「痛」等词
    **永远无法授权**——哪怕词表与心跳看起来都"接好了"。
    """
    soul = _make_soul(tmp_path)
    # 注入"此刻确实难受 + 确实有记忆缺口"的实时状态
    soul._latest_resources = {"cpu_percent": 92.0}
    soul._latest_memory_gap = 0.6

    output = _output(_feelings())
    env = soul._expression_env(output, {"day_phase": "day"})
    permits = permitted_words(env)

    assert env["cpu_pct"] == pytest.approx(92.0), f"cpu_pct 必须是活值，实际 {env['cpu_pct']}"
    assert env["memory_gap"] == pytest.approx(0.6), (
        f"memory_gap 必须是活值，实际 {env['memory_gap']}"
    )
    assert "难受" in permits, f"CPU 92% 时「难受」应可授权；env={env}"
    assert "好像忘了什么" in permits, f"缺口 0.6 时「好像忘了什么」应可授权；env={env}"


@pytest.mark.asyncio
async def test_error_burst_drives_pain_end_to_end(tmp_path: Path) -> None:
    """端到端：真实错误 → `error_burst` →「痛」可授权（路线图 §7.6）。

    旧实现把 `error_burst` 写成字面量 `0` ⇒ 谓词 `> 1` 永不成立 ⇒「痛」永久禁用。
    喂入口有两个，都汇进 `record_error()`：表达任务失败/超时、常驻循环异常。
    """
    soul = _make_soul(tmp_path)
    output = _output(_feelings())

    env_before = soul._expression_env(output, {"day_phase": "day"})
    assert env_before["error_burst"] == 0, "一开始不该有错误"
    assert "痛" not in permitted_words(env_before), "没出错时不该能说「痛」"

    soul.record_error()
    soul.record_error()

    env_after = soul._expression_env(output, {"day_phase": "day"})
    assert env_after["error_burst"] == 2, f"两次错误应计入，实际 {env_after['error_burst']}"
    assert "痛" in permitted_words(env_after), f"窗口内两次错误应可授权「痛」；env={env_after}"


@pytest.mark.asyncio
async def test_error_burst_forgets_old_errors(tmp_path: Path) -> None:
    """窗口外的旧错误必须剔除，否则「痛」会永久挂着（早年的错不该疼到现在）。"""
    soul = _make_soul(tmp_path)
    soul.record_error()
    soul.record_error()
    assert soul._error_burst() == 2

    # 把这两次错误"推到"窗口之外
    stale = soul._clock.now() - ERROR_BURST_WINDOW_S - 1.0
    soul._error_times.clear()
    soul._error_times.extend([stale, stale])

    assert soul._error_burst() == 0, "窗口外的错误不该继续计入"
    assert "痛" not in permitted_words(soul._expression_env(_output(_feelings()), {}))


# ── 6. 记忆：detail_level 与"越旧越难想起"都必须真的有效果 ─────────


def _hit(
    *, age_days: float | None, detail_level: float = 1.0, protected: bool = False
) -> MemoryHit:
    return MemoryHit(
        memory_id=1,
        narrative="她记得那天你说过的话",
        level=LEVEL_SHALLOW,
        score=1.0,
        protected=protected,
        age_days=age_days,
        detail_level=detail_level,
    )


def test_detail_level_changes_injected_label() -> None:
    """取消测试：`detail_level` 必须真的改变注入给她的话。

    P3 起 `detail_level` 一直**只写不读**（数值在库里、检索路径零消费）——
    这条用例就是拦"它又变回装饰"的。
    """
    clear = _hit(age_days=1.0, detail_level=1.0).label
    fuzzy = _hit(age_days=1.0, detail_level=0.5).label

    assert "记不太清" not in clear
    assert "记不太清" in fuzzy
    assert clear != fuzzy, "detail_level 变了但注入行没变 ⇒ 它是装饰，按 T1 应删除"


def test_recall_latency_grows_with_age() -> None:
    """「记忆越旧越难想起」：回忆耗时必须随年龄增长（遗忘的物理体现）。"""
    fresh = recall_latency_ms([_hit(age_days=0.5)])
    old = recall_latency_ms([_hit(age_days=200.0)])
    assert fresh > 0.0, "新鲜记忆也该有一点点回忆耗时（10ms 档）"
    assert old > fresh, f"更旧的记忆必须更慢（{old} vs {fresh}）"
    assert old <= RECALL_LATENCY_CAP_MS


def test_recall_latency_capped_and_ignores_unknown_age() -> None:
    """极老的记忆有体验兜底上限；拿不到年龄的条目不计入（宁可不提时间，也不硬编）。"""
    assert recall_latency_ms([_hit(age_days=36500.0)]) == pytest.approx(RECALL_LATENCY_CAP_MS)
    assert recall_latency_ms([_hit(age_days=None)]) == 0.0


def test_recall_latency_precious_memory_is_faster() -> None:
    """「真爱不模糊」：珍贵记忆衰减慢 3× ⇒ 同样年龄下回忆得更快。"""
    normal = recall_latency_ms([_hit(age_days=90.0)])
    precious = recall_latency_ms([_hit(age_days=90.0, protected=True)])
    assert precious < normal, f"珍贵记忆应更快想起（{precious} vs {normal}）"
