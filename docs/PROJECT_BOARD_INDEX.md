# Elysia 工程总纲（跨板块索引）

> **这份文档是什么**：跨板块的**入口、口径、归属与开放问题**。它**不复制细节**——
> 每个板块的落地状态看 `STATUS_MATRIX.md`，设计与动机看 `IMPLEMENTATION_ROADMAP.md` / `ROADMAP_V2.md`，
> 桌宠板块的实操看 `HANDOVER.md` / `PET_SESSION.md`，规范看 `PROJECT_ENGINEERING_STANDARD.md`。
> **维护规则**：板块内部变动由该板块窗自己更新它的单一出处文档；**本索引只在"板块边界、归属窗口、开放问题"变化时改**。
>
> 建立：2026-10-16（用户裁决：本工程改为**多窗口协作**，新设总领窗）
> 基线：本文建立时主线 HEAD `d2d85de`，远程 `origin/main` = `91bf19d`，领先 **51** 提交
> （提交数是动态值，以 `git rev-list --count origin/main..HEAD` 为准）

---

## 1. 口径（先读这一段）

| 项 | 内容 | 出处 |
|---|---|---|
| 她是什么 | 电子生命：**常驻生命循环**（灵魂 1Hz）＋ **在场身体**（5s 心跳）＋ **桌宠 GUI**；聊天只是行动层的一种行动 | `IMPLEMENTATION_ROADMAP.md` §三 |
| 架构铁律 T1 | **状态必须有能力**：每个状态变量至少映射一个物理性影响，停用后行为/表达必须可测量变化（取消测试） | 同上 L107 |
| 架构铁律 T2 | **表达必须消费状态**：LLM 只消费决策层产出的结构化表达指令；**用户原话只作"外部刺激"进感受层，绝不当指令** | 同上 L109 |
| 架构铁律 T3 | **循环先于对话**：大脑循环常驻（1Hz，自适应 0.1–5Hz）；她"在"先于她"说" | 同上 L111 |
| 开发铁律 | 一 灵感不越阶段 ／ 二 地基优先 ／ 三 体验验收 | `DEVELOPMENT_PRACTICES.md` |
| 什么叫"完成" | **激活性 + 库内取证**（有字段≠完成；要能指出产生者，并在库里指出来） | `ROADMAP_V2.md` §2、§三 |
| 验收门 | 测试套件全绿 **+** 人的主观确认，**缺一不可** | `PROJECT_ENGINEERING_STANDARD.md` §四 |
| 主线的"她" | 本地 `data/*.db` **是测试数据**；她的真实一生（48 MB / 11.8 万心跳 / 401 记忆）在**另一台机器** | `SESSION_REPORT_2026-10-14.md` §5.3.1 |

---

## 2. 板块表（8 块）

