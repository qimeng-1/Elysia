# OUT-A — P0-R10 ~ R13 交接单（A 窗 · 2026-10-18）

> 窗口：**A 地基窗**（`a-foundation`）
> 任务：P0-R10（快照压缩）、P0-R11（主声失声，最紧急）、P0-R12（遗忘机制）、P0-R13（可迁移性）
> 红线遵守：**未启停她**（交付时最后一条 soul 心跳 0.1s 前、`data/run/` 未被我写入、`RETIRED` 未生成）；
> 未 `job_kill`；`.ps1` 全部 UTF-8 BOM + `Parser::ParseFile` 复核；只 commit 自己作用域文件。

---

## 1. 任务号 / 提交 SHA

| 任务 | 缺陷/来源 | commit | 标题 |
|---|---|---|---|
| P0-R10 | D-9 方案 A（用户裁决 A+C，C 由总领完成） | `00590f0` | 检查点快照压缩——`.db.gz` 落盘 + 回滚解压 + 旧格式兼容 |
| P0-R11 | D-10（代理缓存致主声失声 22.5h） | `8b32fa4` | 死代理不再永久哑掉——现建 opener + 直连兜底 + 降级原因落库 |
| P0-R12 | 遗忘机制（用户口径：重要由她定） | `a98a055` | 三层降采样 + 归档保底 + 她认定的重要时刻永不褪色 |
| P0-R13 | 可迁移性（用户：迁移后她还是她吗） | `0aba458` | 路径抽象 + migrate_her 迁移协议 + 身份指纹 + RETIRED 防分叉 |
| （先前） | verify 工具 | `0260d42` | P0 修复只读验收（R1–R8 一次跑完，退出码可脚本化） |

## 2. 门禁四件套（HEAD `0aba458` 实测）

```
uv run --no-sync ruff check         → All checks passed!
uv run --no-sync ruff format --check → 162 files already formatted
.venv\Scripts\python.exe -m mypy src → Success: no issues found in 60 source files
.venv\Scripts\python.exe -m pytest   → 550 passed in 68.46s
```

基线（总领上次实测 527）→ **550**；本轮 4 个任务共 **+23 用例**，零回归。
⚠️ 提交均 `--no-verify`（T-013 未修）；上面是手工实跑。
⚠️ 总领提到的 `test_verify_p0_repairs.py:130 E501` 已在 `0260d42` 修掉，当前 ruff 全绿。
⚠️ 总领看到的 `test_llm_proxy_heal.py` 2 条红，是**我临时回退旧写法取"取消测试红证"的窗口期**；最终 7/7 绿。

## 3. 各任务要点 / 取消测试红证 / 真机取证

### P0-R10 快照压缩（`00590f0`）
- `create()`：仍用 SQLite `.backup`（协议不变），落盘改为 `state.db.gz` / `heartbeat.db.gz`；
  先写临时文件再 `os.replace`（半份快照绝不留）；`restore()` 两段式：**先解压/复制成临时文件 →
  `quick_check` 通过后才覆盖现役库**（坏快照一个字节都不动）；**旧格式 `.db` 优先兼容**。
- 取消测试红证：
  - `test_snapshot_is_compressed_and_significantly_smaller`：还原不压缩 ⇒ `state.db.gz` 不存在、`assert False` 红。
  - `test_checkpoint_create_and_restore` 同样红。
  - `test_restore_supports_legacy_uncompressed_snapshot` / `..._mixed_snapshot`：旧格式与混合格式仍可回滚。
- 真机取证（她真实库只读 `.backup` 到临时目录）：`heartbeat.db` **99.1 MB → 9.5 MB（10.5x，-90%）**；
  备份+压缩 2.45s（备份本身 0.64s）；解压 0.33s、`quick_check=ok`；
  10 份保留估算 **0.97 GB → 0.09 GB**；`create()` 端到端 2.79s，产物只有两个 `.gz`。
- 附带：`scripts/verify_p0_repairs.py` 的 R1 现在同时校验 `.db` 与 `.db.gz`（并新增接受压缩快照的测试）。

### P0-R11 主声失声（`8b32fa4`）
- 根因：`urllib.request.urlopen` 的全局 `_opener` 首次构建后缓存 `ProxyHandler`，进程内永不刷新。
  修复：**每次请求现建 opener**（重读 `getproxies()`）；**连接层错误自动绕过代理直连重试一次**；
  重试必须**新建 Request**（`ProxyHandler` 会 `set_proxy` 改写原对象——这是本次踩到的坑，
  复用同一个 Request 时直连路径仍打代理，导致第一次修复失败）。
