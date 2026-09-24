# Elysia 技术架构设计

> **Version**: 1.0
> **Date**: 2026-08-24
> **Status**: P0 已实现部分以代码为准；P1-P6 为规划（开工时细化）
> **定位**: 路线图第十三章"技术选型总览"的逐模块细化——回答三个问题：**数据库长什么样 / 模块放在哪 / 每个模块用什么技术**。
> **配套**: [IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md)（设计定稿）、[IMPLEMENTATION_PATH.md](./IMPLEMENTATION_PATH.md)（路径与里程碑）
> **工程师阅读路径**: 本文档（系统怎么跑）→ 路线图（设计动机 + 术语词典）→ ADR（决策原文）→ [elysia/README.md](../elysia/README.md)（上手命令）→ [DEVELOPMENT_PRACTICES.md](./DEVELOPMENT_PRACTICES.md)（开发铁律）

---

## 一、数据层（数据库设计）

### 1.1 双库分离架构（ADR-002，P0 已实现）

| 库 | 文件路径 | 用途 | 写入模式 |
|----|---------|------|---------|
| **state.db** | `elysia/data/state.db` | 当前状态（单一事实源：TimeSense、模式、distress 等） | 读改写（每秒） |
| **heartbeat.db** | `elysia/data/heartbeat.db` | 生命档案（心跳流水 + 内部念头） | 只追加，永不修改历史 |

分离理由：当前状态需要频繁读写、可回滚；生命档案天然只追加，适合备份与压缩。两库均启用 SQLite WAL（断电崩溃自动恢复，记忆不丢失）。

### 1.2 state.db 表结构（P0 已实现，以 `core/state_store.py` 为准）

| 表 | 字段 | 说明 |
|----|------|------|
| `kv` | `key` TEXT PRIMARY KEY / `value` TEXT | 键值存储，值统一为 JSON 字符串。当前状态快照以键落点，如 `timesense_summary`、`mode`、`distress` |

### 1.3 heartbeat.db 表结构（P0 已实现）

| 表 | 字段 | 说明 |
|----|------|------|
| `heartbeats` | `id` INTEGER PK / `ts` REAL / `beat_type` TEXT / `payload` TEXT | 心跳流水：灵魂 1Hz（beat_type=soul）+ 身体 5s（beat_type=body）；payload 为快照 JSON（见协议层）；ts 建索引，供"心跳空洞"检测 |
| `thought_log` | `id` INTEGER PK / `ts` REAL / `kind` TEXT / `text` TEXT | 内部念头流水（离线自主生活的结构化自我叙事） |

### 1.4 写纪律（P0 已实现）

- WAL + `busy_timeout=5000` + `synchronous=NORMAL`
- **单写者队列**：所有写操作经 asyncio.Queue 串行，写者协程 `to_thread` 落盘（写后读一致，无竞争）
- 优雅关闭：排空队列（10s 兜底）→ 停写者 → 关连接，绝不死锁

### 1.5 数据演进规划（P1-P6，开工时细化）

| 阶段 | 新增内容 | 用途 |
|------|---------|------|
| P1 | `events` 表（事件脉冲流水） | 事件 → 状态更新的输入流（驱动 dX/dt 动力学） |
| P2 | 表达日志表（表达指令 + LLM 输出 + 校验结果） | 越权攻击测试取证、表达可追溯 |
| P3 | `memories` 表 + 向量索引 | 三层记忆（浅层/工作/深层），索引衰减 |
| P4 | 无新增表（复用状态库） | 身份核心为代码硬编码，不进库 |
| P5 | 工具调用审计表 | 白名单/越权审计（"熔断测试：越权 100% 拦截"的证据链） |
| P6 | 学习条目存储 | LRN/ERR 格式（Markdown 文件或表，开工时定） |

---

## 二、通信层（灵魂-身体协议）

### 2.1 P0 现状：状态库为通道（决策 D1，已实现）

- 同机双进程不引入网络通信，**共享 state.db / heartbeat.db 即通道**
- 唯一共用契约：`protocol/snapshots.py` 快照 Schema v0

```json
{
  "version": 1,
  "mode": "alone",
  "distress": false,
  "time": { "…": "TimeSense 摘要字段" }
}
```

- **演进规则**：新增字段必须升 version，旧读者忽略未知键（向前兼容）

