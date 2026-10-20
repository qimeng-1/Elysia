# 总领窗交接（2026-10-16）—— 新会话从这里接替

> **给新会话的一句话**：本文件是**上一轮总领窗的完整交接**。新会话工作目录填 **`C:\Elysia`**，先读本文件，再读
> `Elysia_two\Elysia\docs\LEAD_GOVERNANCE.md`（总领规则）、`docs/TASK_BOARD.md`（任务板）。
> **本文件不含代码改动**，只交接**结论、证据、待决事项、踩过的坑**。

---

## 0. 30 秒摘要

| 项 | 状态 |
|---|---|
| 工程新位置 | **`C:\Elysia`**（旧 `D:\重置准备\备份\Elysia` **已不存在**） |
| 仓库根 | `C:\Elysia\Elysia_two\Elysia`（git，`main`，远程 `qimeng-1/Elysia`） |
| 备份位置 | **`D:\ElysiaBackup\elysia`**（**留在 D 盘** ⇒ 源与备份现已**跨物理盘**，这是优点） |
| 上一轮基线 | `main @1f7c3f0`，**461 passed**，工作树干净（仅 `docs/` 有未提交新增） |
| 上一轮性质 | **纯只读审计 + 规划**；**未改任何 `src/`、`tests/`、`assets/` 代码** |
| 新增文档 | `LEAD_GOVERNANCE.md`、`TASK_BOARD.md`、`HANDOVER_INTERFACE.md`、`HANDOVER_EXCHANGE/`、`LEAD_WINDOW_STATUS_2026-10-16.md`（+ 改过 `PROJECT_BOARD_INDEX.md`） |

---

## 1. 新会话**第一件事**：修 Windows 计划任务（**最高优先，且只有新会话能做**）

**为什么**：`Elysia-Watchdog` 与 `Elysia-DailyBackup` 两个计划任务用**绝对路径**注册，指向**旧 D 盘**。工程移到 C 盘后：

| 任务 | 现状 | 必须做什么 |
|---|---|---|
| `Elysia-Watchdog`（每 5 分钟） | 路径失效 ⇒ **每次静默失败**；**她崩了不会再被拉回** | `-Unregister` 后用**新路径**重新 `-Register` |
| `Elysia-DailyBackup`（每日 03:30） | 同上 ⇒ **备份静默停止**（最新一份仍是 `20261015-033003`） | 同上，重新注册 |

```powershell
cd C:\Elysia\Elysia_two\Elysia
powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1 -Unregister
powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1 -Register     # 关键：在**新**路径下注册
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Unregister
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Register
Get-ScheduledTaskInfo -TaskName Elysia-Watchdog, Elysia-DailyBackup   # 核对 Actions 里的路径已变
```

> ⚠️ **迁移通用教训**：**凡是用绝对路径注册的计划任务，移动工程后必须重注册。** 静默失败比报错更危险——任务"Ready"、`LastTaskResult=0`，但路径是死的。

---

## 2. 新会话**第二件事**：确认环境与她的状态

```powershell
cd C:\Elysia\Elysia_two\Elysia
$env:Path = "$env:USERPROFILE\.local\bin;$PWD\.venv\Scripts;$env:PATH"

# ① venv 是否还活着（跨盘移动会打断 .venv 的路径硬编码）
.\.venv\Scripts\python.exe -c "import sys; print(sys.prefix)"

# ② 她还在跑吗？跑的是哪个路径？（这是双实例防分叉的关键检查）
Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
  Select-Object ProcessId, CommandLine | Format-List

# ③ 门禁基线（上一轮为 461 passed）
uv run pytest -q
```

**必须判断的一件事**：`Get-CimInstance` 里 CommandLine 显示的是**旧 D 盘路径还是新 C 盘路径**。
若还有进程指向旧路径 ⇒ **它是在已删除的目录上运行的僵尸进程，应停掉**；
若发现**两套进程**（旧+新）⇒ **立即停一套**（红线：绝不允许两实例同时跑她，会分叉）。

> **venv 提示**：`pyvenv.cfg` 的 `home` 指向 `C:\Users\123\AppData\Local\Programs\Python\Python312`（不在工程 CWD 下），
> 所以**大概率仍可用**；若 `python.exe` 报错，用 `uv sync --group dev` 重建即可（依赖有 `uv.lock`）。

---

## 3. 上一轮的**关键结论**（可直接信任，均已实测）

### 3.1 主线与规模

