"""TTS 语音气泡（O1）—— 她的话 -> GPT-SoVITS -> wav -> ffmpeg ogg/opus -> Telegram sendVoice。

事实核查（2026-10-19 真机，均已实测）
------------------------------------
- GPT-SoVITS v2Pro：`D:\\下载\\GPT-SoVITS-v2pro-20250604`
- 启动方式：`一键启动API.bat` -> `runtime\\python.exe api.py -hp`
  -> `POST http://127.0.0.1:9880/`（JSON `{"text": ..., "emotion": ...}` -> `audio/wav`）；
  `api_v2.py` 的 `GET /tts` 也支持（见 `api_style`）。
- 她的模型：`GPT_weights_v2/【GPT2.0】Elysia-e20.ckpt`
  + `SoVITS_weights_v2/【GPT2.0】Elysia_e24_s13080.pth`
- 参考音频：`Elysia_Voice_Model/ref_{default,devoted,gentle,playful}.wav`（4 段克隆参考）
- ffmpeg：`runtime\\ffmpeg.exe`（n4.3.2，本机已在 PATH 之外，配置里写绝对路径）
- GPU：RTX 3060 Laptop 6GB；API 加载后显存约 2.5GB（实测）。
- 实测：3.9 秒语音合成约 3 秒；ffmpeg 转 ogg/opus 后约 24KB。

配置（**外置，UTF-8 无 BOM**）
------------------------------
`data/run/tts.json`（首次可用 `python scripts/tts_voice.py --init` 生成模板）：

    {
      "enabled": true,
      "api_style": "post_json",              // "post_json"(api.py) | "get_tts"(api_v2.py)
      "api_url": "http://127.0.0.1:9880",
      "gpt_sovits_root": "D:/.../GPT-SoVITS-v2pro-20250604",
      "ffmpeg": "D:/.../runtime/ffmpeg.exe", // 留空则按 root/env/PATH 自动找
      "timeout_s": 120,
      "text_lang": "zh", "prompt_lang": "zh",
      "emotion": "default",
      "max_chars_per_voice": 120,            // 长文本按句切，避免一个 2MB 语音
      "opus_bitrate": "48k",
      "cache_dir": "data/cache/tts_voice",
      "profiles": { "default": {"ref_audio_path": "...", "prompt_text": "..."}, ... }
    }

约定
----
- **失败一律抛 `TTSError`（带原因码）**：调用方（Telegram 桥）据此**降级发文本，不丢消息**。
- **幂等**：同一段文本 + 配置 -> 同一个缓存 key（sha256）；合成结果落盘复用，不重复合成。
- 本模块只读她的库、只写 `data/cache/`，不碰 `src/`。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path("data") / "run" / "tts.json"
DEFAULT_CACHE_DIR = Path("data") / "cache" / "tts_voice"
DEFAULT_MAX_CHARS = 120
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?…；;])|\n+")


class TTSError(RuntimeError):
    """一次语音合成/转换失败。`reason` 是稳定的原因码（可落日志/前端显示）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class ConfigError(TTSError):
    def __init__(self, detail: str) -> None:
        super().__init__("config_invalid", detail)


@dataclass(frozen=True)
class VoiceProfile:
    ref_audio_path: str = ""
    prompt_text: str = ""


@dataclass(frozen=True)
class TTSConfig:
    enabled: bool = True
    api_style: str = "post_json"  # post_json | get_tts
    api_url: str = "http://127.0.0.1:9880"
    gpt_sovits_root: str = ""
    ffmpeg: str = ""
    timeout_s: float = 120.0
    ffmpeg_timeout_s: float = 60.0
    text_lang: str = "zh"
    prompt_lang: str = "zh"
    emotion: str = "default"
    max_chars_per_voice: int = DEFAULT_MAX_CHARS
    opus_bitrate: str = "48k"
    cache_dir: str = str(DEFAULT_CACHE_DIR)
    profiles: dict[str, VoiceProfile] = field(default_factory=dict)


