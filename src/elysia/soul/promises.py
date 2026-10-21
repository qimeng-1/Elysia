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
"""

from __future__ import annotations

import re
from typing import Any

PROMISE_WINDOW_S = 24.0 * 3600.0  # 提醒窗口
PROMISE_TTL_S = 48.0 * 3600.0  # 惰性过期
MAX_PENDING_PROMISES = 3
PROMISE_GOAL_CHARS = 120

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
_REJECT_RE = re.compile(
    r"(?:写不|写没|没写|不想写|不写了|不肯写|懒得写|写不动|写完了|写好了|写过了|已经写了)"
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
    return int(intention_id)


async def settle_after_reply(
    store: Any,
    text: str,
    now: float,
    *,
    wrote: bool,
    user_message: str | None = None,
    window_s: float = PROMISE_WINDOW_S,
) -> dict[str, Any]:
    """一次开口落库后的承诺处理：兑现 / 放弃 / 捕获（三者互斥，不代写）。

    `user_message` 是用户这一轮的原话（上下文判据要它；None ⇒ 只看显式自诺）。
    """
    since = float(now) - float(window_s)
    if wrote:
        await store.close_pending_promises(float(now), since, status="done", outcome="她写了")
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


def promise_hint(promises: list[str]) -> str:
    """提醒指令（写进 payload 文本；不是命令：写/推迟/说不写都合法）。"""
    joined = "；".join(f"「{item}」" for item in promises)
    return (
        f"你答应过的事还没做：{joined}。"
        "现在就用 write_note 写完它；不想写就明说不写了——两条都算数。"
    )


# ── T-021：承诺第二拍（答应的事当场写完）────────────────────────────
# 同一个 promise 两次强制尝试之间至少隔 10 分钟（下一轮再试；不设次数配额）。
FORCED_WRITE_COOLDOWN_S = 600.0
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
