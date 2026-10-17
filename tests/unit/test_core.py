"""core 模块（配置/日志/监督器）单元测试。"""

from __future__ import annotations

import asyncio
import json

import pytest
import structlog

from elysia.core.config import Settings, get_settings
from elysia.core.log import get_logger, setup_logging
from elysia.core.supervisor import SupervisorFatal, supervise


def test_settings_defaults() -> None:
    from elysia.core.config import PROJECT_ROOT

    settings = Settings()
    assert settings.log_level == "INFO"
    assert settings.heartbeat_interval_s == 1.0
    assert settings.body_heartbeat_interval_s == 5.0
    # P0-R13：备份目录不再写死本机路径，而是"仓库上一级的 _backup"（可随仓库搬家）
    assert settings.backup_dir == PROJECT_ROOT.parent / "_backup"
    # 外部程序的路径留空 = 未配置（调用方显示"未配置"，不报错）
    assert settings.tts_launcher is None
    assert settings.bake_dir_path is None


def test_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELYSIA_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ELYSIA_HEARTBEAT_INTERVAL_S", "0.5")
    settings = get_settings()
    assert settings.log_level == "DEBUG"
    assert settings.heartbeat_interval_s == 0.5


def test_settings_ensure_dirs(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "data" / "logs",
        run_dir=tmp_path / "data" / "run",
    )
    settings.ensure_dirs()
    assert (tmp_path / "data" / "logs").is_dir()
    assert (tmp_path / "data" / "run").is_dir()


def test_setup_logging_writes_json(tmp_path) -> None:
    setup_logging(level="DEBUG", log_dir=tmp_path)
    log = get_logger("test")
    log.info("hello", answer=42)
    # 触发 handler flush 后读取文件
    lines = (tmp_path / "elysia.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert record["event"] == "hello"
    assert record["answer"] == 42
    assert record["level"] == "info"
    assert "timestamp" in record


def test_get_logger_default_name() -> None:
    log = get_logger()
    # structlog 首次使用时惰性绑定：bind() 触发构建，返回真 BoundLogger
    assert isinstance(log.bind(), structlog.stdlib.BoundLogger)


# ── 常驻协程监督器（F6 的对偶件）────────────────────────────────


@pytest.mark.asyncio
async def test_supervise_restarts_after_exception() -> None:
    """监督器：抛异常不致命——记日志后重启，直到被监督的协程**正常返回**。

    这正是 F6 修复的另一半：状态库改为一失败就抛（说真话），
    而抛出的异常不得让她的生命循环僵死。
    """
    calls = 0

    async def factory() -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("boom")

    await asyncio.wait_for(supervise(factory, "test", restart_delay_s=0.01), timeout=3.0)
    assert calls == 3, "应重启到第 3 次正常返回为止（正常返回不再重启）"


@pytest.mark.asyncio
async def test_supervise_propagates_cancellation() -> None:
    """取消必须**原样抛出**（关闭序依赖它收干净），不得被当成"异常→重启"。"""
    started = asyncio.Event()

    async def factory() -> None:
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(supervise(factory, "test-cancel", restart_delay_s=0.01))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_supervise_reports_errors_to_callback() -> None:
    """每次兜住异常都要回调 `on_error`（「痛」的计数入口）。

    且**回调自身失败不得影响重启**——观测是旁路，不能把她的命搭进去。
    """
    errors: list[int] = []

    def on_error() -> None:
        errors.append(1)
        raise RuntimeError("观测回调自己炸了")

    calls = 0

    async def factory() -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("boom")

    await asyncio.wait_for(
        supervise(factory, "test-cb", restart_delay_s=0.01, on_error=on_error),
        timeout=3.0,
    )
    assert calls == 3, "回调抛异常不得阻断重启"
    assert len(errors) == 2, f"两次失败应各回调一次，实际 {len(errors)}"


# ── P0-R4（D-5）：指数退避 + 熔断 ─────────────────────────────────────
# 旧写法固定等 1.0s、无上限 ⇒ 持久故障（库损坏/磁盘满/内存不足）会让她
# **每 1 秒崩一次、无限循环**，而看门狗只看到"进程在"，永远不介入。
# 取消测试：
#   - test_supervise_exponential_backoff（去掉退避 ⇒ 延迟恒为 1.0s，用例红）
#   - test_supervise_circuit_breaker_fires（去掉熔断 ⇒ 无限重启，用例超时红）


@pytest.mark.asyncio
async def test_supervise_exponential_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """取消测试（P0-R4）：连续失败必须**指数退避**，而不是每次固定 1 秒。"""
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)
        await real_sleep(0)  # 不真的等，但保留让出控制权

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def always_fail() -> None:
        raise RuntimeError("持久故障")

    fatal: list[tuple[str, int]] = []
    with pytest.raises(SupervisorFatal):
        await asyncio.wait_for(
            supervise(
                always_fail,
                "backoff-test",
                restart_delay_s=1.0,
                max_delay_s=60.0,
                max_consecutive_failures=4,
                reset_after_s=60.0,
                on_fatal=lambda name, n: fatal.append((name, n)),
            ),
            timeout=5.0,
        )

    assert delays == [1.0, 2.0, 4.0], f"退避应是 1s→2s→4s（旧写法恒为 1.0）；实际 {delays}"
    assert fatal == [("backoff-test", 4)]


