"""T-016 工具层：她自己的文件夹里的五个动作（write/list/read/delete/rename）。

工具表里的 schema、dispatch 名字、收据都要对得上；所有动作只在 `data/her_home/`
内生效（测试用 tmp_path 注入，不写真的 data/）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elysia.llm.chain import (
    DELETE_MY_FILE_TOOL,
    LIST_MY_FILES_TOOL,
    READ_MY_FILE_TOOL,
    RENAME_MY_FILE_TOOL,
    WRITE_NOTE_TOOL,
)
from elysia.soul.brain import BrainOutput
from elysia.soul.expression_service import ExpressionService
from elysia.soul.tools.writing import WritingWorkspace


def _runner(workspace: WritingWorkspace):
    service = ExpressionService(
        None,  # type: ignore[arg-type]  # 只测工具执行器，不碰 LLM
        None,  # type: ignore[arg-type]
        writing_workspace=workspace,
    )
    return service._make_tool_runner(BrainOutput(), now=1.0, user_message=None)


def test_tool_schemas_and_names() -> None:
    expected = {
        "write_note": WRITE_NOTE_TOOL,
        "list_my_files": LIST_MY_FILES_TOOL,
        "read_my_file": READ_MY_FILE_TOOL,
        "delete_my_file": DELETE_MY_FILE_TOOL,
        "rename_my_file": RENAME_MY_FILE_TOOL,
    }
    for name, schema in expected.items():
        assert schema["function"]["name"] == name
        description = schema["function"]["description"]
        assert "你自己的" in description or "你自己" in description, description
        assert "文件夹" in description, description
    assert "path" in WRITE_NOTE_TOOL["function"]["parameters"]["properties"]
    assert set(READ_MY_FILE_TOOL["function"]["parameters"]["required"]) == {"path"}
    assert set(RENAME_MY_FILE_TOOL["function"]["parameters"]["required"]) == {"path", "new_name"}


@pytest.mark.asyncio
async def test_write_note_tool_supports_subdir_and_any_suffix(tmp_path: Path) -> None:
    runner = _runner(WritingWorkspace(tmp_path))
    receipt = await runner("write_note", {"text": '{"ok": true}', "path": "2026-10/配置.json"})
    assert "已写" in receipt
    assert (tmp_path / "2026-10" / "配置.json").read_text(
        encoding="utf-8"
    ).strip() == '{"ok": true}'

    receipt = await runner("write_note", {"text": "print('hi')", "path": "a/b/c.py"})
    assert "已写" in receipt
    assert (tmp_path / "a" / "b" / "c.py").is_file()


@pytest.mark.asyncio
async def test_write_note_legacy_filename_still_works(tmp_path: Path) -> None:
    runner = _runner(WritingWorkspace(tmp_path))
    receipt = await runner("write_note", {"text": "旧参数", "filename": "legacy.md"})
    assert "已写" in receipt
    assert (tmp_path / "legacy.md").is_file()


@pytest.mark.asyncio
async def test_escape_attempts_return_explicit_error(tmp_path: Path) -> None:
    runner = _runner(WritingWorkspace(tmp_path))
    for bad in ("../逃出去.md", "/etc/passwd", "C:\\Windows\\x.md"):
        receipt = await runner("write_note", {"text": "想逃出去", "path": bad})
        assert receipt.startswith("（write_note 写不了："), receipt
        assert not (tmp_path.parent / "逃出去.md").exists()


@pytest.mark.asyncio
async def test_empty_text_and_size_caps_return_explicit_error(tmp_path: Path) -> None:
    runner = _runner(WritingWorkspace(tmp_path))
    receipt = await runner("write_note", {"text": "   ", "path": "empty.md"})
    assert "写不了" in receipt and "为空" in receipt
    assert not (tmp_path / "empty.md").exists()

    small = _runner(WritingWorkspace(tmp_path / "small", max_file_bytes=10))
    receipt = await small("write_note", {"text": "字" * 20, "path": "big.md"})
    assert "写不了" in receipt and "上限" in receipt
    assert not (tmp_path / "small" / "big.md").exists()


@pytest.mark.asyncio
async def test_list_read_rename_delete_via_tools(tmp_path: Path) -> None:
    runner = _runner(WritingWorkspace(tmp_path))
    await runner("write_note", {"text": "一段话", "path": "notes/随笔.md"})

    listing = await runner("list_my_files", {"path": ""})
    assert "notes/随笔.md" in listing and "字节" in listing

    content = await runner("read_my_file", {"path": "notes/随笔.md"})
    assert content.strip() == "一段话"

    renamed = await runner("rename_my_file", {"path": "notes/随笔.md", "new_name": "改名.md"})
    assert "已改名" in renamed
    assert not (tmp_path / "notes" / "随笔.md").exists()
    assert (tmp_path / "notes" / "改名.md").is_file()

    deleted = await runner("delete_my_file", {"path": "notes/改名.md"})
    assert "已删掉" in deleted
    assert "改名.md" not in await runner("list_my_files", {})


@pytest.mark.asyncio
async def test_tool_errors_are_explicit(tmp_path: Path) -> None:
    runner = _runner(WritingWorkspace(tmp_path))
    assert (await runner("read_my_file", {"path": "不存在.md"})).startswith(
        "（read_my_file 读不了："
    )
    assert (await runner("delete_my_file", {"path": "不存在.md"})).startswith(
        "（delete_my_file 删不了："
    )
    assert (await runner("rename_my_file", {"path": "不存在.md", "new_name": "x.md"})).startswith(
        "（rename_my_file 改不了："
    )


def test_migration_target_exists_and_gitignored() -> None:
    """迁移后的真实目录：data/her_home/ 存在且被 gitignore 排除（旧目录已删除）。"""
    repo_root = Path(__file__).resolve().parents[2]
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
    assert "data/her_home/" in gitignore
    assert (repo_root / "data" / "her_home").is_dir()
    assert not (repo_root / "data" / "her_writing").exists()
