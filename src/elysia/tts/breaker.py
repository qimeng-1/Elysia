"""TTS 资源熔断（P2 §7.2）。

VRAM 超过阈值或队列拥塞 → TTS 只输出文本，不阻塞对话。采样器可注入，
单测用固定数值驱动，不依赖真实 GPU（与 `body/resource.py` 的注入范式一致）。

**阈值的单位是百分比**（路线图 §7.6 写的"VRAM>85%"本来就是百分比）。
旧实现用绝对 MB（`10240`），而本机显存总量只有 6144 MiB ⇒ 熔断永不触发
（`STATUS_MATRIX.md` §12 #5）。百分比跨显卡可移植，也才是设计原本的写法。

不可知策略：采样器返回 `None`（无 GPU / nvidia-smi 不可用 / 命令失败）时
**不武断熔断**，放行合成——避免误伤低负载场景；熔断是保底而非常态。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from elysia.core.vram import vram_percent

log = logging.getLogger("elysia.tts.breaker")

# 当前显存占用**百分比**（0-100），None 表示不可知
VRAMSamplerCallable = Callable[[], int | None]


def default_vram_sampler() -> int | None:
    """读显存占用百分比；无 GPU / 命令失败 → `None`（不可知）。

    实现在 `core/vram.py`（nvidia-smi 的**单一出处**，身体进程的资源采样同源）。
    """
    return vram_percent()


class VramBreaker:
    """TTS 熔断闸：显存压力大时不合成，只出文本。"""

    def __init__(
        self,
        threshold_pct: float,
        sampler: VRAMSamplerCallable = default_vram_sampler,
    ) -> None:
        self._threshold_pct = threshold_pct
        self._sampler = sampler

    async def can_speak(self) -> bool:
        """是否允许出声合成。VRAM 未达阈值或不可知 → 允许。

        采样走 `asyncio.to_thread`（修 F4）：旧实现是同步 `subprocess.run`，
        在 async 链路里会阻塞事件循环最长 5 秒。本方法跑在表达 worker 内，
        但一样不该卡住循环。
        """
        used = await asyncio.to_thread(self._sampler)
        if used is None:
            return True
        return used < self._threshold_pct
