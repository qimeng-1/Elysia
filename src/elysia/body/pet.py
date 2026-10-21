"""最小桌宠 v0（连续物理动画版）：她怎么被你感知。

基于豆包桌宠的连续物理动画方案重构：
- 单张角色图 + 连续物理动画（呼吸/摇摆/浮动/弹跳），30fps 渲染
- 灵魂状态驱动动画参数（distress→sad, interaction→happy 等）
- 参数平滑 lerp 过渡，无顿挫切换
- 无角色图时自动降级为心跳光效
- 仅角色可见，所有功能通过右键菜单访问
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QTransform,
    QWheelEvent,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QWidget,
)

# ── 路径 ──────────────────────────────────────────────
from elysia.body.anim import ClipLibrary, ClipPlayer

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # elysia/
_CHAR_PATH = _PROJECT_ROOT / "assets" / "pet" / "character.png"
# 服装包目录（不入库；由 scripts/asset_pipeline.py 生成，见 assets/pet/README.md）
_COSTUMES_DIR = _PROJECT_ROOT / "assets" / "pet" / "costumes"

# ── 常量 ──────────────────────────────────────────────
POLL_INTERVAL_MS = 200  # 5Hz 灵魂状态轮询
ANIM_INTERVAL_MS = 16  # ~60fps 动画渲染
# 物理参数（lerp 系数、弹跳衰减）原本是按"每 1/30 秒一跳"标定的。
# 提高渲染帧率时必须按比例换算，否则呼吸/摇摆/弹跳会跟着**变快一倍**。
ANIM_REFERENCE_DT_S = 1.0 / 30.0
CLICK_HAPPY_S = 2.0  # 点击后 happy 覆盖持续时长
BUBBLE_SHOW_S = 6.0  # 表达气泡显示时长
ZOOM_MIN = 0.3
ZOOM_MAX = 3.0
ZOOM_STEP = 1.1
WINDOW_PADDING = 30  # 窗口边距（防浮动裁剪）
CLICK_DRAG_THRESHOLD_PX = 5  # 鼠标移动量超过此值视为拖拽而非点击
# 松手落地时的压缩力度（比点击轻：落地是"顿一下"，点击是"Q 弹"）
LAND_STRENGTH = 0.10
# **拖拽状态片段**：不是"动作"，而是按住拖动期间的物理反应
# （对应 dsh-pet 的 `animations.drag` 池——它的 drag 池也是"被无形抓起悬空"的姿势）。
# 按住时一直播、松手就结束；`land` 在松手瞬间播一次作为落地缓冲。
DRAG_CLIP = "grab"
# 拖拽状态片段候选：**优先 `float`（漂浮）**，兼容旧的 `grab`（被拎起悬空）。
# 用户要求把它换成"漂浮起来"——生成新素材放进 `float/` 即自动生效。
DRAG_CLIP_CANDIDATES = ("float", "grab")
LAND_CLIP = "land"
# 鼠标移入时的"预备"片段：抬头注意到你（动作之前的过渡）
NOTICE_CLIP = "notice"

# ── 漫游（对应 dsh-pet 的 `moves` 槽位）──────────────────────────────
# 每隔一段时间，她在桌面上小跑一段。**独立于灵魂状态**：纯桌宠模式下也会漫游。
WANDER_CLIP_CANDIDATES = ("run", "walk")
WANDER_TICK_MS = 33  # 位移刷新 ~30fps（灵魂轮询是 5Hz，用来移动窗口会一顿一顿）
WANDER_MIN_S = 70.0  # 两次漫游的最小间隔
WANDER_MAX_S = 200.0
WANDER_MIN_PX = 90  # 单次移动距离
WANDER_MAX_PX = 260
WANDER_SPEED_PX_S = 76.0  # 走动位移速度。**实测校准值**，别凭感觉改：
# 走动动画是"原地"播的，着地脚相对画面向后移动，其速度就等于她真走时该前进的速度。
# `scripts/measure_walk_speed.py` 逐帧取"包围盒最下面几行"的 alpha 加权 x 质心作为接触点，
# 相邻帧求速、剔除换脚跳变后取稳健值：6 套实测 72~86 px/s（中位 75.5）。
# 原先拍的 110 让她**快了约 46%** ⇒ 明显滑步（用户抱怨过"生硬"，滑步是其中一环）。
# `notice` 的冷却：鼠标在桌面上来回移动会**反复进出**窗口，
# 没有冷却就会不停重播"注意到你"、把待机轮换和整体节奏全打乱（用户实测反馈）。
NOTICE_COOLDOWN_S = 25.0
# 拖拽时的"漂浮"手感：被按住时轻轻上下浮沉并轻微摆动。
# 用户要求："拎起来的视频可以替换为漂浮起来"——素材替换前，先用这一层把观感从
# "被无形的手拎着"改成"浮在空中"。
DRAG_FLOAT_AMP_PX = 7.0
DRAG_FLOAT_HZ = 0.55
DRAG_SWAY_DEG = 2.2

# ── 动画编排（借鉴 dsh-pet 的 animations / animationWeights 设计）──────────
# 待机池**带权重**（对应 dsh-pet 的 `animationWeights`）。
# 权重不是平摊的：`idle-open` 是绝对主力——睁眼待机占待机绝大部分时间；
# 若平摊，闭眼的 `idle` 会占 1/5，把"睁眼"的改进抵消掉。
IDLE_WEIGHTS: dict[str, int] = {
    "idle-open": 8,  # 睁眼待机呼吸：主力（豆包素材）
    "idle": 4,  # 主待机（Spine 素材没有 idle-open，就用它）
    "idle-hunmi": 1,  # 昏睡待机（仅 elvisa_2 那套服装有）
    "idle-cry": 1,  # 哭泣待机（仅 elvisa_6 那套服装有）
    "look-around": 2,  # 东张西望
    "think": 2,  # 发呆思考
    "settle": 1,  # 归位站定
}
# 待机轮换的间隔区间（秒）；有随机性才不会像节拍器
IDLE_SWITCH_MIN_S = 18.0
IDLE_SWITCH_MAX_S = 45.0
# 点按时的回应池（随机抽一个）
CLICK_POOL = ("cheer", "wave", "shy", "surprise", "pout")
# **气息状态 → 片段池**。这一层与 dsh-pet 的根本区别：它接的是 DSH 的工作状态/余额，
# 我们接的是**她自己的灵魂状态**（`_map_state_to_animation` 的输出）。
# 素材不全时自动降级：池里找不到的片段会被跳过，全都没有就保持现状。
STATE_CLIPS: dict[str, tuple[str, ...]] = {
    "away": ("sleep", "miss", "idle-open"),
    "unhappy": ("sad", "tired", "idle-open"),
    "tired": ("tired", "settle", "idle-open"),
    "happy": ("cheer", "wave", "idle-open"),
    "idle": tuple(IDLE_WEIGHTS),
}

# ── 情绪参数预设（由灵魂状态映射）────────────────────
# breath_speed: 呼吸频率  |  sway_speed: 摇摆频率
# sway_amp: 摇摆幅度(度)  |  base_scale: 基础缩放
# float_amp: 上下浮动幅度(px)
EMOTION_PARAMS = {
    "idle": {
        "breath_speed": 1.1,
        "sway_speed": 0.55,
        "sway_amp": 1.4,
        "base_scale": 1.00,
        "float_amp": 6,
    },
    "happy": {
        "breath_speed": 2.0,
        "sway_speed": 1.10,
        "sway_amp": 3.0,
        "base_scale": 1.02,
        "float_amp": 10,
    },
    "unhappy": {
        "breath_speed": 0.55,
        "sway_speed": 0.28,
        "sway_amp": 0.7,
        "base_scale": 0.96,
        "float_amp": 3,
    },
    "tired": {
        "breath_speed": 0.38,
        "sway_speed": 0.18,
        "sway_amp": 0.4,
        "base_scale": 0.94,
        "float_amp": 1,
    },
    "away": {
        "breath_speed": 0.45,
        "sway_speed": 0.22,
        "sway_amp": 0.5,
        "base_scale": 0.95,
        "float_amp": 2,
    },
    "dragging": None,  # 拖拽时冻结动画
}

POKE_STRENGTH = 0.09


class _CharacterWidget(QWidget):
    """连续物理动画角色：呼吸缩放 + 左右摇摆 + 上下浮动 + 点击弹跳。

    有角色图 → 连续物理动画；无角色图 → fallback 心跳光效。
    """

    def __init__(
        self,
        parent: QWidget,
        pixmap: QPixmap,
        player: ClipPlayer | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._pixmap = pixmap
        # 序列帧播放器（P4b）：有它就用素材动画，没有就退回单张立绘 + 物理动画
        self._player = player
        self._valid = not pixmap.isNull() or (player is not None and player.available)
        self._scale = 1.0

        # 当前动画参数
        self._cur = {
            "breath_speed": 1.1,
            "sway_speed": 0.55,
            "sway_amp": 1.4,
            "base_scale": 1.0,
            "float_amp": 6,
        }
        self._target = dict(self._cur)
        self._phase_breath = 0.0
        self._phase_sway = 0.0
        self._float_phase = 0.0
        self._bounce = 0.0
        self._emotion = "idle"
        self._dragging = False
        self._last_tick_s = 0.0  # 上一次动画 tick 的墙钟时刻（算真实 dt）
        # 物理待机层的**主增益**：静态立绘要它（1.0），真动画不要它（0.0）。
        # 用增益而不是删代码，并且跟着 lerp 平滑过渡——切换时不会"跳"一下。
        self._idle_gain = 1.0
        # 拖拽"漂浮"层：被按住时浮沉，松手淡出
        self._drag_float_gain = 0.0
        self._drag_phase = 0.0
        # 朝向：+1 朝右（素材原本方向）、-1 朝左（水平镜像）
        self._facing = 1

        # fallback 状态变量
        self._beat_phase = 0.0
        self._mode = "present"
        self._distress = False

        # 动画定时器（~60fps）。
        # **必须用 PreciseTimer**：Qt 默认是 CoarseTimer，实测有 ±5% 的间隔偏差。
        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._anim_timer.timeout.connect(self._anim_tick)
        self._anim_timer.start(ANIM_INTERVAL_MS)
        self._last_tick_s = time.perf_counter()

        if self._valid:
            self.setMinimumSize(200, 200)
        else:
            self.setMinimumHeight(120)

    # ── 公共接口 ──────────────────────────────────────

    def set_facing(self, facing: int) -> None:
        """设置朝向（+1 朝右 / -1 朝左）。只影响绘制，不需要额外素材。"""
        self._facing = -1 if facing < 0 else 1
        self.update()

    def set_emotion(self, key: str) -> None:
        if key == "dragging":
            self._dragging = True
            return
        self._dragging = False
        self._emotion = key
        params = EMOTION_PARAMS.get(key, EMOTION_PARAMS["idle"])
        if params is not None:
            self._target.update(params)

    def poke(self, strength: float = POKE_STRENGTH) -> None:
        self._bounce = max(self._bounce, strength)

    @property
    def current_emotion(self) -> str:
        return self._emotion

    def zoom_in(self) -> None:
        self._scale = min(self._scale * ZOOM_STEP, ZOOM_MAX)
        self.update()

    def zoom_out(self) -> None:
        self._scale = max(self._scale / ZOOM_STEP, ZOOM_MIN)
        self.update()

    def update_state(
        self,
        anim_state: str = "idle",
        distress: bool = False,
        desire: dict[str, float] | None = None,
    ) -> None:
        """每帧更新：灵魂状态 → 动画参数 + fallback 变量。

        P1 新增：desire（TR/CS/SA）调制动画参数。
        """
        self._distress = distress
        self._beat_phase += 0.3

        if self._valid:
            self.set_emotion(anim_state)
            # P1：TR/CS/SA 调制目标参数
            if desire is not None:
                self._apply_desire_modulation(desire)

    def _apply_desire_modulation(self, desire: dict[str, float]) -> None:
        """TR/CS/SA 调制动画参数。

        TR 高 → 呼吸加快，摇摆幅度增大
        CS 高 → 浮动更活跃
        SA 高 → 基础缩放缩小，浮动幅度减小
        """
        tr = desire.get("tr", 45)
        cs = desire.get("cs", 60)
        sa = desire.get("sa", 20)

        tr_norm = (tr - 30) / 40  # 30-70 → 0-1
        cs_norm = (cs - 40) / 40  # 40-80 → 0-1
        sa_norm = (sa - 15) / 45  # 15-60 → 0-1

        # TR 调制
        self._target["breath_speed"] *= 1.0 + 0.3 * max(0, min(1, tr_norm))
        self._target["sway_amp"] *= 1.0 + 0.4 * max(0, min(1, tr_norm))

        # CS 调制
        self._target["float_amp"] *= 1.0 + 0.5 * max(0, min(1, cs_norm))

        # SA 调制
        self._target["base_scale"] *= 1.0 - 0.04 * max(0, min(1, sa_norm))
        self._target["float_amp"] *= 1.0 - 0.3 * max(0, min(1, sa_norm))

    # ── 内部动画 ──────────────────────────────────────

    def _anim_tick(self) -> None:
        # **用真实流逝时间**，而不是固定间隔。
        # 固定 dt 会让定时器的偏差直接变成播放速度误差（实测 CoarseTimer 偏差 5%
        # ⇒ 动画快 5%）。dsh-pet 用 `<video>`，时间基准是墙钟，所以永不越播越偏。
        now = time.perf_counter()
        dt = now - self._last_tick_s
        self._last_tick_s = now
        if dt <= 0.0 or dt > 0.5:  # 首帧 / 长时间挂起后回归：夹一下，别一次性跳一大段
            dt = ANIM_INTERVAL_MS / 1000.0
        # 物理参数按"每 1/30 秒"标定，这里按真实 dt 换算
        dt_ratio = dt / ANIM_REFERENCE_DT_S
        k = 1.0 - (1.0 - 0.06) ** dt_ratio

        # 序列帧推进：**与物理动画叠加**——素材给"她在演什么"，物理给"她怎么晃"。
        # 放在最前，且不受 dragging 分支影响：拖拽时素材也在走（手感更连续）。
        if self._player is not None:
            self._player.advance(dt)

        # 拖拽"漂浮"层：按住时增益升到 1、松手淡回 0；相位只在增益非零时推进。
        # 这是**输入反应**，不受 `_idle_gain`（那个只管待机晃动）影响。
        drag_target = 1.0 if self._dragging else 0.0
        self._drag_float_gain += (drag_target - self._drag_float_gain) * k
        if self._drag_float_gain > 0.002:
            self._drag_phase += 2.0 * math.pi * DRAG_FLOAT_HZ * dt

        if not self._valid:
            self.update()
            return

        if self._dragging:
            self._bounce *= 0.88**dt_ratio
            if self._bounce < 0.0005:
                self._bounce = 0.0
            self.update()
            return

        # 物理待机层增益：素材本身有真实动画 ⇒ 目标 0（不叠加晃动）；
        # 只有一张静态立绘 ⇒ 目标 1（物理层就是让静止画面活起来的东西）。
        idle_gain_target = 1.0 if self._idle_physics_wanted() else 0.0
        self._idle_gain += (idle_gain_target - self._idle_gain) * k

        for key in self._cur:
            self._cur[key] += (self._target[key] - self._cur[key]) * k

        # 相位累加
        self._phase_breath += self._cur["breath_speed"] * dt
        self._phase_sway += self._cur["sway_speed"] * dt
        self._float_phase += 0.7 * dt

        # 弹跳衰减
        self._bounce *= 0.88**dt_ratio
        if self._bounce < 0.0005:
            self._bounce = 0.0

        self.update()

    # ── 绘制 ──────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 清除背景残留（CompositionMode_Clear 将像素设为全透明）
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        if self._valid:
            self._paint_character(painter)
        else:
            self._paint_fallback(painter)

        painter.end()

    def _paint_character(self, painter: QPainter) -> None:
        """绘制角色（序列帧或单张立绘）+ 连续物理动画（呼吸缩放 + 摇摆 + 浮动 + 弹跳）。"""
        cw, ch = self.width(), self.height()

        scale = (
            self._cur["base_scale"]
            + 0.022 * self._idle_gain * math.sin(self._phase_breath)
            + self._bounce
        )
        scale *= self._scale
        rot = self._cur["sway_amp"] * self._idle_gain * math.sin(self._phase_sway)
        float_y = self._cur["float_amp"] * self._idle_gain * math.sin(self._float_phase)
        # 拖拽"漂浮"：被按住时轻轻上下浮沉 + 轻微摆动。
        # 让观感从"被无形的手拎着"变成"浮在空中"（用户要求把拎起换成漂浮）。
        drag_g = self._drag_float_gain
        if drag_g > 0.002:
            float_y += DRAG_FLOAT_AMP_PX * drag_g * math.sin(self._drag_phase)
            rot += DRAG_SWAY_DEG * drag_g * math.sin(self._drag_phase * 0.63)

        frame, prev, alpha = (
            self._player.current_blend() if self._player is not None else (None, None, 1.0)
        )
        pixmap = frame if frame is not None else self._pixmap
        if pixmap.isNull():
            return
        pw, ph = pixmap.width(), pixmap.height()

        t = QTransform()
        t.translate(cw / 2, ch / 2 + float_y)
        t.rotate(rot)
        # `_facing` 为 -1 时水平镜像（漫游向左时用）——她只有朝右的素材，靠镜像出朝左
        t.scale(scale * self._facing, scale)
        t.translate(-pw / 2, -ph / 2)

        painter.setTransform(t)
        # 交叉溶解：**旧帧必须全不透明地画**，再让新帧以 alpha 压上去。
        #
        # ⚠️ 这里踩过一个坑：最初写成「旧帧按 1-alpha 画、新帧按 alpha 画」，
        # 但两次 `SourceOver` 叠出来的实际是 `新×α + 旧×(1-α)²`——
        # 少的 `α(1-α)` 变成了**透明度**。实测 α=0.556 时她**整个人半透明**
        # （不透明像素占比 0.0%、平均 alpha 92），观感是"闪一下"，
        # 比硬切还难受（用户反馈"淡入淡出不起作用"就是这个原因）。
        #
        # 改成「旧帧 1.0 + 新帧 alpha」后：α=0.556 时不透明占比 46.8%、平均 alpha 123，
        # 且 α≥1 时**根本不画旧帧**（下面的判断），所以不会有残影
        # （实测与纯新帧像素差异 0.00%）。
        if prev is not None and not prev.isNull() and alpha < 1.0:
            painter.setOpacity(1.0)
            if prev.size() == pixmap.size():
                painter.drawPixmap(0, 0, prev)
            else:  # 尺寸不同（例如换服装）：按中心对齐叠画
                painter.drawPixmap((pw - prev.width()) // 2, (ph - prev.height()) // 2, prev)
        painter.setOpacity(alpha)
        painter.drawPixmap(0, 0, pixmap)
        painter.setOpacity(1.0)

    def _idle_physics_wanted(self) -> bool:
        """还要不要叠加物理呼吸/摇摆/浮动。

        **判据：画面是不是"只有一张图"。**
        这层物理效果原本是给静态立绘注入生命的——只有一张图时它让画面活起来；
        素材本身已经有真实的呼吸与身体运动时，两层运动叠加会显得突兀
        （用户实测："呼吸晃动特别突兀"）。所以：真动画 ⇒ 关掉，静态立绘 ⇒ 保留。
        """
        if self._player is None:
            return True  # 没有播放器（退回单张立绘）⇒ 需要物理层
        return self._player.is_static()

    def _paint_fallback(self, painter: QPainter) -> None:
        """无角色图时的降级心跳光效。"""
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        base_r = 30
        pulse = math.sin(self._beat_phase) * 8
        r = base_r + pulse
        color = QColor(255, 60, 60, 200) if self._distress else QColor(120, 255, 180, 200)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))


class PetWindow(QMainWindow):
    """无边框透明置顶桌宠窗口（仅角色可见，右键菜单控制）。"""

    def __init__(
        self,
        state_db_path: Path,
        heartbeat_db_path: Path,
        *,
        standalone: bool = False,
    ) -> None:
        super().__init__()
        # standalone（"纯桌宠"模式）：**不打开任何数据库、不写任何交互记录**。
        # 用于"只想看看桌宠长什么样"的场景——灵魂在不在都不影响她动。
        self._standalone = standalone
        self._state_db_path = state_db_path
        self._heartbeat_db_path = heartbeat_db_path
        self._conn_state: sqlite3.Connection | None = None
        self._conn_heartbeat: sqlite3.Connection | None = None
        self._last_thought_id = 0
        self._last_thought_text = ""
        self._dragging = False
        self._moved = False
        self._drag_offset = QPoint()
        self._drag_global_pos = QPoint()
        self._happy_until = 0.0
        self._last_soul_payload: dict[str, Any] | None = None
        # 动画编排状态（待机轮换 + 状态驱动 + 拖拽状态片段）
        self._idle_next_switch = 0.0
        self._last_state_clip = ""
        self._drag_clip_active = False
        self._notice_last_s = 0.0  # 上次播「注意到你」的时刻（冷却用）
        # 漫游状态
        self._wander_next_s = 0.0
        self._wander_remaining_px = 0.0
        self._wander_dir = 1

        # ── 角色素材：优先序列帧（服装包），其次旧单张立绘 ──
        pixmap = QPixmap(str(_CHAR_PATH))
        library = ClipLibrary(_COSTUMES_DIR)
        self._clip_player: ClipPlayer | None = ClipPlayer(library) if library.costumes() else None
        self._has_character = not pixmap.isNull() or (
            self._clip_player is not None and self._clip_player.available
        )

        # ── 窗口设置 ──
        self.setWindowTitle("Elysia")
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # 窗口尺寸按**素材实际尺寸**定：优先当前动画帧，其次旧立绘
        frame_w, frame_h = (0, 0)
        if self._clip_player is not None:
            frame_w, frame_h = self._clip_player.frame_size()
        if frame_w and frame_h:
            pw = frame_w + WINDOW_PADDING * 2
            ph = frame_h + WINDOW_PADDING * 2
        elif self._has_character:
            pw = pixmap.width() + WINDOW_PADDING * 2
            ph = pixmap.height() + WINDOW_PADDING * 2
        else:
            pw, ph = 260, 260
        self.setFixedSize(pw, ph)

        # ── 角色画布 ──
        self._canvas = _CharacterWidget(self, pixmap, self._clip_player)
        self.setCentralWidget(self._canvas)

        # ── 表达气泡（P2：LLM 开口的文本显示） ──
        self._bubble = QLabel(self._canvas)
        self._bubble.setWordWrap(True)
        self._bubble.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._bubble.setStyleSheet(
            "QLabel {"
            "  background: rgba(255, 255, 255, 235);"
            "  color: #333;"
            "  border: 1px solid #ddd;"
            "  border-radius: 8px;"
            "  padding: 6px 8px;"
            "  font-size: 13px;"
            "}"
        )
        self._bubble.setFixedWidth(int(pw * 0.5))
        self._bubble.hide()
        self._bubble_until = 0.0

        # ── 音频播放（P2 TTS 出声：播放最新缓存 wav） ──
        # 懒初始化：QMediaPlayer 创建时 FFmpeg 会探测系统 MFT 编解码器（H264/HEVC），
        # HEVC 缺失会打红色 stderr 警告；仅播 wav 用不到视频编码器，故首次出声才创建。
        self._player: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None
        self._last_played_audio = ""  # 去重：同一 wav 不重复播放

        # ── 定时器 ──
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_INTERVAL_MS)

        # 漫游定时器：**独立于灵魂轮询**——5Hz 用来移动窗口会一顿一顿，
        # 而且纯桌宠模式（standalone）下 `_tick` 直接 return，就更不该挂在它上面。
        self._wander_timer = QTimer(self)
        self._wander_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._wander_timer.timeout.connect(self._wander_tick)
        self._wander_timer.start(WANDER_TICK_MS)
        self._wander_next_s = time.time() + random.uniform(WANDER_MIN_S, WANDER_MAX_S)

        # ── 初始位置：屏幕右下角 ──
        ag = QGuiApplication.primaryScreen().availableGeometry()
        self.move(ag.right() - pw - 20, ag.bottom() - ph - 20)

    # ── 数据库连接 ──────────────────────────────────

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn_state is None:
            self._conn_state = sqlite3.connect(str(self._state_db_path))
            self._conn_state.execute("PRAGMA busy_timeout=5000")
        return self._conn_state

    @property
    def conn_hb(self) -> sqlite3.Connection:
        if self._conn_heartbeat is None:
            self._conn_heartbeat = sqlite3.connect(str(self._heartbeat_db_path))
            self._conn_heartbeat.execute("PRAGMA busy_timeout=5000")
        return self._conn_heartbeat

    def closeEvent(self, event: Any) -> None:
        self._timer.stop()
        for c in (self._conn_state, self._conn_heartbeat):
            if c is not None:
                c.close()
        super().closeEvent(event)

    # ── 鼠标事件（豆包方案：即时拖拽，移动量判断点击） ────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._moved = False
            self._drag_offset = event.position().toPoint()
            self._drag_global_pos = event.globalPosition().toPoint()
            self._canvas.set_emotion("dragging")
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._drag_global_pos
            if delta.manhattanLength() > CLICK_DRAG_THRESHOLD_PX:
                if not self._moved:
                    # 移动量越过阈值 ⇒ 判定为真拖拽，切到"被拎起悬空"
                    self._begin_drag_clip()
                self._moved = True
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._canvas.set_emotion("idle")
            if self._moved:
                self._end_drag_clip()
            else:
                self._on_character_click()
            event.accept()

    def enterEvent(self, event: Any) -> None:
        """鼠标移入窗口 → 她「注意到你」（抬头看过来）。

        `notice` 这条素材的正确用途就是这个：**动作之前的预备**。
        先抬头看你、再回应点击，比"待机→回应"的直切自然得多。

        打断规则：待机池里的片段（含 `settle` 这类过渡）**可以**被打断——
        你走过来了她就该抬头；但**点按回应演到一半不打断**（那是她对你的回答）。
        正在被拖拽时也不打断。
        """
        player = self._clip_player
        recently = time.time() - self._notice_last_s < NOTICE_COOLDOWN_S
        if player is not None and not self._drag_clip_active and not recently:
            interrupting_response = player.is_playing_once() and (
                player.current_clip_id() not in IDLE_WEIGHTS
            )
            if not interrupting_response:
                player.play_once(NOTICE_CLIP)
                self._notice_last_s = time.time()
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.angleDelta().y() > 0:
            self._canvas.zoom_in()
        else:
            self._canvas.zoom_out()
        event.accept()

    def contextMenuEvent(self, event: Any) -> None:
        self._build_menu().exec(event.globalPos())

    # ── 右键菜单 ──────────────────────────────────────

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.setStyleSheet("QMenu{font-size:13px;}")

        # 输入…
        input_act = QAction("输入…", self)
        input_act.triggered.connect(self._open_input_dialog)
        menu.addAction(input_act)

        # 状态
        status_act = QAction("状态", self)
        status_act.triggered.connect(self._show_status)
        menu.addAction(status_act)

        menu.addSeparator()

        # 换装（P4b）：一套服装一个菜单项；只有真有多套时才出现
        if self._clip_player is not None:
            costumes = self._clip_player.costumes
            if len(costumes) > 1:
                wear = menu.addMenu("换装")
                lib = self._clip_player.library
                for cid in costumes:
                    act = QAction(lib.costume_name(cid), self)
                    act.setCheckable(True)
                    act.setChecked(cid == self._clip_player.costume)
                    act.triggered.connect(lambda _, c=cid: self._switch_costume(c))
                    wear.addAction(act)

        # 窗口置顶
        top_act = QAction("窗口置顶", self)
        top_act.setCheckable(True)
        top_act.setChecked(bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        top_act.triggered.connect(self._toggle_topmost)
        menu.addAction(top_act)

        # 透明度
        opm = menu.addMenu("透明度")
        for v in (60, 80, 100):
            act = QAction(f"{v}%", self)
            act.triggered.connect(lambda _, x=v: self._set_opacity(x))
            opm.addAction(act)

        menu.addSeparator()

        # 退出
        quit_act = QAction("退出", self)
        quit_act.triggered.connect(self._quit)
        menu.addAction(quit_act)

        return menu

    def _switch_costume(self, costume: str) -> None:
        """换装：切到另一套服装，并把窗口重新适配新素材的尺寸。

        位置策略：**保持"脚底"与水平中心不动**——各套立绘尺寸不同（263x354 ~ 380x566），
        若只 `setFixedSize` 会以左上角为锚点，看起来像"她跳了一下"。
        """
        if self._clip_player is None or not self._clip_player.switch_costume(costume):
            return
        fw, fh = self._clip_player.frame_size()
        if fw and fh:
            pw, ph = fw + WINDOW_PADDING * 2, fh + WINDOW_PADDING * 2
            geo = self.geometry()
            self.setFixedSize(pw, ph)
            self.move(geo.center().x() - pw // 2, geo.bottom() - ph)
            self._bubble.setFixedWidth(int(pw * 0.5))
        self._canvas.update()

    def _quit(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _toggle_topmost(self, checked: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        self.show()

    def _set_opacity(self, v: int) -> None:
        self.setWindowOpacity(v / 100.0)

    def _open_input_dialog(self) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "输入", "说点什么…")
        if ok and text.strip():
            self._submit_interaction(text.strip())

    def _show_status(self) -> None:
        p = self._last_soul_payload
        if not p:
            QMessageBox.information(self, "状态", "等待心跳连接…")
            return

        time_data = p.get("time", {})
        desire_data = p.get("desire")
        feelings_data = p.get("feelings")

        lines = [
            f"模式: {p.get('mode', '?')}",
            f"年龄: {time_data.get('age_days', 0):.2f} 天",
            f"离开: {self._fmt_sec(time_data.get('body_away_s', 0))}",
            f"距交互: {self._fmt_sec(time_data.get('since_interaction_s', 0))}",
            f"相位: {time_data.get('day_phase', '?')}",
            f"情绪: {self._canvas.current_emotion}",
        ]

        # P1：欲望状态
        if desire_data:
            lines.append("")
            lines.append(f"信任: {desire_data.get('tr', '?'):.0f}")
            lines.append(f"眷恋: {desire_data.get('cs', '?'):.0f}")
            lines.append(f"压力: {desire_data.get('sa', '?'):.0f}")

        # P1：感受状态
        if feelings_data:
            lines.append("")
            lines.append(f"聊天: {feelings_data.get('chat', 0) * 100:.0f}%")
            lines.append(f"思念: {feelings_data.get('miss', 0) * 100:.0f}%")
            lines.append(f"探索: {feelings_data.get('explore', 0) * 100:.0f}%")
            lines.append(f"好奇: {feelings_data.get('curiosity', 0) * 100:.0f}%")
            lines.append(f"休息: {feelings_data.get('rest', 0) * 100:.0f}%")
            lines.append(f"自检: {feelings_data.get('self_check', 0) * 100:.0f}%")
        if p.get("distress"):
            lines.append("😫 难受中")
        if p.get("frozen"):
            lines.append("❄️ 冻结")
        if self._last_thought_text:
            lines.append("")
            lines.append(f"💭 {self._last_thought_text}")

        QMessageBox.information(self, "Elysia 状态", "\n".join(lines))

    # ── 每帧（灵魂状态轮询） ────────────────────────

    def _tick(self) -> None:
        # 纯桌宠模式：不读灵魂状态（也就不需要数据库连接）。
        # 但**待机轮换是纯本地行为**，照常跑——她一个人的时候也会换姿势。
        if self._standalone:
            self._maybe_rotate_clip("idle")
            return
        try:
            payload = self._latest_soul_payload()
            if payload is None:
                return

            self._last_soul_payload = payload

            # 状态 → 动画映射
            anim_state = self._map_state_to_animation(payload)

            # 点击后 happy 覆盖
            if time.time() < self._happy_until:
                anim_state = "happy"

            self._maybe_rotate_clip(anim_state)

            self._canvas.update_state(
                anim_state=anim_state,
                distress=payload.get("distress", False),
                desire=payload.get("desire"),
            )

            # 读取最新念头（P2 表达气泡随 _latest_thought 更新）
            self._latest_thought()

            # 气泡超时自动隐藏
            if time.time() >= self._bubble_until and self._bubble.isVisible():
                self._bubble.hide()
        except Exception:
            pass

    def _wander_clip(self) -> str:
        """这套服装里用哪个片段漫游（`run` 优先，其次 Spine 烘焙的 `walk`）。"""
        if self._clip_player is None:
            return ""
        for cid in WANDER_CLIP_CANDIDATES:
            if self._clip_player.has_clip(cid):
                return cid
        return ""

    def _available_geometry(self) -> QRect:
        """当前窗口所在屏幕的可用区域（避开任务栏）；拿不到就退回窗口自身几何。"""
        screen = self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else self.geometry()

    def _wander_tick(self) -> None:
        """漫游：偶尔在桌面上小跑一段（对应 dsh-pet 的 `moves` 槽位）。

        **不依赖灵魂状态**——纯桌宠（standalone）模式下也会漫游，
        所以它挂在独立的 33ms 定时器上，而不是灵魂轮询（5Hz）上。
        碰到屏幕边缘、被拖拽、或开始播回应时都会中止。
        """
        player = self._clip_player
        if player is None or not self._wander_clip():
            return
        now = time.time()

        if self._wander_remaining_px <= 0.0:
            if (
                now < self._wander_next_s
                or self._dragging
                or self._drag_clip_active
                or player.is_playing_once()
            ):
                return
            if player.current_clip_id() not in IDLE_WEIGHTS:
                # 她正在做别的事，等会儿再考虑
                self._wander_next_s = now + 5.0
                return
            self._start_wander()
            return

        geo = self.geometry()
        avail = self._available_geometry()
        step = min(WANDER_SPEED_PX_S * (WANDER_TICK_MS / 1000.0), self._wander_remaining_px)
        new_x = geo.x() + round(step) * self._wander_dir
        if new_x < avail.left() or new_x + geo.width() > avail.right():
            self._end_wander()  # 贴边就停，绝不跑出屏幕
            return
        self.move(new_x, geo.y())
        self._wander_remaining_px -= step
        if self._wander_remaining_px <= 0.0:
            self._end_wander()

    def _start_wander(self) -> None:
        """挑一个方向与距离开始漫游；左右由**水平镜像**实现（无需额外素材）。"""
        player = self._clip_player
        geo = self.geometry()
        avail = self._available_geometry()
        room = {1: avail.right() - (geo.x() + geo.width()), -1: geo.x() - avail.left()}
        choices = [d for d, r in room.items() if r > WANDER_MIN_PX]
        if not choices:
            self._wander_next_s = time.time() + 60.0
            return
        self._wander_dir = random.choice(choices)
        self._wander_remaining_px = float(
            min(random.randint(WANDER_MIN_PX, WANDER_MAX_PX), room[self._wander_dir])
        )
        self._canvas.set_facing(self._wander_dir)
        if player is not None:
            clip = self._wander_clip()
            if clip:
                player.set_clip(clip)
        self._idle_next_switch = time.time() + 60.0  # 别让待机轮换打断她跑步

    def _end_wander(self) -> None:
        """漫游结束：回待机 + 朝向复位 + 安排下一次。"""
        self._wander_remaining_px = 0.0
        self._wander_next_s = time.time() + random.uniform(WANDER_MIN_S, WANDER_MAX_S)
        self._canvas.set_facing(1)
        player = self._clip_player
        if player is not None and not player.is_playing_once():
            idle = self._idle_clip()
            if idle:
                player.set_clip(idle)

    def _begin_drag_clip(self) -> None:
        """拖拽开始 → 播「被拎起悬空」。

        **`grab` 不是动作，是拖拽状态下的物理反应**（对应 dsh-pet 的 `animations.drag`）：
        按住拖动期间一直播、松手就结束，所以它**不该出现在待机池/回应池里**。
        它的循环不干净（首末剪影差 26.74），播放器会自动改用**乒乓**播放，接缝天然消失。
        """
        if self._clip_player is None:
            return
        # 被拖起来就中止漫游（否则松手后她会接着把剩下的距离跑完）
        self._wander_remaining_px = 0.0
        self._wander_next_s = time.time() + random.uniform(WANDER_MIN_S, WANDER_MAX_S)
        self._canvas.set_facing(1)
        for cid in DRAG_CLIP_CANDIDATES:
            if self._clip_player.has_clip(cid):
                self._drag_clip_active = self._clip_player.set_clip(cid)
                break
        if self._drag_clip_active:
            # 拖拽期间别被待机轮换打断
            self._idle_next_switch = time.time() + 30.0

    def _end_drag_clip(self) -> None:
        """松手 → 立刻播落地缓冲（**不溶解**），没有落地素材才回待机。

        - `fade=False` 是关键：落地是一瞬间的冲击，拿 0.3 秒去溶解会把冲击抹平，
          观感变成"松手后先愣一下、然后像起跳"（用户实测反馈）。
        - 物理压缩 `poke()` 提到切换**之前**，让"顿一下"和落地素材同一帧发生。
        """
        if self._clip_player is None or not self._drag_clip_active:
            return
        self._drag_clip_active = False
        self._canvas.poke(LAND_STRENGTH)  # 先"顿"一下，再立刻切落地
        if not self._clip_player.play_once(LAND_CLIP, fade=False):
            idle = self._idle_clip()
            if idle:
                self._clip_player.set_clip(idle)
        self._idle_next_switch = time.time() + random.uniform(IDLE_SWITCH_MIN_S, IDLE_SWITCH_MAX_S)

    def _idle_clip(self) -> str:
        """挑一个待机片段（按 `IDLE_WEIGHTS` 的顺序取第一个可用的）。"""
        if self._clip_player is None:
            return ""
        for cid in IDLE_WEIGHTS:
            if self._clip_player.has_clip(cid):
                return cid
        return ""

    def _maybe_rotate_clip(self, anim_state: str) -> None:
        """**状态驱动选片段 + 待机池轮换**（借鉴 dsh-pet 的动画池设计）。

        两条规则：

        1. **状态变了** → 切到该状态对应的片段池里的一个（池里没有的片段自动跳过，
           全都没有就保持现状——素材不全时**优雅降级**，不会白屏也不会报错）
        2. **状态没变但待机够久** → 在待机池里换个片段，让她不那么单调

        与 dsh-pet 的根本区别：它接的是 DSH 的工作状态/余额，我们接的是**她自己的灵魂状态**。
        """
        player = self._clip_player
        if player is None:
            return
        # **拖拽状态片段优先**：按住拖动期间不能被待机轮换或状态映射顶掉。
        # （状态变更分支原来绕过了这个保护，实测确实会把 `grab` 顶回待机。）
        if self._drag_clip_active:
            return
        # 回应演到一半也不打断——等它自然结束（播放器会自动回待机）再轮换
        if player.is_playing_once():
            return
        now = time.time()

        if anim_state != self._last_state_clip:
            pool = [c for c in STATE_CLIPS.get(anim_state, ()) if player.has_clip(c)]
            self._last_state_clip = anim_state
            if pool:
                player.set_clip(random.choice(pool))
            self._idle_next_switch = now + random.uniform(IDLE_SWITCH_MIN_S, IDLE_SWITCH_MAX_S)
            return

        if now < self._idle_next_switch:
            return
        current = player.current_clip_id()
        idle_pool = {c: w for c, w in IDLE_WEIGHTS.items() if player.has_clip(c)}
        if current not in idle_pool:
            # 她正在播非待机片段（点击回应等），等它自然结束再轮换
            self._idle_next_switch = now + 3.0
            return
        picks = {c: w for c, w in idle_pool.items() if c != current}
        if picks:
            player.set_clip(random.choices(list(picks), weights=list(picks.values()), k=1)[0])
        self._idle_next_switch = now + random.uniform(IDLE_SWITCH_MIN_S, IDLE_SWITCH_MAX_S)

    def _map_state_to_animation(self, payload: dict[str, Any]) -> str:
        if payload.get("distress"):
            return "unhappy"
        if payload.get("mode") in ("alone", "body_away"):
            return "away"
        interaction_s = payload.get("time", {}).get("since_interaction_s", 9999)
        if interaction_s < 60:
            return "happy"
        if interaction_s > 3600:
            return "tired"
        return "idle"

    def _latest_soul_payload(self) -> dict[str, Any] | None:
        try:
            rows = self.conn_hb.execute(
                "SELECT payload FROM heartbeats WHERE beat_type = 'soul' ORDER BY id DESC LIMIT 1"
            ).fetchall()
            if not rows:
                return None
            payload = json.loads(rows[0][0])
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _latest_thought(self) -> None:
        try:
            rows = self.conn_hb.execute(
                "SELECT id, kind, text FROM thought_log ORDER BY id DESC LIMIT 1"
            ).fetchall()
            if rows and rows[0][0] != self._last_thought_id:
                self._last_thought_id = rows[0][0]
                self._last_thought_text = rows[0][2]
                # P2：表达气泡——仅当最新念头是"表达"（LLM 开口）时显示
                if rows[0][1] == "expression" and rows[0][2].strip():
                    self._show_bubble(rows[0][2])
        except Exception:
            pass

    @staticmethod
    def _fmt_sec(s: float) -> str:
        s = int(s)
        if s < 60:
            return f"{s}秒"
        return f"{s // 60}分{s % 60}秒"

    def _show_bubble(self, text: str, *, play_audio: bool = True) -> None:
        """显示表达气泡：角色上方，展示 LLM 开口文本，BUBBLE_SHOW_S 后隐藏。

        play_audio=False 时仅显示气泡（用于输入确认等非开口场景，不触发出声）。
        """
        self._bubble.setText(text)
        self._bubble.adjustSize()
        cw, ch = self._canvas.width(), self._canvas.height()
        bw = self._bubble.width()
        x = max(0, (cw - bw) // 2)
        self._bubble.move(x, int(ch * 0.08))
        self._bubble.show()
        self._bubble.raise_()
        self._bubble_until = time.time() + BUBBLE_SHOW_S
        if play_audio:
            self._play_latest_audio()

    def _play_latest_audio(self) -> None:
        """播放 TTS 缓存中最新合成的 wav（同一时刻刚合成，时序对应本次表达）。

        缓存 key = SHA256(文本+情绪+语速)，桌宠无法反推，故直接取最新写入文件；
        prewarm 未启用时缓存全部来自实时合成，最新即本次开口。失败静默降级为仅气泡。
        """
        try:
            cache_dir = _PROJECT_ROOT / "data" / "cache" / "tts"
            wavs = sorted(cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not wavs:
                return
            path = str(wavs[0])
            if path == self._last_played_audio:
                return
            # 懒初始化播放器：首次出声才创建，避免启动期 MFT 探测警告
            if self._player is None:
                self._audio_output = QAudioOutput(self)
                self._player = QMediaPlayer(self)
                self._player.setAudioOutput(self._audio_output)
            self._last_played_audio = path
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()
        except Exception:
            pass

    # ── 交互 ──────────────────────────────────────────
    #
    # ⚠️ 为什么点击**不写 text**（2026-10-21 止血）：
    #   灵魂把 `kv('interaction')` 里任何非空 `text` 当成**用户说的话**
    #   （`soul/heartbeat.py` → `_pending_user_message` → `record_user_message`），
    #   所以旧写法 `_write_interaction("（点击）")` 会在她的对话历史里留下
    #   一句「（点击）」——实测已污染 1 条 `dialogue_turns` + 15 条 interaction 记忆
    #   + 1 条 exchange 记忆。**点击是摸她，不是说话** ⇒ 只写 ts + kind。
    #
    # ⚠️ 另一条：poke **不能覆盖**她还没读到的那句话（旧 F8）。
    #   已读判定用灵魂自己的回执：`timesense.last_interaction_ts`
    #   （灵魂每拍消费 interaction 后把它推进到该条的 ts）。

    def _on_character_click(self) -> None:
        """点击角色 → 从**回应池**里随机播一个 + 物理弹跳 + 交互事件。"""
        # 回应池随机抽（素材越全越丰富；池里没有的自动跳过）
        if self._clip_player is not None:
            available = [c for c in CLICK_POOL if self._clip_player.has_clip(c)]
            if available:
                self._clip_player.play_once(random.choice(available))
        self._write_poke()
        self._happy_until = time.time() + CLICK_HAPPY_S
        self._canvas.poke()
        self._canvas.set_emotion("happy")

    def _submit_interaction(self, text: str) -> None:
        """提交文本交互 → 写入 DB + 即时反馈。

        现在有真实回复了，不再弹"听到啦"确认气泡——回复气泡由灵魂表达
        管线产生（thought_log），认证反馈留给气泡自身即可。
        """
        self._write_interaction(text, kind="say")
        self._canvas.poke()
        self._canvas.set_emotion("happy")

    def _write_poke(self) -> None:
        """记录"有人摸了摸她"：**不写 text**，且不覆盖未读的用户消息。"""
        if self._standalone:
            return
        if self._has_unread_user_message():
            # 宁可少记一个 poke，也不能把你刚说的话吃掉
            return
        self._write_interaction(None, kind="poke")

    def _has_unread_user_message(self) -> bool:
        """`kv('interaction')` 里那条**用户消息**，灵魂是否还没消费。

        判定：消息 `ts` > `timesense.last_interaction_ts` ⇒ 未读。
        不是用户的话（无 text，例如上一次 poke）⇒ 无可保护，返回 False。
        读不到/读失败 ⇒ 当作**未读**（保护用户的话优先）。
        """
        try:
            row = self.conn.execute("SELECT value FROM kv WHERE key = 'interaction'").fetchone()
            if row is None:
                return False
            payload = json.loads(row[0])
            if not str(payload.get("text") or "").strip():
                return False
            msg_ts = float(payload.get("ts") or 0.0)
            if msg_ts <= 0:
                return False
            seen_row = self.conn.execute("SELECT value FROM kv WHERE key = 'timesense'").fetchone()
            if seen_row is None:
                return True
            seen = float(json.loads(seen_row[0]).get("last_interaction_ts") or 0.0)
            return msg_ts > seen
        except Exception:
            return True

    def _write_interaction(self, text: str | None, *, kind: str) -> None:
        """写交互行（`kv('interaction')`）。

        `text` **只在人真的说了话**时才写 —— 灵魂把非空 text 当用户发言，
        会落进 `dialogue_turns` 与记忆。`kind`（`say` / `poke`）给分道留标记。
        """
        # 纯桌宠模式：**不写库**。交互记录是"给灵魂的输入通道"，
        # 只看桌宠时不该在她的状态库里留下痕迹。
        if self._standalone:
            return
        payload: dict[str, Any] = {"ts": time.time(), "kind": kind}
        if text is not None:
            payload["text"] = text
        self.conn.execute(
            "INSERT OR REPLACE INTO kv (key, value) VALUES ('interaction', ?)",
            (json.dumps(payload),),
        )
        self.conn.commit()


def run_pet(
    state_db_path: Path,
    heartbeat_db_path: Path,
    *,
    standalone: bool = False,
) -> None:
    """启动桌宠（独立进程入口）。

    `standalone=True` = **纯桌宠**：不打开数据库、不读写她的状态，
    只把服装包里的序列帧演出来。用于"只想看看桌宠长什么样"。
    """
    app = QApplication([])
    window = PetWindow(state_db_path, heartbeat_db_path, standalone=standalone)
    window.show()
    app.exec()


def _main() -> None:
    """`python -m elysia.body.pet` —— 默认就是**纯桌宠**（不涉及灵魂）。"""
    import argparse

    parser = argparse.ArgumentParser(description="Elysia 桌宠窗口（默认纯桌宠，不涉及灵魂）")
    parser.add_argument("--state-db", type=Path, default=Path("data/state.db"))
    parser.add_argument("--heartbeat-db", type=Path, default=Path("data/heartbeat.db"))
    parser.add_argument(
        "--with-soul-data",
        action="store_true",
        help="同时读取灵魂状态（气泡/表情随她真实状态变化）；默认不读",
    )
    args = parser.parse_args()
    run_pet(args.state_db, args.heartbeat_db, standalone=not args.with_soul_data)


if __name__ == "__main__":
    _main()
