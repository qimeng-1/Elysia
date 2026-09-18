"""TTS 缓存池（P2 §7.1）。

缓存 key = SHA256(文本 + 情绪 + 语速)，落盘到缓存目录。高频短语在低负载
期预合成，缓存命中时零 GPU 开销直接回放。情绪以标签参与 key，情绪向量
差异大（情绪标签不同）时自然落入不同缓存条目，相当于按需失效重合成。
写读异常一律吞掉并记日志，保证缓存不拖垮出声链路。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger("elysia.tts.cache")


class CachePool:
    """以 (文本, 情绪, 语速) 为 key 的 wav 磁盘缓存。"""

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)

    @staticmethod
    def _key(text: str, emotion: str, speed: float) -> str:
        raw = f"{text}\0{emotion}\0{speed}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _path_for(self, text: str, emotion: str, speed: float) -> Path:
        return self._dir / f"{self._key(text, emotion, speed)}.wav"

    def get(self, text: str, emotion: str, speed: float) -> bytes | None:
        """返回缓存 wav 字节；未命中/读失败 → None。"""
        path = self._path_for(text, emotion, speed)
        try:
            if path.is_file():
                return path.read_bytes()
        except OSError as exc:
            log.warning("TTS 缓存读取失败：%s", exc)
        return None

    def put(self, text: str, emotion: str, speed: float, audio: bytes) -> None:
        """写入缓存条目；写失败只记日志，不向外抛。"""
        path = self._path_for(text, emotion, speed)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(audio)
        except OSError as exc:
            log.warning("TTS 缓存写入失败：%s", exc)
