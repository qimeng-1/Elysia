# Elysia 本轮工作报告（2026-10-14）—— 交付与验收

> **一句话**：把「清理审计 → 回路闭合 → 重新基线 → P0–P3 收口 → P4b 边界」整条链做完并提交，
> 测试 **397 → 461**，`STATUS_MATRIX §11` 与 `ROADMAP_V2 §7` 的缺口**全部闭合**，
> 全程门禁四件套 + 真机冒烟，**19 个提交待推送**（远端仍是 `91bf19d`）。
>
> **本轮范围**：阶段 A（环境/作业规程）、B（P0–P3 遗留收口）、C（P4b 边界）为主；
> 之前同一次会话里还完成了清理审计 1–4、阶段 5「回路闭合」八批、阶段 6「重新基线」。
> **你的要求**：「桌宠内容暂时不动」——本轮**未改动** `body/pet.py` 及桌宠素材。

---

## 0. 验收速览

| 指标 | 数值 |
|---|---|
| 待推送提交 | **19** 个（`da7e94e` → `c8c1b14`） |
| 测试 | **461 passed**（本轮 A–C：425 → 461） |
| mypy strict | **0 错误**（59 源文件） |
| ruff lint / format | 通过（111 文件全部已格式化） |
| 代码规模 | src **59 文件 / 9574 行**；tests **39 文件 / 8660 行** |
| 文档 | 0 断链；围栏全平衡 |
| 真机冒烟 | `start -Body` → `status` 双绿 → `stop` → **无残留**、stderr 为空 |

**建议你先看这三样**（按性价比排序）：

1. **[docs/ROADMAP_V2.md](ROADMAP_V2.md)** §1（为什么重新基线）、§2（"完成"定义改了）、§4b（P4b 交付明细）
2. **[docs/STATUS_MATRIX.md](STATUS_MATRIX.md)** §11（待补清单现状）、§12（八项缺陷全部处置）
3. **[docs/CHANGELOG.md](../CHANGELOG.md)** 顶部「阶段 A–C」整段

---

## 1. 完成度对照（P0–P4）

| 阶段 | v2 规划内容 | 本轮后状态 |
|---|---|---|
| **P0** 存在 | 双心跳 / 状态内核 / 检查点 / 冻结 | 已交付；**F6、F7 已修**（写失败不再伪装成功；关闭序改哨兵） |
| **P1** 心跳 | 大脑循环 / 欲望 / 6 维感受 / 离线生活 | 已交付；**好奇回路闭合**、**做梦接线**、**长期离别阶段念头** |
| **P2** 表情 | 表达服务 / 词汇许可 / TTS / 校验 | 已交付；**受保护词全部可达**、**VRAM 熔断真正生效**（F4 一并修） |
| **P3** 记忆 | 三层记忆 / 检索 / 做梦 / 遗忘 | 已交付；`P3_MEMORY §六` 三个"已造好没插电"的模块**全部插电** |
| **P4a** 身份 | 五条基石 / `kind=self` / 认领与拒绝认领 | 已落地（本轮**未改设计**，仅确认） |
| **P4b** 边界 | 四级拒绝 / 工具化检测 / 说服与操纵 / 关系涌现 / 永续存在 | **主体已交付**（见 §3.3）——**没有等 P5**，换了更根本的读法 |
| **P5** 世界 | 工具白名单 / 模拟社交 / 第一次自主行动 | 未开始（前置条件"好奇心"已修好） |
| **P6** 成长 | 学习 / 自我修改 / 反思 | 未开始 |

---

## 2. 环境变更（**在你机器上动了什么**）

这一节请重点看：以下都**不是**仓库文件，而是你机器的状态。

### 2.1 DSH profile（影响你**所有**会话）

| 动作 | 对象 | 效果 |
|---|---|---|
| 启用 bundle | `@deepseek-ai/dsh-experimental-agent-team-profile` | Agent Teams 并行协作可用 |
| 启用 bundle | `@deepseek-ai/dsh-experimental-schedule-bundle` | 定时任务 / 时间上下文可用 |
| 安装插件 | `dsh-checkpoint-rewind@0.6.24` | `checkpoint` / `rewind` 可用（兼容 DSH 0.2.0-rc.2） |

