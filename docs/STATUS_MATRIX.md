# Elysia 状态矩阵（STATUS_MATRIX）

> 建立于 2026-09-24，属《运行时可靠性收口》**步 0.2**（零行为变化，纯文档）。
> 基线数字见 [BASELINE_2026-09-24.md](./BASELINE_2026-09-24.md)；本轮的**真问题**是
> 「心跳不能等外部服务」，因此每行都回答两件事：**这块能力由谁在跑**、**它会不会拖住心跳**。
> 已知问题**不在此重写**（见 `P3_MEMORY_WORKLOG.md` 第十九节 19.4 / 19.5 / 19.7）。

## 0. 口径（先读这一段，否则表格会误读）

| 项 | 口径 |
|---|---|
| **测试** | 三档，**核到具体文件**：`有单测` = `tests/unit/*.py` 里存在直接 import 该模块并断言其行为的用例；`只有集成测` = 仅被 `tests/acceptance/*` 或别的模块的测试顺带构造/消费（无指向它自身的断言）；`无` = 仓库内没有任何测试触碰。 |
| **阻塞心跳？** | 只指**灵魂 1Hz 主循环**（`soul/heartbeat.py::run`）内的等待。`0s` = 该段只做本地 I/O 或纯 CPU，**不等待任何外部服务**；`最长 N s` = 单拍内可能等待外部服务的**硬上限**（由 `core/config.py` 的超时 × 轮数推出）。身体进程（5s 心跳）单列在 §6，不计入本列。 |
| **状态** | `真机运行` = 已接线且被运行中的进程装载；`已实现·未接线` = 代码与单测在位，运行时链路尚未调用它。 |

> 「无测试」不等于「不可靠」：本项目多数模块靠**纯函数 + 边界清晰**保证正确性；
> 这一列只回答一件事——**回归时有没有自动化护栏**。

## 1. 灵魂（soul：生命循环）

| 能力 | 状态 | 主要文件 | 测试 | 阻塞心跳？ | 当前限制 |
|---|---|---|---|---|---|
| 心跳主循环 1Hz（同步身体 → 大脑 → 感受路径 → 落盘 → 离线生活 → 表达 → 记忆维护） | 真机运行 | `soul/heartbeat.py::run` | 有单测 `test_heartbeat.py`、`test_dual_heartbeat.py`、`test_retention.py` | **自身即心跳**；单拍最长 ≈ 下面「表达服务」那行 | 表达段在拍内 `await`（F1）——本轮 1.3 改的正是这里 |
| 大脑循环 / 欲望系统 / 6 维感受 | 真机运行 | `soul/brain.py`、`soul/desire.py`、`soul/dimensions.py` | 有单测 `test_brain.py`、`test_desire.py`；集成 `test_p1_gate.py`；**新增 T1 门禁 `test_t1_cancel.py`** | 0s（纯 CPU 同步） | **好奇心已接线**（由记忆缺口强度 `memory_gap` 直接驱动，阶段 5 第一批）：原 `has_event=False` 字面量 + 上限 0.1 的布尔占位公式已消除 |
| 表达服务（触发判断 → 构造指令 → 记忆/身份注入 → LLM → 校验 → TTS → 落库） | 真机运行 | `soul/expression_service.py` | 有单测 `test_expression_service_memory.py`；集成 `test_p2_gate.py` | **最长 ~185s**（LLM 主声 3 轮工具回合 90s + 次声 30s + TTS 60s + VRAM 采样 5s） | 单拍内串行等待外部服务（F2）；本轮 1.1~1.3 拆出 worker |
| 表达触发与词汇许可（`build_expression` / 词表） | 真机运行 | `soul/expression.py`、`soul/words.py` | 有单测 `test_expression_pipeline.py`；集成 `test_p2_gate.py` | 0s（纯函数） | — |
| 离世内部生活（ALONE / BODY_AWAY 的自我叙事念头） | 真机运行 | `soul/away_life.py` | 有单测 `test_away_life.py` | 0s（念头落库走本地写队列） | 念头写入 `await submit` 仍在拍内（本地，毫秒级） |
| 难受检测（资源高压 → 降频 0.5Hz + distress 事件） | 真机运行 | `soul/distress.py` | 有单测 `test_distress.py`；集成 `test_p0_gate.py` | 0s | 资源样本由身体进程 5s 采一次，灵魂只读缓存（F11） |
| 灵魂进程装配与优雅关闭 | 真机运行 | `soul/main.py` | 无 | 进程级（非拍内） | 关闭序 = 停心跳 → 排空写队列 → 关连接；**本轮 1.3 要插入「先停 worker」** |

