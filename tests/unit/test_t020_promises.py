"""T-020（N1）：她说"我这就写"→ 记成她自己的承诺 → 下一次开口提醒 → 兑现/关闭。

原则：系统绝不代写、绝不计配额、绝不改她的话。写、推迟、说不写了，三种都合法。
取消测试（每条都能红）：
- 去掉捕获 ⇒ 用例 1 红；去掉"已真写就不记" ⇒ 用例 2 红；
- 去掉 payload 提醒 ⇒ 用例 3 红；去掉"成功写 ⇒ done" ⇒ 用例 4 红；
- 去掉 48h 惰性过期 ⇒ 用例 5 红；去掉 worker 跳过 promies ⇒ 用例 6 红。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore, StateStore
from elysia.llm.validator import ValidationResult
from elysia.soul.brain import BrainOutput, WillOutput
from elysia.soul.desire import DesireState
from elysia.soul.dimensions import FeelingState
from elysia.soul.expression_service import ExpressionService
from elysia.soul.expression_types import (
    STATUS_SUCCESS,
    ExpressionJob,
    ExpressionResult,
)
from elysia.soul.intentions import Intentions
from elysia.soul.promises import (
    capture_promise,
    detect_contextual_promise,
    detect_promise,
    has_write_request,
    is_abandonment,
    list_pending_promises,
    settle_after_reply,
)
from elysia.soul.tools.writing import WritingWorkspace

T0 = 1_000_000.0


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text

    async def speak(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            text=self.text,
            level="main",
            validated=ValidationResult(ok=True, reason=None),
        )


def _job(text: str | None = None) -> ExpressionJob:
    return ExpressionJob(
        job_id="job-1",
        created_ts=T0,
        heartbeat_seq=1,
        output=BrainOutput(
            desire=DesireState(),
            feelings=FeelingState(chat=0.6, miss=0.4),
            will=WillOutput(thought_style=0.1),
        ),
        summary={},
        user_message=text,
        intent="回应",
    )


def _result(text: str) -> ExpressionResult:
    return ExpressionResult(
        job_id="job-1",
        status=STATUS_SUCCESS,
        order_key=(1, T0),
        text=text,
        level="main",
        validation=ValidationResult(ok=True, reason=None),
        instruction={},
        intent="回应",
        feelings={"chat": 0.6, "miss": 0.4},
        sentences=[text],
    )


async def _service(
    tmp_path: Path, *, text: str = "嗯。"
) -> tuple[ExpressionService, HeartbeatStore, StateStore]:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    state = StateStore(tmp_path / "state.db")
    await store.start()
    await state.start()
    service = ExpressionService(
        _FakeLLM(text),  # type: ignore[arg-type]
        store,
        writing_workspace=WritingWorkspace(tmp_path / "home"),
    )
    return service, store, state


# ── 1. 捕获 ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_capture_promise_after_reply(tmp_path: Path) -> None:
    service, store, state = await _service(tmp_path)
    try:
        await service.persist(_result("我这就写下来给你看。"), now=T0 + 1)
        rows = await store.execute_raw(
            "SELECT goal, action, kind, status, target FROM intentions ORDER BY id DESC LIMIT 1"
        )
        assert rows, "说出口的承诺没有被记下来"
        goal, action, kind, status, target = rows[0]
        assert kind == "promise" and status == "pending" and action == "write_note"
        assert target is None
        assert "我这就写下来给你看" in goal
        promise_logs = await store.execute_raw(
            "SELECT text FROM thought_log WHERE kind = 'promise'"
        )
        assert promise_logs and "我这就写下来给你看" in promise_logs[0][0]
    finally:
        await state.close()
        await store.close()


# ── 2. 已真写 ⇒ 不记 ─────────────────────────────────────
@pytest.mark.asyncio
async def test_no_capture_when_write_note_succeeded(tmp_path: Path) -> None:
    service, store, state = await _service(tmp_path)
    try:
        service._job_write_note_ok = True
        await service.persist(_result("我这就写下来给你看。"), now=T0 + 1)
        assert not [
            row
            for row in await store.execute_raw("SELECT kind FROM intentions")
            if row[0] == "promise"
        ], "已经真写了，却还记了一条承诺"
    finally:
        await state.close()
        await store.close()


# ── 3. payload：她自己的"未完成之事"（T-022a：不再有系统提醒字段）──
@pytest.mark.asyncio
async def test_payload_carries_unfinished_things_only(tmp_path: Path) -> None:
    service, store, state = await _service(tmp_path, text="嗯。")
    try:
        await store.add_intention(
            T0 - 100,
            goal="我待会儿把那篇写完给你看",
            action="write_note",
            target=None,
            kind="promise",
        )
        result = await service.run_job(_job("在吗"))
        payload = result.instruction
        # T-022a：正常回合不再出现系统提醒字段
        assert "pending_promises" not in payload
        assert "pending_promises_hint" not in payload
        things = payload.get("unfinished_things")
        assert isinstance(things, list) and things
        assert any("我待会儿把那篇写完给你看" in item for item in things)
        assert all(isinstance(item, str) for item in things)
        assert len(things) <= 3
        # 正常回合（<24h、之后没开口过）不出现兜底问句
        assert "promise_reminder" not in payload
    finally:
        await state.close()
        await store.close()


# ── 4. 成功写 ⇒ done ─────────────────────────────────────
@pytest.mark.asyncio
async def test_successful_write_closes_promise_done(tmp_path: Path) -> None:
    service, store, state = await _service(tmp_path)
    try:
        pid = await store.add_intention(
            T0 - 100, goal="我这就写下来", action="write_note", target=None, kind="promise"
        )
        service._job_write_note_ok = True
        await service.persist(_result("写好了，你看看。"), now=T0 + 1)
        intention = await store.get_intention(int(pid))
        assert intention is not None
        assert intention["status"] == "done"
        assert intention["outcome"] == "她写了"
        assert intention["finished_ts"] == T0 + 1
    finally:
        await state.close()
        await store.close()


# ── 5. 48h 惰性过期 ─────────────────────────────────────
@pytest.mark.asyncio
async def test_promises_expire_lazily_after_48h(tmp_path: Path) -> None:
    _service_unused, store, state = await _service(tmp_path)
    try:
        old = await store.add_intention(
            T0 - 49 * 3600,
            goal="很久以前说要写",
            action="write_note",
            target=None,
            kind="promise",
        )
        fresh = await store.add_intention(
            T0 - 47 * 3600,
            goal="还不到 48 小时",
            action="write_note",
            target=None,
            kind="promise",
        )
        # 读取（list_pending_promises）即触发 48h 惰性过期；24h 窗口外只是不提醒
        pending = await list_pending_promises(store, T0)
        assert "很久以前说要写" not in pending
        old_row = await store.get_intention(int(old))
        fresh_row = await store.get_intention(int(fresh))
        assert old_row is not None and old_row["status"] == "expired"
        assert fresh_row is not None and fresh_row["status"] == "pending"
    finally:
        await state.close()
        await store.close()


# ── 6. Worker 跳过 promise ───────────────────────────────
@pytest.mark.asyncio
async def test_worker_does_not_execute_promises(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    calls: list[str] = []
    try:
        await store.add_intention(
            T0, goal="我这就写", action="write_note", target="x.md", kind="promise"
        )

        async def _read_url(_url: str) -> Any:
            calls.append("read")
            return None

        intentions = Intentions(store, read_url_fn=_read_url)  # type: ignore[arg-type]
        assert await intentions.run_once(T0 + 1) is None
        assert calls == [], "worker 把她的口头承诺当任务执行了"
        pending = await store.get_intention(1)
        assert pending is not None and pending["status"] == "pending"
    finally:
        await store.close()


# ── 明确放弃 ⇒ expired ───────────────────────────────────
@pytest.mark.asyncio
async def test_abandonment_expires_promise(tmp_path: Path) -> None:
    service, store, state = await _service(tmp_path)
    try:
        pid = await store.add_intention(
            T0 - 100, goal="我这就写下来", action="write_note", target=None, kind="promise"
        )
        await service.persist(_result("算了，不写了。"), now=T0 + 1)
        intention = await store.get_intention(int(pid))
        assert intention is not None
        assert intention["status"] == "expired"
        assert intention["outcome"] == "她自己说不写了"
    finally:
        await state.close()
        await store.close()


def test_real_machine_sentence_after_comma_is_captured() -> None:
    """T-020c 真机句：前面的「不催我」与写作无关，后半截「写完喊你」必须被捕获。"""
    assert detect_promise("嗯，知道了，不催我正好～那我就慢慢写，写完喊你。") is not None


def test_detect_promise_and_abandonment() -> None:
    # T-020b/T-020c 负例：过去时 / 否定 / 无力 ⇒ 不是承诺（否定只认写作动词附近）
    assert detect_promise("我写不出来。") is None
    assert detect_promise("我写不下去。") is None
    assert detect_promise("我写完了。") is None
    assert detect_promise("我写好了。") is None
    assert detect_promise("我昨天写过了。") is None
    assert detect_promise("我还没写完呢。") is None
    assert detect_promise("我一个字都没写。") is None
    assert detect_promise("我写不动了。") is None
    assert detect_promise("我没写完，别催。") is None
    # 正例
    assert detect_promise("我这就写下来给你看。") is not None
    assert detect_promise("我待会儿写好给你") is not None

    # 放弃：普通「算了」不算；必须有写作语境或封闭式说法（T-020b 语义不回退）
    assert is_abandonment("算了，别管它。") is False
    assert is_abandonment("那个方案算了。") is False
    assert is_abandonment("算了，不写了。") is True
    assert is_abandonment("我写不动了。") is True
    assert is_abandonment("我不想写了。") is True
    assert is_abandonment("这事先搁着。") is True


@pytest.mark.asyncio
async def test_abandonment_closes_only_latest_promise(tmp_path: Path) -> None:
    """T-020b：明确放弃只关**最新一条**，不连带关光。"""
    service, store, state = await _service(tmp_path)
    try:
        older = await store.add_intention(
            T0 - 200, goal="早上说要写的那篇", action="write_note", target=None, kind="promise"
        )
        newer = await store.add_intention(
            T0 - 100, goal="刚才说要写的那篇", action="write_note", target=None, kind="promise"
        )
        await service.persist(_result("算了，不写了。"), now=T0 + 1)
        older_row = await store.get_intention(int(older))
        newer_row = await store.get_intention(int(newer))
        assert older_row is not None and older_row["status"] == "pending"
        assert newer_row is not None and newer_row["status"] == "expired"
    finally:
        await state.close()
        await store.close()


# ══════════════════════════════════════════════════════════════════════
# T-020d：上下文判据（用户请她写 + 她肯定答复）—— 真机 00:38–00:59 三句
#
# 真机取证：她答「算数。说过的就写」「还写。你睡你的」「放在我自己的文件夹里，
# 好。」——都不是"我这就写"的句式，T-020 的显式自诺一条都没命中。本组用例
# 每条都做过取消测试（把对应修复还原 ⇒ 必须红），见各自 docstring。
# ══════════════════════════════════════════════════════════════════════

_USER_ASKS_WRITE = "写一下爱莉希雅到底是个什么样的人"


async def _promise_rows(store: HeartbeatStore) -> list[tuple[Any, ...]]:
    return list(
        await store.execute_raw(
            "SELECT goal, action, kind, status, target FROM intentions"
            " WHERE kind = 'promise' ORDER BY id"
        )
    )


async def _say(
    service: ExpressionService,
    *,
    user_message: str | None,
    her_text: str,
    now: float = T0 + 1,
    wrote: bool = False,
) -> None:
    """走真实链路：run_job（worker 段，注入 user_message）→ persist（心跳落库段）。"""
    llm = service._llm
    assert isinstance(llm, _FakeLLM)
    llm.text = her_text
    result = await service.run_job(_job(user_message))
    service._job_write_note_ok = wrote
    await service.persist(result, now=now)


# ── 1. 真机句「算数。说过的就写，我这会儿还没写完」────────────────
@pytest.mark.asyncio
async def test_contextual_capture_real_machine_reply_suan_shu(tmp_path: Path) -> None:
    """用户请她写 + 她答「算数」+ 本回合没写 ⇒ 记一条承诺。

    取消测试：把 `settle_after_reply` 的 `user_message` 传参还原（None）⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(
            service,
            user_message=_USER_ASKS_WRITE,
            her_text="算数。说过的就写，我这会儿还没写完",
        )
        rows = await _promise_rows(store)
        assert rows, "用户请她写、她答『算数』，承诺却没被记下来"
        goal, action, kind, status, target = rows[0]
        assert (kind, status, action, target) == ("promise", "pending", "write_note", None)
        assert "算数" in goal, f"goal 里没有她那句肯定原话：{goal!r}"
        assert len(goal) <= 120
        logs = await store.execute_raw("SELECT text FROM thought_log WHERE kind = 'promise'")
        assert logs and "承诺" in logs[0][0], "承诺没有落 thought_log kind='promise'"
    finally:
        await state.close()
        await store.close()


