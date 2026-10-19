"""O1 语音气泡单测：GPT-SoVITS -> wav -> ogg/opus -> Telegram sendVoice（离线，不联网）。

守护五件事（每件都配"还原旧写法 ⇒ 用例变红"的取消测试，见各用例 docstring）：

1. **配置**：`data/run/tts.json` 外置、模板**不带 BOM**、坏配置明确报错（不静默半残）。
2. **合成**：两种 api_style（当前 `post_json` / `api_v2` 的 `get_tts`）参数正确；
   服务不可达/返回非 WAV ⇒ `TTSError`（原因码可落日志）。
3. **ffmpeg**：wav -> ogg/opus；ffmpeg 缺失/失败 ⇒ 明确报错。
4. **幂等**：同一段文本 + 同一配置走缓存，**第二次不重复合成**。
5. **桥接降级**：语音任一步失败 ⇒ **发文本，消息不丢**；成功 ⇒ 只发 `sendVoice`；
   长文本按句切成多条语音；`sendVoice` 用 multipart（含 chat_id + ogg）。

不启停她、不碰她的库（thought_log 用 tmp_path 临时库）。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
TOKEN = "123456789:AAFakeTokenForTests_abcdefghijklmnopqrst"
T0 = 1_000_000.0


def _load(name: str, filename: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tts = _load("tts_voice_probe", SCRIPTS / "tts_voice.py")
bridge = _load("telegram_bridge_voice_probe", SCRIPTS / "telegram_bridge.py")


# ── 构造工具 ────────────────────────────────────────────────────────
def _wav() -> bytes:
    """最小合法 WAV 头（synthesize_wav 只校验 RIFF/WAVE，转换用假 runner）。"""
    return b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 36


def _cfg(tmp_path: Path, **overrides: Any) -> Any:
    ff = tmp_path / "ffmpeg.exe"
    ff.write_bytes(b"fake")
    base = tts.TTSConfig(
        api_url="http://127.0.0.1:9880",
        ffmpeg=str(ff),
        cache_dir=str(tmp_path / "cache"),
    )
    values = {**base.__dict__, **overrides}
    return tts.TTSConfig(**values)


class FakeTTSHttp:
    """替换 `tts._open_tts`：记录请求，返回预置 wav 或抛错。"""

    def __init__(self, wav: bytes | None = None, error: Exception | None = None) -> None:
        self.wav = wav if wav is not None else _wav()
        self.error = error
        self.requests: list[Any] = []

    def __call__(self, req: Any, timeout_s: float) -> bytes:
        self.requests.append(req)
        if self.error is not None:
            raise self.error
        return self.wav


class FakeRunner:
    """假 ffmpeg：把 OggS 写到命令最后一个参数（输出路径）。"""

    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_kw: Any) -> Any:
        self.calls.append(cmd)
        if self.ok:
            Path(cmd[-1]).write_bytes(b"OggS" + b"\x00" * 64)
        return SimpleNamespace(returncode=0 if self.ok else 1, stderr=b"" if self.ok else b"boom")


# ── 1. 配置 ────────────────────────────────────────────────────────
def test_write_template_is_bom_free_and_loadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--init` 模板必须是 **UTF-8 无 BOM** 且能被 load_config 读回（外置配置约定）。"""
    gpt = tmp_path / "GPT-SoVITS-fake"
    (gpt / "runtime").mkdir(parents=True)
    (gpt / "api.py").write_text("# fake", encoding="utf-8")
    (gpt / "runtime" / "ffmpeg.exe").write_bytes(b"fake")
    target = tmp_path / "tts.json"
    monkeypatch.setattr(tts, "_default_profiles", lambda: {})
    path = tts.write_template(target, root=str(gpt))
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "配置文件写了 BOM（本工程刚被 BOM 坑过）"
    cfg = tts.load_config(path)
    assert cfg.enabled is True and cfg.api_style == "post_json"
    assert cfg.ffmpeg.endswith("ffmpeg.exe")
    with pytest.raises(tts.TTSError):
        tts.write_template(target, root=str(gpt))  # 已存在拒绝覆盖


