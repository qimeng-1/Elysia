"""W3：她的写作工作区 `data/her_writing/`——路径穿越/大小上限/只追加（不联网）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from elysia.soul.tools.writing import (
    DEFAULT_WRITING_ROOT,
    WritingError,
    WritingWorkspace,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gitignore_excludes_her_writing_dir() -> None:
    lines = {
        line.strip() for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    assert "data/her_writing/" in lines, "写作工作区必须被 .gitignore 排除（运行时数据）"


def test_default_root_is_data_her_writing() -> None:
    assert DEFAULT_WRITING_ROOT.name == "her_writing"
    assert DEFAULT_WRITING_ROOT.parent.name == "data"
    assert WritingWorkspace().root == DEFAULT_WRITING_ROOT


def test_write_note_creates_file_and_reports(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path)
    receipt = workspace.write_note("今天想记住她笑起来的样子。", "note.md")
    target = tmp_path / "note.md"
    assert target.is_file()
    assert target.read_text(encoding="utf-8").strip() == "今天想记住她笑起来的样子。"
    assert "note.md" in receipt and "已写" in receipt


def test_write_note_appends_and_never_overwrites(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path)
    workspace.write_note("第一段。", "记忆.md")
    workspace.write_note("第二段。", "记忆.md")
    content = (tmp_path / "记忆.md").read_text(encoding="utf-8")
    assert "第一段。" in content and "第二段。" in content
    assert content.index("第一段。") < content.index("第二段。")


def test_auto_filename_is_unique(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path, clock=lambda: 1_000_000.0)
    workspace.write_note("甲", None)
    workspace.write_note("乙", None)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert len(names) == 2, f"自动起名撞车：{names}"


@pytest.mark.parametrize(
    "bad_name",
    [
        "../逃出去.md",
        "..\\逃出去.md",
        "sub/目录.md",
        "sub\\目录.md",
        "/etc/passwd",
        "C:\\Windows\\x.md",
        "..",
        ".",
        "a/../b.md",
    ],
)
def test_path_traversal_is_rejected(tmp_path: Path, bad_name: str) -> None:
    workspace = WritingWorkspace(tmp_path)
    with pytest.raises(WritingError):
        workspace.write_note("想逃出去", bad_name)
    # 目录外不得出现任何新文件
    assert [p.name for p in tmp_path.iterdir()] == []


def test_suffix_is_restricted_and_defaults_to_md(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path)
    workspace.write_note("没有后缀", "随手一记")
    assert (tmp_path / "随手一记.md").is_file()
    with pytest.raises(WritingError):
        workspace.write_note("写代码？", "evil.py")


def test_single_file_size_cap(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path, max_file_bytes=100)
    with pytest.raises(WritingError, match="单文件上限"):
        workspace.write_note("字" * 40, "big.md")  # 120 字节 > 100
    assert not (tmp_path / "big.md").exists(), "超限必须不写（不静默截断）"


def test_directory_total_size_cap(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path, max_file_bytes=1000, max_total_bytes=150)
    workspace.write_note("字" * 25, "a.md")  # 75 字节
    with pytest.raises(WritingError, match="目录上限"):
        workspace.write_note("字" * 30, "b.md")  # 累计 165 > 150
    assert not (tmp_path / "b.md").exists()
    assert (tmp_path / "a.md").is_file(), "超限不得破坏已有内容"


def test_write_does_not_delete_or_touch_other_files(tmp_path: Path) -> None:
    keep = tmp_path / "unrelated.txt"
    keep.write_text("别动我", encoding="utf-8")
    workspace = WritingWorkspace(tmp_path)
    workspace.write_note("新笔记", "new.md")
    assert keep.read_text(encoding="utf-8") == "别动我"
    assert (tmp_path / "new.md").is_file()


def test_empty_text_is_rejected(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path)
    with pytest.raises(WritingError, match="为空"):
        workspace.write_note("   ", "empty.md")
    assert not (tmp_path / "empty.md").exists()
