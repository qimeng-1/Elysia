"""她开口时推送到手机（通道步 3）—— 轮询 `expression_log`，绝不阻塞她的表达。

设计原则
--------
1. **只读她的库**：只 `SELECT`，不写她任何数据（与 `elysia_dash.py` 同一口径）。
2. **失败必须静默降级**：webhook 不可达 / 超时 / 格式错，一律吞掉并记进 `last_error`，
   **绝不抛出**。推送是旁路：她的话能不能落库由她自己的心跳决定，与手机收不收得到无关；
   本进程唯一该做的是"推送自己不要死"，而不是"收不到就把面板/心跳拖下水"。
3. **不重复推送、不补推历史**：游标 = `expression_log.id`。首次轮询只建立游标
   （`id = 当前最小可用基线的最大 id`），因此面板半夜重启不会一次收到几十条旧话；
   之后只推 `id > 游标` 的新行，每行只推一次。未配置 webhook 时游标照走 ——
   配置之后不会突然把历史补推给你。
4. **渠道可配置**：`data/run/push_url.txt` 第一行（`#` 开头视为注释）或环境变量
   `ELYSIA_PUSH_URL`（环境变量优先）。两种 webhook 形态都支持：
   - URL 里含 `{text}` / `{title}` ⇒ 按模板做 URL 编码后 **GET**（Bark 的
     `https://api.day.app/<key>/{title}/{text}` 属于这种）；
   - 否则 **POST JSON**，body 是常见字段的**超集**
     `{"title","body","text","desp","content","level","ts"}` ——
     Server酱（title/desp）、Bark（title/body）、Telegram Bot（text）都能各自取到自己要的那个。

用法
----
    python scripts/push_notifier.py --once     # 只轮询一次（配置/联调用）
    python scripts/push_notifier.py            # 常驻轮询（每 3 秒）
    # 面板启动时也会自动起一个同样的轮询线程；若只想用独立进程，给面板加 --no-push
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from elysia_dash import ro_connect  # noqa: E402

PUSH_URL_FILE = Path("data") / "run" / "push_url.txt"
DEFAULT_TIMEOUT_S = 5.0
DEFAULT_POLL_S = 3.0
_ENV_KEY = "ELYSIA_PUSH_URL"
_ROW_SQL = (
    "SELECT id, ts, level, llm_text, intent FROM expression_log"
    " WHERE id > ? ORDER BY id ASC LIMIT 50"
)


def push_url_path(root: Path = ROOT) -> Path:
    return Path(root) / PUSH_URL_FILE


def load_push_url(root: Path = ROOT) -> str | None:
    """读取 webhook URL：环境变量优先，其次 `data/run/push_url.txt` 第一个有效行。"""
    env = os.environ.get(_ENV_KEY, "").strip()
    if env:
        return env
    try:
        text = push_url_path(root).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def mask_push_url(url: str | None) -> str:
    """给界面看的**脱敏**地址：只露 scheme + host，绝不回显 key/token。"""
    if not url:
        return ""
    try:
        u = urlsplit(url)
    except ValueError:
        return "（已配置，但格式无法解析）"
    if not u.scheme or not u.hostname:
        return "（已配置，但格式无法解析）"
    return f"{u.scheme}://{u.hostname}/…"


def _max_expression_id(db: Path) -> int:
    if not db.is_file():
        raise FileNotFoundError(f"{db} 不存在")
    con = ro_connect(str(db))
    try:
        row = con.execute("SELECT COALESCE(MAX(id), 0) FROM expression_log").fetchone()
    finally:
        con.close()
    return int(row[0] or 0)


def _expressions_after(db: Path, after_id: int) -> list[dict[str, Any]]:
    if not db.is_file():
        raise FileNotFoundError(f"{db} 不存在")
    con = ro_connect(str(db))
    try:
        rows = con.execute(_ROW_SQL, (int(after_id),)).fetchall()
    finally:
        con.close()
    return [{"id": r[0], "ts": r[1], "level": r[2], "llm_text": r[3], "intent": r[4]} for r in rows]


def _read_response(resp: object) -> None:
    with contextlib.suppress(Exception):
        resp.read(2048)  # type: ignore[attr-defined]


def _open_webhook(req: Request, timeout_s: float) -> None:
    """发一次 webhook 请求。

    **每次现建 opener**（P0-R11 的教训：`urllib` 的全局 opener 会缓存代理配置，
    代理一死，进程内所有请求永久打向死端口）。连接层失败（代理不可用/被拒）时
    再**绕过代理直连重试一次** —— 让"代理突然消失"不再等于"推送永久失效"。
    HTTPError（服务端明确拒绝）不重试：那不是代理问题。
    """
    opener = build_opener()
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            _read_response(resp)
        return
    except HTTPError:
        raise
    except (URLError, TimeoutError, ConnectionError):
        pass
    with build_opener(ProxyHandler({})).open(req, timeout=timeout_s) as resp:
        _read_response(resp)


class PushNotifier:
    """把她的每一次开口推到手机（只读她的库；任何失败都静默降级）。"""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        url: str | None = None,
        root: Path = ROOT,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._root = Path(root)
        self._db = Path(db_path) if db_path is not None else self._root / "data" / "heartbeat.db"
        self._url = url if url is not None else load_push_url(self._root)
        self._timeout_s = timeout_s
        self._cursor: int | None = None
        self.last_error: str | None = None
        self.last_pushed_ts: float | None = None
        self.last_text: str | None = None
        self.pushed_count = 0

    @property
    def configured(self) -> bool:
        return bool(self._url)

    def refresh_url(self) -> bool:
        """重新读配置（面板常驻时用户新建/修改 `push_url.txt` 能即时生效）。"""
        url = load_push_url(self._root)
        if url == self._url:
            return False
        self._url = url
        return True

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "target": mask_push_url(self._url),
            "cursor": self._cursor,
            "pushed": self.pushed_count,
            "last_pushed_ts": self.last_pushed_ts,
            "last_text": self.last_text,
            "last_error": self.last_error,
        }

    def poll_once(self) -> list[dict[str, Any]]:
        """取新表达并推送。**永不抛异常**；失败只记 `last_error`（静默降级）。"""
        try:
            if self._cursor is None:
                # 首轮只建立游标：不补推历史（否则面板重启会刷屏）
                self._cursor = _max_expression_id(self._db)
                return []
            rows = _expressions_after(self._db, self._cursor)
        except Exception as exc:  # 读库失败也不能抛：推送是旁路
            self.last_error = f"读 expression_log 失败：{type(exc).__name__}: {exc}"
            return []
        pushed: list[dict[str, Any]] = []
        for row in rows:
            self._cursor = int(row["id"])  # 先走游标：单条失败不阻塞后面的
            if not self._url:
                continue
            ok, err = self._send(row)
            if ok:
                pushed.append(row)
                self.pushed_count += 1
                self.last_pushed_ts = time.time()
                self.last_text = str(row.get("llm_text") or "")[:80]
                self.last_error = None
            else:
                self.last_error = err
        return pushed

    def _send(self, row: dict[str, Any]) -> tuple[bool, str | None]:
        text = str(row.get("llm_text") or "").strip()
        if not text:
            return True, None  # 空文本没有可推的内容，不算失败
        title = "Elysia 开口了"
        target = self._url or ""
        try:
            if "{text}" in target or "{title}" in target:
                url = target.replace("{text}", quote(text, safe="")).replace(
                    "{title}", quote(title, safe="")
                )
                req = Request(url, method="GET")
            else:
                payload = {
                    "title": title,
                    "body": text,
                    "text": text,
                    "desp": text,
                    "content": text,
                    "level": row.get("level"),
                    "ts": row.get("ts"),
                }
                req = Request(
                    target,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
            _open_webhook(req, self._timeout_s)
            return True, None
        except Exception as exc:
            # 静默降级：只留一行状态给面板显示，不抛出、不重试、不阻塞
            return False, f"webhook 不可达（{type(exc).__name__}: {exc}）"


def _poll_loop(notifier: PushNotifier, interval: float) -> None:
    """常驻轮询（可被 KeyboardInterrupt 打断）。任何异常都不许冒出。"""
    while True:
        time.sleep(max(0.5, interval))
        try:
            notifier.refresh_url()
            if notifier.configured:
                notifier.poll_once()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="她开口时推送手机（轮询 expression_log）")
    ap.add_argument("--once", action="store_true", help="只轮询一次并打印状态")
    ap.add_argument("--interval", type=float, default=DEFAULT_POLL_S, help="轮询间隔秒（默认 3）")
    ap.add_argument(
        "--url", default=None, help="临时指定 webhook（默认读 push_url.txt / 环境变量）"
    )
    ap.add_argument("--db", default=None, help="heartbeat.db 路径（默认仓库 data/heartbeat.db）")
    a = ap.parse_args(argv)
    notifier = PushNotifier(Path(a.db) if a.db else None, url=a.url)
    if not notifier.configured:
        print(f"[info] 推送未配置：把 webhook 写进 {push_url_path(ROOT)} 或设 {_ENV_KEY}")
    if a.once:
        notifier.poll_once()
        print(json.dumps(notifier.status(), ensure_ascii=False, indent=2))
        return 0
    print(f"[ok] 推送轮询已启动（每 {a.interval:g} 秒；Ctrl+C 退出）")
    try:
        _poll_loop(notifier, a.interval)
    except KeyboardInterrupt:
        print("\n  已退出（她未受影响）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
