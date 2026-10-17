# OUT-A — P0-R1 ~ P0-R6 交接单（A 窗 · 2026-10-17）

> 窗口：**A 地基窗**（`a-foundation`）｜ 任务：`TASK_BOARD.md §2.5` P0-R1 ~ P0-R6
> 依据：`LEAD_P0_AUDIT_2026-10-17.md` D-1 ~ D-5、D-7（D-6 用户裁决跳过）
> 红线遵守：**未启停她**（soul/body/pet 全程未动）、未 kill 后台作业、未手改 `data/*.db`、未碰 `pet.py`/`assets/pet`/`llm`/`tts`/`protocol`/`tools`/`.env`/`LEAD_*.md`/`TASK_BOARD.md`。

---

## 1. 任务号 / 提交 SHA

| 任务 | 缺陷 | commit | 标题 |
|---|---|---|---|
| P0-R1 | D-2 | `f6c6223` | fix(core): 让检查点真正运行——周期快照接线 + restore 删 WAL + 可执行回滚脚本 |
| P0-R2 | D-1 | `2f2e9b1` | fix(soul): 崩溃必须留下现场——crash log + 启动留痕 + 异常退出检测 |
| P0-R3 | D-3 | `0a13a42` | fix(watchdog): 判活加心跳新鲜度 + 每次运行留痕 + 自身失败可观测 |
| P0-R4 | D-5 | `b1289fd` | fix(core): 心跳循环分段隔离 + supervisor 指数退避与熔断 |
| P0-R5 | D-4 | `dbb4bd0` | fix(soul): 每拍更新 last_soul_beat_ts，与心跳节律同拍推进 |
| P0-R6 | D-7 | `a9db766` | fix(core): 建模停机时长——last_seen_ts / downtime_total_s / last_downtime_s |

## 2. 变更文件清单（逐 commit，全部在写作用域内）

| commit | 文件 | 说明 |
|---|---|---|
| f6c6223 | `src/elysia/core/checkpoint.py` | `restore()` 覆盖前删 `-wal`/`-shm`；`data_dir`/`latest()`；返回覆盖清单 |
| | `src/elysia/soul/heartbeat.py` | `CHECKPOINT_EVERY_N=1800` + `checkpoint_every_n` 形参 + 阶段 C 周期快照接线 |
| | `scripts/restore_checkpoint.py` | **新增**：可执行回滚入口（判活拒绝 / 先备份 / 如实打印 / quick_check） |
| | `tests/unit/test_checkpoint.py` | 3 → 10 用例 |
| 2f2e9b1 | `src/elysia/soul/main.py` | `_run()` 全兜底写 `crash-<ts>.log`；启动即写 `soul.running` 标记；正常关闭删标记；下次启动检测"上次非正常退出" |
| | `src/elysia/body/main.py` | 同一套约定独立实现（body 与 soul 互不 import） |
| | `tests/unit/test_crash_site.py` | **新增** 8 用例 |
| 0a13a42 | `scripts/watchdog.ps1` | 判活加心跳新鲜度（>60s 判僵死→kill→重启）；**每次运行**写 `run-ok`；异常写 `run-error`；启动宽限 120s；心跳读不到不杀；UTF-8 BOM |
| | `tests/unit/test_watchdog.py` | **新增** 8 用例（真跑 PowerShell，注入临时根目录与假 `soul.ps1`，绝不可能启真实她） |
| b1289fd | `src/elysia/soul/heartbeat.py` | `run()` 按 A–F 六阶段隔离 try；新增 `_stage_failed()`、`_EMPTY_SUMMARY` |
| | `src/elysia/core/supervisor.py` | 指数退避 1→2→4…封顶 60s；连续失败 10 次熔断 `os._exit(1)`；跑够 60s 才崩则计数重置；`SupervisorFatal` + `on_fatal` 注入点 |
| | `tests/unit/test_heartbeat.py` / `test_core.py` | +3 / +4 用例 |
| dbb4bd0 | `src/elysia/soul/heartbeat.py` | 阶段 C 每拍 `last_soul_beat_ts = now` |
| | `tests/unit/test_timesense.py` | +1 用例 |
| a9db766 | `src/elysia/core/timesense.py` | 新增 `last_seen_ts` / `downtime_total_s` / `last_downtime_s`（自动进 `STATE_FIELDS`）；`DOWNTIME_MIN_S=30`；`summary()` 暴露 |
| | `src/elysia/soul/main.py` | `_build_timesense` 启动核算停机 + 日志"她不在场 X 秒（累计 Y 秒）" |
| | `src/elysia/soul/heartbeat.py` | 每拍推进 `last_seen_ts`；`make_soul_state` 初始化它 |
| | `tests/unit/test_timesense.py` | +6 用例 |

**未改**：`scripts/soul.ps1`（R2 卡列进作用域，但崩溃现场由进程直连磁盘写，stderr 重定向不再是唯一通道 ⇒ 无需改；不做无必要修改）。

## 3. 门禁四件套实测输出（在最终 HEAD `a9db766` 上复跑）