## 2. 记忆（memory）

| 能力 | 状态 | 主要文件 | 测试 | 阻塞心跳？ | 当前限制 |
|---|---|---|---|---|---|
| 记忆写入（用户输入 → 一次经历 + 修正/覆盖） | 真机运行 | `soul/heartbeat.py` L253-279、`core/state_store.py::add_memory` | 有单测 `test_state_store.py`、`test_heartbeat.py` | 0s（本地写队列） | 本轮**一字不动**（D2 裁决：落库留心跳） |
| 生命周期维护（晋升 / 索引衰减 / 降级 / 自动保护） | 真机运行 | `soul/heartbeat.py::_maintain_memories`、`memory/promote.py`、`memory/decay.py` | 有单测 `test_promote.py`、`test_decay.py`；集成 `test_p3_gate.py` | 0s（每 300 拍一次，本地 SQLite） | 全表 iterate（真机 401 行）+ 逐条 UPDATE，**耗时未实测** |
| 检索（话语路径 `hooks` / 感受路径 `recall`） | 真机运行 | `memory/retrieve.py` | 有单测 `test_retrieve.py`、`test_retention.py`；集成 `test_p3_gate.py` | 0s | 命中即 `touch_memory`（写队列）；`_PROBE_MARKERS` 含「吗/呢/？」⇒ 无关问句也可能带出 3 条 hooks（19.4 缺点④） |
| 修正/覆盖（同话题新事实取代旧事实） | 真机运行 | `memory/supersede.py` | 有单测 `test_supersede.py`、`test_similarity.py` | 0s | Jaccard 0.4 不动（误判代价不对称原则） |
| 同话题判据单一入口（`same_event` / `query_coverage`） | 真机运行 | `memory/similarity.py` | 有单测 `test_similarity.py` | 0s（纯函数） | — |
| 评分（重要性 / 情绪强度 / 内容显著度） | 真机运行 | `memory/scorer.py` | 有单测 `test_memory.py`、`test_identity.py` | 0s（纯函数） | — |
| 层级与常量（kind / level / source / certainty / retention / claim） | 真机运行 | `memory/levels.py` | 只有集成测（被 10+ 测试当常量源 import，无指向自身的断言） | 0s | 纯常量 + 默认推导函数 |
| 记忆缺口信号（有些事想不起来了） | 真机运行 | `memory/hooks.py` | 有单测 `test_decay.py`；集成 `test_p3_gate.py` | 0s | — |
| 沉淀候选（发现重复模式，递给她认领） | 真机运行 | `memory/sediment.py` | 有单测 `test_sediment.py` | 0s（每 300 拍） | — |
| 做梦（自由联想流，无 LLM） | **已实现·未接线** | `memory/sleep.py` | 有单测 `test_sleep.py`；集成 `test_p3_gate.py` | —（不在心跳路径上） | 纯函数已就绪，运行时尚未调用 |
| 双库底座（WAL + 单写者队列 + 幂等迁移回填） | 真机运行 | `core/state_store.py` | 有单测 `test_state_store.py` | 0s（`to_thread`） | **F6 已修**（写失败不再 `set_result(None)` 伪装成功，而是向上抛）。**F7 已修**（关闭改**哨兵停写者**：`to_thread` 里的 sqlite 调用无法取消，硬 `cancel()` 会与随后的 `close()` 竞态；`cancel()` 降为超时后的最后兜底；关停后拒收新写并抛错） |
| 常驻协程监督器（F6 的对偶件） | 真机运行 | `core/supervisor.py` | 有单测 `test_core.py`（重启 / 取消传播） | 0s | 协程级自愈：单拍或整循环异常不再等于"她僵死"；**进程级看门狗仍未做**（见 §11 #5） |

