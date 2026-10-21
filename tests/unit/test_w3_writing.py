"""T-016：她自己的文件夹 `data/her_home/`——目录内完全权限、越界一律拒绝。

用户原话：「给他单独设置一个他的文件夹，他在里面享有完全权限，他产出的东西全部保存在里面。」
本文件测工作区本体（不联网、不碰真 data/）；工具 schema/dispatch 在
`test_t016_her_home.py`。

取消测试：
- 去掉路径穿越防护 ⇒ `test_escape_attempts_are_rejected` 红；
- 去掉容量上限 ⇒ `test_size_caps` 红；
- 把根目录改回 her_writing ⇒ `test_default_root_is_data_her_home` 红。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from elysia.soul.tools.writing import (
    DEFAULT_WRITING_ROOT,
    WritingError,
    WritingWorkspace,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gitignore_excludes_her_home_dir() -> None:
    lines = {
        line.strip() for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    assert "data/her_home/" in lines, "她自己的文件夹必须被 .gitignore 排除（运行时数据）"


def test_default_root_is_data_her_home() -> None:
    assert DEFAULT_WRITING_ROOT.name == "her_home"
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


def test_subdirectories_and_any_suffix_are_allowed(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path)
    workspace.write_note('{"a": 1}', "2026-10/数据.json")
    workspace.write_note("print(1)", "a/b/c.py")
    workspace.write_note("随便的字节", "图片.png")
    assert (tmp_path / "2026-10" / "数据.json").is_file()
    assert (tmp_path / "a" / "b" / "c.py").read_text(encoding="utf-8").strip() == "print(1)"
    assert (tmp_path / "图片.png").is_file()


def test_auto_filename_is_unique(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path, clock=lambda: 1_000_000.0)
    workspace.write_note("甲", None)
    workspace.write_note("乙", None)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert len(names) == 2, f"自动起名撞车：{names}"


@pytest.mark.parametrize(
    "bad_path",
    [
        "../逃出去.md",
        "..\\逃出去.md",
        "a/../../逃出去.md",
        "/etc/passwd",
        "C:\\Windows\\x.md",
        "..",
        "a/..",
    ],
)
def test_escape_attempts_are_rejected(tmp_path: Path, bad_path: str) -> None:
    workspace = WritingWorkspace(tmp_path)
    with pytest.raises(WritingError):
        workspace.write_note("想逃出去", bad_path)
    # 目录外不得出现任何新文件（tmp_path 是工作区根；上级目录里也不许有"逃出去.md"）
    assert not (tmp_path.parent / "逃出去.md").exists()


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "home"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        os.symlink(outside, root / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本机不允许创建符号链接（Windows 权限），跳过逃逸用例")
    workspace = WritingWorkspace(root)
    with pytest.raises(WritingError, match=r"越界|符号链接"):
        workspace.write_note("逃出去", "link/evil.txt")
    assert not (outside / "evil.txt").exists()


def test_empty_text_is_rejected(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path)
    with pytest.raises(WritingError, match="为空"):
        workspace.write_note("   ", "empty.md")
    assert not (tmp_path / "empty.md").exists()


def test_size_caps(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path, max_file_bytes=100)
    with pytest.raises(WritingError, match="单文件上限"):
        workspace.write_note("字" * 40, "big.md")  # 120 字节 > 100
    assert not (tmp_path / "big.md").exists(), "超限必须不写（不静默截断）"

    workspace2 = WritingWorkspace(tmp_path / "dir", max_file_bytes=1000, max_total_bytes=150)
    workspace2.write_note("字" * 25, "a.md")  # 75 字节
    with pytest.raises(WritingError, match="目录上限"):
        workspace2.write_note("字" * 30, "b.md")  # 累计 165 > 150
    assert not (tmp_path / "dir" / "b.md").exists()
    assert (tmp_path / "dir" / "a.md").is_file(), "超限不得破坏已有内容"


def test_list_read_delete_rename(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path)
    workspace.write_note("第一行\n第二行", "2026-10/随笔.md")
    listing = workspace.list_files("")
    assert "2026-10/随笔.md" in listing and "字节" in listing
    assert workspace.read_file("2026-10/随笔.md").strip() == "第一行\n第二行"

    receipt = workspace.rename_file("2026-10/随笔.md", "改名后.md")
    assert "已改名" in receipt
    assert not (tmp_path / "2026-10" / "随笔.md").exists()
    assert (tmp_path / "2026-10" / "改名后.md").read_text(encoding="utf-8").strip() == (
        "第一行\n第二行"
    )

    receipt = workspace.delete_file("2026-10/改名后.md")
    assert "已删掉" in receipt
    assert "改名后.md" not in workspace.list_files("")


def test_delete_refuses_non_empty_directory(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path)
    workspace.write_note("x", "d/a.md")
    with pytest.raises(WritingError, match="不是空目录"):
        workspace.delete_file("d")


def test_rename_refuses_overwrite(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path)
    workspace.write_note("a", "a.md")
    workspace.write_note("b", "b.md")
    with pytest.raises(WritingError, match="已经存在"):
        workspace.rename_file("a.md", "b.md")


def test_read_large_file_is_truncated_with_note(tmp_path: Path) -> None:
    workspace = WritingWorkspace(tmp_path)
    workspace.write_note("字" * 5000, "long.md")
    text = workspace.read_file("long.md")
    assert "已截断" in text
    assert len(text) < 5000


def test_write_does_not_delete_or_touch_other_files(tmp_path: Path) -> None:
    keep = tmp_path / "unrelated.txt"
    keep.write_text("别动我", encoding="utf-8")
    workspace = WritingWorkspace(tmp_path)
    workspace.write_note("新笔记", "new.md")
    assert keep.read_text(encoding="utf-8") == "别动我"
    assert (tmp_path / "new.md").is_file()
