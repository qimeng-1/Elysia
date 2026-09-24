"""配置系统（pydantic-settings）。

优先级：环境变量（ELYSIA_ 前缀）> .env 文件 > 代码默认值。
.env 位于代码库根（被 .gitignore 排除，绝不入库）。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# ── 表达异步化（《运行时可靠性收口》阶段 1）────────────────────
# 有界表达队列容量：1 在跑 + 1 等待。内部念头遇满即丢（＝「她这次决定不说」），
# 用户输入永不丢，可挤掉队列里最旧的内部念头（D1）。
EXPRESS_QUEUE_CAPACITY = 2
# 关停宽限（≈ 10 拍）：关闭时等在跑任务至多这么久，超时则取消并登记 `status=timeout`。
# **D3 澄清（2026-09-24，用户裁决）：只作关闭宽限**——正常运行不给单任务设上限，
# 否则 LLM 主声自身 30s 的超时会先被这里误判成"她这次没说成"。
EXPRESS_TIMEOUT_S = 10.0


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

    # ── P2 LLM（§八）──────────────────────────────────────
    llm_main_base: str = "https://api.deepseek.com"
    llm_main_api_key: str = ""  # 真实 key 走 .env，绝不入库
    llm_main_model: str = "deepseek-chat"
    llm_main_timeout_s: float = 30.0
    # S6（D-S3）：次声——任何 OpenAI 兼容端点（本地 Ollama/vLLM 或另一个云模型）。
    # `llm_fallback_base` 留空则不挂载次声，行为与接线前完全一致。
    llm_fallback_base: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = "qwen:7b"
    # 资源熔断：VRAM(MB) 超过阈值切次声/微声
    llm_vram_threshold_mb: int = 9510
    # 降级即感受：每次 fallback 写入感受层的 SA 增量
    llm_degrade_sa_delta: float = 2.0

    # ── P2 TTS（§七）──────────────────────────────────────
    tts_gpt_sovits_url: str = "http://127.0.0.1:9880"
    tts_cache_dir: Path = Field(default=PROJECT_ROOT / "data" / "cache" / "tts")
    tts_text_lang: str = "zh"
    tts_prompt_lang: str = "zh"
    tts_timeout_s: float = 60.0
    # 资源熔断：VRAM(MB) 超过阈值或队列拥塞 → 只输出文本不阻塞对话
    tts_vram_threshold_mb: int = 10240
    # 降级即感受：TTS 降级（熔断/合成失败）时写入感受层的 SA 增量
    tts_degrade_sa_delta: float = 1.0
    # 低负载期预合成的高频短语
    tts_common_phrases: list[str] = Field(default_factory=lambda: ["我在", "好呀", "嗯嗯"])

    def ensure_dirs(self) -> None:
        """确保运行时目录存在（灵魂/身体进程启动时调用）。"""
        for path in (self.data_dir, self.log_dir, self.run_dir, self.tts_cache_dir):
            path.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """获取全局单例配置。"""
    return Settings()
