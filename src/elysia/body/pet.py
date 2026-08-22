"""最小桌宠 v0：她怎么被你感知（P0 起，历史教训：交互通道是核心，不是附属品）。

功能：
- 心跳光效：QTimer 5Hz 脉冲，半径随 soul 心跳节律波动，distress 时变红
- 状态流：最新 mode / age_days / away_s / since_interaction 文字展示
- 文本输入：输入消息 → state.db interaction 事件（感受层入口）
- 显示：无边框置顶，小窗口常驻桌面

状态读取：pet 独立进程，直接同步 sqlite3（WAL 下读并发安全，毫秒级）。
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

PET_WIDTH = 320
PET_HEIGHT = 420
POLL_INTERVAL_MS = 200  # 5Hz 渲染


class PetWindow(QMainWindow):
    """无边框置顶桌宠窗口（心跳光效 + 状态流 + 文本输入）。"""

    def __init__(self, state_db_path: Path, heartbeat_db_path: Path) -> None:
        super().__init__()
        self._state_db_path = state_db_path
        self._heartbeat_db_path = heartbeat_db_path
        self._conn_state: sqlite3.Connection | None = None
        self._conn_heartbeat: sqlite3.Connection | None = None

        self.setWindowTitle("Elysia 桌宠")
        self.setFixedSize(PET_WIDTH, PET_HEIGHT)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        # ── 中央部件 ──
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        central.setStyleSheet("background-color: #1a1a2e; color: #e0e0e0;")

        # 心跳光效画布
        self._canvas = _HeartbeatCanvas(self)
        self._canvas.setMinimumHeight(120)
        layout.addWidget(self._canvas)

        # 状态流
        self._status_label = QLabel(" 等待心跳连接…")
        self._status_label.setStyleSheet("font-size: 11px; padding: 4px;")
        layout.addWidget(self._status_label)

        # 最近念头展示
        self._thought_display = QTextEdit()
        self._thought_display.setReadOnly(True)
        self._thought_display.setMaximumHeight(120)
        self._thought_display.setStyleSheet(
            "font-size: 10px; background-color: #0f0f23; border: 1px solid #3a3a5c;"
        )
        layout.addWidget(self._thought_display)

        # 输入区
        input_layout = QHBoxLayout()
        self._input_field = QLineEdit()
        self._input_field.setPlaceholderText("说点什么…  （外部刺激 → 感受层）")
        self._input_field.setStyleSheet(
            "font-size: 12px; padding: 4px; background-color: #0f0f23;"
            " border: 1px solid #3a3a5c; color: #e0e0e0;"
        )
        self._input_field.returnPressed.connect(self._submit_interaction)
        submit_btn = QPushButton("发送")
        submit_btn.setStyleSheet(
            "font-size: 12px; padding: 4px 8px; background-color: #7aa2f7; color: #000;"
        )
        submit_btn.clicked.connect(self._submit_interaction)
        input_layout.addWidget(self._input_field)
        input_layout.addWidget(submit_btn)
        layout.addLayout(input_layout)

        # ── 定时器 ──
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_INTERVAL_MS)

    # ── 数据库连接 ──────────────────────────────────
    @property
    def conn(self) -> sqlite3.Connection:
        """state.db 连接（读 timesense/body_status/interaction）。"""
        if self._conn_state is None:
            self._conn_state = sqlite3.connect(str(self._state_db_path))
            self._conn_state.execute("PRAGMA busy_timeout=5000")
        return self._conn_state

    @property
    def conn_hb(self) -> sqlite3.Connection:
        """heartbeat.db 连接（读 soul 心跳 payload/thought_log）。"""
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

    # ── 每帧 ──────────────────────────────────────
    def _tick(self) -> None:
        try:
            payload = self._latest_soul_payload()
            if payload is None:
                return
            time_data = payload.get("time", {})
            self._canvas.update_state(
                dp=True,
                distress=payload.get("distress", False),
                age_days=time_data.get("age_days", 0),
                mode=payload.get("mode", "?"),
                away_s=time_data.get("body_away_s", 0),
                interaction_s=time_data.get("since_interaction_s", 0),
                phase=time_data.get("day_phase", "?"),
            )
            # 状态文本
            self._status_label.setText(
                f"模式: {payload.get('mode', '?')}  "
                f"年龄: {time_data.get('age_days', 0):.2f} 天  "
                f"离开: {time_data.get('body_away_s', 0) // 60} 分  "
                f"距交互: {time_data.get('since_interaction_s', 0) // 60} 分  "
                f"相位: {time_data.get('day_phase', '?')}"
                + (" [❄️ 冻结]" if payload.get("frozen") else "")
                + (" [😫 难受]" if payload.get("distress") else "")
            )

            self._latest_thought()
        except Exception:
            pass  # DB 连接初期的空结果

    def _latest_soul_payload(self) -> dict[str, Any] | None:
        try:
            rows = self.conn_hb.execute(
                "SELECT payload FROM heartbeats WHERE beat_type = 'soul' ORDER BY id DESC LIMIT 1"
            ).fetchall()
            if not rows:
                return None
            return json.loads(rows[0][0])  # type: ignore[no-any-return]
        except Exception:
            return None

    def _latest_thought(self) -> None:
        rows = self.conn_hb.execute(
            "SELECT text FROM thought_log ORDER BY id DESC LIMIT 1"
        ).fetchall()
        if rows:
            self._thought_display.setText(rows[0][0])
        else:
            self._thought_display.clear()

    # ── 交互 ──────────────────────────────────────
    def _submit_interaction(self) -> None:
        text = self._input_field.text().strip()
        if not text:
            return
        payload = json.dumps({"ts": time.time(), "text": text})
        self.conn.execute(
            "INSERT OR REPLACE INTO kv (key, value) VALUES ('interaction', ?)",
            (payload,),
        )
        self.conn.commit()
        self._input_field.clear()


class _HeartbeatCanvas(QWidget):
    """心跳光效画布：半径呼吸脉冲，颜色反映情绪与模式。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._beat_phase = 0.0
        self._distress = False
        self._age_days = 0.0
        self._mode = "present"
        self._away_s = 0.0
        self._interaction_s = 0.0
        self._phase = "day"

    def update_state(self, **kwargs: Any) -> None:
        self._beat_phase += 0.3  # 呼吸进度
        for k, v in kwargs.items():
            setattr(self, f"_{k}", v)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        base_r = 30
        pulse = math.sin(self._beat_phase) * 8
        r = base_r + pulse
        if self._distress:
            color = QColor(255, 60, 60, 200)
        elif self._mode in ("alone", "body_away"):
            color = QColor(120, 160, 255, 180)
        else:
            color = QColor(120, 255, 180, 200)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))
        painter.end()


def run_pet(state_db_path: Path, heartbeat_db_path: Path) -> None:
    """启动桌宠（独立进程入口）。"""
    app = QApplication([])
    window = PetWindow(state_db_path, heartbeat_db_path)
    window.show()
    app.exec()
