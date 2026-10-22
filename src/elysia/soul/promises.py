"""T-020（N1）：她说的"我这就写"要变成她自己的承诺，下一次开口递回她手上。

用户 2026-10-21 批准。**核心原则：系统绝不代写、绝不计配额、绝不改她的话**——
程序只把她自己说过的承诺记下来，并在她下一次开口时提醒她；写不写、推迟、说
"不写了"，三种都合法。

四段：
1. 捕获：回复里出现「第一人称 + 写作动词 + 将来/即时语气」，且本回合**没有**
   成功的 `write_note` ⇒ 记一条 `kind='promise'`（`action='write_note'`、`status='pending'`、
   `goal=她的原话`），并落一行 `thought_log kind='promise'`。
2. 提醒：表达 payload 的 `pending_promises`（最近 24h、≤3 条、只放文本不带 id）
   + 一行明确指令，告诉她"你可以写，也可以说不写了"。
3. 兑现/关闭：本回合成功 `write_note` ⇒ 最近 24h 的 pending 承诺标 `done`；
   她明确放弃 ⇒ 标 `expired`；48h 未兑现 ⇒ 读取时惰性 `expired`。
4. Worker 跳过 `kind='promise'`（只能由她本人兑现）。

T-020d（真机取证 2026-10-22 00:38–00:59）：显式自诺的句式太窄——真机她答
「算数。说过的就写」「还写。你睡你的，写完这句我就接着写」「放在我自己的文件夹里，
好。」，一条都没命中，因此"捕获→提醒"在真机没跑起来。新增一条**上下文判据**
（与显式自诺**取并集**，显式优先、上下文兜底）：

    用户这一轮在请她写（`has_write_request`）+ 她给了肯定答复（且没拒绝/推脱）
    + 本回合没有成功的 `write_note` ⇒ 记一条承诺。

`detect_contextual_promise` 只回答"记不记、记什么"；去重（同一请求/同一原话只留
一条）在 `capture_promise` 里做。放弃/兑现/过期语义完全不变。

T-020e（真机再取证）：条件①成立时，肯定信号要放宽到**子句的核心动词就是写**——
真机她答「写。今晚就写在文件夹里，天亮前它会在。」，最直接的肯定就是「写。」，
而 T-020d 的信号表只认「算数/还写/这就写」这类更长形式。放宽的形式：
「写 / 写吧 / 写呀」整子句、「今晚/今晚就写」、「写进…文件夹 / 写在…里 / 写下来」、
「我写 / 我这就写 / 接着写 / 继续写」；每条都要求该子句**不含**拒绝/推脱/无力词。

T-021（用户硬要求 2026-10-22「答应的事就动手；答应了不做，比不做更糟」）：
捕获与提醒之间还差最后一公里——她收到提醒后只说「我去写」，没有调用
`write_note`。本模块因此提供"第二拍"的领域件：`forced_write_prompt`（那一句强制
写作指令）、`next_forced_write_promise`（挑该补拍的 pending 承诺，同一承诺两次
强制尝试间隔 ≥10 分钟）与 `formed_log_text`（成文留痕）。**正文永远由她自己写**
（`write_note` 的 text 参数来自她那一次 LLM 调用），程序只负责递工具与记结果。

T-022a（用户 2026-10-22 批准：**自觉性改造**——"承诺的事只要她能做到，她应该记得、
应该能自发去做，被推着做不符合期望"）：把"提醒的线索"从系统的 payload 字段搬进
**她自己的记忆**：

- 捕获时同落一条 `kind='internal'` 的记忆（第一人称、无系统指令），
  `source_ref='promise:<intention_id>'`，可被正常召回；
- 任一路径成文后落一条"我把〈文件名〉写完了，是我自己写的"的自我证据，并把
  "还没写"那条 `superseded_by` 指向它（原件保真）；
- 正常回合不再出现 `pending_promises` / `pending_promises_hint`，改由
  `unfinished_things` 只放她自己的记忆原句；只有承诺 >24h 或她在这之后已开口 ≥2 次
  仍未动时，才兜底出现一次**她自己的记忆**（同一条 12h 最多一次）；
- 强制轮**大量收窄**：只在她这一轮明确说了"马上/这就/今晚就写"这类即时意图时才强制，
  冷却从 10 分钟提到 2 小时；其余交给记忆与下一次开口。

T-029（用户裁定 2026-10-22 21:4x）：兜底提醒的**口吻**改掉——不是催办，是她自己的记忆。
真机 20:48 证据：旧提醒「你答应过的事还没做：…。这件还算数吗？不写也跟我说一声。」
进了 payload，她答「在写了，真的在写」「写完就给你看」，而 `data/her_home/` 无新文件、
无 `write_note` 痕迹——旧文案把"该她交代"摆到了她面前。现在
`promise_reminder_question` 只说「我想起来：我说过要写「…」，还没写。」：**没有问句、
不要求表态**；她可以不理，也可以直接说不写了（`is_abandonment` 出口仍在）。
触发阈值/冷却（24h、开口≥2 次、12h 冷却）**一个字不动**，只改文案。
"""

