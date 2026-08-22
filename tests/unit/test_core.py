"""core 模块（配置/日志）单元测试。"""

from __future__ import annotations

import json

import pytest
import structlog

from elysia.core.config import Settings, get_settings
from elysia.core.log import get_logger, setup_logging


def test_settings_defaults() -> None:
    settings = Settings()
    assert settings.log_level == "INFO"
    assert settings.heartbeat_interval_s == 1.0
    assert settings.body_heartbeat_interval_s == 5.0
    assert settings.backup_dir.name == "elysia"


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