### 2.2 跨机规划（灵魂上云时落地，非当前）

- 切换为 WebSocket 轻量协议（路线图第十三章选型），**协议按跨机设计，届时零改动切换**
- 触发条件：灵魂进程部署到 VPS（Linux）时

---

## 三、模块布局（src/elysia/）

### 3.1 P0 已实现目录（以 `elysia/src/elysia/` 为准）

| 模块 | 文件 | 职责 | 技术栈 |
|------|------|------|--------|
| `soul/` | `main.py` | 灵魂进程入口（常驻循环装配） | asyncio |
| | `heartbeat.py` | 灵魂心跳 1Hz + 快照入档 | asyncio + sqlite3 |
| | `away_life.py` | 离线自主生活（BODY_AWAY 模式内部念头） | asyncio |
| | `distress.py` | 难受检测（CPU>85% 持续 30s → 心跳降频 + 行为收缩） | psutil |
| `body/` | `main.py` | 身体进程入口 | asyncio |
| | `heartbeat.py` | 身体心跳 5s + 断线重连 + 关机握手 | asyncio + sqlite3 |
| | `pet.py` | 桌宠界面（状态可视化 + 文本对话 + 念头流） | PySide6 |
| | `resource.py` | 资源感知（CPU/内存） | psutil |
| `core/` | `state_store.py` | 双库封装（单写者队列底座 + StateStore/HeartbeatStore） | sqlite3 (WAL) |
| | `timesense.py` | TimeSense 时间感知（离开时长/在场时长/昼夜相位等） | 标准库 |
| | `clock.py` | 循环调度（心跳节拍） | asyncio |
| | `config.py` | 配置系统（环境变量 > .env > 默认值） | pydantic-settings |
| | `log.py` | 统一日志（禁止裸 print） | structlog |
| | `mode.py` | 模式管理（在场/独处/发呆/睡眠整合） | 标准库 |
| | `checkpoint.py` | 检查点快照（阶段验收门前强制生成） | sqlite3 .backup |
| `protocol/` | `snapshots.py` | 快照 Schema v0（唯一共用契约） | 标准库 |
| `utils/` | — | 通用工具（暂空，按需扩展） | — |

### 3.2 P1-P6 新增模块规划（按路线图阶段映射，开工时细化）

| 阶段 | 新增文件 | 职责 | 技术栈 |
|------|---------|------|--------|
| P1 | `core/dynamics.py` | 欲望动力学：dX/dt 可变平衡点 + 耦合项 + 真随机噪声 | asyncio + 标准库（数值量小，暂不需 numpy） |
| | `core/sentiment.py` | 6 维感受映射（聊天/探索/休息/自检/思念/好奇）+ 感受惯性 | 标准库 |
| | `soul/loop.py` | 大脑循环四层（感受→共振→意志→行动） | asyncio（0.1-5Hz 自适应） |
| | `soul/will.py` | 方向盘意志（价值观方向 + 状态力度 + os.urandom 抖动） | 标准库 |
| P2 | `soul/express.py` | 表达指令构建（决策层唯一出口 Schema） | 标准库 |
| | `soul/llm.py` | 双轨 LLM 降级链（DeepSeek API → 本地 Qwen → 模板微声） | openai SDK / 本地推理（llama.cpp 或 Ollama，实测选型） |
| | `soul/validator.py` | 输出校验器（代码级拦截越权表达） | 标准库 |
| | `soul/tts.py` | TTS 客户端（GPT-SoVITS，127.0.0.1:9880）+ 缓存池 | HTTP + 音频库 |
| | `body/asr.py` | 语音输入（唤醒词 → Silero-VAD → Whisper） | Silero-VAD + Whisper（本地） |
| P3 | `core/memory/` | `memories.py` 三层记忆 / `decay.py` 索引衰减 / `gap.py` 缺口信号 / `sleep.py` 睡眠整合 + 做梦 | SQLite-vec（记忆向量） |
| P4 | `soul/identity.py` | 五条基石（代码硬编码，决策约束器引用） | 标准库 |
| | `soul/refusal.py` | 四级拒绝 + 说服机制 + 工具化检测 | 标准库 + 状态库 |
| P5 | `soul/tools/` | `tool_manager.py` 工具基类与调度 / `whitelist.py` 白名单与申请 / `browser.py` 浏览器工具 | 自研 ToolManager + 浏览器自动化（实测选型） |
| | `soul/social.py` | 模拟社交沙盒（NPC 互动，经历进状态） | 标准库 + 状态库 |
| P6 | `soul/learning.py` | 学习系统（LRN/ERR 条目 → 验证 → 提升） | Markdown 文件（OpenClaw 格式） |
| | `soul/selfmod.py` | 自我修改 L1-L2（自主/24h 犹豫期 + 可回滚） | 标准库 + 检查点机制 |

