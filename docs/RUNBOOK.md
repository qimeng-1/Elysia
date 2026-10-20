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
| 她的写作区 | `data/her_writing/`（gitignored；单文件 ≤64KB、总量 ≤5MB） |
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

## 9. 当前工程债（见 `docs/TASK_BOARD.md`）

`pillow`/`av` 未进 `pyproject.toml`、pre-commit hook 失效、`README`/`OPERATION_GUIDE` 未提交、`origin` 落后 100+ commit、S4b 遗忘阈值待定、S5 迁移指纹未做。