def test_config_missing_or_broken_is_explicit(tmp_path: Path) -> None:
    """配置缺失/坏 JSON ⇒ ConfigError（**明确拒绝**，不静默当"没语音"）。"""
    with pytest.raises(tts.ConfigError):
        tts.load_config(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(tts.ConfigError):
        tts.load_config(bad)


def test_config_tolerates_bom(tmp_path: Path) -> None:
    """配置若被老 PowerShell 写成带 BOM，也必须能读（但模板本身写 BOM-free）。"""
    p = tmp_path / "tts.json"
    p.write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps({"enabled": False, "api_url": "http://127.0.0.1:9880"}).encode("utf-8")
    )
    cfg = tts.load_config(p)
    assert cfg.enabled is False


# ── 2. 合成参数 / 失败 ─────────────────────────────────────────────
def test_post_json_sends_text_and_emotion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """当前真机的 `api.py` 形态：POST {text, emotion} 到 `/`。"""
    fake = FakeTTSHttp()
    monkeypatch.setattr(tts, "_open_tts", fake)
    cfg = _cfg(tmp_path)
    wav = tts.synthesize_wav(cfg, "今天过得怎么样", emotion="gentle")
    assert wav == _wav()
    req = fake.requests[0]
    assert req.get_method() == "POST"
    assert req.full_url == "http://127.0.0.1:9880/"
    body = json.loads(req.data.decode("utf-8"))
    assert body["text"] == "今天过得怎么样"
    assert body["emotion"] == "gentle"


