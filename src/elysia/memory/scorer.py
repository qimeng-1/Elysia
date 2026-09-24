"""记忆重要性打分（P3 §8.2：浅层缓冲 + 重要性打分 ≥ 阈值晋升）。

一段经历是否值得被"记住"，由四方面决定：
1. 内容信号：这段话里有没有"值得长期记住"的东西（日期事实/叮嘱记住/稳定事实/
   承诺/更正）——主导项
2. 经历类型：与用户交互 > 她说出的话 > 独处念头 > 普通状态
3. 情感强度：记忆携带的 6 维感受越强烈越重要（弱项：同一场对话里几乎人人相同）
4. 用户相关：带用户参与的记忆权重更高

为什么内容信号是主导项（P3-N 修正）：此前写入路径把用户输入一律标 importance=0.8，
而旧公式只看类型+情感，导致"是哪一天呢"和"你的生日是 11 月 11 日"同分、
整场闲聊全部晋升进深层。程序管"会不会"——把值得留下的东西留下、把闲聊留在
浅层，是程序的责任；她要不要在表达时动用这些记忆，是她的选择（本模块不干预）。

纯函数，可单测，不依赖存储与心脏循环。
"""

from __future__ import annotations

import re

from elysia.memory.levels import (
    KIND_EXPRESSION,
    KIND_INTERACTION,
    KIND_INTERNAL,
    KIND_SELF,
    KIND_STATE,
)

# 各类型基础权重（用户参与度越高权重越大）
_KIND_BASE: dict[str, float] = {
    KIND_INTERACTION: 0.20,  # 与用户交互
    KIND_EXPRESSION: 0.15,  # 她说出口的话
    KIND_INTERNAL: 0.10,  # 独处念头
    KIND_STATE: 0.10,  # 普通状态
    KIND_SELF: 0.30,  # 自我认知（"我是谁"——她认领的，最重的一类）
}

# 情感强度对重要性的加权上限（弱项：一场对话里几乎人人相同，不足以撑起重要性）
EMOTION_WEIGHT = 0.15
# 内容信号对重要性的加权上限（主导项：有没有"值得长期记住"的东西）
CONTENT_WEIGHT = 0.60
# 用户相关记忆可额外提升的上限
USER_RELATED_BONUS = 0.05

# ── 内容信号（正则 → 权重）：决定一段话"值不值得长期留下" ──
# 正信号：命中即加分（同一类信号只算一次）
_SIGNALS: tuple[tuple[float, re.Pattern[str]], ...] = (
    # 日期/时间事实：最硬的事实锚点（"11月11日""2026年"）
    (
        0.45,
        re.compile(
            r"\d{1,4}\s*(年|月)"
            r"|[〇零一二三四五六七八九十]{1,3}\s*月\s*[〇零一二三四五六七八九十\d]{1,3}\s*[日号]"
        ),
    ),
    # 叮嘱/要求记住（"记好""别忘了""一定要"）；"记不住"不算
    (0.35, re.compile(r"记(?!不)(住|好|下|牢|着)|别忘了|别忘记|一定要|不要忘|要记得")),
    # 关于她/你的稳定事实（生日、名字、偏好、愿望…）
    (
        0.30,
        re.compile(r"生日|名字|年龄|梦想|愿望|喜欢|不喜欢|讨厌|害怕|希望|住在|来自|工作|职业|习惯"),
    ),
    # 承诺/约定（"我会…""下次…""永远…"）
    (0.25, re.compile(r"我(会|答应|保证|发誓)|下次|以后|永远|说好")),
    # 更正（"不对""纠正""其实…"）：准确率的关键动作
    (0.25, re.compile(r"不对|记错|纠正|其实|应该是|不是这样")),
    # 关系/情感表达
    (0.15, re.compile(r"陪|一起|我们|想你|爱你|谢谢")),
)

# 闲聊特征（负信号）：纯应答语气词、极短句、纯提问 —— 不该沉淀成长期记忆
_SMALL_TALK = re.compile(
    r"^(嗯+|哦+|噢+|啊+|呀|好的?|行|知道了|是|对|哈哈+|嘿嘿|谢谢|你好|在吗|晚安|早安)"
    r"[呀啊哦嘛呢吧啦~～!！。，,.?\s]*$"
)
_QUESTION = re.compile(r"[吗呢吧]$|[?？]$|什么|为什么|怎么|哪|几|多少")
_SHORT_LEN = 4  # 去掉标点后不超过该长度视作短句（"你真棒"）
_SHORT_PENALTY = 0.25
_QUESTION_PENALTY = 0.30
_NON_WORD = re.compile(r"[^\w\u4e00-\u9fff]")


def content_salience(content: str) -> float:
    """内容信号强度（0-1）：这段话里有多少"值得长期记住"的东西。

    程序侧启发式（离线可用、确定性可测）：只读文本，不做语义推断。
    """
    text = (content or "").strip()
    if not text:
        return 0.0
    if _SMALL_TALK.match(text):
        return 0.0
    score = 0.0
    for weight, pattern in _SIGNALS:
        if pattern.search(text):
            score += weight
    if len(_NON_WORD.sub("", text)) <= _SHORT_LEN:
        score -= _SHORT_PENALTY
    if _QUESTION.search(text):
        score -= _QUESTION_PENALTY
    return round(max(0.0, min(1.0, score)), 3)


def emotion_strength(emotion_vector: dict[str, float]) -> float:
    """6 维感受的总体强度（0-1）：取最大维度作为情感唤起度。"""
    if not emotion_vector:
        return 0.0
    return max(0.0, min(1.0, max(emotion_vector.values())))


def importance(
    *,
    kind: str,
    emotion_vector: dict[str, float],
    content: str = "",
    user_related: bool = False,
) -> float:
    """计算一段经历的重要性打分（0-1）。

    importance = 类型基础权重 + 内容信号加权 + 情感强度加权 + 用户相关加成
    上限 1.0。晋升阈值见 levels.py：≥0.4 进工作层，≥0.7 进深层。
    """
    base = _KIND_BASE.get(kind, _KIND_BASE[KIND_STATE])
    score = base
    score += content_salience(content) * CONTENT_WEIGHT
    score += emotion_strength(emotion_vector) * EMOTION_WEIGHT
    if user_related:
        score += USER_RELATED_BONUS
    return round(max(0.0, min(1.0, score)), 3)
