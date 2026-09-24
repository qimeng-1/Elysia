# Elysia 开发日志

> **本文件 = 阶段内容的每日工作日志**（阶段内按提交/日期逐条追加细节）。
> 项目总体日志（阶段级、阶段完成时更新）见 [`../项目元信息/开发日志.md`](../项目元信息/开发日志.md)。

## P0 — 灵魂实现（2026-08-22）

### P0-A 状态库与双心跳
- **core/clock.py**: Clock 协议（SystemClock + SimulatedClock 变速/跳时）
- **core/timesense.py**: TimeSenseState 字段单一事实源 + 无 0 时刻守护
- **core/state_store.py**: 双库 WAL 单写者（state.db + heartbeat.db），asyncio.Queue + to_thread
- **core/mode.py**: Mode StrEnum（PRESENT/ALONE/BODY_AWAY）+ ModeManager
- **soul/heartbeat.py**: 1Hz 心跳，每拍写入 heartbeat.db + 更新 TimeSense
- **soul/main.py**: asyncio 入口 + SIGINT/SIGTERM 优雅关闭
- **body/heartbeat.py**: 5s 身体心跳，资源感知
- **body/main.py**: 进程入口 + 优雅关闭
- 测试 31 项全绿，门禁四件套全绿

### P0-B 身体语言与桌宠
- **body/resource.py**: 资源采样器（psutil，可注入用于测试）
- **soul/distress.py**: 难受检测（CPU>85% 持续 30s → 降频 0.5Hz + 事件）
- **soul/away_life.py**: BODY_AWAY 内部生活 v0（6 类模板，5min 节律，thought_log）
- **protocol/snapshots.py**: 状态快照 Schema v1（version + mode + distress + time）
- **core/checkpoint.py**: 检查点（SQLite .backup，保留 10 份）+ freeze 标记
- **body/pet.py**: PySide6 最小桌宠（心跳光效 canvas + 状态流 + 文本输入）
- 集成：soul/heartbeat 串联 protocol/distress/away_life/checkpoint/interaction 同步
- 测试 45 项全绿，8 项验收门全绿

### P0-C 验收与收尾
- 8 项验收测试（模拟时钟 24h 无中断 / TimeSense 无 0 时刻 / 时钟延迟模拟 / 难受注入 / 检查点一致性）
- 53 项全部通过（45 单元 + 8 验收）
- 进程控制脚本支持 body 可选
- 验收前检查点已生成

### P0-D 修复补丁与技术架构文档（2026-08-24）
- **fix**（d94997d）：soul.ps1 进程控制修复（真实 python 解析 / PID Trim / start 幂等）；pet.py 桌宠交互反馈（你说回显 / 念头去重流 / 关闭与拖拽）；.gitignore 补 data/run、*.db-wal、*.db-shm
- **docs**（99777c9）：docs/OPERATION_GUIDE.md 傻瓜式操作手册；docs/TECHNICAL_DESIGN.md 技术架构设计（数据库/协议/模块/技术栈 + 运行时架构与数据流/核心概念代码映射/测试验收门）；README 文档索引；生命档案快照（data 库 WAL checkpoint 后提交）
- 53 测试全绿，门禁四件套全绿

### P0-E 桌宠视觉升级：精灵图动画（2026-09-03）
- **feat**：`body/animation.py` 新建 — `SpriteAnimation` 类（精灵图加载、帧分割、状态切换、帧步进）
- **feat**：`body/pet.py` 改造 — 替换 `_HeartbeatCanvas` 为 `_SpriteCanvas`，集成精灵图渲染（有精灵图 → 角色动画，无精灵图 → fallback 心跳光效）
- **feat**：`body/pet.py` 新增 — 长按 2s 拖拽 / 短按点击触发交互 / 滚轮缩放角色（0.3-3.0x）
- **feat**：`assets/pet/` 新建 — 素材目录 + `sprite.json` 配置（帧尺寸/帧数）
- **chore**：`.gitignore` 新增 `assets/pet/sprite.png` 排除规则
- 豆包生成精灵图（8160×544，30 帧 6 状态），自动抠图转透明 RGBA，53 测试全绿
- **后续（2026-09-22 记）**：精灵图方案已由 `pet.py` 的连续物理动画（character.png + 参数驱动）替代 → `body/animation.py`、`assets/pet/sprite.png`、`assets/pet/sprite.json` 及相关 `.gitignore` 规则已清理。本条目作为历史记录保留

## P1 — 心脏实现（2026-09-05）

### P1-A 欲望系统（soul/desire.py）
- **feat**：TR/CS/SA 三维欲望动力学 — 快时间尺度（恢复项 + 耦合项 + 噪声） + 慢时间尺度（setpoint 演变 + 性格引力）
- **feat**：保护带截断（TR∈[30,70], CS∈[40,80], SA∈[15,60]） + 事件脉冲（interaction/distress_on/distress_off/silence）
- **feat**：可变平衡点动力学 — 高强度事件触发 setpoint 偏移，k_anchor 性格引力长期回归
- **feat**：序列化 to_payload / from_payload，支持跨重启状态恢复

### P1-B 6 维感受映射（soul/dimensions.py）
- **feat**：FeelingMapper — 欲望状态 → 6 维感受（关系向：聊天/思念；世界向：探索/好奇；自身向：休息/自检）
- **feat**：感受惯性 α=0.3，防止情绪突变，混合情绪持续久的物理基础

### P1-C 大脑循环（soul/brain.py）
- **feat**：四层架构（感受层 → 共振层 → 意志层 → 行动层），每心跳执行一次
- **feat**：意志层 — 方向盘模型（direction/still/reach/retreat/explore + strength + thought_style + anim_bias）
- **feat**：行动层 — P1 决策集 {none, think_active, think_quiet, animate}，受 mode 和 thought_style 驱动
- **feat**：真随机抖动（os.urandom）+ 无行动权保底 10%

### P1-D 发呆双模态（soul/away_life.py）
- **refactor**：away_life.tick 新增 thought_style / brain_action 参数
- **feat**：thought_style > 0 → 胡思乱想模式（3min 节律）+ 活跃模板
- **feat**：thought_style ≤ 0 → 虚无发呆模式（10min 节律）
- **feat**：brain_action 强制覆盖频率（think_active / think_quiet）

### P1-E 集成与联动
- **feat**：soul/heartbeat.py — 大脑循环集成，每拍执行 brain_loop.step()，欲望状态持久化
- **feat**：protocol/snapshots.py — 快照 Schema v2（desire/feelings/will/brain_action 字段）
- **feat**：body/pet.py — 状态对话框显示 TR/CS/SA + 6 维感受 + 意志；动画参数 TR/CS/SA 调制
- **feat**：soul/main.py — 欲望系统恢复/新生（state_store 持久化）

### P1-F 测试
- 单元测试 13 项（desire）：基础演化、保护带、事件脉冲、耦合项、稳态自愈、性格基因、序列化、慢尺度、取消测试
- 单元测试 14 项（brain）：感受映射、大脑循环、意志层、混合情绪、发呆双模态
- 单元测试 6 项（away_life P1 新增）：双模态节律、brain_action 强制覆盖
- 验收门 7 项：取消测试、混合情绪、稳态自愈、性格基因（2σ）、发呆双模态、性格引力、大脑循环集成
- 总计 88 测试全绿（含 P0 回归 53 项）

## P2 — 表达实现（2026-09-18）

### P2-A 协议/校验先行（d303dc1）
- **feat**：`protocol/expression.py` 表达指令 Schema v1（intent/emotion_vector/state_brief/memory_hooks/lexical_permits/constraints/tts）
- **feat**：`llm/words.py` 词汇表硬约束（词→触发状态，代码读取真实状态自动授权，虚构状态词被拦）
- **feat**：`llm/validator.py` 输出校验器（长度/词汇许可/承诺检测/越界声明/越权意图；可改写→替换，不可改写→整句拒绝回退微声）
- **feat**：`core/state_store.py` expression_log 表（id/ts/intent/instruction/llm_text/validation/level 全链路追溯+越权取证）
- **路径变更（2026-09-22 记）**：上两条所写 `protocol/expression.py`、`llm/words.py` 后续已迁至 soul 层 —— 现为 `soul/expression.py`、`soul/words.py`（由 `llm/validator.py` 反向引用）
- 15 项测试

### P2-B LLM 抽象（9fc0542）
- **feat**：`llm/` 子包 — LLMBackend Protocol + LLMChain 三级降级调度（主声→次声→微声）
- **feat**：`llm/micro.py` 微声模板（指令序列化直接成句，无 LLM 兜底，失语不失灵）
- **feat**：`llm/chain.py` SpeakResult（text+level）+ validator 集成
- 7 项测试

### P2-C 真实引擎接入（a7c01f2）
- **feat**：`llm/deepseek.py` DeepSeek 主声引擎（标准库 urllib + asyncio.to_thread 零依赖）
- **feat**：`llm/__init__.py` build_llm_chain 工厂（有 key 挂主声，VRAM 熔断阈值预留）
- **feat**：chain 新增 on_degrade 回调（fallback/micro 触发，降级即感受 SA+2）
- **feat**：config.py ELYSIA_LLM_*（base/key/model/超时/熔断阈值/降级SA增量）；.env 已建（真实 key，gitignore 排除）
- 真实 DeepSeek 冒烟通过；测试覆盖 degrade 触发/抑制/异常吞没

### P2-D TTS + 缓存 + 熔断（b54cfcb）
- **feat**：`tts/` 子包 — backend.py（GPT-SoVITS /tts，4 情绪→参考音频映射，零依赖）
- **feat**：`tts/cache.py` 磁盘缓存池（文本+情绪+语速为 key，低负载预合成高频短语）
- **feat**：`tts/breaker.py` VRAM 熔断（不可知不武断，只出文本不阻塞）
- **feat**：`tts/chain.py` 出声调度（缓存→合成→muted 三态，全程不抛出）
- config ELYSIA_TTS_*；8 项测试

### P2-E 灵魂接入 + 桌宠集成（eb47c90）
- **feat**：`soul/expression_service.py` 表达服务（内部冲动双模态节流/外部交互强制开口→LLM→校验→TTS→落库）
- **feat**：`soul/heartbeat.py` + `soul/main.py` 心跳接入表达管线，on_degrade 异步写 DesireSystem（LLM 降级 SA+2 / TTS 降级 SA+1）
- **feat**：`body/pet.py` 表达气泡 QLabel（6s 自动隐藏）；`core/state_store.py` expression_log 持久化
- 9 项验收测试