def test_get_tts_style_uses_profile_params(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`api_v2.py` 形态：GET /tts?... 带 ref_audio_path/prompt_text/prompt_lang。"""
    fake = FakeTTSHttp()
    monkeypatch.setattr(tts, "_open_tts", fake)
    cfg = _cfg(
        tmp_path,
        api_style="get_tts",
        profiles={"default": tts.VoiceProfile("Elysia_Voice_Model/ref_default.wav", "嗨")},
    )
    tts.synthesize_wav(cfg, "在吗")
    url = fake.requests[0].full_url
    assert "/tts?" in url and "ref_audio_path=" in url and "prompt_text=" in url
    assert "prompt_lang=zh" in url


def test_tts_unreachable_raises_with_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTS 服务不可达 ⇒ TTSError("http_error")，由桥降级发文本（不是静默丢消息）。"""
    monkeypatch.setattr(tts, "_open_tts", FakeTTSHttp(error=tts.TTSError("http_error", "down")))
    with pytest.raises(tts.TTSError) as exc:
        tts.synthesize_wav(_cfg(tmp_path), "在吗")
    assert exc.value.reason == "http_error"


def test_non_wav_response_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """返回 200 但不是 WAV（比如错误页）⇒ bad_audio，绝不把垃圾当语音发出去。"""
    monkeypatch.setattr(tts, "_open_tts", FakeTTSHttp(wav=b"<html>oops</html>"))
    with pytest.raises(tts.TTSError) as exc:
        tts.synthesize_wav(_cfg(tmp_path), "在吗")
    assert exc.value.reason == "bad_audio"


# ── 3. ffmpeg ─────────────────────────────────────────────────────
def test_wav_to_ogg_uses_libopus_and_checks_magic(tmp_path: Path) -> None:
    """转换必须产出真 OGG（OggS 魔数）；命令里用 libopus / 48k / 单声道。"""
    runner = FakeRunner()
    ogg = tts.wav_to_ogg(_wav(), _cfg(tmp_path), runner=runner)
    assert ogg.startswith(b"OggS")
    cmd = runner.calls[0]
    assert "libopus" in cmd and "48000" in cmd and "-ac" in cmd


def test_ffmpeg_missing_or_failing_is_explicit(tmp_path: Path) -> None:
    """ffmpeg 不存在/失败 ⇒ 明确原因码（调用方据此降级发文本）。"""
    cfg_missing = _cfg(tmp_path, ffmpeg=str(tmp_path / "no-ffmpeg.exe"))
    with pytest.raises(tts.TTSError) as exc:
        tts.wav_to_ogg(_wav(), cfg_missing, runner=FakeRunner())
    assert exc.value.reason == "ffmpeg_missing"

    with pytest.raises(tts.TTSError) as exc2:
        tts.wav_to_ogg(_wav(), _cfg(tmp_path), runner=FakeRunner(ok=False))
    assert exc2.value.reason == "ffmpeg_failed"


# ── 4. 幂等（缓存）────────────────────────────────────────────────
def test_same_text_is_synthesized_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一条文本第二次走缓存，**不再请求 TTS**（不重复合成；GPU 与费用都省）。

    取消测试：去掉缓存读取 ⇒ 本用例的 `len(fake.requests) == 1` 变 2，必红。
    """
    fake = FakeTTSHttp()
    monkeypatch.setattr(tts, "_open_tts", fake)
    runner = FakeRunner()
    cfg = _cfg(tmp_path)
    first = tts.synthesize_ogg(cfg, "我在呢", runner=runner)
    second = tts.synthesize_ogg(cfg, "我在呢", runner=runner)
    assert first == second and first.startswith(b"OggS")
    assert len(fake.requests) == 1, "同一段文本被重复合成了"
    assert len(runner.calls) == 1


# ── 5. 长文本按句切 ───────────────────────────────────────────────
def test_split_for_voice_keeps_chunks_bounded() -> None:
    """ "长文本按句切"：每段 <= 上限，且不丢内容（避免一个 2MB 语音）。"""
    text = "第一句比较短。" * 20
    chunks = tts.split_for_voice(text, 50)
    assert len(chunks) > 1
    assert all(0 < len(c) <= 50 for c in chunks)
    assert "".join(chunks) == text


# ── 6. 桥接：语音成功 / 降级文本 ───────────────────────────────────
class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.voices: list[bytes] = []
        self.edits: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.fail_voice = False

    def __call__(self, req: Any, timeout_s: float) -> bytes:
        method = str(req.full_url).rsplit("/", 1)[-1]
        self.calls.append(method)
        if method == "sendVoice":
            # multipart 不是 JSON：只取文件段
            if self.fail_voice:
                raise urllib.error.URLError("voice down")
            self.voices.append(_ogg_from_multipart(req.data))
            return json.dumps(
                {"ok": True, "result": {"message_id": 900 + len(self.voices)}}
            ).encode()
        payload = json.loads(req.data.decode("utf-8")) if req.data else {}
        if method == "sendMessage":
            self.sent.append(dict(payload))
            return json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
        if method == "editMessageText":
            self.edits.append(dict(payload))
            return json.dumps({"ok": True, "result": True}).encode()
        return json.dumps({"ok": True, "result": []}).encode()


def _ogg_from_multipart(body: bytes) -> bytes:
    """从 multipart body 里取出文件段（测试用，够简单即可）。"""
    marker = b"Content-Type: audio/ogg\r\n\r\n"
    start = body.index(marker) + len(marker)
    end = body.index(b"\r\n--", start)
    return body[start:end]


def _state_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.commit()
    finally:
        con.close()
    return path


def _heartbeat_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    try:
        con.executescript(
            "CREATE TABLE IF NOT EXISTS thought_log ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
            " kind TEXT NOT NULL, text TEXT NOT NULL);"
        )
        con.commit()
    finally:
        con.close()
    return path


def _bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, voice: Any
) -> tuple[Any, FakeTelegram, Path, Path]:
    fake = FakeTelegram()
    monkeypatch.setattr(bridge, "_open_telegram", fake)
    state = _state_db(tmp_path / "state.db")
    hb = _heartbeat_db(tmp_path / "heartbeat.db")
    chat = tmp_path / "run" / "telegram_chat.txt"
    chat.parent.mkdir(parents=True, exist_ok=True)
    chat.write_text("111\n", encoding="utf-8")
    b = bridge.TelegramBridge(
        TOKEN,
        api=bridge.TelegramAPI(TOKEN),
        state_db=state,
        heartbeat_db=hb,
        chat_file=chat,
        pid_file=tmp_path / "run" / "tg.pid",
        tts_config_path=tmp_path / "tts.json",  # 不存在：靠注入的 voice 生效
        voice=voice,
    )
    return b, fake, state, hb


def _add_bubble(hb: Path, text: str) -> None:
    con = sqlite3.connect(hb)
    try:
        con.execute(
            "INSERT INTO thought_log (ts, kind, text) VALUES (?, 'expression', ?)", (T0, text)
        )
        con.commit()
    finally:
        con.close()


def test_voice_success_sends_send_voice_not_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """语音成功 ⇒ 只发 `sendVoice` 语音气泡（不再发同一条文本）。"""
    calls: list[str] = []

    def voice(text: str) -> bytes:
        calls.append(text)
        return b"OggS" + b"\x00" * 32

    b, fake, _state, hb = _bridge(tmp_path, monkeypatch, voice)
    b.initialize_cursor()
    _add_bubble(hb, "我在呢。")
    assert b.poll_expression_once() == 1
    assert fake.voices and not fake.sent
    assert calls == ["我在呢。"]
    assert fake.calls.count("sendVoice") == 1


def test_voice_failure_falls_back_to_text_and_keeps_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """语音失败（合成/发送任一步）⇒ **降级发文本，消息不丢**；并记录原因。

    取消测试：把降级分支改成直接 `raise`（或跳过文本发送）⇒ 本用例 `fake.sent == []`，必红。
    """

    def voice(_text: str) -> bytes:
        raise tts.TTSError("timeout", "服务没响应")

    b, fake, _state, hb = _bridge(tmp_path, monkeypatch, voice)
    b.initialize_cursor()
    _add_bubble(hb, "我在呢。")
    assert b.poll_expression_once() == 1
    assert [m["text"] for m in fake.sent] == ["我在呢。"]
    assert fake.voices == []
    assert b.last_voice_error == "timeout"


def test_voice_long_text_is_split_into_multiple_voice_bubbles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """长文本 ⇒ 多条语音（每段不超上限），而不是一个巨大的语音文件。"""
    texts: list[str] = []

    def voice(text: str) -> bytes:
        texts.append(text)
        return b"OggS" + b"\x00" * 16

    b, fake, _state, hb = _bridge(tmp_path, monkeypatch, voice)
    b.initialize_cursor()
    _add_bubble(hb, "第一句。" * 40)  # 160 字，超过默认 120 的切分上限
    assert b.poll_expression_once() >= 1
    assert len(texts) >= 2
    assert len(fake.voices) == len(texts)
    assert not fake.sent


def test_send_voice_uses_multipart_and_never_leaks_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sendVoice` 必须是 multipart（chat_id + voice 文件），且错误信息里不带 token。"""
    fake = FakeTelegram()
    monkeypatch.setattr(bridge, "_open_telegram", fake)
    api = bridge.TelegramAPI(TOKEN)
    api.send_voice(111, b"OggS" + b"\x00" * 8)
    assert fake.calls == ["sendVoice"]
    assert fake.voices and fake.voices[0].startswith(b"OggS")

    # 失败路径：异常文本里不得出现 token（URL 路径含 token）
    def boom(req: Any, timeout_s: float) -> bytes:
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(bridge, "_open_telegram", boom)
    with pytest.raises(bridge.TelegramError) as exc:
        api.send_voice(111, b"OggS")
    assert TOKEN not in str(exc.value)


def test_voice_bubbles_are_not_resent_on_next_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """幂等（发送侧）：同一条 thought 只发一次；下一次 poll 不会重复发语音。"""
    count = 0

    def voice(_text: str) -> bytes:
        nonlocal count
        count += 1
        return b"OggS" + b"\x00" * 8

    b, fake, _state, hb = _bridge(tmp_path, monkeypatch, voice)
    b.initialize_cursor()
    _add_bubble(hb, "我在呢。")
    assert b.poll_expression_once() == 1
    assert b.poll_expression_once() == 0
    assert count == 1 and len(fake.voices) == 1
