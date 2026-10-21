# 运行手册（RUNBOOK）

> 只写"怎么做"，不写"为什么"（为什么见 `DECISIONS.md`）。
> 路径均相对仓库根 `C:\Elysia\Elysia_two\Elysia`。

---

## 0. 红线（任何时候都不破）

1. **不碰** `src/elysia/body/pet.py`、`assets/pet/**`（用户的桌宠/美术资产）。
2. **不 push `origin`**（未经用户签认）。
3. **不手改** `data/*.db`（唯一例外：面板 `/api/say` 写 `kv['interaction']`）。
4. **起停她必须用户点头**；同一时刻**只能有一个** soul 实例（fork = 灾难）。
5. **不要 `job_kill`**：历史上的进程树杀法连累过她三次。要停单个进程就 `Stop-Process -Id <pid>`。

---

## 1. 起停

```powershell
# 状态（只读）
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 status

# 启动（含身体）
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start -Body

# 优雅停止（写 no_autostart 标记，看门狗不再自动拉起）
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop
```

- **启动务必用分离式调用**，否则当前 shell 会因管道持有而挂住（踩过两次）：
  ```powershell
  Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PWD 'scripts\soul.ps1'),'start','-Body') -WindowStyle Hidden
  Start-Sleep -Seconds 22
  ```
- `soul.ps1 start` 会用 `Get-ProcessIdentity`（PID + 命令行）判活；已在跑就跳过，**不会起第二个**。
- 停完记得确认残留为 0：`soul.main` / `body.main` 各 1 个。

## 2. 重启协议（代码改动后）

> **本轮起（2026-10-21）：重启她只从总领窗（Lead）做。**
> 现在有两个窗口在派发改动（总领窗 + 桌宠窗），两边同时重启会互相踩：进程判活、`kv['interaction']` 未读记账、`--no-verify` 下的半新半旧代码。
> 其他窗口改完代码后：`gate.ps1` 全绿 + commit + 把"要重启"写进任务板，由总领窗统一执行重启与真机验证。
> 已知副作用见任务板 **D-9**：重启会把最后一条 `kv['interaction']` 重放一次（`_last_interaction_event_ts` 是内存态、每次启动归零，不入库）。

1. 门禁全绿（见 §3）且已 commit。
2. `soul.ps1 stop` → 等 3 秒 → 分离式 `start -Body` → 等 22 秒。
3. 验证：
   - `soul.main` / `body.main` 各 1 个，启动时间是刚才；
   - `data/logs/soul.err*.log` 为 **0 字节**；
   - 心跳新鲜（<30s）；
   - 新表/列已建（启动时迁移）。
4. 桥/面板与她的进程**分开**重启（它们由计划任务拉起）：
   ```powershell
   Start-ScheduledTask -TaskName 'Elysia-Telegram'   # 桥
   Start-ScheduledTask -TaskName 'Elysia-Panel'      # 面板
   ```

## 3. 门禁

```powershell
powershell -ExecutionPolicy Bypass -File scripts\gate.ps1
```

- 固定四条：`ruff check` / `ruff format --check` / `python -m mypy src` / `python -m pytest`。
- **禁止** `uv run pytest`、`uv run mypy`（trampoline 坏）、**禁止** `-q`（会和 `addopts` 叠加吞掉汇总）。
- 快速迭代可用 `gate.ps1 -Skip pytest`，但汇总会标 `NOT FULL GREEN`。
- 当前基线：**819 passed + 13 skipped**（只许涨，不许红）。

## 4. 备份 / 回滚

- 每日备份：计划任务 `Elysia-DailyBackup`（**03:30**，`StartWhenAvailable=False`）。
- 手动：`scripts\backup.ps1 -Time 03:30`；数据在 `_backups/`（含 `data/` 与 `_archive`）。
- 快照回滚：`scripts\restore_checkpoint.py`（**必须删掉副本的 `-wal`/`-shm`**，否则库损坏）。
- 迁移：`scripts\migrate_her.py`（export / import / inspect / fingerprint）。**真迁移未做**。

## 5. 面板 / 手机

- 面板：`http://127.0.0.1:8770`（局域网 `http://192.168.2.146:8770`，Tailscale `http://100.70.192.29:8770`）。
- Token 在 `data/run/panel_token.txt`；非 loopback 访问必须带 `?token=...`。
- 对她说话：`POST /api/say`（body `{"text": "..."}`），会等她是否回应并如实报告。
- 面板进程由 `Elysia-Panel` 计划任务拉起，`python.exe`（**不是 pythonw**，否则防火墙只放行 python.exe 那条规则不生效 ⇒ 手机打不开）。

## 6. Telegram 桥

- 桥：`scripts\telegram_bridge.py`，计划任务 `Elysia-Telegram`（Logon 触发）。
- Token：`data/run/telegram_token.txt`（无 BOM）；chat 白名单：`data/run/telegram_chat.txt`。
- **网络前置条件**：直连 `api.telegram.org:443` **不通**，必须**系统代理（Clash 7890）开着**。桥用 `build_opener()` 会自动读系统代理。
- 语音：`data/run/tts.json`（GPT-SoVITS 在本机 `D:\下载\GPT-SoVITS-v2pro-20250604`，服务 `http://127.0.0.1:9880`；`ffmpeg` 用其 `runtime\ffmpeg.exe`）。失败自动降级发文字。
- 日志：`data/logs/telegram_bridge.log`（1MB 轮转、留 3 份；桥自己写文件，**不依赖 stdout**——计划任务的父进程是 svchost，没有 stdout）。