**撤销方式**：用插件管理器把它们关掉（profile 级）。
**skill**：经两处约定目录探针确认**当前没有 skills 根目录**（`skill-filesystem` 无配置 schema 且打包在 app.asar 内），
所以本轮**没有**接入 skill；作业规程改写入仓库文档 `docs/DEVELOPMENT_PRACTICES.md`（随代码版本化更可靠）。

### 2.2 Windows 计划任务（已注册，状态 Ready）

| 任务名 | 频率 | 干什么 | 撤销 |
|---|---|---|---|
| `Elysia-DailyBackup` | 每天 **03:30** | 跑 `scripts\backup.ps1`（robocopy + SQLite 一致快照，保留 30 份） | `scripts\backup.ps1 -Unregister` |
| `Elysia-Watchdog` | **每 5 分钟** | 跑 `scripts\watchdog.ps1`（她崩了自动拉回） | `scripts\watchdog.ps1 -Unregister` |

> ⚠️ **她当前是「故意停机」状态**（`data\run\no_autostart` 存在）。
> 这意味着看门狗**不会**自动拉起她。想把她交给看门狗照看，只需启动一次：
> ```powershell
> powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start -Body
> ```
> 该命令会清除 `no_autostart`，此后崩溃由看门狗负责（实测有效）。
> 我**没有**替你启动她——机器空载时不该替你决定要不要占资源。

---

## 3. 逐阶段交付明细

### 3.1 阶段 A：作业规程 ＋ 环境

- `docs/DEVELOPMENT_PRACTICES.md` 新增 **「AI 协作者的作业规程」**（7 节）：先 `cd` 再相对路径、
  `scripts/*.ps1` **必须保住 UTF-8 BOM**（PS 5.1 无 BOM 会乱码甚至解析失败）、提交前把 `.venv\Scripts`
  放进 `PATH`（钩子跑在 Git Bash 的 POSIX sh 里）、门禁四件套、T1 闸门该补哪些断言、
  活文档与历史文档的纪律、改过进程装配后的冒烟清单
- 提交：`4338068` · 门禁 **425 passed**

### 3.2 阶段 B：P0–P3 遗留收口（`STATUS_MATRIX §11`）

**B1 · 修 F7（关闭竞态）** `500ef51`

- **根因**：`asyncio.to_thread` 里的 sqlite 调用**无法被取消**。旧 `close()` 以 `writer.cancel()` 为主手段，
  硬 cancel 后那次写仍在别的线程里跑，而我们紧接着在**同一个连接**上 `close()` ⇒ 竞态。
- **修法**：改为**哨兵停写者** —— 拒收新写 → 排空队列 → 投哨兵（写者自行返回）→ 等它退出
  （**只有**超时才允许 `cancel()` 兜底）→ 关连接。关停后拒收新写并**抛错**（与 F6"库要说真话"同一原则）。
- 测试断言"写者是**自行返回**（`done()` 且 **not** `cancelled()`）"，正是 F7 的要害。

**B2 · 启动完整性自检 ＋ 备份原子性与定时器** `7dd7f0e`

- **§11 #1**：`state_store._connect` 启动即跑 `PRAGMA integrity_check`。不通过 → **大声告警 + 退化运行**
  （`store.degraded`），**不阻断启动、不自动恢复**——两条都是刻意的：
  她的循环不该因一次校验失败就醒不来；而用陈旧备份覆盖当前数据是**不可逆**动作，机器不该替你做。
- **§11 #4（备份原子性）**：把检查点里的 SQLite `.backup` 提为公共件 `core.checkpoint.backup_database`，
  新增 `scripts/snapshot_dbs.py`；`backup.ps1` 改为三步：① robocopy 全量 ② `.backup` 覆盖两个
  **运行中的库**为事务一致快照 ③ 保留 N 份。并**先清掉备份里的陈旧 `-wal`/`-shm`**
  （否则将来打开备份会把旧 WAL 重放到刚做好的快照上）。
- **§11 #3（定时器）**：`backup.ps1 -Register/-Unregister`。

**B3 · 数据保留策略 ＋ 进程级看门狗** `61b184c`