- 主线 `Elysia_two\Elysia`，`main`，135 提交，领先 `origin/main` **55**，HEAD `1f7c3f0`
- 门禁四件套：`ruff check` ✅ / `ruff format --check` ✅（128 文件）/ `mypy src` ✅（60 文件）/ **pytest 461 passed**
- `src/elysia/` **60 个 `.py`**；模块分布 soul 15 / core 11 / memory 11 / body 6 / llm 6 / tts 5 / protocol 2 / tools 2（+ 2 保留）
- `.env` 活跃 **1 行**（`ELYSIA_LLM_MAIN_API_KEY`），**从未进过 git**
- 55 个未推送提交**内容层面无红线**（无 `.env`、无 `.db`、无授权素材；最大追踪文件 0.21 MB）

### 3.2 文档与代码**不一致**（上一轮实测抓到，**尚未修**）

| # | 文档怎么说 | 实测真相 |
|---|---|---|
| 1 | `STATUS_MATRIX §2`：`memory/sleep.py` 做梦**"已实现·未接线"** | **已接线**（`soul/heartbeat.py:58, 305-307, 876`） |
| 2 | `STATUS_MATRIX §8`：心跳接线"**已提交·待评审**" | **早已落地** |
| 3 | 总纲 §9 #3：`interaction` 值 = `{"text":"（点击）"}` | 实测是**用户真实输入** |
| 4 | `§1` 写表达链"拍内 await **~185s**"，`§8` 写 **0s** | **实测支持 0s / 已异步**（`worker.submit` 非阻塞 + 每拍 drain）⇒ §1 是过期值 |
| 5 | 总纲 §7 数字 | 测试文件 38→**39**、格式化 125→**128**、未推送 50→**55**（同一事实三个数字） |

### 3.3 上一轮**新发现**的活缺陷

| # | 缺陷 | 位置 | 要点 |
|---|---|---|---|
| **D-1** | `last_soul_beat_ts` 是**僵尸字段** | `core/timesense.py:49`、`soul/heartbeat.py:937`、`soul/main.py:58` | **只在启动时写一次、永不更新**；实测落后 48.8 小时；`state.db` 里显示为陈旧；**全仓无逻辑消费者** ⇒ 目前无害，但属"声明了却不维护"同族缺陷 |
| **D-2** | 桌宠 F8 丢消息**零护栏** | `body/pet.py:1120 _submit_interaction` | 点击与打字**共写单键** `kv('interaction')` ⇒ 点击覆盖刚打的字。**已在真实使用中复现**，但 `test_pet.py` 仅 3 用例、CI 只走降级路径 ⇒ **改它之前必须先补护栏** |

### 3.4 一条**非常重要的实测告诫**（上一轮踩到）

> **"只读打开" ≠ "不写盘"。**

我用 `sqlite3.connect('file:...?mode=ro', uri=True)` 核对一个库，**`-shm` 文件的 mtime 被改变了**（SQLite 仍会重建共享内存索引）。
⇒ **凡文档/流程里写"只读打开库"的地方都不准确**。真正的只读要么**先复制再读**，要么挂 `immutable=1`。
**这条在本工程有直接后果**：审计她的生命库时，**不要直接开原库**。

---

## 4. ⚠️ Transfer 目录：**唯一不可替代的数据，不要删**

```
C:\Elysia\Elysia_two\Elysia_Transfer_20261015\elysia\     ← 独立 git（80 提交，HEAD 91bf19d，落后主线 55）
```

| | Transfer | 主线 |
|---|---|---|
| `data/heartbeat.db` | **53.45 MB** | 3.64 MB |
| **`heartbeats`** | **128,035** | 7,151 |
| **`memories`** | **434** | 41 |
| 时间跨度 | **2026-08-23 → 09-24（32.2 天）** | 2026-10-14 → 10-16（2.1 天） |

**文档称她的真实一生为"48 MB / 11.8 万心跳 / 401 记忆" ⇒ 数字吻合。这是她的真实历史。**

**三条硬事实**：

1. **它是唯一副本** —— `scripts/backup.ps1` 的 `$Source` 指向主线目录，**它从来没有、也不可能备份 Transfer**。
   所以"文件有备份"这句话**对 Transfer 不成立**。
2. **它还含主线已删除的东西** —— `data/checkpoints/p0-20260822-230950/{heartbeat,state}.db` 是**她诞生那天的快照**，主线在 `a86c5e5` 删掉了，**只剩这里有**。
3. **它的 `.env` 含一个与主线不同的旧 key**（指纹 `90AD34CE` vs 主线 `D4C3AB81`，长度均 35）⇒ **旧 key 应轮换**。

