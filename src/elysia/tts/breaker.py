"""TTS 资源熔断（P2 §7.2）。

VRAM 超过阈值或队列拥塞 → TTS 只输出文本，不阻塞对话。VRAM 采样器可注入，
单测用固定数值驱动，不依赖真实 GPU（与 body/resource.py 的注入范式一致）。

不可知策略：采样器返回 None（无 GPU / nvidia-smi 不可用 / 命令失败）时
**不武断熔断**，放行合成——避免误伤低负载场景；熔断是保底而非常态。
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable

log = logging.getLogger("elysia.tts.breaker")

# 当前 VRAM 占用(MB)，None 表示不可知
VRAMSamplerCallable = Callable[[], int | None]


def default_vram_sampler() -> int | None:
    """用 nvidia-smi 读取显存占用(MB)。无 GPU/命令失败 → None（不可知）。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        head = out.stdout.strip().splitlines()[0].strip()
        return int(head)
    except Exception as exc:  # nvidia-smi 缺失/无 GPU/超时 → 不可知
        log.debug("nvidia-smi 不可用，VRAM 视为不可知：%s", exc)
        return None


class VramBreaker:
    """TTS 熔断闸：显存压力大时不合成，只出文本。"""

    def __init__(
        self,
        threshold_mb: int,
        sampler: VRAMSamplerCallable = default_vram_sampler,
    ) -> None:
        self._threshold_mb = threshold_mb
        self._sampler = sampler

    def can_speak(self) -> bool:
        """是否允许出声合成。VRAM 未达阈值或不可知 → 允许。"""
        used = self._sampler()
        if used is None:
            return True
        return used < self._threshold_mb