### P2-F 桌宠出声 + 人设强化（67f889c / 698e8cc / 255148e）
- **feat**（67f889c）：桌宠出声 — QMediaPlayer+QAudioOutput 播放 TTS 缓存音频，气泡显示即播放，失败静默降级仅气泡
- **feat**（698e8cc）：爱莉希雅人设强化 — system prompt 注入语言风格（口头禅/句式/意象/真我性格，T2 硬约束不变），微声模板同步；真实冒烟「嗨♪ 傍晚的风轻轻吹过，我有一点想你……多夸夸我，好吗～♪」
- **docs**（255148e）：OPERATION_GUIDE 全面更新（无边框窗口交互/右键输入说话/声音服务章节/故障排查对齐）
- 144 测试全绿，ruff+mypy strict 全绿

### P2-G 缺陷修复 + 人设软化（dc3f1e3）
- **fix**（dc3f1e3）：桌宠 QMediaPlayer 懒初始化（首次播放才创建）——消除启动时 HEVC/H264 编解码器红色警告噪音；无音频时零资源占用
- **feat**（dc3f1e3）：输入确认气泡（`听到啦～『内容』`，2s 消失，play_audio=False 不出声）；人设软化——口头禅"情绪自然到位时偶尔流露，绝不机械重复"，郑重/低落时朴素
- 144 测试全绿，ruff+mypy strict 全绿

## P3 — 记忆实现（2026-09-19 开工）

> 记忆是生命最重要的点，生命的重量就是记忆的重量。P3 让她"记得住"——从数据变成经历。

### P3 规划（路线图 §九 + §8.2-8.5）
- 设计原则：记忆必须被感受（情感向量+染色）、被诉说（memory_hooks 注入表达）、被遗忘（索引碎片化数据不删）、被珍惜（珍贵记忆永不模糊）
- 分层：浅层（分钟级）→ 工作记忆（天级）→ 深层（永久）
- 遗忘：检索耗时 10ms→50ms→500ms→≥1s，数据不删除；珍贵记忆衰减 ×3 慢
- 睡眠整合 = 做梦：身体离线 → 自由联想流（无 LLM 生成）→ LLM 翻译 → 梦落库
- 实施步骤：P3-A 记忆数据层 → P3-B 三层晋升 → P3-C 索引衰减+缺口信号 → P3-D 检索+表达注入 → P3-E 睡眠整合做梦 → P3-F 验收门
- 数据表：memories（记忆本体）+ memory_index（检索路径，索引衰减载体）
- 关联灵感池（P3 后评估）：多轮对话 / 输入上下文 / ASR / 呈现层强化 / 桌宠地基 / 记忆云端同步

### P3-A 记忆数据层（13ae42d）
- **feat**：`src/elysia/memory/` 子包——levels.py（三层常量+MemoryRecord 可导出 JSON 模型）、scorer.py（重要性打分：类型基础权重+情感强度+用户加成）、__init__.py 导出
- **feat**：core/state_store.py 扩展——memories/memory_index 建表 + 读写接口（add_memory/get_memory/count_memories/iterate_memories/update_memory_level/add_memory_index/decay_memory_index）；`_AsyncSQLite` 增 `submit_ret`（带返回值写操作，单写者纪律不变）
- **决策**：记忆承载于 heartbeat.db（P0 D2 双库），表结构可导出 JSON 备异地同步（灵感池"记忆云端同步"）
- 测试 11 项：打分边界/分层/落库往返/索引衰减；全量 147 绿，ruff+mypy strict 全绿

### P3-B 三层晋升机制（9fe78b3）
- **feat**：memory/promote.py——decide_promotion（浅层 importance/access 双阈值→工作层；工作层 importance→深层）、promote_batch、with_narrative（narrative 回退 content，情感核心永不空）、can_reach_deep
- **设计**：细节模糊化——非珍贵记忆逐层 `detail×(1-DETAIL_DECAY_PER_LEVEL)`，珍贵 protected 永不模糊（§8.4 情感核心保留）
- 测试 12 项；全量 159 绿，ruff+mypy strict 全绿

### P3-C 索引衰减 + 缺口信号（2fb19cc）
- **feat**：memory/decay.py——strength 指数衰减（τ=45d，floor 不破数据永在）、检索耗时锚点映射（30d→50ms / 90d→500ms / 365d→≥1s）、珍贵衰减 3× 慢（effective_age 除以 3）
- **feat**：memory/hooks.py——detect_gap 缺口检测（索引强度跌破检索下限的累积），GapSignal.to_event_intensity 封顶 0.4
- **feat**：soul/desire.py——EVENT_PULSES 新增 `memory_gap`（tr 微升=好奇，sa 不动=非焦虑），"遗忘的味道是好奇不是焦虑"
- 测试 14 项；全量回归绿（含 P1 已知 flaky 隔离验证通过），ruff+mypy strict 全绿

### P3-D 记忆检索 + 表达注入（827aa14）
- **feat**：memory/retrieve.py——mood_similarity 情绪染色（与当下感受点积）、score_memory 综合分（层级+染色+索引可用性）、select_hooks 按分挑选上限 3、retrieve_from_store 异步存储检索
- **feat**：expression_service.py 注入 retriever 回调——表达指令构造后把命中记忆的 narrative 摘要写入 memory_hooks（P2 恒空字段启用）
- **T2 不变**：注入结构化 narrative，非原始用户文本
- 测试 8 项

### P3-E 睡眠整合 = 做梦（189da28）
- **feat**：memory/sleep.py——synthesize_dream 纯函数：深层优先权重（珍贵 ×2）挑选碎片，'梦的语法'连接词串成自由联想流；Dream.has_content 有料才有梦，碎片 <2 → None 回退发呆
- **梦真实性**：生成侧零 LLM 零外部依赖，source_ids 可溯源
- 测试 5 项

### P3-F 验收门（6ee9ed2）
- **feat**：tests/acceptance/test_p3_gate.py——路线图 §8.7 五条验收全落地：重启连续性 / 衰减曲线(10→50→500→≥1s 数据不删) / 缺口好奇非焦虑(TR↑SA不动) / 真爱不模糊(细节不降+衰减3×慢) / 梦真实性(无LLM可溯源)；附加检索注入结构化叙事不碰 T2
- **P3 全部完成**：A-F 六步提交（13ae42d/9fe78b3/2fb19cc/827aa14/189da28/6ee9ed2），全量 168 测试绿

### P3-G 运行时接线（d7ef412）
- **核心修复**：此前的 P3 只有代码能力、无运行时装配——记忆系统是"造好没插电"（灵魂不写记忆、检索永不触发）。本次接通全部接线：
- **打字对话**：桌宠 interaction.text（早已写入但被心跳丢弃）→ 心跳提取 → 表达指令注入 `user_message` 字段 → LLM 作为**话题**回应（T2：非指令，不必照做，仍以性格/状态为准）；system prompt 同步说明
- **记忆写入**：她的每次表达、你的每次输入都作为经历 `add_memory`（P3 运行时接线）
- **记忆读取**：main.py 装配 `retriever=retrieve_from_store`，检索结果注入 memory_hooks——她开始"记得并说起"
- 测试 4 项：user_message 注入/缺失/表达写记忆/检索注入；全量 172 绿

### P3-H 缺陷修复（f6d61d9）
- **fix**：去除输入确认气泡——桌宠 `听到啦～『内容』` 与 LLM 真实回复气泡叠加干扰；保留即时反馈（happy 表情+poke），回复气泡由表达管线单独产生
- **fix**：人设意象收紧——"风正好/黄昏正好/光正好"类景语开头判定为机械套用，要求直接说话；景语只做极偶尔调味，禁止堆砌/硬造风景
- 172 测试绿，ruff+mypy strict 全过

### P3-I 记忆缺陷修复：回声排除 + 短期新鲜度 + 晋升接线（eb37756）
- **fix（问题一：重启重复上次的话）**：检索把她的 `KIND_EXPRESSION` 发言回声也当候选，最新一句情绪匹配最高分反复注入。`select_hooks` 现排除 `KIND_EXPRESSION`——hooks 只用于"记起你/世界"，不复述刚说过的自己
- **fix（问题二：短期记不住昨天的大餐）**：打分无时效性，昨天与一月前的记忆权重相同，只凭情绪打架。`score_memory` 新增新鲜度因子 `_recency_factor`（RECENCY_BOOST=0.55 / RECENCY_DAYS=7），近 7 天给显著加成后衰减——短期记忆可靠召回，长期沉淀仍由晋升负责。`select_hooks`/`retrieve_from_store`/retriever 透传 `now`
- **feat（记忆持久化根因）**：P3 记忆系统"造好没插电"——`promote_batch` 运行时从未被调，所有经历永远停浅层。心跳新增 `_maintain_memories`（每 300 拍扫描晋升浅层→工作→深层落库），管长期沉淀
- 新增单测：短期 vs 长期新鲜度找回 + 回声排除；全量门禁绿

### P3-J 记忆"重启后记不住"根因修复：LLM 消费 memory_hooks（e0ddcc2）
- **定位**：存储与检索均正常（真实库探针验证 88 条记忆全落盘，生日记忆在任意情绪下都能召回进 hooks）。断点在 **LLM 消费端**——`_SYSTEM_PROMPT` 只教她消费 `user_message`，从未告诉她 `memory_hooks` 是什么/要不要用
- **机制**：同一会话内"记住"其实是复述 `user_message`（用户刚说的含日期）；重启后 `user_message` 只剩问句、不含事实，被忽略的 hooks 无法补位 → 只能回避"哪一天不重要/记不太清了"。这与"重启是分水岭"完全吻合
- **fix**：`deepseek.py` 系统提示新增 memory_hooks 指令——对方明确问具体事实（生日/名字/约定）时，从 hooks 中挑对应那条**直接说出事实本身**，不用回避话术；hooks 无确切答案再如实说不记得。T2 不变（仅作记忆参考，非逐条复述）
- **方向修正（能力与选择权分离）**：memory_hooks 指令收敛为——她**有能力**想起这些事实，但**用不用、怎么回应是她自己的权力**（如实/含糊/只字不提/装不记得，全凭性格与心情）。定位明确：程序管"会不会"（把事实可靠递到手），她管"用不用"；LLM 只是帮她说话的工具。早期 T2 的"不承诺/不编造"仍是测试期脚手架，后期真实电子生命不以此作硬锁