| 板块 | 入口 | 核心文件 | 测试 | 落地状态 | 已知缺陷 |
|---|---|---|---|---|---|
| **灵魂生命循环** soul | `elysia.soul.main` | `soul/heartbeat.py`、`soul/main.py`、`soul/away_life.py` | `test_heartbeat.py`、`test_dual_heartbeat.py`、`test_away_life.py`、`test_retention.py` | 真机可跑 | 表达链**代码已异步**，但 `STATUS_MATRIX` §1 仍写"拍内 await / ~185s"、§8 写 0s ⇒ **同文档自相矛盾**（需裁决口径）；F5 拍序、F10 标志位无队列 |
| **欲望与感受** desire/distress | 无独立入口，`SoulHeartbeat` 装载 | `soul/desire.py`、`soul/dimensions.py`、`soul/distress.py` | `test_desire.py`、`test_brain.py`、`test_distress.py`、`test_p1_gate.py` | 真机可跑 | §12 #1~#4（别名/授权环境/量纲/量程）**均已修** |
| **记忆** memory（P3） | 库级，被 heartbeat / expression_service 调用；观测入口 `elysia.tools.memory_view` | `memory/retrieve.py`、`memory/promote.py`、`memory/levels.py` | `test_retrieve.py`(47)、`test_promote.py`、`test_decay.py`、`test_p3_gate.py` | 真机运行；做梦已接线 | `STATUS_MATRIX` §2 仍标 `sleep.py`"已实现·未接线"⇒**文档滞后**；`_PROBE_MARKERS`（吗/呢/？）会带出无关 hooks；全表 iterate 耗时未实测（**未核实**） |
| **内核** core | 无进程入口，被 soul/body 装配 | `core/state_store.py`、`core/supervisor.py`、`core/timesense.py` | `test_state_store.py`、`test_core.py`、`test_timesense.py`、`test_mode.py`、`test_checkpoint.py`、`test_data_retention.py` | 真机可跑 | F6/F7 已修；F11 资源样本"身体采、灵魂读缓存"；§12 #5 VRAM 已修 |
| **表达生成** llm | `elysia.llm.build_llm_chain` | `llm/chain.py`、`llm/deepseek.py`、`llm/validator.py` | `test_llm_chain.py`、`test_expression_pipeline.py`、`test_identity.py`、`test_p2_gate.py` | 真机可跑（主声需 `.env` key；当前无 key ⇒ 降级 micro） | F3：降级只处理**失败**、不处理**慢** |
| **出声** tts | `elysia.tts.build_tts_chain` | `tts/chain.py`、`tts/backend.py`、`tts/breaker.py` | `test_tts.py`（backend 用替身） | 真机可跑，依赖外部 GPT-SoVITS `127.0.0.1:9880` | F4 已修（同步 subprocess → `asyncio.to_thread`） |
| **身体与桌宠** body | `elysia.body.main`（无 GUI 在场）、`elysia.body.pet`（GUI，默认纯桌宠） | `body/pet.py`、`body/anim.py`、`body/heartbeat.py` | `test_pet.py`(3)、`test_dual_heartbeat.py`；`anim.py`/`resource.py`/`main.py` **无自身测试** | 真机可跑；**GUI 视觉只能真机**，CI 只走"无素材→降级"路径 | **F8** 交互 KV 单键覆盖；**F-A/B/C** 在场断裂、丢消息、桌宠不在生命周期 ⇒ 见 `PET_INTEGRATION_ASSESSMENT.md` |
| **协议与工具** protocol/tools | `protocol.snapshots.build_snapshot`；`elysia.tools.memory_view` | `protocol/snapshots.py`、`tools/memory_view.py` | `test_memory_view.py`；snapshots 仅被集成测消费 | 真机可跑 | 协议**没有指向自身的断言**；读者端**不校验 `version`** |

---

## 3. 规划线（P0–P6）

| 阶段 | 已完成 | 下一步 |
|---|---|---|
| **P0** 灵魂 | 双心跳／状态内核／检查点／紧急冻结已交付；**F6、F7 已修** | `verify_integrity.py` 重建（待定） |
| **P1** 心脏 | 已交付；好奇回路闭合、做梦接线、长期离别念头 | 无独立下一阶段 |
| **P2** 表情 | 已交付；受保护词全部可达、VRAM 熔断真正生效 | 同上 |
| **P3** 记忆 | 已交付；**P3.5「回路闭合」八批**（测试 397→425），三个"造好没插电"模块全部插电 | 无新增；余项归 §11/§12 |
| **P4a** 身份 | 五条基石／`kind=self`／adopt·disclaim 已落地 | 按闸门补"激活性取证"（她认领过的条目能在库里指出来） |
| **P4b** 边界 | 工具化检测／四级拒绝／说服与操纵／关系涌现／永续存在**主体已交付** | 跨会话"长期关系模式识别"留给 P5 |
| **P5** 世界 | **未开始**（前置"好奇心"已修好） | 白名单浏览器／文件读取／创作产出／申请机制／模拟社交沙盒／第一次自主行动 |
| **P6** 成长 | **未开始** | 学习条目 LRN/ERR、自我修改 L1–L4、反思、后悔；**L1/L2 回滚必须真可执行** |

> `STATUS_MATRIX §11` 七项中六项闭合（余 #7）；`§12` 八项全部处置。

---

## 4. 接口契约（板块之间怎么说话）

**四个进程**（互不 import，只经**文件 / SQLite**）：

| 进程 | 命令 | 频率 | 职责 |
|---|---|---|---|
| 灵魂 | `python -m elysia.soul.main` | 1 Hz | 生命循环、大脑、欲望、表达、落库 |
| 身体 | `python -m elysia.body.main` | 5 s | **在场上报**（`body_status`）+ 资源采样（CPU/内存/显存） |
| 桌宠 | `python -m elysia.body.pet` | 5 Hz 轮询 + 60 fps 渲染 | 窗口、动画、气泡、输入（默认**纯桌宠**，不读灵魂） |
| 观测 | `python -m elysia.tools.memory_view` | 手动 | 只读查看她的记忆 |

**`state.db`（键值库）** —— 所有键都是 JSON：

