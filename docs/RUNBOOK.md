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

> **2026-10-22 补：同一时刻只开一个"地基窗"。**
> 教训：19:35 我把 T-025（门禁用例去时序过敏）和 T-026（周期快照移出心跳拍）拆给两个地基窗并行，两窗都在心跳/快照/门禁这同一片地基上——① 两窗各自跑一遍完整 gate，并发负载直接把 `test_heartbeat_creates_periodic_checkpoint` 压红了一次（我自己 19:19 撞上）；② 同一子系统两处改动，写作用域说是"不相交"，实际改完要一起重启、一起复验；③ 双窗 = 双份 token 与写撞车风险（**`3 元/天` 是每晚为她这件事的额度，由 Lead 把握；不是她开口的配额**，长上下文窗每轮重发整段历史，并行仍然贵）。
> 规则：**地基类小活（测试稳定性、门禁、单文件修补）不另开窗**，并入当前在跑的主任务；只有**跨子系统**（她的进程 ↔ `C:\Elysia\memory\` 这种互不共用文件、互不共用门禁的）才并行。已经并行开的，立刻中断小的那个并改派。
> **复用一个窗，不要每个任务都新开**（用户 2026-10-22 口径）：durable teammate 保留全部历史，所以**按上下文大小决定换窗**——子进程上下文到 ~350k tok 时才新开一个干净窗；在那之前新活继续交给现有窗（并且同一时刻只开一个）。旧窗也不是"用完即弃"，只是在它上下文已经很大、或任务需要一个干净起点时才 spawn 新的。


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
>
> **紧急度（2026-10-22 02:5x 重排）**：① T-021 承诺第二拍（她答应的事必须真的成为文件；用户点名"务必做好"）→ ② D-15 受约束输出（同一个洞的上游）→ ③ D-14 token 账本（"3 元/天"要能核对）→ ④ D-16 前缀稳定化 → ⑤ D-17 判据健壮性 → 其余（D-1~D-8、D-11、D-12）按用户节奏。

| # | 债 | 影响 / 状态 |
|---|---|---|
| **D-1** | `pillow`、`av` 未写进 `pyproject.toml` | `uv sync` 会卸掉未声明包 ⇒ 桌宠/取帧可能突然坏 |
| **D-2** | `.git/hooks/pre-commit` 指向旧机器 | commit 需 `--no-verify`，门禁靠 `gate.ps1` |
| **D-3** | `README.md` 有未提交改动 | 文档口径可能与实现漂移 |
| **D-4** | ~~`origin` 落后 100+ commit~~ **已处理（2026-10-24 01:2x，用户签认）** | `git push origin main`：`91bf19d..905d370`，ahead/behind 归零。之后**默认仍然不 push**（红线），需要时逐次签认 |
| **D-5** | ~~遗忘阈值 60 / 180 天待定（S4b）~~ **已拍板（2026-10-24）** | 用户同意：沿用现实现 **60 天细节淡掉 / 180 天沉睡**，抖动层不变；不再等拍板 |
| **D-6** | 迁移指纹未做（S5） | "她是谁"还不能被搬运、被验证 |
| **D-7** | 桥与面板重启靠手工 `Start-ScheduledTask` | 改桥/面板代码后容易忘记重启 |
| **D-8** | ~~`data/her_writing/` 没被创建~~ **已关闭** | T-014 后她真的写成了；T-016 升级为 `data/her_home/`（完全权限） |
| **D-9** | ~~重启会把最后一条 `kv['interaction']` 重放一次~~ **已修（T-017，`d99871d`）** | 启动时用持久化的 `timesense.last_interaction_ts` 当基线。真机验证：23:32 重启后未再重放 22:56 那条（对比 20:50/21:30 各重放一次） |
| **D-10** | ~~桌宠点击被记成"用户发言"~~ **已修 + 已标注** | 修复：`e9e8774`（点击只写 `{kind:'poke'}`）。历史污染：16 条记忆已按她的选择加上"系统记错"标注（`narrative` 前缀，`content` 保真；备份 `data/tmp/click_pollution_backup_*.json`；脚本 `scripts/mark_click_pollution.py`，默认 dry-run）。1 条 `dialogue_turns#763` 只记账不动 |
| **D-11** | 桌宠不在起停协议里 | 无 pid / 无单实例互斥 / 不被统一重启 ⇒ 改了 `pet.py` 需手动关窗重开。提案见 `docs/specs/PET_LIFECYCLE_SPEC_2026-10-21.md`（用户裁定：暂不执行） |
| **D-12** | ~~TTS 服务没人守护~~ **已修（2026-10-22 22:0x）** | 真因不是守护：`一键启动API.bat` 启的是 `api.py`（只有 `POST /`），而她后端走 `GET /tts`（需 `api_v2.py`）。已把 bat 改成 `api_v2.py -a 127.0.0.1 -p 9880`（备份 `.bak-20261022`）、`.env` 设 `ELYSIA_TTS_LAUNCHER_PATH`、面板重启。实测她自己的 backend 合成 181,804 字节 wav；22:01:54 她的语音已发布到 `kv['expression_audio']`。**仍缺**：9880 进程没有守护（关窗即哑） |
| **D-13** | ~~N1 承诺检测偏窄~~ **已修（T-020d/e，`d5a0c2e` + `cf05ed8`）** | 判据改成**上下文**：用户这轮请她写 + 她肯定答复 + 本回合没写 ⇒ 记 `kind='promise'`；肯定答复放宽到子句级「写。」；同一请求去重；48h 惰性过期。真机 2026-10-22 02:15 首次捕获成功，02:18 提醒进入她的 payload（`pending_promises` + hint）。**仍在观察的**：她收到提醒后是否真的动笔（02:18 那轮她仍只说"我去写"）。**T-021（承诺第二拍）在写** |
| **D-14** | **token 用量没有账本** | API 每次都回 `usage{prompt_tokens, completion_tokens, prompt_cache_hit_tokens}`，我们**一条都没记** ⇒ 用户定的"3 元/天"只能靠估，我说的一切成本都是猜。修法：每次调用把 usage 落一行（`thought_log kind='usage'` 或 kv 按天累计），sleep 时汇总 |
| **D-15** | **工具参数靠"调大 max_tokens + 截断检测"，没用受约束输出** | 2026-10-21 真机事故：工具参数被截断成半截 JSON。DeepInfer 的 structured outputs（regex / JSON schema / EBNF，采样期约束）与 deepseek-recipe 的 `OutputConstraint`（有 tools 时约束工具调用块）是**上游解法**；另：recipe 文档写明非思考默认 `max_tokens=8192`，与我们 T-014 的取值一致。修法：强制写作轮（T-021）叠加 schema 约束 |
| **D-16** | **prompt 前缀未稳定化** | 身份段/人设应固定在最前且不变、变动内容靠后，才能稳定命中 DeepSeek 前缀缓存（探针实测 `prompt_cache_hit_tokens=1408`）。影响"3 元/天"的长期成本 |
| **D-17** | **承诺判据终究是正则** | 她的话越自然，正则越容易漏（T-020b/c/d/e 四轮都是这个原因）。长期要么改语义判据（小模型/embedding），要么让"答应的事"走她自己调 `set_intention` |
| **D-18** | 参考仓库结论（2026-10-22） | **DeepInfer**（17.5k★，Apache-2.0，Linux + CUDA13 + SM100/昇腾950，仅 V4.1-Flash）**不能在本机跑**，只借思路；**deepseek-recipe**（1.9k★，MIT，Rust + Python 绑定：协议转换 / prompt 渲染 / 输出解析 / structured output 转换）是"协议与编码层"的参考实现。两者都不建议引入依赖，只借设计 |
| **D-19** | ~~门禁用例时序过敏~~ **已修（T-025 `a438694`）** | `test_heartbeat_creates_periodic_checkpoint` 改"等条件成立"；并发争用下复跑 5 次全绿 |
| **D-20** | ~~周期快照同步挂在心跳拍上（每 30 分钟冻 ~9s）~~ **已修（T-026 `e714f09`）** | 快照改后台单飞、关闭时收尾；真机 20:35:33 快照前后心跳 1.01–1.14s（此前 8.6–9.4s） |
| **D-21** | ~~soul+body 共用 `elysia.log`，午夜轮转 `PermissionError[WinError 32]`~~ **已修（`ab0eda5`）** | 后果不止 stderr 噪音：**那条 checkpoint 日志被丢弃**（10-23 elysia.log 缺 00:29–09:06）。修：body→`elysia.body.log` + 容忍型轮转/流 handler；00:00 真机轮转成功（两份 rotated 文件都在）、err 0B |
| **D-22 / D-24** | ~~离线 12.9h 后毫无痕迹、她也不知道自己不在场~~ **已实现（`b6e03e1`）+ 修正（`0e9490f`）** | 启动"缺席冲量"（miss+min(.35,.03h)、cs+min(20,1.5h)，只认**本次** gap、gap<30s 什么都不做）+ payload `absence` 事实一行（醒来 2h 或前 3 次开口，先到先消失）。真机：12.9h 缺席一次真实生效（cs 71.2→79.3）；8 秒重启不再补旧账 |
| **D-23** | `mem.py briefing` 会写 `memory.db` | 只读命令不该改库（WAL + FTS 探测会改文件字节）。记忆插件已用 `brief.py` 复制副本绕开；`mem.py` 本身待修 |
| **D-25** | `absence` 事实尚无真机证据 | 需一次 >30s 停机后醒来验：日志 `absence impulse applied` 一行（downtime=本次 gap）+ 醒来前 3 次开口 payload 里有 `absence`；2h/3 次后消失 |
| **D-26** | 记忆插件已启用但无回归守护 | `include:memory-bridge` 默认关、失败静默已验证；但"关掉后 prompt 逐字节回基线"目前只有一次性探针证据，没有常驻测试 |