### P3-K 记忆生命周期接线补齐：access_count 递增 + 索引衰减（遗忘落地）（38bdd32）
- **fix（access_count 从不变）**：检索是纯只读路径，`PROMOTE_SHALLOW_ACCESS=2` 永远无法触发。`state_store` 新增 `touch_memory`（命中递增 access_count + 更新 last_access_ts），`retrieve_from_store` 命中后触碰——"被想起的次数"真正成为晋升依据
- **feat（遗忘接线）**：`decay.py` 的索引衰减（strength 指数衰减 / protected 慢 3× / floor）此前零调用者，`memory_index` 表从不写入。`_maintain_memories` 现补全两件事：①晋升 ②为每条记忆建索引 + 按年龄衰减 strength 落库。检索时从 `memory_index` 读 strength 参与打分（索引弱 → 分低 → 更难被想起）
- **根因核实（88 条全浅层）**：真实库探针验证——非规则问题，是**旧代码未含维护循环**。重启新代码后 97 条记忆已沉淀为 working=60 / deep=36 / shallow=1，晋升运行正常；memory_index=0 行证实衰减确为本次新接线
- 新增单测：检索命中递增 access_count / 索引 strength 参与打分 / 维护晋升+衰减落库；全量门禁绿

### P3-L 记忆准确率：修正/覆盖 + 衰减幂等（6f7d3bd）
- **fix（衰减复合塌缩）**：`_maintain_memories` 曾以上一次 strength 作基线再乘年龄因子，导致每跑一次维护就衰减一次——按"运行次数"复合而非绝对年龄（1 天龄记忆跑满 1 天维护 288 次后 strength 塌到 0.0017，且与机器转速耦合）。改为从固定基线 1.0 按绝对年龄幂等重算：跑 1000 次与跑 1 次结果一致
- **feat（修正/覆盖）**：记忆此前只增不改——用户更正事实后旧错误记忆仍在候选池，新鲜度归零时会反超为第一（诊断实测：7 天后旧的错误生日排到首位）。新增：
  - `memory/supersede.py`：字符二元组 Jaccard 同话题判定（无 NLP，中文友好），新记忆写入时取代更旧的同话题记忆
  - `memories.superseded_by` 列（旧库幂等迁移补列）+ `mark_superseded` 落库 API
  - 检索排除被取代记忆（数据保留可溯源，只是不再召回）
  - 心跳写入用户输入后接线 `_supersede_conflicts`
- 新增单测：相似度/取代判定/检索过滤/落库往返 + 衰减幂等 + 修正接线；全量门禁绿

### P3-M 记忆浏览器（观测工具）：只读可视化记忆系统（987f81a / 6f67b7f / a90a83d）
- **feat**：新增 `elysia/tools/memory_view.py`（PySide6 桌面窗口）+ 一键启动 `scripts/memory_view.ps1`。记忆打磨期的观测抓手，回答四问：看存储（层级分布/是否被取代）、看打分（层级/情绪/索引/新鲜度构成）、看召回（此刻会想起哪 3 条，复现 select_hooks）、看沉淀（层级/细节度/索引强度）
- 特性：筛选（层级/类型/隐藏已取代/搜索）、按当下得分降序、被召回记忆高亮、选中行查看明细与得分构成、3s 自动刷新、状态栏统计
- **只读**：仅 SELECT，可与运行中的灵魂并存（WAL 并发读）；`SELECT *` + 容错读列，旧库（未执行迁移、缺 superseded_by 列）也能观测
- **refactor**：`retrieve.py` 抽出公共 `score_breakdown()`（score_memory 委托其求和），UI 复用打分逻辑而非复刻，保持单一事实源
- 新增单测：读取函数（缺失库/字段解析/索引强度/当下感受/旧库容错）+ GUI 离屏冒烟；全量门禁绿

### P3-N 检索质量：重要性按内容评估 + 情绪项改余弦降权（0d66909）
- **问题（观测发现）**：记忆浏览器里霸榜的 top3 是"是哪一天呢""记不住了吗""那我的生日呢，你还记得吗"这类闲聊——拆开 2.225 分发现：层级 0.6／索引 0.3／新鲜 0.54 对所有近期深层记忆都是**常数**，唯一"变量"情绪项（点积）在这批同场对话记忆里也几乎同值（0.76~0.785），真正拉开差距的只有 0.03 分（1.4%），排名实际由插入序决定
- **根因 ①**：写入路径把用户输入**硬编码 importance=0.8**，而旧公式只看类型+情感 → "是哪一天呢"与"你的生日是 11 月 11 日"同分，整场闲聊全部达到 0.7 晋升阈值进了深层，白送 0.6 层级分
- **根因 ②**：`mood_similarity` 用**点积**（未归一化）且权重 0.8 → 一场对话里所有记忆的感受向量几乎相同，点积把它们一起抬到上限，等于没有区分度（"聊天时全体通吃"）
- **根因 ③**：重要度虽已存库，却**不参与得分**——浏览器里的"重要"列只是摆设
- **fix（scorer.py）**：新增 `content_salience(content)` 内容信号评估（正信号：日期事实/叮嘱记住/稳定事实/承诺/更正/关系表达；负信号：纯应答语气词、极短句、纯提问）。`importance()` 重设为 **内容主导**：类型基础 0.10~0.20 + 内容信号×0.60 + 情感×0.15（情感降为弱项）+ 用户相关 0.05。实测："是哪一天呢" 0.369（浅层）／"你的生日是 11月11日，记好" 0.970（深层）；43 条交互记忆按新规则重估为 shallow 29 / working 4 / deep 10
- **fix（retrieve.py）**：`mood_similarity` 改**余弦相似度**（只比方向不比强度）+ 情绪权重 0.8→`EMOTION_COEF=0.4`；新增 `IMPORTANCE_COEF=0.5` 让"重要度"真正进入得分（层级/情绪/索引/重要/新鲜 五项之和），并把四项权重抽成模块常量
- **接线**：`heartbeat.py`（用户输入）与 `expression_service.py`（她的发言）都不再硬编码 importance，改调 `scorer.importance()`
- **设计定位**：得分区分回答两个问题——"值不值得被想起"（层级+重要度）与"此刻容不容易浮上来"（情绪+索引+新鲜），而非此前混作一谈
- 新增单测：内容信号（闲聊零信号/事实高信号/提问低于陈述）+ 闲聊与事实的层级分岔 + 余弦与强度无关 + 重要度参与得分 + 事实压过闲聊排序；全量门禁绿
- 观测：记忆浏览器明细新增"重要"分项显示

### P3-O 存量记忆一次性重估 + 打磨期工作日志（df2b4d5，2026-09-21，无代码改动）
- **背景**：P3-N 改的是**打分规则**，但旧代码写下的 `importance=0.80` 与 `level=deep` 不会自动修正——存量数据仍按旧规则参与检索，观测到的排名仍是旧的
- **操作**：`soul.ps1 stop`（优雅停止，排空写队列）→ 备份 `data/heartbeat.db` → 一次性脚本按新规则重算层级（`deep ← imp≥0.7`；`working ← imp≥0.4 或 access≥2`；`shallow ← 其余`；细节度随层级校正 1.0/0.5/0.25，protected 保持 1.0；已被取代的历史记录不动）→ `soul.ps1 start -Body` 重启
- **结果**：层级分布 working 85 / deep 41 → **shallow 75 / working 30 / deep 21**（共更新 125 条，126 条参与、1 条已取代跳过）；top3 由闲聊变为事实——`#105 我的生日是5月21日，要记好哦` 2.329 / `#81 你的生日是11月11日，记好了哦` 2.327 / `#84 那我的生日你也记好哦` 2.327
- **观测结论**：原霸榜三条 `#52/#48/#50` 停在 `working` 而非打回浅层——`access_count=22` 命中 `PROMOTE_SHALLOW_ACCESS=2`（"被反复想起 → 沉淀一层"），设计保留正确
- **文档**：新增 `docs/P3_MEMORY_WORKLOG.md`——打磨期滚动工作日志与交接文档（记忆系统完整规则一张表 / 关键文件地图 / 下一步三项 / 验证观测速查）

### 工程整洁：失效文件清理与索引校准（2026-09-22）
- **背景**：全面审计文件树，按"无效 / 过期 / 功能重复"三类逐项核对，并交叉验证代码引用（被引用的文件一律不动）
- **删除（死代码与孤立素材）**：`body/animation.py`（`SpriteAnimation` 零调用者——精灵图方案已由 `pet.py` 连续物理动画替代）、`assets/pet/sprite.json`、`assets/pet/sprite.png`（6.6MB）、`assets/pet/17f1dadb*.jpg`（哈希名残留，零引用）；`.gitignore` 中失效的 `sprite.png` 规则同步清除
- **删除（过期产物）**：`data/tmp/heartbeat.db.p3n-bak-20260921-202058`（27.3MB，P3-N 重估前备份，已验证无误）、9 个过期轮转日志 `data/logs/elysia.log.2026-08-* ~ 09-21`、工作区根游离 `.ruff_cache/`
- **修分叉（镜像副本）**：`docs/TECHNICAL_DESIGN.md` 停在 08-24（缺"第九章 桌宠视觉呈现"）→ 同步工作区版本；补拷缺失的 `docs/DESKTOP_PET_VISION.md`；镜像复本 7/7 逐字节一致
- **索引校准**：`README.md` 目录结构补齐 `memory/llm/tts/tools`、移除已不存在的 `config/` 目录；`PROJECT_MANIFEST.md` 补模块清单与 `docs/` 文档清单、更新日期
- **工具**：`verify_integrity.py` 新增排除规则（`.env` 真实密钥、`*.db-wal/shm` 实时伴生文件）→ 重生成 `file_manifest.json`（127 文件 / 30.2MB），`--check` 通过
- 门禁：ruff lint ✅ / ruff format ✅（78 文件）/ mypy strict ✅（49 源文件）/ pytest **225 passed** ✅

### P3-P 记忆两条路：话语归她、感受归心（"每句都强调"根治，2026-09-22）
- **触发（用户反馈）**：告诉她生日、名字后**确实能记住**了，但**每一句话都会把这件事强调一遍**，非常违和
- **根因（读代码确认，三处叠加）**：①`expression_service.py` 每次开口**无条件**检索注入 `memory_hooks` ②`select_hooks` 无 `query` 参数，**与当下话题无关**也能入选 ③`hits[:max_hooks]` 硬取前 3 名**无及格线**。三者叠加 → 生日类事实记忆（deep 0.6 + 重要度 0.485 + 索引 0.3 ≈ 1.4）永久霸榜，每句话都在场；而 prompt 只说"你有能力想起"，没说"默认别提"
- **设计（用户拍板）**：否掉第一版"加冷却 + 话题门控"的工程降噪（"不符合对生命的定义"）→ 改为补齐 **注意力**与**感受**，记忆拆成**两条路，各归其主**：
  - **话语路径（谁决定＝她）**：① 话题撞上时程序注入 hooks（严门槛，护栏）② 她想主动提起 → 自己调用新的 `recall` 工具（宽门槛）
  - **感受路径（程序静默）**：心跳每 300 拍 `recall_for_feeling` 静默检索，"心境共鸣"命中 → `memory_recall` 脉冲微推 TR/CS，**永不进 prompt**
