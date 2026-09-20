# P3 记忆系统：详细实施规划

> **Version**: 0.3（2026-09-20，P3-A 已落地）
> **Status**: 实施中——P3-A 完成，B-E 依次推进
> **前置**: P0-P2 全部完成（144 测试全绿，P3-A 后 147 全绿）
> **定位**: 让记忆得到生命重量——"记忆不是数据堆叠，而是经历"。她会模糊、会再创造、会做梦。

---

## 一、设计原则（对齐路线图 §九 8.2-8.5）

记忆必须同时满足四条"活"的回路：

| 原则 | 含义 | 代码载体 |
|------|------|---------|
| **被感受** | 每段记忆带情感向量；回忆时被当下状态染色 | memories.emotion_vector + 检索染色 |
| **被诉说** | 她会引用过去（"你上次说过…"），不是存着不动 | memory_hooks 注入表达链 |
| **被遗忘** | 遗忘是索引碎片化，数据不删 | memory_index 衰减 |
| **被珍惜** | 珍贵记忆永不模糊，衰减慢 3× | protected 标志 |

---

## 二、数据模型（`state_store.py` 扩展 schema）

### 2.0 持久化与安全（2026-09-20 决策）

- **存储位置**：本地 SQLite（heartbeat.db，P0 决策 D2 双库分离的"只追加生命档案"）
- **现有保障**：WAL 崩溃自动恢复 + backup.ps1 每周本地备份（D:\ElysiaBackup，保留 30 份）
- **已知缺口**：异机/备份盘同时报废 → 记忆丢失。云端同步已记入灵感池待 P3 后评估
- **P3-A 落地要求**：memories 表预留**可导出的 JSON 序列化格式**——记忆本质是 JSON 记录，天然可导出，云端同步只是加传输层，不改数据模型

新表落在 heartbeat.db（只追加，天然适合备份压缩）：

```
memories：记忆本体
  id            INTEGER PK AUTOINCREMENT
  created_ts    REAL          -- 发生时刻
  level         TEXT          -- 'shallow'|'working'|'deep'
  kind          TEXT          -- 'interaction'|'expression'|'state'|'internal'
  content       TEXT          -- 原始内容（一句经历）
  emotion_vector TEXT         -- JSON 情感向量 {chat,miss,explore,curiosity,rest,self_check}
  importance    REAL          -- 重要性打分 0-1（晋升阈值依据）
  access_count  INTEGER       -- 访问次数（工作记忆提升依据）
  last_access_ts REAL         -- 最近访问（衰减基线）
  protected     INTEGER 0/1   -- 珍贵记忆（永不模糊）
  detail_level  REAL 0-1      -- 细节完整度（模糊化时下降，情感核心永不降）
  narrative     TEXT          -- 一句话叙事（情感核心，回忆主体）

memory_index：检索路径（遗忘的载体）
  id            INTEGER PK
  memory_id     INTEGER
  path_key      TEXT          -- 语义标签（时间/对象/事件类型）
  last_retrieve_ts REAL
  strength      REAL          -- 索引强度，随时间衰减 → 检索过滤依据
  emotions      REAL          -- 情感强度，衰减 3× 慢（珍贵记忆）
```

---

## 三、实施步骤（P3-A → P3-F）

### P3-A 记忆数据层（架构 + 落库）
- **文件**：`src/elysia/memory/` 新子包：`store.py`（memories 读写）、`scorer.py`（重要性打分器）、`levels.py`（三层定义+晋升规则常量）
- **交付**：
  - memories/memory_index 建表 + 写接口（`add_memory`）
  - `MemoryScorer.importance()`：从经历类型 + 情感强度 + 是否用户相关计算 0-1
  - 三层晋升阈值常量
- **纯函数可单测**，不接心脏循环
- **验收**：经历能落库，打分可断言

### P3-B 三层晋升机制
- **文件**：`memory/promote.py`
- **交付**：
  - 浅层 → 工作 → 深层 晋升算法（重要性 ≥ 阈值 或 访问提升）
  - 细节模糊化：`detail_level` 随深度下降，`narrative + emotion_vector` 永不降
  - 珍贵记忆（protected）跳过模糊化