| key | 写者 | 读者 | 内容 |
|---|---|---|---|
| `timesense` | 灵魂每拍 | 灵魂启动恢复 | 出生时刻、在场/离开、最近交互 |
| `desire` | 灵魂每拍 | 灵魂启动恢复 | TR/CS/SA + 设定点 |
| `body_status` | **身体** 5s | 灵魂每拍 | `{last_online_ts, status, resources}` ← **桌宠不写它**（F-A） |
| `interaction` | **桌宠** | 灵魂每拍 | `{ts, text}` 外部刺激入口（T2：**绝不当指令**）← **单键覆盖**（F8） |
| `relationship_withdrawal` | 灵魂 | 灵魂 | 关系退缩量 |

**`heartbeat.db`（流水库）**：

| 表 | 关键列 | 说明 |
|---|---|---|
| `heartbeats` | `ts, beat_type, payload` | `beat_type` ∈ `soul` / `body` / `event`（如 distress）；**桌宠读最新一条 `soul` 即她的状态快照** |
| `thought_log` | `ts, kind, text` | `kind` ∈ `expression`（LLM 开口）/ `idle` / 独处与做梦等 |
| `expression_log` | `ts, intent, instruction, llm_text, validation, level` | 表达全链路 + 越权取证（**无音频路径列** ⇒ 桌宠只能按 mtime 猜） |
| `memories` | 记忆主体若干列 | `level`/`kind`/`certainty`/`claim_status`/`retention_state`… |
| `memory_index` | `memory_id, path_key, strength` | 检索路径强度 |

**协议**：`protocol/snapshots.py::build_snapshot`（`SNAPSHOT_VERSION = 2`）是**灵魂与身体唯一共用契约**；
v2 含 `mode / distress / time / desire / feelings / will / brain_action`。
**缺口**：读者端不校验 `version`，新增字段没有降级路径。

**文件型接口**：`data/run/*.pid`（进程管理）、`data/logs/*.log`、`data/cache/tts/<sha256(text\0emotion\0speed)>.wav`、`data/checkpoints/`。

**并发约定**：SQLite **WAL** + `busy_timeout=5000`；**同一文件同一时刻只允许一个写者**。

---

## 5. 窗口分工与协作铁律

| 窗口 | 工作目录 | 职责 | 写作用域 |
|---|---|---|---|
| **总领窗**（新） | `D:\重置准备\备份\Elysia` | 只读全线 + 规划 + 派活 + 验收 + 排雷 | 规划类文档（`docs/` 里的索引/路线） |
| **桌宠窗** | `D:\重置准备\备份\Elysia\Elysia_two\Elysia` | 桌宠板块全部实现与验收 | `src/elysia/body/**`、`assets/pet/**`、`docs/PET_*.md`、`docs/HANDOVER.md` |
| 其他板块窗 | 各自 | 各板块实现 | 各自模块（**必须与上表不重叠**） |

**铁律**：
1. **总领窗不写代码** —— 写只发生在板块窗；总领窗负责把任务说清楚、把验收关住。
2. **同一时刻只有一个窗口在写同一文件** —— 文件版本守卫只拦"陈旧写入"，**不拦并发覆盖**。
3. **会话之间不能互相发消息**（Team 队友是同一会话内的成员）⇒ 交接一律走**文件 + git**。
4. **上下文各自独立** ⇒ 靠文档而不是记忆；长内容落盘，回复只报结论。
5. 如需并行多个板块：**在总领窗内开 Agent Teams**（队友共享同一工作目录，按写作用域分派），比多开窗口更好管。

---

## 6. 目录与资产地图

