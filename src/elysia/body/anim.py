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
# 交叉淡入淡出的时长（秒）。
# **为什么需要**：素材越少，硬切的"啪"越明显。实测待机→点击回应是硬切时观感很跳；
# 在切换时把旧片段淡出、新片段淡入，即使只有两段动画，过渡也会顺很多。
# 用**秒**而不是"帧数"：按帧数会让淡出时长随渲染 tick 频率变化（脆弱）。
FADE_SECONDS = 0.3

# 单次片段播完后先垫一拍"归位站定"再回待机——这一拍就是**动作之间的过渡缓冲**
# （用户反馈："动作间没有关联和转换的缓冲空间"）。
# dsh-pet 靠"每段首尾都是中立姿势 + 专门的过渡动作"做到动作链，这里用同样思路：
# 动作 → 归位站定 → 待机。`settle-bridge` 是裁短的版本（~1.7 秒），没有就用整段 `settle`。
BRIDGE_CLIP = "settle-bridge"
BRIDGE_FALLBACK = "settle"
# 片段命名约定：这些前缀是"播一次就回待机"，其余默认循环
ONCE_CLIP_PREFIXES = (
    "notice",
    "settle",
    "land",
    "wave",
    "cheer",
    "shy",
    "surprise",
    "pout",
    "poke",
    "click",
)
# 优先当待机用的片段名（**睁眼待机排最前**：它占待机 90% 的时间）
IDLE_CLIP_PREFIXES = ("idle-open", "idle", "wait", "breathe")


