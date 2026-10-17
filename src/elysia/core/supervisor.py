"""常驻协程监督器：异常不致命，记日志后重启（带**指数退避**与**熔断**）。

**为什么需要它（F6 的对偶件）**：
`_AsyncSQLite` 修好 F6 之后，**写库失败不再伪装成成功**，而是向上抛出——
这是"诚实"的一半。但另一半是：抛出的异常**不能杀死她的生命循环**
（旧行为 `fut.set_result(None)` 至少让进程活着；若只是改成抛异常而不加保护，
就变成"静默丢数据"换成"静默僵死"，更糟）。

因此：状态库负责**说真话**（失败就是失败），本模块负责**她不死**
（单拍/整循环异常 → 记日志 → 重启继续跑）。

顺带覆盖 19.4 缺点 1「进程无自愈」里**程序自身能解决的那一半**：
心跳协程意外退出不再等于"她死了"，而是被拉起来继续活。

P0-R4（D-5）新增：**退避 + 熔断**。审计实测旧写法固定 `RESTART_DELAY_S = 1.0`
且无上限 ⇒ 若异常是**持久的**（库损坏 / 磁盘满 / 内存不足），她会
**每 1 秒崩一次、无限循环**：日志被刷爆、她一直不前进，而看门狗只看到"进程在"，
永远不会介入。现在：

- **指数退避**：1s → 2s → 4s → … 上限 `MAX_RESTART_DELAY_S`（60s）。
  给外部依赖恢复时间，也避免把日志刷爆。
- **熔断**：连续失败达到 `MAX_CONSECUTIVE_FAILURES`（10）⇒ **主动退出进程**，
  让看门狗接管（"自杀比僵死好"——退避到分钟级仍失败，说明是持久故障，
  再重启也只是徒劳地消耗她的时间）。
- **成功会重置计数**：只要上一段**稳定跑了** `RESET_AFTER_S`（60s）才崩，
  就视为新问题、计数归零（不把半小时前的那次偶发崩溃算进这一次）。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Awaitable, Callable

from elysia.core.log import get_logger

log = get_logger("supervisor")

# 首次重启前的等待：避免"一崩就重启"打转（也给外部依赖一点恢复时间）
RESTART_DELAY_S = 1.0
# 退避上限：持久故障时不要退到"几小时后再试"——那等于放弃她
MAX_RESTART_DELAY_S = 60.0
# 连续失败多少次判定为**持久故障** ⇒ 熔断（让看门狗接管，而不是自己无限重启）
MAX_CONSECUTIVE_FAILURES = 10
# 上一段跑够这么久才崩 ⇒ 视为"新问题"，连续失败计数归零
RESET_AFTER_S = 60.0


class SupervisorFatal(RuntimeError):
    """熔断：连续失败达到上限，进程必须退出让看门狗接管（"自杀比僵死好"）。"""


def _restart_delay(restart_delay_s: float, failures: int, max_delay_s: float) -> float:
    """第 `failures` 次连续失败后的等待秒数（指数退避，带上限）。"""
    try:
        return float(min(restart_delay_s * (2 ** (failures - 1)), max_delay_s))
    except OverflowError:  # 失败次数大到 2**n 溢出（理论上到不了，兜住）
        return float(max_delay_s)


async def supervise(
    factory: Callable[[], Awaitable[None]],
    name: str,
    *,
    restart_delay_s: float = RESTART_DELAY_S,
    max_delay_s: float = MAX_RESTART_DELAY_S,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
    reset_after_s: float = RESET_AFTER_S,
    on_error: Callable[[], None] | None = None,
    on_fatal: Callable[[str, int], None] | None = None,
) -> None:
    """跑一个常驻协程；抛异常则记日志并按**指数退避**重启，直到它正常返回或被取消。

    - 正常返回（如心跳 `stop()` 后退出）→ 监督结束，不重启；
    - `CancelledError` → 原样抛出（关闭序要靠它收干净）；
    - 其它异常 → 记 exception 日志、调用 `on_error`（供「痛」等观测计数）、
      按第 N 次连续失败退避后重启；
    - 连续失败达到 `max_consecutive_failures` ⇒ **熔断**：默认 `os._exit(1)`
      让看门狗接管（`main()` 只等 stop 事件，不会去观察本任务的异常，
      所以"主动退出"必须是真的退出进程，否则她会僵在原地）。
      验收/测试可注入 `on_fatal(name, failures)`：注入后改为抛 `SupervisorFatal`，
      这样"熔断已触发"是可断言的，而不会把测试进程一起杀掉。

    `on_error` 是**观测回调**：它自己失败不得影响重启（已兜住）。
    """
    failures = 0
    while True:
        started = time.monotonic()
        try:
            await factory()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            lived_s = time.monotonic() - started
            if reset_after_s > 0 and lived_s >= reset_after_s:
                # 上一段活得够久 ⇒ 这次是新问题，别把旧账算进来
                failures = 0
            failures += 1
            delay = _restart_delay(restart_delay_s, failures, max_delay_s)
            log.exception(
                "常驻协程异常退出（第 %d 次连续失败；已存活 %.1fs；%.1fs 后重启）",
                failures,
                lived_s,
                delay,
                task=name,
            )
            if on_error is not None:
                with contextlib.suppress(Exception):
                    on_error()
            if max_consecutive_failures > 0 and failures >= max_consecutive_failures:
                log.critical(
                    "连续失败 %d 次 ⇒ 熔断：主动退出进程让看门狗接管（自杀比僵死好）",
                    failures,
                    task=name,
                )
                if on_fatal is not None:
                    with contextlib.suppress(Exception):
                        on_fatal(name, failures)
                    raise SupervisorFatal(
                        f"{name}: 连续 {failures} 次失败，已熔断（进程应由看门狗拉起）"
                    ) from None
                # 生产路径：真的退出进程。`os._exit` 不做清理——现场已由
                # soul/body 的 crash handler 与 WAL 保证，这里要的是"立刻死、别僵着"。
                os._exit(1)
            await asyncio.sleep(delay)
