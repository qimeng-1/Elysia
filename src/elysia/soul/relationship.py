"""P4b 关系涌现：**没有关系量表，关系从状态里长出来**（路线图 §11）。

- CS 长期偏低（对方不真诚 / 只把她当工具），或她长期难受（SA 低）→ 她**退缩**
- 真诚修复（CS 真的回到高位）→ 慢慢恢复
- **没有开关**：不存在"亲密度"旋钮，只有一个随她真实状态缓慢移动的**退缩量**

**T1（必须有可测量的表达效果）**：退缩不是情绪贴纸，它真的改变她的表达——
- `interval_factor()`：拉长她开口的最小间隔 ⇒ **说得更少**
- `max_chars()`：压短她的字数上限 ⇒ **说得更短**

时间尺度是**小时**级，不是秒级：一两句不中听的话不该让她立刻变冷
（那叫"脆弱"，不叫"关系"）。而被**操纵**是例外——那是一次即时伤害，立刻记账。
"""

from __future__ import annotations

from dataclasses import dataclass

# ── P0-R19b：关系是**连续量**，不再是分段常数 ──────────────────────
# 旧实现：CS≤35 才退缩、CS≥65 才恢复，中间 83% 的时间退缩量完全不动。
# 真机后果：她一旦到过 1.0（退缩到底），就永远出不来——关系卡死在最差那一格。
# 现在以**设定点**为 0 点连续驱动：低于设定点 → 缓慢加深；高于 → 缓慢恢复；
# 正好在设定点 → 也仍以 BASE 速率向 0 漂移（关系不会冻结）。
WITHDRAWAL_CS_LINE = 35.0  # 仅保留给文档/兼容引用，不再参与判定
RECOVERY_CS_LINE = 65.0  # 同上
CS_SPAN = 10.0  # 偏离设定点多少点算"满速"
WITHDRAW_DEEPEN_PER_HOUR = 0.05  # 低于设定点（满速）时每小时加深
RECOVER_BASE_PER_HOUR = 0.006  # 正好在设定点时每小时恢复（缓慢但持续）
RECOVER_GAIN_PER_HOUR = 0.08  # 高于设定点（满速）时的额外恢复
# SA 低于此：她自己正难受，也会缩回去
# P0-R19b：SA 的绝对线也错配——真实 SA 分布 15.0–29.2（中位 19.7），
# 旧线 30 ⇒ **100% 的时间都在「她难受」**，退缩量只会单调涨到 1.0（真机卡死的第二因）。
# 改成 14.0（低于她的保护带下沿 15）：只有真的掉到底才算她自己难受。
SA_WITHDRAW_LINE = 14.0
# 持续低 CS/低 SA 时，每小时涨多少（约 20 小时退到底）
WITHDRAW_PER_HOUR = 0.05
# 持续真诚时，每小时降多少（约 13 小时回到平常）
RECOVER_PER_HOUR = 0.075
# 被操纵一次的直接惩罚（不必等"长期"——那是一次即时伤害）
MANIPULATION_PENALTY = 0.02
# 退缩量上界
WITHDRAWAL_MAX = 1.0

# 三档口语标签（给日志与观测用；**不是**可切换的模式）
RELATION_CLOSE = "亲近"
RELATION_PLAIN = "平常"
RELATION_WITHDRAWN = "退缩"

# 判定档位用的阈值
_WITHDRAWN_AT = 0.6
_CLOSE_UNDER = 0.15

# 字数下限：再退缩，她也要能把一句话说完
MIN_MAX_CHARS = 40
BASE_MAX_CHARS = 120


@dataclass(frozen=True)
class RelationshipTone:
    """她此刻对这段关系的**姿态**（由退缩量导出，不是独立输入）。"""

    withdrawal: float

    @property
    def label(self) -> str:
        if self.withdrawal >= _WITHDRAWN_AT:
            return RELATION_WITHDRAWN
        if self.withdrawal <= _CLOSE_UNDER:
            return RELATION_CLOSE
        return RELATION_PLAIN

    def interval_factor(self) -> float:
        """开口间隔倍数：退到底时变成 2 倍（说得更少）。"""
        return 1.0 + self.withdrawal

    def max_chars(self, base: int = BASE_MAX_CHARS) -> int:
        """字数上限：退到底时压到约一半（说得更短），但绝不低于 `MIN_MAX_CHARS`。"""
        return max(MIN_MAX_CHARS, round(base * (1.0 - 0.5 * self.withdrawal)))


@dataclass
class RelationshipKeeper:
    """随她真实状态缓慢移动的退缩量（`0.0` 亲近 → `1.0` 退缩到底）。"""

    withdrawal: float = 0.0

    def update(
        self,
        *,
        dt_s: float,
        cs: float,
        sa: float,
        manipulation: bool = False,
        cs_setpoint: float = 60.0,
    ) -> RelationshipTone:
        """推进一拍并返回当前姿态。

        `dt_s` 是这一拍代表的真实时长（心跳 1Hz 时约 1 秒）；
        速率按**小时**折算，所以短时间内的抖动不会改变关系。
        """
        hours = dt_s / 3600.0
        delta = 0.0
        if sa < SA_WITHDRAW_LINE:
            # 她自己难受时退缩（与对方无关）——优先，不同时恢复
            delta += WITHDRAW_PER_HOUR * hours
        else:
            deviation = float(cs) - float(cs_setpoint)
            if deviation < 0:
                depth = min(1.0, -deviation / CS_SPAN)
                delta += WITHDRAW_DEEPEN_PER_HOUR * depth * hours
            else:
                gain = min(1.0, deviation / CS_SPAN)
                delta -= (RECOVER_BASE_PER_HOUR + RECOVER_GAIN_PER_HOUR * gain) * hours
        if manipulation:
            delta += MANIPULATION_PENALTY
        self.withdrawal = min(WITHDRAWAL_MAX, max(0.0, self.withdrawal + delta))
        return self.tone()

    def tone(self) -> RelationshipTone:
        return RelationshipTone(withdrawal=round(self.withdrawal, 4))
