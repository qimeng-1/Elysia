"""她的运行时工具（W3）：只读 `read_url` + 写作工作区 `write_note`。

两者都经表达链路的 function-calling 递给她——**用不用由她自己决定**（C 方案）。
放在 `soul/tools/` 而不是 `elysia/tools/`：后者是 C 契约窗的**开发/观测工具集**
（memory_view，不属于灵魂运行时），语义与写作用域都不同。
"""

from __future__ import annotations

from elysia.soul.tools.read_url import (
    EXTERNAL_DATA_MARKER,
    MAX_TEXT_CHARS,
    READ_TIMEOUT_S,
    ReadResult,
    is_allowed_url,
    read_url,
)
from elysia.soul.tools.writing import (
    DEFAULT_WRITING_ROOT,
    HER_HOME_DIR_NAME,
    WritingError,
    WritingWorkspace,
)

__all__ = [
    "DEFAULT_WRITING_ROOT",
    "EXTERNAL_DATA_MARKER",
    "HER_HOME_DIR_NAME",
    "MAX_TEXT_CHARS",
    "READ_TIMEOUT_S",
    "ReadResult",
    "WritingError",
    "WritingWorkspace",
    "is_allowed_url",
    "read_url",
]