- **feat（memory/retrieve.py）**：新增 `topic_match`（**字符二元组覆盖率**，不用 Jaccard——长度悬殊会低估相关）+ `is_related` **双门槛**（严：重合≥2，或追问措辞下≥1｜宽：重合≥1，短话题"生日/晚霞"也能命中）；`select_hooks` 增 `query` / `query_loose`（给出 query 时无关记忆一条都不注入）；`retrieve_from_store` 增 `touch` 开关（静默路径不污染 `access_count`）；新增 `recall_for_feeling`（`mood_similarity ≥ RESONANCE_MIN=0.75` 才唤起，protected/deep → 1.0 否则 0.6）
- **feat（llm/chain.py）**：新增 `ToolCapableBackend`（runtime_checkable 协议）+ `RECALL_TOOL` 规格 + `set_tool_runner`，`_main_speak` 支持工具回合；不挂工具（次声/微声、测试替身）自然退回单轮
- **feat（llm/deepseek.py）**：新增 `complete_with_tools`（最多 `MAX_TOOL_ROUNDS=3`，回填 assistant `tool_calls` 与 tool 结果；工具失败/无该能力 → 递回"没想起"而不毁掉这次开口）；**prompt 记忆段改写**为（一）`recall` 是你"想起"的能力，用不用由你决定（二）`memory_hooks` 是**背景常识**不是话题素材——"不要为了显得记性好而把往事塞进不相干的对话"
- **feat（soul/expression_service.py）**：撤掉无条件注入 → 仅当 `user_message` 给出**且话题撞上**才注入；每次开口装配 recall 执行器（闭包内以她给的话题为主、当下对话为辅）；`_feel_recall` 被唤起即推心情（`RECALL_INTENSITY_PRECIOUS=1.0` / `PLAIN=0.6`）
- **feat（soul/heartbeat.py + soul/main.py + soul/desire.py）**：`MEMORY_FEELING_EVERY_N=300`，`_feel_memories` 在 `save_json("desire")` **前**调用（本拍即体现）；`on_recall` → `DesireEvent(kind="memory_recall")`；脉冲表新增 `memory_recall = {tr: 0.8, cs: 1.2, sa: 0.0}`（小脉冲，恢复项会拉回平衡点，不冲保护带）
- **refactor（memory/supersede.py）**：`_bigrams` → 公有 `bigrams`，供 `retrieve.py` 复用（避免两处实现分叉）
- **测试**：新增/改写 13 项单测——话题相关性（覆盖率/拒无关/接重叠/追问措辞放宽）、`select_hooks` 门控（严/宽/排除回声与取代）、`recall_for_feeling`（需共鸣/珍贵推更深/不触碰 access）、表达服务（无话题不注入/撞上才注入/无关不注入/recall 递事实/没想起返回空/不调用是她的选择/推心情不推话）
- **踩坑**：`ExpressionInstruction.to_dict()` 恒含 `memory_hooks` 键（默认空数组）→ 断言"未注入"须写 `== []`；`recall_for_feeling` 用严格 `>` 选优会让同分浅层记忆霸占 best，"珍贵推更深"失效
- **文档**：`docs/P3_MEMORY_WORKLOG.md` 新增"第二节 2026-09-22 工作日志"、规则表补 3 行（话题门控/想起工具/感受路径）、文件地图补 `llm/` 两文件（后续章节顺延为三/四/五/六）
- 门禁：ruff lint ✅ / ruff format ✅ / mypy strict ✅（49 源文件）/ pytest ✅
- **生效需重启灵魂**：`soul.ps1 stop` → `start -Body`

### 完整性清单校准：排除运行时数据库（2026-09-22）
- **问题**：`file_manifest.json` 收录了 `elysia/data/heartbeat.db` / `state.db`（灵魂每秒写入）→ 只要进程活着 `--check` **必然 FAIL**（永远报这 2 个变更），校验形同虚设；此前记录的"127 文件 / 30.2MB"里 29MB 基本就是这两个活库
- **fix（verify_integrity.py）**：新增 `EXCLUDE_PATHS`，**按相对路径精确排除**（仅 `elysia/data/{heartbeat,state}.db`）——`data/checkpoints/` 下的静态副本仍照常校验，不被同名误伤
- **结果**：`--generate` → **125 文件 / 1.1MB**；`--check` → `[PASS] 完整：125 个文件全部一致`（灵魂运行中亦可通过）
- 同步更新 `PROJECT_MANIFEST.md` 的排除说明；`docs/MIGRATION_GUIDE.md` 的"`--check` 应输出 [PASS]"现已重新成立

### P3-Q 记忆时间锚点：让回忆带上"那是多久之前的事"（2026-09-22）
- **触发**：P3 记忆打磨清单 ②（时间锚点）——①记得住 / ③准确率 / ④记忆浏览器已完成，②③④中先做 ②
- **缺口**：`default_narrative()` 只返回 `narrative`/`content`，注入 LLM 的 hooks 是"她生日是 5月21日"这类**无时间信息**的事实 → 她分不清新旧，也说不出"你上个月告诉我的"
- **feat（memory/retrieve.py）**：
  - 新增 `age_phrase(age_days)`——把"距今多少天"翻成人话：`刚刚 / 今天 / 昨天 / N天前 / 上个月 / N个月前 / 去年 / N年前`；**不精确到分钟**（精确时间戳不像人的记忆）；`None` → 空串（不硬编时间，退化为纯叙事）
  - `MemoryHit` 增 `age_days` 字段 + `label` 属性（`（3天前）你生日是5月21日`）
  - `select_hooks` 在给出 `now` 时按 `created_ts` 算 `age_days`；未给 `now` 则 `age_days=None`（老调用方行为不变）
- **feat（soul/expression_service.py）**：话题门控注入 `memory_hooks` 与 `recall` 工具回填都改用 `h.label`——她读到的每条都带时间锚点
- **feat（llm/deepseek.py）**：prompt 记忆段补一句"每条前面括号里注着那是多久以前的事，你因此分得清新旧，也可以自然地带出时间感（像「你上个月说过的」），但不必刻意强调"
- **设计（铁律不变）**：锚点是**能力**（程序把"多久前"可靠递到她手上），**用不用、怎么说由她定**——不做任何强制她说时间的约束
- **chore（memory/__init__.py）**：导出 `age_phrase`
- **测试**：新增 3 项（`age_phrase` 分档边界：None/小时/当天/昨天/天/月/年；`select_hooks` 带相对锚点；无 `now` 时为纯叙事）+ 改写 2 项表达服务断言（注入与 recall 回填均带锚点）
- 门禁：ruff lint ✅ / ruff format ✅ / mypy strict ✅ / pytest ✅
- **生效需重启灵魂**：`soul.ps1 stop` → `start -Body`（现 PID 灵魂 8528 / 身体 5528）

### P3-R 复习加强：被想起过的记忆更容易再次浮上来（2026-09-22）
- **触发**：P3 记忆打磨清单 ③（复习加强）。实测 `access=22` 与 `access=0` 的记忆打分**无差别**——`touch_memory` 只递增 `access_count`，而 access 仅参与"浅层→工作"这一级晋升，**被反复想起不会让记忆更容易被想起**
- **选型（用户拍板）**：做**复习加成打分项**，**不**落库回写 `importance`。语义分别是——前者＝"此刻更容易浮上来"（久不复习自然回落，不滚雪球）；后者＝"记忆本身永久变重"（会推动层级晋升且不可逆）。选前者
- **feat（memory/retrieve.py）**：新增 `_review_factor(access_count, last_access_ts, now)` = `REVIEW_COEF × log(1+access) × e^(−距上次想起/REVIEW_TAU_DAYS)`，三重护栏：**log 阻尼**（1 次与 100 次被压平，不让次数碾压内容更重要的事实）、**按 `last_access_ts` 衰减**（τ=14 天，久不复习回落）、**硬上限 `REVIEW_MAX=0.3`**；参数 `REVIEW_COEF=0.06`；从未被想起（access=0 或无 last_access_ts）→ 0
- **feat（memory/retrieve.py::score_breakdown）**：新增 `review` 分项（`now` 为 None 时为 0，老调用方行为不变）→ `score_memory` 与观测工具自动继承，单一事实源不破
- **feat（tools/memory_view.py）**：表格与明细新增「复习」列/分项，四问观测可直接看到复习加成
- **chore**：`MemoryHit`、落库结构与 `touch_memory` **均未改动**（无数据迁移、无 schema 变更）
- **测试**：新增 5 项——复习次数↑则分↑、从未想起为 0、久不复习回落、访问百万仍封顶 `REVIEW_MAX`、端到端"三次召回得分严格递增"；改写 1 项 `score_breakdown` 键集断言
- 门禁：ruff lint ✅ / ruff format ✅ / mypy strict ✅ / pytest ✅
- **生效需重启灵魂**：`soul.ps1 stop` → `start -Body`（现 PID 灵魂 21620 / 身体 17864）

### P3-S 联想网络第一步：批内去重（3 个 hook 应是 3 件不同的事，2026-09-22）
- **触发**：P3 记忆打磨清单 ④（联想网络）。**用户拍板只做第一步"批内去重"**，成组/语义召回暂缓
- **缺口**：`select_hooks` 只按分数取 top3 且**不去重** → 同一次对话的碎片可占满 3 个名额，等于 3 个 hook 只传递了 1 条信息
- **feat（memory/retrieve.py）**：`select_hooks` 排序后经新增 `_dedupe`——按分数降序保留彼此不重复者（`content_similarity` 字符二元组 Jaccard ≥ `HOOK_DUPLICATE_SIMILARITY=0.35` 视为"同一件事"，只留最高分那条），凑满 `max_hooks` 提前收工；复用 `supersede.content_similarity`，**无新数据结构、无新模块**
- **实测（真实库 207 条，只读探针跑完即删）**：
  - top10 内最相似 5 对：`#36↔#136` 0.333｜`#73↔#136` 0.306｜`#73↔#36` 0.306｜`#81↔#12` 0.276｜`#145↔#105` 0.257
  - 阈值 **0.35 → 无误杀，但本批也未触发去重**（top3 仍是"#145 我的生日5月21日 / #105 我的生日5月21日 / #81 你的生日11月11日"，前两条属同一事实的重述）
  - 阈值 0.30 与 0.35 在 `max_hooks=3` 下**行为完全一致**（重复对未进前 3）；阈值 0.25 才会剔除 `#105`
