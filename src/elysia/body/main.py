"""身体进程入口：随设备在场的 5s 心跳。

启动流程：ensure_dirs → 日志 → 双库启动 → 心跳循环 → 优雅关闭。

P0-C 扩展：soul.ps1/body.ps1 进程管理 + 开机自启注册（桌宠形态）。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys

from elysia.body.heartbeat import BodyHeartbeat
from elysia.core.clock import SystemClock
from elysia.core.config import Settings, get_settings
from elysia.core.log import get_logger, setup_logging
from elysia.core.state_store import HeartbeatStore, StateStore


async def _run(settings: Settings) -> int:
    setup_logging(level=settings.log_level, log_dir=settings.log_dir)
    log = get_logger("body")
    log.info("body process starting", pid=os.getpid())

    state_store = StateStore(settings.data_dir / "state.db")
    heartbeat_store = HeartbeatStore(settings.data_dir / "heartbeat.db")
    await state_store.start()
    await heartbeat_store.start()

    heartbeat = BodyHeartbeat(
        state_store=state_store,
        heartbeat_store=heartbeat_store,
        clock=SystemClock(),
        interval_s=settings.body_heartbeat_interval_s,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows：add_signal_handler 仅 Unix
            break

    beat_task = asyncio.create_task(heartbeat.run(), name="body-heartbeat")
    try:
        await stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("body stopping：优雅关闭")
        await heartbeat.stop()
        beat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat_task
        await state_store.close()
        await heartbeat_store.close()
        log.info("body stopped")
    return 0


def main() -> int:
    settings = get_settings()
    settings.ensure_dirs()
    try:
        return asyncio.run(_run(settings))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