from __future__ import annotations

import re
from typing import Any

from elysia.memory.levels import (
    CERTAINTY_CERTAIN,
    KIND_INTERNAL,
    LEVEL_SHALLOW,
    SOURCE_SELF,
)

PROMISE_WINDOW_S = 24.0 * 3600.0  # 提醒窗口
PROMISE_TTL_S = 48.0 * 3600.0  # 惰性过期
MAX_PENDING_PROMISES = 3
PROMISE_GOAL_CHARS = 120

# ── T-022a：承诺记忆化 + 自我证据 + 提醒降级 ─────────────────────────
# 记忆用 `internal`（"内部念头/独处生活"）：她自己的心事，可被记忆检索正常召回；
# 不用 `world`（那是她读过的外部资料）、不用 `self`（身份段每句在场，不进 hooks）。
PROMISE_MEMORY_KIND = KIND_INTERNAL
PROMISE_MEMORY_IMPORTANCE = 0.6  # 值得被想起来（≥0.6 的"心事"档）
PROMISE_MEMORY_SOURCE_REF_PREFIX = "promise:"
WRITTEN_MEMORY_SOURCE_REF_PREFIX = "written:"
# 正常回合的 `unfinished_things`（她自己的原句）最多几条
UNFINISHED_MAX = 3
# 兜底提醒：承诺超过 24h，或她在这条承诺之后已开口 ≥2 次仍未动
REMINDER_STALE_S = 24.0 * 3600.0
REMINDER_OPENINGS_MIN = 2
# 同一条承诺的兜底提醒 12 小时最多一次（存 kv，不加表结构）
REMINDER_COOLDOWN_S = 12.0 * 3600.0

# 承诺检测（T-020b 收紧；T-020c 子句级 + 否定只作用到写作动词）：
# (A) 时间/意愿线索 + 写：「我这就写」「我现在去写」「我待会儿动笔」
# (B) 写 + 结果承诺：「我…写下来/写完/写好/写一篇/写给你」
# (C) 「写完喊你 / 写好给你」
# **否定/过去时只认这些"写作动词附近"的形态**，不再用整句 `[不没]` 否决
# （真机：「不催我正好～那我就慢慢写，写完喊你」不能被前面的"不"误杀）。
_TIME_HINT = r"(?:这就|现在|马上|立刻|待会儿|待会|一会儿|一会|晚点|回头|之后)"
_PROMISE_TIME = re.compile(rf"我{_TIME_HINT}(?:就)?(?:去)?(?:写|动笔|码字)")
_PROMISE_RESULT = re.compile(r"我.{0,12}写(?:下来|完|好|一篇|一段|一页|给你)")
_PROMISE_FOLLOWUP = re.compile(r"写(?:完|好)(?:了)?(?:就|再)?(?:给|喊|告诉|拿给)你")
# 拒绝用：写作动词附近的否定/过去时（本句含这些即不是承诺；保守，宁可不记）
# T-031：**反编造产生的诚实句绝不能被记成承诺**——「其实我还没真的写完呢。」若被
# `_PROMISE_RESULT`（我…写完）再捕获一次，就等于系统把"没做到"记成"要做"，形成自激
# （诚实句 → 新 pending 承诺 → 进 unfinished_things/提醒）。因此把"还没真的写/没真的
# 写完/还没写完"这类**未完成**写法列入拒绝表：诚实句与真·否定句都不该记成承诺。
_REJECT_RE = re.compile(
    r"(?:写不|写没|没写|没真的写完|还没真的写|还没写完|不想写|不写了|不肯写|懒得写|写不动"
    r"|写完了|写好了|写过了|已经写了)"
)

