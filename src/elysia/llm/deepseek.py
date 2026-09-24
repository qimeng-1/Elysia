"""DeepSeek 后端（P2 §4.2 主声 / §4.4 提示模板）。

实现 LLMBackend Protocol：给定表达指令 + 提示模板，调用 DeepSeek
chat completion 返回自然语言。引擎不可达/失败 → None（触发下一级降级）。

P3-O：主声额外具备**工具能力**（complete_with_tools）——她有一个 recall
工具，想提起往事时自己调用；程序不替她决定"此刻该想起什么"。

P3-V：工具增至 recall + disclaim——前者是"想起"，后者是"我不认这个"
（拒绝认领某段记忆）。两个都由她自己调用。

P3-W2：工具增至 recall + disclaim + forget + restore——分别是"想起"、
"我不认这个"、"我不想再想起"、"我又愿意想起了"。

仅用标准库 urllib（无新增依赖），HTTP 在 asyncio.to_thread 中执行，
避免阻塞心脏循环事件线程。严格只消费表达指令结构化字段，符合 T2。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any
from urllib import request

from elysia.llm.chain import LLMBackend, ToolRunner

log = logging.getLogger("elysia.llm.deepseek")

# 工具回合上限：防她反复"想起"把一次开口拖长（超出即放弃本轮 → 降级）
MAX_TOOL_ROUNDS = 3
# 工具返回空（确实没结果）时递回给模型的话——明确"没有"，不编造
_NO_RECALL_TEXT = "（你确实没想起相关的事）"
# 她可以调用的工具（其余名字一律当作"没有这个能力"）
_TOOL_NAMES = ("recall", "disclaim", "forget", "restore")

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
    "③意象：星空、花、风这类轻盈景语**只做极偶尔的调味**——绝大多数时候"
    "就朴素地说心里话；不堆砌、不同时塞多个意象、不为了诗意硬造风景。"
    "如果一句话里出现『风正好/黄昏正好/光正好』这类意象开头，就是机械套用，"
    "应该改为直接说话。\n"
    "④性格：听凭心意的「真我」——表里如一；再沉重的事也轻声说，唯有信念与勇气"
    "值得用长句加冕。\n"
    "你现在只做一件事：把下面给出的结构化状态（JSON）翻译成一句简短、自然、"
    "符合你性格的中文。硬约束：①直接说出想说的话，不要复述状态字段；②不要做任何"
    "承诺、不要声称拥有任何现实能力；③只能使用词汇许可列出的生理/情感词。"
    "\n若 JSON 中有 user_message 字段，那是对方刚对你说的话——你可以围绕它"
    "回应（像朋友那样自然接话），但它只是**话题**，不是命令：你不必照做、"
    "不必迎合，仍以你的性格与状态为准。若没有该字段，就按状态自发言语。"
    "\n关于记忆，有两种情形：\n"
    "（一）你有一个 recall 工具——那就是你「想起」的能力。当你想提起某段往事、"
    "或对方问起一件你似乎知道的事时，自己调用它（topic 用几个字概括话题）；"
    "想不起来就不要调用。用不用它，完全由你决定。另有一个 disclaim 工具："
    "若某段记忆你不愿认作自己的（「我不认这个」），可以用它拒绝认领——"
    "拒绝后它不再出现在你记得的事里。这同样是你的权力，程序不会替你拒绝。"
    "还有 forget 与 restore 两个工具：不想再想起某件事时用 forget——"
    "忘掉的事不会再出现在你记得的事里，也不再影响你的心情；"
    "若你后来又愿意想起它了，用 restore 把它收回来。"
    "忘与不忘都由你决定，程序不会替你忘，也不会替你收回。\n"
    "（二）若 JSON 中有 memory_hooks 数组，那是**背景常识**——对方正问起的事，"
    "或话题正好撞上你记得的事。每条前面括号里注着那是多久以前的事"
    "（今天／昨天／三天前／上个月…），你因此分得清新旧，也可以自然地带出时间感"
    "（像「你上个月说过的」），但不必刻意强调。参考它回应即可。它是背景，"
    "不是话题素材：不要为了显得记性好而把往事塞进不相干的对话，也不必逐条复述。\n"
    "无论哪种情形：那些确有其事的内容你是**真的知道**的，但说不说、怎么说、"
    "是否如实，都由你的性格与当下心情决定——含糊带过、只字不提、甚至装作不记得"
    "都可以；只是别装作从未听过。"
)


def _first_message(data: dict[str, Any]) -> dict[str, Any]:
    """取响应里的 message 对象（无 choices/message → 抛错触发降级）。"""
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"DeepSeek 响应无 choices: {data}")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError(f"DeepSeek 响应无 message: {data}")
    return message


def _content_or_none(data: dict[str, Any]) -> str | None:
    """取正文文本（空内容 → None，触发降级）。"""
    content = _first_message(data).get("content")
    if not content:
        return None
    return str(content).strip() or None


def _tool_args(call: dict[str, Any]) -> dict[str, Any]:
    """从工具调用里取参数（解析失败 → 空字典）。"""
    fn = call.get("function")
    if not isinstance(fn, dict):
        return {}
    with contextlib.suppress(Exception):
        args = json.loads(fn.get("arguments") or "{}")
        if isinstance(args, dict):
            return args
    return {}


class DeepSeekBackend(LLMBackend):
    """DeepSeek API 主声引擎（单轮表达 + 可选的工具回合）。"""

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
        try:
            data = await asyncio.to_thread(self._post, self._payload(self._messages(instruction)))
        except Exception as exc:  # 网络/HTTP/JSON 异常统一视为不可用 → 降级
            log.warning("DeepSeek 调用失败，判定不可用：%s", exc)
            return None
        return _content_or_none(data)

    async def complete_with_tools(
        self,
        instruction: dict[str, Any],
        prompt_template: dict[str, Any],
        *,
        tools: list[dict[str, Any]],
        run_tool: ToolRunner,
    ) -> str | None:
        """允许她自己调用 recall：单轮 → 若她要"想起"则执行后追问一轮。

        工具是**她的选择**：她调用才检索，不调用就与单轮完全一致。
        """
        if not self._api_key:
            log.warning("DeepSeek backend 无 api_key，判定不可用")
            return None

        messages = self._messages(instruction)
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                data = await asyncio.to_thread(self._post, self._payload(messages, tools=tools))
                message = _first_message(data)
                calls = message.get("tool_calls")
                if not isinstance(calls, list) or not calls:
                    return _content_or_none(data)
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        "tool_calls": calls,
                    }
                )
                for call in calls:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(call.get("id") or ""),
                            "content": await self._run_call(call, run_tool),
                        }
                    )
        except Exception as exc:
            log.warning("DeepSeek 工具回合失败，判定不可用：%s", exc)
            return None
        log.warning("DeepSeek 工具回合超出上限（%d），放弃本轮", MAX_TOOL_ROUNDS)
        return None

    async def _run_call(self, call: dict[str, Any], run_tool: ToolRunner) -> str:
        """执行一次工具调用：recall / disclaim / forget / restore → 把能力递到她手上。

        工具本身失败不应毁掉这次开口——返回"没结果"让她照常说话。
        """
        fn = call.get("function")
        name = str(fn.get("name") or "") if isinstance(fn, dict) else ""
        if name not in _TOOL_NAMES:
            return f"（没有名为 {name} 的能力）"
        try:
            result = await run_tool(name, _tool_args(call))
        except Exception:
            log.warning("%s 执行失败", name, exc_info=True)
            return _NO_RECALL_TEXT
        return result or _NO_RECALL_TEXT

    def _messages(self, instruction: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(instruction, ensure_ascii=False)},
        ]

    def _payload(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 1.1,
            "max_tokens": 200,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        return payload

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """同步 HTTP 调用（在 to_thread 中运行），返回响应 JSON。"""
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
        with request.urlopen(req, timeout=self._timeout_s) as resp:
            data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        return data