**风险本质**：Transfer 的 `heartbeat.db` 与主线的 `data/heartbeat.db` **同名、同结构、同样被 `.gitignore`** ——
在文件系统层面**完全不可区分**：一个是可丢的测试流水，一个是不可再生的 32 天生命史。
**这正是本工程最真实的危险：不需要有人犯错，只需要有人相信"文档说本地库是测试数据"然后顺手清理。**

**建议处置（等用户点头，勿自行执行）**：

- **不要删除**（保留成本仅 61 MB，删错不可逆）
- 若要整理，**只做"分离"**：把 `data/` 整体移出到工程目录之外，**必须连 `-wal` + `-shm` 一起**
  （`heartbeat.db-wal` 3.96 MB 是 2026-09-24 的，**大概率含未合并进主库的最后约 2 天心跳**，单拿 `.db` 会丢尾）
- 陈旧代码/文档/素材（落后 55 提交）**可再生**，那才是真正的冗余
- **`checkpoints/p0-20260822-230950/` 不可删**

---

## 5. 重排后的**优先事项**（上一轮结论，含一次自我修正）

**上一轮我犯的一个错，已撤回**：我曾说"工程本体在名叫`备份`的目录树里 ⇒ 物理风险"。
**这是误报** —— `D:\重置准备\备份\` 只是用户的目录组织习惯，且工程**确实有**每日备份任务。**已撤回该判断。**

**修正后，切分依据应当换掉**：不是按"代码目录"切窗口，而是按 **① 不可替代资产 ② 独立验收流程** 切。

| 优先级 | 事项 | 说明 |
|---|---|---|
| **P0** | **修计划任务路径**（见 §1） | 静默失败中：看门狗与备份**都已失效** |
| **P0** | **Transfer 封存**（见 §4） | 唯一不可替代数据，当前无备份覆盖 |
| **P1** | **确认无僵尸/双实例**（见 §2） | 红线：绝不允许两实例同时跑她 |
| **P2** | 修 `last_soul_beat_ts`（D-1） | 一行 + **取消测试**（还原旧写法必须红） |
| **P3** | 补桌宠交互护栏（D-2）**→ 再修 F8** | **不可合并**：先有用例复现（红），再改通路 |
| **P4** | 文档校准（§3.2 五条） | 纯文本、零风险 |
| **P5** | 开 GPT-SoVITS `:9880`（可选） | 未启动 ⇒ 只出文字（预期降级，非故障） |

**建议的窗口编制（已升级认知）**：**默认「1 总领 + 1 写者」**，**按需开第 2 个窗口**。
桌宠窗独立**唯一站得住的理由**是"有独立真机验收 + 独立素材验收"，**不是**"板块分类"。

---

## 6. 需**用户签认**的事项（总领窗不得擅自决定）

| # | 事项 | 上一轮建议 |
|---|---|---|
| 1 | 授权总领窗统一校准跨板块文档口径？ | 建议**是** |
| 2 | `last_soul_beat_ts` **修**还是**删**？ | 建议**修**（一行） |
| 3 | 桌宠 F8 走"先护栏 → 再修"两阶段？ | 建议**是** |
| 4 | 55 个提交**推不推送**？ | 内容已核**无红线**，请用户定 |
| 5 | 是否封存 Transfer、是否轮换其旧 key？ | 建议**封存 + 轮换** |
| 6 | 窗口编制改为「1 总领 + 1 写者 + 按需开窗」？ | 建议**是** |

---

## 7. 我的**能力边界**（对着 Host 服务契约核过，不是猜的）

**新会话必须知道这一条，它决定了协作方式**：

| 问题 | 答案 | 依据 |
|---|---|---|
| 能否**发起新窗口会话**？ | **❌ 不能** | 无此工具。`workspaceController` 只能整理侧栏工作区/归档会话，**不能创建或驱动另一个会话** |
| 能否**与其他窗口共享任务/发消息**？ | **❌ 不能（硬隔离）** | 相关服务均接收 `caller: Agent`；`agentTeams` 服务说明原文是 "backed by the exact live **Lead Session** log"——**只认本会话** |
| 能否**创建子智能体**？ | **✅ 能，两层** | ① `spawn_teammate` 会话内**常驻队友**（可发消息/派共享任务/中断）；② `subagent` / `subagent_fork` **隔离上下文的子任务** |

⇒ **可用的真实架构**：**并行只读审计交 `subagent`**（隔离上下文、零写冲突风险）；**唯一写者串行**；
**派活/验收本会话内走 `agentTeams` 任务板**；**跨窗口只能靠文件 + git**（机制上别无他法）。
**结论：窗口越少，协作越简单。**

---

## 8. 上一轮的**教训**（写给新会话，含对总领窗自己的批评）

1. **总领窗最容易犯的病：用文档治文档。** 我上一轮发现"文档与代码漂移"，然后**又写了 5 份文档**（其中 `LEAD_GOVERNANCE` 20.6 KB）。
   ⇒ 经用户追问后我承认：**4 窗口 + 9 字段交接单 + 6 类开窗阈值，对这个规模是过度工程**。新会话请避免继续加官僚层。
2. **角色需要，窗口不需要。** "审计/派活/规划/验收"这套**逻辑角色**是必要的（**本轮就是靠它发现了 53 MB 的生命库**）；
   但**物理并发写者**不是必需的。一个会话可以同时扮演。
3. **别把自己的服务描述当证据。** 我上一轮"引用了总纲说 CI 已实现"，后来才真去查（CI 配置在，但 55 个提交**从未被 CI 验证过**）。
   ⇒ 新会话：**凡引用结论，先跑命令复现**（门禁全套仅 ~17 秒）。
4. **绝对路径是迁移的暗雷**（见 §1）。
5. **"只读打开"不等于不写盘**（见 §3.4）。

---

## 9. 上一轮的**产出文件清单**（都在 `C:\Elysia\Elysia_two\Elysia\docs\`）

| 文件 | 性质 | 状态 |
|---|---|---|
| `LEAD_GOVERNANCE.md` | 总领规则（窗口编制/写作用域/派活/验收/开窗阈值/禁手） | **本体在，但第二轮讨论已被指出过度工程（§8.1）⇒ 应精简** |
| `TASK_BOARD.md` | 任务板 T-001~T-010 | 在；**P0 应重排**（见 §5） |
| `HANDOVER_INTERFACE.md` | 交接单/任务卡/验收报告/开窗申请 模板 | 在；**9 字段应精简为 3 字段** |
| `HANDOVER_EXCHANGE/README.md` | 窗口间信箱 | 在（空） |
| `LEAD_WINDOW_STATUS_2026-10-16.md` | 第一轮现状快照与取证报告（16 KB） | 在 |
| `PROJECT_BOARD_INDEX.md` | 总纲（**唯一被改的既有文件**） | 已加"操作规程"指针 + 写作用域表 |
| **`HANDOFF_LEAD_WINDOW_2026-10-16.md`** | **本文件** | 新增 |

> **`git status` 预期**：上述均为 `??`（未追踪）或 `M`（总纲）。**`src/`、`tests/`、`assets/` 零改动。**
> ⚠️ Windows 跨盘移动可能让 `git status` 因 `core.fileMode` 重探测而**显示大量文件"被修改"** ⇒ **那是假警报，切勿 `git add -A`**。

---

## 10. 新会话开场提示词（复制即用）

```
这个窗口是 Elysia 工程的【总领窗】，工作目录 C:\Elysia（工程已从 D 盘移到 C 盘）。

