"""资源感知：身体心跳的资源快照（真实 psutil 采样 / 测试注入）。

难受测试不真实压 CPU：测试注入采样器即可驱动 DistressMonitor。

**显存也在这里采**（`core/vram` = nvidia-smi 单一出处）：灵魂侧只读身体写下的缓存。
`STATUS_MATRIX.md` §12 #5 的根因之一就是"这里**不采 VRAM**"⇒
`state_brief["vrram_mb"]` 曾恒为假值 0。**采不到就不写这个键**（不编造 0）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psutil  # type: ignore[import-untyped]

from elysia.core.vram import sample_vram

ResourceSample = dict[str, float]
ResourceSamplerCallable = Callable[[], ResourceSample]


class ResourceSampler:
    """CPU / 内存 / 显存采样器：随身体心跳每 5s 一次。

    CPU 用 `interval=0`（非阻塞）；显存在无 GPU 时不产出对应键。
    """

    def __call__(self) -> ResourceSample:
        sample: ResourceSample = {
            "cpu_percent": psutil.cpu_percent(interval=0.0),
            "mem_percent": psutil.virtual_memory().percent,
        }
        vram = sample_vram()
        if vram is not None:
            sample["vrram_mb"] = float(vram.used_mb)
            sample["vram_pct"] = float(vram.percent)
        return sample


def injectable_sampler(sample: ResourceSample) -> ResourceSamplerCallable:
    """构造固定样本采样器（测试注入用）。"""
    return lambda: dict(sample)


def parse_resources(status: Any) -> ResourceSample:
    """从 body_status 提取资源快照（缺失时返回空）。"""
    if not isinstance(status, dict):
        return {}
    resources = status.get("resources")
    return resources if isinstance(resources, dict) else {}
