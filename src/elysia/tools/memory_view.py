"""记忆浏览器（P3 观测工具）：只读可视化 heartbeat.db 中的记忆。

记忆打磨期的观测抓手，回答四个问题：
- 看存储：库里有哪些记忆、层级分布、是否已被更正取代
- 看打分：每条记忆"当下"的得分构成（层级 / 情绪 / 索引 / 新鲜度）
- 看召回：此刻她会想起哪 3 条（复现表达管线的 select_hooks 逻辑）
- 看沉淀：层级、细节度、索引强度（晋升与衰减的结果）

只读：仅执行 SELECT，可与运行中的灵魂并存（SQLite WAL 支持并发读）。

用法：
    uv run python -m elysia.tools.memory_view
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from elysia.core.config import get_settings
from elysia.memory.levels import MemoryRecord
from elysia.memory.retrieve import score_breakdown, select_hooks

AUTO_REFRESH_MS = 3000

LEVEL_LABELS = {"shallow": "浅层", "working": "工作", "deep": "深层"}
KIND_LABELS = {"interaction": "交互", "expression": "表达"}
LEVEL_COLORS = {
    "deep": QColor("#dbeafe"),
    "working": QColor("#e8f5e9"),
    "shallow": QColor("#f5f5f5"),
}
_SUPERSEDED_COLOR = QColor("#9e9e9e")
_RECALL_COLOR = QColor("#1565c0")

COLUMNS = [
    "id",
    "层级",
    "类型",
    "年龄",
    "重要",
    "访问",
    "细节",
    "索引",
    "新鲜",
    "得分",
    "召回",
    "状态",
    "内容",
]

_MEMORY_SELECT = "SELECT * FROM memories"


# ── 只读数据读取（可单测，不依赖 Qt 实例）───────────────
def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    keys = row.keys()  # sqlite3.Row 迭代的是"值"，取列名必须用 keys()
    data: dict[str, Any] = {k: row[k] for k in keys}
    data["emotion_vector"] = json.loads(data.get("emotion_vector") or "{}")
    data["protected"] = bool(data.get("protected"))
    # 兼容旧库：缺 superseded_by 列时 from_dict 默认 None（无需迁移即可观测）
    return MemoryRecord.from_dict(data)


def load_memories(db_path: Path) -> list[MemoryRecord]:
    """读取全部记忆（表不存在时返回空）。

    用 SELECT * 而非显式列名：兼容尚未执行迁移的旧库
    （缺 superseded_by 列时按"未取代"处理），观测不被 schema 版本阻塞。
    """
    if not db_path.exists():
        return []
    con = _connect(db_path)
    try:
        rows = con.execute(_MEMORY_SELECT).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    finally:
        con.close()
    return [_row_to_record(r) for r in rows]


def load_index_strengths(db_path: Path) -> dict[int, float]:
    """读取 memory_index 的索引强度：memory_id → strength。"""
    if not db_path.exists():
        return {}
    con = _connect(db_path)
    try:
        rows = con.execute("SELECT memory_id, strength FROM memory_index").fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return {}
        raise
    finally:
        con.close()
    return {int(r["memory_id"]): float(r["strength"]) for r in rows}


def load_current_mood(db_path: Path) -> dict[str, float]:
    """取最近一次灵魂心跳快照的"当下感受"（复现检索打分用）。"""
    if not db_path.exists():
        return {}
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT payload FROM heartbeats WHERE beat_type='soul' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return {}
        raise
    finally:
        con.close()
    if row is None:
        return {}
    payload: dict[str, Any] = json.loads(row["payload"])
    feelings = payload.get("feelings") or {}
    return {str(k): float(v) for k, v in feelings.items()}


# ── 展示格式化 ─────────────────────────────────────────
def _fmt_age(ts: float, now: float) -> str:
    age = max(0.0, now - ts)
    if age < 60:
        return "刚刚"
    if age < 3600:
        return f"{int(age // 60)}分钟前"
    if age < 86400:
        return f"{int(age // 3600)}小时前"
    return f"{age / 86400:.1f}天前"


def _fmt_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


# ── 窗口 ───────────────────────────────────────────────
class MemoryBrowser(QMainWindow):
    """记忆观测窗口：筛选 + 明细 + 当下召回预览，可选自动刷新。"""

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self._db_path = db_path
        self._records_by_row: list[MemoryRecord] = []
        self.setWindowTitle(f"Elysia 记忆浏览器 — {db_path}")
        self.resize(1280, 760)

        # 控制条
        self._level_combo = QComboBox()
        self._level_combo.addItem("全部层级", None)
        for key, label in LEVEL_LABELS.items():
            self._level_combo.addItem(label, key)
        self._kind_combo = QComboBox()
        self._kind_combo.addItem("全部类型", None)
        for key, label in KIND_LABELS.items():
            self._kind_combo.addItem(label, key)
        self._hide_superseded = QCheckBox("隐藏已取代")
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索内容…")
        self._search.setClearButtonEnabled(True)
        self._auto = QCheckBox("自动刷新(3s)")
        self._refresh_btn = QPushButton("刷新")

        bar = QHBoxLayout()
        for w in (
            self._level_combo,
            self._kind_combo,
            self._hide_superseded,
            self._search,
        ):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self._auto)
        bar.addWidget(self._refresh_btn)

        # 表格 + 明细
        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)

        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("选中一行查看记忆明细与得分构成")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._table)
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        root = QVBoxLayout()
        root.addLayout(bar)
        root.addWidget(splitter, 1)
        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        # 信号
        self._refresh_btn.clicked.connect(lambda: self.refresh())
        self._level_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self._kind_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self._hide_superseded.toggled.connect(lambda _c: self.refresh())
        self._search.textChanged.connect(lambda _t: self.refresh())
        self._table.itemSelectionChanged.connect(self._show_detail)

        # 自动刷新
        self._timer = QTimer(self)
        self._timer.setInterval(AUTO_REFRESH_MS)
        self._timer.timeout.connect(lambda: self.refresh())
        self._auto.toggled.connect(self._on_auto_toggled)

        self.refresh()

    def _on_auto_toggled(self, on: bool) -> None:
        if on:
            self._timer.start()
        else:
            self._timer.stop()

    def refresh(self) -> None:
        db = self._db_path
        records = load_memories(db)
        strengths = load_index_strengths(db)
        mood = load_current_mood(db)
        now = time.time()

        level = self._level_combo.currentData()
        kind = self._kind_combo.currentData()
        hide_sup = self._hide_superseded.isChecked()
        keyword = self._search.text().strip()

        filtered: list[MemoryRecord] = []
        for rec in records:
            if level and rec.level != level:
                continue
            if kind and rec.kind != kind:
                continue
            if hide_sup and rec.superseded_by is not None:
                continue
            if (
                keyword
                and keyword not in (rec.content or "")
                and keyword not in (rec.narrative or "")
            ):
                continue
            filtered.append(rec)

        # 当下召回排名（复现表达管线：排除表达回声与已取代）
        hits = select_hooks(records, mood, index_strengths=strengths, now=now)
        rank = {h.memory_id: i + 1 for i, h in enumerate(hits)}

        def _total(rec: MemoryRecord) -> float:
            mid = rec.id if rec.id is not None else -1
            part = score_breakdown(rec, mood, index_strength=strengths.get(mid, 1.0), now=now)
            return float(sum(part.values()))

        filtered.sort(key=_total, reverse=True)

        self._records_by_row = filtered
        self._table.setRowCount(len(filtered))
        for i, rec in enumerate(filtered):
            mid = rec.id if rec.id is not None else -1
            strength = strengths.get(mid, 1.0)
            parts = score_breakdown(rec, mood, index_strength=strength, now=now)
            total = round(sum(parts.values()), 3)
            rec_rank = rank.get(mid)
            cells = [
                str(mid),
                LEVEL_LABELS.get(rec.level, rec.level),
                KIND_LABELS.get(rec.kind, rec.kind),
                _fmt_age(rec.created_ts, now),
                f"{rec.importance:.2f}",
                str(rec.access_count),
                f"{rec.detail_level:.2f}",
                f"{strength:.2f}",
                f"{parts['recency']:.2f}",
                f"{total:.3f}",
                f"#{rec_rank}" if rec_rank else "",
                "已取代" if rec.superseded_by is not None else "现行",
                (rec.content or "")[:60].replace("\n", " "),
            ]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if rec.superseded_by is not None:
                    item.setForeground(_SUPERSEDED_COLOR)
                elif rec.level in LEVEL_COLORS:
                    item.setBackground(LEVEL_COLORS[rec.level])
                if rec_rank:
                    item.setForeground(_RECALL_COLOR)
                self._table.setItem(i, j, item)
        self._table.resizeColumnsToContents()

        # 统计
        dist: dict[str, int] = {}
        for rec in records:
            dist[rec.level] = dist.get(rec.level, 0) + 1
        superseded = sum(1 for rec in records if rec.superseded_by is not None)
        dist_txt = " / ".join(
            f"{LEVEL_LABELS.get(k, k)} {v}" for k, v in sorted(dist.items(), reverse=True)
        )
        mtime = _fmt_ts(db.stat().st_mtime) if db.exists() else "库不存在"
        self.statusBar().showMessage(
            f"共 {len(records)} 条（{dist_txt}）｜已取代 {superseded}｜当前显示 {len(filtered)}"
            f"｜库更新 {mtime}"
        )

    def _show_detail(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._records_by_row):
            self._detail.clear()
            return
        rec = self._records_by_row[row]
        strengths = load_index_strengths(self._db_path)
        mood = load_current_mood(self._db_path)
        now = time.time()
        mid = rec.id if rec.id is not None else -1
        strength = strengths.get(mid, 1.0)
        parts = score_breakdown(rec, mood, index_strength=strength, now=now)
        total = round(sum(parts.values()), 3)

        lines = [
            f"# {mid}  {LEVEL_LABELS.get(rec.level, rec.level)} / "
            f"{KIND_LABELS.get(rec.kind, rec.kind)}",
            f"状态：{'已取代（不再召回）' if rec.superseded_by is not None else '现行'}"
            + (f" ← 被 #{rec.superseded_by} 取代" if rec.superseded_by is not None else ""),
            f"内容：{rec.content}",
            f"叙事：{rec.narrative}",
            "",
            f"创建：{_fmt_ts(rec.created_ts)}（{_fmt_age(rec.created_ts, now)}）",
            f"重要性 {rec.importance:.2f}｜访问 {rec.access_count} 次"
            + (f"｜上次 {_fmt_age(rec.last_access_ts, now)}" if rec.last_access_ts else ""),
            f"细节度 {rec.detail_level:.2f}｜索引强度 {strength:.3f}"
            f"｜{'珍贵(protected)' if rec.protected else '普通'}",
            f"情感向量：{rec.emotion_vector}",
            "",
            f"—— 当下得分 {total:.3f}（当前感受 {mood or '∅'}）——",
            f"  层级 {parts['level']:.3f}｜情绪 {parts['emotion']:.3f}"
            f"｜索引 {parts['index']:.3f}｜重要 {parts['importance']:.3f}"
            f"｜新鲜 {parts['recency']:.3f}",
        ]
        self._detail.setPlainText("\n".join(lines))


def main() -> int:
    settings = get_settings()
    db_path = settings.data_dir / "heartbeat.db"
    app = QApplication(sys.argv)
    window = MemoryBrowser(db_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
