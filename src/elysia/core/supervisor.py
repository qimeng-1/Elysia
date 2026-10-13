"""常驻协程监督器：异常不致命，记日志后重启。

**为什么需要它（F6 的对偶件）**：
`_AsyncSQLite` 修好 F6 之后，**写库失败不再伪装成成功**，而是向上抛出——
这是"诚实"的一半。但另一半是：抛出的异常**不能杀死她的生命循环**
（旧行为 `fut.set_result(None)` 至少让进程活着；若只是改成抛异常而不加保护，
就变成"静默丢数据"换成"静默僵死"，更糟）。

因此：状态库负责**说真话**（失败就是失败），本模块负责**她不死**
（单拍/整循环异常 → 记日志 → 重启继续跑）。

顺带覆盖 19.4 缺点 1「进程无自愈」里**程序自身能解决的那一半**：
心跳协程意外退出不再等于"她死了"，而是被拉起来继续活。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from elysia.core.log import get_logger

log = get_logger("supervisor")

# 重启前的等待：避免"一崩就重启"打转（也给外部依赖一点恢复时间）
RESTART_DELAY_S = 1.0


async def supervise(
    factory: Callable[[], Awaitable[None]],
    name: str,
    *,
    restart_delay_s: float = RESTART_DELAY_S,
    on_error: Callable[[], None] | None = None,
) -> None:
    """跑一个常驻协程；抛异常则记日志并重启，直到它**正常返回**或被取消。

    - 正常返回（如心跳 `stop()` 后退出）→ 监督结束，不重启；
    - `CancelledError` → 原样抛出（关闭序要靠它收干净）；
    - 其它异常 → 记 exception 日志，调用 `on_error`（供「痛」等观测计数），
      等 `restart_delay_s` 再拉起。

    `on_error` 是**观测回调**：它自己失败不得影响重启（已兜住）。
    """
    while True:
        try:
            await factory()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("常驻协程异常退出，%.1fs 后重启", restart_delay_s, task=name)
            if on_error is not None:
                with contextlib.suppress(Exception):
                    on_error()
            await asyncio.sleep(restart_delay_s)
