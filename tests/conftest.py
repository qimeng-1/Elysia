"""pytest 全局配置与共享 fixture。

src layout：包经 uv 安装到 .venv，测试直接 import elysia.*，无需路径 hack。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def tmp_db_path(tmp_path):
    """临时数据库路径（WAL 相关测试复用）。"""
    return tmp_path / "test.db"