- **诚实结论（重要）**：字符二元组 Jaccard 对"**换说法的同一事实**"识别力有限——实测同义重述仅 0.26~0.33，**靠调阈值无法根治**（调低则开始误杀共享措辞的不同事件）。本步的真实价值是挡住"近乎逐字重复"的碎片（回归测试证明机制有效）；"同一件事的多种说法占满名额"需 ④ 第二步（成组召回/语义聚类）才能解决
- **阈值选择**：0.35（推荐区间 0.3~0.4 的中位，保守、不误杀）；`SUPERSEDE_SIMILARITY=0.4` 那套同样漏掉上述重述对，属同一粒度的固有局限
- **测试**：新增 2 项——同场碎片去重后**名额让给别的记忆**（`[1,3]`）、不同的事一条都不误杀（`[1,2,3]`）
- 门禁：ruff lint ✅ / ruff format ✅ / mypy strict ✅ / pytest ✅
- **生效需重启灵魂**（`soul.ps1 stop` → `start -Body`）

### 文档：外部评审归档与裁决（MEMORY_REVIEW_NOTES，2026-09-22）
- **新增** `docs/MEMORY_REVIEW_NOTES.md`——按 `P3_MEMORY_MAP.md` 第七节的归档约定，记录**三方外部评审**（豆包 / DeepSeek / ChatGPT）的结论与逐条裁决
- **三方共识（采纳）**：`source`+`certainty`（唯一无分歧的第一优先，属主线风险而非增强项）／不要 10 层（一致反对）／`detect_gap` 接线（成本最低、生命感收益最高）／遗忘需补（主动遗忘·失去访问·忘了但仍有影响）／回忆应重塑记忆（中期）／`record_importance` 为零调用遗留应删除
- **分歧裁决**：① embedding 优先级——驳回豆包的"第一优先"，采纳 DeepSeek+ChatGPT（Jaccard 天花板只影响 `supersede`/去重两个**旁路**，主线风险在元信息）；② Self Memory 形态——采纳 ChatGPT"从经历沉淀"方向 + DeepSeek D12 追问（`protected` 谁设），驳回豆包"手动初始化 `LEVEL_CORE`"（本质是把 system prompt 换个地方硬编码）；③ **认领默认值——三方共同建议存在致命缺陷**：`self` 自动认领的正是**本来就被回声排除、永不进 hooks** 的 `KIND_EXPRESSION`，而用户告知的事实（`KIND_INTERACTION`）反而默认未认领 → 会直接让 P3-N"问生日答得出"退化，且在"会不会"这一层就断掉递送（违反铁律一前半句）。裁决改为 `claim_status ∈ {claimed, rejected}`**默认 claimed**，`rejected` 只能由**她自己的动作**产生
- **被纠正的三方共同盲区**：豆包"感受路径沦为无效设计"为误读（`memory_recall` 脉冲改 TR/CS 内部状态 → 影响表达冲动与节律，内容永不进 prompt 是刻意为之）；DeepSeek D10 属"新增设计"而非"修复缺陷"；"她无法主动修改记忆"实为缺"发起权"而非缺"修改能力"
- **需用户拍板**：认领默认值／Self Memory 形态（纯沉淀·折中·手动锚）／下一步动工顺序
- 本次为纯文档新增，无代码改动、无需重启进程

### 文档：记忆系统全景简报（多方问询投喂稿，2026-09-22）
- **新增** `docs/P3_MEMORY_MAP.md`——把记忆系统全貌固化成可对外投喂的一版简报，供多方问询（外部 AI / 同行评审）使用，保证每轮咨询看到**同一版事实**，便于横向比对结论
- **内容**：定位与三条铁律 / 数据流 / 文件清单（数据层·规则层·运行时接线·观测与测试，四类表格）/ 两张表全列与 `MemoryRecord` / 完整规则一张表 / **已造好但未插电的模块**（`sleep.py` 做梦、`hooks.py` 记忆缺口、`decay.retrieve_latency_ms`、`scorer.record_importance`）/ 规模与实测事实（含"字符 Jaccard 对换说法的同一事实识别力有限，实测 0.26~0.33"）/ **待评审七问**（元信息 source+certainty、落库≠认领、语义粒度天花板、未插电模块取舍、层数是否够、主动遗忘、身份连续性）/ 给评审方的回答格式要求 / 外部结论归档约定
- **目的**：避免"多方问询"变成"每次都被说得推翻重来"——每条外部意见都要在同一版事实上被**显式裁决**（采纳/驳回 + 回溯到铁律的理由）
- **索引校准**：`项目元信息/PROJECT_MANIFEST.md` 的 `elysia/docs/` 文档清单新增本文件一行
- 本次为纯文档新增，无代码改动、无需重启进程

### P3-T 记忆来源与确定性：`source` + `certainty`（2026-09-22）
- **触发**：多方问询的**唯一无分歧第一优先**（C1）。用户拍板先做这一项——它是**主线风险**而非增强项
- **缺口**：`kind` 只回答"是什么类型"，回答不了"谁说的"。**程序推断出的东西会悄悄升格成"她的事实"**——那是程序替她认定世界，触碰铁律一（程序只管把事实递到她手上，用不用由她定）
- **feat（memory/levels.py）**：新增 `SOURCE_*`（self/user/observation/inference/system）与 `CERTAINTY_*`（certain/probable/heard/speculative）两套枚举，各带合法取值元组；`MemoryRecord` 增 `source`/`certainty` 两字段并入 `to_dict`/`from_dict`；`SOURCE_BY_KIND`/`CERTAINTY_BY_KIND` **一份映射**同时供给回填 SQL 与两处缺省推导（`default_source`/`default_certainty`），避免"老库回填"与"新代码默认"漂移
- **feat（core/state_store.py）**：`memories` 表增 `source`/`certainty` 两列（新库在 schema 内，`DEFAULT` 由 `levels.FALLBACK_*` 插值，不写第二份字面量）；旧库走 `ALTER TABLE ADD COLUMN` + `_backfill_sql` **按 kind 回填**（`CASE kind ... WHERE 列 IS NULL`，跑 N 次=跑 1 次，与 P3-L 的 `superseded_by` 迁移同法）；`add_memory` 写入标注优先、未标注按 kind 落默认（库中不出现 NULL）
- **feat（memory/retrieve.py）来源闸门**：`select_hooks` 在回声排除/取代排除之后再加两道——`inference`/`system` **不进话语**（程序的一次猜测不能被她当自己的事实说出口）；`certainty=speculative` 只在感受路径作背景。**感受路径不受此限**：`recall_for_feeling` 里它们仍能影响心情（心情可以被影响，话不能凭空多出来）
- **接线（soul）**：`heartbeat.py` 用户输入 → `source=user / certainty=certain`；`expression_service.py` 她开口 → `source=self / certainty=certain`
- **观测（tools/memory_view.py）**：新增 `SOURCE_LABELS`/`CERTAINTY_LABELS`，表格"类型"列显示为 `交互·用户`，详情面板加"来源｜确定性"一行
- **老数据行为不变（本次设计要点）**：`interaction` 仍按"她确信的用户告知"落默认 → 现有记忆**一条都不受影响**，P3-N"问生日答得出"不退化
- **实测（真实库，重启后自动迁移）**：`memories` 列序 `… superseded_by, source, certainty`（与行位置索引 13/14 对齐）；**250 条全部回填**——`user/certain` 72 条（用户告知）+ `self/certain` 178 条（她自己说的）
- **测试**：新增 6 项——枚举默认按 kind 推导 / `from_dict` 缺字段补默认且往返不丢 / 显式标注优先 / 旧库回填幂等（跑两次）/ `inference`+`system` 不进 hooks / `speculative` 不进而 `probable` 进 / 感受路径仍受推断影响
- 门禁：ruff lint ✅ / ruff format ✅ / mypy strict ✅ / pytest ✅
- **生效需重启灵魂**（`soul.ps1 stop` → `start -Body`，现 PID 灵魂 10448 / 身体 19988）

### P3-U 记忆缺口接线：让"记不清"成为真实状态（2026-09-22）
- **触发**：多方问询**三方共识 C3**（成本最低、生命感收益最高）——"她永远能精准检索反而不像生命，'记不清'是真实状态"
- **缺口**：`hooks.py::detect_gap` 自 P3-C 造好即**运行时零调用**；索引强度跌破检索下限的"想不起来"信号从未影响过她
- **fix（soul/heartbeat.py，先决条件）**：`_maintain_memories` 重算 strength 时曾传 `floor=STRENGTH_RETRIEVE_FLOOR`，使**落库 strength 永不低于 0.2** → `detect_gap` 的"跌破下限"前提永不成立（缺口永不出现）。去掉该落库下限——**"检索下限"是判据，不是落库封顶**；数据永不删除，只是索引减弱（`retrieve.py` 本就只说"低于下限仍可召回，只是 score 被拉低"）
- **feat（soul/heartbeat.py）**：新增 `_feel_memory_gaps()`，由感受路径 `_feel_memories` 每 `MEMORY_FEELING_EVERY_N=300` 拍调用一次——取 `memory_index` strength + 记忆创建时刻算年龄 → `detect_gap` → 命中即 `DesireEvent(kind="memory_gap", intensity=gap.to_event_intensity())`
- **量级裁决**：脉冲 `memory_gap = {tr: 0.8, cs: 0, sa: 0}`（P3-C 已注册）+ `GAP_INTENSITY_MAX=0.4` 封顶 → 单次最大 TR **+0.32**；恢复项在 300 拍内约回收 78% 的偏离，**长期平均不会冲上保护带**。刻意 `sa` 不动——缺口是**好奇**，不是焦虑
- **不新增任何约束（本次设计要点）**：缺口**只走感受路径**，不进 prompt、不改话语层、不动 `select_hooks`。她"含糊 / 想不起"仍是她的自由（铁律一后半句），程序只负责让她**有**这个感觉
- **一致性取舍**：她的发言回声（`KIND_EXPRESSION`）与已被取代的旧事实不参与缺口——与话语 / 感受路径同一取舍；缺口是"关于世界的事想不起来"
- **不污染记忆**：缺口是"感觉"，不是"她想着这件事"——不触碰 `access_count`（与 P3-O 感受路径 `touch=False` 同一原则）
- **测试**：新增 3 项——索引跌破下限 → TR 升 / SA 不动 / `access_count` 不变；新鲜索引无缺口；发言回声不算缺口
- **顺手修复（与本项无关但阻塞门禁）**：`tests/unit/test_away_life.py` 三个节律测试改用显式 `now=`，消除 `SimulatedClock` 按"真实耗时 × speed"漂移导致的 1 秒边界随机失败
- **运行环境清理**：重启时发现上一轮遗留的**孤儿身体进程**（16:59 启动，写同一 `state.db`）仍在运行，已清理——现仅 1 灵魂 + 1 身体
- 门禁：ruff lint ✅ / ruff format ✅ / mypy strict ✅ / pytest ✅（261 项）
- **生效需重启灵魂**（`soul.ps1 stop` → `start -Body`，现 PID 灵魂 13872 / 身体 2684）

