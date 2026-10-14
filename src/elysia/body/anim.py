"""桌宠动画播放：读服装包里的 WebP 序列帧并按时播放。

**为什么是序列帧而不是视频**（2026-10-14 实测，结论见 `assets/pet/README.md`）：

- Qt 自己播 alpha WebM 会**丢透明通道**（`QVideoFrame.toImage()` → `Format_RGB32`，四角 alpha=255）
- PyAV 编出来的 `yuva420p` WebM 报 4 个平面，但 alpha **逐帧完全相同、透明比例只有 14%**（假 alpha）
- **WebP 序列**：Qt 原生读得到、alpha 正确、体积约 PNG 的 1/4.5

**本模块不碰灵魂**：只读素材目录与 `manifest.json`，**不打开也不写任何数据库**。
桌宠可以完全独立运行（`python -m elysia.body.pet`），灵魂在不在都不影响她动。

内存说明：一帧 372×555 的 ARGB32 约 0.8 MB，97 帧 ≈ 80 MB。
所以**按需加载 + 缓存**：待机片段启动时加载，点击回应片段首次点击才加载，不预载全部。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtGui import QPixmap

DEFAULT_FPS = 24.0
# 片段命名约定：这些前缀是"播一次就回待机"，其余默认循环
ONCE_CLIP_PREFIXES = ("cheer", "wave", "shy", "drag", "land", "poke", "surprise")
# 优先当待机用的片段名
IDLE_CLIP_PREFIXES = ("idle", "wait", "breathe")


@dataclass
class Clip:
    """一段动画（已解码为 QPixmap 序列）。"""

    costume: str
    clip_id: str
    fps: float
    frames: list[QPixmap] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def is_once(self) -> bool:
        """单次片段（播完回待机）还是循环片段。"""
        return self.clip_id.startswith(ONCE_CLIP_PREFIXES)

    @property
    def frame_interval_s(self) -> float:
        return 1.0 / self.fps if self.fps > 0 else 1.0 / DEFAULT_FPS


class ClipLibrary:
    """扫描 `costumes/<服装>/<片段>/{manifest.json, webp/}`。"""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def costumes(self) -> list[str]:
        """可用的服装包（只认含有片段目录的）。"""
        if not self._root.is_dir():
            return []
        return sorted(d.name for d in self._root.iterdir() if d.is_dir() and self._clip_dirs(d))

    def clips(self, costume: str) -> list[str]:
        d = self._root / costume
        return [p.name for p in self._clip_dirs(d)] if d.is_dir() else []

    def costume_name(self, costume: str) -> str:
        """服装的**显示名**（读 `costume.json` 的 `name`；缺失则退回目录名）。"""
        cfg = self._root / costume / "costume.json"
        if cfg.is_file():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                name = str(data.get("name", "")).strip()
                if name:
                    return name
            except (OSError, ValueError):
                pass
        return costume

    def _clip_dirs(self, costume_dir: Path) -> list[Path]:
        return sorted(d for d in costume_dir.iterdir() if d.is_dir() and (d / "webp").is_dir())

    def default_clip(self, costume: str) -> str:
        """挑待机片段：优先名字像待机的，其次第一个。"""
        clips = self.clips(costume)
        for pref in IDLE_CLIP_PREFIXES:
            for c in clips:
                if c.startswith(pref):
                    return c
        return clips[0] if clips else ""

    def load(self, costume: str, clip_id: str) -> Clip | None:
        """加载一段动画；素材缺失或全部解码失败返回 None（调用方退回静态立绘）。"""
        clip_dir = self._root / costume / clip_id
        webp_dir = clip_dir / "webp"
        if not webp_dir.is_dir():
            return None
        files = sorted(webp_dir.glob("*.webp"))
        if not files:
            return None

        fps = DEFAULT_FPS
        manifest_path = clip_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                fps = float(data.get("fps", DEFAULT_FPS) or DEFAULT_FPS)
            except (OSError, ValueError):
                pass  # manifest 坏了不影响播放，用默认帧率

        frames = [QPixmap(str(f)) for f in files]
        frames = [f for f in frames if not f.isNull()]
        if not frames:
            return None
        return Clip(costume=costume, clip_id=clip_id, fps=fps, frames=frames)


class ClipPlayer:
    """推进当前帧；单次片段播完自动回待机。"""

    def __init__(
        self,
        library: ClipLibrary,
        costume: str = "",
        clip_id: str = "",
    ) -> None:
        self._library = library
        self._cache: dict[tuple[str, str], Clip | None] = {}
        self._fallback: Clip | None = None
        self._current: Clip | None = None
        self._index = 0
        self._acc = 0.0

        costumes = library.costumes()
        self._costume = costume if costume in costumes else (costumes[0] if costumes else "")
        if not self._costume:
            return

        base = clip_id or library.default_clip(self._costume)
        self._fallback = self._get(self._costume, base) if base else None
        self._current = self._fallback

    # ── 查询 ──────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._current is not None

    @property
    def costume(self) -> str:
        return self._costume

    @property
    def library(self) -> ClipLibrary:
        """底层素材库（菜单要读服装显示名）。"""
        return self._library

    @property
    def costumes(self) -> list[str]:
        return self._library.costumes()

    def current_clip_id(self) -> str:
        return self._current.clip_id if self._current is not None else ""

    def current(self) -> QPixmap | None:
        if self._current is None or not self._current.frames:
            return None
        return self._current.frames[self._index % self._current.frame_count]

    def frame_size(self) -> tuple[int, int]:
        """当前片段单帧尺寸（供窗口定尺寸用）；没有则 (0, 0)。"""
        frame = self.current()
        return (frame.width(), frame.height()) if frame is not None else (0, 0)

    # ── 播放 ──────────────────────────────────────────

    def advance(self, dt_s: float) -> bool:
        """按时长推进；返回"画面是否变了"（变了才需要重绘）。"""
        clip = self._current
        if clip is None or clip.frame_count <= 1:
            return False
        self._acc += dt_s
        moved = False
        while self._acc >= clip.frame_interval_s:
            self._acc -= clip.frame_interval_s
            self._index += 1
            moved = True
            if self._index >= clip.frame_count:
                if clip.is_once and self._fallback is not None:
                    # 单次片段播完 → 回待机（不循环）
                    self._current = self._fallback
                    self._index = 0
                    self._acc = 0.0
                    break
                self._index %= clip.frame_count
        return moved

    def set_clip(self, clip_id: str) -> bool:
        """切到某片段（`self._costume` 内）。"""
        if not self._costume:
            return False
        clip = self._get(self._costume, clip_id)
        if clip is None:
            return False
        self._current = clip
        self._index = 0
        self._acc = 0.0
        return True

    def play_once(self, clip_id_prefix: str) -> bool:
        """播放一个"单次"片段（按前缀匹配，如 `cheer`）。"""
        if not self._costume:
            return False
        for name in self._library.clips(self._costume):
            if name.startswith(clip_id_prefix):
                return self.set_clip(name)
        return False

    def switch_costume(self, costume: str) -> bool:
        """换装：切到另一套服装，并回到那套的待机片段。"""
        if costume not in self._library.costumes():
            return False
        self._costume = costume
        base = self._library.default_clip(costume)
        fallback = self._get(costume, base) if base else None
        if fallback is None:
            return False
        self._fallback = fallback
        self._current = fallback
        self._index = 0
        self._acc = 0.0
        return True

    # ── 内部 ──────────────────────────────────────────

    def _get(self, costume: str, clip_id: str) -> Clip | None:
        key = (costume, clip_id)
        if key not in self._cache:
            self._cache[key] = self._library.load(costume, clip_id)
        return self._cache[key]
