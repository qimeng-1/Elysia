"""显存采样：nvidia-smi 的**单一出处**。

**为什么需要这个模块**（`STATUS_MATRIX.md` §12 #5）：
VRAM 曾经**根本没有进入她的状态**——`body/resource.py` 只采 CPU / 内存，
于是 `state_brief["vrram_mb"]` 恒为假值 `0`，而「累」与 TTS 熔断用的
三个阈值（8510 / 9510 / 10240）又**全部高于本机 6144 MiB 的显存总量**
⇒ 一律永不触发。这里把"问显卡"收成一处，并一律用**百分比**表达：
路线图 §7.6 写的本来就是"**VRAM>85%**"（百分比），当年被误写成了绝对 MB 数。

不可知策略（与 `tts/breaker.py` 一致）：无 GPU / nvidia-smi 缺失 / 超时 / 解析失败
一律返回 `None`——**宁可说"不知道"，也不编造一个 0**。
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger("elysia.core.vram")

# nvidia-smi 采样超时（秒）
VRAM_SAMPLE_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class VramSample:
    """一次显存采样。"""

    used_mb: int
    total_mb: int

    @property
    def percent(self) -> int:
        """占用百分比（0-100）；总量不可信时返回 0。"""
        if self.total_mb <= 0:
            return 0
        return round(self.used_mb * 100 / self.total_mb)


def sample_vram() -> VramSample | None:
    """用 nvidia-smi 读显存 used/total；不可知 → `None`。

    注意：本函数是**同步**的（`subprocess.run`）。调用方若在 async 链路里，
    请用 `asyncio.to_thread` 包住——否则会阻塞事件循环最长
    `VRAM_SAMPLE_TIMEOUT_S` 秒（这正是旧 `tts/breaker` 的 F4 破口）。
    """
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=VRAM_SAMPLE_TIMEOUT_S,
        )
        first = out.stdout.strip().splitlines()[0]
        used_s, total_s = (part.strip() for part in first.split(","))
        return VramSample(used_mb=int(used_s), total_mb=int(total_s))
    except Exception as exc:  # nvidia-smi 缺失 / 无 GPU / 超时 / 解析失败 → 不可知
        log.debug("VRAM 不可知：%s", exc)
        return None


def vram_percent() -> int | None:
    """显存占用百分比；不可知 → `None`（调用方自行决定放行还是保守）。"""
    sample = sample_vram()
    return None if sample is None else sample.percent
