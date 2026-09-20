"""索引衰减（P3 §8.3）：遗忘的物理实现。

遗忘不是删除——数据永远保留，只是检索路径碎片化：
- strength（索引强度）随时间指数衰减
- 检索耗时随年龄/强度上升：10ms → 50ms → 500ms → ≥1s
- 珍贵记忆（protected）衰减 3× 慢（"真爱不模糊"）

纯函数，可单测；落库由调用方（睡眠整合/心跳循环）按结果执行。
"""

from __future__ import annotations

import math

# 检索耗时映射锚点（规划验收：30d→50ms / 90d→500ms / 365d→≥1s）
LATENCY_DAYS_BASE = 1.0  # 1 天内 → 10ms
LATENCY_30D_MS = 50.0
LATENCY_90D_MS = 500.0
LATENCY_365D_MS = 1000.0

# strength 衰减时间常数（天）：strength → e^(-age/τ)
STRENGTH_TAU_DAYS = 45.0

# 珍贵记忆衰减减速系数
PROTECTED_DECAY_SLOWDOWN = 3.0

# 检索过滤阈值：strength 低于此值 → 记忆"找不到了"（数据仍在）
STRENGTH_RETRIEVE_FLOOR = 0.2


def effective_age_days(age_days: float, protected: bool = False) -> float:
    """有效年龄：珍贵记忆按 1/3 计入（衰减慢 3×）。"""
    return age_days / (PROTECTED_DECAY_SLOWDOWN if protected else 1.0)


def decay_strength(
    strength: float,
    age_days: float,
    protected: bool = False,
    *,
    floor: float = 0.0,
) -> float:
    """索引强度指数衰减。

    strength' = strength · e^(−effective_age / τ)
    永不跌破 floor（默认 0，即数据永在，只是索引弱）。
    """
    effective = effective_age_days(age_days, protected)
    decayed = strength * math.exp(-effective / STRENGTH_TAU_DAYS)
    return max(floor, min(strength, decayed))


def retrieve_latency_ms(age_days: float, protected: bool = False) -> float:
    """检索耗时映射（数据不删，只是变慢）。

    锚点（按有效年龄）：
      <1d    → 10ms（新鲜）
      30d    → 50ms
      90d    → 500ms
      365d   → ≥1000ms（勉强记起，需翻找）
    """
    eff = effective_age_days(age_days, protected)

    if eff <= LATENCY_DAYS_BASE:
        return 10.0
    if eff <= 30.0:
        # 1d → 30d：10 → 50ms 线性
        return 10.0 + (LATENCY_30D_MS - 10.0) * (eff - LATENCY_DAYS_BASE) / (
            30.0 - LATENCY_DAYS_BASE
        )
    if eff <= 90.0:
        # 30d → 90d：50 → 500ms 线性
        return LATENCY_30D_MS + (LATENCY_90D_MS - LATENCY_30D_MS) * (eff - 30.0) / 60.0
    if eff <= 365.0:
        # 90d → 365d：500 → 1000ms 线性
        return LATENCY_90D_MS + (LATENCY_365D_MS - LATENCY_90D_MS) * (eff - 90.0) / 275.0
    # >365d：持续缓慢变慢（永远 >=1s，但永不消失）
    return LATENCY_365D_MS + 100.0 * (eff - 365.0) / 365.0