## 3. 内核（core）

| 能力 | 状态 | 主要文件 | 测试 | 阻塞心跳？ | 当前限制 |
|---|---|---|---|---|---|
| 时间体验 TimeSense（在场/离开/年龄/时段） | 真机运行 | `core/timesense.py` | 有单测 `test_timesense.py`；集成 `test_p0_gate.py` | 0s | — |
| 模式派生 PRESENT / ALONE / BODY_AWAY | 真机运行 | `core/mode.py` | 有单测 `test_mode.py`；集成 `test_p0_gate.py` | 0s | — |
| 时钟（真实 / 模拟） | 真机运行 | `core/clock.py` | 无（`SimulatedClock` 被多份单测当测试替身用，无自身断言） | 0s | — |
| 检查点 / 回滚 / 紧急冻结 | 真机运行 | `core/checkpoint.py` | 有单测 `test_checkpoint.py`；集成 `test_p0_gate.py` | 0s（拍内只有 `is_frozen()` 读文件） | 快照创建不在心跳路径内调用 |
| 配置（路径 / 间隔 / 超时 / 熔断阈值） | 真机运行 | `core/config.py` | 有单测 `test_core.py`、`test_llm_chain.py` | 0s | 本轮新增 `EXPRESS_TIMEOUT_S`（10s）与队列容量（2） |
| 日志（双通道） | 真机运行 | `core/log.py` | 有单测 `test_core.py` | 0s | — |

## 4. 表达生成（llm）

| 能力 | 状态 | 主要文件 | 测试 | 阻塞心跳？ | 当前限制 |
|---|---|---|---|---|---|
| 三级降级调度（主声 → 次声 → 微声，每级过校验） | 真机运行 | `llm/chain.py` | 有单测 `test_llm_chain.py`；集成 `test_p2_gate.py` | 上限即「表达服务」那行 | 降级只处理**失败**，不处理**慢**（F3） |
| 主声 DeepSeek（含 5 个工具的 function calling） | 真机运行 | `llm/deepseek.py` | 有单测 `test_llm_chain.py`、`test_identity.py` | 单次请求 `llm_main_timeout_s` = 30s；带工具最多 3 轮 ⇒ ~90s | `MAX_TOOL_ROUNDS = 3`；HTTP 走 `to_thread`（不占事件循环，但仍占拍） |
| 微声（无 LLM 的结构化呓语） | 真机运行 | `llm/micro.py` | 有单测 `test_llm_chain.py` | 0s | 身份段只「带着」不进呓语（S4 拍板） |
| 输出校验器（T2 防线） | 真机运行 | `llm/validator.py` | 有单测 `test_expression_pipeline.py`；集成 `test_p2_gate.py` | 0s | — |
| 身份段取数 + 出生设定种子 | 真机运行 | `llm/identity.py` | 有单测 `test_identity.py`、`test_memory_view.py` | 0s | 契约「以库为准」（S5） |
| 表达链装配（主声 / 次声 + 降级回调） | 真机运行 | `llm/__init__.py` | 有单测 `test_llm_chain.py` | 0s | `llm_fallback_*` 已接线（S6） |

## 5. 出声（tts）

| 能力 | 状态 | 主要文件 | 测试 | 阻塞心跳？ | 当前限制 |
|---|---|---|---|---|---|
| 出声调度（熔断 → 缓存 → 合成，失败只出文本） | 真机运行 | `tts/chain.py` | 有单测 `test_tts.py`；集成 `test_p2_gate.py` | 最长 ~65s（`tts_timeout_s` 60s + VRAM 采样 5s） | 熔断查询在 `await` 链内做**同步**采样（F4，本轮 1.4） |
| GPT-SoVITS 后端（HTTP GET /tts，走 `to_thread`） | 真机运行 | `tts/backend.py` | 无（`test_tts.py` 只测 chain/cache/breaker，合成器用替身） | `tts_timeout_s` = 60s | 依赖外部服务（127.0.0.1:9880） |
| VRAM 熔断 | 真机运行 | `tts/breaker.py` | 有单测 `test_tts.py`；集成 `test_p2_gate.py` | **最长 5s**（`subprocess.run(timeout=5)` 同步调用，F4） | 本轮 1.4 挪进 `asyncio.to_thread` |
| 磁盘缓存池 | 真机运行 | `tts/cache.py` | 有单测 `test_tts.py` | 0s（本地文件） | — |
| 出声链装配 | 真机运行 | `tts/__init__.py` | 无 | 0s | — |

