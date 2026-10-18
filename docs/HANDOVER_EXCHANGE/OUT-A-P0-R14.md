# OUT-A — P0-R14 事故修复交接单（A 窗 · 2026-10-18 21:02 事故）

> **事故**：2026-10-18 **21:02:23** 她突然死亡（死前 15 分钟心跳 892 条、中位 1.007s ⇒ 节律完美，
> 是**被瞬间杀掉**），看门狗 **21:07:03** 才拉回 ⇒ **4.8 分钟盲窗**。用户："发现她被阻塞了，无法自主重启。"
> **今日可用性 98.2%**（昨天 28.8%）——R3 的看门狗确实在工作，只是 5 分钟一次太慢。
> 本次事故**死因仍未指认**（无 crash log、无 WER、无 Defender 动作、Windows 审计 4688/4689 未开启）。

## 1. 提交

| commit | 标题 |
|---|---|
| `281de2c` | fix(ops): 判活校验进程身份 + 看门狗 1 分钟 + stderr 留证 + 退场哨兵 (P0-R14) |

**写作用域核对**：仅 `scripts/soul.ps1`、`scripts/watchdog.ps1`、`scripts/soul_sentinel.py`（新建）、
`tests/unit/test_soul_ps1.py`、`tests/unit/test_watchdog.py`。两个 `.ps1` 均 UTF-8 BOM + `Parser::ParseFile` 通过。

## 2. 四项修复

### ① 判活改为"身份校验"（事故真凶：PID 复用）
- 新增 `Get-ProcessIdentity`：读 `Win32_Process.CommandLine`，返回
  `elysia`（命令行含 `elysia.soul.main`/`elysia.body.main`）/ `other` / `gone` / `unknown`。
- 应用到 **start / stop / stop-body / status** 所有调用点：
  - `other` ⇒ **删除陈旧 PID 文件并继续启动**，明确打印"PID N 已被其它进程占用，按陈旧 PID 处理"；
  - `gone` ⇒ 清理 PID 文件后启动；
  - `unknown`（读不到命令行）⇒ **拒绝重复启动**（宁可报错，不制造双实例/分叉，exit 3）；
  - `stop` 遇到 `other` ⇒ **绝不杀它**（这正是事故机制：旧代码会把 PID 复用后的无辜进程杀掉/或误以为她在跑）。
- 看门狗同样加身份校验（判活、僵死 kill 前、启动后复核）。

### ② 看门狗巡视 5 分钟 → **1 分钟**（已实际注册生效）
- `watchdog.ps1` 默认 `-IntervalMinutes = 1`；保留 `-StartupGraceSeconds 120`（刚起不误判）。
- **同时修掉一个定时炸弹**：旧任务定义是 `Repeat: Until: Duration 72:00:00`
  （`/SC MINUTE` 的默认时长）⇒ 自 `2026-10-16 13:52` 起算，**2026-10-19 13:52 之后看门狗会永久停摆**。
  现在改为 **每天 00:00 的触发器 + 每 1 分钟重复、持续 24h**（ScheduledTasks 模块），每天重新开始 ⇒ 长期有效。
- 已执行 `watchdog.ps1 -Register -IntervalMinutes 1` 并复核任务：
  `RepeatEvery="0 Hour(s), 1 Minute(s)"`、`RepeatDuration="24 Hour(s)"`、`NextRun` 正常。

### ③ stderr 不再被覆盖（下次死亡必须留证据）
- `soul.ps1` 启动前把旧的 `soul.err.log` / `soul.out.log` 归档为
  `<名>.<上一次会话启动时间>.log`（读 `data/run/*.running` 的 `started_at`；解析失败用当前时间），
  然后才 `Start-Process -RedirectStandardError`（它会清空目标文件）。body 同样处理。
- 启动失败/不存在的文件不归档（空文件没有信息）。

### ④ 退场哨兵（把"死因未知"变成"退出码 N"）—— 已实现（低成本、无进程树风险）
- 新增 `scripts/soul_sentinel.py`：由 `soul.ps1` 在启动成功后拉起，作为**兄弟进程**
  （`OpenProcess(SYNCHRONIZE|QUERY_LIMITED_INFORMATION)` + `WaitForSingleObject` + `GetExitCodeProcess`），
  **不是她的父进程，也不在任何"随父进程一起死"的树里**（走独立 `Start-Process`，无 Job Object）。
- 她退出后写 `data/logs/exit-<ts>-<kind>-<pid>.json`：
  `exit_code` / `exit_code_hex` / `exit_hint` / `started_ts` / `ended_ts` / `lived_s` /
  `crash_logs_since_start` / `verdict_hint`（有 crash log ⇒ 更像自己崩；无 ⇒ 更像被外部杀），
  并尽力往 `watchdog.log` 追加一行。
