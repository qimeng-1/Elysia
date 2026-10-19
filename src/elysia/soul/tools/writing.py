"""她的写作工作区（W3 / 用户 2026-10-19 答复 #1：`data/her_writing/`）。

她在表达流程里可以用 `write_note` 工具把自己的话写下来——**写不写由她自己决定**
（C 方案：程序不硬塞、不代写）。本模块只保证"叫得动 + 写得安全"：

- 目录固定在 `data/her_writing/`（`.gitignore` 已排除）；
- **路径穿越防护**：拒绝 `..`、路径分隔符、绝对路径/盘符、symlink 逃出；
- 单文件 ≤ 64 KB、目录总量 ≤ 5 MB（超限**明确报错**，不静默截断）；
- 只追加或新建：从不删除、不覆盖已有内容；不碰 `src/`、`data/*.db`、`data/run/`；
- 写盘在调用方用 `asyncio.to_thread` 跑，不阻塞心跳。

外部依赖：仅标准库。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path

from elysia.core.config import PROJECT_ROOT

WRITING_DIR_NAME = "her_writing"
DEFAULT_WRITING_ROOT = PROJECT_ROOT / "data" / WRITING_DIR_NAME

MAX_NOTE_BYTES = 64 * 1024  # 单文件 64 KB
MAX_DIR_BYTES = 5 * 1024 * 1024  # 目录总量 5 MB
ALLOWED_SUFFIXES = (".md", ".txt")
MAX_FILENAME_CHARS = 80

# 只允许"一个文件名"：中英文/数字/下划线/短横/点/空格（不含分隔符与 ..）
_FILENAME_RE = re.compile(rf"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff .-]{{0,{MAX_FILENAME_CHARS - 1}}}$")


class WritingError(Exception):
    """写不了（原因必须可读，直接回给她的工具回执）。"""


class WritingWorkspace:
    """她自己的写作目录（默认 `data/her_writing/`；测试可注入临时根目录）。"""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_file_bytes: int = MAX_NOTE_BYTES,
        max_total_bytes: int = MAX_DIR_BYTES,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else DEFAULT_WRITING_ROOT
        self._max_file_bytes = int(max_file_bytes)
        self._max_total_bytes = int(max_total_bytes)
        self._clock = clock  # 测试可注入固定时间（起默认文件名用）

    # ── 对外 API ─────────────────────────────────────────────
    def write_note(self, text: str, filename: str | None = None) -> str:
        """把 `text` 追加/新建到写作区；返回可读回执（工具结果）。失败抛 WritingError。"""
        if not isinstance(text, str) or not text.strip():
            raise WritingError("正文为空——没有可写的内容")
        payload = text.encode("utf-8")
        if len(payload) > self._max_file_bytes:
            raise WritingError(
                f"太长：{len(payload)} 字节 > 单文件上限 {self._max_file_bytes} 字节（未写入）"
            )
        name = self._validate_name(filename) if filename else self._auto_name()
        target = self._safe_target(name)
        existing = target.stat().st_size if target.exists() else 0
        if existing + len(payload) > self._max_file_bytes:
            raise WritingError(
                f"会超过单文件上限（已有 {existing} 字节 + 本次 {len(payload)} 字节 "
                f"> {self._max_file_bytes}）——未写入"
            )
        total = self._dir_bytes()
        if total + len(payload) > self._max_total_bytes:
            raise WritingError(
                f"会超过目录上限（已有 {total} 字节 + 本次 {len(payload)} 字节 "
                f"> {self._max_total_bytes}）——未写入"
            )

        self.root.mkdir(parents=True, exist_ok=True)
        separator = "\n\n" if existing else ""
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(separator + text.rstrip("\n") + "\n")
        size = target.stat().st_size
        return (
            f"（已写进你的写作区：data/{WRITING_DIR_NAME}/{name}；"
            f"本次 {len(text)} 字，文件 {size} 字节）"
        )

    # ── 内部：路径与容量 ─────────────────────────────────────
    def _validate_name(self, raw: str) -> str:
        name = str(raw).strip()
        if not name:
            raise WritingError("文件名不能为空")
        if len(name) > MAX_FILENAME_CHARS:
            raise WritingError(f"文件名太长（>{MAX_FILENAME_CHARS} 字）")
        if "\x00" in name or "/" in name or "\\" in name or ".." in name:
            raise WritingError("文件名里不能有路径分隔符或 `..`（只能给一个文件名）")
        # 绝对路径/盘符/目录形式一律拒绝（`Path.name != name` 即说明带了目录）
        posix = Path(name)
        if posix.is_absolute() or posix.name != name or (hasattr(posix, "drive") and posix.drive):
            raise WritingError("只接受文件名，不接受路径")
        if not _FILENAME_RE.match(name):
            raise WritingError("文件名只能用中英文/数字/下划线/短横/点/空格")
        suffix = posix.suffix.lower()
        if not suffix:
            name = f"{name}.md"
            suffix = ".md"
        if suffix not in ALLOWED_SUFFIXES:
            raise WritingError(f"只允许写 .md / .txt（收到 {suffix}）")
        return name

    def _auto_name(self) -> str:
        now = self._clock() if self._clock is not None else time.time()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        candidate = f"note-{stamp}.md"
        index = 2
        while (self.root / candidate).exists():
            candidate = f"note-{stamp}-{index}.md"
            index += 1
        return candidate

    def _safe_target(self, name: str) -> Path:
        """最终防线：解析后的父目录必须**恰好是**写作区根目录（防 symlink 逃逸）。"""
        try:
            root = self.root.resolve()
        except OSError as exc:
            raise WritingError(f"写作区不可用：{exc}") from exc
        resolved = (self.root / name).resolve()
        if resolved.parent != root:
            raise WritingError("只能写 data/her_writing/（拒绝路径穿越/符号链接逃逸）")
        return resolved

    def _dir_bytes(self) -> int:
        if not self.root.exists():
            return 0
        total = 0
        for path in self.root.glob("**/*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total
