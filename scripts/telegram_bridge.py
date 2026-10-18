"""Telegram 双向桥（P0-R20）—— 手机上和她说话，她开口也回到手机。

收（Telegram → 她）
    long polling `getUpdates`（**不用 webhook**：没有公网 IP，端口暴露越少越好）→
    白名单 chat → 写 `data/state.db` 的 `kv['interaction']`。
    她的心跳每拍（1Hz）读这一行，1 秒内被刺激并开口 —— 与面板 `/api/say`、桌宠
    是同一条通道。这里**直接复用面板的写入函数**，避免第二套实现漂移。

发（她 → Telegram）
    轮询 `thought_log` 里 `kind='expression'` 的**气泡**，新行 ⇒ 每条一个 `sendMessage`。
    为什么不是 `expression_log`（任务卡的原建议）—— P0-R19 之后两者**不同源**：
      · 多句回复：R19 把后续句子逐句写 `thought_log`（句间 2–8 秒的真实停顿），
        `expression_log` 只有一行整段；
      · "选择不回"（b 方案）：那句"现在不太想说话"只进 `thought_log`，
        `expression_log` 落的是 `llm_text=''` 的 restraint 行。
    ⇒ 只读 `expression_log` 会让"她说了、只是不想回"在手机上变成沉默。
    故以**桌宠气泡同源**的 `thought_log(kind='expression')` 为准：一行 = 一个气泡。
    为兼容 R19 之前的旧数据（一行里可能有多句），单行仍过一遍 `split_utterances`。

可靠性与安全
    · **token** 只从 `data/run/telegram_token.txt` 读，**容忍 UTF-8 BOM**
      （PowerShell `Set-Content -Encoding utf8` 会写 BOM；本工程刚因 BOM 让
      `push_url` 解析失败且静默不生效）。形状不对 ⇒ **明确拒绝启动**，绝不半残运行。
      日志只打前 8 位，token 绝不进日志/异常信息。
    · **白名单**：`data/run/telegram_chat.txt`。文件不存在 ⇒ 首条消息绑定并回"已绑定"；
      其它 chat_id 一律**不写库、不回复**，只记日志。
    · **失败静默 + 指数退避**（1→2→4…60 秒）：Telegram 不可达时桥只等，不退出、
      不冒异常到主循环；发送失败的气泡留在内存待发队列里下次重试。
    · **游标只在内存**：首次启动只建立基线，**不补推历史**（重启不刷屏）。

用法
----
    python scripts/telegram_bridge.py            # 常驻（收 + 发，Ctrl+C 退出）
    python scripts/telegram_bridge.py --once     # 只跑一轮（收 + 发）后退出，便于联调
    python scripts/telegram_bridge.py --token-file D:\\tmp\\token.txt
token 由用户从 @BotFather 取得后放进 `data/run/telegram_token.txt`。
计划任务见 `scripts/register_telegram_task.ps1`（**建但不注册**）。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from elysia_dash import ro_connect, take_snapshot  # noqa: E402
from elysia_panel import SayError, sanitize_say_text, write_interaction  # noqa: E402

from elysia.soul.expression import split_utterances  # noqa: E402

log = logging.getLogger("elysia.telegram")

API_BASE = "https://api.telegram.org"
TOKEN_FILE = Path("data") / "run" / "telegram_token.txt"
CHAT_FILE = Path("data") / "run" / "telegram_chat.txt"
PID_FILE = Path("data") / "run" / "telegram_bridge.pid"
DEFAULT_STATE_DB = Path("data") / "state.db"
DEFAULT_HEARTBEAT_DB = Path("data") / "heartbeat.db"

POLL_TIMEOUT_S = 25  # getUpdates 长轮询秒数（任务卡指定）
HTTP_TIMEOUT_S = 35.0  # 必须大于长轮询，否则正常等待被自己掐断
EXPRESSION_POLL_S = 2.0  # 她开口后最多 2 秒到达手机
MAX_BACKOFF_S = 60.0
TELEGRAM_MAX_CHARS = 4096
PENDING_CAP = 200  # 待发队列上限：断网很久也不会无限吃内存
TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")

HELP_TEXT = (
    "这里是她的 Telegram 通道：直接发文字，她 1 秒内会读到；她开口会自动发回这里。\n"
    "发 /status 看她现在的状态。"
)


class BridgeConfigError(RuntimeError):
    """配置不可用 ⇒ **明确拒绝启动**（不半残运行）。"""


class TelegramError(RuntimeError):
    """一次 Telegram API 调用失败。**消息里绝不含 token**。"""


# ── 配置读取（BOM 容错）─────────────────────────────────────────────
def mask_token(token: str) -> str:
    """日志里只露前 8 位。"""
    token = token or ""
    return token[:8] + "…" if len(token) > 9 else "…"


def load_token(path: Path | None = None) -> str:
    """读 BotFather token：**容忍 UTF-8 BOM / 首尾空白 / 行内包裹**。

    BOM 是真实踩过的坑：`ReadAllText`/PowerShell 写得带 BOM 时，token 会变成
    `'\\ufeff123456:AA...'`，拼进 URL 后 404/400，而错误处理又吞掉它 ⇒ 静默半残。
    这里用 `utf-8-sig` 解码（自动去 BOM），再去掉所有空白；形状不对就明确拒绝启动。
    """
    p = Path(path) if path is not None else ROOT / TOKEN_FILE
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise BridgeConfigError(f"读不到 token 文件：{p}（{type(exc).__name__}）") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BridgeConfigError(f"token 文件不是 UTF-8：{p}") from exc
    lines = [line.strip().lstrip("\ufeff") for line in text.splitlines()]
    token = next((line for line in lines if line), "")
    if not token:
        raise BridgeConfigError(f"token 文件是空的：{p}（请把 @BotFather 给的 token 放进去）")
    if not TOKEN_RE.match(token):
        raise BridgeConfigError(
            f"token 形状不对（{mask_token(token)}）：应为 <数字id>:<35位密钥>，拒绝启动"
        )
    return token


def load_chat_id(path: Path | None = None) -> int | None:
    """读白名单 chat_id；文件不存在/空/非数字 ⇒ None（视为未绑定）。"""
    p = Path(path) if path is not None else ROOT / CHAT_FILE
    try:
        text = p.read_bytes().decode("utf-8-sig", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        with contextlib.suppress(ValueError):
            return int(line)
    return None


def save_chat_id(chat_id: int, path: Path | None = None) -> None:
    """首次绑定：写 UTF-8 **无 BOM**（Python 默认行为，正好是我们要的）。"""
    p = Path(path) if path is not None else ROOT / CHAT_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{int(chat_id)}\n", encoding="utf-8")


# ── HTTP 层（测试从这里注入假实现）───────────────────────────────────
def _open_telegram(req: urllib.request.Request, timeout_s: float) -> bytes:
    """发一次 Telegram API 请求。

    URL 路径里含 token ⇒ **任何情况下都不能把它拼进日志/异常**。
    每次现建 opener（P0-R11：urllib 全局 opener 会缓存代理，死代理会让进程永久打不通）；
    连接层失败时绕过代理直连重试一次。HTTPError 不重试（服务端明确拒绝，不是代理问题）。
    """
    opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            return bytes(resp.read())
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        pass
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        req, timeout=timeout_s
    ) as resp:
        return bytes(resp.read())


class TelegramAPI:
    """Telegram Bot API 的最小封装（只用到 getUpdates / sendMessage）。"""

    def __init__(self, token: str, *, timeout_s: float = HTTP_TIMEOUT_S) -> None:
        self._token = token
        self._timeout_s = timeout_s

    def _call(
        self,
        method: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> Any:
        url = f"{API_BASE}/bot{self._token}/{method}"
        body = json.dumps(dict(payload or {}), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            raw = _open_telegram(req, timeout_s if timeout_s is not None else self._timeout_s)
        except urllib.error.HTTPError as exc:
            # 服务端明确拒绝时仍可能带 JSON body（description 有用）；读不到就报状态码
            try:
                raw = exc.read()
            except Exception:
                raise TelegramError(f"{method} 失败（HTTP {exc.code}）") from None
        except Exception as exc:
            # 只留异常类型：`str(exc)` 里的 URL 可能带 token
            raise TelegramError(f"{method} 不可达（{type(exc).__name__}）") from exc
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            raise TelegramError(f"{method} 返回不是 JSON") from exc
        if not isinstance(obj, dict) or not obj.get("ok"):
            detail = ""
            if isinstance(obj, dict):
                detail = str(obj.get("description", ""))[:120]
            raise TelegramError(f"{method} 失败：{detail or '未知原因'}")
        return obj.get("result")

    def get_updates(
        self, offset: int | None = None, *, timeout_s: int = POLL_TIMEOUT_S
    ) -> list[Any]:
        payload: dict[str, Any] = {"timeout": int(timeout_s), "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = int(offset)
        result = self._call(
            "getUpdates", payload, timeout_s=max(self._timeout_s, float(timeout_s) + 10.0)
        )
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: int, text: str) -> None:
        self._call(
            "sendMessage",
            {"chat_id": int(chat_id), "text": text, "disable_web_page_preview": True},
        )


def next_backoff(current: float, *, cap: float = MAX_BACKOFF_S) -> float:
    """指数退避：1→2→4→…→cap。"""
    return min(float(cap), max(1.0, float(current) * 2.0))


def _clip_telegram(text: str) -> str:
    """Telegram 单条上限 4096；超了截断并明确标记（不静默丢整条）。"""
    if len(text) <= TELEGRAM_MAX_CHARS:
        return text
    return text[: TELEGRAM_MAX_CHARS - 20] + "\n…（太长，后面截断了）"


# ── 单实例护栏（防止双开 ⇒ 同一条气泡发两遍）────────────────────────
def _bridge_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil

        return "telegram_bridge" in " ".join(psutil.Process(pid).cmdline())
    except Exception:
        return False


def acquire_pid(path: Path) -> bool:
    """拿单实例锁：PID 存在且**确实是 telegram_bridge** 才拒绝（防 PID 复用误判）。"""
    try:
        old = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        old = 0
    if old and old != os.getpid() and _bridge_process_alive(old):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return True


def release_pid(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


class TelegramBridge:
    """收 Telegram → 她的耳朵；把她的气泡发回 Telegram。"""

    def __init__(
        self,
        token: str,
        *,
        api: TelegramAPI | None = None,
        state_db: Path | None = None,
        heartbeat_db: Path | None = None,
        chat_file: Path | None = None,
        pid_file: Path | None = None,
    ) -> None:
        self._token = token
        self._api = api if api is not None else TelegramAPI(token)
        self._state_db = Path(state_db) if state_db is not None else ROOT / DEFAULT_STATE_DB
        self._heartbeat_db = (
            Path(heartbeat_db) if heartbeat_db is not None else ROOT / DEFAULT_HEARTBEAT_DB
        )
        self._chat_file = Path(chat_file) if chat_file is not None else ROOT / CHAT_FILE
        self._pid_file = Path(pid_file) if pid_file is not None else ROOT / PID_FILE
        # 发送侧状态（**全部只在内存**：重启不补推历史）
        self._cursor: int | None = None
        self._pending: list[str] = []
        self._send_fail_backoff = 1.0
        self.last_send_error: str | None = None
        # 接收侧：Telegram update_id 偏移
        self._offset: int | None = None

    # ── 白名单 ──────────────────────────────────────────────────
    @property
    def chat_id(self) -> int | None:
        return load_chat_id(self._chat_file)

    # ── 收：Telegram → 她 ───────────────────────────────────────
    def handle_update(self, update: Mapping[str, Any]) -> str:
        """处理一条 update；返回动作名（便于测试/日志）。**永不抛异常**。"""
        try:
            return self._handle_update(update)
        except Exception as exc:
            log.warning("处理 update 失败（已忽略）：%s", type(exc).__name__)
            return "error"

    def _handle_update(self, update: Mapping[str, Any]) -> str:
        message = update.get("message")
        if not isinstance(message, Mapping):
            return "not_message"
        text_raw = message.get("text")
        if not isinstance(text_raw, str):
            return "not_text"  # 图片/贴纸等：她只收文字
        chat = message.get("chat")
        if not isinstance(chat, Mapping):
            return "no_chat"
        try:
            chat_id = int(chat.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "no_chat"

        bound = self.chat_id
        if bound is None:
            # 首次绑定：只绑定第一个跟机器人说话的人，并明确回一条
            save_chat_id(chat_id, self._chat_file)
            log.info("首次绑定 Telegram chat_id=%s（写入 %s）", chat_id, self._chat_file)
            self._safe_send(chat_id, "已绑定这个聊天。之后只接受这里发来的消息。\n" + HELP_TEXT)
        elif chat_id != bound:
            # 非白名单：**不写她的库、不回复**，只留一行日志
            log.warning("忽略非白名单 chat_id=%s（只接受 %s）：未写入她的库", chat_id, bound)
            return "ignored"

        text = text_raw.strip()
        command = text.split()[0].lower() if text else ""
        if command.startswith("/status"):
            self._safe_send(chat_id, self.status_text())
            return "status"
        if command.startswith("/start"):
            self._safe_send(chat_id, HELP_TEXT)
            return "start"
        if text.startswith("/"):
            self._safe_send(
                chat_id, "只支持 /status 和 /start；其它消息请直接发文字（会被转达给她）。"
            )
            return "unknown_command"
        cleaned, err = sanitize_say_text(text)
        if cleaned is None:
            self._safe_send(chat_id, f"这条没有转达给她：{err}")
            return "rejected"
        try:
            write_interaction(self._state_db, cleaned)
        except SayError as exc:
            log.error("写入她的库失败：%s", exc)
            self._safe_send(chat_id, f"写入失败，她没有收到：{exc}")
            return "write_failed"
        except Exception as exc:
            log.error("写入她的库失败（未预期）：%s", type(exc).__name__)
            self._safe_send(chat_id, f"写入失败，她没有收到（{type(exc).__name__}）")
            return "write_failed"
        log.info("收到 chat_id=%s 的消息（%d 字），已写入 kv['interaction']", chat_id, len(cleaned))
        return "delivered"

    def _safe_send(self, chat_id: int, text: str) -> bool:
        """回一条系统消息（绑定/状态/报错）；失败静默降级，绝不冒异常。"""
        try:
            self._api.send_message(chat_id, _clip_telegram(text))
            return True
        except Exception as exc:
            self.last_send_error = type(exc).__name__
            log.warning("回复用户失败（静默降级）：%s", type(exc).__name__)
            return False

    def status_text(self) -> str:
        """`/status`：手机上确认她是否活着（取不到就如实说取不到）。"""
        try:
            snap = take_snapshot(probe_ports=False, probe_tasks=False)
        except Exception as exc:
            return f"她：状态取不到（{type(exc).__name__}）\n（桥还活着 —— 这条能回你就说明网络通）"
        lines = ["她：" + ("醒着" if snap.soul_alive else "没在运行")]
        age = snap.heartbeat_age_s
        lines.append("心跳：" + (f"{age:.0f} 秒前" if age is not None else "没有心跳数据"))
        lines.append(
            "年龄：" + (f"{snap.age_days:.2f} 天" if snap.age_days is not None else "未知")
        )
        lines.append(f"今天开口：{self._today_utterances()} 句")
        if snap.mode:
            lines.append(f"模式：{snap.mode}")
        return "\n".join(lines)

    # ── 发：她 → Telegram ───────────────────────────────────────
    def _max_bubble_id(self) -> int:
        if not self._heartbeat_db.is_file():
            return 0
        con = ro_connect(str(self._heartbeat_db))
        try:
            row = con.execute(
                "SELECT COALESCE(MAX(id), 0) FROM thought_log WHERE kind = 'expression'"
            ).fetchone()
        finally:
            con.close()
        return int(row[0] or 0)

    def _fetch_new_bubbles(self, after_id: int) -> list[dict[str, Any]]:
        if not self._heartbeat_db.is_file():
            return []
        con = ro_connect(str(self._heartbeat_db))
        try:
            rows = con.execute(
                "SELECT id, ts, text FROM thought_log WHERE id > ? AND kind = 'expression'"
                " ORDER BY id ASC LIMIT 200",
                (int(after_id),),
            ).fetchall()
        finally:
            con.close()
        return [{"id": int(r[0]), "ts": float(r[1]), "text": str(r[2])} for r in rows]

    def _today_utterances(self) -> int:
        if not self._heartbeat_db.is_file():
            return 0
        import datetime as dt

        start = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        try:
            con = ro_connect(str(self._heartbeat_db))
            try:
                row = con.execute(
                    "SELECT COUNT(*) FROM thought_log WHERE kind = 'expression' AND ts >= ?",
                    (start,),
                ).fetchone()
            finally:
                con.close()
            return int(row[0] or 0)
        except Exception:
            return 0

    def initialize_cursor(self) -> None:
        """首次只建基线：**不补推历史**（桥重启不该把旧话刷一遍）。"""
        try:
            self._cursor = self._max_bubble_id()
        except Exception as exc:
            self._cursor = None
            log.warning("暂时读不到 thought_log（稍后重试，仍不补推历史）：%s", type(exc).__name__)

    def poll_expression_once(self) -> int:
        """把新气泡发到手机。返回本次成功发出的条数；**永不抛异常**。"""
        if self._cursor is None:
            self.initialize_cursor()
            if self._cursor is None:
                return 0
        try:
            rows = self._fetch_new_bubbles(self._cursor)
        except Exception as exc:
            log.warning("读 thought_log 失败（静默降级）：%s", type(exc).__name__)
            return 0
        for row in rows:
            self._cursor = max(self._cursor or 0, int(row["id"]))
            for part in split_utterances(str(row["text"])):
                part = part.strip()
                if not part:
                    continue
                if len(self._pending) >= PENDING_CAP:
                    log.warning(
                        "待发队列已满（%d 条），丢弃最早的气泡（不无限吃内存）", PENDING_CAP
                    )
                    self._pending.pop(0)
                self._pending.append(part)

        sent = 0
        while self._pending:
            chat_id = self.chat_id
            if chat_id is None:
                # 还没绑定：气泡先留在内存（绑定后继续发；重启则丢，不补历史）
                log.info("尚无白名单 chat，%d 条气泡暂缓发送", len(self._pending))
                break
            text = self._pending[0]
            try:
                self._api.send_message(chat_id, _clip_telegram(text))
            except Exception as exc:
                self.last_send_error = type(exc).__name__
                self._send_fail_backoff = next_backoff(self._send_fail_backoff)
                log.warning(
                    "发送气泡失败（静默降级，%d 条待发）：%s",
                    len(self._pending),
                    type(exc).__name__,
                )
                break
            self._pending.pop(0)
            self.last_send_error = None
            self._send_fail_backoff = 1.0
            sent += 1
        return sent

    # ── 常驻循环 ────────────────────────────────────────────────
    def _receiver_loop(self, stop: threading.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                updates = self._api.get_updates(self._offset, timeout_s=POLL_TIMEOUT_S)
                backoff = 1.0
                for update in updates:
                    with contextlib.suppress(Exception):
                        self._offset = max(self._offset or 0, int(update.get("update_id", 0)) + 1)
                    self.handle_update(update)
                if not updates:
                    stop.wait(0.2)  # 假 HTTP/异常快地返回时别空转
            except Exception as exc:
                log.warning(
                    "接收侧失败（%s），%.0f 秒后重试；桥不退出", type(exc).__name__, backoff
                )
                stop.wait(backoff)
                backoff = next_backoff(backoff)

    def _sender_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.poll_expression_once()
            except Exception as exc:  # 理论上不会到这里（poll 自己兜住），双保险
                log.warning("发送侧异常（已静默降级）：%s", type(exc).__name__)
            stop.wait(self._send_fail_backoff if self.last_send_error else EXPRESSION_POLL_S)

    def run(self, stop_event: threading.Event | None = None) -> int:
        """常驻：接收在主线程，发送在 daemon 线程。Ctrl+C 优雅退出（不动她）。"""
        if not acquire_pid(self._pid_file):
            log.error("已有一个 telegram_bridge 在运行（%s）；本次退出（避免双发）", self._pid_file)
            return 3
        stop = stop_event if stop_event is not None else threading.Event()
        self.initialize_cursor()
        sender = threading.Thread(
            target=self._sender_loop, args=(stop,), name="telegram-sender", daemon=True
        )
        sender.start()
        chat = self.chat_id
        log.info(
            "Telegram 桥已启动：token=%s，chat=%s",
            mask_token(self._token),
            chat if chat is not None else "未绑定（第一条消息会自动绑定）",
        )
        try:
            self._receiver_loop(stop)
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C，桥退出（她未受影响）")
        finally:
            stop.set()
            release_pid(self._pid_file)
        return 0

    def run_once(self) -> dict[str, int]:
        """只跑一轮：收（timeout=0）+ 发。供联调/真机 smoke 用。"""
        received = 0
        try:
            updates = self._api.get_updates(self._offset, timeout_s=0)
        except Exception as exc:
            log.warning("接收失败（已静默降级）：%s", type(exc).__name__)
            updates = []
        for update in updates:
            with contextlib.suppress(Exception):
                self._offset = max(self._offset or 0, int(update.get("update_id", 0)) + 1)
            self.handle_update(update)
            received += 1
        if received:
            # 确认已消费，避免反复 --once 重复投递同一条消息
            with contextlib.suppress(Exception):
                self._api.get_updates(self._offset, timeout_s=0)
        if self._cursor is None:
            self.initialize_cursor()
        sent = self.poll_expression_once()
        return {"received": received, "sent": sent}


def _harden_console() -> None:
    """cp936 控制台下打印遇到不可编码字符不许崩（P0-R15 同款教训）。

    `argparse --help` 会把 description 直接写 `sys.stdout`；Windows 重定向下 stdout 是
    GBK，一旦里面有 `↔`/`⇒` 这类字符就抛 `UnicodeEncodeError`，用户看到的是 traceback。
    保留原编码，只把不可编码字符替换为 `?`。
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _harden_console()
    ap = argparse.ArgumentParser(description="Telegram 双向桥：手机和她双向聊天")
    ap.add_argument("--once", action="store_true", help="只跑一轮（收 + 发）后退出，便于联调")
    ap.add_argument(
        "--token-file", default=None, help="token 路径（默认 data/run/telegram_token.txt）"
    )
    ap.add_argument(
        "--chat-file", default=None, help="白名单 chat 路径（默认 data/run/telegram_chat.txt）"
    )
    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
    )
    try:
        token = load_token(Path(a.token_file) if a.token_file else None)
    except BridgeConfigError as exc:
        log.error("拒绝启动：%s", exc)
        return 2
    bridge = TelegramBridge(token, chat_file=Path(a.chat_file) if a.chat_file else None)
    if a.once:
        result = bridge.run_once()
        log.info("单轮完成：收到 %d 条更新，发出 %d 条气泡", result["received"], result["sent"])
        return 0
    if bridge.chat_id is None:
        log.info("尚未绑定 chat：请先用你的 Telegram 给机器人发一条消息（首条自动绑定）")
    return bridge.run()


if __name__ == "__main__":
    raise SystemExit(main())