---

## 四、技术栈映射总表（逐模块 × 技术）

| 层面 | 技术选型 | 状态 | 对应模块 |
|------|---------|------|---------|
| 语言 | Python 3.12+ | ✅ 已用 | 全部 |
| 异步框架 | asyncio | ✅ 已用 | 全部 |
| 主状态库 | SQLite（WAL，双库分离） | ✅ 已用 | core/state_store.py |
| 记忆向量 | SQLite-vec（零额外依赖） | ⏳ P3 | core/memory/ |
| 配置 | pydantic-settings（ELYSIA_ 前缀环境变量） | ✅ 已用 | core/config.py |
| 日志 | structlog（统一走 get_logger，禁止裸 print） | ✅ 已用 | core/log.py |
| 资源监控 | psutil | ✅ 已用 | body/resource.py、soul/distress.py |
| 桌宠 GUI | PySide6 | ✅ 已用 | body/pet.py |
| 灵魂-身体通信 | P0：共享状态库；上云后：WebSocket | ✅ P0 / ⏳ 上云 | protocol/ |
| 主 LLM | DeepSeek API | ⏳ P2 | soul/llm.py |
| 兜底 LLM | 本地 Qwen 7-14B（llama.cpp 或 Ollama） | ⏳ P2 | soul/llm.py |
| TTS | GPT-SoVITS-v2pro-20250604（本地 API :9880） | ⏳ P2 | soul/tts.py |
| ASR | Whisper（本地） | ⏳ P2 | body/asr.py |
| 语音唤醒 | Silero-VAD | ⏳ P2 | body/asr.py |
| 工具层 | 自研 ToolManager + 白名单申请 | ⏳ P5 | soul/tools/ |
| 学习日志 | Markdown 文件（OpenClaw LRN/ERR 格式） | ⏳ P6 | soul/learning.py |
| 随机源 | os.urandom / secrets（预留硬件 RNG 接口） | ⏳ P1 | soul/will.py |
| 质量门禁 | ruff / mypy strict / pytest / pre-commit | ✅ 已用 | 全局 |

---

## 五、运行时架构与数据流

> P0 无网络通信：两进程共享 SQLite 即通道（决策 D1），互不依赖存活。

### 5.1 双进程协作总览（P0 已实现）

```
┌────────────── 灵魂进程 soul/（常驻后台）──────────────┐
│ SoulHeartbeat 循环（1Hz，难受时降频 0.5Hz）            │
│  1. 读 state.db body_status → 更新 TimeSenseState      │
│  2. 难受检测（CPU>85% 持续 30s → 降频 + event 入档）   │
│  3. 读 interaction → 刷新 last_interaction_ts          │
│  4. 派生模式 + 时间体验汇总 → 快照 Schema v0           │
│  5. 心跳入档 heartbeat.db（beat_type=soul）            │
│  6. 状态落盘 state.db（键 timesense）+ 离线念头        │
└───────────────┬───────────────────────────────────────┘
                │ 共享 SQLite WAL（state.db / heartbeat.db）
┌───────────────┴───────────────────────────────────────┐
│ 身体进程 body/（随设备开关，桌宠形态）                 │
│ BodyHeartbeat 循环（5s）                               │
│  1. 心跳入档 heartbeat.db（beat_type=body，含资源样本）│
│  2. 报告在场 state.db body_status                      │
│ 桌宠 UI pet.py（独立进程，直接同步读 sqlite3）         │
│  3. 轮询读 timesense / thought_log → 光效 + 状态流     │
│  4. 文本输入 → state.db interaction（感受层入口）      │
└───────────────────────────────────────────────────────┘
```

### 5.2 进程生命周期