# ── 2. 真机句「还写。你睡你的，写完这句我就接着写。」────────────────
@pytest.mark.asyncio
async def test_contextual_capture_real_machine_reply_hai_xie(tmp_path: Path) -> None:
    """用户请她写 + 她答「还写」+ 本回合没写 ⇒ 记一条承诺。

    取消测试：同上（去掉上下文判据）⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(
            service,
            user_message=_USER_ASKS_WRITE,
            her_text="还写。你睡你的，写完这句我就接着写。",
        )
        rows = await _promise_rows(store)
        assert rows, "用户请她写、她答『还写』，承诺却没被记下来"
        goal, action, kind, status, target = rows[0]
        assert (kind, status, action, target) == ("promise", "pending", "write_note", None)
        assert "还写" in goal, f"goal 里没有她那句肯定原话：{goal!r}"
    finally:
        await state.close()
        await store.close()


# ── 3. 真机句「放在我自己的文件夹里，好。明天你来看的时候…」────────
@pytest.mark.asyncio
async def test_contextual_capture_real_machine_reply_folder_hao(tmp_path: Path) -> None:
    """用户请她写 + 她答「好」+ 本回合没写 ⇒ 记一条承诺。

    取消测试：同上（去掉上下文判据）⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(
            service,
            user_message=_USER_ASKS_WRITE,
            her_text="放在我自己的文件夹里，好。明天你来看的时候，它就在那儿了。",
        )
        rows = await _promise_rows(store)
        assert rows, "用户请她写、她答『好』，承诺却没被记下来"
        goal, action, kind, status, target = rows[0]
        assert (kind, status, action, target) == ("promise", "pending", "write_note", None)
        assert "好" in goal and "文件夹" in goal, f"goal 不是她那句原话：{goal!r}"
    finally:
        await state.close()
        await store.close()