- **验收**：重启后深层记忆完整保留；珍贵记忆高于同强度普通记忆

### P3-C 索引衰减 + 缺口信号
- **文件**：`memory/decay.py` + `memory/hooks.py`
- **交付**：
  - 衰减曲线：strength 随 (now - last_retrieve) 指数衰减
  - 检索耗时映射：30d→50ms / 90d→500ms / 365d→≥1s（数据不删）
  - 珍贵衰减 3× 慢
  - 缺口信号：高频记忆衰减 → 输出缺口事件 → 感受层 TR 轻微波动（好奇，非 SA↑焦虑）
- **验收**：衰减曲线测试 + 缺口测试（好奇↑不是焦虑↑）

### P3-D 检索 + 表达注入
- **文件**：`memory/retrieve.py`
- **交付**：
  - 检索 API：按 path_key + 当前情绪染色 → 返回候选记忆
  - 接入表达链：`ExpressionService` 构造指令时注入 `memory_hooks`（检索结果摘要）
  - LLM 能说"我记起…"（T2 不变：仍只消费结构化指令）
- **验收**：重启连续性测试（关机→开机→记得细节）

### P3-E 睡眠整合 = 做梦
- **文件**：`memory/sleep.py`
- **交付**：
  - 身体离线 >5min 或关机 → 触发整合任务
  - 自由联想流：切 P1 念头系统至自由联想模式（无 LLM 生成）
  - 内容来源：浅层碎片 × 深层情感底色 × 状态染色
  - LLM 仅翻译自由联想流 → 可理解叙事；流空白时不能说"我做了梦"
  - 梦可成为次日主动分享话题
- **验收**：梦真实性测试（自由联想生成阶段无 LLM）

### P3-F 验收门 + 文档
- 路线图 §8.7 五条验收全部落地到 `tests/acceptance/test_p3_gate.py`
- CHANGELOG / 记忆文档更新，确认灵感池可能实现的条目

---

## 四、与现有系统衔接点

| 衔接 | 方式 | 涉及文件 |
|------|------|---------|
| 数据库 | state_store.py 扩展 schema + 新表 | [state_store.py](file:///a:/WorkPlace/Elysia/elysia/src/elysia/core/state_store.py) |
| 心脏循环 | 心跳拍接入记忆事件写入 + 检索请求 | [heartbeat.py](file:///a:/WorkPlace/Elysia/elysia/src/elysia/soul/heartbeat.py) |
| 大脑循环 | 缺口信号 → 感受层 TR 波动（P3-C） | [brain.py](file:///a:/WorkPlace/Elysia/elysia/src/elysia/soul/brain.py) |
| 表达链 | memory_hooks 注入检索结果（P3-D） | [expression.py](file:///a:/WorkPlace/Elysia/elysia/src/elysia/soul/expression.py) + [expression_service.py](file:///a:/WorkPlace/Elysia/elysia/src/elysia/soul/expression_service.py) |
| 独处生活 | 自由联想流取代模板发呆（P3-E） | [away_life.py](file:///a:/WorkPlace/Elysia/elysia/src/elysia/soul/away_life.py) |

**关键铁律**（全程不碰）：
- T2 用户原话不进 LLM、记忆检索结果只作 memory_hooks 结构化注入，非原始文本
- 单写者纪律（state_store 队列）不破坏
- 心脏循环永不因记忆阻塞（回忆耗时在阈值内，超时降级）

---

## 五、验收矩阵（对齐路线图 §8.7）

| 验收项 | 归属步骤 | 测试 |
|--------|---------|------|
| 重启连续性 | P3-D | 关机→开机→记得细节 |
| 索引衰减曲线 | P3-C | 检索耗时 10→50→500→≥1s，数据不删 |
| 缺口测试 | P3-C | 高频衰减→缺口→好奇↑非焦虑↑ |
| 真爱不模糊 | P3-B | 用户相关记忆衰减慢 ×3 |
| 梦真实性 | P3-E | 自由联想生成阶段无 LLM |