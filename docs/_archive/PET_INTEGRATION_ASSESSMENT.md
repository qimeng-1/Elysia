# 桌宠接入工程的可行性评估

> 日期：2026-10-15 ｜ 评估对象：`src/elysia/body/pet.py`（1177 行）+ `body/anim.py`（356 行）+ `assets/pet/costumes/*`
> 方法：**跑起来的两个进程取证**，不是只读代码。探针脚本 `D:\ElysiaAssets\_probe\pet_integration_probe.py`（临时库、LLM key 置空，零外部调用）。
> 门禁基线（**2026-10-15 当日**）：ruff ✓ ／ ruff format 125 files ✓ ／ mypy strict 60 files ✓ ／ pytest **461 passed**。
> ⚠️ 2026-10-16 迁移后口径已变：format 计数 **135**（= 106 个 `.py` + 29 个 `.md`，**不是代码指标**，别当验收门），
> 且 `uv run mypy/pytest` 已坏 ⇒ 当前门禁口径与她的库数字见 [HANDOVER.md](HANDOVER.md) §3.5 #35/#37、§8.4、§9。

---

## 0. 结论

**可以接入，且不需要重构。** 桌宠与灵魂之间**已经存在可用的数据契约**（`protocol/snapshots.py` v2 + 双库文件），实测桌宠能读到她的真实状态并据此选动画；耦合面只有"SQLite 文件"，没有任何 import 纠缠。

但"能读"不等于"接好了"：**有 3 处硬伤、3 处软肋**。硬伤全部会让她对你产生错误认知，属于接入前必修。

| | 结论 | 依据 |
|---|---|---|
| 技术上能否接入 | **能**，协议/数据通路已在运行 | §1.1 |
| 需要重构吗 | **不需要**，补齐接缝即可 | 耦合面仅文件 |
| P0 必修（3 项） | 在场语义断裂、交互丢消息、生命周期缺位 | §2 |
| 建议形态 | **桌宠即身体**（一个进程承担"在场"） | §4 选项 1 |
| 工作量 | P0 ≈ 1 天 ／ P1 ≈ 1 天 ／ P2 ≈ 0.5 天 | §5 |

---

## 1. 现状实测

### 1.1 通路是活的（正面证据）

临时库启一个真灵魂（1Hz），5s 后让桌宠以 offscreen 方式读一次：

```
A_soul_beats_5s = 5                         ← 她真的在活
A_pet_view = {read_ok: true, version: 2, mode: "present",
              since_interaction_s: 4, distress: false,
              anim_state: "happy",
              has_desire: true, has_feelings: true, has_will: true}
```

⇒ 桌宠→她的状态链路**端到端可用**：P1 的 `desire/feelings/will` 全部到手（只是桌宠**没用**，见 §3.2）。

### 1.2 离线可用（正面临界证据）

LLM key 置空、本地 TTS 服务未启动（日志：`GPT-SoVITS … 无法连接`）时：

```
thought_log = [(1,'expression','……嗯，陪你聊～。'), (2,'idle','独处的缝隙里…')]
expression_log = [(1,'发呆呓语', …, level='micro')]
```

⇒ 降级链（micro 声）扛住了整条表达链路 ⇒ 桌宠接入后**无网/CI 也能验收**。

### 1.3 三处硬伤实测

| 编号 | 现象（实测） | 判定 |
|---|---|---|
| **F-A** | 桌宠开在屏幕上 5s：`body_status` 行数 = **0**、`last_body_online_ts` = **0.0** ⇒ 她**完全不知道你在场**；再模拟"身体心跳停在 400s 前"→ `mode: "alone"` → 桌宠 `anim_state: "away"` | **双向断裂**：她不知道你在；也能在你眼前以为你走了 |
| **F-B** | 0.3s 内连打两句：KV **2 写 → 1 行**、存活的是第二句、第一句的 ts 不可恢复；`timesense.last_interaction_ts` = 第二句 ts；她只产生 **1** 条表达（`expression_log` 1 行） | 每个心跳窗（1s）**恒定丢前 N−1 句**，且无法回溯 |
| **F-C** | `scripts/soul.ps1` 只管 soul + body（`-Body` → `elysia.body.main`）；**没有任何 pid 文件、日志重定向、status、stop、watchdog、自启**覆盖 `elysia.body.pet` | 桌宠在生命周期管理之外：不会自愈、状态看不见、`stop` 停不掉 |