def config_path(path: Path | None = None) -> Path:
    return Path(path) if path is not None else ROOT / DEFAULT_CONFIG


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def load_config(path: Path | None = None) -> TTSConfig:
    """读 `data/run/tts.json`；**容忍 BOM**（老 PowerShell 写的），但建议 BOM-free。"""
    p = config_path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise ConfigError(
            f"读不到配置 {p}（{type(exc).__name__}）；可运行 --init 生成模板"
        ) from exc
    try:
        # utf-8-sig：带 BOM 也能正确解析（本工程踩过 BOM 让 push_url 静默失效）
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"配置不是合法 UTF-8 JSON：{p}（{exc}）") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置顶层必须是对象：{p}")
    profiles_raw = data.get("profiles")
    profiles: dict[str, VoiceProfile] = {}
    if isinstance(profiles_raw, dict):
        for name, item in profiles_raw.items():
            if isinstance(item, dict):
                profiles[str(name)] = VoiceProfile(
                    ref_audio_path=str(item.get("ref_audio_path", "")),
                    prompt_text=str(item.get("prompt_text", "")),
                )
    return TTSConfig(
        enabled=_as_bool(data.get("enabled"), True),
        api_style=str(data.get("api_style", "post_json")).strip() or "post_json",
        api_url=str(data.get("api_url", "http://127.0.0.1:9880")).strip().rstrip("/"),
        gpt_sovits_root=str(data.get("gpt_sovits_root", "")).strip(),
        ffmpeg=str(data.get("ffmpeg", "")).strip(),
        timeout_s=_as_float(data.get("timeout_s"), 120.0),
        ffmpeg_timeout_s=_as_float(data.get("ffmpeg_timeout_s"), 60.0),
        text_lang=str(data.get("text_lang", "zh")).strip() or "zh",
        prompt_lang=str(data.get("prompt_lang", "zh")).strip() or "zh",
        emotion=str(data.get("emotion", "default")).strip() or "default",
        max_chars_per_voice=max(20, _as_int(data.get("max_chars_per_voice"), DEFAULT_MAX_CHARS)),
        opus_bitrate=str(data.get("opus_bitrate", "48k")).strip() or "48k",
        cache_dir=str(data.get("cache_dir", str(DEFAULT_CACHE_DIR))).strip()
        or str(DEFAULT_CACHE_DIR),
        profiles=profiles,
    )


def _default_profiles() -> dict[str, VoiceProfile]:
    """模板里预填她的 4 段克隆参考（读 src 里的单一出处，不复制文本）。"""
    try:
        from elysia.tts.backend import EMOTION_PROFILES

        return {
            name: VoiceProfile(
                ref_audio_path=profile.ref_audio_path, prompt_text=profile.prompt_text
            )
            for name, profile in EMOTION_PROFILES.items()
        }
    except Exception:  # 读不到 src 就留空，配置里仍可手填
        return {}


def discover_gpt_sovits_root(explicit: str | None = None) -> str:
    """找 GPT-SoVITS 目录（只用于 `--init` 生成模板，不参与运行时硬编码）。"""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("GPT_SOVITS_ROOT", "").strip()
    if env:
        candidates.append(env)
    for pattern_dir in (Path("D:/下载"), Path("D:/"), Path("C:/")):
        if pattern_dir.is_dir():
            with contextlib.suppress(OSError):
                candidates.extend(
                    str(p)
                    for p in sorted(pattern_dir.glob("GPT-SoVITS*"), reverse=True)
                    if p.is_dir()
                )
    for candidate in candidates:
        p = Path(candidate)
        if (p / "api.py").is_file() or (p / "api_v2.py").is_file():
            return str(p)
    return ""


def resolve_ffmpeg(cfg: TTSConfig) -> str:
    """ffmpeg 路径：配置 -> root/runtime -> 环境变量 -> PATH -> 明确报错。"""
    if cfg.ffmpeg:
        p = Path(cfg.ffmpeg)
        if p.is_file():
            return str(p)
        raise TTSError("ffmpeg_missing", f"配置里的 ffmpeg 不存在：{p}")
    if cfg.gpt_sovits_root:
        p = Path(cfg.gpt_sovits_root) / "runtime" / "ffmpeg.exe"
        if p.is_file():
            return str(p)
    env = os.environ.get("FFMPEG_PATH", "").strip()
    if env and Path(env).is_file():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise TTSError("ffmpeg_missing", "找不到 ffmpeg：请在 tts.json 填写 ffmpeg 或设 FFMPEG_PATH")