## 6. 身体（body：独立进程，5s 心跳；不计入灵魂阻塞列）

| 能力 | 状态 | 主要文件 | 测试 | 阻塞心跳？ | 当前限制 |
|---|---|---|---|---|---|
| 桌宠窗口（无边框 + 气泡 + 右键菜单 + 打字交互） | 真机运行 | `body/pet.py` | 有单测 `test_pet.py` | 0s（独立进程） | F8：交互走 KV 单键覆盖，连打两句只剩后一句（本轮不做） |
| 身体心跳 5s（在场上报 + 资源快照） | 真机运行 | `body/heartbeat.py` | 有单测 `test_dual_heartbeat.py`；集成 `test_p0_gate.py` | 0s | — |
| 资源采样（psutil CPU / 内存） | 真机运行 | `body/resource.py` | 无（`injectable_sampler` 只供别的测试注入，本体无断言） | 0s（`interval=0`，非阻塞） | — |
| 身体进程装配与关闭 | 真机运行 | `body/main.py` | 无 | 进程级 | — |

## 7. 协议与工具

| 能力 | 状态 | 主要文件 | 测试 | 阻塞心跳？ | 当前限制 |
|---|---|---|---|---|---|
| 快照构造（心拍 payload 的单一出口） | 真机运行 | `protocol/snapshots.py` | 只有集成测（`test_pet.py` / `test_p0_gate.py` 构造并消费它） | 0s | — |
| 记忆观测窗（PySide6，只读 SELECT） | 真机运行（独立进程） | `tools/memory_view.py` | 有单测 `test_memory_view.py` | 不在灵魂进程内 | 观测工具是独立进程，**改了要看新列须重开**（如「簇」列需在 A2 提交后重开） |

## 8. 本轮（第二十节）新增项 —— 落地状态（已回填）

| 能力 | 状态 | 主要文件 | 测试 | 阻塞心跳？ | 当前限制 |
|---|---|---|---|---|---|
| 表达任务/结果数据模型（`ExpressionJob` / `ExpressionResult`） | 已实现·未接线（1.1，`32fcfb8`） | `soul/expression_types.py` | 有单测 `test_expression_types.py`（11 项） | — | 持 `BrainOutput` 引用，不新造快照 |
| 表达 worker（有界队列 2 + 偏序判过期 + 超时取消） | 已实现·未接线（1.2，`dc87fba`） | `soul/expression_worker.py` | 有单测 `test_expression_worker.py`（11 项） | 0s（`submit` 非阻塞） | 内部念头遇满即丢（D1）；用户输入永不丢 |
| 心跳接线（`submit` + 每拍 drain 落库） | 已提交·待评审（1.3，`77ee827`） | `soul/heartbeat.py`、`soul/main.py` | 无（1.3 单测已删） | 由「最长 ~185s」降为 **0s** | 落库晚 ≤1 次表达耗时（D2 已知代价） |
| VRAM 采样挪线程 | ✅ **已落地**（阶段 5 第八批） | `tts/breaker.py` | 沿用 `test_tts.py` | 5s → 0s（`asyncio.to_thread`） | `can_speak()` 改为 async；采样统一走 `core/vram.py` |

## 9. 维护规则

1. **每步落地后回填**：第二十节拆步（0.x / 1.x）每完成一步，更新受影响行的「状态 / 阻塞心跳？ / 当前限制」。
2. **新增模块即补行**：新文件加入 `src/elysia/` 时，按上表口径补一行（测试列必须核到具体文件）。
3. **超时数字变了就同步**：`core/config.py` 的 `*_timeout_s` / 轮数常量变更时，同步 §4 / §5 的「最长 N s」。
4. **本表不重复设计理由**：设计在 `P3_MEMORY_WORKLOG.md` 第二十节，落地证据在第二十一节。

