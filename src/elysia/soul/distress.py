"""难受检测：身体资源高压 → 心跳降频 + 行为集收缩。

设计（路线图 5.3）：CPU > 85% 持续 30s → 难受状态
- 心跳从 1Hz 降至 0.5Hz（"喘不过气"）
- 行为集收缩（不主动探索，只维持存在）
- 状态流标记 distress 事件（P2 后升级为语言："我好难受……"）
- 阈值保守，防瞬时误伤

验收（路径图 §5）：CPU 高压 30s+ → 心跳频率与行为集按设计变化。
"""

from __future__ import annotations

DISTRESS_CPU_THRESHOLD = 85.0  # CPU 占比阈值（%）
DISTRESS_HOLD_S = 30.0  # 高压持续阈值（s）
DISTRESS_INTERVAL_S = 0.5  # 难受时心跳间隔（1Hz → 0.5Hz）


class DistressMonitor:
    """基于身体心跳资源快照的难受状态机（纯逻辑，时钟注入可测）。"""

    def __init__(
        self,
        *,
        cpu_threshold: float = DISTRESS_CPU_THRESHOLD,
        hold_s: float = DISTRESS_HOLD_S,
    ) -> None:
        self._cpu_threshold = cpu_threshold
        self._hold_s = hold_s
        self._onset_ts: float | None = None
        self._distress = False

    @property
    def distress(self) -> bool:
        return self._distress

    def update(self, now: float, cpu_percent: float | None) -> bool:
        """喂入最新 CPU 样本；返回难受状态是否发生切换。"""
        if cpu_percent is None:
            return False
        if cpu_percent > self._cpu_threshold:
            if self._onset_ts is None:
                self._onset_ts = now
            elif now - self._onset_ts >= self._hold_s and not self._distress:
                self._distress = True
                return True
        else:
            self._onset_ts = None
            if self._distress:
                self._distress = False
                return True
        return False