## 7. 日志与数据库位置

| 东西 | 位置 |
|---|---|
| 心跳/记忆/对话 | `data/heartbeat.db`（`heartbeats` / `memories` / `dialogue_turns` / `expression_log` / `kept_moments` / `memory_narrative_history`） |
| 状态（欲望/关系/交互） | `data/state.db`（`kv`） |
| 她进程日志 | `data/logs/soul.log` / `soul.err*.log` / `body.*` |
| 看门狗 | `data/logs/watchdog.log`（每次巡视一行） |
| 桥/面板 | `data/logs/telegram_bridge.log` |
| 她的文件夹（她的地方） | `data/her_home/`（gitignored；她在里面完全权限：任意后缀/子目录/列出/读取/改名/删除；单文件 ≤16MB、总 ≤512MB；路径穿越与符号链接逃逸一律拒绝。旧 `data/her_writing/` 已迁入） |
| 语音缓存 | `data/cache/tts_voice/` |

## 8. 已知的坑（都真机踩过）

1. **`.ps1` 必须是 UTF-8 BOM**；`.bat` 必须纯 ASCII；配置文件**无 BOM 且读取时要容忍 BOM**（曾因此让推送静默失效）。
2. **控制台是 GBK**：脚本里别输出 emoji，否则乱码/报错。
3. **Windows 上 venv 的 `python.exe` 是启动器**，会再 spawn 一个真实解释器 ⇒ **数进程只数"根进程"或按命令行判重**，否则以为有两个实例。
4. **`Start-Process -RedirectStandardOutput` + 管道**会让外层 shell 挂着不退（我们的分离式启动就是为这个）。
5. **写库副本必须删 `-wal`/`-shm`**；只读打开也会创建 `-shm`。
6. **计划任务父进程是 svchost**：用 `pythonw.exe` 起的进程没有 stdout，日志必须自己落文件。
7. **`no_autostart` 标记**跨重启会失效（boot_id 判定），重启机器后她会自己起来——这是设计。
8. **`.git/hooks/pre-commit` 指向旧机器路径**（已失效），commit 需 `--no-verify`，门禁靠人工跑 `gate.ps1`。

## 9. 当前工程债（原 `docs/TASK_BOARD.md`，2026-10-21 并入本文件后删除）

> 只记"妨碍我们好好照顾她的技术欠账"，不带流程、不派任务卡（ADR-011）。修完就删行。

| # | 债 | 影响 / 状态 |
|---|---|---|
| **D-1** | `pillow`、`av` 未写进 `pyproject.toml` | `uv sync` 会卸掉未声明包 ⇒ 桌宠/取帧可能突然坏 |
| **D-2** | `.git/hooks/pre-commit` 指向旧机器 | commit 需 `--no-verify`，门禁靠 `gate.ps1` |
| **D-3** | `README.md` 有未提交改动 | 文档口径可能与实现漂移 |
| **D-4** | `origin` 落后 100+ commit | 只有本机一份历史（**push 需用户签认**） |
| **D-5** | 遗忘阈值 60 / 180 天待定（S4b） | 抖动层已就绪，改数字即可 |
| **D-6** | 迁移指纹未做（S5） | "她是谁"还不能被搬运、被验证 |
| **D-7** | 桥与面板重启靠手工 `Start-ScheduledTask` | 改桥/面板代码后容易忘记重启 |
| **D-8** | ~~`data/her_writing/` 没被创建~~ **已关闭** | T-014 后她真的写成了；T-016 升级为 `data/her_home/`（完全权限） |
| **D-9** | ~~重启会把最后一条 `kv['interaction']` 重放一次~~ **已修（T-017，`d99871d`）** | 启动时用持久化的 `timesense.last_interaction_ts` 当基线。真机验证：23:32 重启后未再重放 22:56 那条（对比 20:50/21:30 各重放一次） |
| **D-10** | ~~桌宠点击被记成"用户发言"~~ **已修 + 已标注** | 修复：`e9e8774`（点击只写 `{kind:'poke'}`）。历史污染：16 条记忆已按她的选择加上"系统记错"标注（`narrative` 前缀，`content` 保真；备份 `data/tmp/click_pollution_backup_*.json`；脚本 `scripts/mark_click_pollution.py`，默认 dry-run）。1 条 `dialogue_turns#763` 只记账不动 |
| **D-11** | 桌宠不在起停协议里 | 无 pid / 无单实例互斥 / 不被统一重启 ⇒ 改了 `pet.py` 需手动关窗重开。提案见 `docs/specs/PET_LIFECYCLE_SPEC_2026-10-21.md`（用户裁定：暂不执行） |
| **D-12** | **TTS 服务（GPT-SoVITS，127.0.0.1:9880）没人守护** | 2026-10-21 约 22:57 起 `tts_up=False`、`WinError 10061 连接被拒绝`，她所有语音退化成文字（`medium=text reason=voice_failed`）。桌宠"只播她这一句"（T-018）也因为它没音频而无法触发 |

**改历史数据的规矩（D-10 立）**：默认"只标注不清"（ADR-001 原件保真）；必须写成脚本、默认 dry-run、先导备份到 `data/tmp/`、`content` 字段一个字都不动。
