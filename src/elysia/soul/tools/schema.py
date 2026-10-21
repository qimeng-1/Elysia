"""D-15：工具参数的最小 JSON Schema + 执行前校验（provider 无关）。

背景：这条链出过两次事故，根子都是"参数结构坏了还照样执行"——
1) `arguments` 被 `max_tokens` 截断成半截 JSON → 旧实现静默当 `{}` 执行（T-014 已改成
   回"截断"回执、不执行）；
2) 参数缺字段/正文只有空白 → 被当空字符串递给工具，她以为写了、其实没写。

本模块只做**执行前**的结构校验：不合法 ⇒ **不执行**，回一条明确回执让模型补全后
用同样的工具再试（自然用满现有工具轮）；仍不合法就正常说话——绝不死循环，
**绝不再把缺字段当空字符串执行**。

schema 是"最小"的：只约束她真正会用的字段；未知字段放行（向前兼容）。
真正的安全边界（写盘路径/容量/白名单）仍由工具自身把关——这里只是**早失败**。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda value: isinstance(value, str),
    # bool 是 int 的子类，必须单独判，否则 True 会被当成 integer
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
}

_MEMORY_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"topic": {"type": "string"}},
}

# 她真正会用的工具：严格校验（缺字段/类型/空白/越界路径都在执行前挡住）。
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "write_note": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "x-non-blank": True},
            "path": {"type": "string", "x-forbid-parent": True},
        },
        "required": ["text"],
    },
    "read_url": {
        "type": "object",
        "properties": {"url": {"type": "string", "x-non-blank": True}},
        "required": ["url"],
    },
    "list_my_files": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    },
    "read_my_file": {
        "type": "object",
        "properties": {"path": {"type": "string", "x-non-blank": True}},
        "required": ["path"],
    },
    "delete_my_file": {
        "type": "object",
        "properties": {"path": {"type": "string", "x-non-blank": True}},
        "required": ["path"],
    },
    "rename_my_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "x-non-blank": True},
            "new_name": {"type": "string", "x-non-blank": True},
        },
        "required": ["path", "new_name"],
    },
    "keep_this_moment": {
        "type": "object",
        "properties": {"reason": {"type": "string", "x-non-blank": True}},
        "required": ["reason"],
    },
    "set_intention": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "x-non-blank": True},
            "action": {"type": "string", "enum": ["read_url", "write_note"]},
            "target": {"type": "string"},
            "kind": {"type": "string", "enum": ["task", "surprise", "conceal", "play"]},
        },
        "required": ["goal", "action"],
    },
    # 记忆类工具：只校验类型（"没给 topic" 是执行器本来就有兜底的合法输入）
    "recall": _MEMORY_TOOL_SCHEMA,
    "adopt": _MEMORY_TOOL_SCHEMA,
    "disclaim": _MEMORY_TOOL_SCHEMA,
    "forget": _MEMORY_TOOL_SCHEMA,
    "restore": _MEMORY_TOOL_SCHEMA,
}


def _iter_checks(tool: str) -> tuple[dict[str, Any], list[str]]:
    schema = TOOL_SCHEMAS.get(tool) or {}
    properties = schema.get("properties") or {}
    required = [str(key) for key in (schema.get("required") or [])]
    return properties, required


def validate_tool_args(tool: str, args: dict[str, Any]) -> tuple[bool, str | None]:
    """执行前校验工具参数：`(ok, reason)`；ok=False 时 reason 可直接给模型看。

    没有 schema 的工具（未知/未来新增）一律放行——本模块只负责"她真会用的那些"。
    """
    properties, required = _iter_checks(tool)
    if not properties and not required:
        return True, None
    for key in required:
        if args.get(key) is None:
            return False, f"缺少必填字段 {key}"
    for key, spec in properties.items():
        if key not in args or args[key] is None:
            continue
        value = args[key]
        expected = spec.get("type")
        if expected is not None:
            checker = _TYPE_CHECKS.get(str(expected))
            if checker is not None and not checker(value):
                return False, f"{key} 类型不对（要 {expected}，实际 {type(value).__name__}）"
        if isinstance(value, str):
            if spec.get("x-non-blank") and not value.strip():
                return False, f"{key} 是空白（空白不算有效内容）"
            if spec.get("x-forbid-parent") and ".." in value:
                return False, f"{key} 不许包含 `..`（不许逃出她的文件夹）"
        allowed = spec.get("enum")
        if allowed is not None and value not in allowed:
            return False, f"{key} 只能取 {' / '.join(str(item) for item in allowed)}"
    return True, None


def invalid_args_receipt(reason: str) -> str:
    """参数不合法时回给模型的回执（明确"没有执行" + 怎么重试）。"""
    return f"（工具参数不合法：{reason}；没有执行，请补全后用同样的工具再试一次）"
