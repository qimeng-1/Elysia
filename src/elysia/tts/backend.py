"""GPT-SoVITS 出声后端（P2 §7.1）。

仅用标准库 urllib，HTTP 在 asyncio.to_thread 中执行，避免阻塞心脏循环
事件线程（与 llm/deepseek.py 同构）。情绪标签映射到参考音频 + 原文
（voice model 的 4 段克隆参考），speed 映射到 speed_factor。

引擎不可达/失败 → None，由 TTSChain 降级为"只输出文本不阻塞对话"。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib import parse, request

log = logging.getLogger("elysia.tts.backend")


@dataclass(frozen=True)
class EmotionProfile:
    """一种情绪的克隆参考：参考音频路径 + 对应原文（提升克隆效果）。"""

    ref_audio_path: str
    prompt_text: str


EMOTION_PROFILES: dict[str, EmotionProfile] = {
    "default": EmotionProfile(
        "Elysia_Voice_Model/ref_default.wav",
        "嗨，想我了吗？无论何时地，爱莉希雅都会回应你的期待",
    ),
    "devoted": EmotionProfile(
        "Elysia_Voice_Model/ref_devoted.wav",
        "看看你现在的表情，好想去那里，好想再见她，这种复杂而激烈的感情都快溢出来了哦",
    ),
    "gentle": EmotionProfile(
        "Elysia_Voice_Model/ref_gentle.wav",
        "我知道你在想什么，不过，也稍微休息一下吧",
    ),
    "playful": EmotionProfile(
        "Elysia_Voice_Model/ref_playful.wav",
        "嗯？你刚才是不是在偷瞄我",
    ),
}


class TTSSynthesizer:
    """GPT-SoVITS 同步 HTTP 客户端（GET /tts，返回 wav 音频流）。"""

    def __init__(
        self,
        base_url: str,
        *,
        text_lang: str = "zh",
        prompt_lang: str = "zh",
        timeout_s: float = 60.0,
    ) -> None:
        self._endpoint = base_url.rstrip("/") + "/tts"
        self._text_lang = text_lang
        self._prompt_lang = prompt_lang
        self._timeout_s = timeout_s

    async def synthesize(
        self,
        text: str,
        emotion: str = "default",
        speed: float = 1.0,
    ) -> bytes | None:
        """合成 wav 字节；任一步骤失败 → None（触发出声降级）。"""

        profile = EMOTION_PROFILES.get(emotion, EMOTION_PROFILES["default"])
        params = {
            "text": text,
            "text_lang": self._text_lang,
            "ref_audio_path": profile.ref_audio_path,
            "prompt_text": profile.prompt_text,
            "prompt_lang": self._prompt_lang,
            "speed_factor": speed,
            "media_type": "wav",
            "streaming_mode": 0,
        }
        url = self._endpoint + "?" + parse.urlencode(params)
        try:
            return await asyncio.to_thread(self._call, url)
        except Exception as exc:  # 网络/HTTP 异常统一视为不可用 → 只输出文本
            log.warning("GPT-SoVITS 合成失败，判定不可用：%s", exc)
            return None

    def _call(self, url: str) -> bytes:
        """同步 HTTP GET（在 to_thread 中运行），返回 wav 字节。"""
        req_obj = request.Request(url, method="GET")
        with request.urlopen(req_obj, timeout=self._timeout_s) as resp:
            data: bytes = resp.read()
            return data