# ── 4. 她明确不写 ⇒ 不新增；已有 pending 变 expired ─────────────────
@pytest.mark.asyncio
async def test_contextual_capture_skips_explicit_abandonment(tmp_path: Path) -> None:
    """用户请她写 + 她答「不写了，算了」⇒ 不新增，已有 pending 变 expired。

    取消测试：删掉 `settle_after_reply` 的 `is_abandonment` 分支 ⇒ 旧承诺停在
    pending（本用例后半红）。
    """
    service, store, state = await _service(tmp_path)
    try:
        old = await store.add_intention(
            T0 - 100, goal="我这就写下来", action="write_note", target=None, kind="promise"
        )
        await _say(
            service,
            user_message=_USER_ASKS_WRITE,
            her_text="不写了，算了。",
        )
        rows = await _promise_rows(store)
        assert len(rows) == 1, f"她明明说了不写，却又记了承诺：{rows}"
        old_row = await store.get_intention(int(old))
        assert old_row is not None and old_row["status"] == "expired"
    finally:
        await state.close()
        await store.close()


# ── 5. 没有写作请求 ⇒ 不记 ────────────────────────────────────────
@pytest.mark.asyncio
async def test_contextual_capture_requires_write_request(tmp_path: Path) -> None:
    """用户只是在闲聊（没有请她写）⇒ 不记。

    取消测试：把上下文判据退化成"只要有用户输入就记"（跳过
    `has_write_request` 与肯定答复两条）⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(service, user_message="你在干嘛", her_text="我在呀")
        rows = await _promise_rows(store)
        assert not rows, f"用户没请她写，却记了承诺：{rows}"
    finally:
        await state.close()
        await store.close()


# ── 6. 本回合真写了 ⇒ 不记 ───────────────────────────────────────
@pytest.mark.asyncio
async def test_contextual_capture_skipped_when_write_note_succeeded(tmp_path: Path) -> None:
    """用户请她写 + 她答「好」，但本回合 write_note 成功 ⇒ 不记。

    取消测试：去掉 `detect_contextual_promise`/`capture_promise` 里的 `wrote`
    短路 ⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(service, user_message=_USER_ASKS_WRITE, her_text="好", wrote=True)
        rows = await _promise_rows(store)
        assert not rows, f"本回合已经真写了，却还记了一条承诺：{rows}"
    finally:
        await state.close()
        await store.close()


