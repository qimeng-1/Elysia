"""状态快照 Schema v0：灵魂-身体唯一共用契约（P0 决策 D1：状态库为通道）。

version 字段保证演进兼容：新增字段必须升版本，旧读者忽略未知键。
"""

from __future__ import annotations

from typing import Any

SNAPSHOT_VERSION = 1


def build_snapshot(
    *, timesense_summary: dict[str, Any], mode: str, distress: bool
) -> dict[str, Any]:
    """构建心跳状态快照（soul 每拍入档 heartbeat.db 的结构）。"""
    return {
        "version": SNAPSHOT_VERSION,
        "mode": mode,
        "distress": distress,
        "time": timesense_summary,
    }