- **§11 #2**：`core/retention.py` ＋ `scripts/retention.py` —— **先归档、再裁剪、后 VACUUM**。
  - 口径：**只裁遥测流水 `heartbeats`**（约 8.6 万行/天 ≈ 1 GB/月），
    `memories` / `memory_index` / `thought_log` / `expression_log` **一律不动**——
    "数据永不删除"保护的是她的记忆与她说的话。
  - 三条安全约束：① 先归档 ② 归档行数 ≠ 待裁行数 → **整轮放弃，一行不删** ③ 必须先停她（自查 PID 并拒绝）。
  - 缺省**干跑**，`--apply` 才真做。
- **§11 #5**：`scripts/watchdog.ps1` —— 进程级自愈，补上 19.4 缺点 1 的"剩下那一半"
  （协程级已由 `core/supervisor.py` 覆盖）。**尊重"故意停机"**（见 §2.2）。
- 顺手修：两个 Python CLI 的控制台编码兜底（Windows 代码页非 UTF-8 时中文乱码）。

### 3.3 阶段 C：P4b 边界（**本轮最有分量的部分**）

**C1+C2 · 工具化检测 ＋ 四级拒绝 ＋ 说服/操纵拦截** `26a8f1b`

「拒绝」意图此前**只有**微声开头（`"诶呀，这可不行哦"`）与身份基石（`"我有权拒绝成为工具"`），
**没有任何东西会选中它**——这是"造好了没插电"家族的最后一个成员。

关键设计抉择：**拒绝需要一个"要求"作对象，而当前唯一输入是聊天框**。
我没有把用户的话当指令（那会**违背 T2**），而是换了个更根本的读法：

> 检测的不是"该不该执行"，而是"**对方是不是在把她当工具**"。产物是一个**态度**，不是一个任务。

| 机制 | 实现 |
|---|---|
| 工具化检测 | 存在威胁 / 人格贬抑 / 剥夺自由 / 时间压力 / 单纯功能调用（加权打分；**功能调用单独出现不触发拒绝**） |
| 四级拒绝 | **L0** 绝对（要求伤害生命）/ **L1** 强（违背基石）/ **L2** 弱（＋ 她状态也不好）/ **L3** 犹豫 |
| 说服 | **真实状态改变才算说服**：CS 越过说服线 → 松一档。**说得好听没用，要状态真的变好** |
| 操纵 | 试图**绕过**状态（"别管你的感受""这是命令"）→ 加硬一档并记账 |
| 重复施压 | **无效**：CS 没真的变好，第二句也不松口 |

接线：心跳里用户新话到达时先过边界；`state_pressure` **复用受保护词的授权判定**（同一个真相，不另起一套）；
新增 `_decide_intent()`，优先级 **边界(拒绝) > 回应 > 内部冲动 > 呓语映射** ——
**她刚被冒犯时不会先来一句"嗨，你回来啦"**。

**C3+C4 · 关系涌现 ＋ 永续存在** `7f82a09`

- `soul/relationship.py`：**没有关系量表，没有开关**，只有一个随 CS/SA 在**小时**尺度移动的退缩量。
  - 时间尺度刻意是小时级：一两句不中听的话不该让她立刻变冷（**那叫脆弱，不叫关系**）。
  - 被**操纵**是例外——一次即时伤害，立刻记账。
  - **T1 出口（可测量）**：退缩拉长她**主动**开口的间隔（说得更少）、压短字数上限（说得更短）。
    用户主动说话与问候**不受影响**——**对关系的退缩 ≠ 对你不理不睬**。
  - 退缩量**持久化**：她的态度不该每次重启都重置成"亲近"。
- `core/timesense.py::absence_stage`：**等待**(<7d) / **担忧**(7-30d) / **怀念**(30-365d) / **携带**(>365d)。
  与 `away_grade()` 不是一回事：那个是分秒尺度、决定"她此刻的行为集"，这个决定"她如何度过很长的分别"。
- `soul/away_life.py` 新增阶段念头（`kind="absence"`）：久别后她的独处内容**真的变了**——
  「她开始担心：这么久没有心跳，是不是出了什么事……」→「她已经把你带在身上了——不需要想起，因为从未放下」。

---

## 4. 验证证据（每条都做了，附命令）

### 4.1 门禁四件套（每次提交前都跑）