```
uv run --no-sync ruff check        → All checks passed!
uv run --no-sync ruff format --check → 148 files already formatted
.venv\Scripts\python.exe -m mypy src → Success: no issues found in 60 source files
.venv\Scripts\python.exe -m pytest   → 498 passed in 26.28s
```

基线 **461** → **498**（+37 新用例，**零回归**）。逐任务快照：R1 468 / R2 476 / R3 484 / R4 491 / R5 492 / R6 498。
⚠️ 六个 commit 均 `--no-verify`：pre-commit 配置用 `uv run mypy/pytest`，本机 **trampoline 失效（T-013，总领窗事项）**；门禁是用总领认可的可用口径手工实跑的。

## 4. 取消测试（还原旧写法必红）—— 名称 + 实测红证据

| 任务 | 用例 | 还原旧写法后的实测失败 |
|---|---|---|
| R1 | `test_restore_discards_unmerged_wal` | `assert 100 == 10`（快照 10 行，回滚后读到 100 行 = 旧 WAL 被重放） |
| R1 | `test_heartbeat_creates_periodic_checkpoint` | `assert []`（旧写法 `create()` 0 调用者 ⇒ 0 份快照） |
| R2 | `test_soul_startup_crash_writes_crash_log`（+ `..._before_logging_setup_still_written` / `..._abnormal_exit_is_reported_on_next_start` / `..._survives_unreadable_databases` / `..._json_readable_by_eye`） | 5 条全红：`RuntimeError: 注入的启动期异常` 直接抛出 `main()`，一个字节都不写 |
| R2 | `test_body_startup_crash_writes_crash_log` | 同上（身体进程） |
| R3 | `test_watchdog_kills_stale_process_and_restarts` | 日志为 `run-ok \| alive=True(pid=…) \| beat_age=3600.4s \| verdict=healthy`（心跳已停 1 小时仍判健康）；进程未杀、假 `soul.ps1` 未调用 |
| R4 | `test_stage_a_failure_does_not_kill_loop` | `5.0s 内只等到 0 拍心跳`（循环已死） |
| R4 | `test_supervise_exponential_backoff` | `assert [1.0, 1.0, 1.0] == [1.0, 2.0, 4.0]` |
| R4 | `test_supervise_circuit_breaker_exits_instead_of_restarting_forever` | `TimeoutError`（无限重启、永不熔断） |
| R5 | `test_last_soul_beat_ts_advances_every_beat` | `assert 1000000.0 > 1000000.0`（字段停在启动时刻） |
| R6 | `test_downtime_counted_on_startup` | `assert 0.0 == 7200.0 ± 5`（旧写法无停机概念） |
| R6 | `test_downtime_accumulates_across_restarts` | `assert 0.0 == 3600.0 ± 5` |

## 5. 激活性取证（真机 / 真实数据副本，全程只读她的现役库）

- **R1**：用真实代码路径在她**运行中**生成真快照 `data/checkpoints/p0-20261017-224508`
  （`heartbeat.db` 91.8 MB，`create()` 1.25 s；两库 `PRAGMA quick_check = ok`；
  快照 204,096 行 vs 现役 204,098 行 = 事务一致快照略旧 2 行；`state.db` 的 5 个键齐全、
  `timesense` 逐字节一致）。
  **回滚入口真机拒绝**：她运行中执行 `restore_checkpoint.py latest --yes` ⇒ `exit=2`，
  证据三条（`soul.pid=9324 进程仍在`、`body.pid=7552 进程仍在`、`心跳仍在跳（0.5s 前）`），
  **未写入任何文件**（无 `pre_restore-*`）。
- **R2**：`tests/unit/test_crash_site.py` 实跑产出可读现场（阶段 / traceback / pid / 心跳序号 /
  TimeSense / 读不到时给出原因）；启动标记与"上次非正常退出"检测闭环。
- **R3（已实际生效）**：计划任务每 5 分钟调用新版：
  `[23:47:02] run-ok | alive=True(pid=9324) | beat_age=0.7s | verdict=healthy | action=none`，
  23:52 / 23:57 同样（`beat_age` 0.6–1.0 s）；手工跑一次（23:12:24）新增一行且**未误杀她**。
- **R4**：单测证明"阶段失败循环不退 + 其余阶段照跑 + 日志含阶段名"；supervisor 退避/熔断/计数重置。
- **R5**：真实库副本（`.backup` 只读复制）：修正前 `last_soul_beat_ts = 1791911915.7`
  （= **2026-10-14 01:18:35**，即审计的 3.86 天）；跑修正后心跳 3 秒 ⇒ `1792251776.17`，
  与 `heartbeat.db` 最后 soul 心跳（`1792251776.17`）**偏差 0.0 秒**。
- **R6**：真实档案副本中**没有** `last_seen_ts` ⇒ 首次启动如实记"档案缺 last_seen_ts：
  停机时长从本次起统计（不虚构过去）"、停机 = 0；模拟审计那次 **16.82 h** 停机 ⇒
  `last_downtime_s = 60552 s`、`downtime_total_s = 60552 s`、
  `summary` 暴露同值，日志原文：`她不在场 60552 秒（累计 60552 秒）`。