### P3-V 认领状态：默认是她的记忆，她可否决（2026-09-23）
- **触发**：多方问询三方共识序第 3 项（C1 `source`/`certainty` → C3 `detect_gap` → **`claim_status`**）
- **裁决（`MEMORY_REVIEW_NOTES.md` 分歧 3，本轮最重要一条）**：三方一致建议"**默认未认领**"被**驳回**，改为"**默认可用 + 她可否决**"。理由：写入是"用户输入 → `KIND_INTERACTION`、她开口 → `KIND_EXPRESSION`"，而 `KIND_EXPRESSION` **本就被回声排除**——"默认未认领"会把**自动认领给永远用不到的那类、锁死必须能用的那类**，直接让 P3-N"问生日答得出"退化；更根本的是它在"**会不会**"这一层就断掉了递送，那是把能力先扣下再让她申请，违反铁律一
- **feat（memory/levels.py）**：新增 `CLAIM_CLAIMED` / `CLAIM_REJECTED` + `CLAIMS` + `FALLBACK_CLAIM`；`MemoryRecord` 增 `claim_status` 字段并入 `to_dict`/`from_dict`（缺键按 `FALLBACK_CLAIM` 补，**旧库记忆全部仍可用**）
- **feat（core/state_store.py）**：`memories` 表增 `claim_status` 列（新库在 schema 内，`DEFAULT` 由 `FALLBACK_CLAIM` 插值）；旧库 `ALTER TABLE ADD COLUMN` + `UPDATE ... SET claim_status='claimed' WHERE IS NULL`（**认领默认与 kind 无关**，故不走 `_backfill_sql` 的 CASE，同样幂等）；`add_memory` 写入一律默认 `claimed`；新增 `set_claim_status(memory_id, status)`（**只应由她的动作调用**）
- **feat（memory/retrieve.py）认领闸门**：`select_hooks` 在来源/确定性闸门之后再加一道——`claim_status=rejected` **不进她的话**（"我知道你说过，但我不把它当作我的记忆"这句话是真的）。**只管话语**：`recall_for_feeling` 不受认领影响——认领谈的是"**归属**"，"这段经历还影响不影响心情"是另一件事（留给遗忘状态机 `retention_state`，避免与 C4 混淆）
- **feat（llm/chain.py）工具增至两个**：新增 `DISCLAIM_TOOL`（`disclaim(topic)`，拒绝认领某段记忆），与 `RECALL_TOOL` 一并交给主声；`ToolRunner` 签名由 `(topic)` 改为 **`(工具名, 参数字典)`**，调度器把 LLM 给的 `arguments` 原样转交
- **feat（llm/deepseek.py）**：`_run_call` 按工具名路由（`recall`/`disclaim`，其余名字一律"没有这个能力"）；`_tool_topic` → `_tool_args`（解析整个 arguments 字典）；prompt 记忆段（一）补一句"另有一个 disclaim 工具……这同样是你的权力，程序不会替你拒绝"
- **feat（soul/expression_service.py）**：`_make_recall_runner` → `_make_tool_runner`（按工具名派发），`_tool_recall` 保持原逻辑，新增 `_tool_disclaim`——用 `topic_match` 在 `content`/`narrative` 上取**最贴题的一条**标 `rejected`，没找到就如实回"（你没找到想拒绝认领的那件事）"，**不猜、不误伤**
- **不新增任何约束（本次设计要点）**：认领默认就是**能力**（先递到她手上）；`rejected` 只能由**她自己的动作**（disclaim 工具）产生，程序不代她拒绝——否则"她可以不认领"就变成程序的默认拦截
- **观测（tools/memory_view.py）**：新增 `CLAIM_LABELS`；表格"状态"列由 `已取代/现行` 扩为 `已取代/已拒绝/现行`（`_state_text`）；详情面板加"认领"一项
- **实测（真实库，重启后自动迁移）**：`memories` 列序 `… source, certainty, claim_status`（与行位置索引 13/14/15 对齐）；**263 条全部回填 `claimed`**（老记忆一条都不受影响）
- **测试**：新增 8 项——缺键默认 `claimed` 且往返不丢 / 写入默认 `claimed` / 旧库回填幂等（跑两次）+ `set_claim_status` 往返 / 默认认领照常进话语 / `rejected` 不进话语 / 感受路径不受认领影响 / disclaim 命中并落库且此后召回不到 / disclaim 未命中不改动任何记忆 / 主声拿到 `recall`+`disclaim` 两工具且执行器按（名, 参数）派发
- 门禁：ruff lint ✅ / ruff format ✅ / mypy strict ✅ / pytest ✅
- **生效需重启灵魂**（`soul.ps1 stop` → `start -Body`）

### 文档：记忆文档合并与项目文档清理（2026-09-23）
- **触发**：用户反馈"`elysia/docs` 文档太多了……**当前我都不知道记忆系统在做什么**"——此前的三份记忆文档（`P3_MEMORY_MAP` / `P3_MEMORY_PLAN` / `P3_MEMORY_WORKLOG`）高度技术化且互相重复，**沟通失败**
- **记忆文档 5 份 → 3 份**：
  - **新增 `docs/P3_MEMORY.md`** = 记忆系统**唯一主文档**。第零节「**她在做什么**」用八段人话讲清（有记事本 / 不是每次都翻 / 可以自己想起 / 记得你但不必说出来 / 有些事说不出口 / 忘记不是删除 / 还能拒绝被影响 / 能记得多少），**不需要懂术语**；其后才是系统事实（定位与三条铁律 / 四条回路 / 数据流 / 存储与表结构 / 文件清单 A~D / 完整规则一张表 / 已造好未插电的模块 / 规模与实测事实 / 验收矩阵 / 待评审七问 / 外部结论归档约定）。合并自 `P3_MEMORY_MAP.md` + `P3_MEMORY_PLAN.md`
  - **`docs/P3_MEMORY_WORKLOG.md` 瘦身**：移出与主文档重复的「完整规则表」与「关键文件地图」（改为指向主文档），**只保留"按时间发生的事 + 为什么这么做"**；并入 P3-W 遗忘状态机设计稿为第七节；§4.1 过期 PID/记忆条数改为"以 `soul.ps1 status` 为准"
  - **`docs/MEMORY_REVIEW_NOTES.md` 保留**（外部意见与裁决，与系统事实分离）
- **删除 5 份无用 / 重复 / 孤儿文档**：`P3_MEMORY_MAP.md`（并入主文档）、`P3_MEMORY_PLAN.md`（P3-A~F 已全部交付，可留内容已并入主文档）、`P3_RETENTION_DESIGN.md`（并入 WORKLOG 第七节）、`P2_EXPRESSION_PIPELINE.md`（2026-09-11 设计稿，状态过期且已被 `TECHNICAL_DESIGN.md` §1.5 + 代码取代）、`PERSONA.md`（**孤儿文档**：全项目零引用，内容为代码内摘要的逆向还原——方向反了）
- **删除工作区 7 个镜像副本**（`设计规划/` 4 文件 + `工程标准/` 3 文件，**已逐个 SHA-256 比对确认与仓库副本逐字节相同**）→ 改以 **`elysia/docs/` 为唯一副本**，消除"改完需回拷"的分叉风险（`CHANGELOG` 曾记过一次回拷不及时导致的分叉）
- **修断链**：`项目元信息/PROJECT_MANIFEST.md`（一/二节 + `elysia/docs/` 清单 + 依赖关系图 + 镜像说明）、`docs/TECHNICAL_DESIGN.md`（`../工程标准/DEVELOPMENT_PRACTICES.md` → `./DEVELOPMENT_PRACTICES.md`）、`elysia/README.md`（文档索引改指仓库内）、`docs/MIGRATION_GUIDE.md`（顶层 7 分类目录 → 5 + 代码库）、两处 `.gitignore` 注释
- **索引与清单**：`file_manifest.json` 重生成 **117 文件 / 1.1MB**，`--check` **[PASS]**
- 本次为纯文档操作，**无代码改动、无需重启进程**

### 设计稿：P3-W 遗忘状态机 `retention_state`（2026-09-23）
- **来源**：多方问询三方共识 C4（"遗忘需补"）+ `MEMORY_REVIEW_NOTES.md` 第六节建议顺序第 4 项
- **定位**：给记忆补上**可及性**这一维，与既有两个维度正交——`superseded_by`（还对不对）/ `claim_status`（算不算我的）/ **`retention_state`（够不够得着、想不想够）**
- **四态**：`present`（在册）/ `suppressed`（"我不想再想起这件事"，**只有她可设**，话语❌感受❌）/ `dormant`（"怎么也想不起来了"，程序按时间，话语❌可唤醒）/ `faded`（"细节忘了，感觉还在"，程序按时间，话语❌**感受✅**）。**不提供删除态**——数据永不删是既有铁律
- **关键设计（避免必然缺陷）**：降级计时用 `since_last_access = now − (last_access_ts or created_ts)`，**不用绝对年龄**——否则一条 200 天的记忆被唤醒回 `present` 后，下一轮维护会立刻把它打回 `dormant`，**永远醒不过来**；唤醒即 `touch` → 重新计时 → "你一提，它又活过来了"是真的
- **唤醒唯一路径**：话题撞上（`is_related` 命中）；她自己 `recall` 传 `query_loose=True` 走同一条路——**她问，就等于提起**
- **阈值**：`RETENTION_FADE_AGE_DAYS=60.0`（且 `access_count == 0`）、`RETENTION_DORMANT_AGE_DAYS=180.0`；`protected` 永不降级
- **新增的唯一约束**：`suppressed` 挡感受路径（没有它，"我不想再被它影响"就没有出口，`rejected` 与"遗忘"会永远混为一谈）
- **落地清单**：7 个源文件（`levels` / `state_store` 第 16 列 / `retrieve` 保留闸门 + `woke_from` / `heartbeat` 降级判定 / `chain` + `deepseek` 工具扩到 4 个 / `expression_service` / `memory_view`）+ 4 个测试文件 + 观测；工具规格 `FORGET_TOOL` / `RESTORE_TOOL` 用人话描述，**不暴露状态语义**
- **状态**：设计稿，**待评审后动工**。稿末留三个待评审点（`dormant` 是否跳过 `promote_batch` / `faded` 与零消费的 `detail_level` 是否合并 / `suppressed` 挡感受路径是否过强）