---

## 2. P0 必修项（接入前置）

### F-A 在场语义：谁代表"身体在场"

- 现状：只有 `body/main.py` → `BodyHeartbeat` 写 `body_status`（`src/elysia/body/heartbeat.py` L52）。桌宠窗口开/关对**她的时间感毫无影响**。
- 两种错法：
  1. 只跑 soul+桌宠（不起 `body.main`）⇒ `last_body_online_ts` 恒为 0 ⇒ `ModeManager.mode()` 走"无 0 时刻"分支恒 `PRESENT` ⇒ 她的**独处/离开生活（`away_life`）永不触发**。
  2. 跑了 `body.main` 又关掉桌宠 ⇒ 身体心跳照旧 ⇒ 你关掉桌宠，**她还以为你在场**。
- 修法：抽 `body/presence.py::PresenceReporter`（"写一拍在场"的单一出处），由 `body/main.py`（无 GUI）与 `pet.py`（QTimer 5s）共用；桌宠 `closeEvent` 写显式 `status:"offline"`，`soul._sync_body_status` 识别后**立即**记离开（不等 5 分钟阈值）。
- 验收：起 soul + 桌宠（不起 body.main）⇒ `body_status` 每 5s 刷新；关窗口 ⇒ ≤1 拍内 `body_away_start_ts` 被设置，`mode` 由 `present` → `alone`。

### F-B 交互通道：单键覆盖 → 追加队列

- 现状：`pet.py` L1136 `INSERT OR REPLACE INTO kv ('interaction', ?)`；`soul/heartbeat.py` L720 每拍读同键一次。
- 修法：新增**只追加**表（`interaction_log(id, ts, kind, text)`，走 `state_store.py` 既有幂等迁移风格）；桌宠只 `INSERT`，灵魂按 `id > last_seen_id` 逐拍取全部新行（连打 N 句收 N 句、可回溯、可区分"点击/文本/拖拽"）。`kv('interaction')` 保持可读以兼容旧桌宠。
- 验收：单测"0.3s 连打 2 句 ⇒ 她收到 2 条"；`timesense.last_interaction_ts` 推进到第 2 句；旧格式读路径不回归。

### F-C 生命周期：桌宠进入工程管理

- 修法：`soul.ps1` 增桌宠启停（`-Pet` 或把 `-Body` 语义改为"启动桌宠"，建议前者，保留无 GUI 的 headless 身体）；桌宠写 `data/run/pet.pid` + `data/logs/pet.out.log`；`watchdog.ps1` 把桌宠纳入存活判定；`stop` 一并停桌宠（先停 GUI 再停灵魂，避免她的表达写空）。
- 验收：`soul.ps1 status` 能看到桌宠 PID；杀掉桌宠进程后 watchdog 能拉起；`stop` 后 `run/*.pid` 全部清理、`Get-Process` 无残留。

---

## 3. P1 该修项（接好之后才谈"整体开发"）

### 3.1 单一读写出口
`pet.py` 在 GUI 线程里裸 `sqlite3`（L589/L596 建连、L1033/L1046 读、L1136 写）。虽然 WAL + `busy_timeout=5000` 让并发安全，但**写操作在 UI 线程**，一旦撞上灵魂的长事务就会**冻结界面最多 5s**。修法：`body/pet_bridge.py` 收口（只读查询 + 后台线程写队列），`pet.py` 不再出现 SQL 字符串。

### 3.2 P1 状态字段白传
实测 `has_desire/has_feelings/has_will = true`，但 `_map_state_to_animation`（L1019）只用 `distress / mode / since_interaction_s` ⇒ **P1 三层状态对动画零影响**；`STATE_CLIPS`（L123）只有 5 个状态。修法：按 `sa`（累）/`miss`（想）/`brain_action`（在想什么）扩展映射，把"她此刻的内心"真正演出来。

### 3.3 出声靠猜 + 无版本协商
`_play_latest_audio`（L1081）按 **mtime 取 TTS 缓存里最新的 wav**——同一时刻多句/预合成会放错音。修法：`expression_log` 增 `audio_path` 列，灵魂落库时写相对路径，桌宠直接读。另外读者端忽略 `version`（实测 `version: 2` 到手但没校验）⇒ 加"不认识就退到安全状态 + 记日志"，并补**契约测试**（offscreen + 临时库，用 micro 声，不需要真 LLM）。