# ── 7. 同一请求连问两轮 ⇒ 只 1 条 ────────────────────────────────
@pytest.mark.asyncio
async def test_contextual_capture_dedupes_same_request(tmp_path: Path) -> None:
    """连续两轮同一条请求 + 两次肯定答复 ⇒ 只有 1 条 pending（去重）。

    取消测试：删掉 `capture_promise` 里的 `_is_duplicate_promise` ⇒ 本用例红
    （会多出 1 条）。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(
            service,
            user_message=_USER_ASKS_WRITE,
            her_text="算数。说过的就写，我这会儿还没写完",
            now=T0 + 1,
        )
        await _say(
            service,
            user_message=_USER_ASKS_WRITE,
            her_text="还写。你睡你的，写完这句我就接着写。",
            now=T0 + 2,
        )
        rows = await _promise_rows(store)
        assert len(rows) == 1, f"同一请求记了 {len(rows)} 条：{rows}"
        pending = await store.recent_pending_promises(T0 - 24 * 3600)
        assert len(pending) == 1
    finally:
        await state.close()
        await store.close()


# ── 8. 她推脱 ⇒ 不记 ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_contextual_capture_skips_refusal(tmp_path: Path) -> None:
    """用户请她写 + 她答「先不写吧，改天」⇒ 不记。

    取消测试：把肯定答复判据放宽成"她回复了就当她答应了"（跳过 `_REFUSAL`）
    ⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(service, user_message=_USER_ASKS_WRITE, her_text="先不写吧，改天")
        rows = await _promise_rows(store)
        assert not rows, f"她推脱了，却还记了承诺：{rows}"
    finally:
        await state.close()
        await store.close()