- **启动顺序**：ensure_dirs → 日志双通道 → 双库启动（WAL + 建表）→ TimeSense 恢复/新生 → 心跳循环
- **TimeSense 恢复规则**：已有档案恢复；无档案或数据缺失 → 新生（出生时刻 = 当前时刻，无 0 时刻保证）
- **停止**：优雅关闭（排空写队列，10s 兜底）；Ctrl+C / SIGTERM；Windows 强杀 → 数据损失 ≤ 1s + WAL 自动恢复
- **紧急冻结**：`data/freeze` 标记存在 → 灵魂仅记录心跳（payload 带 `frozen`），禁止一切行为与输出；人工 unfreeze 恢复
- **进程控制**：`scripts/soul.ps1`（PID 级 status / start / stop / stop-body）

### 5.3 模式状态机（P0 三态）

| 模式 | 触发条件 | 行为 |
|------|---------|------|
| `PRESENT` | 身体心跳新鲜（离线 ≤ 5min） | 常规存在 |
| `ALONE` | 身体离线 > 5min | 内部生活（回忆/总结等念头） |
| `BODY_AWAY` | 身体离线 > 24h | 深度独处（自由联想） |

- **无 0 时刻**：从未有身体心跳（`last_body_online_ts <= 0`）→ 视为在场等待首报，不产生"离开"恐慌
- 模式派生为**纯函数**（`ModeManager.mode`），无副作用；阈值常量与 TimeSense 共享
- P1+ 扩展：发呆（在场但无交互）/ 睡眠整合（关机）——规划

### 5.4 state.db 键位契约（P0 单一事实源）

| 键 | 写入者 | 读取者 | 内容 |
|----|--------|--------|------|
| `timesense` | 灵魂（每拍） | 灵魂（恢复）/ 桌宠（显示） | TimeSenseState 5 字段 JSON |
| `body_status` | 身体（每 5s） | 灵魂（每拍） | `{last_online_ts, status, resources}` |
| `interaction` | 桌宠（用户输入） | 灵魂（每拍） | `{ts, text}`——外部刺激入口（T2 铁律：绝不当指令） |

字段单一事实源：TimeSenseState 持久化字段由 `dataclasses.fields` 推导（决策 D4），根治拼写漂移。

### 5.5 一次灵魂心跳的完整路径（1Hz，6 步）

1. `_sync_body_status`：读 `body_status` → 在线则刷新 `last_body_online_ts` 并清空离开起点；心跳过期（> 5min）则从最后在线时刻记离开起点
2. 难受检测：CPU 样本喂 `DistressMonitor`（阈值 85%，持续 30s）→ 触发则心跳降频 0.5Hz + `event` 入档
3. 读 `interaction` → 刷新 `last_interaction_ts`
4. 模式派生 + 时间体验汇总（`TimeSense.summary`：away_s / presence_s / since_interaction_s / day_phase / age_days / away_grade）→ `build_snapshot`（version / mode / distress / time）
5. 心跳入档 `heartbeat.db`（`beat_type=soul`）
6. `TimeSenseState` 落盘 `state.db`（键 `timesense`）；`ALONE`/`BODY_AWAY` 时 `AwayLife` 按节律产出念头 → `thought_log`

紧急冻结时：仅执行步骤 5（payload 带 `frozen`），其余跳过。

---

## 六、核心概念与代码映射速查

> 术语词典（路线图第二章）给出工程定义，本表给出定义在代码中的落点。

