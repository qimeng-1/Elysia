"""P0-R13（可迁移性）单测：**把工程搬到另一个路径，它仍然是她**。

这条测试只验"程序能在新路径跑"与"配置不再指向旧机器的绝对路径"，
**绝不启动任何进程的副本** —— 启动复制品 = 双实例 = 分叉（红线 #2），测试里也不许做。

取消测试：`test_project_runs_from_any_path`（还原任一处硬编码 ⇒ 红）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 在"搬过去的副本"里跑的探针（独立进程；脚本路径由环境变量传入）
_PROBE = r"""
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))

from elysia.core.config import PROJECT_ROOT, Settings  # noqa: E402

assert PROJECT_ROOT == root, f"PROJECT_ROOT 应随文件位置走：{PROJECT_ROOT} != {root}"
settings = Settings()
assert settings.data_dir == root / "data"
assert settings.backup_dir == root.parent / "_backup", settings.backup_dir
assert settings.tts_launcher is None, "未配置时 tts_launcher 必须是 None，不得写死本机路径"
assert settings.bake_dir_path is None

# 源码里不得再有本机绝对路径
for rel in (
    "src/elysia/core/config.py",
    "scripts/elysia_panel.py",
    "scripts/elysia_console.py",
    "scripts/spine_check_all.py",
):
    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    for bad in ("D:\\", "D:/", "C:\\Elysia", "C:/Elysia"):
        assert bad not in text, f"{rel} 里仍有硬编码路径 {bad!r}"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, root / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


panel = _load("elysia_panel")
console = _load("elysia_console")
spine = _load("spine_check_all")
assert panel._tts_launcher() is None, "面板：未配置语音脚本时应显示未配置而不是报错"
assert console._tts_launcher() is None
assert spine.default_bake_dir() is None
print("PORTABILITY OK")
"""


def test_project_runs_from_any_path(tmp_path: Path) -> None:
    """把 `src/` 与 `scripts/` 复制到另一处 ⇒ 配置随文件位置走、零硬编码。"""
    dst = tmp_path / "moved" / "Elysia"
    shutil.copytree(REPO_ROOT / "src" / "elysia", dst / "src" / "elysia")
    shutil.copytree(
        REPO_ROOT / "scripts",
        dst / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.bat", "*.md"),
    )
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("ELYSIA_")  # 让副本用"默认配置"（模拟新机器）
    }
    env["PYTHONPATH"] = str(dst / "src")
    result = subprocess.run(
        [sys.executable, str(probe), str(dst)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=str(dst),
        env=env,
    )

    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "PORTABILITY OK" in out