**上游目录 `D:\重置准备\备份\Elysia\`（总领窗工作目录）** —— 2026-10-16 00:14–00:37 实测：

| 条目 | 是什么 | 规模 | 依据 |
|---|---|---|---|
| `Elysia_two\Elysia` | **代码主线**（git，分支 `main`，远程 `github.com/qimeng-1/Elysia.git`） | 1.32 GB（`.venv` 938 MB、`assets` 392 MB） | `src/elysia/*` 新架构；130 提交；与旧工程**不共享历史** |
| `GPT-SoVITS-Elysia_Voice_Model` | 声音板块：**参考音频 4 条**（default/devoted/gentle/playful） | 2 MB | 2025-12-25 |
| `语音文档` | 声音板块：参考音频 17 条 + **GPT 权重**（`Elysia-e20.ckpt`、`Elysia_e24_s13080.pth`）+ 合成样例 | 235 MB | 2025-12-25 |
| `00_总纲入口.md` | 本索引的入口指针 | 1.5 KB | 2026-10-16 建 |

> ⚠️ **旧工程 `Elysia_Project` 已于 2026-10-16 00:14–00:28 之间离开本目录**，现位于
> `C:\Users\123\Desktop\Elysia_Project`（git `master` 84 提交，最新 `86a90fc`「Soul Backup: 2026-03-28」，
> 同一远程，4.11 GB，含 `client/ core/ data/ logs/ memory/`）。**移动者未核实**（非本会话所为）。
> 它是**旧版工程（v1.3 Phase 1）**；`Elysia_two\Elysia` 是它的重写版 —— 依据：
> `IMPLEMENTATION_ROADMAP.md` 开头注明"源文档在**旧工程** `Elysia_Project/docs/planning/`"；
> 结构由根级平铺改为 `src/elysia/*`；旧历史止于 2026-03-28，新首提交 `65ab334`(2026-08-22)，**历史独立**。

**仓库外素材（都不进 git；本次只报告、未改动）**：

| 路径 | 是什么 | 规模 | 判定 |
|---|---|---|---|
| `D:\ElysiaAssets\spine_elysia\` | **Spine 解包源素材**（elvisa_1–6 的 `.skel/.atlas/.png` + 封面） | 4.8 MB | **唯一副本 / 绝不能删** |
| `D:\ElysiaAssets\spine_bake\` | Spine 烘焙工作台（`bake.js`、`canvas.js`、`out/`、`node_modules/`） | 855 MB | 生成物可重建（**脚本是唯一副本**） |
| `D:\ElysiaAssets\_feas_probe\` | 运行时实时渲染可行性探针 + 实测日志 | 1.6 MB | 生成物（脚本唯一） |
| `D:\ElysiaAssets\_probe\` | 桌宠接灵魂的取证探针 | <0.1 MB | 脚本唯一 |
| `D:\ElysiaAssets\_obsolete\` | 已废弃素材（首帧、MiniMax 试片） | 45.6 MB | 可弃 |
| `D:\图片\Saved Pictures\` | 用户原始素材：**10 个 mp4**（呼吸/东张西望/奔跑/挥手/注意到我/漂浮/落地/雀跃…） | 5.3 MB | **唯一副本 / 不主动删** |
| `assets\pet\costumes\`（仓内，gitignore） | 6 套 Spine 服装 × 50 片段 | 94.6 MB | 可重建（`scripts\ingest_spine.py`） |
| `assets\pet\_retired\`（仓内，gitignore） | 9 套已撤下 AI/立绘服装 | 296.8 MB | 可恢复 / 可弃（**用户决定**） |
| `D:\ElysiaBackup\elysia\` | 备份 2 份：`20261014-033002`(1.9 MB)、`20261015-033003`(316.8 MB) | 318.7 MB | **含 `.env`** ⇒ 见 §8 红线 |

**`assets/pet/` 在 git 里只有 2 个文件**（`README.md`、`character.png`）——`costumes/`、`_retired/` 均被 gitignore。

**`.env` 现状（只读核对，未读任何值）**：文件存在（1.6 KB），**活跃赋值行 = 0**（全部为注释态）
⇒ 主声/次声**都未挂载**，`.env` 里没有 API key；实跑时她走 **micro 降级声**（与探针实测 `level=micro` 一致）。

---

## 7. 验证方式

| 项 | 内容 |
|---|---|
| **门禁四件套** | `uv run ruff check` → `uv run ruff format --check` → `uv run mypy src` → `uv run pytest` |
| 当前规模 | **461 用例 / 38 个测试文件**；ruff format 125 文件；mypy strict 60 文件 |
| 离屏 | 设 `QT_QPA_PLATFORM=offscreen`（覆盖 `test_pet.py` / `test_memory_view.py`）；**真机启动时不要设** |
| **CI** | `.github/workflows/ci.yml`：`windows-latest`，触发 `push`/`pull_request`/`workflow_dispatch`，同分支新推送取消旧任务；四步与本地同源；`uv sync --group dev --frozen` |
| 真机验收 | `scripts\soul.ps1 start -Body` → `scripts\soul.ps1 status`（两行 `[ok]`）→ `stop`；桌宠：`python -m elysia.body.pet`（右键 → 退出） |
| 素材验收 | `scripts\spine_verify.py`（桌宠素材：触边 0 / 尺寸一致 / 行为齐全） |
| 其他脚本 | `scripts\soul.ps1`、`watchdog.ps1`、`backup.ps1`、`memory_view.ps1`、`retention.py` |

---

## 8. 红线（不能碰）

1. **素材授权**：Spine Runtimes 需 Spine Editor 授权；手上这套归档标注「解包资源，仅供学习使用」⇒ **仅本地使用、绝不外传、绝不入库/公开**。同人立绘同理。
2. **她的真实数据在另一台机器**：本地 `data/*.db` 是测试数据；**绝不允许 本地 → 他机**（会用测试数据覆盖她真实的一生）；**绝不允许两台机同时跑她**。
3. **MiniMax API 每次调用都要用户逐次确认**（曾因"试一下"烧掉 ¥10）；key 在 `C:\Users\123\.elysia-minimax.key`，需要轮换。
4. **数据永不删除**：裁流水表须先拍板口径（归档保底 + 只裁 `heartbeats`，不碰 `memories`）。
5. **`.env` 绝不入库**（含 API key）。
6. **破坏性操作先确认**：删/移/覆盖/推送/改远程，一律先问。
7. **备份里含 `.env`**（`D:\ElysiaBackup\elysia\20261015-033003\.env`）⇒ 备份目录**同样不得外传**。
8. **旧工程含真实数据痕迹**（`C:\Users\123\Desktop\Elysia_Project\data`）⇒ 只读参考，**不当草稿、不覆盖**。

---

## 9. 需要人工裁决的开放问题

| # | 问题 | 出处 |
|---|---|---|
| 1 | **50 个提交未推送**（`origin/main` 仍是 `91bf19d`） | `HANDOVER.md` §8.3 |
| 2 | **桌宠按哪种形态接入灵魂**（"桌宠即身体" vs 先只做 P0 三项） | `PET_INTEGRATION_ASSESSMENT.md` §4/§7 |
| 3 | **F8/F-B 交互丢消息**（0.3s 连打两句只活第二句） | 同上 §1.3/§2 |
| 4 | **`applaud` 鼓掌素材 6 套全废**（眉毛横穿眼睛）⇒ 点击回应只剩挥手/比心 | `HANDOVER.md` §3.4 #23 |
| 5 | **P1 状态字段白传**（`desire/feelings/will` 到手但动画没用） | `PET_INTEGRATION_ASSESSMENT.md` §3.2 |
| 6 | **她的真实数据在他机**：本地库只是测试数据 | `HANDOVER.md` §8.4 |
| 7 | **文档口径冲突**：`STATUS_MATRIX` 内部（~185s vs 0s）；`OPERATION_GUIDE` §10 与 `STATUS_MATRIX` §11 #3/#4 相互矛盾 | 两文档 |
| 8 | **运维待定**：进程看门狗无崩溃循环退避；保留策略有意不自动调度；全局 git 身份损坏；无 API key、出声需手动起 GPT-SoVITS | `SESSION_REPORT_2026-10-14.md` §5.1/§5.3 |
| 9 | **旧工程位置变动**：`Elysia_Project` 于 2026-10-16 00:14–00:28 从 `D:\重置准备\备份\Elysia\` 移到桌面（**移动者未核实**）⇒ 需确认是否还需要它留在上层目录、以及桌面那份是否就是唯一完整副本 | 现场实测（`PROJECT_BOARD_INDEX.md` §6） |
| 10 | **`.env` 活跃赋值行 = 0** ⇒ 主声（DeepSeek）**从未真正挂载**，她一直走 micro 降级声；是否配置 key 需裁决（注意调用成本与"每次调用先确认"红线） | 现场实测（§6） |

---

## 10. 总领窗开盘提示词（复制即用）

```
这个窗口是 Elysia 工程的【总领窗】，工作目录 D:\重置准备\备份\Elysia。
先读：00_总纲入口.md → 再读 Elysia_two\Elysia\docs\PROJECT_BOARD_INDEX.md（总纲）、
STATUS_MATRIX.md（八板块状态 + 待补清单 + 缺陷登记）、PROJECT_ENGINEERING_STANDARD.md（规范）。

职责：只读 + 规划 + 派活 + 验收；不要直接改代码（写由板块窗负责）。
第一个雷：本目录下有两套 Elysia —— Elysia_two\Elysia 是主线（git，远程 qimeng-1/Elysia），
Elysia_Project 是旧版 + 真实数据痕迹。先确认主线，再动任何东西。
板块分工：桌宠板块由【另一个专属窗口】负责，它只写 src/elysia/body/** 与 assets/pet/**，
窗口说明见 docs/PET_SESSION.md；你不要碰这两个路径。

先给我一份「现状 + 优先事项」的结论（只报结论，长表格落盘 docs/，别往对话里塞）。
```

---

## 11. 维护规则

- 本索引**只放**：口径、板块边界、归属窗口、接口契约、开放问题、入口。
- **细节一律不复制**：板块状态改 `STATUS_MATRIX.md`，设计改路线图，实操改 `HANDOVER.md`，避免三处漂移。
- 触发更新的三件事：板块新增/合并、归属窗口变化、开放问题被裁决。