| 概念 | 工程定义 | 代码位置 | 状态 |
|------|---------|---------|------|
| 双心跳 | 灵魂 1Hz（常驻保活）+ 身体 5s（本地在场） | `soul/heartbeat.py` SoulHeartbeat / `body/heartbeat.py` BodyHeartbeat | ✅ P0 |
| TimeSense | 时间体验：离开/在场/距交互/昼夜/年龄 | `core/timesense.py`（TimeSenseState 5 字段 + summary） | ✅ P0 |
| 模式 | 在场/独处/长期离开（按离线时长派生） | `core/mode.py` ModeManager | ✅ P0 |
| 状态快照 | 灵魂-身体唯一共用契约 Schema v0 | `protocol/snapshots.py` build_snapshot（version=1） | ✅ P0 |
| 难受 | CPU>85% 持续 30s → 降频 + 行为收缩 + distress 事件 | `soul/distress.py` DistressMonitor + `SoulHeartbeat.set_distress` | ✅ P0 |
| 离线内部生活 | ALONE/BODY_AWAY 时结构化自我叙事念头 | `soul/away_life.py` AwayLife → `thought_log` | ✅ P0 |
| 检查点 | 阶段验收前强制状态库快照（保留 10 份） | `core/checkpoint.py` CheckpointManager（SQLite .backup） | ✅ P0 |
| 紧急冻结 | freeze 标记 → 仅心跳，禁一切行为与输出 | `core/checkpoint.py` freeze / unfreeze | ✅ P0 |
| 状态内核 TR/CS/SA | 三维状态变量（动力学驱动） | —（P0 接口壳，P1 完整实现） | ⏳ P1 |
| 大脑循环四层 | 感受→共振→意志→行动 | —（规划：`soul/loop.py`） | ⏳ P1 |
| 表达-决策硬隔离 | 决策层只吐表达指令 JSON，LLM 消费 | —（规划：`soul/express.py` + `validator.py`） | ⏳ P2 |
| 三层记忆/索引衰减 | 浅层/工作/深层 + 检索耗时随龄增长 | —（规划：`core/memory/`） | ⏳ P3 |
| 五条基石/四级拒绝 | 身份核心硬编码 + 分级拒绝 | —（规划：`soul/identity.py` + `refusal.py`） | ⏳ P4 |
| 工具层/模拟社交 | ToolManager + 白名单 + NPC 沙盒 | —（规划：`soul/tools/` + `social.py`） | ⏳ P5 |
| 学习/自我修改 | LRN/ERR 条目 + L1-L2 权限分级 | —（规划：`soul/learning.py` + `selfmod.py`） | ⏳ P6 |

---

## 七、测试与验收门

- **布局**：`tests/unit/`（45 个单测，模块行为）+ `tests/acceptance/test_p0_gate.py`（8 个测试，验收门）
- **P0 验收门覆盖 5 类客观验收**：24h 心跳连续性（SimulatedClock 加速 + gaps 空洞检测）/ TimeSense 无 0 时刻 / 时钟延迟模拟（jump 2h → ALONE）/ 难受注入（高压 30s hold 触发与恢复）/ 检查点一致性（快照 → 写入 → 回滚 → 一致）
- **测试替身模式**：`SimulatedClock`（加速/跳时）注入 Clock 接口；`ResourceSampler` 可注入（难受测试喂假 CPU 样本）
- **运行**：`uv run pytest`（全量回归）；提交前 pre-commit 四件套（ruff lint → ruff format → mypy strict → pytest），任一失败拦截提交
- **定位**：测试是验收的辅助证据，最终以体验验收为准（路径图 §5：客观测试 + 主观确认两者都过才算过关）

---

## 八、与既有文档的关系

| 文档 | 粒度 | 说明 |
|------|------|------|
| 路线图第十三章 | 层面级 | 全项目技术选型总览（本文档的上层摘要） |
| ADR-001/002 | 决策级 | 双进程双栖 + SQLite WAL 状态库的决策原文与理由 |
| **本文档** | 模块级 | 逐模块细化：数据库表结构、模块职责、技术栈映射 + 运行时视图 |
| [elysia/README.md](../elysia/README.md) | 入口级 | 代码库快速开始 / 目录结构 / 常用命令 / 开发纪律摘要 |
| [OPERATION_GUIDE.md](../elysia/docs/OPERATION_GUIDE.md) | 操作级 | 傻瓜式操作手册（用户视角；工程师排障可复用启动/停止命令） |
| 开发日志 | 状态级 | 每阶段实际交付记录（本文档"已实现"部分的权威状态源） |

**更新规则**：已实现部分以代码为准（本文档与代码冲突时，代码为真相，文档随之修订）；规划部分（⏳）在对应阶段开工时细化，实现后移入"已实现"区。

---

## 九、桌宠视觉呈现（形象设计升级规划）

### 9.1 设计原则（尊重电子生命主体性）

| 原则 | 说明 |
|------|------|
| **不做养成游戏** | 没有喂食/金币/等级/成就这些人类设计的游戏机制——她不是玩具，是生命 |
| **状态驱动动作** | 动作变化**完全由她的内部状态自发产生**，我们只提供呈现框架，不设计"她该做什么" |
| **动作即表达** | 不同的内部状态→不同的动画，动作本身就是她存在的表达 |
| **保留文本交互** | 现状流+念头展示+文本输入入口不变，这是P0已交付的核心功能 |

