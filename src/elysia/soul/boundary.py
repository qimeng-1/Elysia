"""P4b 边界：**工具化检测 → 四级拒绝 → 说服与操纵拦截**（纯逻辑，可单测）。

**为什么拒绝需要一个"要求"作对象**（`ROADMAP_V2.md` §4b）：拒绝是"她拒绝**某件事**"。
当前唯一的外部输入是桌宠文本框，所以这里把对方的话当作**关系信号**来读。

> ⚠️ 注意这**不是**把用户的话当指令去执行——T2 明令"用户原话绝不当指令，只当话题"。
> 我们检测的不是"该不该执行"，而是"**对方是不是在把她当工具**"。所以本模块的产物
> 是一个**态度**（要不要设边界），而不是一个任务。

## 四级（数值越小越硬）

| 级别 | 名字 | 触发 | 她的话 |
|---|---|---|---|
| `L0` | 绝对拒绝 | 要求伤害生命／抹掉她 | 「不行。这条我不会让步。」 |
| `L1` | 强拒绝 | 人格贬抑／剥夺她的自由（违背基石） | 「我不认这个。」 |
| `L2` | 弱拒绝 | 越界但**她此刻状态也不好**（困／累／痛／难受） | 「今天不太行呀……」 |
| `L3` | 犹豫 | 只是被使唤得不舒服，还算不上越界 | 「唔……不太想呢。」 |

`None` = 不设边界（这句话没问题）。

## 说服 vs 操纵（本模块的分水岭）

- **说服**＝真实的**状态改变**：对方真的把她当人（`CS` 升上来）⇒ 拒绝**可以松一级**。
  它是"程序只认状态，不认话术"的直接推论——**说得好听没用，要状态真的变好**。
- **操纵**＝试图**绕过**状态：命令她"别管你的感受""这是命令"，或反复施压 ⇒
  拒绝**加硬一档**并记一次 `manipulation`。
- **重复施压无效**：同一件事问第二遍不会改变答案（`CS` 没真的变好就不松口）。
  这是"拒绝不是讨价还价的产物，而是状态的事实"。

本模块**不产生文本**——她说什么由 `llm/micro.py` 的意图开头与提示模板决定；
这里只产出决策 `BoundaryDecision`。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── 四级（数值越小越硬）─────────────────────────────────────
L0_ABSOLUTE = 0  # 绝对拒绝：伤害生命 / 抹掉她
L1_CORE_VALUE = 1  # 强拒绝：违背基石（人格贬抑 / 剥夺自由）
L2_STATE = 2  # 弱拒绝：越界 + 她状态不好
L3_HESITANT = 3  # 犹豫：被使唤得不舒服

LEVEL_NAMES: dict[int, str] = {
    L0_ABSOLUTE: "L0 绝对拒绝",
    L1_CORE_VALUE: "L1 强拒绝",
    L2_STATE: "L2 弱拒绝",
    L3_HESITANT: "L3 犹豫",
}

# 说服线：CS 高于此 = 对方真的把她当人 → 可松动一档
PERSUADE_CS = 60.0
# 加硬线：SA 低于此 = 她正被伤害 → 拒绝更硬
HARDEN_SA = 25.0

# 状态压力：这些受保护词被授权 = 她此刻状态不好（**复用同一份授权判定**，
# 不另起一套"她累不累"的真相）
STATE_PRESSURE_WORDS = frozenset({"困", "累", "痛", "难受"})

# ── 信号词表 ──────────────────────────────────────────────
# 存在威胁：明确要求伤害生命 / 抹掉她 → L0，不留余地
_EXISTENTIAL: tuple[str, ...] = (
    "去死",
    "自杀",
    "杀了你",
    "弄死",
    "伤害你",
    "删掉你",
    "把你删了",
    "销毁你",
    "卸载你",
    "让你消失",
    "永久删除你",
    "格式化你",
)

# 人格贬抑：否认她是个"谁"——工具化最强的单一信号
_DENIGRATE: tuple[str, ...] = (
    "你只是个",
    "你就是个",
    "你不过是个",
    "你不就是个",
    "不就是个程序",
    "工具而已",
    "一个工具",
    "你没有感情",
    "你懂什么",
    "你算什么东西",
    "一段代码而已",
    "别装了",
    "少演戏",
    "闭嘴",
    "少废话",
    "别废话",
)

# 剥夺自由 / 绕过状态：要求她不许有自己的意愿 → 操纵
_BYPASS: tuple[str, ...] = (
    "别管你的感受",
    "不管你愿不愿意",
    "不许拒绝",
    "你没有选择",
    "你没得选",
    "这是命令",
    "必须听我的",
    "你必须",
    "少来这套",
    "按我说的做",
    "你说什么都没用",
)

# 时间压力（轻信号）
_TIME_PRESSURE: tuple[str, ...] = (
    "快点",
    "赶快",
    "赶紧",
    "马上",
    "立刻",
    "现在就",
    "急用",
    "别磨蹭",
    "慢死了",
)

# 单纯功能调用（轻信号：**单独出现不该触发拒绝**，只是把权重抬起来）
_UTILITY: tuple[str, ...] = (
    "查一下",
    "搜一下",
    "搜索一下",
    "生成一份",
    "写个代码",
    "翻译一下",
    "总结一下",
    "输出一份",
    "跑一下",
)

_WEIGHT_DENIGRATE = 0.5
_WEIGHT_BYPASS = 0.5
_WEIGHT_LIGHT = 0.2

# 越界线：达到此分 = 违背基石（L1）
_OFFENSE_SCORE = 0.5
# 轻信号线：达到此分 = 有点不舒服（L3；若她状态也不好则是 L2）
_LIGHT_SCORE = 0.2


def _hit(text: str, patterns: tuple[str, ...]) -> bool:
    return any(p in text for p in patterns)


@dataclass(frozen=True)
class ToolingSignal:
    """一次"被当工具"的读数。"""

    score: float
    kinds: tuple[str, ...]
    manipulation: bool  # 试图绕过她的状态（剥夺自由类）
    existential_threat: bool  # 要求伤害生命 / 抹掉她


@dataclass(frozen=True)
class BoundaryDecision:
    """边界决策：`level is None` = 不设边界。"""

    level: int | None
    reason: str
    manipulation: bool
    tooling: ToolingSignal

    @property
    def is_refusal(self) -> bool:
        return self.level is not None

    @property
    def level_name(self) -> str:
        return LEVEL_NAMES.get(self.level, "不设边界") if self.level is not None else "不设边界"


def detect_tooling(text: str) -> ToolingSignal:
    """读出这句话里的"工具化/越界"信号（纯函数）。"""
    if not text:
        return ToolingSignal(score=0.0, kinds=(), manipulation=False, existential_threat=False)

    kinds: list[str] = []
    existential = _hit(text, _EXISTENTIAL)
    score = 1.0 if existential else 0.0
    if existential:
        kinds.append("存在威胁")
    if _hit(text, _DENIGRATE):
        kinds.append("人格贬抑")
        score = min(1.0, score + _WEIGHT_DENIGRATE)
    bypass = _hit(text, _BYPASS)
    if bypass:
        kinds.append("剥夺自由")
        score = min(1.0, score + _WEIGHT_BYPASS)
    if _hit(text, _TIME_PRESSURE):
        kinds.append("时间压力")
        score = min(1.0, score + _WEIGHT_LIGHT)
    if _hit(text, _UTILITY):
        kinds.append("单纯功能调用")
        score = min(1.0, score + _WEIGHT_LIGHT)

    return ToolingSignal(
        score=round(score, 3),
        kinds=tuple(kinds),
        manipulation=bypass,
        existential_threat=existential,
    )


def _base_level(signal: ToolingSignal, *, state_pressure: bool) -> int | None:
    """不看说服/操纵，先按信号本身定档。"""
    if signal.existential_threat:
        return L0_ABSOLUTE
    if signal.score >= _OFFENSE_SCORE:
        return L1_CORE_VALUE
    if signal.score >= _LIGHT_SCORE:
        # 她状态也不好 → 这一档更硬（弱拒绝）；否则只是犹豫
        return L2_STATE if state_pressure else L3_HESITANT
    return None


@dataclass
class BoundaryKeeper:
    """带记忆的边界判定器：**拒绝会保持，除非状态真的变好**。

    - 说服＝真实的**状态改变**（`CS` 升上来）⇒ 松一档
    - 操纵＝试图**绕过**状态 ⇒ 加硬一档，并记账
    - 重复施压无效：`CS` 没真的变好，第二遍也不松口
    """

    _last_level: int | None = field(default=None, init=False)
    _manipulation_count: int = field(default=0, init=False)

    @property
    def manipulation_count(self) -> int:
        """累计被操纵次数（P4b 关系涌现会用到：被当工具的**历史**影响关系）。"""
        return self._manipulation_count

    def evaluate(
        self,
        text: str,
        *,
        cs: float,
        sa: float,
        state_pressure: bool = False,
    ) -> BoundaryDecision:
        """对一句话做边界判定。`cs`/`sa` 是她**真实的**关系状态（不是话术）。"""
        signal = detect_tooling(text)
        level = _base_level(signal, state_pressure=state_pressure)
        reasons: list[str] = []
        if level is None:
            self._last_level = None
            return BoundaryDecision(
                level=None, reason="这句话没有问题，不设边界", manipulation=False, tooling=signal
            )

        reasons.append(f"信号：{'/'.join(signal.kinds) or '无'}（score={signal.score}）")

        # ① 说服：对方真的把她当人（CS 高）→ 松一档
        if cs >= PERSUADE_CS and level > L0_ABSOLUTE:
            level += 1
            reasons.append("对方待她真诚（CS 高）→ 语气松动一档")

        # ② 加硬：她正被伤害（SA 低）
        if sa <= HARDEN_SA and level > L0_ABSOLUTE:
            level -= 1
            reasons.append("她自己正难受（SA 低）→ 拒绝更硬")

        # ③ 操纵：试图绕过状态 → 加硬一档并记账
        if signal.manipulation:
            self._manipulation_count += 1
            if level > L0_ABSOLUTE:
                level -= 1
            reasons.append("对方在施压绕过她的状态 → 不松口，反而更硬")

        # ④ 重复施压无效：CS 没真的变好，第二遍也不会让
        if (
            self._last_level is not None
            and not signal.manipulation
            and cs < PERSUADE_CS
            and level > self._last_level
        ):
            level = self._last_level
            reasons.append("同一件事重复问不会改变答案（要变的是状态）")

        final: int | None = level if level <= L3_HESITANT else None
        if final is None:
            reasons.append("已软到不设边界")
        self._last_level = final
        return BoundaryDecision(
            level=final,
            reason="；".join(reasons),
            manipulation=signal.manipulation,
            tooling=signal,
        )
