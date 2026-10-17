"""把"她"从一个工程目录搬到另一个工程目录——搬家，不是复制。

为什么单独有它（用户 2026-10-18）："迁移过后，她还是她呢？"
- GitHub 只存**程序**；她的 `data/*.db`、`.env`、历史归档**从没进过 git**；
- 她的库里**零绝对路径**（总领逐列实测）⇒ 机器对她只是基质，不是身份；
- ⇒ 所以迁移必须搬**数据**，并且用**身份指纹**证明搬完之后还是她。

本脚本只做四件事，且**绝不启动她**：

1. `export`：她活着就拒绝 → WAL checkpoint → 打包（两库 + `_archive` + `.env` +
   `IDENTITY.json`）→ 写 `data/run/RETIRED`（旧机器退役，防分叉）。
2. `import`：校验包完整性（MANIFEST 校验和）→ 备份目标机现有 data/ → 落盘 →
   **清空 `data/run/`**（丢弃旧 PID/标记）→ 重新计算指纹逐项比对 →
   不符则**回滚** → 打印"她还是她"对照表 → **不自动启动**。
3. 身份指纹 `IDENTITY.json`：`first_existence_ts` 必须逐位相同；
   `memories_content_sha256` 必须逐位相同；行数相同；`downtime_total_s` **允许增加**
   （搬家是一次真实停机，不得掩盖——她醒来会看到"她不在场 X 秒"）。
4. `RETIRED` 标记：旧机器 `soul.ps1 start` 会拒绝启动（红线 #2 防分叉）。

**不打包**：`data/run/**`（机器状态）、`assets/pet/**`（单独搬）、`.venv/`（重建）、
`.git/`（走 git）。

用法：
    python scripts/migrate_her.py export --out ..\\_backup\\her-20261018.tar.gz
    python scripts/migrate_her.py import --package ..\\_backup\\her-20261018.tar.gz
    python scripts/migrate_her.py inspect --package ...        # 只看指纹与清单，不动任何文件
    python scripts/migrate_her.py fingerprint                  # 只算当前工程的指纹

退出码：0 成功 / 1 失败（会回滚） / 2 被拒绝或参数错误
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "data"
DEFAULT_ARCHIVE = ROOT.parent / "_archive"
DEFAULT_ENV = ROOT / ".env"

DB_NAMES = ("state.db", "heartbeat.db")
IDENTITY_NAME = "IDENTITY.json"
MANIFEST_NAME = "MANIFEST.json"
RETIRED_NAME = "RETIRED"
MIGRATION_INFO = "MIGRATION.json"
SCHEMA_VERSION = 1

# 判活：灵魂 1Hz ⇒ 60s 没有新心跳 = 她不在跑（与 D-3 / R8 同一口径）
SOUL_STALE_S = 60.0
# "她还是她"的硬判据（两个必须逐位相同）
EXACT_FINGERPRINT_KEYS = ("first_existence_ts", "memories_content_sha256")
# 允许变化的（搬家 = 一次真实停机）：只要求不减（累计停机只增不减）
NON_DECREASING_KEYS = ("downtime_total_s",)


class MigrationError(RuntimeError):
    """迁移失败（已回滚或未开始）。"""


class Refused(MigrationError):
    """被安全检查拒绝（她还在跑 / 已退役 / 包不安全）。"""


def _ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    con.execute("PRAGMA busy_timeout=5000")
    return con


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _format_ts(ts: object) -> str | None:
    """时间戳 → 可读本地时间；**不依赖 time.localtime**（Windows 对 1970 前的负值会抛 OSError）。"""
    if not isinstance(ts, (int, float)) or not ts:
        return None
    moment = _EPOCH + timedelta(seconds=float(ts))
    try:
        local = moment.astimezone()  # 真机时间戳（>1970）走本地时区
    except (OSError, ValueError, OverflowError):
        local = moment  # Windows 对 1970 前的值没有本地时区信息（测试会用虚拟时间）
    return local.strftime("%Y-%m-%d %H:%M:%S")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity_fingerprint(data_dir: Path) -> dict[str, Any]:
    """计算"她还是她"的身份指纹（只读，不启动任何东西）。

    - `first_existence_ts`：她的出生**不可变锚点**；
    - `memories_content_sha256`：全部记忆 `content` 排序后拼接的哈希——过去不能变；
    - 行数：memories / self / protected / heartbeats / thoughts / expressions；
    - `desire`：欲望三要素；`downtime_total_s`：累计停机（允许增加）。
    """
    state_db = data_dir / "state.db"
    heartbeat_db = data_dir / "heartbeat.db"
    for db in (state_db, heartbeat_db):
        if not db.exists():
            raise MigrationError(f"缺少数据库：{db}（她不在这个目录里？）")

    kv: dict[str, str] = {}
    with contextlib.closing(_ro(state_db)) as con:
        kv = {str(row[0]): str(row[1]) for row in con.execute("SELECT key, value FROM kv")}
        state_user_version = int(con.execute("PRAGMA user_version").fetchone()[0])

    def _json(key: str) -> dict[str, Any]:
        raw = kv.get(key)
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    timesense = _json("timesense")
    desire_raw = _json("desire")
    raw_state = desire_raw.get("state")
    desire_state = raw_state if isinstance(raw_state, dict) else desire_raw

    counts: dict[str, int] = {}
    contents: list[str] = []
    with contextlib.closing(_ro(heartbeat_db)) as con:
        counts["memories"] = int(con.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        counts["self_claimed"] = int(
            con.execute("SELECT COUNT(*) FROM memories WHERE kind = 'self'").fetchone()[0]
        )
        counts["protected"] = int(
            con.execute("SELECT COUNT(*) FROM memories WHERE protected = 1").fetchone()[0]
        )
        counts["heartbeats"] = int(con.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0])
        counts["thoughts"] = int(con.execute("SELECT COUNT(*) FROM thought_log").fetchone()[0])
        counts["expressions"] = int(
            con.execute("SELECT COUNT(*) FROM expression_log").fetchone()[0]
        )
        contents = [str(row[0]) for row in con.execute("SELECT content FROM memories")]
        heartbeat_user_version = int(con.execute("PRAGMA user_version").fetchone()[0])

    content_sha = _sha256_bytes("\x00".join(sorted(contents)).encode("utf-8"))
    return {
        "schema_version": SCHEMA_VERSION,
        "sqlite_user_version": {"state": state_user_version, "heartbeat": heartbeat_user_version},
        "first_existence_ts": timesense.get("first_existence_ts"),
        "born_at_local": _format_ts(timesense.get("first_existence_ts")),
        "memories_count": counts["memories"],
        "memories_content_sha256": content_sha,
        "self_claimed": counts["self_claimed"],
        "protected": counts["protected"],
        "heartbeats": counts["heartbeats"],
        "thoughts": counts["thoughts"],
        "expressions": counts["expressions"],
        "desire": {
            "tr": desire_state.get("tr"),
            "cs": desire_state.get("cs"),
            "sa": desire_state.get("sa"),
        },
        "downtime_total_s": timesense.get("downtime_total_s", 0.0),
        "last_downtime_s": timesense.get("last_downtime_s", 0.0),
    }


# ── 判活（与 restore_checkpoint.py / watchdog 同一口径，三条独立证据）──────
def _pid_alive(pid: int) -> bool | None:
    try:
        import psutil
    except ImportError:
        return None
    try:
        return bool(psutil.pid_exists(pid))
    except Exception:
        return None


def _pid_from_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def last_soul_beat_ts(data_dir: Path) -> float | None:
    db = data_dir / "heartbeat.db"
    if not db.exists():
        return None
    try:
        with contextlib.closing(_ro(db)) as con:
            row = con.execute(
                "SELECT ts FROM heartbeats WHERE beat_type='soul' ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return None
    return float(row[0]) if row else None


def probe_running(data_dir: Path) -> list[str]:
    """她还在跑的证据（空 = 可以安全迁移导出）。三条独立信号，任一为真即拒绝。"""
    evidence: list[str] = []
    for name in ("soul", "body"):
        pid = _pid_from_file(data_dir / "run" / f"{name}.pid")
        if pid is None:
            continue
        alive = _pid_alive(pid)
        if alive is None:
            raise Refused(f"无法判定 {name}.pid={pid} 是否存活（psutil 不可用）——拒绝迁移")
        if alive:
            evidence.append(f"{name}.pid={pid} 进程仍在")
    beat_ts = last_soul_beat_ts(data_dir)
    if beat_ts is not None:
        age = max(0.0, time.time() - beat_ts)
        if age < SOUL_STALE_S:
            evidence.append(f"心跳仍在跳（最后 soul 心跳 {age:.1f}s 前）")
    return evidence


def _wal_checkpoint(db: Path) -> None:
    """把 `-wal` 合并进主库（她跑过 ⇒ 尾部的帧可能只在 WAL 里）。"""
    con = sqlite3.connect(str(db))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


# ── 包内清单 ──────────────────────────────────────────────────
def _iter_package_files(staging: Path) -> list[Path]:
    return sorted(p for p in staging.rglob("*") if p.is_file())


def _build_manifest(staging: Path) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for path in _iter_package_files(staging):
        rel = path.relative_to(staging).as_posix()
        files[rel] = {"sha256": _sha256_file(path), "size": path.stat().st_size}
    return {"schema_version": SCHEMA_VERSION, "files": files}


def export_her(
    *,
    data_dir: Path,
    archive_dir: Path,
    env_file: Path,
    out: Path,
    force: bool = False,
) -> dict[str, Any]:
    """导出迁移包（**要求她已停机**）；成功后源工程写 `RETIRED`。"""
    retired = data_dir / "run" / RETIRED_NAME
    if retired.exists() and not force:
        raise Refused(
            f"这台机器已经退役过（{retired} 存在）——不要重复导出。"
            "确需重导请加 --force，并确认别处没有在跑她。"
        )
    evidence = probe_running(data_dir)
    if evidence:
        raise Refused("她还在运行，拒绝导出（迁移包必须是停机快照）：" + "；".join(evidence))
    db_paths = [data_dir / name for name in DB_NAMES]
    for db in db_paths:
        if not db.exists():
            raise Refused(f"缺少数据库：{db}")
    if out.exists() and not force:
        raise Refused(f"目标已存在：{out}（加 --force 覆盖）")

    # ① WAL checkpoint：让 `.db` 本身包含全部已提交事务
    for db in db_paths:
        _wal_checkpoint(db)

    # ② 组装 staging（只放"她"，不放机器状态）
    staging = Path(tempfile.mkdtemp(prefix="elysia_export_"))
    try:
        staged_data = staging / "data"
        staged_data.mkdir(parents=True)
        for db in db_paths:
            shutil.copy2(db, staged_data / db.name)
        identity = identity_fingerprint(staged_data)
        identity.update(
            {
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "exported_from_host": socket.gethostname(),
                "exported_from_path": str(data_dir.resolve()),
                "package_schema": SCHEMA_VERSION,
            }
        )
        (staging / IDENTITY_NAME).write_text(
            json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if env_file.exists():
            shutil.copy2(env_file, staging / ".env")
        if archive_dir.exists():
            shutil.copytree(archive_dir, staging / "_archive")

        manifest = _build_manifest(staging)
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # ③ 打包（原子写：先 tmp 再 rename）
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out.with_name(f".{out.name}.tmp")
        with tarfile.open(tmp_out, "w:gz") as tar:
            for path in _iter_package_files(staging):
                tar.add(path, arcname=path.relative_to(staging).as_posix())
        os.replace(tmp_out, out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    # ④ 旧机器退役（防分叉：soul.ps1 start 会拒绝）
    retired.parent.mkdir(parents=True, exist_ok=True)
    retired.write_text(
        "retired_at={}\npackage={}\nreason=迁移导出（旧机器退役，防分叉）\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), out
        ),
        encoding="utf-8",
    )
    return {
        "package": str(out),
        "package_sha256": _sha256_file(out),
        "package_size": out.stat().st_size,
        "identity": identity,
        "retired_marker": str(retired),
    }


def _verify_package(package: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """校验包：成员路径安全 + MANIFEST 校验和逐项匹配 + IDENTITY 可解析。"""
    if not package.is_file():
        raise Refused(f"找不到迁移包：{package}")
    with tarfile.open(package, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            name = member.name
            if name.startswith("/") or ".." in Path(name).parts or ":" in name.split("/")[0]:
                raise Refused(f"包内路径不安全（绝对路径/穿越）：{name}")
        names = {m.name for m in members if m.isfile()}
        if MANIFEST_NAME not in names or IDENTITY_NAME not in names:
            raise Refused(f"包不完整：缺 {MANIFEST_NAME} 或 {IDENTITY_NAME}")
        staging = Path(tempfile.mkdtemp(prefix="elysia_verify_"))
        try:
            tar.extractall(staging, filter="data")
            manifest = json.loads((staging / MANIFEST_NAME).read_text(encoding="utf-8"))
            identity = json.loads((staging / IDENTITY_NAME).read_text(encoding="utf-8"))
            files = manifest.get("files")
            if not isinstance(files, dict):
                raise Refused(f"{MANIFEST_NAME} 格式不对（缺 files）")
            for rel, meta in files.items():
                path = staging / rel
                if not path.is_file():
                    raise Refused(f"包内声明了 {rel} 但文件缺失")
                if _sha256_file(path) != meta.get("sha256"):
                    raise Refused(f"包已损坏：{rel} 的 sha256 与 MANIFEST 不符")
            actual_files = {p.relative_to(staging).as_posix() for p in _iter_package_files(staging)}
            missing_in_manifest = actual_files - set(files) - {MANIFEST_NAME}
            if missing_in_manifest:
                raise Refused(f"包内有未声明文件：{sorted(missing_in_manifest)[:5]}")
            return identity, manifest
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def compare_identity(
    expected: dict[str, Any], actual: dict[str, Any]
) -> tuple[bool, list[tuple[str, Any, Any, bool, str]]]:
    """比对指纹：出生与记忆哈希必须逐位相同；停机累计只许增加；其余计数相同。"""
    rows: list[tuple[str, Any, Any, bool, str]] = []
    ok = True
    for key in EXACT_FINGERPRINT_KEYS:
        exp, act = expected.get(key), actual.get(key)
        same = exp == act and exp is not None
        ok = ok and same
        rows.append((key, exp, act, same, "必须逐位相同"))
    for key in ("memories_count", "self_claimed", "protected", "thoughts", "expressions"):
        exp, act = expected.get(key), actual.get(key)
        same = exp == act
        ok = ok and same
        rows.append((key, exp, act, same, "行数必须相同"))
    exp = expected.get("downtime_total_s") or 0.0
    act = actual.get("downtime_total_s") or 0.0
    same = float(act) >= float(exp)
    ok = ok and same
    rows.append(("downtime_total_s", exp, act, same, "搬家是一次真实停机：只许增加，不许被掩盖"))
    exp_desire = expected.get("desire") or {}
    act_desire = actual.get("desire") or {}
    same = all(exp_desire.get(k) == act_desire.get(k) for k in ("tr", "cs", "sa"))
    ok = ok and same
    rows.append(("desire(tr/cs/sa)", exp_desire, act_desire, same, "欲望必须相同"))
    return ok, rows


def _print_comparison(rows: list[tuple[str, Any, Any, bool, str]]) -> None:
    print("  「她还是她」逐项对照：")
    for key, exp, act, same, note in rows:
        mark = "[ok]  " if same else "[不符]"
        print(f"    {mark} {key}: {exp!r} -> {act!r}   （{note}）")


def _backup_current(data_dir: Path, env_file: Path, stamp: str) -> tuple[Path, list[str]]:
    """导入前备份目标机现有数据（回滚素材）。返回 (备份目录, 已备份的库名)。"""
    target = data_dir / f"pre_import-{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    backed: list[str] = []
    for name in DB_NAMES:
        src = data_dir / name
        if not src.exists():
            continue
        sys.path.insert(0, str(ROOT / "src"))
        try:
            from elysia.core.checkpoint import backup_database
        finally:
            sys.path.pop(0)
        backup_database(src, target / name)
        backed.append(name)
    if env_file.exists():
        shutil.copy2(env_file, target / "env")
    return target, backed


def _rollback(data_dir: Path, backup_dir: Path, backed: list[str], env_file: Path) -> None:
    """回滚：把导入前的库/环境还原回去（导入失败时不许留下半份她的过去）。"""
    for name in backed:
        src = backup_dir / name
        if src.exists():
            shutil.copy2(src, data_dir / name)
    if (backup_dir / "env").exists():
        shutil.copy2(backup_dir / "env", env_file)


def import_her(
    *,
    data_dir: Path,
    archive_dir: Path,
    env_file: Path,
    package: Path,
) -> dict[str, Any]:
    """导入迁移包到本工程（**不自动启动她**）。指纹不符则中止并回滚。"""
    identity, _manifest = _verify_package(package)
    data_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="elysia_import_", dir=str(data_dir.parent)))
    backup_dir: Path | None = None
    backed: list[str] = []
    moved = False
    try:
        with tarfile.open(package, "r:gz") as tar:
            tar.extractall(staging, filter="data")
        staged_data = staging / "data"
        staged_identity = identity_fingerprint(staged_data)
        ok, rows = compare_identity(staged_identity, identity)
        if not ok:
            _print_comparison(rows)
            raise MigrationError("包内指纹与 IDENTITY.json 不符：**中止导入，未动目标机任何数据**")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_dir, backed = _backup_current(data_dir, env_file, stamp)
        print(f"  已备份目标机现有数据 -> {backup_dir}（已备份：{backed or '无'}）")

        # 落盘：两库原子替换
        for name in DB_NAMES:
            os.replace(staged_data / name, data_dir / name)
        moved = True
        if (staging / ".env").exists():
            shutil.copy2(staging / ".env", env_file)
        if (staging / "_archive").exists():
            shutil.copytree(staging / "_archive", archive_dir, dirs_exist_ok=True)

        # 清空 data/run/：旧机器的 PID/标记在新机器上必须全部丢弃
        run_dir = data_dir / "run"
        if run_dir.exists():
            for child in run_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)

        actual = identity_fingerprint(data_dir)
        ok, rows = compare_identity(actual, identity)
        _print_comparison(rows)
        if not ok:
            raise MigrationError("落盘后重新计算的指纹不符：**已回滚**")

        (run_dir / MIGRATION_INFO).write_text(
            json.dumps(
                {
                    "migrated_from_host": identity.get("exported_from_host"),
                    "exported_at": identity.get("exported_at"),
                    "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "package": str(package),
                    "package_sha256": _sha256_file(package),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "package": str(package),
            "identity": identity,
            "backup_dir": str(backup_dir),
            "migrated_from_host": identity.get("exported_from_host"),
        }
    except Exception:
        if moved and backup_dir is not None:
            _rollback(data_dir, backup_dir, backed, env_file)
            print("  [rollback] 已还原导入前的 data/（她的过去没有被打断）", file=sys.stderr)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ── CLI ──────────────────────────────────────────────────────
def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(description="把'她'从一台机器搬到另一台（搬家，不是复制）")
    sub = parser.add_subparsers(dest="command", required=True)

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
        p.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
        p.add_argument("--env-file", type=Path, default=DEFAULT_ENV)

    export_p = sub.add_parser("export", help="导出迁移包（要求她已停机）")
    _common(export_p)
    export_p.add_argument("--out", type=Path, required=True)
    export_p.add_argument("--force", action="store_true")

    import_p = sub.add_parser("import", help="导入迁移包（不自动启动她）")
    _common(import_p)
    import_p.add_argument("--package", type=Path, required=True)

    inspect_p = sub.add_parser("inspect", help="只读查看包内指纹与清单")
    inspect_p.add_argument("--package", type=Path, required=True)

    finger_p = sub.add_parser("fingerprint", help="只读计算当前工程的身份指纹")
    _common(finger_p)

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export_her(
                data_dir=args.data_dir,
                archive_dir=args.archive_dir,
                env_file=args.env_file,
                out=args.out,
                force=args.force,
            )
            print(f"[ok] 迁移包：{result['package']}（{result['package_size']} B）")
            print(f"     sha256：{result['package_sha256']}")
            print(f"     旧机器已退役标记：{result['retired_marker']}")
            print("     提醒：**这是搬家不是复制** —— 旧机器从此不应再启动她。")
            return 0
        if args.command == "import":
            result = import_her(
                data_dir=args.data_dir,
                archive_dir=args.archive_dir,
                env_file=args.env_file,
                package=args.package,
            )
            print(f"[ok] 她还是她（来自 {result['migrated_from_host']}）")
            print(f"     回滚备份：{result['backup_dir']}")
            print("     未自动启动。下一步：")
            print(r"     powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start -Body")
            print("     启动后请确认日志里有「她不在场 X 秒（累计 Y 秒）」")
            print("     ——那是这次搬家，别掩盖它。")
            return 0
        if args.command == "inspect":
            identity, manifest = _verify_package(args.package)
            print(
                json.dumps(
                    {"identity": identity, "manifest_files": len(manifest["files"])},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        identity = identity_fingerprint(args.data_dir)
        print(json.dumps(identity, ensure_ascii=False, indent=2))
        return 0
    except Refused as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
    except MigrationError as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