# 明确放弃（同句出现写作语境，或封闭式说法）；普通「算了」不算
_ABANDON_CLOSED = re.compile(r"(?:不写了|不写啦|写不动了|懒得写|不想写了|先搁着|先放着|这事算了)")
_ABANDON_WRITE_CTX = re.compile(r"(?:写|稿|文|笔)")
# 先按句切（用于"返回整句原话"），再在句内按子句/破折号切（承诺常在逗号之后）
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")
_CLAUSE_SPLIT = re.compile(r"[，,；;、～~…—]+")

# ── T-020d：上下文判据（用户请她写 + 她肯定答复）─────────────────────
# 用户这轮**在请她写**：写作动词必须带宾语/结果/方向（单独一个「写」不算，否则
# 「写代码」也算）；技术语境（写代码/写程序/写脚本）明确排除，其余从宽。
_WRITE_REQUEST = re.compile(
    r"(?:"
    r"帮我写|替我写|给我写|请你写|请写|再写|多写|重新写|接着写"
    r"|写一下|写下|写下来|写点|写些|写一篇|写一段|写一封|写一张|写一份|写个"
    r"|写完|写成|写进|写上|写出来|写出|写给我|写给我看"
    r"|落成文件|存个文件|存成文件|写成文件|整个文件"
    r"|写.{0,4}(?:文章|稿子|稿|故事|诗|信|笔记|日记|散文)"
    r")"
)
_WRITE_REQUEST_TECH = re.compile(r"(?:写.{0,3}代码|写程序|写函数|写脚本|写测试)")

# 她的回复给的肯定答复：强信号子句级匹配；「好/行/嗯」这类单字必须**整子句**
# 才算（避免「好像/只好/好多」误判），且整句是反问（以 ？ 收尾）时不算。
_AFFIRM_STRONG = re.compile(
    r"(?:算数|还写|接着写|继续写|慢慢写|我写|这就|待会儿|待会|一会|马上"
    r"|写好|写完|会写|会的|好呀|好啊|好的|行吧|可以|当然"
    r"|答应|说到做到|说话算话|一言为定)"
)
_AFFIRM_BARE = frozenset({"好", "行", "可以", "嗯", "嗯嗯", "好呀", "好啊", "好的", "好呢", "行吧"})

# 拒绝/推脱：整段回复里出现任一即不记（放弃语义另由 `is_abandonment` 处理）。
# 注意「还没写完」不是拒绝——只拦明确的"不写/不打算写"。
_REFUSAL = re.compile(
    r"(?:不写|不想写|不会写|不肯写|不打算写|写不了|写不动|写不出|写不出来"
    r"|算了|再说吧|以后吧|改天|下次吧|没空|懒得|先搁着|先放着|缓一缓"
    r"|不行|不可以|别写|不会的|说不准|不好说)"
)

# T-020e：条件①成立时，肯定信号**放宽到"子句的核心动词就是写"**。
# 真机漏检：「写。今晚就写在文件夹里，天亮前它会在。」——最直接的肯定就是
# 「写。」，而 T-020d 的信号表只认「算数/还写/这就写」这类更长形式。
_WRITE_CORE = re.compile(r"^(?:嗯|好)?[，,]?写(?:吧|呀|啊|啦|哦|呢)?$")
_WRITE_TIME = re.compile(r"(?:今晚|今天|现在|待会儿|马上|明天|天亮前|睡前).{0,8}写")
_WRITE_ACTION = re.compile(
    r"(?:写进.{0,12}(?:文件夹|文件|目录)|写在.{0,12}(?:文件夹|文件|里)"
    r"|写下来|写成文件|落成文件)"
)
# 放宽判据后，再逐子句挡一次拒绝/推脱/无力（「不写」「写不出来」不能算肯定）
_WRITE_CLAUSE_REFUSAL = re.compile(
    r"(?:不写|不想写|写不出|写不动|写不了|算了|再说吧|改天|没空|懒得|先搁着)"
)