@pytest.mark.asyncio
async def test_supervise_circuit_breaker_exits_instead_of_restarting_forever() -> None:
    """取消测试（P0-R4）：连续失败达阈值 ⇒ **主动退出**，不是无限重启。

    旧写法会永远重启（本用例在 `wait_for` 超时处变红），而不是走到熔断。
    """
    calls = 0

    async def always_fail() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("持久故障")

    with pytest.raises(SupervisorFatal):
        await asyncio.wait_for(
            supervise(
                always_fail,
                "breaker-test",
                restart_delay_s=0.001,
                max_consecutive_failures=3,
                reset_after_s=60.0,
                on_fatal=lambda _name, _n: None,
            ),
            timeout=5.0,
        )
    assert calls == 3, "连续失败 3 次即熔断，不应继续重启"


@pytest.mark.asyncio
async def test_supervise_circuit_breaker_default_path_exits_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产路径：没有注入 on_fatal 时，熔断必须**真的退出进程**（让看门狗接管）。

    为什么不能只是 `raise`：`main()` 只 `await stop`，不观察 beat_task 的异常，
    所以"抛出去"等于"她僵在原地"。这里用替身拦住 `os._exit` 以断言它被调用。
    """
    exit_codes: list[int] = []

    def fake_exit(code: int) -> None:
        exit_codes.append(code)
        raise SystemExit(code)

    monkeypatch.setattr("elysia.core.supervisor.os._exit", fake_exit)

    async def always_fail() -> None:
        raise RuntimeError("持久故障")

    with pytest.raises(SystemExit):
        await asyncio.wait_for(
            supervise(
                always_fail,
                "exit-test",
                restart_delay_s=0.001,
                max_consecutive_failures=2,
            ),
            timeout=5.0,
        )
    assert exit_codes == [1], "熔断必须退出进程（非 0），旧写法是永远重启"


@pytest.mark.asyncio
async def test_supervise_resets_counter_after_a_healthy_run() -> None:
    """跑够 `reset_after_s` 才崩 ⇒ 计数归零（不把旧账算进这一次）。"""
    calls = 0

    async def factory() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            await asyncio.sleep(0.05)  # 跑够 reset_after_s=0.02 ⇒ 计数重置
        raise RuntimeError(f"第 {calls} 次失败")

    fatal: list[int] = []
    with pytest.raises(SupervisorFatal):
        await asyncio.wait_for(
            supervise(
                factory,
                "reset-test",
                restart_delay_s=0.001,
                max_consecutive_failures=3,
                reset_after_s=0.02,
                on_fatal=lambda _n, n: fatal.append(n),
            ),
            timeout=5.0,
        )
    assert calls == 4, "第 2 次失败前跑够了 reset_after_s ⇒ 计数重置，故第 4 次才熔断"
    assert fatal == [3]