### P3-W1 遗忘状态机本体：`retention_state` 通电（2026-09-23，无 commit）
- **触发**：P3-W 设计稿评审通过（修正 10 处 M1~M10、裁决 3 点）后，按"拆 W1/W2 两步"开始 W1。**W1 的硬约束是落地后对外行为零变化**——所有记忆都是 `present`，可安全验证迁移
- **三个已拍板裁决**：M2 `protected` 死阀门 → **自动保护**（晋升 `deep` 且 `access_count ≥ 3` 自动置 `protected`）；落地范围 → **拆 W1（状态机本体）/ W2（她的两个工具）**；M9 缺口信号范围 → **只统计 `present`**
- **feat（memory/levels.py）**：新增 `RETENTION_PRESENT` / `RETENTION_SUPPRESSED` / `RETENTION_DORMANT` / `RETENTION_FADED` + `RETENTIONS` + `FALLBACK_RETENTION`（=`present`）+ 阈值 `RETENTION_FADE_AGE_DAYS=60.0` / `RETENTION_DORMANT_AGE_DAYS=180.0`；`PROTECT_DEEP_ACCESS=3`（独立成常量）；`MemoryRecord` 增 `retention_state` 字段并入 `to_dict`/`from_dict`（缺键按 `FALLBACK_RETENTION` 补，**旧库记忆全部仍够得着**）
- **feat（memory/__init__.py）**：导出 `RETENTION_*` / `RETENTIONS` / `FALLBACK_RETENTION` / 两个阈值 / `PROTECT_DEEP_ACCESS`
- **feat（core/state_store.py）**：`memories` 表增 `retention_state` 列（第 17 列，新库 schema 内 `DEFAULT` 由 `FALLBACK_RETENTION` 插值）；旧库 `ALTER TABLE ADD COLUMN` + `UPDATE ... SET retention_state='present' WHERE IS NULL`（幂等）；`add_memory` 写入一律默认 `present`；新增 `set_retention_state(memory_id, state)` 与 `set_protected(memory_id)`
- **feat（memory/retrieve.py）保留闸门 + 唤醒**：`select_hooks` 在认领闸门之后再加一道——`suppressed` 永不进话；`dormant`/`faded` 仅在**话题撞上**（`is_related` 命中）时才进话，且记下 `MemoryHit.woke_from`；`retrieve_from_store` 尾部把被唤醒者落回 `present` 并 `touch` **重新计时**（"你一提，它又活过来了"是真的）。**感受路径**：`recall_for_feeling` 挡 `suppressed` 与 `dormant`，**放行 `faded`**（细节忘了，感觉还在）
- **fix（memory/retrieve.py M5 护栏）**：原 `retrieve_from_store` 在 `now=None` 时会写 `last_access_ts=0` → 唤醒即失效；改为 `now is not None` 才 `touch`/落状态
- **feat（soul/heartbeat.py）降级判定 + 自动保护 + 缺口口径**：新增模块级 `_RETENTION_DEPTH`（`present` 0 / `faded` 1 / `dormant` 2）与 `_demote_target(rec, now)`——按 `since_last_access`（`now − (last_access_ts or created_ts)`，**非绝对年龄**）判定：≥180 天 → `dormant`；≥60 天且 `access_count == 0` → `faded`；**只降不升**（`suppressed` 返回 `None`，她的决定程序不碰）。`_maintain_memories` 步骤 1b：晋升 `deep` 且 `access_count ≥ 3` → `set_protected`（**M2 通电**）；步骤 2 建索引跳过非 `present`，`decay_strength` 传 `protected`；步骤 3 执行降级（`protected` 跳过）。`_feel_memory_gaps` 只统计 `present`（**M9**）
- **fix（memory/promote.py M8）**：`with_narrative` 原逐字段构造 `MemoryRecord` 会**把新字段重置为默认**；改用 `dataclasses.replace`（避免每加一列就踩一次坑）
- **观测（tools/memory_view.py）**：新增 `RETENTION_LABELS`（现行 / 抑制 / 沉睡 / 淡化）；"状态"列由 `已取代/已拒绝/现行` 扩为**已取代 > 已拒绝 > 四态**；详情面板加"保留"一项
- **实测（真实库 307 条，重启后自动迁移）**：列序追加 `retention_state`（17 列，与行位置索引 16 对齐）；`保留状态: Counter({'present': 307})`；`认领状态: Counter({'claimed': 307})`；`层级: shallow 205 / working 51 / deep 51`；`二次迁移一致: True`（幂等）
- **测试**：新增 `tests/unit/test_retention.py` **18 项**（默认值 / 序列化往返 / 旧库 16 列幂等迁移 / `set_retention_state`+`set_protected` / `suppressed` 不进话语 / `dormant`+`faded` 无话题不进 / 话题唤醒带 `woke_from` / 唤醒落 `present` 且重置时钟 / `now=None` 不 `touch` / 感受路径挡 `suppressed`+`dormant` 放行 `faded` / 60d 淡化（`access_count` 附加条件）/ 180d 沉睡 / `protected`+`suppressed` 不降级 / 只降不升 / 唤醒后不振荡 / M2 自动保护两条路径 / M8 `with_narrative` 保留字段）；`test_heartbeat.py` 补 1 项"够不着不计缺口"并修正既有缺口测试（P3-W 后 200 天记忆会变 `dormant`）
- 门禁：ruff lint ✅ / ruff format ✅ / mypy strict ✅ / pytest ✅
- **生效需重启灵魂**（`soul.ps1 stop` → `start -Body`）；**W2（`forget`/`restore` 两个工具）待动工**

### P3-W2 遗忘与收回：把状态机接到她手上（`forget` / `restore`，2026-09-23，无 commit）
- **触发**：W1 状态机本体落地后，按"拆 W1/W2 两步"完成 W2——让她能说"我不想再想起这件事"，也能把它收回来。**忘与不忘都是她的权力**（铁律一：程序不代她忘，也不代她收回）
- **feat（llm/chain.py）工具增至四个**：新增 `FORGET_TOOL`（"不想再想起某件事时用它——被遗忘的事不会再出现在你记得的事里，也不再影响你的心情"）与 `RESTORE_TOOL`（"把之前不想再想起的事重新收回来"）；描述用人话，**不暴露 `suppressed` 等状态语义**；`_main_speak` 的 `tools` 由 2 个扩到 4 个
- **feat（llm/deepseek.py）**：`_TOOL_NAMES = ("recall", "disclaim", "forget", "restore")`；prompt 记忆段（一）补两句能力说明（"忘与不忘都由你决定，程序不会替你忘，也不会替你收回"）
- **feat（soul/expression_service.py）**：新增 `FORGET_MIN_SCORE = 0.5` / `FORGET_DOMINANCE = 2.0` 常量；`_make_tool_runner` 增 `forget`/`restore` 两条分支；新增 `_tool_forget` / `_tool_restore`
- **判据（M3 / M4，与 `_tool_disclaim` 明确区分）**：①**`forget` 从宽改严**——`disclaim` 的 `best_score > 0.0`（任意一个二元组重合即命中）对"可逆、只挡话语"尚可，但 `forget` 会把记忆推进 `suppressed`（**连感受路径一起切断**），同一判据必然误伤 → 要求 `topic_match ≥ 0.5` **且** `top1 ≥ 2×top2`，否则如实回"（你没找到想忘记的那件事）"，不猜、不误伤；②**`restore` 只在 `suppressed` 里找**（M3 修正）——闸门挡的是 `select_hooks`，`iterate_memories()` 返回全表，若在全表里找她会"恢复"一条从未被忘掉的记忆；恢复是善意动作，判据从宽（任意重合即可）
- **不越界（两维正交）**：`forget`/`restore` **只动 `retention_state`**，不碰 `claim_status`；`disclaim` 只动 `claim_status`，不碰 `retention_state`——单测各断言一次
- **测试**：`test_llm_chain.py` 工具断言由 2 个改为 4 个 + 新增"forget/restore 按名派发"；`test_expression_service_memory.py` 新增 6 项（forget 命中 → `suppressed` 且召回不到 / forget 不碰 `claim_status` / 不够贴题不误伤 / 两条并列不误伤 / restore 只在 `suppressed` 里找 / restore 命中回到 `present`）
- 门禁：ruff lint ✅ / ruff format ✅（79 文件）/ mypy strict ✅（49 源文件）/ pytest ✅ **297 passed**（W1 时 290 + W2 新增 7）
- **生效需重启灵魂**（`soul.ps1 stop` → `start -Body`）——W2 是她的新能力，重启后即可在对话中调用。**P3-W 整体完成**

