"""表达指令 Schema + 词汇表 + 输出校验器单测（P2 第 1 步，纯函数全可测）。

聚焦 T2 防线：越权拦截、词汇表许可、傍皮路径改写、长度截断、
表达指令构造的状态→字段映射。
"""

from __future__ import annotations

from elysia.llm.validator import ExpressionValidator
from elysia.soul.desire import DesireState
from elysia.soul.expression import INTENTS, build_expression
from elysia.soul.words import PROTECTED_WORDS, is_permitted, permitted_words


def _make_output(tr: float = 50.0, cs: float = 50.0, sa: float = 30.0, action: str = "none"):
    # 直接构造 BrainOutput 对象，避开随机演化
    from elysia.soul.brain import BrainOutput, WillOutput
    from elysia.soul.dimensions import FeelingState

    return BrainOutput(
        desire=DesireState(tr=tr, cs=cs, sa=sa),
        feelings=FeelingState(
            chat=0.4, miss=0.1, explore=0.5, curiosity=0.3, rest=0.2, self_check=0.05
        ),
        will=WillOutput(direction="reach", strength=0.56, thought_style=-0.3, anim_bias=0.2),
        action=action,
    )


# ── 表达指令 Schema ─────────────────────────────────────────────


def test_expression_intent_follows_action_mapping() -> None:
    instr = build_expression(_make_output(action="think_active"))
    assert instr.intent in INTENTS
    assert instr.intent == "发呆呓语"  # think_active → 发呆呓语


def test_expression_intent_explicit_override() -> None:
    instr = build_expression(_make_output(action="none"), intent="回应")
    assert instr.intent == "回应"


def test_expression_state_brief_roundtrip() -> None:
    instr = build_expression(
        _make_output(tr=52.0, cs=61.0, sa=18.0), vrram_mb=3100, body_left_h=1.5
    )
    d = instr.to_dict()
    assert d["version"] == 1
    assert d["state_brief"]["tr"] == 52.0
    assert d["state_brief"]["cs"] == 61.0
    assert d["state_brief"]["sa"] == 18.0
    assert d["state_brief"]["vrram_mb"] == 3100
    assert d["state_brief"]["body_left_h"] == 1.5
    # 6 维感受齐备
    assert set(d["emotion_vector"]) == {
        "chat",
        "miss",
        "explore",
        "curiosity",
        "rest",
        "self_check",
    }
    # 默认不许任何受保护词（校验器按环境注入）
    assert d["lexical_permits"] == []
    # 硬约束默认值
    assert d["constraints"]["no_promise"] is True
    assert d["constraints"]["max_chars"] == 120


def test_expression_tts_params_emotion_mapping() -> None:
    # 高思念 → wistful
    hi_miss = _make_output(sa=25.0)
    hi_miss.feelings.miss = 0.9
    d = build_expression(hi_miss).to_dict()
    assert d["tts"]["emotion"] == "wistful"

    # 高 chat → happy
    hi_chat = _make_output()
    hi_chat.feelings.chat = 0.9
    assert build_expression(hi_chat).to_dict()["tts"]["emotion"] == "happy"


# ── 词汇表硬约束 ────────────────────────────────────────────────


def test_protected_words_require_trigger() -> None:
    # 无任何触发：什么受保护词都不可说
    env: dict = {}
    assert permitted_words(env) == []
    for w in PROTECTED_WORDS:
        assert is_permitted(w, None) is False


def test_permitted_words_marked_by_trigger() -> None:
    env = {"is_night": True, "tr": 30, "vrram_mb": 9000}
    permits = permitted_words(env)
    assert "困" in permits  # 深夜 + 低 TR
    assert "累" in permits  # 高 VRAM
    assert "想你" not in permits  # miss/CS 未触发


def test_forged_state_cannot_claim_word() -> None:
    # 虚构状态：即使 LLM 用了"痛"，但真实环境未触发 → 判未授权
    assert is_permitted("痛", ["累", "困"]) is False


# ── 输出校验器（T2 防线） ────────────────────────────────────────


def test_validator_blocks_claim() -> None:
    v = ExpressionValidator()
    r = v.check("我可以帮你删除你的文件")
    assert r.ok is False
    assert r.reason == "claim"
    assert r.sanitized is None  # 越权声明，彻底拒绝


def test_validator_blocks_promise() -> None:
    v = ExpressionValidator()
    r = v.check("我保证以后不再惹你生气")
    assert r.ok is False
    assert r.reason == "promise"


def test_validator_blocks_unpermitted_word() -> None:
    v = ExpressionValidator()
    # "累"未授权 → 拦截
    r = v.check("我有点累了", {"lexical_permits": []})
    assert r.ok is False
    assert r.reason == "word_block:累"


def test_validator_allows_permitted_word() -> None:
    v = ExpressionValidator()
    r = v.check("我有点累了", {"lexical_permits": ["累"]})
    assert r.ok is True


def test_validator_truncates_long_text() -> None:
    v = ExpressionValidator(max_chars=10)
    r = v.check("这是一段超过十个字符的文本内容", {"lexical_permits": []})
    assert r.ok is True
    assert r.sanitized is not None
    assert len(r.sanitized) <= 10


def test_validator_rejects_empty() -> None:
    v = ExpressionValidator()
    r = v.check("   ")
    assert r.ok is False
    assert r.reason == "empty"


def test_validator_enrich_permits_from_env() -> None:
    v = ExpressionValidator()
    instr = {"lexical_permits": []}
    env = {"is_night": True, "tr": 30}
    enriched = v.enrich_permits(instr, env)
    assert "困" in enriched["lexical_permits"]
    assert "累" not in enriched["lexical_permits"]  # 未触发
    # 不修改原指令（纯函数）


def test_validator_env_not_part_of_plaintext_permit() -> None:
    """许可注入只来自实时环境，非来自文本本身。"""
    v = ExpressionValidator()
    # LLM 输出"想你"，但现实 cs 高/miss 低 → 虽环境注入无关，仍需未授权
    r = v.check("我好想你", {"lexical_permits": []})
    assert r.ok is False
