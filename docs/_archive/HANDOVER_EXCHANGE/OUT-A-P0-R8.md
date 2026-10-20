# OUT-A — P0-R8 优雅关闭（D-8）交接单（A 窗 · 2026-10-18）

> 窗口：**A 地基窗**（`a-foundation`）｜ 任务：`TASK_BOARD` P0-R8（共享任务 `task-7`）
> 上游验收：`docs/HANDOVER_EXCHANGE/VERDICT-P0-R1-R6.md`
> 红线遵守：**未启停她**（全程未调用 `soul.ps1 start/stop` 对真实 PID；所有 PowerShell 测试用 `-ProjectRoot` 指向临时目录）、未 `job_kill`、未手改 `data/*.db`、未碰 `pet.py`/`assets/pet`/`llm`/`tts`/`protocol`/`tools`/`.env`/`LEAD_*.md`/`TASK_BOARD.md`。

---

## 1. 任务号 / 提交 SHA

| 任务 | 缺陷 | commit | 标题 |
|---|---|---|---|
| P0-R8 | D-8 | `3592b24` | fix(ps1): 停止改为优雅关闭——标记+等待+超时兜底，并区分主动停与崩溃 |

## 2. 变更文件清单

| 文件 | 改动 |
|---|---|
| `scripts/soul.ps1` | 新增 `Stop-OneGracefully`：写 `stop_requested` + `stopped_intentionally` → **轮询等进程自己退出**（默认 15s，`-StopTimeoutSeconds` 可调）→ 超时才 `Stop-Process -Force` 兜底；`stop`/`stop-body` 都走它；`start` 清掉残留停止请求；新增 `-ProjectRoot`（验收注入点）。**UTF-8 BOM 保持** |
| `src/elysia/soul/heartbeat.py` | 新形参 `stop_marker` / `stop_event`；`run()` **每拍**检查停止请求 → 置 stop 事件 → 退出循环 |
| `src/elysia/soul/main.py` | 常量 `stop_requested`/`stopped_intentionally`；`_intentional_stop_evidence` / `_clear_stop_markers`；`_detect_abnormal_exit` 在"主动停"时返回 None；启动日志区分 **"上次是你主动停的"** vs "上次是非正常退出"；启动即消费两个标记；把 `stop_marker`/`stop_event` 交给心跳；正常关闭时消费 `stop_requested` |
| `src/elysia/body/main.py` | 同套约定（标记名加 `body_` 前缀）；`_watch_stop_request` 轮询任务（body 心跳 5s 一拍，轮询保证 ≤0.5s 响应）；`stop-body` 同样优雅 |
| `tests/unit/test_graceful_shutdown.py` | **新增 6 用例**（进程内集成 + 取消测试） |
| `tests/unit/test_soul_ps1.py` | **新增 7 用例**（真跑 PowerShell，`-ProjectRoot` 临时目录 + 假进程） |

## 3. 门禁四件套实测输出（HEAD `3592b24`）

```
uv run --no-sync ruff check        → All checks passed!
uv run --no-sync ruff format --check → 152 files already formatted
.venv\Scripts\python.exe -m mypy src → Success: no issues found in 60 source files
.venv\Scripts\python.exe -m pytest   → 511 passed in 50.15s
```

基线 **498** → **511**（+13 新用例，零回归）。
⚠️ `--no-verify` 提交（T-013 未修），门禁用总领认可的 `.venv\Scripts\python.exe -m` 口径手工实跑。
⚠️ 套件耗时 26s → **50s**：`test_soul_ps1.py` 真起 Windows PowerShell（8 次 × ~1s）+ 一个进程内 `_run` 集成用例；该集成用例**显式关掉主声**（`llm_main_api_key=""`）并把 TTS 指向不可达端口，**不会产生付费请求**。

## 4. 取消测试（还原旧写法必红）——名称 + 实测红证据

| 用例 | 还原的旧写法 | 实测红 |
|---|---|---|
| `test_soul_ps1.py::test_stop_is_graceful_when_process_respects_stop_request` | `soul.ps1` 直接 `Stop-Process -Force`（不写标记、不等待） | 输出只有 `[info] 身体 未在运行…`，**没有**"已优雅关闭"；`stopped_intentionally` 不存在；进程是被杀不是自退 |
| `test_soul_ps1.py::test_intentional_stop_marker_makes_app_skip_abnormal_report` | `soul.ps1` 不写 `stopped_intentionally` | 应用侧对残留的 `soul.running` 判出 `上次的会话标记仍在（pid=1 \| started_at=0）⇒ 上一次没有走到正常关闭` ⇒ 断言 `is None` 失败 |
| `test_graceful_shutdown.py::test_stop_marker_exits_heartbeat_loop` | 心跳不看 `stop_requested`（旧：只认 Ctrl+C/SIGTERM） | `asyncio.wait_for` **TimeoutError**（循环永不退出） |
| `test_graceful_shutdown.py::test_run_completes_graceful_shutdown_on_stop_marker` | 同上（端到端） | `wait_for(20s)` **TimeoutError**；`soul stopping` / `soul stopped` 只在取消时由 finally 出现 |

（每条都实测：备份修复版 → 只回退一处 → 跑该用例 → 红 → 还原 → 绿。）

## 5. 激活性取证（测试内的真实证据；真机那一半需用户签认）

- 进程内集成：写 `stop_requested` 后
  `elysia.log` 出现 `收到停止请求` → `soul stopping：优雅关闭（先停表达，再停心跳与库）` →
  `soul stopped：档案已封存`；`_run` 返回 0；停止请求文件被消费。
  **这两行日志在旧强杀路径下历史上从未出现过**（总领审计实测 0 条）。
