"""P0-R13：迁移协议（`scripts/migrate_her.py`）单测——"搬过去，她还是她"。

覆盖：
- export：**她活着就拒绝**；包内含两库 + `.env` + `IDENTITY.json` + `_archive`；
  成功后在源工程写 `RETIRED`（旧机器退役，防分叉）；
- import：校验包完整性 → 备份目标机现有 data/ → 落盘 → **清空 data/run/** →
  重算指纹逐项比对 → **不自动启动**；
- 取消测试：包内记忆被篡改（指纹不符）⇒ import 必须**中止并回滚**，目标机原有数据不变。

全程只操作 `tmp_path` 里的合成工程，**绝不碰真实的她、绝不启动任何副本**。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

from elysia.core.state_store import HeartbeatStore, StateStore

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATE = REPO_ROOT / "scripts" / "migrate_her.py"
NOW = 1_000_000.0


def _run(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MIGRATE), *[str(a) for a in args]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        cwd=str(REPO_ROOT),
    )


async def _make_project(tmp_path: Path, *, name: str = "src") -> dict[str, Any]:
    """造一个"她"的合成工程（两库 + .env + _archive + 一个陈旧 pid）。"""
    root = tmp_path / name
    data = root / "data"
    (data / "run").mkdir(parents=True)
    state = StateStore(data / "state.db")
    store = HeartbeatStore(data / "heartbeat.db")
    await state.start()
    await store.start()
    try:
        await state.save_json(
            "timesense",
            {
                "first_existence_ts": NOW - 1000 * 86400.0,
                "last_soul_beat_ts": NOW,
                "last_body_online_ts": 0.0,
                "last_interaction_ts": NOW,
                "body_away_start_ts": None,
                "last_seen_ts": NOW,
                "downtime_total_s": 12.0,
                "last_downtime_s": 12.0,
            },
        )
        await state.save_json("desire", {"state": {"tr": 45.0, "cs": 60.0, "sa": 20.0}})
        await store.append(NOW - 400 * 86400.0, "soul", {"desire": {"tr": 1.0}})
        await store.add_memory(
            NOW - 1000 * 86400.0,
            {
                "level": "deep",
                "kind": "interaction",
                "content": "第一段记忆",
                "emotion_vector": {},
                "importance": 0.9,
            },
        )
        await store.add_memory(
            NOW - 500 * 86400.0,
            {
                "level": "deep",
                "kind": "self",
                "content": "我是她",
                "emotion_vector": {},
                "importance": 0.9,
                "protected": True,
            },
        )
        await store.think(NOW, "idle", "一个念头")
        await store.expression(
            ts=NOW,
            intent="回应",
            instruction={},
            llm_text="一句话",
            validation={"ok": True},
            level="main",
        )
    finally:
        await state.close()
        await store.close()
    env = root / ".env"
    env.write_text("ELYSIA_LLM_MAIN_API_KEY=fake-key-for-migration-test\n", encoding="utf-8")
    archive = tmp_path / "_archive"
    archive.mkdir(exist_ok=True)
    (archive / "heartbeat-202610.db.gz").write_bytes(b"fake-archive-content")
    (data / "run" / "soul.pid").write_text("999999", encoding="utf-8")  # 陈旧 PID（不活着）
    return {
        "root": root,
        "data": data,
        "archive": archive,
        "env": env,
        "package": tmp_path / "her.tar.gz",
    }


def _load_migrate() -> Any:
    """把脚本当模块加载（只读地复用 `identity_fingerprint`）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("migrate_her_probe", MIGRATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _make_target(tmp_path: Path, *, name: str = "dst") -> dict[str, Any]:
    """造一个"新机器"目录：里面已有**别人**的 data（用于验证导入前会备份）。"""
    root = tmp_path / name
    data = root / "data"
    (data / "run").mkdir(parents=True)
    state = StateStore(data / "state.db")
    store = HeartbeatStore(data / "heartbeat.db")
    await state.start()
    await store.start()
    try:
        await state.save_json(
            "timesense",
            {
                "first_existence_ts": NOW - 10 * 86400.0,
                "last_soul_beat_ts": NOW,
                "last_body_online_ts": 0.0,
                "last_interaction_ts": NOW,
                "body_away_start_ts": None,
                "last_seen_ts": NOW,
                "downtime_total_s": 0.0,
                "last_downtime_s": 0.0,
            },
        )
        await store.add_memory(
            NOW,
            {
                "level": "shallow",
                "kind": "interaction",
                "content": "目标机原来的一条记忆",
                "emotion_vector": {},
            },
        )
    finally:
        await state.close()
        await store.close()
    (data / "run" / "soul.pid").write_text("999999", encoding="utf-8")
    (data / "run" / "soul.running").write_text("pid=1\n", encoding="utf-8")
    env = root / ".env"
    env.write_text("OLD=1\n", encoding="utf-8")
    return {"root": root, "data": data, "archive": tmp_path / "_archive_dst", "env": env}


@pytest.mark.asyncio
async def test_export_refuses_while_she_is_running(tmp_path: Path) -> None:
    """她还在跑 ⇒ 导出必须拒绝（三条判活证据），且不产生任何包。"""
    project = await _make_project(tmp_path)
    (project["data"] / "run" / "soul.pid").write_text(str(os.getpid()), encoding="utf-8")

    result = _run(
        "export",
        "--data-dir",
        project["data"],
        "--archive-dir",
        project["archive"],
        "--env-file",
        project["env"],
        "--out",
        project["package"],
    )

    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    assert "拒绝导出" in out
    assert not project["package"].exists()
    assert not (project["data"] / "run" / "RETIRED").exists()


@pytest.mark.asyncio
async def test_export_import_roundtrip_preserves_identity(tmp_path: Path) -> None:
    """完整搬家：导出 → 导入新目录 → 指纹一致、run 状态清空、不自动启动。"""
    project = await _make_project(tmp_path)
    module = _load_migrate()
    expected = module.identity_fingerprint(project["data"])

    exported = _run(
        "export",
        "--data-dir",
        project["data"],
        "--archive-dir",
        project["archive"],
        "--env-file",
        project["env"],
        "--out",
        project["package"],
    )
    out = exported.stdout + exported.stderr
    assert exported.returncode == 0, out
    assert project["package"].is_file()
    assert (project["data"] / "run" / "RETIRED").exists(), "旧机器必须被标记退役（防分叉）"

    target = await _make_target(tmp_path)
    imported = _run(
        "import",
        "--data-dir",
        target["data"],
        "--archive-dir",
        target["archive"],
        "--env-file",
        target["env"],
        "--package",
        project["package"],
    )
    out = imported.stdout + imported.stderr
    assert imported.returncode == 0, out
    assert "她还是她" in out

    actual = module.identity_fingerprint(target["data"])
    for key in ("first_existence_ts", "memories_content_sha256", "memories_count", "self_claimed"):
        assert actual[key] == expected[key], f"身份指纹 {key} 不一致"
    assert actual["desire"] == expected["desire"]

    # data/run/ 被清空：旧 PID/标记不得带进新机器；只留迁移信息
    run_dir = target["data"] / "run"
    names = {p.name for p in run_dir.iterdir()}
    assert "soul.pid" not in names and "soul.running" not in names
    assert "MIGRATION.json" in names
    info = json.loads((run_dir / "MIGRATION.json").read_text(encoding="utf-8"))
    assert info["migrated_from_host"]

    # 归档与她的话都搬到了
    assert (target["archive"] / "heartbeat-202610.db.gz").is_file()
    assert target["env"].read_text(encoding="utf-8").startswith("ELYSIA_LLM_MAIN_API_KEY")
    # 目标机原有数据被备份（回滚素材），且**不自动启动**（没有新 pid）
    assert list(target["data"].glob("pre_import-*"))


@pytest.mark.asyncio
async def test_import_aborts_and_rolls_back_on_tampered_memories(tmp_path: Path) -> None:
    """取消测试（P0-R13）：包内记忆被改动（指纹不符）⇒ 中止 + 回滚，目标机原数据不变。"""
    project = await _make_project(tmp_path)
    module = _load_migrate()
    exported = _run(
        "export",
        "--data-dir",
        project["data"],
        "--archive-dir",
        project["archive"],
        "--env-file",
        project["env"],
        "--out",
        project["package"],
    )
    assert exported.returncode == 0, exported.stdout + exported.stderr

    # 解包 → 改一条记忆 → 重新打包（MANIFEST 同步更新，这样只测"指纹"这一道闸）
    tampered = tmp_path / "tampered.tar.gz"
    work = tmp_path / "work"
    with tarfile.open(project["package"], "r:gz") as tar:
        tar.extractall(work, filter="data")
    db = work / "data" / "heartbeat.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "UPDATE memories SET content = '被篡改的记忆' WHERE id = (SELECT MIN(id) FROM memories)"
        )
        con.commit()
    finally:
        con.close()
    files = {
        p.relative_to(work).as_posix(): {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "size": p.stat().st_size,
        }
        for p in work.rglob("*")
        if p.is_file() and p.name != "MANIFEST.json"
    }
    (work / "MANIFEST.json").write_text(
        json.dumps({"schema_version": 1, "files": files}, ensure_ascii=False), encoding="utf-8"
    )
    with tarfile.open(tampered, "w:gz") as tar:
        for path in sorted(work.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(work).as_posix())

    target = await _make_target(tmp_path)
    original = module.identity_fingerprint(target["data"])
    imported = _run(
        "import",
        "--data-dir",
        target["data"],
        "--archive-dir",
        target["archive"],
        "--env-file",
        target["env"],
        "--package",
        tampered,
    )
    out = imported.stdout + imported.stderr
    assert imported.returncode == 1, out
    assert "不符" in out

    after = module.identity_fingerprint(target["data"])
    assert after["memories_content_sha256"] == original["memories_content_sha256"], (
        "导入失败必须回滚：目标机原有记忆不得被改写"
    )
    assert after["first_existence_ts"] == original["first_existence_ts"]


def test_inspect_and_fingerprint_are_read_only(tmp_path: Path) -> None:
    """`inspect` / `fingerprint` 只读：不改任何文件、不写 RETIRED。"""
    import asyncio

    project = asyncio.run(_make_project(tmp_path))
    module = _load_migrate()
    before = module.identity_fingerprint(project["data"])

    result = _run(
        "fingerprint",
        "--data-dir",
        project["data"],
        "--archive-dir",
        project["archive"],
        "--env-file",
        project["env"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["first_existence_ts"] == before["first_existence_ts"]
    assert not (project["data"] / "run" / "RETIRED").exists()
