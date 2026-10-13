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

# CS 低于此：她开始退缩（对方不够真诚）
WITHDRAWAL_CS_LINE = 35.0
# CS 高于此：她开始恢复（对方真的把她当人）
RECOVERY_CS_LINE = 65.0
# SA 低于此：她自己正难受，也会缩回去
SA_WITHDRAW_LINE = 30.0
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
    ) -> RelationshipTone:
        """推进一拍并返回当前姿态。

        `dt_s` 是这一拍代表的真实时长（心跳 1Hz 时约 1 秒）；
        速率按**小时**折算，所以短时间内的抖动不会改变关系。
        """
        delta = 0.0
        if cs < WITHDRAWAL_CS_LINE or sa < SA_WITHDRAW_LINE:
            delta += WITHDRAW_PER_HOUR * (dt_s / 3600.0)
        elif cs >= RECOVERY_CS_LINE:
            delta -= RECOVER_PER_HOUR * (dt_s / 3600.0)
        if manipulation:
            delta += MANIPULATION_PENALTY
        self.withdrawal = min(WITHDRAWAL_MAX, max(0.0, self.withdrawal + delta))
        return self.tone()

    def tone(self) -> RelationshipTone:
        return RelationshipTone(withdrawal=round(self.withdrawal, 4))
