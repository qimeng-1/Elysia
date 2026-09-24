"""表达异步化的数据模型（《运行时可靠性收口》20.6 的 1.1 步）。

本模块**只有类型**：不含调度逻辑、不接线（调度见 `expression_worker.py`，
接线见 `soul/heartbeat.py`）——因此引入它**零行为变化**。

背景（20.1 / 20.9）：心跳不能等外部服务。表达全链路（构造指令 → LLM → TTS）目前
同步跑在 1Hz 主循环里，LLM 慢 30s ⇒ 心跳停 30 拍、期间新交互全丢。异步化的做法是
把"耗时段"交给 worker，**心跳只负责提交任务与落库**。

职责切分（D2 裁决，2026-09-24）：
- **worker 只做耗时那段**：构造指令（`build_expression` + `enrich_permits` +
  记忆/身份注入）→ `await llm.speak` → `await tts.speak`
- **全部落库留在心跳**：`think` / `expression` / `add_memory` 仍由心跳写，因此结果
  必须把**取证所需的一切**带回来（`instruction` / `intent` / `text` / `level` /
  `validation` / `tts`）——这是 `ExpressionResult` 比 20.6 初稿多出两个字段的原因：
  `expression_log` 的取证行需要 `intent` 与 `instruction`（payload）。
- **单一事实源＝心跳**：「她说了什么」与「用户说了什么」在同一处落库。

`ExpressionJob` **持 `BrainOutput` 本体，不新造快照**（20.6 的 1.1「需改」）：少一层
就少一处漂移；约定 **worker 只读不改** `output`。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput
from elysia.tts.chain import TTSResult

# 结果状态（20.6 的 1.1）：四态语义见 `ExpressionResult`。
STATUS_SUCCESS = "success"
STATUS_SKIPPED = "skipped"
STATUS_TIMEOUT = "timeout"
STATUS_FAILED = "failed"

# 任务排序键：(心跳序号, 创建时刻)。偏序比较见 `is_fresh`。
JobOrder = tuple[int, float]


def make_job_id(heartbeat_seq: int, created_ts: float) -> str:
    """任务标识（20.6 的 1.4）：`{seq}-{created_ts}`。

    同一拍可能有多个 job，只带 `seq` 不足以区分 ⇒ 带创建时刻。它只作日志/测试的
    可读标签；判新旧一律用 `order_key`（比较数值，不解析字符串）。
    """
    return f"{heartbeat_seq}-{created_ts}"


def is_fresh(candidate: JobOrder, latest: JobOrder | None) -> bool:
    """偏序比较（20.6 的 1.4）：候选结果的 (seq, ts) 是否**不旧于**已接受的最近结果。

    `latest is None`（还没接受过任何结果）时任何候选都算新。同一拍可能有多个 job，
    因此**不能只比 seq**——必须 (seq, created_ts) 一起比。
    """
    return latest is None or candidate >= latest


@dataclass
class ExpressionJob:
    """一次待执行/执行中的表达任务（心跳 → worker 的输入）。

    心跳在提交时把 worker 需要的**全部输入**装进本对象（含 `vrram_mb` / `env`），
    使 worker **自包含**：它不必回读心跳内部状态，只读不改。
    """

    job_id: str
    created_ts: float
    heartbeat_seq: int
    output: BrainOutput  # 大脑输出本体（不新造快照）；worker 只读不改
    summary: Mapping[str, Any]  # TimeSense.summary()：body_away_s / day_phase 等
    vrram_mb: float = 0.0  # 心跳侧缓存的显存占用（build_expression 用）
    env: Mapping[str, object] | None = None  # 词汇许可环境（enrich_permits 用）
    user_message: str | None = None  # 本拍用户输入（作为"话题"，非指令）
    force: bool = False  # True = 用户输入驱动的强制开口（D1：永不丢）

    @property
    def order_key(self) -> JobOrder:
        """用于 `is_fresh` 的偏序键。"""
        return (self.heartbeat_seq, self.created_ts)


@dataclass
class ExpressionResult:
    """一次表达任务的产出（worker → 心跳的结果）。

    心跳 drain 到它之后**由自己落库**（D2）：`think` / `expression` / `add_memory`
    一律不改归属，故本对象要带回取证与写入所需的一切。

    状态语义：
    - `success`：正常产出（有文本；TTS 可能失败，但文本仍在）
    - `skipped`：无话可说（文本为空）⇒ 心跳无需落库
    - `timeout`：超过 `EXPRESS_TIMEOUT_S` 被取消；**若有已校验文本则气泡仍显示**
      （她说了，只是没出声）
    - `failed`：执行中抛异常（error 记原因）
    """

    job_id: str
    status: str
    order_key: JobOrder  # 偏序比较用，免解析 job_id
    text: str = ""
    level: str = ""  # "main" | "fallback" | "micro"
    validation: ValidationResult | None = None
    instruction: Mapping[str, Any] | None = None  # payload：expression_log 取证用
    intent: str = ""
    tts: TTSResult | None = None
    error: str | None = None