# ── HTTP 层（测试从这里注入假实现）─────────────────────────────────
def _open_tts(req: urllib.request.Request, timeout_s: float) -> bytes:
    """一次 TTS HTTP 请求；失败抛 TTSError（带稳定原因码）。每次现建 opener（R11 教训）。"""
    opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            return bytes(resp.read())
    except urllib.error.HTTPError as exc:
        raise TTSError("http_error", f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        pass
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            req, timeout=timeout_s
        ) as resp:
            return bytes(resp.read())
    except TimeoutError as exc:
        raise TTSError("timeout", f"{timeout_s:g}s 内没返回") from exc
    except Exception as exc:
        raise TTSError("http_error", f"{type(exc).__name__}") from exc


def _check_wav(data: bytes) -> bytes:
    if not data or len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise TTSError("bad_audio", f"返回的不是 WAV（{len(data)} 字节）")
    return data


def _synth_post_json(cfg: TTSConfig, text: str, emotion: str) -> bytes:
    payload = {"text": text, "emotion": emotion}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg.api_url.rstrip("/") + "/",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    return _open_tts(req, cfg.timeout_s)


def _synth_get_tts(cfg: TTSConfig, text: str, emotion: str) -> bytes:
    profile = cfg.profiles.get(emotion) or cfg.profiles.get("default") or VoiceProfile()
    if not profile.ref_audio_path or not profile.prompt_text:
        raise TTSError("unconfigured", f"profiles.{emotion} 缺 ref_audio_path/prompt_text")
    params = {
        "text": text,
        "text_lang": cfg.text_lang,
        "ref_audio_path": profile.ref_audio_path,
        "prompt_text": profile.prompt_text,
        "prompt_lang": cfg.prompt_lang,
        "media_type": "wav",
        "streaming_mode": 0,
    }
    req = urllib.request.Request(
        cfg.api_url.rstrip("/") + "/tts?" + urllib.parse.urlencode(params), method="GET"
    )
    return _open_tts(req, cfg.timeout_s)


def synthesize_wav(cfg: TTSConfig, text: str, *, emotion: str | None = None) -> bytes:
    """合成 wav 字节。失败抛 TTSError（调用方降级发文本）。"""
    if not cfg.enabled:
        raise TTSError("disabled", "配置里 enabled=false")
    text = text.strip()
    if not text:
        raise TTSError("empty_text", "空文本没有可合成的内容")
    mood = (emotion or cfg.emotion or "default").strip() or "default"
    if cfg.api_style == "get_tts":
        raw = _synth_get_tts(cfg, text, mood)
    elif cfg.api_style == "post_json":
        raw = _synth_post_json(cfg, text, mood)
    else:
        raise TTSError("config_invalid", f"未知 api_style：{cfg.api_style}")
    return _check_wav(raw)


# ── ffmpeg → ogg/opus ──────────────────────────────────────────────
def wav_to_ogg(wav: bytes, cfg: TTSConfig, *, runner: Any = subprocess.run) -> bytes:
    """wav -> Telegram 语音格式（ogg/opus, 48kHz, 单声道）。失败抛 TTSError。"""
    ffmpeg = resolve_ffmpeg(cfg)
    with tempfile.TemporaryDirectory(prefix="elysia_tts_") as td:
        wav_path = Path(td) / "in.wav"
        ogg_path = Path(td) / "out.ogg"
        wav_path.write_bytes(wav)
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(wav_path),
            "-c:a",
            "libopus",
            "-b:a",
            cfg.opus_bitrate,
            "-ar",
            "48000",
            "-ac",
            "1",
            "-f",
            "ogg",
            str(ogg_path),
        ]
        try:
            proc = runner(cmd, capture_output=True, timeout=cfg.ffmpeg_timeout_s)
        except FileNotFoundError as exc:
            raise TTSError("ffmpeg_missing", ffmpeg) from exc
        except subprocess.TimeoutExpired as exc:
            raise TTSError("ffmpeg_timeout", f"{cfg.ffmpeg_timeout_s:g}s") from exc
        if getattr(proc, "returncode", 1) != 0:
            err = getattr(proc, "stderr", b"") or b""
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="replace")
            raise TTSError("ffmpeg_failed", str(err)[-300:])
        out = ogg_path.read_bytes()
    if not out or not out.startswith(b"OggS"):
        raise TTSError("bad_ogg", f"ffmpeg 输出不是 OGG（{len(out)} 字节）")
    return out


