"""P4：事件驱动的开口调度——"有事才说"，**不用经过时间当触发器**。

根因（真机）：旧触发是"距上次开口 ≥ interval（60–1800s）"⇒ 她永远 ≤10 分钟必有声音
（259 个间隔中位 117s、最长 591s、>30min 0 次；一天 572 条 vs 用户 27 条）。
P4 把"过了多久"从触发条件里删掉：**只在离散事件/状态跨越阈值时**才可能开口，
没事件就可以几小时甚至一整天不说话——这不是异常，是生活。

触发原因（每条都写进 `expression_log.validation.speech_cause`）：
- `discovery`：新的 world 记忆 / 刚完成的意图（P2/P3）
- `threshold`：`miss/chat/rest` 从一侧**跨越**到另一侧（带迟滞，防抖）
- `presence`：你从不在到在场（问候路径）
- `write`：她刚写完东西（write_note done）
- `response`：你对她说话（强制开口；P4 前后语义不变）
- `none`：**不允许开口**（调度器永不返回它）

意愿（`rhythm_interval_s`）只决定**事件发生后多久回应**（响应延迟），不再决定"什么时候轮到她开口"。
"""

from __future__ import annotations

from collections.abc import Mapping

CAUSE_RESPONSE = "response"
CAUSE_PRESENCE = "presence"
CAUSE_DISCOVERY = "discovery"
CAUSE_THRESHOLD = "threshold"
CAUSE_WRITE = "write"
CAUSE_NONE = "none"

ALLOWED_CAUSES = (CAUSE_RESPONSE, CAUSE_PRESENCE, CAUSE_DISCOVERY, CAUSE_THRESHOLD, CAUSE_WRITE)

# 迟滞带（low, high）：value 从 high 以下跨上去、或从 low 以上跨下来，都算一次"状态跨越"。
# 这些是**触发带的宽度**，不是"每天说几条"的配额。
# 取值依据（2026-10-20 真机 40000 拍）：让她真实状态分布下偶尔跨一次，而不是几秒一次——
# 窄带会退化成新节拍器（实测窄带 11h 内跨 689 次；本组带子 11h 内 21 次、最长静默 6.9h）。
THRESHOLD_BANDS: dict[str, tuple[float, float]] = {
    "miss": (0.20, 0.60),
    "chat": (0.55, 0.85),
    "rest": (0.30, 0.70),
}


class SpeechTrigger:
    """把"离散事件/阈值跨越"翻译成开口决定；时间本身不再是触发条件。"""

    def __init__(self) -> None:
        self._prev: dict[str, float] = {}
        self._pending: str | None = None
        self._due: float = 0.0
        self._last_speak_ts: float | None = None

    # ── 内部 ─────────────────────────────────────────────
    def _crossed(self, feelings: Mapping[str, float]) -> bool:
        crossed = False
        for dim, (low, high) in THRESHOLD_BANDS.items():
            raw = feelings.get(dim)
            if raw is None:
                continue
            value = float(raw)
            previous = self._prev.get(dim)
            self._prev[dim] = value
            if previous is None:
                continue
            if (previous < high <= value) or (previous > low >= value):
                crossed = True
        return crossed

    def _prime(self, cause: str, now: float, delay_s: float) -> None:
        self._pending = cause
        self._due = float(now) + max(0.0, float(delay_s))

    # ── 对外 ─────────────────────────────────────────────
    @property
    def pending_cause(self) -> str | None:
        return self._pending

    def observe(
        self,
        feelings: Mapping[str, float],
        now: float,
        *,
        force: bool = False,
        greeting: bool = False,
        discovery: bool = False,
        wrote: bool = False,
        dialogue_active: bool = False,
        delay_s: float = 0.0,
    ) -> str | None:
        """每拍调用：返回"此刻该开口的原因"或 None（不开口）。

        - `force`（你对她说）/ `greeting`（你回来）⇒ 立即开口；
        - 对话进行中（`dialogue_active`）⇒ 内部冲动一律不说（并入 S1b 的闸）；
        - 否则只认**事件/跨越**：discovery > threshold > write；
        - 事件先挂起，等 `delay_s`（意愿决定的响应延迟）后再开口。
        """
        if force:
            self._pending = None
            self._last_speak_ts = float(now)
            return CAUSE_RESPONSE
        if greeting:
            self._pending = None
            self._last_speak_ts = float(now)
            return CAUSE_PRESENCE
        if dialogue_active:
            # 你正在跟她说话：她不需要再自己起话头（S1b 的闸）
            self._pending = None
            return None
        crossed = self._crossed(feelings)
        if self._pending is None:
            cause: str | None = None
            if discovery:
                cause = CAUSE_DISCOVERY
            elif crossed:
                cause = CAUSE_THRESHOLD
            elif wrote:
                cause = CAUSE_WRITE
            if cause is not None:
                self._prime(cause, now, delay_s)
        if self._pending is not None and float(now) >= self._due:
            cause = self._pending
            self._pending = None
            self._last_speak_ts = float(now)
            return cause
        return None

    def reset(self) -> None:
        self._prev.clear()
        self._pending = None
        self._due = 0.0
        self._last_speak_ts = None