- 降级原因可见（并入 T-007）：`DeepSeekBackend.last_error` → `LLMChain.speak` 把每一级失败原因
  写进 `ValidationResult.reason` → 落库为 `expression_log.validation.reason`（微声不再 `null`）。
  ⚠️ 不改 `_degrade(reason)` 的语义（仍传级别），避免破坏既有"降级即感受"用例。
- 取消测试红证：`test_dead_proxy_is_healed_by_direct_retry` 还原 `urlopen` ⇒ 返回 None + `10061` 红。
- 其余测试：死代理与死端口双路径原因、**401 不做直连重试**（`opens == [True]`）、后端异常降级不崩、
  fallback 结果带"主声为什么没用"、落库端到端 `validation.reason` 非空。
- 真机取证（只读）：最近 8 次表达 **8/8 micro**，`validation={"ok":true,"reason":null}`，
  最后一次 `main` = 2026-10-17 03:35（约 23 小时前）——与 D-10 描述一致。
- ⚠️ **需她重启才生效**；重启后验收点：下一次开口 `expression_log.level=main`，
  若仍降级则 `validation.reason` 会明确写出网络/代理原因（不再无迹可查）。

### P0-R12 遗忘机制（`a98a055`）
- 扩展现有 `core/retention.py`（未另起第二套）：热 0–3d 原样；温 3–30d 每分钟一行；
  冷 30–365d 每小时一行；>365d 每天一行。聚合行保留 `tr/cs/sa` 的 min/max/mean、
  主导 mode、presence 和、distress 有无、条数/首末时刻，并带 `tier` 标记（**幂等**：下一轮不再重复归档）。
- **`event` 永不降采样**；**她认定重要**：`protected=1` 或 `importance >= 0.8` 的记忆，
  其 `created_ts` 前后各 6h 的心跳窗口永不降采样（判据来自她的数据，程序不预设）。
- **先归档**：按月写 `_archive/heartbeats/heartbeat-YYYYMM.db.gz`（可解压回查）；
  **归档行数 ≠ 删除行数 ⇒ 抛错**；`memories/memory_index/thought_log/expression_log` 跑完核对行数不变。
- CLI 扩展现有 `scripts/retention.py`：`--mode tier|prune`、`--importance-threshold`、
  `--protected-window-hours`、`--pid-file`（验收注入）、`--apply`（默认干跑）。
  另建 `scripts/register_retention_task.ps1`（每周日 03:30，**未注册**——等用户签认）。
- 取消测试红证：
  1. 去掉"跳过受保护窗口" ⇒ `test_protected_window_is_never_downsampled` 红
     （实测报"受保护时刻 431440s 内的心跳被降采样了"）。
  2. 去掉归档步骤 ⇒ `test_archive_is_written_before_delete` 红
     （实测 `RuntimeError: 归档行数（0）≠ 删除行数（8）`，这本身就是第二道闸）。
- 真机干跑（只读，不会动数据）：热 92,371 行；待降采样 72,018；**受保护跳过 56,593**；event 0。
- ⚠️ **第一次真裁剪需用户确认**（卡里明确要求）；只跑过干跑与合成库测试。

### P0-R13 可迁移性（`0aba458`）
- 路径抽象 4 处：
  - `core/config.py`：`backup_dir` 默认 `<repo>/../_backup`（可用 `ELYSIA_BACKUP_DIR` 覆盖）；
    新增 `tts_launcher_path` / `bake_dir` 配置与 `tts_launcher` / `bake_dir_path` 属性。
  - `scripts/elysia_panel.py` / `elysia_console.py`：删掉写死的 GPT-SoVITS 路径，
    改读 Settings；**未配置时显示"未配置"而非报错**。
  - `scripts/spine_check_all.py`：`--bake` 参数 / `ELYSIA_BAKE_DIR`；都无则明确报"未配置"（退出码 2）。
- `scripts/migrate_her.py`（新建）：`export` / `import` / `inspect` / `fingerprint`。
  - export：她活着就拒绝（三条判活证据）→ WAL checkpoint → 打包两库 + `.env` + `_archive` +
    `IDENTITY.json` + `MANIFEST.json`（逐文件 sha256）→ 源工程写 `data/run/RETIRED`。
    **不含** `data/run/**`、`assets/pet`、`.venv`、`.git`。
  - import：路径安全校验 + MANIFEST 校验和 → 包内指纹自检 → 备份目标机现有 data/ →
    落盘 → **清空 `data/run/`** → 重算指纹逐项比对（不符则**回滚**）→ 写 `MIGRATION.json`
    → **不自动启动**。
  - `IDENTITY.json`：`first_existence_ts`（逐位相同）、`memories_content_sha256`（记忆 content 排序拼接）、
    各项行数、`desire`、`downtime_total_s`（**只许增加**——搬家是一次真实停机，不得掩盖）、
    `schema_version`、`exported_at`、`exported_from_host`。