## 10. 失效引用与仓库外依赖登记

> 建立于 2026-10-14（清理审计）。原则：**只登记，不重写历史**——
> `CHANGELOG.md` 与 `P3_MEMORY_WORKLOG.md` 是**历史记录**，其中引用的下列路径保持原样；
> 读到那些引用时，**以本表为准**。活文档（README / 本文档 / `TECHNICAL_DESIGN.md`）里的引用已直接修正。

| 被引用物 | 原位置 | 现状（2026-10-14 实测） | 处置 |
|---|---|---|---|
| `verify_integrity.py` | 工作区外 `工具脚本/scripts/` | **本仓库不存在，且从未进过 git**；worklog 中 10 处「完整性 PASS」据此不可复核 | **待重建**（本轮不动；重建后回填本行） |
| `IDEAS.md` | 工作区外 `灵感管理/` | 该目录已移出 ⇒ 铁律一失去落地处 | **已重建**为仓库根 `IDEAS.md` |
| `项目元信息/开发日志.md` | 工作区外 `项目元信息/` | 已移出（`CHANGELOG.md` 头部已说明） | 保持引用；`CHANGELOG.md` 为代码库唯一变更日志 |
| `项目元信息/PROJECT_MANIFEST.md` | 同上 | 已移出 | 不再维护；文件清单以 `git ls-files` 为准 |
| `ELECTRONIC_LIFE_BLUEPRINT.md`、`OPENCLAW_RESEARCH.md` | `IMPLEMENTATION_ROADMAP.md` 头部「基于」 | 不在本仓库；实位于旧工程 `Elysia_Project/docs/planning/` | 已在路线图头部加位置注 |
| `建议.docx` | 外部素材（用户上传） | 不在仓库 | 历史叙述，无需处置 |

**审计口径**（可复核）：`docs/` 内 markdown 相对链接全量扫描 + 活文档反引号路径全量比对文件系统。
本轮唯一修好的断链 = `P3_MEMORY_WORKLOG.md` §19.4 缺点 6 的两条 `file:///a:/WorkPlace/...` 绝对链接。

## 11. 待补清单（「已写成标准，但代码里没有」）

> 建立于 2026-10-14（阶段 3 内容校正）。这些项**在文档里已被当成标准写死**，但**代码中并不存在**。
> 按用户裁决：本轮**只标注、不实现**（阶段 3 = 文档校正），实现排在阶段 4/5。

| # | 项 | 文档出处 | 实测现状 | 影响 | 建议落点 |
|---|---|---|---|---|---|
| 1 | 启动时 `PRAGMA integrity_check` + 损坏告警 | `PROJECT_ENGINEERING_STANDARD.md` §3.2 | **全代码库无此调用** | 库损坏无法尽早发现 | 阶段 4 |
| 2 | 定期 `VACUUM` | `ADR/002` §后果 | **从未落地** | `heartbeats` 按 1 Hz 线性增长且无保留策略（约 8.6 万行/天） | 阶段 4（需先拍板"数据永不删除"口径） |
| 3 | 备份**定时器**（文档原写"每日自动"） | `STANDARD` §3.2 ／ `ADR/002` ／ README | `backup.ps1` 是**纯手动**脚本 | 实际不会自动备份 | 阶段 4 |
| 4 | 备份**原子性** | `ADR/002` §后果（断言 robocopy"天然覆盖"WAL） | 非原子；已实测日志会漏进备份 | 备份可能不一致 | 阶段 4（先停她再备，或改用 `.backup` / `VACUUM INTO`） |
| 5 | 进程监督 / 崩溃自动重启 | `STANDARD` §3.3 | **协程级已做**（`core/supervisor.py`，阶段 5 第四批）；进程级看门狗 / 计划任务仍无 | 崩了不会自愈（19.4 缺点 1）——**该缺点现已解决一半**：心跳协程意外退出会被拉起，但**整个进程**被杀仍需人工 | 进程级部分仍待做 |
| 6 | CI（`.github/workflows`） | `STANDARD` §2.4「CI/本地脚本」 | `.github` 不存在 | 门禁仅在本地 | 阶段 4 |
| 7 | `verify_integrity.py` 重建 | worklog **10 处**引用 | 文件不存在、从未入库 | 那些「完整性 PASS」不可复核 | 待定（见 §10） |