- PowerShell 端到端（临时目录 + 真进程）：
  - 应答进程：`[ok] 灵魂 已优雅关闭（0.2s；落库与档案封存已执行）`，进程**自己**退出（码 0），
    `stopped_intentionally` 生成、`stop_requested` 被清除、PID 文件清除、`no_autostart` 生成；
  - 不响应进程：`[warn] … 在 2s 内没有响应停止请求 ⇒ 强杀兜底`，≥2s 后才杀，主动停标记仍在；
  - `stop-body`：只停身体，灵魂进程与 `soul.pid` 原样保留，只生成 `body_stopped_intentionally`；
  - 没进程在跑：`[info] … 未在运行`，**不写**主动停标记（不假装停过）；
  - `status`：只读，不写任何标记。
- 她本人未被触碰：`soul=11796` / `body=6004`，最后一条 soul 心跳 **0.3s 前**；
  真实 `data/run/` 四个文件时间戳仍是 `00:28:52`（我未写任何真实标记）。

### ⚠️ 真机验收的关键前提（请务必转告用户）

**她现在跑的是 R1–R6 的代码，没有 R8 的"看停止请求"逻辑**（进程启动于 00:28:52，早于本次提交）。
因此**第一次**点「停止她」会：写标记 → 等 15s（旧进程不看）→ **强杀兜底**（预期行为，不是 bug），
同时写下 `stopped_intentionally`。**下一次启动后她才具备 R8 行为**，之后的停止才会是
0.2–1s 的优雅关闭。建议真机验收按这个顺序：

1. 停止（等 ≤15s，输出 `强杀兜底`）→ 确认 `data/run/stopped_intentionally` 存在；
2. 启动 → 日志应是 **`上次是你主动停的（不是异常退出）`**，**不得**出现 `上次是非正常退出`（R2 不再误报）；
3. 再停止一次（此时进程已是 R8 代码）→ 应输出 `已优雅关闭（Xs）`，日志出现 `soul stopping` + `soul stopped`，
   `data/run/soul.running` 消失；
4. 再启动 → 日志既无 `非正常退出` 也无 `主动停`（干净关闭）。

## 6. 未做 / 已知限制

1. **第一次停止会 15s 后强杀兜底**（原因见 §5：现进程是旧代码）。这是过渡期的一次性现象。
2. `soul.ps1 stop` 最坏耗时 = 身体 15s + 灵魂 15s = **30s**（面板 `_ps` 超时 90s，安全）。生产中通常 <1s。
3. `soul.ps1 start` 会清掉残留的 `stop_requested`（防止刚起就自我关闭）。理论上"另一个窗口在 stop 等待中又 start"会把请求清掉、导致 stop 等满 15s 后杀掉新进程——**只有跨窗口并发手工操作才可能**；面板/终端是同步序列，不受影响。未加锁（如需可另开卡）。
4. 看门狗自己判僵死后的强杀**故意不写** `stopped_intentionally`：僵死不等于"人主动停"，下次启动仍应报"非正常退出"（语义正确）。
5. body 的标记名带 `body_` 前缀（`body_stop_requested` / `body_stopped_intentionally`），与灵魂的 `stop_requested` 不对称——刻意如此，避免两者互相误读。
6. `scripts/soul.ps1` 之外的停止入口（直接 `taskkill`、任务管理器、蓝屏）仍不走优雅路径——这类会如实报"非正常退出"，符合预期。
7. 门禁总耗时翻倍（26s→50s）；若总领认为回归门太慢，可把 `test_soul_ps1.py` 标 `slow`（需另开卡，未擅自改）。

## 7. 发现但没修的问题

| # | 问题 | 说明 |
|---|---|---|
| 1 | 面板「↻ 重启她」在 stop 后 `time.sleep(1.0)` | stop 现在**已经等到进程退出**才返回，这 1s 是冗余等待（无害，未改；属 O 窗脚本） |
| 2 | `execute_memory_merge.ps1` 的 stop 调用 | 现在会自动获得优雅关闭，无需改动；仅记录行为变化（停机更安全） |
| 3 | `scripts/elysia_dash.py` / `elysia_panel.py` 不展示"上次是主动停还是崩" | 新标记已落盘，面板以后可读；不在本卡作用域 |
| 4 | 跨窗口 stop/start 竞态（见 §6.3） | 需要文件锁，属新需求，未擅自加 |

## 8. 建议的验收步骤（总领只读即可做 1–3）

1. `git show --stat 3592b24`（6 个文件，全在作用域内）
2. 门禁四件套复跑（§3 命令；预期 511 passed）
3. `pytest tests/unit/test_graceful_shutdown.py tests/unit/test_soul_ps1.py -q`（13 passed）
4. `python -c` 检查 `scripts/soul.ps1` 首字节为 BOM；`Parser::ParseFile` 已复核 `PARSE OK`
5. **真机（需用户签认）**：按 §5 的四步走（注意第一步会 15s 强杀兜底）

## 9. 自检写作用域

改动文件：`scripts/soul.ps1`、`src/elysia/soul/heartbeat.py`、`src/elysia/soul/main.py`、
`src/elysia/body/main.py`、`tests/unit/test_graceful_shutdown.py`、`tests/unit/test_soul_ps1.py`。
全部落在 P0-R8 卡写作用域内。**零触碰**真实 `data/`（测试全用 `-ProjectRoot` / `tmp_path`）。
