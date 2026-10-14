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

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
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
ANIM_INTERVAL_MS = 33  # ~30fps 动画渲染
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
LAND_CLIP = "land"

# ── 动画编排（借鉴 dsh-pet 的 animations / animationWeights 设计）──────────
# 待机池**带权重**（对应 dsh-pet 的 `animationWeights`）。
# 权重不是平摊的：`idle-open` 是绝对主力——睁眼待机占待机绝大部分时间；
# 若平摊，闭眼的 `idle` 会占 1/5，把"睁眼"的改进抵消掉。
IDLE_WEIGHTS: dict[str, int] = {
    "idle-open": 8,  # 睁眼待机呼吸：主力
    "idle": 2,  # 闭眼呼吸：偶尔打个盹
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

        # fallback 状态变量
        self._beat_phase = 0.0
        self._mode = "present"
        self._distress = False

        # 动画定时器 30fps
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._anim_tick)
        self._anim_timer.start(ANIM_INTERVAL_MS)

        if self._valid:
            self.setMinimumSize(200, 200)
        else:
            self.setMinimumHeight(120)

    # ── 公共接口 ──────────────────────────────────────

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
        dt = ANIM_INTERVAL_MS / 1000.0

        # 序列帧推进：**与物理动画叠加**——素材给"她在演什么"，物理给"她怎么晃"。
        # 放在最前，且不受 dragging 分支影响：拖拽时素材也在走（手感更连续）。
        if self._player is not None:
            self._player.advance(dt)

        if not self._valid:
            self.update()
            return

        if self._dragging:
            self._bounce *= 0.88
            if self._bounce < 0.0005:
                self._bounce = 0.0
            self.update()
            return

        # 参数平滑逼近目标（lerp）
        k = 0.06
        for key in self._cur:
            self._cur[key] += (self._target[key] - self._cur[key]) * k

        # 相位累加
        self._phase_breath += self._cur["breath_speed"] * dt
        self._phase_sway += self._cur["sway_speed"] * dt
        self._float_phase += 0.7 * dt

        # 弹跳衰减
        self._bounce *= 0.88
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

        scale = self._cur["base_scale"] + 0.022 * math.sin(self._phase_breath) + self._bounce
        scale *= self._scale
        rot = self._cur["sway_amp"] * math.sin(self._phase_sway)
        float_y = self._cur["float_amp"] * math.sin(self._float_phase)

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
        t.scale(scale, scale)
        t.translate(-pw / 2, -ph / 2)

        painter.setTransform(t)
        # 交叉淡入淡出：旧帧先以 1-alpha 淡出，新帧再以 alpha 淡入。
        # 素材越少，这一步对"过渡自然度"的提升越明显。
        if prev is not None and not prev.isNull() and alpha < 1.0:
            painter.setOpacity(1.0 - alpha)
            if prev.size() == pixmap.size():
                painter.drawPixmap(0, 0, prev)
            else:  # 尺寸不同（例如换服装）：按中心对齐叠画
                painter.drawPixmap((pw - prev.width()) // 2, (ph - prev.height()) // 2, prev)
        painter.setOpacity(alpha)
        painter.drawPixmap(0, 0, pixmap)
        painter.setOpacity(1.0)

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

        # ── 初始位置：屏幕右下角 ──
        from PySide6.QtGui import QGuiApplication

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

    def _begin_drag_clip(self) -> None:
        """拖拽开始 → 播「被拎起悬空」。

        **`grab` 不是动作，是拖拽状态下的物理反应**（对应 dsh-pet 的 `animations.drag`）：
        按住拖动期间一直播、松手就结束，所以它**不该出现在待机池/回应池里**。
        它的循环不干净（首末剪影差 26.74），播放器会自动改用**乒乓**播放，接缝天然消失。
        """
        if self._clip_player is None:
            return
        if self._clip_player.has_clip(DRAG_CLIP):
            self._drag_clip_active = self._clip_player.set_clip(DRAG_CLIP)
        if self._drag_clip_active:
            # 拖拽期间别被待机轮换打断
            self._idle_next_switch = time.time() + 30.0

    def _end_drag_clip(self) -> None:
        """松手 → 有落地片段就播一次落地缓冲，否则直接回待机。"""
        if self._clip_player is None or not self._drag_clip_active:
            return
        self._drag_clip_active = False
        if not self._clip_player.play_once(LAND_CLIP):
            idle = self._idle_clip()
            if idle:
                self._clip_player.set_clip(idle)
        self._canvas.poke(LAND_STRENGTH)  # 落地"顿一下"
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

    def _on_character_click(self) -> None:
        """点击角色 → 从**回应池**里随机播一个 + 物理弹跳 + 交互事件。"""
        # 回应池随机抽（素材越全越丰富；池里没有的自动跳过）
        if self._clip_player is not None:
            available = [c for c in CLICK_POOL if self._clip_player.has_clip(c)]
            if available:
                self._clip_player.play_once(random.choice(available))
        self._write_interaction("（点击）")
        self._happy_until = time.time() + CLICK_HAPPY_S
        self._canvas.poke()
        self._canvas.set_emotion("happy")

    def _submit_interaction(self, text: str) -> None:
        """提交文本交互 → 写入 DB + 即时反馈。

        现在有真实回复了，不再弹"听到啦"确认气泡——回复气泡由灵魂表达
        管线产生（thought_log），认证反馈留给气泡自身即可。
        """
        self._write_interaction(text)
        self._canvas.poke()
        self._canvas.set_emotion("happy")

    def _write_interaction(self, text: str) -> None:
        # 纯桌宠模式：**不写库**。交互记录是"给灵魂的输入通道"，
        # 只看桌宠时不该在她的状态库里留下痕迹。
        if self._standalone:
            return
        payload = json.dumps({"ts": time.time(), "text": text})
        self.conn.execute(
            "INSERT OR REPLACE INTO kv (key, value) VALUES ('interaction', ?)",
            (payload,),
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
