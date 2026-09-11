"""精灵图动画：加载、帧分割、状态切换、帧步进。

集成到 pet.py 的 _SpriteCanvas 中使用，无精灵图时自动 fallback。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtGui import QPixmap


class SpriteAnimation:
    """精灵图动画加载与帧步进。

    布局约定：单行水平排列的 PNG 精灵图，每帧等宽等高。
    各状态帧连续排列，顺序由 frame_counts 的键顺序决定。

    使用方式：
        anim = SpriteAnimation(sprite_path, config)
        anim.set_state("happy")
        # 每帧调用
        frame = anim.step()  # 步进并返回当前帧
    """

    STATES: ClassVar[list[str]] = ["idle", "happy", "unhappy", "tired", "dragging", "away"]

    def __init__(
        self,
        sprite_path: Path,
        frame_width: int,
        frame_height: int,
        frame_counts: dict[str, int],
    ) -> None:
        self._frame_width = frame_width
        self._frame_height = frame_height
        self._frame_counts = frame_counts
        self._offsets: dict[str, int] = {}
        self._frames: dict[str, list[QPixmap]] = {}
        self._current_state = "idle"
        self._frame_index = 0
        self._valid = False

        if sprite_path.exists():
            self._load_sprite(sprite_path)

    def _load_sprite(self, sprite_path: Path) -> None:
        """加载精灵图，按状态分割为帧列表。"""
        sheet = QPixmap(str(sprite_path))
        if sheet.isNull():
            return

        # 计算各状态在精灵图中的起始偏移（帧索引）
        offset = 0
        for state in self.STATES:
            count = self._frame_counts.get(state, 0)
            self._offsets[state] = offset
            frames: list[QPixmap] = []
            for i in range(count):
                x = (offset + i) * self._frame_width
                frame = sheet.copy(x, 0, self._frame_width, self._frame_height)
                if not frame.isNull():
                    frames.append(frame)
            self._frames[state] = frames
            offset += count

        self._valid = True

    @property
    def valid(self) -> bool:
        """精灵图是否加载成功。"""
        return self._valid

    @property
    def frame_width(self) -> int:
        return self._frame_width

    @property
    def frame_height(self) -> int:
        return self._frame_height

    @property
    def current_state(self) -> str:
        return self._current_state

    @property
    def current_frame(self) -> QPixmap | None:
        """返回当前帧（不步进）。"""
        frames = self._frames.get(self._current_state)
        if not frames:
            return None
        idx = self._frame_index % len(frames)
        return frames[idx]

    def set_state(self, state: str) -> None:
        """切换动画状态，帧索引归零。"""
        if state not in self._frames or not self._frames[state]:
            state = "idle"
        if state != self._current_state:
            self._current_state = state
            self._frame_index = 0

    def step(self) -> QPixmap | None:
        """步进一帧，返回新帧。"""
        frames = self._frames.get(self._current_state)
        if not frames:
            return None
        self._frame_index = (self._frame_index + 1) % len(frames)
        return frames[self._frame_index]


def load_sprite_config(config_path: Path) -> dict[str, Any] | None:
    """加载 sprite.json 配置，失败返回 None。"""
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None