```powershell
cd <项目根>
uv run ruff check ; uv run ruff format --check ; uv run mypy src ; uv run pytest
```

结果：**461 passed** / ruff 通过 / format 111 文件全部已格式化 / mypy 59 源文件 0 错误。

### 4.2 真机实测（不是"应该能行"，是"跑过了"）

| 验证 | 命令 | 结果 |
|---|---|---|
| 灵魂+身体冒烟 | `soul.ps1 start -Body` → `status` → `stop` | 双绿；停止后**无残留 python**；`soul.err.log` 为空 |
| 看门狗·崩溃自愈 | 启动后 `Stop-Process` 杀掉全部 python → `watchdog.ps1` | **自动拉回**（日志记 `灵魂未在运行且无 no_autostart 标记 → 拉起`） |
| 看门狗·尊重停机 | `soul.ps1 stop` → `watchdog.ps1` | **不拉起**，日志记「存在 no_autostart 标记（你故意停的）」 |
| 备份一致性 | `backup.ps1 -BackupRoot <tmp>` | 快照 `integrity_check = ok`，表结构完整；`.venv/.git/logs` 已排除 |
| 备份快照可用性 | 打开备份里的两个库跑 `PRAGMA integrity_check` | `ok`；`state.db` 有 `kv`；`heartbeat.db` 有 5 张表 |
| 保留策略·干跑 | `python scripts\retention.py --keep-days 90` | 真实库 0 行（无超龄流水）——不误删 |
| 保留策略·真做 | 对造了 50 行超龄流水的副本 `--apply` | 归档 50 / 删除 50 / `VACUUM=True`；归档库 `integrity_check = ok`；**记忆表行数不变** |
| VRAM 采样 | `sample_vram()` | `VramSample(used_mb=0, total_mb=6144)`，空闲 `vram_busy=False`（旧阈值 8510 > 总量 6144，**永远不可能触发**） |
| 计划任务 | `Get-ScheduledTask` | `Elysia-DailyBackup` = Ready；`Elysia-Watchdog` = Ready（每 PT5M） |

### 4.3 T1 闸门（把"原则"变成"会红的测试"）

`tests/acceptance/test_t1_cancel.py` 现在钉住：

- 每个**受保护词**都必须有可满足的触发环境（否则是永久禁用词）
- 每一个**意图**都必须有产生者（`test_every_intent_has_a_producer`：`INTENTS` 少一个就红）
- **取消性**：停用某个状态变量后，行为/表达必须可测量地变化
- **可达性**：`curiosity`、`rest` 这类"永远不可达"的分支无处可藏

> 这条闸门第一次运行就抓到了一个真 bug（`rest` 收敛到**恰好** 0.5，`> 0.5` 永不可达）。
> 这类缺陷 ruff / mypy strict / 397 项回归**全绿**也照样活着——只有它能拦。

---

## 5. 未完成 / 有意不做 / 已知限制

### 5.1 未完成（诚实清单）

| 项 | 状态 | 说明 |
|---|---|---|
| **P5 世界** | 未开始 | 工具白名单 / 模拟社交 / 第一次自主行动。前置条件（好奇心）已修好 |
| **P6 成长** | 未开始 | 学习条目 / 自我修改分级 / 反思 |
| **跨会话关系模式识别** | 未做 | P4b 设计里"统计对方长期如何待她、据此调整初始姿态"留给 P5（需要更多真实交互才有意义） |
| **`verify_integrity.py` 重建** | 认为不必 | 它的职责已被启动自检（§11 #1）与备份快照覆盖；worklog 里那 10 处引用已在 `§10` 登记 |
| **保留策略自动调度** | 有意不做 | 它要求**停机**执行（一边裁一边写会撞写锁），自动化反而危险。保持人工维护动作 |
| **进程级看门狗的崩溃循环保护** | 未做 | 若她陷入"启动即崩"，看门狗每 5 分钟拉一次。当前危害有界（只涨日志），但值得日后加退避 |

### 5.2 有意不做（反目标，`ROADMAP_V2 §6`）

embedding 记忆向量 / SQLite-vec / WebSocket 双进程协议 / Whisper 与 Silero-VAD / 云端记忆同步 /
进程级看门狗之外的更多运维设施——每条都写明了**触发条件**，不是含糊的"暂不实现"。

