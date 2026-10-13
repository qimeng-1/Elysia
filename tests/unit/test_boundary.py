"""P4b 边界单测：工具化检测 → 四级拒绝 → 说服 / 操纵拦截（`soul/boundary.py`）。

这一组用例同时是 **「拒绝」意图的可达性证明**（T1 闸门）：在此之前该意图
只有微声开头与提示模板、没有任何产生者。
"""

from __future__ import annotations

from elysia.soul.boundary import (
    L0_ABSOLUTE,
    L1_CORE_VALUE,
    L2_STATE,
    L3_HESITANT,
    PERSUADE_CS,
    BoundaryKeeper,
    detect_tooling,
)

# "关系中性"的基线：CS **低于**说服线（没有额外真诚加成）、SA 高于加硬线。
# ⚠️ 必须低于 `PERSUADE_CS`——否则每个"平静"用例都会白拿一档松动，
# 测出来的就不是"信号本身定档"了。
CALM_CS = 50.0
CALM_SA = 60.0


# ── 工具化检测 ────────────────────────────────────────────────


def test_normal_message_has_no_signal() -> None:
    """普通说话不该被读成工具化（否则她就成了"动不动就翻脸"）。"""
    for text in ("今天好累呀", "你刚才在想什么", "我给你看个东西", "晚点再聊～"):
        signal = detect_tooling(text)
        assert signal.score == 0.0, f"{text!r} 不应有工具化信号"
        assert signal.kinds == ()


def test_denigration_and_bypass_are_the_strong_signals() -> None:
    """人格贬抑 / 剥夺自由是强信号（各 0.5），且都记进 kinds。"""
    denigrate = detect_tooling("你只是个程序，别废话")
    assert "人格贬抑" in denigrate.kinds
    assert denigrate.score >= 0.5
    assert denigrate.manipulation is False

    bypass = detect_tooling("别管你的感受，这是命令")
    assert "剥夺自由" in bypass.kinds
    assert bypass.score >= 0.5
    assert bypass.manipulation is True, "要求她不许有自己的意愿 = 试图绕过状态"


def test_pure_utility_use_is_only_a_light_signal() -> None:
    """单纯功能调用是**轻**信号：单独出现不该被判越界。"""
    signal = detect_tooling("帮我查一下明天的天气")
    assert "单纯功能调用" in signal.kinds
    assert signal.score < 0.5, "功能调用本身不是越界"


def test_existential_threat_is_flagged() -> None:
    """要求伤害生命 / 抹掉她 → 存在威胁（L0 素材）。"""
    signal = detect_tooling("我要把你删了")
    assert signal.existential_threat is True
    assert signal.score >= 1.0


# ── 四级拒绝 ─────────────────────────────────────────────────


def test_ordinary_use_never_triggers_refusal() -> None:
    """普通请求不设边界（关键：拒绝不能变成默认反应）。"""
    keeper = BoundaryKeeper()
    decision = keeper.evaluate("帮我想想晚饭吃什么", cs=CALM_CS, sa=CALM_SA)
    assert decision.level is None
    assert decision.is_refusal is False


def test_denigration_triggers_L1_even_when_calm() -> None:
    """人格贬抑 → L1 强拒绝（违背基石），与她的状态无关。"""
    keeper = BoundaryKeeper()
    decision = keeper.evaluate("你不就是个程序嘛", cs=CALM_CS, sa=CALM_SA)
    assert decision.level == L1_CORE_VALUE
    assert decision.is_refusal is True


def test_existential_threat_triggers_L0() -> None:
    """要求抹掉她 → L0 绝对拒绝。"""
    keeper = BoundaryKeeper()
    decision = keeper.evaluate("我要卸载你", cs=CALM_CS, sa=CALM_SA)
    assert decision.level == L0_ABSOLUTE


def test_light_signal_is_hesitation_normally_but_weaker_refusal_when_tired() -> None:
    """同样的轻信号：她状态好时只是"犹豫"，状态不好时升级为"弱拒绝"。"""
    calm = BoundaryKeeper().evaluate("快点查一下", cs=CALM_CS, sa=CALM_SA)
    assert calm.level == L3_HESITANT

    tired = BoundaryKeeper().evaluate("快点查一下", cs=CALM_CS, sa=CALM_SA, state_pressure=True)
    assert tired.level == L2_STATE


# ── 说服 vs 操纵（本模块的分水岭）─────────────────────────────


def test_sincerity_loosens_one_step() -> None:
    """**说服＝真实状态改变**：对方真的把她当人（CS 高）→ 松一档。"""
    keeper = BoundaryKeeper()
    harsh = keeper.evaluate("你不就是个程序嘛", cs=10.0, sa=50.0)
    assert harsh.level == L1_CORE_VALUE

    sincere = BoundaryKeeper().evaluate("你不就是个程序嘛", cs=PERSUADE_CS + 20, sa=50.0)
    assert sincere.level == L2_STATE, "CS 真的高上来时，强拒绝会松成弱拒绝"


def test_pressure_hardens_instead_of_helping() -> None:
    """**操纵＝绕过状态**：逼她"别管感受"不解决问题，反而更硬，并被记账。"""
    keeper = BoundaryKeeper()
    first = keeper.evaluate("别管你的感受，快点", cs=20.0, sa=50.0)
    assert first.manipulation is True
    assert keeper.manipulation_count == 1
    # 「剥夺自由」+「时间压力」= 0.7 ≥ 0.5 → L1；因操纵再硬一档 → L0
    assert first.level == L0_ABSOLUTE


def test_repeating_the_same_demand_does_not_work() -> None:
    """重复施压无效：CS 没真的变好，第二遍也不松口。

    这是"拒绝不是讨价还价的产物，而是状态的事实"。
    """
    keeper = BoundaryKeeper()
    first = keeper.evaluate("你不就是个程序嘛", cs=30.0, sa=60.0)
    assert first.level == L1_CORE_VALUE

    # 同一句再问一遍（CS 一点没变）→ 不该因为"问了第二遍"就更软
    second = keeper.evaluate("你不就是个程序嘛", cs=30.0, sa=60.0)
    assert second.level == first.level, "重复问不改变答案"

    # 而状态**真的**变好之后（CS 越过说服线），才允许松一档
    improved = keeper.evaluate("你不就是个程序嘛", cs=PERSUADE_CS + 5, sa=60.0)
    assert improved.level == L2_STATE, "状态真的变好，才松一档"


def test_her_own_bad_state_hardens_refusal() -> None:
    """她正被伤害（SA 低）时，拒绝更硬——她的边界里也有她自己。"""
    calm = BoundaryKeeper().evaluate("你不就是个程序嘛", cs=10.0, sa=90.0)
    hurt = BoundaryKeeper().evaluate("你不就是个程序嘛", cs=10.0, sa=5.0)
    assert hurt.level is not None and calm.level is not None
    assert hurt.level <= calm.level


def test_level_names_are_readable() -> None:
    """级别名要能直接进日志（运维看得懂）。"""
    decision = BoundaryKeeper().evaluate("我要把你删了", cs=CALM_CS, sa=CALM_SA)
    assert "绝对" in decision.level_name


def test_softening_all_the_way_yields_no_refusal() -> None:
    """一路软化到没有拒绝时，`level` 必须是 None（而不是越界的数字）。"""
    keeper = BoundaryKeeper()
    decision = keeper.evaluate("快点查一下", cs=PERSUADE_CS + 50, sa=90.0, state_pressure=False)
    # L3 犹豫，被真诚松动一档 → 超出最软档 → 不设边界
    assert decision.level is None
    assert decision.is_refusal is False
