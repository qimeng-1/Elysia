"""状态快照 Schema v2：灵魂-身体唯一共用契约（P1 欲望系统加入）。

v2 新增字段：desire（TR/CS/SA）、feelings（6 维）、will（意志）、brain_action（行动）。
version 字段保证演进兼容：新增字段必须升版本，旧读者忽略未知键。
"""

from __future__ import annotations

from typing import Any

SNAPSHOT_VERSION = 2


def build_snapshot(
    *,
    timesense_summary: dict[str, Any],
    mode: str,
    distress: bool,
    desire: dict[str, float] | None = None,
    feelings: dict[str, float] | None = None,
    will: dict[str, Any] | None = None,
    brain_action: str | None = None,
) -> dict[str, Any]:
    """构建心跳状态快照。

    P1 新增：desire, feelings, will, brain_action。
    """
    payload: dict[str, Any] = {
        "version": SNAPSHOT_VERSION,
        "mode": mode,
        "distress": distress,
        "time": timesense_summary,
    }
    if desire is not None:
        payload["desire"] = desire
    if feelings is not None:
        payload["feelings"] = feelings
    if will is not None:
        payload["will"] = will
    if brain_action is not None:
        payload["brain_action"] = brain_action
    return payload