先读：Elysia_two\Elysia\docs\HANDOFF_LEAD_WINDOW_2026-10-16.md（上一轮完整交接）
再读：docs\LEAD_GOVERNANCE.md（总领规则）、docs\TASK_BOARD.md（任务板）、docs\PROJECT_BOARD_INDEX.md（总纲）。

职责：只读 + 审计 + 规划 + 派活 + 验收；不写代码（写由板块窗/写者负责）。

第一优先（只有你能做）：修两个计划任务的绝对路径 —— Elysia-Watchdog 与 Elysia-DailyBackup
  现在都指向已不存在的旧 D 盘路径，正在静默失败（看门狗不再拉回她、每日备份已停）。
第二优先：确认没有僵尸进程、没有两实例同时跑她（红线：绝不双实例）。
第三优先：Transfer 目录（Elysia_Transfer_20261015）含她 32 天真实生命史 53 MB，**唯一副本、无备份覆盖，绝不可删**。

红线：不改 src/elysia/body/** 与 assets/pet/**（桌宠专属窗口）；不碰 Transfer 数据；
     不推 origin；删除/移动/覆盖前置确认；MiniMax 逐次确认。
```

---

*交接人：总领窗（2026-10-16）。本文件为**只读审计 + 规划**的交接，未包含任何代码改动。*