@dataclass
class Clip:
    """一段动画（**按需解码 + 滑动窗口**，不一次性吃满内存）。

    ⚠️ 曾经的做法是加载时把整段 `webp/` 全部解码成 QPixmap 常驻。
    460x620 RGBA 每帧 1.14 MB，实测九段合计 **937.8 MB**——
    对一个桌面宠物完全不可接受（进程实测已涨到 356 MB）。
    现在只保留当前帧附近 `WINDOW` 帧，其余丢弃：
    内存从 ~938 MB 降到 ~60 MB，而且**加载片段变成瞬时**（不再有切换时的解码卡顿）。
    单帧 WebP 解码实测 5.47 ms，由 `prefetch()` 在渲染 tick 里提前做掉，
    不占用绘制时间（24fps 下每帧有 41.7 ms 预算，占 13%）。
    """

    costume: str
    clip_id: str
    fps: float
    paths: list[Path] = field(default_factory=list)
    suggest_ping_pong: bool = False
    _window: dict[int, QPixmap] = field(default_factory=dict, repr=False)

    # 解码窗口：保留当前帧附近这么多帧
    WINDOW = 16

    @property
    def frame_count(self) -> int:
        return len(self.paths)

    def frame(self, index: int) -> QPixmap | None:
        """取第 `index` 帧（按需解码；超出窗口的旧帧会被丢弃）。"""
        n = len(self.paths)
        if n == 0:
            return None
        index %= n
        cached = self._window.get(index)
        if cached is not None:
            return None if cached.isNull() else cached
        pixmap = QPixmap(str(self.paths[index]))
        if pixmap.isNull():
            return None
        self._window[index] = pixmap
        if len(self._window) > self.WINDOW:
            # 丢掉离当前帧最远的（环形距离）
            far = sorted(
                self._window,
                key=lambda k: min(abs(k - index), n - abs(k - index)),
                reverse=True,
            )
            for k in far[: len(self._window) - self.WINDOW]:
                self._window.pop(k, None)
        return pixmap

    def prefetch(self, index: int, ahead: int = 2) -> None:
        """在当前帧之前**提前解码**后续帧（在 tick 里做，别拖到绘制时）。"""
        n = len(self.paths)
        for k in range(ahead):
            self.frame((index + k) % n)

    def release(self) -> None:
        """释放解码窗口（非活动片段不该占着内存）。

        实测：每片段窗口 16 帧 × 1.6 MB（654x614）≈ **26 MB**；一套 8~9 个片段若全部驻留
        就是 **247 MB**（长跑 3000 帧 + 反复切换 20 轮都稳定 ⇒ 不是泄漏，是这个设计）。
        释放后切回会重新解码（单帧 5.47 ms，由 `prefetch` 摊到 tick 里）。
        """
        self._window.clear()

    @property
    def is_once(self) -> bool:
        """单次片段（播完回待机）还是循环片段。"""
        return self.clip_id.startswith(ONCE_CLIP_PREFIXES)

    @property
    def ping_pong(self) -> bool:
        """是否用**乒乓**（正放 + 倒放）播放。

        对"循环不干净"的循环类片段，乒乓**天然无缝**：实测 `被拎起悬空` 的
        首末剪影差 26.74（正常要求 < 3），正放到底再倒放回来就没有接缝。
        """
        return self.suggest_ping_pong and not self.is_once

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
        ping_pong = False
        manifest_path = clip_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                fps = float(data.get("fps", DEFAULT_FPS) or DEFAULT_FPS)
                ping_pong = bool(data.get("suggest_ping_pong", False))
            except (OSError, ValueError):
                pass  # manifest 坏了不影响播放，用默认帧率

        if not files:
            return None
        # **只记路径，不解码**：解码交给 `Clip.frame()` 按需做（滑动窗口）。
        # 顺带好处：加载片段是瞬时的，切换服装/片段不再有解码卡顿。
        return Clip(
            costume=costume,
            clip_id=clip_id,
            fps=fps,
            paths=list(files),
            suggest_ping_pong=ping_pong,
        )


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
        self._dir = 1  # 乒乓播放方向（+1 正放 / -1 倒放）
        # 交叉淡入淡出：切换片段时保留旧片段与剩余淡出**秒数**
        self._prev: Clip | None = None
        self._prev_index = 0
        self._fade_left_s = 0.0

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

    def has_clip(self, clip_id: str) -> bool:
        """这套服装里有没有这个片段（素材不全时用于**优雅降级**）。"""
        return bool(self._costume) and clip_id in self._library.clips(self._costume)

    def clip_ids(self) -> list[str]:
        return self._library.clips(self._costume) if self._costume else []

    def is_playing_once(self) -> bool:
        """当前是否正在播"单次"片段（点按回应等）——播完会自动回到待机。

        编排器用它**避免把演到一半的回应打断**。
        """
        return self._current is not None and self._current.is_once

    def is_static(self) -> bool:
        """当前画面是不是"只有一张图"（静态立绘服装包就是单帧 `idle`）。

        绘制层用它决定**要不要叠加物理呼吸/摇摆/浮动**：那些物理效果的本质是
        "给静态立绘注入生命"，只有一张图时才有意义；素材本身已是真实动画时，
        两层运动叠加会显得突兀（用户实测反馈）。
        """
        return self._current is None or self._current.frame_count <= 1

    def current(self) -> QPixmap | None:
        if self._current is None:
            return None
        return self._current.frame(self._index)

    def current_blend(self) -> tuple[QPixmap | None, QPixmap | None, float]:
        """返回 `(新帧, 旧帧, 新帧权重)`，供绘制层做交叉溶解。

        `alpha == 1.0`（或旧帧为 None）时直接画新帧即可；
        否则先把旧帧**全不透明**地画上、再以 `alpha` 画新帧
        （`SourceOver` 两次会得到 `新×α + 旧×(1-α)²`，少掉的 α(1-α) 会变成透明度）。
        """
        cur = self.current()
        if self._prev is None or self._fade_left_s <= 0:
            return cur, None, 1.0
        prev = self._prev.frame(self._prev_index)
        if prev is None:
            return cur, None, 1.0
        alpha = 1.0 - self._fade_left_s / FADE_SECONDS
        return cur, prev, min(1.0, max(0.0, alpha))

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
        # 淡出期间让旧片段继续走帧，过渡更"活"（而不是冻在某一帧上淡出）
        if self._fade_left_s > 0:
            self._fade_left_s = max(0.0, self._fade_left_s - dt_s)
            if self._prev is not None and self._prev.frame_count > 1:
                self._prev_index = (self._prev_index + 1) % self._prev.frame_count
            if self._fade_left_s <= 0:
                self._prev = None  # 淡出结束，旧片段不再需要
                self._trim_windows()
        while self._acc >= clip.frame_interval_s:
            self._acc -= clip.frame_interval_s
            moved = True
            if clip.ping_pong:
                # 乒乓：正放到尾再倒放回头，接缝天然消失（端点不重复播）
                nxt = self._index + self._dir
                if nxt >= clip.frame_count:
                    self._dir = -1
                    nxt = max(0, clip.frame_count - 2)
                elif nxt < 0:
                    self._dir = 1
                    nxt = min(1, clip.frame_count - 1)
                self._index = nxt
                continue
            self._index += 1
            if self._index >= clip.frame_count:
                if clip.is_once and self._fallback is not None:
                    # 单次片段播完 → 先垫一拍过渡，再回待机（不循环）。
                    # **这里必须走交叉溶解**：早先直接改 `_current` 绕过了 `_start_fade`，
                    # 于是"每次点击回应结束后回待机"都是**硬切**——而这是最常见的过渡，
                    # 用户反馈的"衔接不流畅"很大一部分来自这里。
                    nxt_clip = self._after_once(clip)
                    self._start_fade(nxt_clip)
                    self._current = nxt_clip
                    self._index = 0
                    self._acc = 0.0
                    self._dir = 1
                    break
                self._index %= clip.frame_count
        # **在 tick 里提前解码**（而不是拖到绘制时）：单帧 5.47ms，
        # 放在渲染 tick 的 16ms 预算里很宽松；放在 paintEvent 里就可能掉帧。
        if moved:
            target = self._current
            if target is not None:
                target.prefetch(self._index, 3)
            if self._prev is not None and self._fade_left_s > 0:
                self._prev.prefetch(self._prev_index, 2)
        return moved

    def set_clip(self, clip_id: str, *, fade: bool = True) -> bool:
        """切到某片段（`self._costume` 内）。

        `fade=False` 用于**突发事件**：落地就是一瞬间的事，拿 0.3 秒去溶解
        反而把冲击抹掉了（用户实测："松开鼠标却没有落地动作，反而像起跳"）。
        """
        if not self._costume:
            return False
        clip = self._get(self._costume, clip_id)
        if clip is None:
            return False
        if fade:
            self._start_fade(clip)
        else:
            self._prev = None
            self._fade_left_s = 0.0
        self._current = clip
        self._index = 0
        self._acc = 0.0
        self._dir = 1
        self._trim_windows()
        return True

    def play_once(self, clip_id_prefix: str, *, fade: bool = True) -> bool:
        """播放一个"单次"片段（按前缀匹配，如 `cheer`）。"""
        if not self._costume:
            return False
        for name in self._library.clips(self._costume):
            if name.startswith(clip_id_prefix):
                return self.set_clip(name, fade=fade)
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
        self._start_fade(fallback)
        self._current = fallback
        self._index = 0
        self._acc = 0.0
        self._dir = 1
        self._trim_windows()
        return True

    def _trim_windows(self) -> None:
        """只保留"当前"与"正在淡出"两个片段的解码窗口。

        内存 = 片段数 × 窗口帧数 × 单帧字节。共享画布让单帧涨到 1.6 MB（654x614），
        而片段数也从 4~5 增到 8~9 ⇒ 若全部驻留就是 **247 MB**（实测；长跑稳定、非泄漏）。
        这里把它们释放掉，切回时重新解码（单帧 5.47 ms，prefetch 会摊到 tick 里）。
        """
        keep = {id(c) for c in (self._current, self._prev) if c is not None}
        for clip in self._cache.values():
            if clip is not None and id(clip) not in keep:
                clip.release()

    def _after_once(self, finished: Clip) -> Clip:
        """单次片段播完后接什么。

        **过渡缓冲**：动作演完直接回待机是"硬收尾"；先垫一拍「归位站定」再回待机，
        就有了 dsh-pet 那种"动作链"的缓冲感。`settle*` 自身播完不再套一层（避免无限套娃）。
        """
        if self._fallback is None:  # pragma: no cover - 调用点已保证非 None
            return finished
        if finished.clip_id.startswith("settle"):
            return self._fallback
        for name in (BRIDGE_CLIP, BRIDGE_FALLBACK):
            if name == finished.clip_id:
                continue
            bridge = self._get(self._costume, name)
            if bridge is not None:
                return bridge
        return self._fallback

    # ── 内部 ──────────────────────────────────────────

    def _start_fade(self, incoming: Clip) -> None:
        """切片段时启动交叉淡入淡出（同一个片段则不动）。"""
        if self._current is not None and self._current is not incoming:
            self._prev = self._current
            self._prev_index = self._index
            self._fade_left_s = FADE_SECONDS

    def _get(self, costume: str, clip_id: str) -> Clip | None:
        key = (costume, clip_id)
        if key not in self._cache:
            self._cache[key] = self._library.load(costume, clip_id)
        return self._cache[key]