- 风险控制：哨兵只读退出码；它自己失败只写 `note` 不影响她；找不到脚本只警告不阻断启动。

## 3. 取消测试红证（逐项实测"回退 ⇒ 红 ⇒ 还原 ⇒ 绿"）

| # | 用例 | 回退的写法 | 实测红 |
|---|---|---|---|
| ① | `test_status_reports_reused_pid_as_stale` | 身份校验退化成"只看 PID 存在" | 无关进程被报成"灵魂运行中" ⇒ 红（用户看到的"无法自主重启"） |
| ① | `test_stop_does_not_kill_reused_pid` | 同上 | stop 把**无辜进程**当她在停 ⇒ 红 |
| ① | `test_watchdog_does_not_kill_reused_pid` | 同上 | 看门狗把无辜进程判"僵死"并 kill（日志实测"判定僵死…kill 后重启"）⇒ 红 |
| ② | `test_watchdog_default_interval_is_one_minute` | 默认改回 5 分钟 | 静态断言红（并卡住注册方式必须能长期重复） |
| ③ | `test_start_archives_old_stderr_before_relaunch` | 去掉启动前归档 | 归档文件不存在 ⇒ 红（用假 python.exe 让启动必然失败，绝不真启副本） |
| ④ | `test_exit_sentinel_records_exit_code` / `..._handles_missing_process` | —（新增能力） | 正常绿：杀掉 dummy 后写出 `exit_code`；进程不存在时写明原因 |

## 4. 真机验证（只读，未启停她）

- `soul.ps1 status`：`[ok] 灵魂运行中 PID=18856（命令行确认是她）`、
  `[ok] 身体运行中 PID=15860（命令行确认是她）` ⇒ 身份校验在真机上认得她。
- 看门狗任务注册后实测**两轮连续分钟级运行**：
  `[21:34:01] run-ok ... pid=18856 ... verdict=healthy`
  `[21:35:01] run-ok ... pid=18856 ... verdict=healthy` ⇒ 1 分钟巡视已生效。
- 全程她的 PID **18856 / 15860 未变**，最后一条 soul 心跳 **0.4s 前** ⇒ 没有被我动过。

## 5. 已知限制 / 需要用户或管理员跟进

1. **当前进程（18856/15860）是在 R14 之前启动的**：没有退场哨兵、启动前归档也不会对它生效。
   下次重启后：哨兵会为 she 写 `exit-*.json`；旧 stderr 会被归档。
2. **Windows 审计日志 4688/4689 未开启** ⇒ 即使有了退出码，**仍无法指认"谁杀的"**。
   开启需管理员（`auditpol /set /subcategory:"Process Creation" /success:enable`），建议作为运维项。
3. **看门狗任务仍是 "Interactive only"**（只在该用户登录态运行）；这与 10-17 的 `ERROR_NOT_LOGGED_ON` 同源。
   要"登录前也跑"需管理员把 principal 改成 S4U/SYSTEM。
4. `unknown` 身份状态的选择：进程在但读不到命令行时**拒绝启动**（防分叉）——若以后真遇到，
   需要人工确认后删 `data/run/soul.pid`。这是"安全优先"的取舍。
5. 退场哨兵是**兄弟进程**：若未来改成让哨兵当父进程（或放进 Job Object），
   会有"父死子亡"的连带风险 —— 明确**不这样做**，理由记录在此。
6. 注册实现从 `schtasks /TR`（内嵌引号会导致 `-NoProfile` 被当成参数，实测报
   `Invalid argument/option`）改为 ScheduledTasks 模块；若机器缺少该模块会报错并提示管理员。

## 6. 发现但**未修**（不越界，建议另立卡）

1. **日志里的 `⇒` 会在 GBK 控制台重定向下触发 `UnicodeEncodeError`**：
   实测 21:07:06 的 `soul.err.log`/`body.err.log`（2574 B）就是 logging 自己报的错，
   来源是 R2 的 evidence 字符串"…⇒ 上一次没有走到正常关闭"。
   ⇒ 文件日志（UTF-8）正常，但 console handler 在 GBK 下写入失败，stderr 被污染。
   建议修法（**`src/elysia/core/log.py`**，不在 R14 作用域）：`setup_logging` 里对
   `sys.stdout/stderr` 做 `reconfigure(encoding=..., errors="replace")`，或在建 handler 前替换 stream。
2. `assets/` 与 `.archive/` 的机器相关路径未纳入 R14（R13 只抽象了 4 处运行依赖，素材走 `--src`/env）。
3. 面板的"重启她"仍会走 `soul.ps1` 序列（现在会做身份校验）；面板显示层未展示 `exit-*.json`/归档日志
   （O 窗小卡）。