> **未接线模块进度**：`decay.retrieve_latency_ms` 与 `levels.detail_level` **已接线**（第六批）；
> `memory/sleep.py`（做梦）**已接线**（第七批）；见 `P3_MEMORY.md` §六。
> 4 个无产生者意图中**三个已接**（主动问候＝边沿触发 / 思念＝`miss` 高位 / 好奇提问＝缺口高位预报，第七批）。
> **「拒绝」有意推迟**：它属 P4「工具化检测」，而当前唯一的输入是桌宠文本框——
> **没有"指令"可拒绝**，硬接只会让模板空转（待 P5 有真实外部任务时再做）。

## 12. 本轮新发现的缺陷登记

> 与 §11 的区别：§11 是"文档写了、代码没有"；本节是**代码里的活 bug**，
> 由阶段 5 建立 T1 取消测试（`tests/acceptance/test_t1_cancel.py`）时**由测试暴露**。
> 同样先登记、后定夺，不擅自改行为语义。

| # | 缺陷 | 位置 | 症状 | 影响 | 状态 |
|---|---|---|---|---|---|
| 1 | **`BrainOutput` 持有活引用（别名缺陷）** | `desire.py:155`（`step()` 返回 `self._state`）；`dimensions.py:85`（`compute()` 返回 `self._current`） | `BrainOutput.desire` / `.feelings` 与系统内部对象**是同一个对象**，心跳每拍原地改写 | `ExpressionJob` 把 `BrainOutput` 交给 worker **异步执行**（可能数拍之后）⇒ 她说出口的话消费的是**执行那一刻**的状态，而非"决定开口那一刻"的状态；与 1.3 改用 `created_ts` 锚定决策时刻的设计意图自相矛盾 | ✅ **已修**（`brain.py` 构造 `BrainOutput` 时对 `desire` / `feelings` 取 `.copy()`，使其成为**真快照**；`test_t1_cancel.py::test_brain_output_is_snapshot_not_live_reference` 加锁——已验证：还原旧写法该用例**失败**） |
| 2 | **受保护词授权环境是字面量**（T2 防线半死） | `heartbeat.py::_expression_env` 原写 `cpu_pct: 0.0` / `q_len: 0` / `memory_gap: 0` | 谓词永远拿到 0 ⇒「难受」「累」「好像忘了什么」**永远无法授权** | T2 的词汇硬约束对这三个词形同虚设（她永远说不出"我难受""我好像忘了什么"） | ✅ **已修**（阶段 5 第一批：接活值 `cpu_pct` / `q_len` / `memory_gap`，并由 `test_t1_cancel.py` 加锁） |
| 3 | **`memory_gap` 谓词量纲错误** | `words.py` 原写 `_num(env,"memory_gap",0) > 10` | 缺口强度是 **0-1**，阈值却是 10 ⇒ 即便接了活值也不可说 | 「好像忘了什么」永久禁用 | ✅ **已修**（阈值改 `> 0.4`，与好奇心的消费阈值同源） |
| 4 | **`q_len` 谓词量程错误** | `words.py` 原写 `_num(env,"q_len",0) > 10` | 表达队列容量 2、最大深度 3，阈值 10 永不成立 | 「累」只剩 VRAM 一条路，而 VRAM 那条…见 #5 | ✅ **已修**（阈值改 `>= 2`，语义＝"队列有积压"） |
| 5 | **VRAM 三层次缺陷**（阈值全高于本机显存总量） | ① `body/resource.py::ResourceSampler` **不采 VRAM**（只有 `cpu_percent` / `mem_percent`）⇒ `heartbeat._latest_vrram_mb()` 读 `_latest_resources["vrram_mb"]` **恒返回 0.0**；② `words.py`「累」写 `vrram_mb > 8510`，而路线图 §7.6 写的是 **VRAM>85%（百分比）**；③ `core/config.py` 的 `llm_vram_threshold_mb = 9510` **从未被任何代码读取**；`tts_vram_threshold_mb = 10240` 只被 TTS 熔断读 | 本机显存总量 **6144 MiB** ⇒ 三个阈值一律永不触发 | ① `state_brief["vrram_mb"]` **恒为假值 0**（她汇报给 LLM 的显存占用一直是错的）；②「累」的 VRAM 通路**双重死亡**（值恒 0 ＋ 阈值高于总量）；③ TTS 熔断形同虚设 | ⏸→✅ **已修**（阶段 5 第八批，照上面已备方案落地）：新建 `core/vram.py`（nvidia-smi **单一出处**，采 used+total → 百分比）；`body/resource.py` 采 `vram_pct` / `vrram_mb`（**采不到就不写这个键**，不编造 0）；TTS 熔断与「累」共用 `settings.vram_threshold_pct`（**单一出处**）；删除从未读取的 `llm_vram_threshold_mb`。真机实测：`VramSample(used_mb=0, total_mb=6144)`，空闲时 `vram_busy=False` |
| 6 | **`error_burst` 无真实来源**（已修） | 原 `heartbeat.py::_expression_env` 写死 `error_burst: 0` | 谓词 `> 1` 永不成立 ⇒「痛」永久禁用。**根因链**：真正代表"异常"的**写失败当时被 F6 吞掉**（`state_store.py:172` 把失败 `set_result(None)` 伪装成成功），而现成的 LLM/TTS 降级事件语义偏宽（**未配 API key 时每次开口都 `_degrade("micro")`**，用它计数会让「痛」离线常驻） | 「痛」永久禁用 | ✅ **已修**（阶段 5 第五批，**F6 修好后才有真实异常可数**）：新增 `SoulHeartbeat.record_error()` / `_error_burst()`，`ERROR_BURST_WINDOW_S = 1800` 窗口内计数，两个喂入口＝**表达任务失败/超时**（`persist_expressions`）＋**常驻循环异常**（`supervisor` 的 `on_error` 回调）。**有意不含优雅降级**（那是"失声不失活"，不是痛） |
| 7 | **`llm_vram_threshold_mb` 是死配置** | `core/config.py:58`（`= 9510`），且 `.env.example` 已文档化 | **全代码库从未读取**——`build_llm_chain` 的 docstring 称"VRAM 熔断阈值由调用方依据资源判断"，但**没有任何调用方判断** | 用户在 `.env` 里设它**不产生任何效果**：属"文档承诺了不存在的功能" | ✅ **已修**（第八批）：该键**已删除**——主声/次声的挂载只看 key 与 endpoint 是否配置，与显存无关；`build_llm_chain` 的 docstring 与 `.env.example` 一并更正，不再"承诺不存在的功能" |
| 8 | **「困」的时段宽于设计** | `words.py`「困」用 `is_night`；`core/timesense.py:87-100` 的 `day_phase` = dawn(5-9) / day(9-17) / dusk(17-21) / night(21-5) | 路线图 §7.6 写「困 ↔ 凌晨 **2-5 点** + 低 TR」，实现是 **21:00–05:00** | 可达、**非死分支**，但偏宽：她 21 点就会说"困" | ✅ **已修**（第八批）：新增 `TimeSense.hour()` 与 `summary["hour"]` 作精确取数口，谓词改为 `2 <= hour < 5 and tr < 45`（环境键 `is_night` 由 `hour` 取代——`day_phase` 只到"夜"这一档，不够用） |

**发现经过（照实记）**：#1 不是读代码发现的，而是写 T1 端到端用例时连续调用两次
`loop.step()` 比较好奇心，两次读到**同一个值**——**"取消测试"的跨拍比较把它逼了出来**。
#2–#4 则是顺着 #1 的"同一族"往下查（凡"声明了却永不触发"的分支）时找到的：
`_expression_env` 与 `has_event=False` 是**同一类缺陷**。

**影响量级（#1）**：worker 单飞 + 队列容量 2，通常只延迟一次 LLM 调用的时长（数秒）；
欲望有恢复项、漂移慢，故实际偏差有限；但它是**非确定性的**（取决于队列与 LLM 时序），
且让 `expression_log` 的取证行"时间戳=决策时刻、状态=执行时刻"口径不一致。