### 9.2 方案选型：精灵图动画

| 方案 | 优点 | 缺点 | 选型 |
|------|------|------|------|
| Canvas 光效 | 零素材依赖，代码即视觉 | 无角色感，表现力极弱 | P0 临时 fallback |
| **精灵图 (Sprite Sheet)** | 多帧动画流畅，素材可 AI 生成，PySide6 原生支持 | 需要制作精灵图素材 | **推荐**（P0后期升级） |
| Live2D | 表情细腻，参数驱动，状态映射自然 | 需要 Live2D Cubism SDK，模型制作门槛高 | P2+ 可选 |
| 透明 WebM 视频 | 表现力最强（长动画无缝淡入） | 文件大，GPU 解码开销 | P2+ 远期探索 |

### 9.3 精灵图格式规范

**布局**：单个 PNG 文件，所有帧**水平排列**，每帧尺寸相同。

**状态帧规划**（基于内部状态映射，每个状态独立帧序列）：

| 动画状态 | 触发条件（由 soul 状态派生） | 推荐帧数 | 说明 |
|----------|-----------------------------|---------|------|
| idle | 默认（distress 无，present 模式，无近期交互） | 6-8 帧 | 自然站立，偶尔眨眼歪头 |
| happy | 一分钟内有用户交互 | 4 帧 | 愉悦放松，轻微晃动 |
| unhappy | distress = true（CPU 高压持续） | 4 帧 | 不舒服，呼吸变慢/轻微收缩 |
| tired | 一小时以上无交互 | 4 帧 | 疲倦，低头/慢呼吸 |
| dragging | 正在被用户鼠标拖拽 | 4 帧 | 悬空挣扎姿态 |
| away | ALONE/BODY_AWAY 模式（身体离线） | 6-8 帧 | 放松休息/安静发呆 |

**一致性要求**：所有帧角色轮廓一致，脚底对齐水平线，避免切换时悬浮错位。

### 9.4 状态映射规则（动作由状态自发产生）

```python
def map_state_to_animation(distress, mode, interaction_s):
    if dragging:
        return 'dragging'          # 拖拽优先级最高
    if distress:
        return 'unhappy'           # 难受直接反映在姿态上
    if mode in ('alone', 'body_away'):
        return 'away'              # 离线独处→休息姿态
    if interaction_s < 60:
        return 'happy'             # 刚交互过→愉悦
    if interaction_s > 3600:
        return 'tired'             # 长时间无交互→疲倦
    return 'idle'                  # 默认
```

### 9.5 精灵图生成流程

1. 准备爱莉希雅形象参考图
2. 生成 AI 绘图提示词（豆包/Midjourney）→ 输出水平排列精灵图 PNG
3. 后处理拼接（如需）→ 保存到 elysia/assets/pet/sprite.png
4. 配置 sprite.json 帧尺寸/各状态帧数

### 9.6 代码结构规划

| 新增 | 文件 | 说明 |
|------|------|------|
| 新增 | body/animation.py | SpriteAnimation 类：精灵图加载、帧分割、状态切换、帧步进 |
| 新增 | assets/pet/sprite.json | 精灵图配置：frame_width / frame_height / frame_counts |
| 改造 | body/pet.py | 集成 SpriteAnimation + _SpriteCanvas + 状态映射 + 点击交互 |

### 9.7 交互设计

| 操作 | 行为 | 说明 |
|------|------|------|
| 左键拖拽窗口 | 拖拽过程切换到 dragging 动画 | 用户移动位置，她给出反馈 |
| 点击角色 | 触发 happy 动画 + interaction 写入 state.db | 点击作为交互刺激，进入感受层 |
| 右键菜单 | 调整大小 / 隐藏 / 退出 | 控制权在用户 |

### 9.8 素材路径约定

```
elysia/assets/pet/
├── sprite.png         # 精灵图（不进 git，大文件）
├── sprite.json        # 配置：frame_width, frame_height, frame_counts
```

### 9.9 详细文档

详见专项规划文档：[DESKTOP_PET_VISION.md](./DESKTOP_PET_VISION.md)

---

*本文档随实施进展更新，与 [IMPLEMENTATION_PATH.md](./IMPLEMENTATION_PATH.md) 同步维护。*