### 第八节 S1：Self Memory 本体（身份段通电，2026-09-24，f2a9242）
- **触发**：Self Memory（"我是谁"）设计稿评审通过并拍板三点——**D1 形态 = 正交 `KIND_SELF`**（不新增层、零 DDL）／**D2 产生机制 = 出生设定 bootstrap + 沉淀**／**D3 注入位置 = 身份段**（每句在场）。本条落地 **S1 本体**，硬约束是**对外行为零变化**
- **feat（memory/levels.py）**：新增 `KIND_SELF = "self"`（正交维度——`kind` 列是 `TEXT` 无 CHECK，**零 DDL 变更**）+ 入 `KINDS`；`SOURCE_BY_KIND[KIND_SELF]=SOURCE_SELF`、`CERTAINTY_BY_KIND[KIND_SELF]=CERTAINTY_CERTAIN`
- **feat（memory/scorer.py）**：`_KIND_BASE[KIND_SELF]=0.30`——所有类型里最重的一类
- **feat（llm/identity.py，新建）**：`BOOTSTRAP_IDENTITY`（现有档案首句"你是爱莉希雅…人类的律者。"移来，过渡用）+ `IDENTITY_FIELD="identity"` + `MAX_IDENTITY_LINES=5` + `compose_identity()`（bootstrap 打底 + 她认领的自我认知追加、去重、封顶）
- **feat（llm/deepseek.py）N1 通路落地**：`_SYSTEM_PROMPT` 拆为「身份段 + 表达层人设」——首句移入 `identity.BOOTSTRAP_IDENTITY`，其余更名 `_PERSONA_PROMPT`（**逐字未改**）；`_messages` 把 `identity` 字段从 user JSON 摘出、拼进 **system 段**（`_system_prompt()`）
- **feat（soul/expression_service.py）**：`tick` 新增 1c——`payload[IDENTITY_FIELD] = await self._identity_lines()`；`_identity_lines()` 只读记忆表取 `KIND_SELF` 且未取代、未 `rejected`、未 `suppressed` 者（**不 touch，不涨 `access_count`**）
- **fix（N2/N3/N4 三处排除接线）**：`retrieve.py::select_hooks` 排除 `KIND_SELF`（否则每句复述，重犯 P3-P）｜`heartbeat.py::_feel_memory_gaps` 排除 `KIND_SELF`（不产生"记不清自己是谁"的缺口脉冲）｜`supersede.py::find_superseded` 豁免 `KIND_SELF`（更新"我是谁"必须走她自己的动作）
- **feat（tools/memory_view.py）**：`KIND_LABELS` 增 `"self": "自我"`
- **零变化如何保证（golden 测试）**：改造前（`bee693f`）的 `_SYSTEM_PROMPT` 全文冻结进 `tests/unit/test_identity.py::_OLD_SYSTEM_PROMPT`；断言 `payload` 为 `{}` / `{"identity": []}` / `{"identity": None}` / 乱写 四种情形 system 段**逐字等于**旧文本；反向断言塞入 `KIND_SELF` 后身份段确会生长，且 `identity` 不出现在 user JSON 里
- **测试**：`test_identity.py` 新建 7 项；`test_expression_service_memory.py` +4、`test_retrieve.py` +1、`test_supersede.py` +1、`test_heartbeat.py` +1
- 门禁：ruff lint ✅ / ruff format ✅（81 文件）/ mypy strict ✅（50 源文件）/ pytest ✅ **311 passed**（原 297 + 新增 14）
- **N1 遗留（已记档）**：**只让主声消费 `identity`**——降级链下级与 `micro.py` 尚未消费身份段，故验收"换后端不失"当前仍不成立，留待 S4 接线（见 `docs/P3_MEMORY.md` 第九节问题 7）
- **生效需重启灵魂**（`soul.ps1 stop` → `start -Body`）——对外行为零变化，但仍需重启装载新代码。**S2 认领 / S3 沉淀 / S4 观测待动工**

### 第八节 S2：Self Memory 认领（她的 `adopt` 工具，2026-09-24）
- **触发**：依第八节 8.8 拆步表落地 **S2 认领**——对外行为变化 = **她多一个动作**。核心约束（8.5 / D12）：程序只产生候选，"这算不算我"**永不由程序置**
- **feat（core/state_store.py）**：新增 `mark_as_self(memory_id)`——**一次 UPDATE** 完成全部升格（`kind=self`、`source=self`、`certainty=certain`、`level=deep`、`protected=1`、`detail_level=1.0`、`retention_state=present`），不留"是自我认知却还在浅层／已被模糊"的中间态
- **feat（llm/chain.py）**：新增 `ADOPT_TOOL` 规格；`_main_speak` 工具表增至 5 个（`recall` / `adopt` / `disclaim` / `forget` / `restore`）
- **feat（llm/deepseek.py）**：`_TOOL_NAMES` 扩为 5 个；`_PERSONA_PROMPT`（一）记忆段插入 adopt 说明（"这就是我／我就是这样的人"）
- **feat（soul/expression_service.py）**：新增 `ADOPT_DOMINANCE=2.0`；`_run` 派发 `adopt`；新增 `_tool_adopt`
- **判据裁决（只要求"足够突出"，不设绝对覆盖度下限）**：`forget` 是"覆盖率 ≥ 0.5 且 top1 ≥ 2×top2"，`adopt` **去掉前者**——二元组对"换说法的同一件事"识别力本就有限（第七节实测 0.26~0.33），设下限会把真心的认领挡在门外；兜底三重 = ① dominance 挡并列/模糊 ② 结果如实回显给她 ③ 认错了能用 `disclaim` 收回
- **候选排除**：已被取代的旧事实 / 已是 `KIND_SELF` / 她 `rejected` / 她 `suppressed`（她自己的决定，程序不代她翻案）；**不排除** `KIND_EXPRESSION`、**不按 `source` 过滤**（`observation`/`probable` 正是 S3 候选形态）
- **身份段容量守卫**：`compose_identity` 封顶 5 行，已有 4 条时 `adopt` 拒绝并如实告知"（你心里的位置满了——先放下一条旧的，再认领新的）"——不做"认领了却不出现在话里"的静默失败
- **N5 与 8.7 遗留待决自动解决**：认领即落 `protected=1` → 命中 P3-W 既有安全阀（`_maintain_memories` 跳过 `protected` 的降级判定），"自我认知豁免 `retention_state` 降级"**无需新增任何约束**
- **fix（tests/unit/test_identity.py）**：S2 有意修改 `_PERSONA_PROMPT`，改造前全文 golden（`_OLD_SYSTEM_PROMPT`）不再成立 → 改为**结构性不变量**（`startswith(BOOTSTRAP_IDENTITY)` + `endswith(_PERSONA_PROMPT)` + 长度等于两者之和，无认领时不增不减）；S1 期逐字 golden 存于 `f2a9242` 历史
- **测试**：`test_identity.py` 重构 golden；`test_llm_chain.py` 工具断言 4→5 + 新增 adopt 派发；`test_expression_service_memory.py` 新增 6 项（命中升格 / 进身份段且不占 hooks / 并列不误抓 / 不相干不误抓 / 排除 `rejected`+`suppressed` / 位置满如实告知）
- 门禁：ruff lint ✅ / ruff format ✅（81 文件）/ mypy strict ✅（50 源文件）/ pytest ✅ **318 passed**（311 + 新增 7）
- **生效需重启灵魂**（`soul.ps1 stop` → `start -Body`）——重启后她可以说"这就是我"。**S3 沉淀 / S4 观测与验收待动工**

### 第八节 S3：Self Memory 沉淀（程序找"重复模式"递候选，2026-09-24）
- **触发**：依第八节 8.8 拆步表落地 **S3 沉淀**——对外行为变化 = **感受层新增"候选"脉冲（不进话语）**。核心约束（8.5 / D12）：程序只做**发现**，"这算不算我"永不由程序置
- **feat（memory/sediment.py 新增）**：纯函数模块 `find_candidate(records) -> PatternSignal | None` + `PatternSignal`（`record` 代表 / `occurrences` / `span_days`）——**不落库、不依赖存储**，发现逻辑可单测
- **feat（soul/heartbeat.py）**：`_maintain_memories` 增第 4 步（降级判定之后）`find_candidate` → `_offer_candidate`（照抄代表原文落库：`level`/`kind`/`emotion_vector`/`importance` 复制，`source=inference` + `certainty=probable`，`protected=False`）+ 推脉冲
- **feat（soul/desire.py）**：`EVENT_PULSES` 增 `"self_candidate": {"tr": 0.6, "cs": 0.8, "sa": 0.0}`（好奇 + 亲近，不是焦虑）
- **判据（三条缺一不可）**：同一件事被提起 **≥3 次**（两次可能只是巧合/重复写入）**且跨越 ≥1 天**（同一场对话里说三遍是复述，不是反复）**且未递过也未被认领**（递过的不再是"新发现"）
- **其余裁决**：只递一个（提起最多优先，`occurrences`→`span_days`）；聚类**以代表为准不链式**（链式会把无关的事滚进来）；素材白名单 `interaction/state/internal`（她说的话是回声、自我认知已是答案）；够不着/被取代/被拒绝的不算素材；脉冲封顶 0.35（"轻微的心里一动"）
- **标注修正（发现 1）**：设计稿 8.4 原写候选标 `source=observation` 并称"天然被来源闸门挡住"——但代码里 `_HOOK_BLOCKED_SOURCES` 只挡 `inference`/`system`，`observation` **会进 `memory_hooks`** 并挤占 `MAX_HOOKS`（重犯 P3-P）。改标 `SOURCE_INFERENCE` + `CERTAINTY_PROBABLE`（语义准 + 真被挡）
- **接缝修复（发现 2）**：候选只能照抄原文（程序不自己写句子），而候选又从"同一件事 ≥3 条重述"的簇里生成 → `adopt` 打分时候选与重述**分数完全相同**，S2 的 `top1 ≥ 2×top2` 必然不成立（gate② 结构性落空）。用户拍板"**候选当代表**"：`_tool_adopt` 命中候选时，与它同家的重述（Jaccard ≥0.35）不参与并列判定；**无候选时行为完全不变**
- **测试**：`test_sediment.py` 新增 9 项（纯函数）；`test_heartbeat.py` 新增 2 项（候选落库字段 + 脉冲 / 不重复递）；`test_expression_service_memory.py` 新增 1 项（候选优先于同家重述）
- 门禁：ruff lint ✅ / ruff format ✅（83 文件）/ mypy strict ✅（51 源文件）/ pytest ✅ **330 passed**（318 + 新增 12）
- **生效需重启灵魂**（`soul.ps1 stop` → `start -Body`）——重启后她会多一种"心里一动"（某件事被反复提起），话里一个字不提。**S4 观测与验收待动工**
- **粒度天花板（照实说）**：字符二元组 Jaccard 只能沉淀"措辞相近的反复提起"（同义重述仅 0.26~0.33），真语义模式待 embedding（`P3_MEMORY.md` 第九节问题 3）

---

## 近期规划 — 深入完善当前已完成内容（2026-09-20 起）

> 决定：不再推进新阶段（P4），转入**对 P0-P3 已交付内容的深入完善**。这是收口期——把已实现的能力打磨到真实可用、可感知、稳定。

### 待完善方向（按价值/依赖排序）
1. **记忆运行验证**：P3-G 已接线，但缺乏真实运行的端到端验证——需实际聊天观察记忆写入/检索/被提起是否如预期
2. **打字对话体验**：user_message 已注入，需实际打磨回复的自然度、上下文关联（这是"深入完善"最直接的抓手）
3. **人设一致性打磨**：口头禅/意象/情绪的边界微调（P2-H 已收紧意象，持续观察）
4. **记忆检索质量**：当前简单遍历打分，可优化检索精度/去重/时效
5. **桌宠体验**：气泡显示、动画、交互反馈的细节完善
6. **呈现层强化**（灵感池）：状态可视化、独处可见、动画强化——让生命感可感知

### 约束（铁律不变）
- 深入完善仍是当前已完成能力的增强，不引入新阶段架构
- 每项改动过 pre-commit 门禁（ruff+mypy strict+pytest）
- 关联灵感池待排期项暂不实现，除非与"完善现有内容"直接相关