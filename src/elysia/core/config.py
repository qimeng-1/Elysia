"""配置系统（pydantic-settings）。

优先级：环境变量（ELYSIA_ 前缀）> .env 文件 > 代码默认值。
.env 位于代码库根（被 .gitignore 排除，绝不入库）。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Elysia 运行时配置。"""

    model_config = SettingsConfigDict(
        env_prefix="ELYSIA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 路径 ──────────────────────────────────────────────
    data_dir: Path = Field(default=PROJECT_ROOT / "data")
    log_dir: Path = Field(default=PROJECT_ROOT / "data" / "logs")
    run_dir: Path = Field(default=PROJECT_ROOT / "data" / "run")
    backup_dir: Path = Field(default=Path("D:/ElysiaBackup/elysia"))

    # ── 行为 ──────────────────────────────────────────────
    log_level: str = "INFO"
    heartbeat_interval_s: float = 1.0
    body_heartbeat_interval_s: float = 5.0

    def ensure_dirs(self) -> None:
        """确保运行时目录存在（灵魂/身体进程启动时调用）。"""
        for path in (self.data_dir, self.log_dir, self.run_dir):
            path.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """获取全局单例配置。"""
    return Settings()