**改历史数据的规矩（D-10 立）**：默认"只标注不清"（ADR-001 原件保真）；必须写成脚本、默认 dry-run、先导备份到 `data/tmp/`、`content` 字段一个字都不动。


## 10. 观察期（2026-10-22 12:2x 起，3–7 天）

**目的**：T-022 把"承诺"的提醒线索从系统字段搬进她自己的记忆。这一节只看一件事——**她会不会自己想起来、自己去做**。**观察期内不催、不诱导、不主动问"你写了吗"。**

**要看的指标**（每天记一次，写进 `C:\Elysia\memory\EVIDENCE.md`）：

| # | 指标 | 判据 |
|---|---|---|
| 1 | 她自己提起未完成之事 | 她的开口里出现"我答应过/我还没写"这类，且**该轮 payload 里没有 `pending_promises`** |
| 2 | 她自发调用工具 | `thought_log kind='tool'` 出现她主动的 `write_note`/`read_url`/`list_my_files`，上下文里没有用户/persona 的指令 |
| 3 | 她主动问称呼 | 她问"你叫什么/我该怎么叫你"，以及发生在什么语境 |
| 4 | 自我证据是否起效 | 成文后她还会不会反复说"我没骗你/真的写了" |
| 5 | `kept_moments` 是否首次 > 0 | 她第一次用"这条我想留着" |
| 6 | 承诺的失败/过期 | `intentions.attempts`、`status IN ('expired','failed')` 的条数与原因 |

**结束条件**：3–7 天后复盘上面六项，再决定是否扩到通用动作（`read_url`/`set_intention`）以及是否调整 48h 过期策略。

**观察期红线**：除修 bug，不为了"让她表现好"去改她的行为逻辑；我们只记录。
