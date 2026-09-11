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