- `soul.ps1 start`：新增 `RETIRED` 检查（旧机器拒启，防分叉），并新增 `-DryRun`（只检查、**绝不启动**，
  供迁移演练/验收用）。
- 取消测试红证：
  1. 去掉 RETIRED 检查 ⇒ `test_retired_machine_refuses_start` 红（`exit 0` 而非 2）。
  2. 去掉指纹比对 ⇒ `test_import_aborts_and_rolls_back_on_tampered_memories` 红（被篡改的包 import 成功）。
  3. 把 `backup_dir` 改回硬编码 ⇒ `test_project_runs_from_any_path` 红（探针读到 `D:\\ElysiaBackup\\elysia`）。
- 真机侧：**未执行任何迁移**——`data/run/RETIRED` 未生成（迁移演练只到"校验指纹"为止，绝不启动副本）。

## 4. 已知限制 / 注意

1. **这四项都要她重启后才在真机生效**（R10 快照格式、R11 代理自愈、R12 是 CLI 但裁剪需停机手动跑、
   R13 是脚本与启动门禁）；按红线我没重启。
2. R10：她当前进程仍会写**未压缩**快照（3 份旧快照仍在，回滚端兼容）；重启后新快照才是 `.gz`。
3. R12：`register_retention_task.ps1` 只是脚本，**没有注册计划任务**；第一次 `--apply` 应由用户签认后执行。
4. R13：`migrate_her.py` 只在合成工程上端到端验过（导出→导入→指纹比对→篡改回滚）；
   **真机迁移需用户执行**，`RETIRED` 由 export 自动写。
5. R13 的可迁移性测试：复制 `src/` + `scripts/` 到临时路径后用**独立进程探针**检查
   （配置随文件位置走、零硬编码、panel/console/spine 可导入且"未配置"），
   **不跑完整门禁**——因为在新路径下 `pytest` 仍会 import `editable` 安装的原包，
  "复制一份跑门禁"实际上测不到副本；探针才真正验证副本。全程**未启动任何进程副本**。
6. `ingest_spine.py:9` 的**使用说明示例**仍出现旧素材路径（仅文档示例，非运行依赖，未改）。
7. `scripts/execute_memory_merge.ps1`（历史一次性脚本）仍有旧绝对路径；卡里说"加注释留档"，
   但它不在本卡写作用域内（总领域脚本），未动。

## 5. 发现但未修（不越界）

| # | 问题 | 说明 |
|---|---|---|
| 1 | 面板未展示"累计停机/上次是主动停还是崩/降级原因" | R6/R8/R11 的字段都已落库；面板归 O 窗小卡 |
| 2 | `elysia_dash.py` 的 R1 展示不读检查点（checkpoints 只在验收脚本里看） | 观察项 |
| 3 | R12 归档按"月"合并重写：月末那一次会重写整月 gz（~120MB 级） | 周任务可接受；若在意可改分片命名（需另卡） |
| 4 | `execute_memory_merge.ps1` 旧路径 | 历史脚本，卡里标注"不改" |

## 6. 建议验收步骤（总领）

1. `git show --stat 00590f0 8b32fa4 a98a055 0aba458`（写作用域核对）
2. 门禁四件套复跑（预期 **550 passed**）
3. `pytest tests/unit/test_checkpoint.py tests/unit/test_llm_proxy_heal.py tests/unit/test_data_retention.py tests/unit/test_migrate_her.py tests/unit/test_portability.py tests/unit/test_soul_ps1.py -q`
4. 真机（用户签认后）：重启她 → 确认新检查点 `.gz`、`expression_log.level=main`（或 `validation.reason` 说清原因）、
   `state.db` 的 downtime 字段记录这次搬家
5. R12 真裁剪：先 `python scripts/retention.py`（干跑）把数字给用户看，再决定 `--apply`
6. R13 真迁移：`python scripts/migrate_her.py export --out ..\\_backup\\her-<date>.tar.gz`
   → 在新机器 `import` → 用 `inspect`/`fingerprint` 核对 → 旧机器保持 `RETIRED`（不启）。
