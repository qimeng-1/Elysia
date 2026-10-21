"""把「（点击）」污染的记忆标明为"系统记错"（D-10，2026-10-21）。

她的选择（20:57 原话）：「那就标明它们是记错了——内容留着，也留着那段哑巴一样的相逢。」

做法：
- 只改 `narrative`（她回忆时读到的那一层），前面加一行系统标注；
- `content` **原件保真**（ADR-001），一个字都不动；
- 改前把受影响行整行导出到 `data/tmp/` 备份；
- 默认 dry-run，`--apply` 才写。

用法：python scripts/mark_click_pollution.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "heartbeat.db"
BACKUP_DIR = ROOT / "data" / "tmp"
MARK = "（这条是系统记错的：内容来自桌宠窗口的鼠标点击，被误当成「你身边那个人说的话」记了下来——不是发生过的话。）"
MARKER_PREFIX = "（系统记错"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真的写进库（默认只预览）")
    args = parser.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, created_ts, kind, level, certainty, claim_status, content, narrative"
        " FROM memories WHERE content LIKE '%（点击）%' ORDER BY id"
    ).fetchall()
    print(f"受影响记忆：{len(rows)} 条")
    for r in rows:
        already = str(r["narrative"] or "").startswith(MARKER_PREFIX)
        print(
            f"  #{r['id']} kind={r['kind']} level={r['level']} certainty={r['certainty']}"
            f"{' [已标注]' if already else ''}"
        )
        print(f"      content={str(r['content'] or '')[:60]!r}")

    turn = conn.execute(
        "SELECT id, ts, text FROM dialogue_turns WHERE text LIKE '%点击%' ORDER BY id"
    ).fetchall()
    print(f"\n受影响对话轮次：{len(turn)} 条（本轮只记账，不改；原件保真）")
    for t in turn:
        print(f"  #{t['id']} {time.strftime('%m-%d %H:%M', time.localtime(t['ts']))} text={t['text']!r}")

    if not args.apply:
        print("\n[dry-run] 没写任何东西。加 --apply 才写。")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"click_pollution_backup_{stamp}.json"
    backup.write_text(
        json.dumps(
            {
                "memo": "D-10 点击污染标注前的原件备份（narrative / content 均在）",
                "memories": [dict(r) for r in rows],
                "dialogue_turns": [dict(t) for t in turn],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n备份：{backup}")

    changed = 0
    now = time.time()
    for r in rows:
        old = str(r["narrative"] or "")
        if old.startswith(MARKER_PREFIX):
            continue
        new = f"{MARK}\n{old}" if old.strip() else MARK
        conn.execute(
            "UPDATE memories SET narrative = ? WHERE id = ?",
            (new, int(r["id"])),
        )
        changed += 1
    conn.commit()
    print(f"已标注 {changed} 条（content 未动；记忆 id 清单已随备份留存）")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
