"""她自己的文件夹（T-016 / 用户 2026-10-21）：「她在里面享有完全权限，产物全部保存在里面」。

目录：`data/her_home/`（`.gitignore` 已排除；旧 `data/her_writing/` 的内容已迁入）。

权限（"完全" = 目录内全部；越界一律拒绝）：
- 可读写**任意文件类型**（不再只限 .md/.txt）；
- 可建子目录（`2026-10/随笔.md` 这种相对路径）；
- 可列出、读取、改名/移动、删除她自己的文件；
- 硬边界：`..`、绝对路径/盘符、符号链接逃逸 ⇒ 一律 `WritingError`；根目录外一个字节都不写；
- 容量保护：单文件 ≤ 16 MB、目录总计 ≤ 512 MB（超了明确回执，不静默截断）。

写盘/遍历在调用方用 `asyncio.to_thread` 跑，不阻塞心跳。外部依赖：仅标准库。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from elysia.core.config import PROJECT_ROOT

HER_HOME_DIR_NAME = "her_home"
DEFAULT_WRITING_ROOT = PROJECT_ROOT / "data" / HER_HOME_DIR_NAME

MAX_FILE_BYTES = 16 * 1024 * 1024  # 单文件 16 MB
MAX_TOTAL_BYTES = 512 * 1024 * 1024  # 目录总计 512 MB
MAX_FILENAME_CHARS = 120
MAX_LIST_ENTRIES = 200
MAX_READ_CHARS = 4000
MAX_DEPTH = 12

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class WritingError(Exception):
    """写不了/读不了（原因必须可读，直接回给她的工具回执）。"""


class WritingWorkspace:
    """她自己的文件夹（默认 `data/her_home/`；测试可注入临时根目录）。"""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else DEFAULT_WRITING_ROOT
        self._max_file_bytes = int(max_file_bytes)
        self._max_total_bytes = int(max_total_bytes)
        self._clock = clock  # 测试可注入固定时间（起默认文件名用）

    # ── 对外 API（全部是"她自己的动作"）──────────────────────
    def write_note(self, text: str, path: str | None = None) -> str:
        """把 `text` 追加/新建到她的文件夹；支持子目录与任意后缀。失败抛 WritingError。"""
        if not isinstance(text, str) or not text.strip():
            raise WritingError("正文为空——没有可写的内容")
        payload = text.encode("utf-8")
        if len(payload) > self._max_file_bytes:
            raise WritingError(
                f"太长：{len(payload)} 字节 > 单文件上限 {self._max_file_bytes} 字节（未写入）"
            )
        relative = str(path).strip() if path else self._auto_name()
        target = self._safe_path(relative)
        if target.exists() and target.is_dir():
            raise WritingError(f"`{relative}` 是个目录，不能当文件写")
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
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            separator = "\n\n" if existing else ""
            with target.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(separator + text.rstrip("\n") + "\n")
        except OSError as exc:
            raise WritingError(f"写不进去：{exc}") from exc
        size = target.stat().st_size
        shown = self._display(target)
        return f"（已写进你的文件夹：{shown}；本次 {len(text)} 字，文件 {size} 字节）"

    def list_files(self, path: str = "") -> str:
        """列出她的文件夹（名字、大小、时间）；目录为空时如实说空。"""
        target = self._safe_path(path, must_exist=True, allow_root=True)
        if not target.is_dir():
            raise WritingError(f"`{path}` 不是目录")
        entries: list[str] = []
        for item in sorted(target.rglob("*")):
            if len(entries) >= MAX_LIST_ENTRIES:
                entries.append(f"……（条目太多，只列前 {MAX_LIST_ENTRIES} 条）")
                break
            rel = item.relative_to(self.root.resolve()).as_posix()
            if item.is_dir():
                entries.append(f"[目录] {rel}/")
            else:
                try:
                    stat = item.stat()
                except OSError:
                    continue
                stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
                entries.append(f"{rel}  ({stat.st_size} 字节，{stamp})")
        if not entries:
            return "（你的文件夹现在是空的——想写什么都可以放进来）"
        return "（你的文件夹里：\n" + "\n".join(entries) + "）"

    def read_file(self, path: str) -> str:
        """读回她的文件（大文件截断到 4000 字并注明"已截断"）。"""
        target = self._safe_path(path, must_exist=True)
        if not target.is_file():
            raise WritingError(f"`{path}` 不是文件")
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise WritingError(f"读不出来：{exc}") from exc
        if len(text) > MAX_READ_CHARS:
            return text[:MAX_READ_CHARS] + f"\n……（已截断，原文件 {len(text)} 字）"
        return text

    def delete_file(self, path: str) -> str:
        """删她自己文件（只删文件或**空**目录）；删前给明确回执。"""
        lexical = self._safe_path(path, must_exist=True)
        if lexical.is_symlink():
            raise WritingError("不操作符号链接（避免误删链接指向的东西）")
        if lexical.is_dir():
            if any(lexical.iterdir()):
                raise WritingError(f"`{path}` 不是空目录——先清空里面的东西再删")
            lexical.rmdir()
            return f"（已删掉空目录：{self._display(lexical)}）"
        if not lexical.is_file():
            raise WritingError(f"`{path}` 既不是文件也不是目录")
        lexical.unlink()
        return f"（已删掉：{self._display(lexical)}）"

    def rename_file(self, path: str, new_name: str) -> str:
        """改名/移动（同目录内只改名字）；不许覆盖已有文件。"""
        source = self._safe_path(path, must_exist=True)
        if source.is_symlink():
            raise WritingError("不操作符号链接（避免移走链接指向的东西）")
        name = str(new_name or "").strip()
        if not name:
            raise WritingError("新名字不能为空")
        if (
            name in (".", "..")
            or "/" in name
            or "\\" in name
            or "\x00" in name
            or len(name) > MAX_FILENAME_CHARS
            or _DRIVE_RE.match(name)
        ):
            raise WritingError("新名字只能是一个名字，不能带路径/盘符/`..`")
        destination = self._safe_path(f"{source.relative_to(self.root.resolve()).as_posix()}")
        destination = destination.parent / name
        destination = self._ensure_inside(destination)
        if destination.exists():
            raise WritingError(f"`{name}` 已经存在——不覆盖（换个名字或先删掉）")
        try:
            source.rename(destination)
        except OSError as exc:
            raise WritingError(f"改不了名：{exc}") from exc
        return f"（已改名：{self._display(source)} → {self._display(destination)}）"

    # ── 内部：路径与容量 ─────────────────────────────────────
    def _normalize_parts(self, raw: str | None) -> list[str]:
        text = str(raw or "").strip()
        if not text:
            raise WritingError("路径不能为空")
        if "\x00" in text:
            raise WritingError("路径里有非法字符")
        normalized = text.replace("\\", "/")
        if normalized.startswith("/") or _DRIVE_RE.match(normalized):
            raise WritingError("不接受绝对路径——只给 data/her_home/ 内的相对路径")
        parts: list[str] = []
        for part in PurePosixPath(normalized).parts:
            if part in ("", "."):
                continue
            if part == "..":
                raise WritingError("路径里不能有 `..`（不许逃出你的文件夹）")
            if len(part) > MAX_FILENAME_CHARS:
                raise WritingError(f"名字太长（>{MAX_FILENAME_CHARS} 字）：{part[:20]}…")
            parts.append(part)
        if len(parts) > MAX_DEPTH:
            raise WritingError(f"目录太深（最多 {MAX_DEPTH} 层）")
        return parts

    def _root_dir(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root.resolve()

    def _ensure_inside(self, candidate: Path) -> Path:
        root = self._root_dir()
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise WritingError(f"路径不可用：{exc}") from exc
        if resolved != root and root not in resolved.parents:
            raise WritingError("越界：只能访问 data/her_home/（拒绝路径穿越/符号链接逃逸）")
        return resolved

    def _safe_path(
        self,
        raw: str | None,
        *,
        must_exist: bool = False,
        allow_root: bool = False,
    ) -> Path:
        parts = self._normalize_parts(raw) if str(raw or "").strip() else []
        root = self._root_dir()
        if not parts:
            if allow_root:
                return root
            raise WritingError("路径不能为空")
        target = self._ensure_inside(self.root.joinpath(*parts))
        if must_exist and not target.exists():
            raise WritingError(f"没有这个文件/目录：{str(raw).strip()}")
        return target

    def _auto_name(self) -> str:
        now = self._clock() if self._clock is not None else time.time()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        candidate = f"note-{stamp}.md"
        index = 2
        while (self.root / candidate).exists():
            candidate = f"note-{stamp}-{index}.md"
            index += 1
        return candidate

    def _display(self, target: Path) -> str:
        try:
            return target.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return target.name

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