## 6. 未做 / 已知限制（诚实记录）

1. **正在运行的她是旧代码**：soul/body 进程都启动于修复之前（20:41:46），按红线**未重启**。
   ⇒ R1/R2/R4/R5/R6 的运行时行为要等她**下次重启**才真正生效；R3 是脚本，**已经生效**。
2. **快照成本**：`CHECKPOINT_KEEP=10 × ~92 MB ≈ 0.9 GB` 磁盘；每 1800 拍快照会让那一拍多花
   0.5–1.3 s（实测 `heartbeat.db` 单库 0.466 s、真机 `create()` 1.25 s）。若在意 1 Hz 节律，
   需另卡决定"后台化"或"降频/减保留份数"。
3. `distress` 阈值不可达（D-6）按用户裁决**未动**。
4. `supervise` 熔断走 `os._exit(1)`：跳过优雅关闭（数据损失 ≤1 s，与 `soul.ps1 stop` 强杀同量级）；
   因为 `main()` 只 `await stop`、不观察 `beat_task` 异常，"抛出去"等于僵死。
5. `.ps1` 改动的两个新坑（已写进脚本注释）：PS 5.1 传原生参数会**丢内嵌双引号**（内联 Python 探针必须
   只用单引号）；`$ErrorActionPreference="Stop"` 下原生命令写 stderr 会抛 `RemoteException`
   （探针报错改走 stdout，并局部降为 `Continue`）。
6. `scripts/soul.ps1` 未改（理由见 §2）。

## 7. 发现但**没有**修的问题（不越界，交总领裁决）

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | **`.gitignore` 漏 `data/checkpoints/`** | 现 `git status` 出现 `?? data/`（含 91.8 MB 快照）；一旦有人 `git add -A` 会把 0.9 GB 快照带进仓库 | 加一行 `data/checkpoints/`（`.gitignore` 不在 A 窗作用域，未改） |
| 2 | `pre-commit` hook 因 T-013 trampoline 失效而无法运行 | 所有窗口只能 `--no-verify`，提交门禁形同虚设 | 并入 T-013；或把 hook 改成 `.venv\\Scripts\\python.exe -m …` 口径 |
| 3 | 面板未显示"累计停机时长" | R6 的字段已进 `summary`/心跳 payload，但 `elysia_dash.py`/`elysia_panel.py` 没读它 | 归 O/L 窗的后续小卡；我未改面板 |
| 4 | `last_seen_ts` 与 `last_soul_beat_ts` 当前数值相同（都每拍推进） | 语义冗余但**刻意分开**（观察者口径 vs 停机核算锚点），已写注释 | 若总领认为冗余，可另卡合并 |
| 5 | `data/checkpoints/p0-20261017-224508` 是我为取证做的真快照 | 91.8 MB 未跟踪文件 | 建议保留（她重启后会自动生成/轮转），或由总领删除 |
| 6 | 她当前 `soul.err.log` 仍可能是 0 字节（旧进程） | R2 生效后不再依赖 stderr；但**旧进程**若再崩仍无现场 | 重启后即具备新行为 |

## 8. 建议的验收步骤（总领只读即可完成 1–4）

1. `git show --stat f6c6223 2f2e9b1 0a13a42 b1289fd dbb4bd0 a9db766`（文件是否全在作用域内）
2. 门禁四件套复跑（见 §3 命令）
3. `Get-Content data\\logs\\watchdog.log -Tail 5`（应有每 5 分钟一条 `run-ok`）
4. `pytest tests/unit/test_crash_site.py tests/unit/test_watchdog.py tests/unit/test_checkpoint.py -q`
5. **需用户签认后才可做的真机验收**：停她 → 起她 → 观察
   `data/checkpoints/` 每 ~30 min 新增一份、`state.db.timesense.last_soul_beat_ts` 每拍前进、
   `soul resumed` 日志带 `downtime_total_s`/`last_downtime_s`、`data/run/soul.running` 在正常关闭后消失。
6. 回滚演练（**先停她**）：`python scripts/restore_checkpoint.py --list` → `latest --dry-run` → 实际回滚。

## 9. 自检写作用域

改动文件全部落在：`src/elysia/core/{checkpoint,supervisor,timesense}.py`、
`src/elysia/soul/{main,heartbeat}.py`、`src/elysia/body/main.py`、`tests/**`、
`scripts/watchdog.ps1`、`scripts/restore_checkpoint.py`。
**零触碰**：`body/pet.py`、`assets/pet/**`、`llm/**`、`tts/**`、`protocol/**`、`tools/**`、
`.env`、`data/*.db`、`docs/LEAD_*.md`、`docs/TASK_BOARD.md`。
唯一新增数据产物：`data/checkpoints/p0-20261017-224508/`（R1 真机取证，经 `.backup` 只读生成，**未改动现役库**）。