---

## 4. 接入形态三选项

| 选项 | 内容 | 优点 | 代价 | 判定 |
|---|---|---|---|---|
| **1（推荐）桌宠即身体** | `elysia.body.pet` 承担在场上报（QTimer 5s）；`body/main.py` 退为"无 GUI 场景"备用 | 在场信号**永远真实**（有窗口=在你眼前）；少一个进程；无双写者 | 需抽 `PresenceReporter` 共用 | ✅ 推荐 |
| 2 三进程并存 | 桌宠保持纯 UI，另跑 `body.main` | 改动最小 | 在场与"窗口是否在眼前"永久解耦；必须约定"两个都要开"；双写者 | ⚠️ 仅当坚持无 GUI 运行时 |
| 3 单进程合体 | 灵魂 + PySide6 同进程 | 零轮询、信号直达 | 需引入 `qasync`（新依赖）；灵魂崩=窗口崩；**摧毁"纯桌宠/身体独立"这条已验证的隔离**（你要的"单纯桌宠不涉及灵魂"就没了） | ❌ 否决 |

选项 1 同时满足你此前的两条硬要求：`python -m elysia.body.pet` 仍默认**纯桌宠**（`--with-soul-data` 才接灵魂），隔离性不破。

---

## 5. 施工计划

| 阶段 | 任务 | 主要改动 | 验收 |
|---|---|---|---|
| P0-1 | 在场上报收口 | 新 `body/presence.py`；`pet.py` 挂 5s QTimer；`closeEvent` 写 offline；`soul/heartbeat.py` 识别 offline | §2 F-A 验收 |
| P0-2 | 交互追加队列 | `state_store.py` 加 `interaction_log` + 迁移；`pet.py` 只写队列；`soul/heartbeat.py` 逐 id 消费 | 连打 2 句收 2 句（新单测） |
| P0-3 | 生命周期纳管 | `soul.ps1` / `watchdog.ps1` / pid / 日志 | §2 F-C 验收 |
| P1-1 | 读写出口 `pet_bridge.py` | `pet.py` 去 SQL；写走后台队列 | 门禁 + 桌面验收 |
| P1-2 | 状态→动画升级 | `_map_state_to_animation` + `STATE_CLIPS` 吃 desire/feelings/will | 桌面验收（她"累/想你/发呆"看得出差别） |
| P1-3 | 声音直连 + 版本协商 + 契约测试 | `expression_log.audio_path`；reader 校验 version；`tests/integration/test_pet_soul_contract.py` | 新测试入 CI（offscreen，无网可跑） |
| P2 | 文档与自启 | `HANDOVER.md` / `PET_SESSION.md` / 开机自启注册 | 文档与实现一致 |

---

## 6. 风险与边界

1. **素材不在库里**：`assets/pet/costumes/` 被 gitignore（仓库只跟踪 `README.md` + `character.png`）⇒ CI 只能跑"无素材→降级"路径；桌宠的**视觉验收必须真机**。
2. **延迟下限 1s**：她 1Hz 心跳，桌宠 5Hz 轮询 ⇒ 任何"她→桌宠"的反应最快也要 ~1s。要更跟手就得走选项 3（已否决）或加进程间通知（不划算）。
3. **60fps Qt 主线程**：动画/解码都在 UI 线程（现 85–89 MB，可接受）；P1-1 的写队列不得引入阻塞。
4. **授权红线不变**：Spine 与同人素材**仅本地使用、不入公开库、不外传**。
5. **她真正的生活数据在另一台机器**：本地库是测试数据；接入验证**只能用本地测试库**，绝不动真身。

---

## 7. 需要你裁决的一步

按选项 1 施工（P0 三项 + P1 三项，约 2.5 天量级），还是先只做 P0 三项（约 1 天）让你看"她终于知道你在不在"？

> 附：本评估的探针可复跑 —— `.venv\Scripts\python.exe D:\ElysiaAssets\_probe\pet_integration_probe.py`（临时目录、零外部调用，15s 出结论）。