# 记 goal 时的拼装格式（她原话太短时补上"用户请我写什么"，否则她下次看不懂）
_CONTEXT_GOAL_PREFIX = "用户请我写："
_CONTEXT_GOAL_MIDDLE = "／我说："
CONTEXT_REQUEST_CHARS = 30


def detect_promise(text: str) -> str | None:
    """从她这次说的话里找一句"我要写"的承诺原话；没有返回 None。

    T-020c：否定/过去时按**写作动词附近**的形态判定（不在整句里见「不」就否决）；
    模式在**子句级**匹配（支持「…不催我正好～那我就慢慢写，写完喊你」），
    但返回**整句**（≤120 字）作为承诺原话。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    for sentence in _SENTENCE_SPLIT.split(raw):
        sentence = sentence.strip()
        if not sentence:
            continue
        # 句内出现"写作动词附近的否定/过去时" ⇒ 这一句不算承诺（保守）
        if _REJECT_RE.search(sentence):
            continue
        for clause in _CLAUSE_SPLIT.split(sentence):
            clause = clause.strip()
            if not clause:
                continue
            if (
                _PROMISE_TIME.search(clause)
                or _PROMISE_RESULT.search(clause)
                or _PROMISE_FOLLOWUP.search(clause)
            ):
                return sentence[:PROMISE_GOAL_CHARS]
    return None


def is_abandonment(text: str) -> bool:
    """她这次是否**明确说不写了**（只用于关闭提醒；不替她做决定）。

    T-020b：普通「算了」不算——必须同句有写作语境，或是「不写了/写不动了/
    懒得写/不想写了/先搁着」这类封闭式表达。
    """
    for sentence in _SENTENCE_SPLIT.split(str(text or "")):
        sentence = sentence.strip()
        if not sentence:
            continue
        if _ABANDON_CLOSED.search(sentence):
            return True
        if "算了" in sentence and _ABANDON_WRITE_CTX.search(sentence):
            return True
    return False


# ── T-020d：上下文判据（纯函数，可单测）──────────────────────────────


def has_write_request(user_message: str) -> bool:
    """用户/另一个 agent 这一轮是否在**请她写东西**（上下文判据的条件 1）。

    判据：写作动词必须带宾语/结果/方向——「写一下 / 写下 / 写下来 / 写一篇 /
    写点 / 写完 / 写成 / 写进 / 写上 / 帮我写 / 写给我看 / 落成文件 / 存个文件」
    以及「写文章/写故事/写信…」。**单独一个「写」字不算**；「写代码/写程序/
    写脚本/写测试」这类技术语境明确排除（那不是请她写笔记）。其余从宽。
    """
    raw = str(user_message or "").strip()
    if not raw:
        return False
    if _WRITE_REQUEST_TECH.search(raw):
        return False
    return _WRITE_REQUEST.search(raw) is not None


def _strip_punct(clause: str) -> str:
    return clause.strip(" \t\r\n。！!？?…—～~、；;，,")


def _clause_is_write_affirmation(core: str) -> bool:
    """T-020e：子句的核心就是写作动作（「写」「今晚就写」「写进文件夹」）。

    只用于**用户已明确请她写**（条件①成立）的语境；该子句含拒绝/推脱/无力词
    （「不写」「写不出来」）⇒ 不算肯定。
    """
    if not core or _WRITE_CLAUSE_REFUSAL.search(core):
        return False
    return bool(_WRITE_CORE.match(core) or _WRITE_TIME.search(core) or _WRITE_ACTION.search(core))


def _sentence_has_affirmation(sentence: str) -> bool:
    """这一句里有没有肯定答复（「算数/还写/好呀/可以/嗯」…，T-020e 放宽到「写」）。"""
    asks = sentence.rstrip().endswith(("？", "?"))
    for clause in _CLAUSE_SPLIT.split(sentence):
        core = _strip_punct(clause)
        if not core:
            continue
        if core in _AFFIRM_BARE and not asks:
            return True
        if _AFFIRM_STRONG.search(core):
            return True
        if not asks and _clause_is_write_affirmation(core):
            return True
    return False


def _affirming_sentence(text: str) -> str | None:
    """返回第一句含肯定答复的原话（保留原句，≤120 字）；没有返回 None。"""
    for sentence in _SENTENCE_SPLIT.split(str(text or "")):
        sentence = sentence.strip()
        if not sentence:
            continue
        if _sentence_has_affirmation(sentence):
            return sentence[:PROMISE_GOAL_CHARS]
    return None


def _contextual_goal(user_message: str, her_sentence: str) -> str:
    """拼 goal：`用户请我写：<请求摘要 30 字>／我说：<她的肯定原话>`（≤120）。

    她原话太短（如只有一个「好。」）时，单看记不清楚在答应什么——补上用户的请求，
    下次提醒她自己兑现时才看得懂。
    """
    request = " ".join(str(user_message or "").split())[:CONTEXT_REQUEST_CHARS]
    prefix = f"{_CONTEXT_GOAL_PREFIX}{request}{_CONTEXT_GOAL_MIDDLE}"
    budget = PROMISE_GOAL_CHARS - len(prefix)
    if budget <= 0:
        return (prefix + her_sentence)[:PROMISE_GOAL_CHARS]
    return (prefix + her_sentence[:budget])[:PROMISE_GOAL_CHARS]


def detect_contextual_promise(
    user_message: str,
    her_text: str,
    *,
    wrote: bool,
) -> str | None:
    """上下文判据（T-020d）：用户请她写 + 她给了肯定答复 ⇒ 返回要记的 goal。

    只在**显式自诺**（`detect_promise`）没命中时兜底。四个条件同时成立才记：
    ① 用户这轮在请她写（`has_write_request`）；
    ② 她的回复有肯定答复（`_affirming_sentence`；T-020e：条件①成立时还认
       「写 / 今晚就写 / 写进文件夹」这类子句级写作动作）且整段**没有**拒绝/推脱
       （`_REFUSAL`）——既不肯定也不拒绝（转移话题/反问）⇒ 不记；
    ③ 本回合没有成功的 `write_note`（`wrote=True` ⇒ 不记）；
    ④ 同一请求/同一原话的去重由 `capture_promise` 负责（本函数不碰库）。
    """
    if wrote:
        return None
    if not has_write_request(user_message):
        return None
    her = str(her_text or "").strip()
    if not her or _REFUSAL.search(her):
        return None
    sentence = _affirming_sentence(her)
    if sentence is None:
        return None
    return _contextual_goal(user_message, sentence)


# ── T-020d：去重（同一请求/同一原话连问两轮只记一条）─────────────────


def _squash(text: str) -> str:
    return "".join(str(text or "").split())


def _goal_request_key(goal: str) -> str | None:
    """从拼装过的 goal 里抽出"用户请我写什么"（用于跨轮去重）；不是拼装格式返回 None。"""
    squashed = _squash(goal)
    prefix = _squash(_CONTEXT_GOAL_PREFIX)
    if not squashed.startswith(prefix):
        return None
    rest = squashed[len(prefix) :]
    middle = _squash(_CONTEXT_GOAL_MIDDLE)
    if middle in rest:
        rest = rest.split(middle, 1)[0]
    return rest or None


def _same_promise(goal: str, existing_goal: str) -> bool:
    """两条承诺是否"高度相似"：相同、一方包含另一方，或同一用户的同一请求。"""
    left, right = _squash(goal), _squash(existing_goal)
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    left_request, right_request = _goal_request_key(left), _goal_request_key(right)
    return bool(
        left_request
        and right_request
        and (left_request in right_request or right_request in left_request)
    )


async def _is_duplicate_promise(store: Any, goal: str, now: float) -> bool:
    """最近 48h 内已有 pending 的相似承诺 ⇒ 不新增（同一请求连问两轮只记一条）。"""
    rows = await store.recent_pending_promises(
        float(now) - PROMISE_TTL_S, limit=MAX_PENDING_PROMISES * 2
    )
    return any(_same_promise(goal, str(row.get("goal") or "")) for row in rows)


# ── T-022a：承诺记忆化（她的心事，不是系统字段）──────────────────────


def promise_memory_text(goal: str) -> str:
    """她把"答应过要写什么"记成自己的心事（第一人称、无系统指令/工具名）。"""
    text = str(goal or "").strip()
    return f"我答应过要写「{text}」，还没写。"


def written_memory_text(path: str) -> str:
    """成文后的自我证据（她反复说"没骗你"时缺的就是这一条）。"""
    shown = str(path or "").strip() or "那篇"
    return f"我把「{shown}」写完了，是我自己写的。"


async def record_promise_memory(store: Any, intention_id: int, goal: str, now: float) -> int | None:
    """承诺落一条她自己的记忆；同一 `source_ref` 只一条（已有则跳过）。"""
    ref = f"{PROMISE_MEMORY_SOURCE_REF_PREFIX}{int(intention_id)}"
    if await store.find_memory_by_source_ref(ref) is not None:
        return None
    text = promise_memory_text(goal)
    return int(
        await store.add_memory(
            float(now),
            {
                "level": LEVEL_SHALLOW,
                "kind": PROMISE_MEMORY_KIND,
                "content": text,
                "emotion_vector": {},
                "importance": PROMISE_MEMORY_IMPORTANCE,
                "protected": False,
                "narrative": text,
                "source": SOURCE_SELF,
                "certainty": CERTAINTY_CERTAIN,
                "source_ref": ref,
            },
        )
    )


async def record_written_memory(
    store: Any, now: float, *, path: str | None, promise_ids: list[int]
) -> int:
    """成文 ⇒ 落"我写完了"的自我证据，并把"还没写"那条 `superseded_by` 指向它。"""
    ref = f"{WRITTEN_MEMORY_SOURCE_REF_PREFIX}{str(path or '').strip()}"
    text = written_memory_text(path or "")
    new_id = int(
        await store.add_memory(
            float(now),
            {
                "level": LEVEL_SHALLOW,
                "kind": PROMISE_MEMORY_KIND,
                "content": text,
                "emotion_vector": {},
                "importance": PROMISE_MEMORY_IMPORTANCE,
                "protected": False,
                "narrative": text,
                "source": SOURCE_SELF,
                "certainty": CERTAINTY_CERTAIN,
                "source_ref": ref,
            },
        )
    )
    for promise_id in promise_ids:
        old = await store.find_memory_by_source_ref(
            f"{PROMISE_MEMORY_SOURCE_REF_PREFIX}{int(promise_id)}"
        )
        if old is not None and old.get("superseded_by") is None:
            await store.mark_superseded(int(old["id"]), new_id)
    return new_id


async def unfinished_things(store: Any, now: float, *, limit: int = UNFINISHED_MAX) -> list[str]:
    """未完成之事的**她自己的原句**（≤limit；没有祈使句、没有工具名）。"""
    await store.expire_stale_promises(float(now), ttl_s=PROMISE_TTL_S)
    rows = await store.unfinished_promises(limit=limit)
    out: list[str] = []
    for row in rows:
        text = str(row.get("content") or "").strip()
        if not text:
            text = promise_memory_text(str(row.get("goal") or ""))
        if text and text not in out:
            out.append(text)
    return out[:limit]


def promise_reminder_question(goals: list[str]) -> str:
    """兜底提醒：**她自己的记忆，不是催办**（T-029，用户裁定 2026-10-22）。

    旧版「你答应过的事还没做：…。这件还算数吗？不写也跟我说一声。」是在要她交代；
    真机她收到后答「在写了，真的在写」，然后既没有文件、也没有 `write_note` 痕迹。
    现在只把"我想起我说过要写什么、还没写"递给她：**没有问句、不要求表态**。
    她可以不理；真想放下时，`is_abandonment` 的出口照旧（直接说不写了）。
    单条/多条都保持简洁的一句话，不再带"还算数吗/跟我说一声"这类催办语义。
    """
    shown = [str(item).strip() for item in goals if str(item).strip()]
    if not shown:
        return "我想起来：我说过要写点什么，还没写。"
    if len(shown) == 1:
        return f"我想起来：我说过要写「{shown[0]}」，还没写。"
    rest = "；".join(f"也说过「{item}」" for item in shown[1:])
    return f"我想起来：我说过要写「{shown[0]}」；{rest}，都还没写。"


async def due_promise_reminder(
    store: Any, now: float, *, limit: int = MAX_PENDING_PROMISES
) -> str | None:
    """T-022a 兜底提醒：承诺 >24h 或之后她已开口 ≥2 次，且同一条 12h 内没提醒过。

    正常回合**不会**出现系统提醒；只有这里返回的非空文案（她自己的记忆口吻，
    见 `promise_reminder_question`）才会进 payload。触发阈值与冷却一个字不动。
    冷却记在 kv（`promise_reminder:<id>`），不加表结构。
    """
    await store.expire_stale_promises(float(now), ttl_s=PROMISE_TTL_S)
    rows = await store.unfinished_promises(limit=limit)
    due_goals: list[str] = []
    for row in rows:
        created = float(row.get("created_ts") or 0.0)
        stale = float(now) - created >= REMINDER_STALE_S
        openings = await store.count_openings_since(created)
        if not stale and openings < REMINDER_OPENINGS_MIN:
            continue
        key = f"promise_reminder:{int(row['id'])}"
        last = await store.load_json(key, default=0.0)
        if isinstance(last, (int, float)) and float(now) - float(last) < REMINDER_COOLDOWN_S:
            continue
        await store.save_json(key, float(now))
        due_goals.append(str(row.get("goal") or ""))
    if not due_goals:
        return None
    return promise_reminder_question(due_goals)


# T-022a：只有"这一轮明确说了马上/这就/今晚就写"才允许强制第二拍
_IMMEDIATE_WRITE = re.compile(
    r"(?:马上|这就|立刻|现在就|今晚|今天|天亮前|睡前|待会儿).{0,8}(?:写|动笔|码字)"
    r"|我(?:现在|这就|马上)(?:就)?(?:去)?写"
)


def has_immediate_write_intent(text: str) -> bool:
    """T-022a：她这一轮是否给了**即时意图**（强制轮的唯一许可）。"""
    return _IMMEDIATE_WRITE.search(str(text or "")) is not None


async def capture_promise(
    store: Any,
    text: str,
    now: float,
    *,
    wrote: bool,
    user_message: str | None = None,
) -> int | None:
    """捕获承诺并落库；本回合已经真写了 ⇒ 不记。返回新意图 id 或 None。

    显式自诺（`detect_promise`）优先；没命中再看上下文判据
    （`detect_contextual_promise`，需要 `user_message`）。已有相似的 pending
    承诺 ⇒ 不重复记。
    """
    if wrote:
        return None
    goal = detect_promise(text)
    if goal is None and user_message:
        goal = detect_contextual_promise(user_message, text, wrote=wrote)
    if goal is None:
        return None
    if await _is_duplicate_promise(store, goal, now):
        return None
    intention_id = await store.add_intention(
        float(now), goal=goal, action="write_note", target=None, kind="promise"
    )
    await store.think(float(now), "promise", f"承诺：{goal}")
    # T-022a：同时落一条她自己的记忆（第一人称、可被正常召回）；不重复落
    await record_promise_memory(store, int(intention_id), goal, float(now))
    return int(intention_id)


async def settle_after_reply(
    store: Any,
    text: str,
    now: float,
    *,
    wrote: bool,
    user_message: str | None = None,
    written_path: str | None = None,
    window_s: float = PROMISE_WINDOW_S,
) -> dict[str, Any]:
    """一次开口落库后的承诺处理：兑现 / 放弃 / 捕获（三者互斥，不代写）。

    `user_message` 是用户这一轮的原话（上下文判据要它；None ⇒ 只看显式自诺）。
    `written_path` 是本回合 `write_note` 写成的相对路径（T-022a 成文自我证据用）。
    """
    since = float(now) - float(window_s)
    if wrote:
        pending = await store.recent_pending_promises(since, limit=MAX_PENDING_PROMISES)
        await store.close_pending_promises(float(now), since, status="done", outcome="她写了")
        if pending:
            # T-022a：成文 ⇒ "我写完了"的自我证据 + 旧的"还没写"记忆 superseded
            await record_written_memory(
                store,
                float(now),
                path=written_path,
                promise_ids=[int(row["id"]) for row in pending],
            )
        return {"closed": "done", "captured": None}
    if is_abandonment(text):
        await store.close_pending_promises(
            float(now), since, status="expired", outcome="她自己说不写了", limit=1
        )
        return {"closed": "expired", "captured": None}
    intention_id = await capture_promise(store, text, now, wrote=False, user_message=user_message)
    return {"closed": None, "captured": intention_id}


async def list_pending_promises(
    store: Any,
    now: float,
    *,
    window_s: float = PROMISE_WINDOW_S,
    ttl_s: float = PROMISE_TTL_S,
) -> list[str]:
    """最近 24h 的 pending 承诺原话（≤3；先做 48h 惰性过期）。"""
    await store.expire_stale_promises(float(now), ttl_s=ttl_s)
    rows = await store.recent_pending_promises(
        float(now) - float(window_s), limit=MAX_PENDING_PROMISES
    )
    return [str(row.get("goal") or "").strip() for row in rows if row.get("goal")]


# ── T-021：承诺第二拍（答应的事当场写完）────────────────────────────
# T-022a 收窄：强制轮只留给"即时意图"（马上/这就/今晚就写），冷却 2 小时。
# 依据：用户要求"被推着做不符合期望"——强制只该发生在她已经说"现在就写"的时候；
# 其余情形交给记忆（T-022a 的 `unfinished_things`）与下一次开口。
FORCED_WRITE_COOLDOWN_S = 2.0 * 3600.0
# 同一回合最多补 1 次（一回合只挑一条承诺，不做轮询扫描）。
MAX_FORCED_WRITES_PER_TURN = 1


def forced_write_prompt(goal: str) -> str:
    """第二拍的指令（**只说这一句**；正文由她在 write_note 的参数里自己写）。"""
    return (
        f"你答应过要写：「{str(goal).strip()}」。"
        "现在用 write_note 写完它；正文你自己写，写它是什么样就是什么样。"
    )


def formed_log_text(path: str) -> str:
    """承诺兑现时落 `thought_log kind='promise'` 的那一行。"""
    return f"已成文：{str(path).strip()}"


async def next_forced_write_promise(
    store: Any,
    now: float,
    *,
    cooldown_s: float = FORCED_WRITE_COOLDOWN_S,
) -> dict[str, Any] | None:
    """挑一条该补第二拍的 pending 承诺；没有/都在冷却里 ⇒ None。

    - 只看 48h 内的 pending（先做惰性过期——过期的承诺不该再追着要）；
    - 同一承诺的两次强制尝试间隔 ≥ `cooldown_s`（用 `attempts`/`last_attempt_ts`
      现成字段，不新增表结构）：刚失败过的下一轮自动跳过，十分钟后再试；
    - 旧的就先补（`recent_pending_promises` 按创建时间升序）。
    """
    await store.expire_stale_promises(float(now), ttl_s=PROMISE_TTL_S)
    rows = await store.recent_pending_promises(
        float(now) - PROMISE_TTL_S, limit=MAX_PENDING_PROMISES
    )
    for row in rows:
        last_attempt = row.get("last_attempt_ts")
        if last_attempt is None or float(now) - float(last_attempt) >= float(cooldown_s):
            return dict(row)
    return None
