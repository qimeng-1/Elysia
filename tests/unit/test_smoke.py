"""冒烟测试：包可导入、版本可读、基础环境就绪。

用于验证工程骨架完整性（清单 #6），P0 业务测试在此基础上扩展。
"""

from __future__ import annotations

import elysia


def test_package_importable() -> None:
    assert elysia.__version__ == "0.1.0"


def test_core_modules_present() -> None:
    """src layout 五个模块目录均已就位（占位，P0 实现后充实）。"""
    import importlib

    for module in ("soul", "body", "core", "protocol", "utils"):
        assert importlib.util.find_spec(f"elysia.{module}") is not None