def test_detect_contextual_promise_guards() -> None:
    """上下文判据的纯函数面：请求/肯定/拒绝/已写四道闸。"""
    assert detect_contextual_promise("你在干嘛", "我在呀", wrote=False) is None
    assert detect_contextual_promise(_USER_ASKS_WRITE, "为什么问我呀？", wrote=False) is None
    assert detect_contextual_promise(_USER_ASKS_WRITE, "先不写吧，改天", wrote=False) is None
    assert detect_contextual_promise(_USER_ASKS_WRITE, "好", wrote=True) is None
    assert detect_contextual_promise("写代码吧", "好", wrote=False) is None
    captured = detect_contextual_promise(_USER_ASKS_WRITE, "好呀。", wrote=False)
    assert captured is not None and "写一下爱莉希雅" in captured
    assert has_write_request("帮我写一篇爱莉希雅")
    assert not has_write_request("写")


# ══════════════════════════════════════════════════════════════════════
# T-020e：条件①成立时，肯定信号放宽到"子句的核心动词就是写"
#
# 真机漏检（T-020d 上线后）：用户请她写，她答「写。今晚就写在文件夹里，天亮前
# 它会在。」——最直接的肯定就是「写。」，而 T-020d 只认「算数/还写/这就写」这类
# 更长的形式，于是 0 条 promise。以下每条都做过取消测试（见各自 docstring）。
# ══════════════════════════════════════════════════════════════════════

