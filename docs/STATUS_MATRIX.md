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
| 大脑循环 / 欲望系统 / 6 维感受 | 真机运行 | `soul/brain.py`、`soul/desire.py`、`soul/dimensions.py` | 有单测 `test_brain.py`、`test_desire.py`；集成 `test_p1_gate.py` | 0s（纯 CPU 同步） | `has_event=False` 是字面量 ⇒ 好奇心维恒 0（F9，本轮不做） |
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
| 双库底座（WAL + 单写者队列 + 幂等迁移回填） | 真机运行 | `core/state_store.py` | 有单测 `test_state_store.py` | 0s（`to_thread`） | F6 写失败 `set_result(None)` 伪装成功、F7 `writer.cancel()` 主手段 —— 属阶段 4，本轮不动 |

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

## 8. 本轮（第二十节）新增项 —— 落地后回填本表

| 能力 | 状态 | 主要文件 | 测试 | 阻塞心跳？ | 当前限制 |
|---|---|---|---|---|---|
| 表达任务/结果数据模型（`ExpressionJob` / `ExpressionResult`） | **待落地（1.1）** | `soul/expression_types.py`（新） | 待补单测 | — | 计划持 `BrainOutput`，不新造快照 |
| 表达 worker（有界队列 2 + 偏序判过期 + 超时取消） | **待落地（1.2）** | `soul/expression_worker.py`（新） | 待补单测 | 0s（`submit` 非阻塞） | 内部念头遇满即丢（D1）；用户输入永不丢 |
| 心跳接线（`submit` + 每拍 drain 落库） | **待落地（1.3，唯一行为变化点）** | `soul/heartbeat.py`、`soul/main.py` | 待补 acceptance | 由「最长 ~185s」降为 **0s** | 落库晚 ≤1 次表达耗时（D2 已知代价） |
| VRAM 采样挪线程 | **待落地（1.4）** | `tts/breaker.py` | 沿用 `test_tts.py` | 5s → 0s | 独立小改，可单独回退 |

## 9. 维护规则

1. **每步落地后回填**：第二十节拆步（0.x / 1.x）每完成一步，更新受影响行的「状态 / 阻塞心跳？ / 当前限制」。
2. **新增模块即补行**：新文件加入 `src/elysia/` 时，按上表口径补一行（测试列必须核到具体文件）。
3. **超时数字变了就同步**：`core/config.py` 的 `*_timeout_s` / 轮数常量变更时，同步 §4 / §5 的「最长 N s」。
4. **本表不重复设计理由**：设计在 `P3_MEMORY_WORKLOG.md` 第二十节，落地证据在第二十一节。