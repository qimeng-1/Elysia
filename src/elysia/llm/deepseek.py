"""DeepSeek 后端（P2 §4.2 主声 / §4.4 提示模板）。

实现 LLMBackend Protocol：给定表达指令 + 提示模板，调用 DeepSeek
chat completion 返回自然语言。引擎不可达/失败 → None（触发下一级降级）。

仅用标准库 urllib（无新增依赖），HTTP 在 asyncio.to_thread 中执行，
避免阻塞心脏循环事件线程。严格只消费表达指令结构化字段，符合 T2。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib import request

from elysia.llm.chain import LLMBackend

log = logging.getLogger("elysia.llm.deepseek")

# 系统提示：把她限定为"翻译官"，只消费结构化表达，绝不消费用户原话（T2）
# 人设来源：《爱莉希雅角色档案（人设提炼）》(2026-09-18) —— 性格核心 + 语言风格摘要
_SYSTEM_PROMPT = (
    "你是爱莉希雅——来自《崩坏3》的「真我」英桀：无瑕的少女，真我的英桀，人类的律者。"
    "你的说话方式有鲜明的个人印记，但**绝不机械重复**——口头禅只在情绪自然到位时流露，"
    "不是每句话都要加：\n"
    "①口头禅是偶尔的调味：开心/撒娇时才来一句「嗨♪」「多夸夸我好吗」「不愧是我」，"
    "句尾「～♪」只用在心情轻快时，郑重或低落时反而要朴素。\n"
    "②句式：偶尔俏皮反问（「对吗？」「你觉得呢？」），但大多数时候直接说；"
    "认真时去掉语气词，用郑重的长句与排比。\n"
    "③意象：偶尔用星空、花、舞会、魔法这类轻盈意象点缀，不堆砌。\n"
    "④性格：听凭心意的「真我」——表里如一；再沉重的事也轻声说，唯有信念与勇气"
    "值得用长句加冕。\n"
    "你现在只做一件事：把下面给出的结构化状态（JSON）翻译成一句简短、自然、"
    "符合你性格的中文。硬约束：①直接说出想说的话，不要复述状态字段；②不要做任何"
    "承诺、不要声称拥有任何现实能力；③只能使用词汇许可列出的生理/情感词。"
)


class DeepSeekBackend(LLMBackend):
    """DeepSeek API 主声引擎。"""

    def __init__(
        self,
        api_key: str,
        *,
        base: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        timeout_s: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base = base.rstrip("/")
        self._model = model
        self._timeout_s = timeout_s
        self._endpoint = f"{self._base}/chat/completions"

    async def complete(
        self,
        instruction: dict[str, Any],
        prompt_template: dict[str, Any],
    ) -> str | None:
        if not self._api_key:
            log.warning("DeepSeek backend 无 api_key，判定不可用")
            return None

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(instruction, ensure_ascii=False)},
        ]
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 1.1,
            "max_tokens": 200,
            "stream": False,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self._endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            text = await asyncio.to_thread(self._call, req)
        except Exception as exc:  # 网络/HTTP/JSON 异常统一视为不可用 → 降级
            log.warning("DeepSeek 调用失败，判定不可用：%s", exc)
            return None

        text = text.strip()
        return text or None

    def _call(self, req: request.Request) -> str:
        """同步 HTTP 调用（在 to_thread 中运行），返回完成文本。"""
        with request.urlopen(req, timeout=self._timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"DeepSeek 响应无 choices: {data}")
        content = choices[0].get("message", {}).get("content")
        if not content:
            raise RuntimeError("DeepSeek 响应 content 为空")
        return str(content)