# ── 缓存（幂等的物理保证）────────────────────────────────────────────
def cache_dir(cfg: TTSConfig) -> Path:
    p = Path(cfg.cache_dir)
    if not p.is_absolute():
        p = ROOT / p
    return p


def cache_key(cfg: TTSConfig, text: str, emotion: str) -> str:
    material = json.dumps(
        {
            "style": cfg.api_style,
            "url": cfg.api_url,
            "text": text,
            "emotion": emotion,
            "text_lang": cfg.text_lang,
            "prompt_lang": cfg.prompt_lang,
            "bitrate": cfg.opus_bitrate,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cached_ogg(path: Path) -> bytes | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if data.startswith(b"OggS") else None


def _put_cached(path: Path, ogg: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(ogg)
    os.replace(tmp, path)


def synthesize_ogg(
    cfg: TTSConfig,
    text: str,
    *,
    emotion: str | None = None,
    use_cache: bool = True,
    runner: Any = subprocess.run,
) -> bytes:
    """合成一条 ogg/opus（带缓存：同一段文本不会重复合成）。失败抛 TTSError。"""
    text = (text or "").strip()
    if not text:
        raise TTSError("empty_text", "空文本")
    mood = (emotion or cfg.emotion or "default").strip() or "default"
    target = cache_dir(cfg) / f"{cache_key(cfg, text, mood)}.ogg"
    if use_cache:
        hit = _cached_ogg(target)
        if hit is not None:
            return hit
    wav = synthesize_wav(cfg, text, emotion=mood)
    ogg = wav_to_ogg(wav, cfg, runner=runner)
    if use_cache:
        with contextlib.suppress(OSError):
            _put_cached(target, ogg)
    return ogg


# ── 长文本按句切（避免一个 2MB 语音）────────────────────────────────
def split_for_voice(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """把一段话按句子边界切成 <= max_chars 的若干段；单句过长时硬切。"""
    text = (text or "").strip()
    if not text:
        return []
    limit = max(20, int(max_chars))
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for piece in _SENTENCE_SPLIT.split(text):
        piece = (piece or "").strip()
        if not piece:
            continue
        while len(piece) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece[:limit])
            piece = piece[limit:]
        if not piece:
            continue
        if len(current) + len(piece) <= limit:
            current += piece
        else:
            if current:
                chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return [c for c in chunks if c]


# ── 环境核查 / CLI ──────────────────────────────────────────────────
def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_environment(path: Path | None = None) -> int:
    """`--check`：把"缺什么"说清楚（不合成、不写文件）。返回 0=全好，1=有缺失。"""
    ok = True
    p = config_path(path)
    print(f"[cfg] {p}")
    if not p.is_file():
        print("[fail] 配置不存在（可运行 --init 生成模板）")
        return 1
    try:
        cfg = load_config(p)
    except TTSError as exc:
        print(f"[fail] {exc}")
        return 1
    print(f"[ok] enabled={cfg.enabled} api_style={cfg.api_style} api_url={cfg.api_url}")
    if not cfg.enabled:
        print("[warn] enabled=false：桥会只发文本")
    try:
        ffmpeg = resolve_ffmpeg(cfg)
        print(f"[ok] ffmpeg={ffmpeg}")
        with contextlib.suppress(Exception):
            ver = subprocess.run(
                [ffmpeg, "-version"], capture_output=True, text=True, timeout=20, errors="replace"
            )
            first = (ver.stdout or ver.stderr or "").splitlines()
            if first:
                print(f"[ok] {first[0][:100]}")
    except TTSError as exc:
        print(f"[fail] {exc}")
        ok = False
    parsed = urllib.parse.urlsplit(cfg.api_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if _port_open(host, port):
        print(f"[ok] TTS 服务可达：{host}:{port}")
    else:
        print(f"[fail] TTS 服务不可达：{host}:{port}（先启动 一键启动API.bat）")
        ok = False
    if cfg.api_style == "get_tts":
        for name, profile in sorted(cfg.profiles.items()):
            ref = Path(profile.ref_audio_path)
            if not ref.is_absolute() and cfg.gpt_sovits_root:
                ref = Path(cfg.gpt_sovits_root) / ref
            state = "ok" if ref.is_file() else "fail"
            print(f"[{state}] profile {name}: {ref}")
            ok = ok and ref.is_file()
    print(f"[ok] cache_dir={cache_dir(cfg)}")
    return 0 if ok else 1


def write_template(path: Path | None = None, *, root: str | None = None) -> Path:
    """生成 `tts.json` 模板（UTF-8 **无 BOM**）。已存在则拒绝覆盖。"""
    p = config_path(path)
    if p.exists():
        raise ConfigError(f"{p} 已存在，拒绝覆盖（要重建请先自行移走）")
    svits_root = discover_gpt_sovits_root(root)
    ffmpeg = ""
    if svits_root:
        candidate = Path(svits_root) / "runtime" / "ffmpeg.exe"
        if candidate.is_file():
            ffmpeg = str(candidate)
    data: dict[str, Any] = {
        "enabled": True,
        "api_style": "post_json",
        "api_url": "http://127.0.0.1:9880",
        "gpt_sovits_root": svits_root,
        "ffmpeg": ffmpeg,
        "timeout_s": 120,
        "text_lang": "zh",
        "prompt_lang": "zh",
        "emotion": "default",
        "max_chars_per_voice": DEFAULT_MAX_CHARS,
        "opus_bitrate": "48k",
        "cache_dir": str(DEFAULT_CACHE_DIR).replace("\\", "/"),
        "profiles": {
            name: {
                "ref_audio_path": profile.ref_audio_path,
                "prompt_text": profile.prompt_text,
            }
            for name, profile in _default_profiles().items()
        },
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with contextlib.suppress(Exception):
        if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")
        if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(
        description="TTS 语音：GPT-SoVITS -> ogg/opus（供 Telegram 语音气泡）"
    )
    ap.add_argument("--config", default=None, help="配置路径（默认 data/run/tts.json）")
    ap.add_argument("--init", action="store_true", help="生成配置模板（已存在则拒绝覆盖）")
    ap.add_argument("--root", default=None, help="--init 用：GPT-SoVITS 目录")
    ap.add_argument("--check", action="store_true", help="核查配置/ffmpeg/TTS 服务（不合成）")
    ap.add_argument("--text", default=None, help="合成这句话（配合 --out）")
    ap.add_argument(
        "--out", default=None, help="输出 ogg 路径（默认 data/cache/tts_voice/_cli.ogg）"
    )
    ap.add_argument("--emotion", default=None, help="情绪档（default/devoted/gentle/playful）")
    ap.add_argument("--max-chars", type=int, default=None, help="每段语音最大字数（默认取配置）")
    a = ap.parse_args(argv)
    path = Path(a.config) if a.config else None
    if a.init:
        try:
            print(f"[ok] 已生成模板：{write_template(path, root=a.root)}")
        except TTSError as exc:
            print(f"[fail] {exc}")
            return 1
        return 0
    if a.check or not a.text:
        return check_environment(path)
    cfg = load_config(path)
    max_chars = a.max_chars or cfg.max_chars_per_voice
    chunks = split_for_voice(a.text, max_chars)
    out = Path(a.out) if a.out else cache_dir(cfg) / "_cli.ogg"
    try:
        ogg = b""
        if len(chunks) <= 1:
            ogg = synthesize_ogg(cfg, a.text, emotion=a.emotion)
        else:
            print(f"[info] 长文本按句切成 {len(chunks)} 段（每段 <= {max_chars} 字）")
            parts = [synthesize_ogg(cfg, c, emotion=a.emotion) for c in chunks]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(parts[0])
            for i, part in enumerate(parts[1:], start=2):
                print(f"[info] 第 {i} 段：{len(part)} 字节（--out 只保存第 1 段）")
            ogg = parts[0]
        if len(chunks) <= 1:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(ogg)
    except TTSError as exc:
        print(f"[fail] {exc.reason}: {exc.detail}")
        return 2
    print(f"[ok] 已生成：{out}（{len(ogg)} 字节，OggS={ogg[:4]!r}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