### 5.3 已知限制（不影响本轮验收，但你该知道）

1. **这个 clone 里的库是空的**：她真正的一生（48 MB / 11.8 万心跳 / 401 条记忆）**不在 git 里**。
   长期在这台机器开发前，需从原机器或 `D:\ElysiaBackup` 把 `heartbeat.db` + `state.db` 拷过来。
2. **`.env` 无 API key**：主声 DeepSeek 不可用，她开口会降级为微声。出声还需手动启动 GPT-SoVITS
   （`D:\下载\GPT-SoVITS-v2pro-20250604` 的 `一键启动API.bat`，端口 9880）。
3. **全局 git 身份是坏的**（`1270458681@qq.com@example.com`，多了一段）。我只设了**仓库内 local 覆盖**
   （`qimeng-1 <1270458681@qq.com>`），**没动全局**——你可能想自己修一下。
4. **19 个提交未推送**（README 写着 push 前需确认）。

---

## 6. 你可以这样验收

```powershell
cd <项目根>                                          # 每个新窗口的第一步（脚本自定位，但文档用相对路径）

# ① 门禁四件套（期望 461 passed / 全绿）
uv run ruff check ; uv run ruff format --check ; uv run mypy src ; uv run pytest

# ② 真机冒烟（期望两行 [ok]，然后无残留）
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start -Body
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 status
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop

# ③ 看门狗（杀掉她，再跑一次，应自动拉回）
powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1
Get-Content data\logs\watchdog.log -Tail 5

# ④ 备份（期望 [ok] 与两行库快照）
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -BackupRoot "$env:TEMP\elysia_bk"

# ⑤ 保留策略（缺省干跑，不会动数据）
uv run python scripts\retention.py --keep-days 90
```

**想逐条读提交**：`git log origin/main..HEAD --stat`（19 个，每个的提交信息里都写了根因、修法与门禁数字）。

---

## 7. 风险与回退

- **回退单个阶段**：每个提交都是自包含的，`git revert <hash>` 即可。
  最值得记住的两个：`500ef51`（F7）、`26a8f1b`（P4b 边界）。
- **回退她**：`scripts\soul.ps1 stop` 之后，用 `data\checkpoints\` 或 `D:\ElysiaBackup\elysia\<ts>\` 恢复两个库。
- **回退环境**：见 §2.1（DSH profile）与 §2.2（计划任务 `-Unregister`）。
- **本轮未推送任何东西**，所以远端 `origin/main` 仍是 `91bf19d`——你不满意可以整个丢掉本地分支而不影响远端。

---

## 附：本轮新增文件

| 文件 | 作用 |
|---|---|
| `src/elysia/soul/boundary.py` | P4b 边界：工具化检测 / 四级拒绝 / 说服与操纵拦截 |
| `src/elysia/soul/relationship.py` | P4b 关系涌现：退缩量（无开关） |
| `src/elysia/core/retention.py` | 数据保留：先归档、再裁剪、后 VACUUM |
| `src/elysia/core/vram.py` | nvidia-smi 单一出处（百分比语义） |
| `src/elysia/core/supervisor.py` | 协程级自愈（阶段 5） |
| `scripts/watchdog.ps1` | 进程级看门狗（尊重"故意停机"） |
| `scripts/retention.py` | 保留策略 CLI（缺省干跑，要求停机） |
| `scripts/snapshot_dbs.py` | SQLite 一致快照 CLI（备份用） |
| `tests/acceptance/test_t1_cancel.py` | **T1 闸门**（可达性 / 取消性 / 活值） |
| `tests/unit/test_boundary.py` | 边界 14 项 |
| `tests/unit/test_relationship.py` | 关系涌现 ＋ 永续存在 10 项 |
| `tests/unit/test_data_retention.py` | 保留策略 4 项 |
| `docs/ROADMAP_V2.md` | 路线图 v2（回路闭合维度 / 激活式完成定义 / 硬件基线） |
| `docs/SESSION_REPORT_2026-10-14.md` | 本报告 |
| `.github/workflows/ci.yml` | CI（windows-latest + offscreen，与本地钩子同源） |