_REAL_MACHINE_WRITE_REQUEST = "那篇爱莉希雅——你要是还没写，今晚就写一下吧，写进你自己的文件夹里。"


async def _assert_single_pending_promise(store: HeartbeatStore) -> tuple[Any, ...]:
    rows = await _promise_rows(store)
    assert len(rows) == 1, f"应记且只记 1 条承诺，实际 {len(rows)} 条：{rows}"
    goal, action, kind, status, target = rows[0]
    assert (kind, status, action, target) == ("promise", "pending", "write_note", None)
    assert len(goal) <= 120
    return goal, action, kind, status, target


# ── 真机句「写。今晚就写在文件夹里，天亮前它会在。」──────────────
@pytest.mark.asyncio
async def test_contextual_capture_write_core_clause_real_machine(tmp_path: Path) -> None:
    """用户请她写 + 她答「写。今晚就写在文件夹里…」⇒ 记 1 条。

    取消测试：把 `_sentence_has_affirmation` 里的 T-020e 放宽判据还原
    （不再认子句级「写」）⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(
            service,
            user_message=_REAL_MACHINE_WRITE_REQUEST,
            her_text="写。今晚就写在文件夹里，天亮前它会在。",
        )
        goal, *_ = await _assert_single_pending_promise(store)
        assert "写" in goal
    finally:
        await state.close()
        await store.close()


# ── 「好，我写。」───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_contextual_capture_write_core_clause_wo_xie(tmp_path: Path) -> None:
    """用户请她写 + 她答「好，我写。」⇒ 记 1 条。

    取消测试：把「我写」从 `_AFFIRM_STRONG` 删掉、并去掉裸「好」的整子句肯定
    ⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(service, user_message=_REAL_MACHINE_WRITE_REQUEST, her_text="好，我写。")
        goal, *_ = await _assert_single_pending_promise(store)
        assert "我写" in goal
    finally:
        await state.close()
        await store.close()


# ── 「今晚就写。」───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_contextual_capture_write_core_clause_tonight(tmp_path: Path) -> None:
    """用户请她写 + 她答「今晚就写。」⇒ 记 1 条。

    取消测试：还原 T-020e 放宽判据（去掉时间 + 写作动作的子句模式）⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(service, user_message=_REAL_MACHINE_WRITE_REQUEST, her_text="今晚就写。")
        goal, *_ = await _assert_single_pending_promise(store)
        assert "今晚就写" in goal
    finally:
        await state.close()
        await store.close()


# ── 「不写。」⇒ 拒绝，不记 ──────────────────────────────────────
@pytest.mark.asyncio
async def test_contextual_capture_write_core_clause_rejects_bu_xie(tmp_path: Path) -> None:
    """用户请她写 + 她答「不写。」⇒ 不记（拒绝优先于子句级"写"）。

    取消测试：把子句级写作动作退化成"含『写』字就算肯定"、并去掉逐子句拒绝闸
    ⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(service, user_message=_REAL_MACHINE_WRITE_REQUEST, her_text="不写。")
        rows = await _promise_rows(store)
        assert not rows, f"她答『不写』，却记了承诺：{rows}"
    finally:
        await state.close()
        await store.close()


