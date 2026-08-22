"""资源感知：身体心跳的资源快照（真实 psutil 采样 / 测试注入）。

难受测试不真实压 CPU：测试注入采样器即可驱动 DistressMonitor。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psutil  # type: ignore[import-untyped]

ResourceSample = dict[str, float]
ResourceSamplerCallable = Callable[[], ResourceSample]


class ResourceSampler:
    """CPU/内存采样器：非阻塞（interval=0），随身体心跳每 5s 一次。"""

    def __call__(self) -> ResourceSample:
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.0),
            "mem_percent": psutil.virtual_memory().percent,
        }


def injectable_sampler(sample: ResourceSample) -> ResourceSamplerCallable:
    """构造固定样本采样器（测试注入用）。"""
    return lambda: dict(sample)


def parse_resources(status: Any) -> ResourceSample:
    """从 body_status 提取资源快照（缺失时返回空）。"""
    if not isinstance(status, dict):
        return {}
    resources = status.get("resources")
    return resources if isinstance(resources, dict) else {}
