# Elysia 开发日志

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

### P3-I 记忆缺陷修复：回声排除 + 短期新鲜度 + 晋升接线（进行中）
- **fix（问题一：重启重复上次的话）**：检索把她的 `KIND_EXPRESSION` 发言回声也当候选，最新一句情绪匹配最高分反复注入。`select_hooks` 现排除 `KIND_EXPRESSION`——hooks 只用于"记起你/世界"，不复述刚说过的自己
- **fix（问题二：短期记不住昨天的大餐）**：打分无时效性，昨天与一月前的记忆权重相同，只凭情绪打架。`score_memory` 新增新鲜度因子 `_recency_factor`（RECENCY_BOOST=0.55 / RECENCY_DAYS=7），近 7 天给显著加成后衰减——短期记忆可靠召回，长期沉淀仍由晋升负责。`select_hooks`/`retrieve_from_store`/retriever 透传 `now`
- **feat（记忆持久化根因）**：P3 记忆系统"造好没插电"——`promote_batch` 运行时从未被调，所有经历永远停浅层。心跳新增 `_maintain_memories`（每 300 拍扫描晋升浅层→工作→深层落库），管长期沉淀
- 新增单测：短期 vs 长期新鲜度找回 + 回声排除；全量门禁绿

### P3-J 记忆"重启后记不住"根因修复：LLM 消费 memory_hooks（进行中）
- **定位**：存储与检索均正常（真实库探针验证 88 条记忆全落盘，生日记忆在任意情绪下都能召回进 hooks）。断点在 **LLM 消费端**——`_SYSTEM_PROMPT` 只教她消费 `user_message`，从未告诉她 `memory_hooks` 是什么/要不要用
- **机制**：同一会话内"记住"其实是复述 `user_message`（用户刚说的含日期）；重启后 `user_message` 只剩问句、不含事实，被忽略的 hooks 无法补位 → 只能回避"哪一天不重要/记不太清了"。这与"重启是分水岭"完全吻合
- **fix**：`deepseek.py` 系统提示新增 memory_hooks 指令——对方明确问具体事实（生日/名字/约定）时，从 hooks 中挑对应那条**直接说出事实本身**，不用回避话术；hooks 无确切答案再如实说不记得。T2 不变（仅作记忆参考，非逐条复述）
- **方向修正（能力与选择权分离）**：memory_hooks 指令收敛为——她**有能力**想起这些事实，但**用不用、怎么回应是她自己的权力**（如实/含糊/只字不提/装不记得，全凭性格与心情）。定位明确：程序管"会不会"（把事实可靠递到手），她管"用不用"；LLM 只是帮她说话的工具。早期 T2 的"不承诺/不编造"仍是测试期脚手架，后期真实电子生命不以此作硬锁

### P3-K 记忆生命周期接线补齐：access_count 递增 + 索引衰减（遗忘落地）
- **fix（access_count 从不变）**：检索是纯只读路径，`PROMOTE_SHALLOW_ACCESS=2` 永远无法触发。`state_store` 新增 `touch_memory`（命中递增 access_count + 更新 last_access_ts），`retrieve_from_store` 命中后触碰——"被想起的次数"真正成为晋升依据
- **feat（遗忘接线）**：`decay.py` 的索引衰减（strength 指数衰减 / protected 慢 3× / floor）此前零调用者，`memory_index` 表从不写入。`_maintain_memories` 现补全两件事：①晋升 ②为每条记忆建索引 + 按年龄衰减 strength 落库。检索时从 `memory_index` 读 strength 参与打分（索引弱 → 分低 → 更难被想起）
- **根因核实（88 条全浅层）**：真实库探针验证——非规则问题，是**旧代码未含维护循环**。重启新代码后 97 条记忆已沉淀为 working=60 / deep=36 / shallow=1，晋升运行正常；memory_index=0 行证实衰减确为本次新接线
- 新增单测：检索命中递增 access_count / 索引 strength 参与打分 / 维护晋升+衰减落库；全量门禁绿

### P3-L 记忆准确率：修正/覆盖 + 衰减幂等
- **fix（衰减复合塌缩）**：`_maintain_memories` 曾以上一次 strength 作基线再乘年龄因子，导致每跑一次维护就衰减一次——按"运行次数"复合而非绝对年龄（1 天龄记忆跑满 1 天维护 288 次后 strength 塌到 0.0017，且与机器转速耦合）。改为从固定基线 1.0 按绝对年龄幂等重算：跑 1000 次与跑 1 次结果一致
- **feat（修正/覆盖）**：记忆此前只增不改——用户更正事实后旧错误记忆仍在候选池，新鲜度归零时会反超为第一（诊断实测：7 天后旧的错误生日排到首位）。新增：
  - `memory/supersede.py`：字符二元组 Jaccard 同话题判定（无 NLP，中文友好），新记忆写入时取代更旧的同话题记忆
  - `memories.superseded_by` 列（旧库幂等迁移补列）+ `mark_superseded` 落库 API
  - 检索排除被取代记忆（数据保留可溯源，只是不再召回）
  - 心跳写入用户输入后接线 `_supersede_conflicts`
- 新增单测：相似度/取代判定/检索过滤/落库往返 + 衰减幂等 + 修正接线；全量门禁绿

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