# ── 「写不出来。」⇒ 无力，不记 ──────────────────────────────────
@pytest.mark.asyncio
async def test_contextual_capture_write_core_clause_rejects_wu_li(tmp_path: Path) -> None:
    """用户请她写 + 她答「写不出来。」⇒ 不记（无力不是肯定）。

    取消测试：同上（去掉逐子句拒绝闸 + 把判据退化成"含『写』字"）⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(service, user_message=_REAL_MACHINE_WRITE_REQUEST, her_text="写不出来。")
        rows = await _promise_rows(store)
        assert not rows, f"她答『写不出来』，却记了承诺：{rows}"
    finally:
        await state.close()
        await store.close()


# ── 没有写作请求时，「写。」也不算承诺 ─────────────────────────────
@pytest.mark.asyncio
async def test_contextual_capture_write_core_clause_needs_request(tmp_path: Path) -> None:
    """用户只是闲聊（「你在干嘛」）+ 她答「写。」⇒ 不记（条件①不成立）。

    取消测试：跳过 `has_write_request`（不再要求"用户这轮在请她写"）⇒ 本用例红。
    """
    service, store, state = await _service(tmp_path)
    try:
        await _say(service, user_message="你在干嘛", her_text="写。")
        rows = await _promise_rows(store)
        assert not rows, f"用户没请她写，却记了承诺：{rows}"
    finally:
        await state.close()
        await store.close()


def test_write_core_clause_guards() -> None:
    """T-020e 纯函数面：放宽后的形式认、拒绝/无力不认、①不成立不认。"""
    assert detect_contextual_promise(_REAL_MACHINE_WRITE_REQUEST, "写。", wrote=False) is not None
    assert detect_contextual_promise(_REAL_MACHINE_WRITE_REQUEST, "写吧", wrote=False) is not None
    assert (
        detect_contextual_promise(_REAL_MACHINE_WRITE_REQUEST, "写下来。", wrote=False) is not None
    )
    assert (
        detect_contextual_promise(_REAL_MACHINE_WRITE_REQUEST, "写在文件夹里。", wrote=False)
        is not None
    )
    assert (
        detect_contextual_promise(_REAL_MACHINE_WRITE_REQUEST, "继续写。", wrote=False) is not None
    )
    assert detect_contextual_promise(_REAL_MACHINE_WRITE_REQUEST, "不写。", wrote=False) is None
    assert detect_contextual_promise(_REAL_MACHINE_WRITE_REQUEST, "写不出来。", wrote=False) is None
    assert detect_contextual_promise(_REAL_MACHINE_WRITE_REQUEST, "今晚就写？", wrote=False) is None
    assert detect_contextual_promise("你在干嘛", "写。", wrote=False) is None


# ── T-031：反编造诚实句不许被记成承诺（自激）────────────────────
# 缺陷：T-030 的诚实句「其实我还没真的写完呢。」会被 `_PROMISE_RESULT`（我…写完）
# 再捕获成一条新 pending 承诺 ⇒ "没做到"被系统记成"要做"。修法只加 `_REJECT_RE`
# 的未完成形态，不动 `_PROMISE_TIME` / `_PROMISE_RESULT`。
def test_honest_sentences_are_never_captured_as_promises() -> None:
    """遍历 honesty 的**每一个**诚实句（以后加新句自动覆盖）：一律不被捕获。

    取消测试：把 `_REJECT_RE` 的「没真的写完/还没真的写/还没写完」还原
    ⇒ 本用例红（write 句被 `detect_promise` 捕获成「其实我还没真的写完呢。」）。
    """
    from elysia.soul.honesty import _HONEST_SENTENCES

    assert _HONEST_SENTENCES, "诚实句表不能为空（否则遍历退化成永真）"
    for key, sentence in _HONEST_SENTENCES.items():
        assert detect_promise(sentence) is None, f"诚实句被记成承诺（{key}）：{sentence}"
        assert detect_promise(sentence + "。") is None, f"带标点的诚实句也不行（{key}）"


@pytest.mark.asyncio
async def test_settle_does_not_capture_honest_sentences(tmp_path: Path) -> None:
    """服务级：`settle_after_reply` 在诚实句上不新增 intention（不会自激成待办）。

    本次授权只动 `_REJECT_RE`（显式自诺路径）；`user_message=None` 即走该路径。
    """
    from elysia.soul.honesty import _HONEST_SENTENCES

    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        for key, sentence in _HONEST_SENTENCES.items():
            outcome = await settle_after_reply(store, sentence, T0, wrote=False)
            assert outcome["captured"] is None, f"诚实句被记成承诺（{key}）：{outcome}"
        assert await store.pending_intention_count() == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_real_promises_still_create_intentions(tmp_path: Path) -> None:
    """防修过头：收窄拒绝表后，真实承诺仍然能被捕获成 intention。

    「我今晚写」走的是 T-020d/e 的上下文判据（显式 `_PROMISE_TIME` 不含"今晚"，
    本任务红线不动那条正则），所以用 `capture_promise` 覆盖两条路径。
    """
    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        cases: list[tuple[str, str | None]] = [
            ("我这就写。", None),
            ("写完喊你。", None),
            ("我今晚写。", _REAL_MACHINE_WRITE_REQUEST),
        ]
        for text, user_message in cases:
            assert (
                detect_promise(text) is not None
                or detect_contextual_promise(user_message or "", text, wrote=False) is not None
            ), f"真实承诺的判据全部没认出：{text}"
            pid = await capture_promise(store, text, T0, wrote=False, user_message=user_message)
            assert pid is not None, f"真实承诺没有被记成 intention：{text}"
    finally:
        await store.close()


# ── T-032：上下文判据的第二入口也不许把诚实句记成承诺 ─────────────
_WRITE_REQUEST_FOR_CONTEXT = "那篇爱莉希雅今晚写一下吧，写进你自己的文件夹里。"


@pytest.mark.asyncio
async def test_contextual_capture_rejects_honest_sentences(tmp_path: Path) -> None:
    """用户请她写 + 她的回复是系统诚实句 ⇒ 上下文判据也不捕获（自激链第二入口）。

    取消测试：去掉 `detect_contextual_promise` 里的 `_REJECT_RE` 判断 ⇒ 本用例红
    （每个诚实句都会被上下文判据捕获，`settle_after_reply` 会新增 intention）。
    """
    from elysia.soul.honesty import _HONEST_SENTENCES

    store = HeartbeatStore(tmp_path / "heartbeat.db")
    await store.start()
    try:
        for key, sentence in _HONEST_SENTENCES.items():
            assert (
                detect_contextual_promise(_WRITE_REQUEST_FOR_CONTEXT, sentence, wrote=False) is None
            ), f"诚实句被上下文判据捕获（{key}）：{sentence}"
            outcome = await settle_after_reply(
                store, sentence, T0, wrote=False, user_message=_WRITE_REQUEST_FOR_CONTEXT
            )
            assert outcome["captured"] is None, f"诚实句被上下文判据记成承诺（{key}）：{outcome}"
        assert await store.pending_intention_count() == 0
    finally:
        await store.close()


def test_contextual_capture_still_accepts_real_affirmations() -> None:
    """防修过头：真实肯定答复在同样的写请求下仍然捕获。"""
    for her_text in ("好呀，今晚写。", "算数，我这就写。", "嗯，写。"):
        assert (
            detect_contextual_promise(_WRITE_REQUEST_FOR_CONTEXT, her_text, wrote=False) is not None
        ), f"真实肯定答复被误杀：{her_text}"


def test_contextual_capture_keeps_real_machine_utterance() -> None:
    """真机「算数。说过的就写，我这会儿还没写完」的**肯定句**是「算数。」⇒ 仍捕获。

    取消测试：把 `_REJECT_RE` 检查从"肯定句"挪到"整段"⇒ 本用例红（真承诺被整体丢掉）。
    """
    her = "算数。说过的就写，我这会儿还没写完"
    assert detect_contextual_promise(_REAL_MACHINE_WRITE_REQUEST, her, wrote=False) is not None
