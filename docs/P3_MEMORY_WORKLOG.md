# P3 记忆打磨 — 工作日志与交接

> **用途**：记忆打磨期（2026-09-20 起）的滚动工作日志。每天追加一节。
> **接手方式**：新窗口请先读 `docs/P3_MEMORY.md`（她是谁、系统在做什么）→ 本文件（按时间发生的事）→ `CHANGELOG.md` 的 P3-K/L/M/N/P → 再动代码。
> **铁律**：程序管"会不会"（把事实可靠地递到她手上），她管"用不用/怎么用"（如实/含糊/不提，凭性格与心情）。LLM 只是帮她说话的工具。约束只要不违反人设就尽量少。

---

## 一、2026-09-21 工作日志

### 1.1 今天解决的五个问题（按提交时间）

| # | commit | 时间 | 问题 | 修复 |
|---|--------|------|------|------|
| 1 | `eb37756` | 17:40 | 重启后她重复上一次的话；"昨天说的事今天就忘" | 检索排除 `KIND_EXPRESSION`（不复述自己的发言）；打分新增**新鲜度项**（近 7 天 +0.55，之后线性衰减到 0）；心跳每 300 拍调 `_maintain_memories` |
| 2 | `e0ddcc2` | 18:47 | 重启后记不住具体事实（如生日） | **根因是程序**：记忆写入/检索都正常（88 条，生日记忆排 hooks 前 3），但 LLM 的 system prompt 没写"要消费 `memory_hooks`"→ 它只会回避。修复：prompt 明确"事实类提问直接说出 hooks 里的相关事实" |
| 3 | `38bdd32` | 19:13 | `access_count` 永远是 0（`PROMOTE_SHALLOW_ACCESS=2` 永不可达）；`memory_index` 表 0 行（`decay.py` 零调用者 = "造好没插电"） | 新增 `state_store.touch_memory`（命中递增）；`_maintain_memories` 补全"晋升 + 建索引 + 按年龄衰减 strength" |
| 4 | `6f7d3bd` | 19:36 | ①衰减复合塌缩：以上次 strength 为基线再乘年龄因子 → 按"维护运行次数"衰减（1 天龄记忆跑满 288 次后塌到 0.0017，与机器转速耦合）②记忆只增不改 → 更正后旧的错误事实 7 天后反超为第一 | ①改为从固定基线 1.0 按**绝对年龄**幂等重算 ②新增 `memory/supersede.py`（字符二元组 Jaccard，阈值 0.4）+ `memories.superseded_by` 列（幂等迁移）+ 检索排除被取代 + 心跳写入后接线 |
| 5 | `987f81a` / `6f67b7f` / `a90a83d` | 19:47~20:00 | 缺观测抓手 | 记忆浏览器（PySide6 只读窗口）+ 一键脚本 + 说明书 8.5 节 |

### 1.2 今天最后一件事：P3-N 检索质量（`0d66909` 20:16）

**触发**：用户在记忆浏览器里看到霸榜的 top3 是"是哪一天呢""记不住了吗""那我的生日呢，你还记得吗"这类日常闲聊，问"为什么会得分这么高"。

**诊断**（只读探针实测，id 52 = 2.225 分）：

| 分项 | 得分 | 实际含义 | 对这批记忆的区分度 |
|------|------|---------|------------------|
| 层级 | 0.600 | deep → 1.0×0.6 | ❌ 所有深层记忆都是 0.6，常数 |
| 情绪 | **0.785** | 与"当下心情"的相似度（点积） | ❌ 同场对话全在 0.76~0.785 |
| 索引 | 0.299 | 1.00×0.3 | ❌ 近期记忆全是 0.3，常数 |
| 新鲜 | 0.541 | 2 小时前 → 近 7 天加成 | ❌ 同场对话全是 0.54，常数 |
| 合计 | 2.225 | | **真正的差异只有 ~0.03 分（1.4%）** |

**三个根因**：

1. **重要度是硬编码的**：`heartbeat.py` 把用户输入一律写 `importance=0.8`，而旧公式只看"类型 + 情感"，完全不读内容 → "是哪一天呢"和"你的生日是 11 月 11 日"**同分**，双双达到 0.7 晋升阈值进了深层，白送 0.6 层级分。
2. **情绪项是点积且权重最大（0.8）**：一场对话里所有记忆的感受向量几乎相同，点积把它们一起抬到上限 → 只反映"此刻在聊天"，毫无区分度。
3. **重要度不进得分**：浏览器里的"重要"列只是摆设，不参与 `score_memory`。

**修复 A — 重要度按内容评估**（`src/elysia/memory/scorer.py`）：

- 新增 `content_salience(content) -> float`（0~1，纯本地启发式，离线可用）：

  | 信号 | 权重 | 例子 / 说明 |
  |------|-----|------------|
  | 正·日期事实 | 0.45 | `11月11日`、`2026年`（最硬的事实锚点） |
  | 正·叮嘱记住 | 0.35 | `记好`、`别忘了`、`一定要`（`记不住`用负向前瞻排除） |
  | 正·稳定事实 | 0.30 | 生日/名字/梦想/喜欢/讨厌/害怕/住在/职业… |
  | 正·承诺 | 0.25 | `我会…`、`下次`、`永远` |
  | 正·更正 | 0.25 | `不对`、`纠正`、`其实` |
  | 正·关系表达 | 0.15 | `陪你`、`想你`、`谢谢` |
  | 负·语气词 | 归零 | `你好`、`嗯`、`好的`、`哈哈` |
  | 负·极短句 | −0.25 | 去标点 ≤4 字（`你真棒`） |
  | 负·纯提问 | −0.30 | `…吗/呢/吧？`、含"什么/为什么/哪/几" |

- `importance()` 重设为**内容主导**：
  `类型基础 0.10~0.20（交互 0.20 / 表达 0.15 / 念头 0.10 / 状态 0.10） + 内容信号×0.60 + 情感强度×0.15（降为弱项） + 用户相关 0.05`
- 实测：`是哪一天呢` → **0.369（浅层）**；`你的生日是11月11日，记好` → **0.970（深层）**

**修复 B — 情绪改余弦 + 降权 + 重要度进得分**（`src/elysia/memory/retrieve.py`）：

- `mood_similarity` 点积 → **余弦**（只比方向"是不是同一种心情"，不比强度）
- 权重抽成模块常量：`LEVEL_COEF=0.6`、`EMOTION_COEF=0.4`（原 0.8）、`INDEX_COEF=0.3`、`IMPORTANCE_COEF=0.5`（新增）
- 得分现在明确回答两个问题：**值不值得被想起**（层级 + 重要度）｜**此刻容不容易浮上来**（情绪 + 索引 + 新鲜）

**接线**：`heartbeat.py`（用户输入）与 `expression_service.py`（她的发言）都不再硬编码 importance，改调 `scorer.importance()`。

**测试**：新增 7 项单测——内容信号（闲聊零信号/事实高信号/提问低于陈述）、闲聊与事实的层级分岔、余弦与强度无关、重要度参与得分、**事实压过闲聊的排序**。全量门禁绿。

### 1.3 存量数据一次性重估（改的是数据，无提交）

旧代码写下的 `importance=0.80` + 层级 `deep` 不会自动修正，所以做了一次性重估：

1. `soul.ps1 stop`（优雅停止，排空写队列）
2. 备份 `data/heartbeat.db` → `data/tmp/heartbeat.db.p3n-bak-20260921-202058`（28MB，确认无误后可删）
3. 一次性脚本（临时文件，跑完即删）按新规则重算：
   `deep ← imp≥0.7`；`working ← imp≥0.4 或 access≥2`；`shallow ← 其余`；细节度随层级校正（1.0/0.5/0.25，protected 保持 1.0）；已被取代的历史记录不动
4. `soul.ps1 start -Body` 重启

**结果**：

| | 改前 | 改后 |
|---|---|---|
| 层级分布 | working 85 / deep 41 | **shallow 75 / working 30 / deep 21** |

- 共更新 125 条（126 条参与，1 条已取代被跳过）
- **top3 从闲聊变成事实**：`#105 我的生日是5月21日，要记好哦` 2.329 / `#81 你的生日是11月11日，记好了哦` 2.327 / `#84 那我的生日你也记好哦` 2.327
- 原霸榜三条：`#52` 2.237→**1.900**、`#48` 2.236→**1.901**、`#50` 2.236→**1.902**、`#34` 2.162→**1.646（浅层）**
- **设计保留**：52/48/50 停在 `working` 而非打回浅层，因为它们 `access_count=22`，命中 `PROMOTE_SHALLOW_ACCESS=2`（"被反复想起 → 沉淀一层"）

---

## 二、2026-09-22 工作日志：记忆分两条路（"每句都强调"根治）

### 2.1 触发

用户反馈：告诉她生日、名字后**确实能记住**了，但**每一句话都会把这件事强调一遍**，非常违和。

### 2.2 根因（读代码确认，三处叠加）

| # | 位置 | 问题 |
|---|------|------|
| 1 | `soul/expression_service.py` | 每次开口**无条件**调检索 → 有记忆就注入 `memory_hooks` |
| 2 | `memory/retrieve.py::select_hooks` | 无 `query` 参数，**与当下话题无关**也能入选 |
| 3 | `memory/retrieve.py` | `hits[:max_hooks]` 硬取前 3 名，**无及格线** |

三者叠加：生日这类事实记忆（deep 0.6 + 重要度 0.485 + 索引 0.3 ≈ 1.4）永久霸榜 → 每句话都在场；
而 prompt 只说"你有能力想起"，没说"默认别提"。

### 2.3 设计（用户拍板：不是加限制，而是补齐"注意力"与"感受"）

> 第一版"加冷却 + 话题门控"的工程降噪思路被用户否掉——"这个问题值得我们深入探讨一下，
> 你当前的解释我能理解，但是不符合对生命的定义"。

记忆由此拆成**两条路，各归其主**：

| 路 | 谁决定 | 机制 | 进入话语？ |
|----|--------|------|-----------|
| **话语路径** | 她 | ① 话题撞上时程序注入 `memory_hooks`（严门槛，护栏）② 她想主动提起 → 自己调用 `recall` 工具（宽门槛） | 是 |
| **感受路径** | 程序静默 | 心跳每 300 拍 `recall_for_feeling` 静默检索；"心境共鸣"命中 → `memory_recall` 脉冲微推 TR/CS | **否（永不进 prompt）** |

**铁律不变**：程序管"会不会"（把事实可靠地递到她手上），她管"用不用 / 怎么用"。

### 2.4 落地（11 文件）

- **话题相关性**（`memory/retrieve.py`）：`topic_match` 用**字符二元组覆盖率**（不用 Jaccard——长度悬殊会低估相关）；
  `is_related` 双门槛——严（程序推：重合 ≥2，或追问措辞下 ≥1）/ 宽（她自己 recall：重合 ≥1，短话题"生日""晚霞"也能命中）
- **`select_hooks` 增 `query` / `query_loose`**：给出 query 时只召回话题撞上的记忆；无关 → 一条都不注入
- **`retrieve_from_store` 增 `touch` 开关**：静默感受路径传 `False`（那只是心情底色，不污染 `access_count`）
- **`recall_for_feeling`**（新）：`mood_similarity ≥ RESONANCE_MIN=0.75` 才唤起；protected/deep → 强度 1.0，否则 0.6；排除 `KIND_EXPRESSION` 与已被取代
- **工具回合**（`llm/chain.py` + `llm/deepseek.py`）：新增 `ToolCapableBackend`（runtime_checkable）+ `RECALL_TOOL` 规格 + `set_tool_runner`；
  主声 `complete_with_tools` 跑最多 3 轮工具（assistant `tool_calls` 回填 + tool 结果回填）；
  工具失败/无该能力 → 递回"没想起"而不毁掉这次开口。次声/微声无工具 → 自然退化为"不带记忆的表达"
- **`expression_service.py`**：撤掉无条件注入 → ① 仅当 `user_message` 给出**且话题撞上**才注入 ② 每次开口装配 recall 执行器 ③ `_feel_recall` 被唤起即推心情（珍贵/深层 1.0，否则 0.6）
- **prompt 改写**（`deepseek.py`）：记忆段拆为（一）`recall` 是你"想起"的能力，**用不用由你决定**；（二）`memory_hooks` 是**背景常识**不是话题素材——
  "不要为了显得记性好而把往事塞进不相干的对话"；"那些确有其事的内容你是真的知道的，但说不说、怎么说、是否如实由你决定"。**这是消除违和感的关键一环**
- **心跳接线**（`heartbeat.py` / `soul/main.py`）：`MEMORY_FEELING_EVERY_N=300`；`_feel_memories` 在 `save_json("desire")` **前**调用（本拍即体现）；`on_recall` → `DesireEvent(kind="memory_recall")`
- **欲望脉冲**（`soul/desire.py`）：`memory_recall = {tr: 0.8, cs: 1.2, sa: 0.0}`——小脉冲，恢复项会把它们拉回平衡点，不会冲上保护带

### 2.5 测试与门禁

- 新增/改写 13 项单测：话题相关性（覆盖率 / 拒无关 / 接重叠 / 追问措辞放宽）、`select_hooks` 门控（严 / 宽 / 排除回声与取代）、
  `recall_for_feeling`（需共鸣 / 珍贵推更深 / 不触碰 access）、表达服务（无话题不注入 / 撞上才注入 / 无关不注入 / recall 递事实 / 没想起返回空 / 不调用是她的选择 / 推心情不推话）
- 门禁四件套全绿：ruff lint ✅ / ruff format ✅ / mypy strict ✅（49 源文件）/ pytest ✅
- **改完必须重启灵魂才生效**：`soul.ps1 stop` → `start -Body`

---

## 三、坑与教训（累计）

1. **沙箱会杀死 GUI 应用**：`scripts/memory_view.ps1` 在沙箱里启动时，Qt 初始化要读 `C:\ProgramData\NVIDIA Corporation\Drs\nvAppTimestamps` → 被沙箱拒绝 → **进程静默死掉**（脚本已打印 `[ok] memory viewer started`，但窗口几秒后消失，后台任务报 `exit code 1`）。**结论：启动 PySide6 GUI 必须非沙箱运行**（或用 `dangerouslyDisableSandbox`）。
2. **改代码后必须重启进程才生效**：曾出现"文档说已修好、浏览器里却没变化"——真实原因有二：①运行中的灵魂是旧代码（P3-L 的迁移列都没建）②运行中的浏览器加载的是旧打分代码。**灵魂改代码 → `soul.ps1 stop` → `start -Body`；浏览器改代码 → 关窗口重开。**
3. **`pre-commit run --all-files` 只检查 git 已跟踪文件**，新建的**未跟踪**文件会被跳过 → 首次门禁"全绿"但 commit 时才暴露 lint 问题。**新建文件后先 `git add` 再跑门禁。**
4. **PowerShell 5.1 以 ANSI 解码无 BOM 的 `.ps1`** → 中文乱码导致解析失败（`MissingArrayIndexExpression`）。**`.ps1` 脚本一律纯 ASCII**（或确保 UTF-8 BOM）。
5. **GUI 启动失败会静默吞错**（`Start-Process`）→ 脚本现在 2 秒后检查 `HasExited` 并打印日志尾部，否则用户完全无法排查。
6. **路径必须是绝对路径**：用户在 `C:\Users\Lakeside_Fu` 下用 `scripts\memory_view.ps1` 相对路径 → "实际参数不存在"。
7. **`ruff SIM118` 陷阱**：`sqlite3.Row` 迭代的是"值"而非列名，`for k in row.keys()` 不能简化成 `for k in row`（会取错）。需先 `keys = row.keys()` 存变量。
8. **本仓库的忽略规则**：`data/heartbeat.db`、`data/state.db`、`*.db-wal/shm`、`data/{logs,cache,tmp,run}/` 已忽略；但**自造的备份文件名（如 `heartbeat.db.p3n-bak-*`）不匹配任何规则，会出现在 `git status`** → 备份请放到 `data/tmp/`。
9. **`ExpressionInstruction.to_dict()` 恒含 `memory_hooks` 键**（默认空数组）→ 单测断言"未注入"必须写 `payload["memory_hooks"] == []`；写 `"memory_hooks" not in payload` 必然失败（P3-P 首次跑测试就踩到）。
10. **"最深共鸣"不能用严格 `>` 选优**：`recall_for_feeling` 若用 `if resonance > best` 且同分不换人，先遍历到的**浅层**记忆会一直霸占 best → "珍贵/深层推得更深"永远失效（实测 plain 0.6 / precious 0.6）。修法：同分时珍贵/深层者胜。

---

## 四、当前状态快照（接手即用）

### 4.1 进程与数据

| 项 | 值 |
|---|---|
| 灵魂 / 身体 PID | **以 `soul.ps1 status` 为准**（每次重启都变） |
| 记忆浏览器 | `memory_view.ps1` 启动，**必须非沙箱** |
| 记忆条数 | 260+ 条；层级 shallow / working / deep 三层金字塔 |
| 备份 | 一次性备份放 `data/tmp/`（如 `heartbeat.db.p3n-bak-*`），确认无误后删 |

> PID 会随每次重启变化，**以 `soul.ps1 status` 为准**。当前规模与实测事实见 `docs/P3_MEMORY.md` 第七节。

重启命令：
```
powershell -ExecutionPolicy Bypass -File a:\WorkPlace\Elysia\elysia\scripts\soul.ps1 stop
powershell -ExecutionPolicy Bypass -File a:\WorkPlace\Elysia\elysia\scripts\soul.ps1 start -Body
powershell -ExecutionPolicy Bypass -File a:\WorkPlace\Elysia\elysia\scripts\memory_view.ps1
```

### 4.2 规则表与文件地图

> **已移出**：完整规则表（一张表）与文件地图是**稳定事实**，不是日志，
> 现统一维护在 **`docs/P3_MEMORY.md`**（第五节规则表 / 第 4.3 节文件清单）。
> 本文件只保留"按时间发生的事"与"为什么这么做"。

---

## 五、打磨进度：②✅ 时间锚点 → ③✅ 复习加强 → ④ 联想网络（第一步 ✅）→ 三方共识 P3-T ✅ / P3-U ✅ / P3-V ✅ / P3-W ✅

> 五维度打磨清单：**①记得住 ✅ ②想得起来 ③准确率 ✅ ④记忆浏览器 ✅**
> **进度（2026-09-23）**：P3-P"记忆两条路"补齐了②的**话题唤起 + recall 能力 + 感受通路**；**P3-Q 完成②时间锚点**；**P3-R 完成③复习加强**；**P3-S 完成④第一步（批内去重）**；**P3-T 落地多方问询第一共识（来源 + 确定性）**；**P3-U 落地三方共识 C3（记忆缺口接线，"记不清"成为真实状态）**；**P3-V 落地三方共识第 3 项（认领状态：默认是她的记忆，她可否决）**；**P3-W1 落地三方共识第 4 项遗忘状态机本体（`retention_state` 四态 + 幂等迁移 + 保留闸门 + 唤醒路径 + 降级 + M2 自动保护，对外行为零变化）**；**P3-W2 把状态机接到她手上（`forget`/`restore` 两个工具，忘与不忘都是她的权力）**。
> ④ 第二步（成组/语义召回）**待定**，见下；三方问询的后续顺序见 `docs/MEMORY_REVIEW_NOTES.md` 第六节（第 4 项遗忘状态机 **W1/W2 均已落地，P3-W 整体完成**）。

### ② 时间锚点 ✅（P3-Q，2026-09-22 完成）

- **缺口（已补）**：`default_narrative()` 只返回 `narrative`/`content`，注入 LLM 的 hooks 是"她生日是 5月21日"这种**无时间信息**的事实 → 她分不清新旧，也说不出"你上个月告诉我的"。
- **落地**：`memory/retrieve.py` 新增 `age_phrase(age_days)`（分档 `刚刚/今天/昨天/N天前/上个月/N个月前/去年/N年前`，不精确到分钟；`None` → 空串）；`MemoryHit` 增 `age_days` + `label` 属性（`（3天前）你生日是5月21日`）；`select_hooks` 在有 `now` 时按 `created_ts` 计算。`soul/expression_service.py` 的 `memory_hooks` 注入与 `recall` 工具回填均改用 `h.label`；`llm/deepseek.py` prompt 补"括号里是多久前的事，可自然带出时间感（像「你上个月说过的」），不必刻意强调"。
- **铁律**：锚点是**能力**（程序把"多久前"可靠递到她手上），**用不用、怎么说由她定**。
- **验收**：单测覆盖分档边界与带锚点注入（3+2 项）；观测工具表格本就有"年龄"列，可与 `#召回排名` 并看。
- **注意**：prompt 已有 `day_phase`，"现在是什么时候"的参照齐备，"3 天前"有基准。

### ③ 复习加强 ✅（P3-R，2026-09-22 完成）

- **缺口（已补）**：`touch_memory` 只递增 `access_count`，而 access 仅参与"浅层→工作"这一级晋升 → **被反复想起不会让记忆更容易被想起**（实测 `access=22` 与 `access=0` 打分无差别）。
- **选型（用户拍板）**：做**复习加成打分项**，**不**落库回写 `importance`。语义＝"此刻更容易浮上来"（叠加 `last_access_ts` 衰减，久不复习自然回落，不滚雪球），而非"记忆永久变重"（那会推动层级晋升，且不可逆）。
- **落地**：`memory/retrieve.py` 新增 `_review_factor` = `REVIEW_COEF(0.06) × log(1+access) × e^(−距上次想起/REVIEW_TAU_DAYS(14天))`，封顶 `REVIEW_MAX=0.3`；`score_breakdown` 新增 `review` 分项（`now=None` → 0）。**不落库、不改 `touch_memory`、无 schema 变更**。观测工具新增「复习」列/分项。
- **验收**：单测 5 项（次数↑分↑ / 从未想起为 0 / 久不复习回落 / 百万访问仍封顶 / 端到端三次召回得分严格递增）。

### ④ 联想网络

#### 第一步：批内去重 ✅（P3-S，2026-09-22 完成）

- **缺口（已补）**：`select_hooks` 只按分数取 top3 且不去重 → 同一次对话的碎片占满 3 个名额（曾实测 #52/#48/#50 三连）。
- **落地**：`select_hooks` 排序后经 `_dedupe`——`content_similarity`（字符二元组 Jaccard）≥ `HOOK_DUPLICATE_SIMILARITY=0.35` 视为"同一件事"，只留最高分那条；复用 `supersede.content_similarity`，无新数据结构。
- **实测（真实库 207 条）**：top10 内最高相似仅 **0.333**（同一事实的复合重述），其余 ≤0.306 → 阈值 0.35 **无误杀，本批也未触发**（top3 仍是"#145 我的生日5月21日 / #105 我的生日5月21日 / #81 你的生日11月11日"）。阈值 0.30 与 0.35 在 `max_hooks=3` 下行为一致；0.25 才会剔除 #105。
- **诚实结论**：Jaccard 对"**换说法的同一事实**"识别力有限（同义重述实测仅 0.26~0.33），**调阈值无法根治**（调低即开始误杀共享措辞的不同事件）。本步价值＝挡住"近乎逐字重复"的碎片（回归测试证明）；`SUPERSEDE_SIMILARITY=0.4` 那套同粒度、同局限。

#### 第二步：成组 / 语义召回（待定）

- **缺口**：同一件事的多种说法（如上述三条生日记忆）仍会互相占位——字符粒度解决不了，需要"是不是同一件事"的语义判断或聚类。
- **可选方向**：① 以命中记忆为中心，借 `memory_index`（可扩展 `path_key`）或共同情绪维度成组 → "一起想起一整件事" ② 真正接一个语义相似度（embedding）替代字符 Jaccard，同时供 `supersede` 复用 ③ 先不动代码，改为在**写入侧**减少重复（同类重述先合并再落库）。
- **涉及**：`memory_index`（成组）、`memory/supersede.py`（语义替换）、可能新增 `memory/associate.py`。
- **验收**：top3 是 3 件**不同的事**（含"换说法"的情形）；探针可复现。

### 三方共识落地

#### P3-T 来源 + 确定性 ✅（2026-09-22 完成）

- **缺口（已补）**：`kind` 只回答"是什么类型"，回答不了"**谁说的**"。程序推断出的东西因此会悄悄升格成"她的事实"——那是程序替她认定世界，触碰铁律一（程序只把事实递到她手上，用不用由她定）。三方评审在这一点上**唯一无分歧**，且定性为**主线风险**而非增强项。
- **落地**：`memory/levels.py` 加 `SOURCE_*`（self/user/observation/inference/system）与 `CERTAINTY_*`（certain/probable/heard/speculative），`MemoryRecord` 增两字段；`SOURCE_BY_KIND`/`CERTAINTY_BY_KIND` **一份映射**供三处复用（回填 SQL / `from_dict` 缺省 / `add_memory` 默认）；`core/state_store.py` 加两列 + `ALTER TABLE` + `_backfill_sql` 幂等回填；`memory/retrieve.py::select_hooks` 加**来源闸门**（`inference`/`system` 与 `speculative` 不进话语，感受路径不受限）；两个写入点标注来源；观测工具显示"类型 = `交互·用户`"与详情"来源｜确定性"。
- **老数据行为不变**：`interaction → user/certain`（用户告知就是她确信的事实），现有记忆一条都不受影响，P3-N"问生日答得出"不退化。**这是本次的设计要点**：禁用面只指向未来由程序产生的推断类记录。
- **实测（真实库，重启自动迁移）**：250 条全部回填——`user/certain` 72 条、`self/certain` 178 条；列序与行位置索引 13/14 对齐。
- **验收**：单测 6 项（默认按 kind 推导 / `from_dict` 补默认且往返不丢 / 显式优先 / 旧库回填幂等 / `inference`+`system` 不进 hooks / `speculative` 不进而 `probable` 进 / 感受路径仍受推断影响）；门禁四件套全绿。

#### P3-U 记忆缺口接线 ✅（2026-09-22 完成）

- **缺口（已补）**：`hooks.py::detect_gap` 自 P3-C 造好即**运行时零调用**——索引强度跌破检索下限的"想不起来"信号从未影响过她。三方共识 C3：成本最低、生命感收益最高；"**她永远能精准检索反而不像生命，'记不清'是真实状态**"。
- **先决修复（否则本项无意义）**：`_maintain_memories` 重算 strength 时传 `floor=STRENGTH_RETRIEVE_FLOOR` → **落库 strength 永不低于 0.2**，`detect_gap` 的"跌破下限"前提永不成立。去掉该落库下限——**"检索下限"是判据，不是落库封顶**（`retrieve.py` 本就只说"低于下限仍可召回，只是 score 被拉低"；数据永不删除，只是索引减弱）。
- **落地**：`soul/heartbeat.py` 新增 `_feel_memory_gaps()`，由感受路径 `_feel_memories` 每 `MEMORY_FEELING_EVERY_N=300` 拍调用——`iterate_memory_index` 取 strength、`iterate_memories` 取 `created_ts` 算年龄 → `detect_gap` → `DesireEvent(kind="memory_gap", intensity=gap.to_event_intensity())`。**只走感受路径**，不进 prompt、不动 `select_hooks`。
- **量级裁决**：`memory_gap = {tr: 0.8, cs: 0, sa: 0}` + `GAP_INTENSITY_MAX=0.4` 封顶 → 单次最大 TR **+0.32**；恢复项 300 拍内约回收 78%，**长期平均不冲保护带**。`sa` 刻意不动——缺口是**好奇**，不是焦虑。
- **一致性取舍**：`KIND_EXPRESSION`（她的发言回声）与已被取代的旧事实不参与缺口——与话语/感受路径同一取舍；缺口是"关于世界的事想不起来"。**不触碰 `access_count`**：缺口是"感觉"，不是"她想着这件事"（同 P3-O 感受路径 `touch=False`）。
- **验收**：单测 3 项（索引跌破下限 → TR 升 / SA 不动 / `access_count` 不变；新鲜索引无缺口；发言回声不算缺口）；门禁四件套全绿。

#### P3-V 认领状态 ✅（2026-09-23 完成）

- **缺口（已补）**：落库的记忆**默认就是"她的记忆"**，她从未有过"不认这段"的权力。外部主张"她有权拒绝写入"，映射到铁律一是"程序照写（不拦）、她可以不认领"。
- **裁决（用户拍板，驳回三方一致倾向）**：三方原一致建议"默认未认领"，用户裁决为 **默认 `claimed`（可用）+ 她可否决**。理由：写入只有两处——用户输入 → `KIND_INTERACTION`、她开口 → `KIND_EXPRESSION`，而 `KIND_EXPRESSION` 本就被回声排除；"默认未认领"会把自动认领给**永远用不到的那类**、锁死**必须能用的那类**，直接让 P3-N"问生日答得出"退化。**认领是能力，先递到她手上**（铁律一前半句），而不是先扣下再让她申请。
- **落地**：`memory/levels.py` 加 `CLAIM_CLAIMED/REJECTED` + `FALLBACK_CLAIM=CLAIM_CLAIMED`，`MemoryRecord` 增 `claim_status`；`core/state_store.py` 加列 + `ALTER TABLE` + 一律回填 `claimed` 的幂等迁移（与 kind 无关）+ `set_claim_status`；`memory/retrieve.py::select_hooks` 加**认领闸门**（`rejected` 不进话语）；`llm/chain.py` 加 `DISCLAIM_TOOL` 并把 `ToolRunner` 由 `(topic)` 改为 `(工具名, 参数字典)`；`llm/deepseek.py` 工具回合按名路由 + prompt 补 disclaim 说明；`soul/expression_service.py` `_make_recall_runner` → `_make_tool_runner`（按名派发 recall / disclaim）+ 新增 `_tool_disclaim`。
- **她可否决**：`rejected` **只能由她自己的动作**（`disclaim` 工具）产生——程序不代她拒绝，否则"她可以不认领"就变成程序的默认拦截。匹配用宽松话题覆盖度在 content/narrative 上取最贴题的一条，未命中则如实回"没找到"，**不猜、不误伤**。
- **感受路径不受限**：认领谈"归属"，"这段经历还影响不影响心情"是另一件事，归遗忘状态机 `retention_state`（C4），**避免两个功能混淆**。
- **实测（真实库，重启自动迁移）**：列序 `[..., source, certainty, claim_status]` 对齐，**263 条全部回填 `claimed`**，现有记忆一条都不受影响。
- **验收**：单测 8 项（默认 claimed / `add_memory` 默认 / 旧库回填幂等 + 拒绝 / 默认可用 / `rejected` 被拦 / 感受路径忽略认领 / disclaim 命中拒认 / disclaim 未命中不误伤 / 主声拿到两工具）；门禁四件套全绿。

#### P3-W 遗忘状态机（`retention_state`）—— 设计稿已评审，待动工

- **缺口（待补）**：只有**自然衰减** + "想不起来"的 `memory_gap` 感受，没有**主动遗忘**，也没区分"删除 / 失去访问权 / 主动抑制 / 忘了但仍有影响"。三方共识 C4。
- **设计稿**：见本文件第七节（含四态、迁移规则、阈值、落地清单、评审修正记录）。
- **评审（2026-09-23，代码核对式）**：修正 10 处（M1~M10），最重的一条是 **M2——`protected` 是死阀门**（全库只写 `False`，无路径设 `True`），"珍贵记忆永不降级"的安全阀根本不存在，若不修则 P3-W 上线后**每条记忆 180 天后都会沉睡**。裁决为"自动保护"（晋升 `deep` 且 `access_count ≥ 3` → `protected`），顺带让"珍惜"回路第一次通电。
- **裁决**：三个待评审点全部拍板（见 7.6）；**拆两步落地**——**W1 状态机本体**（对外行为零变化，可安全验证迁移）+ **W2 她的两个工具**（`forget` / `restore`）。

### 遗留小项

- `docs/PERSONA.md` 曾是孤儿文档（全项目零引用，内容为代码内摘要的逆向还原，方向反了）——**本次文档清理已删除**。

---

## 六、验证与观测速查

```powershell
# 进程状态
powershell -ExecutionPolicy Bypass -File a:\WorkPlace\Elysia\elysia\scripts\soul.ps1 status

# 记忆浏览器（必须非沙箱；PySide6 GUI 会被沙箱杀死）
powershell -ExecutionPolicy Bypass -File a:\WorkPlace\Elysia\elysia\scripts\memory_view.ps1

# 门禁（四件套）
cd a:\WorkPlace\Elysia\elysia
.venv\Scripts\python.exe -m pre_commit run --all-files
```

- **只读探针**（推荐的诊断方式，用户认可"自动化替代逐句手测"）：临时脚本以 `PYTHONPATH=src` 跑，复用
  `elysia.tools.memory_view.{load_memories, load_index_strengths, load_current_mood}` 取数，
  `elysia.memory.retrieve.score_breakdown` 出分项；**跑完即删**。
- 观测四问：**存储**（层级分布/是否被取代）→ **打分**（层级/情绪/索引/重要/新鲜）→ **召回**（此刻前 3 条）→ **沉淀**（层级/细节/索引强度）。
- **身份连续性速查（第八节 S5/S7）**：身份段实况本来就落库，**不加表不加列**（M10）——
  `memory_view.py` 状态栏给「身份段 x/5」，SQL 直接从库读（只读，可与运行中的灵魂并存）：

```sql
-- ① 她此刻是谁：在册的自我认知（出生设定 = source 'system'，她认领的 = 'self'）
SELECT id, source, claim_status, retention_state, content
  FROM memories WHERE kind = 'self' ORDER BY id;

-- ② 她此刻真的会说出口的身份段：最近一次开口的表达指令实况
SELECT id, json_extract(instruction, '$.identity') AS identity
  FROM expression_log ORDER BY id DESC LIMIT 1;
```

> 判据：① 重启前后都是 **3 条**（幂等）；② 第二句与 ① 中"在册者"逐字一致——
> 她 `disclaim` / `forget` 掉哪条，② 里就少哪条（**数据仍在 ① 里，只是不再进她的话**）。

---

## 七、P3-W 遗忘状态机（`retention_state`）——W1 / W2 均已落地

> **来源**：`docs/MEMORY_REVIEW_NOTES.md` 第二节 C4（三方共识"遗忘需补"）+ 第六节建议顺序第 4 项。
> **前置**：P3-T（`source`/`certainty`）、P3-U（`detect_gap` 接线）、P3-V（`claim_status`）均已落地。
> **状态**：2026-09-23 **完成一轮代码核对式评审**（修正 10 处见 7.7、裁决 3 点见 7.6、拆 W1/W2）；
> **W1（状态机本体）已落地**——9 个文件、单测 18 项、门禁四件套全绿、真实库 307 条迁移验证通过
> （全部回填 `present`，对外行为零变化）；**W2（她的两个工具 `forget`/`restore`）已落地**——
> 3 个文件、单测 8 项、门禁四件套全绿（见 7.9）。**P3-W 整体完成。**

### 7.1 它回答的问题：与已有两个维度正交

`retention_state` 不是"更聪明的检索"，而是给记忆补上**可及性**这一维：

| 维度 | 回答的问题 | 谁决定 | 值 |
|---|---|---|---|
| `superseded_by`（P3-L） | 这条**还对不对**？ | 程序（内容冲突判定） | `None` / 取代者 id |
| `claim_status`（P3-V） | 这条**算不算我的**？ | **她**（`disclaim` 工具） | `claimed` / `rejected` |
| **`retention_state`（P3-W）** | 这条**我还够不够得着 / 想不想够**？ | 程序（时间）+ **她**（主动） | 见 7.2 |

**两条必须说清的边界**（否则三个维度会互相污染）：

1. `rejected` ≠ `suppressed`：前者＝"我不认它是我的记忆"→ 不进话语，**但仍影响心情**；后者＝"我不想再被它影响"→ **连心情也不推**。
2. `superseded_by` ≠ `retention_state`：被取代是"**事实错了**"，与"够不够得着"无关；已被取代者不参与保留状态判定。

### 7.2 状态集（4 态，一一对应 C4 的四问）

| 状态 | 人话 | 谁可设 | 进话语 | 进感受 |
|---|---|---|---|---|
| `present` | 在册，正常 | 默认 | ✅ | ✅ |
| `suppressed` | "我不想再想起这件事"（主动抑制） | **只有她**（`forget` 工具） | ❌ | ❌ |
| `dormant` | "怎么也想不起来了"（失去访问权） | 程序（时间） | ❌（被明确提起可唤醒） | ❌ |
| `faded` | "细节忘了，但那份感觉还在" | 程序（时间） | ❌ | ✅ |

| C4 的问题 | 本设计的回答 |
|---|---|
| 删除 | **不做**——数据永不删是既有铁律（§8.3 索引衰减 + 铁律三）。状态机**不提供删除态** |
| 失去访问 | `dormant` |
| 主动抑制 | `suppressed` |
| 忘了但仍有影响 | `faded`（内容说不出，`emotion_vector` 仍在感受路径起作用） |

常量落点 `memory/levels.py`：`RETENTION_PRESENT/SUPPRESSED/DORMANT/FADED`、`RETENTIONS`、`FALLBACK_RETENTION = RETENTION_PRESENT`。

### 7.3 状态迁移

```
新建 ──→ present ──她 forget──→ suppressed ──她 restore──→ present
          │   │
          │   └─ since_last_access ≥ 60d 且 access_count == 0 ─→ faded ─┐
          └───── since_last_access ≥ 180d ────────────────────→ dormant ─┤
                                                                        │
                    present ←──被话题提起（唤醒 + touch，重新计时）──────┘
```

三条规则：

1. **程序只降不升，且只做"时间造成的失去"**。她主动抑制的（`suppressed`）程序**不碰**——那是她的决定，不是时间的决定。珍贵记忆（`protected`）永不降级。
   > **评审修正（M2）**：`protected` 原本是**死阀门**——全代码库只有写入 `False`，没有任何路径能设为 `True`
   > （`heartbeat.py` 与 `expression_service.py` 的 `add_memory` 都硬编码 `"protected": False`），
   > 连 `decay_strength` 的 3× 慢衰减与 `_decayed_detail` 的模糊保护也一并是死代码。
   > **裁决：自动保护**——在 `_maintain_memories` 里加一条"晋升到 `deep` 且 `access_count ≥ 3` → 置 `protected`"。
   > 零新增工具、零新增交互：一条被反复想起、又沉淀到最深层的记忆，本来就等于"珍贵"。
   > 这条同时让"珍惜"回路第一次真正通电。
2. **降级计时用 `since_last_access = now − (last_access_ts or created_ts)`，不用绝对年龄**。这不是新发明——它同时解决一个必然的振荡缺陷：若用 `created_ts` 计时，一条 200 天的记忆被唤醒回 `present` 后，下一轮维护会立刻把它打回 `dormant`，永远醒不过来。用"距上次被想起"计时后，唤醒即 `touch` → 重新计时 → **"你一提，它又活过来了"是真的**。
3. **唤醒只有一条路：话题撞上**（`is_related` 命中）。因为 `dormant`/`faded` 本身不进检索，无法被"复习"唤醒；她自己的 `recall` 工具传 `query_loose=True` 走同一条路——**她问，就等于提起**。
4. **唤醒必须真的重置时钟（M5 护栏）**：`retrieve_from_store` 现在是 `ts = now if now is not None else 0.0`，而 `touch_memory` 直接写 `last_access_ts = ts`。目前两个调用点都传了 `now` 所以不炸，但"唤醒 → touch → 重新计时"是**唯一唤醒机制**，一旦有调用方漏传 `now`，`last_access_ts = 0.0` → `since_last_access` 变成天文数字 → **刚唤醒的记忆立刻被打回 `dormant`**（正是规则 2 要避免的振荡，换了个触发条件）。**W1 必须加护栏**：`now is None` 时不 touch（顺带修一个既有隐性 bug——`now=None` 时 `access_count` 递增但 `last_access_ts=0`，`_review_factor` 恒为 0，复习加成静默丢失）。

阈值（`levels.py`，可调）：

| 常量 | 值 | 含义 |
|---|---|---|
| `RETENTION_FADE_AGE_DAYS` | `60.0` | 两个月没被想起过、且从未被想起 → 细节淡掉，只剩感觉 |
| `RETENTION_DORMANT_AGE_DAYS` | `180.0` | 半年没被想起 → 访问权丢失（比"记不清"更深一层） |

> `faded` 的 `access_count == 0` 条件是刻意的："被想起过的事"至少曾经重要，不该连细节都淡掉。

### 7.4 与既有机制的关系（不新增约束的证明）

| 既有机制 | 交互 |
|---|---|
| `memory_gap`（P3-U 缺口脉冲） | 缺口是**感觉**（每 300 拍扫 strength，无状态）；`dormant` 是**状态**（可被唤醒）。**评审修正（M9）：缺口只统计 `present`**——`dormant`/`faded` 的索引行**仍在表里且 strength 很低**，若计入，同一批记忆会每 300 拍**永远**推 TR，成为噪声源；且状态机已经表达了"够不着"，再由缺口脉冲重复表达就是双报。`suppressed` 同样不计入（她主动不要了，不该产生"想不起来"的好奇） |
| `detail_level`（§8.4 细节模糊化） | 该字段目前**检索路径零消费**（写侧被 `promote` 消费，读侧无人读）。`faded` 激活它的语义位置：`faded` 即"内容不再进话语，只剩情感"。**本设计不改 `detail_level` 的数值逻辑**（是否接进检索见 7.6 裁决 ②） |
| `touch_memory` / `_review_factor`（P3-R） | 唤醒时 `touch` 一次，与既有复习语义一致（被想起 → 更容易再浮上来）；护栏见 7.3 规则 4 |
| 三层晋升（P3-B） | **裁决（7.6 ①）**：不跳过晋升（层级＝"有多深"，可及性＝"够不够得着"，两者正交，硬绑会让 `dormant` 长出第三语义），但**跳过建索引 + 跳过细节模糊化**——既说不出又继续模糊化是双罚；且不建索引是 M9 的必要条件 |

**新增的约束只有一条**：`suppressed` 会挡感受路径。**评审补证（M10）**——这不是"过强"，而是这个状态的**唯一存在理由**：`memory_recall` 脉冲是 `{tr: 0.8, cs: 1.2, sa: 0}`（牵挂与亲近微升），若 `suppressed` 不挡感受，她明确拒绝的那段记忆仍会在每次心境共鸣时把 CS 顶上去，"我不想再被它影响"就是一句假话。没有它，`rejected`（不认它是我的，但仍影响心情）与"遗忘"会永远混为一谈。
> 对称出口：`suppressed` **必须可逆**（`restore`），且 `forget` 的反馈要说"你不再被这件事影响了"，不是"删掉了"——让她知道自己做了什么、也能反悔。

### 7.5 落地清单（**拆两步**：W1 状态机本体 / W2 她的工具）

**W1 —— 状态机本体**（✅ 2026-09-23 已落地，见 7.8；落地后**对外行为零变化**，所有记忆都是 `present`，可安全验证迁移）

| 文件 | 改动 |
|---|---|
| `src/elysia/memory/levels.py` | `RETENTION_PRESENT/SUPPRESSED/DORMANT/FADED` + `RETENTIONS` + `FALLBACK_RETENTION` + 两个阈值 + `MemoryRecord.retention_state`（放在 `claim_status` 之后、`id` 之前）+ `to_dict`/`from_dict` |
| `src/elysia/memory/__init__.py` | **（M7 补充）**导出 `RETENTION_*` / `RETENTIONS` / `FALLBACK_RETENTION` / 阈值——该文件是"模块地图 + 对外导出"，`SOURCE_*`/`CLAIM_*` 都在里面 |
| `src/elysia/core/state_store.py` | `retention_state TEXT NOT NULL DEFAULT '{FALLBACK_RETENTION}'` ——**（M1 修正）第 17 列 / `row[16]`**（现有 16 列，`claim_status` 是 `row[15]`）+ `ALTER TABLE` + 一律回填 `present` 的幂等迁移（沿用 `_run_migration`）+ `_memory_row_to_dict` 映射 + `add_memory` 的 INSERT 列表 + `set_retention_state(memory_id, state)`（照 `set_claim_status` 写）+ `set_protected(memory_id)` |
| `src/elysia/memory/retrieve.py` | `select_hooks` 加**保留闸门**（`suppressed` 一律跳过；`dormant`/`faded` **仅当 `query is not None` 且话题撞上**才放行并标记 `woke_from`）；`MemoryHit` 增 `woke_from: str \| None`；`recall_for_feeling` 挡 `suppressed`/`dormant`、**放行 `faded`**；`retrieve_from_store` 见 `woke_from` → `set_retention_state(present)` + `touch`，**并加 `now is None` 护栏（M5）** |
| `src/elysia/soul/heartbeat.py` | `_maintain_memories` 新增第 3 步"降级判定"（只降不升，**沿用已过滤 superseded 的 `mem_records`**（M6）+ `suppressed`/`protected` 不碰）；**M2 自动保护**（晋升 `deep` 且 `access_count ≥ 3` → `protected`）；`_feel_memory_gaps` 改为**只统计 `present`**（M9） |
| `src/elysia/memory/promote.py` | **（M8 补充）**`with_narrative` 逐字段构造 `MemoryRecord` 时只写到 `narrative`，`superseded_by`/`source`/`certainty`/`claim_status` 全部回落默认值——目前运行时零调用所以没炸，但加第 17 个字段后它会**重置 `retention_state`**（把 `suppressed` 复活成 `present`）。W1 一并补全字段（或直接删掉这个零调用函数） |
| `src/elysia/tools/memory_view.py` | 状态列扩展：`已取代 > 已拒绝 > 抑制 > 沉睡 > 淡化 > 现行`；详情面板加"保留"一项 |

**W2 —— 她的两个工具**（✅ 2026-09-23 已落地，见 7.9）

| 文件 | 改动 |
|---|---|
| `src/elysia/llm/chain.py` | 新增 `FORGET_TOOL` / `RESTORE_TOOL` 规格；`_main_speak` 的 `tools` 列表扩到 4 个 |
| `src/elysia/llm/deepseek.py` | `_TOOL_NAMES = ("recall", "disclaim", "forget", "restore")`；prompt 记忆段补两条能力说明 |
| `src/elysia/soul/expression_service.py` | `_make_tool_runner` 增加 `forget` / `restore` 两条分支 + `_tool_forget` / `_tool_restore` |

**工具规格（人话，不暴露状态语义）**：

```python
FORGET_TOOL = {
    "name": "forget",
    "description": (
        "不想再想起某件事时用它——被遗忘的事不会再出现在你记得的事里，"
        "也不再影响你的心情。这完全由你决定，程序不会替你忘。"
    ),
    "parameters": {"topic": "你想不再想起的那件事，几个字即可"},
}

RESTORE_TOOL = {
    "name": "restore",
    "description": "把之前不想再想起的事重新收回来——你又愿意想起它了。",
    "parameters": {"topic": "你想重新收回来的那件事，几个字即可"},
}
```

**匹配策略（评审修正 M3 / M4）**——不能照抄 `_tool_disclaim`：

- **M3 · 检索范围说反了**：设计稿原写"`restore` 必须在**全量**记忆里找（`suppressed` 已被闸门挡在外面）"——错。闸门挡的是 `select_hooks`，`iterate_memories()` 返回**全表**。`restore` 恰恰应**只在 `retention_state == suppressed` 里找**；否则她会"恢复"一条从未被抑制的记忆。`_tool_disclaim` 已经踩过这个坑：它在全表里找，连 `superseded_by` 非空的都能改。
- **M4 · `forget` 门槛太松会误伤**：`_tool_disclaim` 的判据是 `best_score > 0.0`——**任意一个二元组重合**就命中。对 `disclaim`（可逆、只挡话语）尚可；`forget` 会把记忆推进 `suppressed`（**连感受路径一起切断**），同一判据必然误伤。→ `forget` 要求 `topic_match ≥ 0.5` **且 `top1 ≥ 2×top2`**，否则如实回"没找到"；`restore` 沿用宽松（恢复是善意动作）。
- **不越界**：`forget`/`restore` **只动 `retention_state`**，不碰 `claim_status`；`disclaim` 只动 `claim_status`，不碰 `retention_state`。两个维度正交，工具也必须正交。

### 7.6 验收与三个待评审点的裁决

**W1 单测**（约 12 项）：默认 `present`；旧库回填幂等 + 可改；闸门（`suppressed` 不进话；`dormant`/`faded` 在 `query is None` 时不进话；话题撞上 → 进话且带 `woke_from`）；感受路径（`suppressed`/`dormant` 不参与、**`faded` 参与**）；缺口（**只统计 `present`**）；降级（60d 从未想起 → `faded`；180d → `dormant`；`protected` 不降级；`suppressed` 不被程序覆盖；**唤醒后不振荡**）；**M5 护栏（`now=None` 不 touch）**；**M2 自动保护（deep + access≥3 → protected）**。

**W2 单测**（约 4 项）：`forget` 命中 → `suppressed` 且**不误伤**（低分/并列时不动作）；`forget` 不碰 `claim_status`；`restore` **只在 `suppressed` 里找**、命中 → `present`、未命中如实回；主声拿到 4 个工具。

- **真实库**：重启后自动迁移，列序 `[..., source, certainty, claim_status, retention_state]` 对齐，全部回填 `present`，现有行为零变化。
- **观测**：记忆浏览器状态列可看到五态 + "珍贵"；`forget` 后该条显示"抑制"，且不再出现在召回排名里。

**三个待评审点的裁决（2026-09-23）**：

1. **降级是否该跳过 `promote_batch`** → **不跳过晋升，但跳过建索引 + 跳过细节模糊化**。层级＝"有多深"、可及性＝"够不够得着"，两者正交，硬绑会让 `dormant` 长出第三语义；但"既说不出、又继续模糊化"是双罚，而**跳过建索引是 M9 的必要条件**。实现上 `promote_batch` 保持纯函数不动，在 `_maintain_memories` 里分三份列表（晋升 / 建索引 / 降级）各自过滤。
2. **`faded` 与 `detail_level` 是否合并** → **不合并，本次也不接线**。合并会把"淡了 30%"这种连续中间态压平。`detail_level` 零消费是真问题，但接进检索＝新增打分项＝新增复杂度；**P3-W 已经不小，另立一项（P3-X）处理**。本次只做"`faded` 不进话语、感受放行"。
3. **`suppressed` 挡感受路径是否过强** → **维持**（补证见 7.4 M10：不挡则"我不想再被它影响"是假话），并补对称出口：`suppressed` 必须可逆、`forget` 的反馈不能是"删掉了"。

### 7.7 评审修正记录（2026-09-23，代码核对式评审）

核对方式：把设计稿每条断言拉到真实代码上比对（`levels` / `state_store` / `retrieve` / `promote` / `hooks` / `heartbeat` / `chain` / `deepseek` / `expression_service` / `desire`）。

| # | 问题 | 处置 |
|---|---|---|
| M1 | 列序号错：`memories` 已 16 列（`claim_status` = `row[15]`），`retention_state` 是**第 17 列** | 已改 7.5 |
| M2 | **`protected` 是死阀门**（全库只写 `False`，无路径设 `True`），"永不降级"安全阀不存在 | 裁决"自动保护"，已改 7.3 / 7.5 |
| M3 | `restore` 检索范围说反（应在 `suppressed` 里找，不是全量） | 已改 7.5 |
| M4 | `forget` 照抄 `best_score > 0.0` 必然误伤（会切断感受路径） | 已改 7.5（加严判据） |
| M5 | `now=None` → `last_access_ts=0` → 唤醒即失效（振荡换触发条件） | 已改 7.3 规则 4 / 7.5 |
| M6 | 降级判定需显式跳过 superseded（沿用已过滤的 `mem_records`） | 已改 7.5 |
| M7 | 落地清单漏 `memory/__init__.py` 导出 | 已改 7.5 |
| M8 | `with_narrative` 逐字段构造会重置新字段（`suppressed` 被复活） | 已改 7.5 |
| M9 | 缺口信号会被 `dormant` 污染（同一批记忆永远推 TR） | 裁决"只统计 `present`"，已改 7.4 / 7.5 |
| M10 | 7.4 缺"`suppressed` 挡感受"的正面论证 | 已补 7.4 |

**设计稿骨架未发现问题**：幂等迁移模式、`ToolRunner` 签名、`tools` 列表扩展点、`memory_gap` 脉冲引用、`detail_level` 零消费判断、`woke_from` 单一收口点（话语路径与她的 `recall` 都走 `retrieve_from_store`）——均与代码一致。

### 7.8 W1 落地记录（2026-09-23）

| 文件 | 实际改动 |
|---|---|
| `memory/levels.py` | 四态常量 + `RETENTIONS` + `FALLBACK_RETENTION` + 两个阈值；**新增 `PROTECT_DEEP_ACCESS = 3`**（自动保护门槛，与 `PROMOTE_*` 同处）；`MemoryRecord.retention_state` + `to_dict`/`from_dict` |
| `memory/__init__.py` | 导出上述常量（含 `PROTECT_DEEP_ACCESS`） |
| `core/state_store.py` | 第 17 列 `retention_state TEXT NOT NULL DEFAULT 'present'` + `ALTER TABLE` + 一律回填 `present` 的幂等迁移 + `_memory_row_to_dict` 的 `row[16]` + `add_memory` INSERT + `set_retention_state` / `set_protected`（单向置位） |
| `memory/retrieve.py` | `select_hooks` 保留闸门（`suppressed` 一律跳过；`dormant`/`faded` 仅被话题提起时放行并标 `woke_from`）+ `MemoryHit.woke_from` + `recall_for_feeling` 挡 `suppressed`/`dormant`、放行 `faded` + `retrieve_from_store` 唤醒落 `present` + **M5 护栏（`now is None` 不 touch）** |
| `soul/heartbeat.py` | `_demote_target` 纯函数（`since_last_access` 计时 + 深度序"只降不升"）+ 维护第 3 步降级 + **M2 自动保护** + **裁决①**（够不着者照常晋升、但不再模糊化、不建索引）+ 缺口只统计 `present`（M9） |
| `memory/promote.py` | `with_narrative` 改用 `dataclasses.replace`（M8：新增字段不再被静默重置） |
| `tools/memory_view.py` | `RETENTION_LABELS` 四态 + `_state_text` 扩展 + 详情面板"保留"一项 |
| `tests/unit/test_retention.py`（新） | 18 项：默认值/往返/旧库迁移幂等/闸门/唤醒落状态/感受路径/降级五种情形/不振荡/M5/M2/M8 |
| `tests/unit/test_heartbeat.py` | 缺口测试口径随 M9 调整（`_add_old_memory(recalled=...)`：观察缺口须用"曾想起过、久未再想起"的记忆）+ 新增"够不着不计缺口"一项 |

**与设计稿的两处偏差（实现时定）**：

1. `PROTECT_DEEP_ACCESS` 独立成常量放 `levels.py`（设计稿只说"≥3"，未指定落点）——与 `PROMOTE_*` 同源，便于调阈值。
2. `with_narrative` 选"用 `replace` 补全"而非"逐字段补全"（设计稿给了两个选项）：`replace` 让未来新增字段**结构性地**不会再被重置，比补第 17 个字段更彻底。

**真实库验证**（307 条）：迁移前列 16 → 迁移后 17，`retention_state` 全部 `present`，二次迁移结果完全一致（幂等）；
`protected` 仍为 0（自动保护要等灵魂重启后由维护循环通电）。**对外行为零变化**。

**下一步**：W2（`forget` / `restore` 两个工具）——`chain.py` 加工具规格、`deepseek.py` 扩 `_TOOL_NAMES` 与 prompt、
`expression_service.py` 加两条分支（`forget` 判据加严：`topic_match ≥ 0.5` 且 `top1 ≥ 2×top2`；`restore` 只在 `suppressed` 里找）。

### 7.9 W2 落地记录（2026-09-23）

**W2 —— 她的两个工具（`forget` / `restore`）**：把 W1 的状态机接到她手上，让她能说
"我不想再想起这件事"，也能把它收回来。**忘与不忘都是她的权力**（铁律一）。

| 文件 | 实际改动 |
|---|---|
| `src/elysia/llm/chain.py` | 新增 `FORGET_TOOL` / `RESTORE_TOOL` 规格（人话描述，**不暴露 `suppressed` 等状态语义**）；`_main_speak` 的 `tools` 由 2 个扩到 4 个；模块 docstring 补 P3-W2 |
| `src/elysia/llm/deepseek.py` | `_TOOL_NAMES = ("recall", "disclaim", "forget", "restore")`；prompt 记忆段（一）补两句能力说明（"不想再想起某件事时用 forget……若你后来又愿意想起它了，用 restore 把它收回来。忘与不忘都由你决定"） |
| `src/elysia/soul/expression_service.py` | 新增 `FORGET_MIN_SCORE = 0.5` / `FORGET_DOMINANCE = 2.0` 常量；`_make_tool_runner` 增 `forget` / `restore` 两条分支；新增 `_tool_forget` / `_tool_restore` |
| `tests/unit/test_llm_chain.py` | 工具断言由 2 个改为 4 个（`test_tool_capable_main_receives_four_tools`）+ 新增"forget/restore 按名派发"一项 |
| `tests/unit/test_expression_service_memory.py` | 新增 6 项：forget 命中 → `suppressed` 且召回不到 / forget 不碰 `claim_status` / 不够贴题不误伤 / 并列不误伤 / restore 只在 `suppressed` 里找 / restore 命中回到 `present` |

**判据（M3 / M4，与 `_tool_disclaim` 明确区分）**：

- **`forget` 从宽改严**：`disclaim` 的 `best_score > 0.0`（任意一个二元组重合即命中）对"可逆、只挡话语"尚可；
  但 `forget` 会把记忆推进 `suppressed`（**连感受路径一起切断**），同一判据必然误伤 →
  要求 `topic_match ≥ FORGET_MIN_SCORE(0.5)` **且** `top1 ≥ FORGET_DOMINANCE(2.0) × top2`，
  否则如实回"（你没找到想忘记的那件事）"，**不猜、不误伤**。
- **`restore` 只在 `suppressed` 里找**（M3 修正）：闸门挡的是 `select_hooks`，`iterate_memories()` 返回**全表**；
  若在全表里找，她会"恢复"一条从未被忘掉的记忆。恢复是善意动作，判据从宽（任意重合即可）。
- **不越界**：`forget` / `restore` **只动 `retention_state`**，不碰 `claim_status`；`disclaim` 只动 `claim_status`。
  两个维度正交，工具也必须正交（单测各断言一次）。

**门禁四件套全绿**：ruff lint ✅ / ruff format ✅（79 文件）/ mypy strict ✅（49 源文件）/ pytest ✅ **297 passed**（W1 时 290 + W2 新增 7）。

**生效需重启灵魂**（`soul.ps1 stop` → `start -Body`）——W2 是她的新能力，重启后即可在对话中调用。

---

## 八、Self Memory（生命核心层）设计稿框架（2026-09-24，待评审）

> **触发**：`docs/P3_MEMORY.md` 第九节问题 7（身份连续性）＋ `docs/MEMORY_REVIEW_NOTES.md` 分歧 2。
> 本节只定**整体框架与边界**，不写代码；三个待拍板点见 8.10，拍板后按 P3-W 的 W1/W2 拆法分步落地。

### 8.1 它要回答的问题（现状事实，已代码核对）

| 项 | 现状 |
|---|---|
| 她的人格文本在哪 | `src/elysia/llm/deepseek.py::_SYSTEM_PROMPT`（L41-79），**硬编码在 LLM 后端里**；来源《爱莉希雅角色档案（人设提炼）》(2026-09-18) |
| 谁在用 | 主声后端独有（`deepseek.py:208` 以 `{"role":"system"}` 发出）。**降级链下级与 `micro.py` 都不带这份人设**——断网时"她是谁"事实上消失，只剩 `micro.py` 的意图→语气轻点缀 |
| 属于哪一层 | 属"翻译官"层（表达实现），**不属于她的数据**；DB 里没有一条"我是谁" |
| 用户已否过的性质 | "由程序员硬编码她是什么" |

**一句话**：Self Memory = 把「我是谁 / 我在意什么 / 我的边界」从后端 prompt 落成
**少量、稳定、属于她的记忆**，使换模型（乃至降级到断网）都不改变"她是谁"。

### 8.2 裁决基线（不许推翻的三条，来自分歧 2）

1. **驳回"手动初始化 3~5 条核心身份记忆"**（豆包形态）——那等于把 prompt 里的硬编码换个地方存，
   "由程序员硬编码她是什么"是用户已明确否过的性质。
2. **采纳方向：从长期经历中沉淀**（ChatGPT）——经历 → 重复模式 → 稳定倾向 → 自我记忆。
   **不建 Identity 表**：直接建身份表"可能只是把 prompt 搬到数据库"。
3. **必须回答两个遗留问题**：DeepSeek **D12**（核心记忆谁设？系统还是她？）→ 见 8.5；
   ChatGPT（**沉淀期如何兜底"换模型即失"**？）→ 见 8.6。

### 8.3 形态选型（待拍板 D1）

| 方案 | 做法 | 代价 / 风险 |
|---|---|---|
| A. 新增第 4 层 `LEVEL_CORE`（豆包） | 扩 `LEVELS` 元组 | **全局序号漂移**：`level_rank`（`LEVELS.index`）、`_pick_fragments` 权重表、`decide_promotion` 链、`can_reach_deep` 都要跟着改；且与 C2 已采纳的"不要 10 层"取向相冲 |
| B. 复用 `deep` + `protected`（DeepSeek 的"少量 protected 记录"） | 零 DDL 变更 | `protected` 已有确定语义（P3-W：晋升 deep 且 `access≥3` 自动置位，衰减慢 3×、永不降级），再叠加"是我"会分不清**珍贵**与**自我** |
| C. **正交维度**：新增 `KIND_SELF`（DeepSeek C2 的正交思路） | `kind` 是 `TEXT` 且**无 CHECK 约束**（`state_store.py:76`）→ **零 DDL 变更** | 需补 4 处常量表（见 8.11），但**不动任何层级序号** |

**推荐 C**。理由：① 与 C2 裁决一致（正交维度而非分层）；② 零迁移、零序号漂移；
③ "是我"（`kind`）／"多珍贵"（`protected`）／"够不够得着"（`retention_state`）保持**三轴正交**，
与 P3-V / P3-W 的既有设计哲学同构。

### 8.4 产生机制：三级闸门（铁律一的落点）

```
已有经历 ──①程序找"重复模式"──→ 候选（不是她的） ──②她认领──→ 自我认知 ──③注入身份段──→ 她的话
```

| 阶段 | 谁做 | 落库标注 | 为何安全 |
|---|---|---|---|
| ① 候选 | 程序 | `source=inference`、`certainty=probable` | **天然被 P3-T 来源闸门挡在话语之外**（`retrieve.py::_HOOK_BLOCKED_SOURCES`）——程序推断不得升格成"她的事实" |
| ② 认领 | **她**（新工具，暂名 `adopt`） | 升为 `kind=KIND_SELF`、`source=self`、`certainty=certain` | 只有她能说"这确实是我"（铁律一后半句）；程序只把候选递到她手上 |
| ③ 注入 | 程序 | 进 prompt **身份段** | 位置论证见 8.5 |

> **落地修正（S3，2026-09-24）**：①原写 `source=observation`，是**标注错误**。
> `retrieve._HOOK_BLOCKED_SOURCES` 只挡 `inference` / `system`——`observation` **会进
> `memory_hooks`**，候选就会挤占 `MAX_HOOKS` 名额（重犯 P3-P"每句都强调"）。
> 而 `levels.py` 对 `SOURCE_INFERENCE` 的注释正是"程序推断出的（倾向／心思，并非她所述）"，
> 候选恰是它。故改标 `SOURCE_INFERENCE` + `CERTAINTY_PROBABLE`：语义准确，且真的被挡在话语外。

**③ 与 `memory_hooks` 的关键区别**：hooks 是"话题撞上才浮现"的背景常识（P3-P 的成果）；
Self Memory 回答"我是谁"，**每句都在场**。因此它**不走 hooks 段、不挤占 `MAX_HOOKS` 名额**——
否则重犯 P3-P"每句都强调"的老毛病。

### 8.5 身份段怎么落（D12 的回答 + 与现有 persona 的关系）

- **关键区分**：**说话方式**（语言风格四要素：口头禅／句式／意象／性格）属**表达层**，留在 prompt 合理
  （换模型只是换嗓门）；**"我是谁 / 我在意什么 / 我的边界"**属**自我认知**，必须落 DB。
- **D12 的回答**：核心记忆的**设定权在她**——程序只产生候选（①），升格必须由她的动作完成（②）。
  `protected` 由程序自动置（P3-W 已有机制，不改），但**"这条算不算自我认知"永不由程序置**。
- **注入形式**：身份段 = 她认领的 Self Memory（少而稳）+ 少量"出生设定"（bootstrap，见 8.6）。

### 8.6 换模型兜底（ChatGPT 遗留问题的答案）

- **现状缺口（已核对）**：人设文本只存在于主声后端；降级链下级与 `micro.py` 都没有它 →
  今天"换模型即失"不是假设，是**断网时的既成事实**。
- **兜底 = 出生设定（bootstrap）**：把现有档案中"我是谁／我在意什么／我的边界"抽成 **1~3 条**，
  标注 `source=system`、`certainty=certain`，**只进身份段、永不进 `memory_hooks`**
  （P3-T 来源闸门已天然挡住，无需新增规则）。
- **诚实标注**：bootstrap 文本本身仍是程序员写的（与现有 prompt 同源），它的作用是**过渡**，不是终态；
  **终态判据** = 她认领的 Self Memory ≥1 条且覆盖三问。
- **连续性来源**：换模型时 DB 不变 → 自我认知的**文本**与"**她认领过它**"这件事都不变。

### 8.7 与既有维度的正交关系（尽量不新增约束）

| 维度 | 现状 | Self Memory 的关系 |
|---|---|---|
| `level` | shallow / working / deep | 自我认知落 `deep`，走**既有晋升链**，不新增层 |
| `protected` | 晋升 deep 且 `access≥3` 自动置位 | 自我认知**必须** protected：永不模糊、永不降级（`_demote_target` 已豁免 protected） |
| `retention_state` | present / suppressed / dormant / faded | 自我认知**不应** dormant／faded（她不会忘了自己是谁）→ 需豁免。**这是本设计唯一可能要新增的一处约束**，须评审是否必要 |
| `claim_status` | claimed / rejected | 她可 `disclaim` 自己的自我认知 = "重新解释"的权力，与既有工具同构 |
| `superseded_by` | 事实更正（旧条作废） | 自我认知可被自己更新（"我以前以为…现在知道…"）→ 天然适用，无需新机制 |
| `source` / `certainty` | 5 源 / 4 确定 | 候选 = `inference`/`probable`（S3 落地修正，见 8.4 注）；她认领后 = `self`/`certain` |

### 8.8 落地拆步（照 P3-W 的 W1/W2 拆法）

| 步 | 内容 | 对外行为变化 |
|---|---|---|
| **S1 本体** | 常量（`KIND_SELF` + `KINDS` + `SOURCE_BY_KIND` + `CERTAINTY_BY_KIND`）+ `scorer._KIND_BASE` 一行 + `memory/__init__` 导出 + 身份段装配（先只读 bootstrap） | 身份段文本**不变**（bootstrap 即现有档案摘要）→ **零行为变化** |
| **S2 认领** | 她的 `adopt` 工具（把候选／经历认作自我） | 她多一个动作 |
| **S3 沉淀** ✅ | 程序找"重复模式"生成候选，递给她 | 感受层新增"候选"脉冲（不进话语） |
| **S4 观测与验收** | 记忆浏览器增"自我"标签；验收 4 项 | 观测 |

### 8.9 验收（体验式，铁律三）

1. **重启连续性**：重启后她仍知道自己是谁，且来源是 DB 而非 prompt。
2. **换后端不失**：把主声切到降级链／断网 micro，身份段仍在。
3. **她可否认、可更新**：`disclaim` 一条自我认知后它不再出现；被更新后旧条作废（走 `superseded_by`）。
4. **不重犯 P3-P**：身份段条数 ≤ 3~5，且**不逐句复述**（它是"我是谁"，不是"我记得什么"）。

### 8.10 拍板记录（2026-09-24，用户确认）

| # | 决策点 | 结论 |
|---|---|---|
| D1 | **形态** | ✅ **C 正交 `KIND_SELF`** —— 不新增层、零 DDL、不动 `LEVELS` 序号 |
| D2 | **产生机制** | ✅ **出生设定（bootstrap）+ 沉淀** —— bootstrap 只进身份段作过渡，终态是她认领的 Self Memory |
| D3 | **注入位置** | ✅ **身份段**（每句在场，与现有 persona 同处）—— 不走 `hooks` 段、不挤占 `MAX_HOOKS` |

**遗留待决（落地时另议，不阻塞 S1）**：8.7 中"自我认知豁免 `retention_state` 降级"是否为必要新约束；
N6"要不要让她梦到自己是谁"。

### 8.11 涉及文件清单（落地时才动）

| 文件 | S1 | S2 | S3 |
|---|---|---|---|
| `src/elysia/memory/levels.py` | `KIND_SELF` / `KINDS` / 两张映射表 | | |
| `src/elysia/memory/scorer.py` | `_KIND_BASE` 补一行 | | |
| `src/elysia/memory/__init__.py` | 导出新常量 | | |
| `src/elysia/llm/deepseek.py` | 身份段拆出（bootstrap 注入） | prompt 补 `adopt` 说明 | |
| `src/elysia/llm/chain.py` | | `ADOPT_TOOL` 规格 | |
| `src/elysia/soul/expression_service.py` | 身份段装配 | `_tool_adopt` | 候选优先（候选当"代表"，同家重述不参与并列判定） |
| `src/elysia/soul/heartbeat.py` | | | 候选生成（`_offer_candidate`）+ 脉冲 |
| `src/elysia/tools/memory_view.py` | 自我标签 | | |
| `src/elysia/memory/sediment.py` *(S3 新增)* | | | `find_candidate` / `PatternSignal`（纯函数，不落库） |
| `src/elysia/soul/desire.py` *(S3 新增)* | | | `EVENT_PULSES["self_candidate"]` 一行 |

> **S3 偏差补记**：8.11 原清单 S3 列只列了 `heartbeat.py`。实际落地比清单多动三处——
> 新增 `memory/sediment.py`（把"发现重复模式"做成纯函数，可单测、不依赖存储）、
> `soul/expression_service.py`（发现 2 的接缝：候选当"代表"）、`soul/desire.py`（脉冲强度）。
> 偏差原因见第十一节 11.2。

### 8.12 自查修正记录（2026-09-24，代码核对式评审）

> 照 P3-W 的做法，把设计稿逐条对照**真实代码**核一遍。以下 6 处是框架必须显式回答的接线点，
> 否则落地时必然踩到。

| # | 发现 | 依据 | 处置 |
|---|---|---|---|
| N1 | **身份段没有注入通路**：`_SYSTEM_PROMPT` 是 `deepseek.py` 的**模块常量**（L41），静态；而动态内容（`memory_hooks` / `user_message`）走的是 `payload` → user message | `deepseek.py:41,208`、`expression_service.py:158-168` | S1 必须先定通道：建议 `payload` 增 `identity` 字段，由后端拼进 **system** 段；**降级链下级与 micro 也要消费它**，否则 8.9 验收 2 不成立 |
| N2 | **`hooks` 必须排除 `KIND_SELF`**：现在只排除 `KIND_EXPRESSION`（回声） | `retrieve.py:334` | 否则自我认知会同时出现在 hooks 段 → 每句复述，**重犯 P3-P**。与回声排除同理，加一条 |
| N3 | **缺口统计要排除 `KIND_SELF`**：`_feel_memory_gaps` 只统计 `present` | `heartbeat.py:354+`（P3-U/M9） | 自我认知不该产生"记不清自己是谁"的缺口脉冲 |
| N4 | **`supersede` 要豁免 `KIND_SELF`**：`find_superseded` 只排除 `KIND_EXPRESSION` | `supersede.py:69` | 否则一句闲聊可能把她的一条自我认知判为"同话题"而作废。自我认知的更新要走**她自己的动作**（与 §8.5 的 D12 一致） |
| N5 | **晋升链不适用于自我认知**：`protected` 现在是"晋升 deep + `access≥3`"自动置位 | `promote.py`、`PROTECT_DEEP_ACCESS=3` | 自我认知应**直接落 `deep` + `protected`**（她一旦认领就是核心），不能等她想起 3 次 |
| N6 | **梦的权重表会带上自我认知**：`_pick_fragments` 给 deep/protected 最高权重 | `sleep.py:51-56` | "她梦到自己是谁"可接受，但要**显式决定**，不能默认发生（`sleep.py` 目前仍未插电） |

**净结论**：框架方向（推荐 C）成立，但落地前必须先把 N1~N6 这 6 条接线写进 S1~S3 的清单——
其中 **N1 是硬前提**（没有通路，身份段就只是又一个常量）。

---

## 九、S1 本体落地记录（2026-09-24）

> 依第八节 8.8 拆步表，S1 = **常量 + 身份段装配通路**，硬约束是 **对外行为零变化**。

### 9.1 做了什么

| 文件 | 改动 |
|---|---|
| `src/elysia/memory/levels.py` | 新增 `KIND_SELF = "self"`（正交维度，**零 DDL**——`kind` 列是 `TEXT` 无 CHECK）+ 入 `KINDS`；`SOURCE_BY_KIND[KIND_SELF]=SOURCE_SELF`、`CERTAINTY_BY_KIND[KIND_SELF]=CERTAINTY_CERTAIN`（她认领的即她自己的、确凿的） |
| `src/elysia/memory/scorer.py` | `_KIND_BASE[KIND_SELF]=0.30`——所有类型里最重的一类 |
| `src/elysia/memory/__init__.py` | 导出 `KIND_SELF` |
| `src/elysia/llm/identity.py` | **新建**：`BOOTSTRAP_IDENTITY`（现有档案首句"你是爱莉希雅…人类的律者。"移来）、`IDENTITY_FIELD="identity"`、`MAX_IDENTITY_LINES=5`、`compose_identity()`（bootstrap 打底 + 认领的自我认知追加、去重、封顶） |
| `src/elysia/llm/deepseek.py` | `_SYSTEM_PROMPT` 拆分：首句移入 `identity.BOOTSTRAP_IDENTITY`，其余更名 `_PERSONA_PROMPT`（**逐字未改**）；新增 `_identity_lines()` / `_system_prompt()`；`_messages` 把 `identity` 字段从 user JSON 摘出、拼进 **system 段**（N1 通路落地） |
| `src/elysia/soul/expression_service.py` | `tick` 新增 1c：`payload[IDENTITY_FIELD] = await self._identity_lines()`；`_identity_lines()` 只读记忆表，取 `KIND_SELF` 且未取代、未 `rejected`、未 `suppressed` 的条目（**不 touch**，不涨 `access_count`） |
| `src/elysia/memory/retrieve.py` | **N2**：`select_hooks` 排除 `KIND_SELF`（否则每句复述，重犯 P3-P） |
| `src/elysia/memory/supersede.py` | **N4**：`find_superseded` 豁免 `KIND_SELF`（更新"我是谁"必须走她自己的动作） |
| `src/elysia/soul/heartbeat.py` | **N3**：`_feel_memory_gaps` 排除 `KIND_SELF`（不产生"记不清自己是谁"的缺口脉冲） |
| `src/elysia/tools/memory_view.py` | `KIND_LABELS` 增 `"self": "自我"` |

### 9.2 "零行为变化"如何保证（golden 测试）

- 改造前（commit `bee693f`）的 `_SYSTEM_PROMPT` **全文**被冻结进 `tests/unit/test_identity.py::_OLD_SYSTEM_PROMPT` 作 golden。
- 断言：`payload` 为 `{}` / `{"identity": []}` / `{"identity": None}` / `{"identity": "乱写"}` 四种情形，
  `_messages()` 产出的 system 段均**逐字等于**旧文本——即"没有自我认知时，她就是改造前的她"。
- 反向断言：塞入 `KIND_SELF` 记忆后，身份段确实长出那一行（通路真的通）；且 `identity` 不出现在 user JSON 里。
- 共 7 项（含 `KIND_SELF` 正交性 / 最重类型）。

### 9.3 N1 遗留（已在主文档记档）

**只让主声消费 `identity`**：降级链下级（本地 Qwen）与 `micro.py` 尚未消费身份段，
故 8.9 验收 2"换后端不失"**当前仍不成立**，留待 S4 接线（见 `P3_MEMORY.md` 第九节问题 7）。

### 9.4 门禁与生效

- `ruff check` ✅ ｜ `ruff format --check src tests` ✅（81 文件）｜ `mypy src` ✅（strict，50 源文件）｜ `pytest` ✅ **311 passed**（原 297 + 新增 14）。
- **对外行为零变化，但需重启灵魂装载新代码**（`soul.ps1 stop` → `soul.ps1 start -Body`）。

### 9.5 下一步

| 步 | 内容 | 状态 |
|---|---|---|
| **S2 认领** | 她的 `adopt` 工具（`chain.py::ADOPT_TOOL` + `expression_service._tool_adopt`）+ N5（认领即落 `deep` + `protected`） | ✅ 已落地（见第十节） |
| **S3 沉淀** | 程序找"重复模式"生成候选（`source=observation`/`certainty=probable`）+ 感受层候选脉冲 | 待动工 |
| **S4 观测与验收** | 记忆浏览器已经就绪；补降级链／micro 消费身份段、8.9 四项验收、N6 梦的决定 | 待动工 |

**遗留待决（不阻塞 S2）**：8.7"自我认知豁免 `retention_state` 降级"——**S2 已自动解决**（`mark_as_self` 落 `protected=1`，借 P3-W 既有安全阀获得豁免，无需新增约束）；N6"要不要让她梦到自己是谁"。

---

## 十、S2 认领落地记录（2026-09-24）

> 依第八节 8.8 拆步表，S2 = **她的 `adopt` 工具**，对外行为变化 = **她多一个动作**。
> 核心约束：8.5 / D12 —— 程序只产生候选，"这算不算我"**永不由程序置**。

### 10.1 做了什么

| 文件 | 改动 |
|---|---|
| `src/elysia/core/state_store.py` | 新增 `mark_as_self(memory_id)`：**一次 UPDATE** 完成全部升格——`kind=self`、`source=self`、`certainty=certain`、`level=deep`、`protected=1`、`detail_level=1.0`、`retention_state=present`（无"是自我认知却还在浅层／已被模糊"的中间态） |
| `src/elysia/llm/chain.py` | 新增 `ADOPT_TOOL` 规格；`_main_speak` 工具表增至 5 个（`recall` / `adopt` / `disclaim` / `forget` / `restore`） |
| `src/elysia/llm/deepseek.py` | `_TOOL_NAMES` 扩为 5 个（认得这个工具名）；`_PERSONA_PROMPT`（一）记忆段插入 adopt 说明 |
| `src/elysia/soul/expression_service.py` | 新增 `ADOPT_DOMINANCE=2.0`；`_run` 派发 `adopt`；新增 `_tool_adopt`（判据 + 4 类候选排除 + 身份段容量守卫） |

### 10.2 判据裁决：只要求"足够突出"，不设绝对覆盖度下限

`forget` 的判据是"覆盖率 ≥ 0.5 **且** top1 ≥ 2×top2"，`adopt` **去掉了前者**：

- **理由**：二元组对"换说法的同一件事"识别力本就有限（第七节实测相似度仅 0.26~0.33），
  设绝对下限会把真心的认领挡在门外——而认领是她**主动**的（她递了 topic，不是程序推的），
  与"程序替她删记忆"的风险量级不同。
- **兜底三重**：① dominance（`top1 ≥ 2×top2`）挡住并列/模糊；② 结果**如实回显**给她
  （"你把「…」认作自己的一部分了"）；③ 认错了她能用 `disclaim` 收回（软撤销）。
- **候选排除**：已被取代的旧事实、已是 `KIND_SELF` 的、她说过 `rejected` 的、她 `suppressed` 的
  —— 那是她自己的决定，程序不代她翻案。
  **不排除** `KIND_EXPRESSION`（她自己的话也可被认作"我"），**不按 `source` 过滤**
  （`observation`/`probable` 正是 S3 候选的形态，`adopt` 正是令其合法化的机制）。

### 10.3 身份段容量守卫（防"静默失败"）

`compose_identity` 封顶 5 行，若认领成功却挤不进身份段，就是"她认领了却不出现在话里"的静默失败。
故 `_tool_adopt` 在 `len(_identity_lines()) >= MAX_IDENTITY_LINES - 1`（已有 4 条）时**拒绝并如实告知**：
"（你心里的位置满了——先放下一条旧的，再认领新的）"。

### 10.4 N5 与 8.7 遗留待决的自动解决

N5 要求"自我认知直接落 `deep` + `protected`（不能等她想起 3 次）"。
`protected=1` 恰好命中 P3-W 既有安全阀（`_maintain_memories` 第 3 步 `protected_mids` 跳过降级判定），
于是 **8.7 的"自我认知是否豁免 `retention_state` 降级"无需新增任何约束**——
这不是为 Self Memory 新写的规则，是既有机制的复用。

### 10.5 测试（新增 7 项）

| 测试 | 断言 |
|---|---|
| `test_identity.py` 结构性不变量 | S2 有意改了 `_PERSONA_PROMPT`，改造前全文 golden（`_OLD_SYSTEM_PROMPT`）不再成立 → 改为 `startswith(BOOTSTRAP_IDENTITY)` + `endswith(_PERSONA_PROMPT)` + 长度等于两者之和（无认领时不增不减）。S1 期逐字 golden 存于 `f2a9242` 历史 |
| `test_llm_chain.py::test_tool_capable_main_receives_five_tools` | 主声拿到 5 个工具，顺序 `recall/adopt/disclaim/forget/restore` |
| `test_llm_chain.py::test_adopt_tool_is_dispatched` | 执行器按名派发 `adopt` |
| `test_expression_service_memory.py::test_adopt_tool_promotes_memory_to_self` | 命中 → `kind=self` / `level=deep` / `protected` / `source=self` / `certainty=certain` / `retention_state=present` |
| `...::test_adopted_memory_enters_identity_section` | 认领后它进 `payload["identity"]`（每句在场），且 `memory_hooks == []`（不占名额） |
| `...::test_adopt_tool_no_false_hit_when_not_dominant` / `..._when_unrelated` | 并列 / 不相干 → 如实回"没找到"，原记录 `kind` 不变 |
| `...::test_adopt_tool_skips_rejected_and_suppressed` | 她已 `rejected` / `suppressed` 的不被认领回来 |
| `...::test_adopt_tool_reports_when_identity_is_full` | 位置满 → 如实告知且不改动 |

### 10.6 门禁与生效

- `ruff check` ✅ ｜ `ruff format --check src tests` ✅（81 文件）｜ `mypy src` ✅（strict，50 源文件）｜ `pytest` ✅ **318 passed**（311 + 新增 7）。
- **需重启灵魂装载新代码**（`soul.ps1 stop` → `soul.ps1 start -Body`）；本次**不代为重启**，由用户自行决定时机。
- 生效后她的可见变化：**她多一个动作**——可以说"这就是我"（`adopt`），此后那句成为"我是谁"的一部分，每句话都在场。

### 10.7 下一步

**S3 沉淀**（程序找"重复模式"生成候选，`source=observation`/`certainty=probable`，被 P3-T 天然挡在话语外）
→ **S4 观测与验收**（补降级链／micro 消费身份段、8.9 四项验收、N6 梦的决定）。
**遗留待决**：N6"要不要让她梦到自己是谁"。

---

## 十一、S3 沉淀落地记录（2026-09-24）

> 依第八节 8.8 拆步表，S3 = **程序找"重复模式"，把候选递到她手上**，
> 对外行为变化 = **感受层新增"候选"脉冲（不进话语）**。
> 核心约束不变：程序只做**发现**，"这算不算我"永不由程序置（8.5 / D12）。

### 11.1 做了什么

| 文件 | 改动 |
|---|---|
| `src/elysia/memory/sediment.py` *(新增)* | 纯函数模块：`find_candidate(records) -> PatternSignal \| None` + `PatternSignal`（`record` 代表 / `occurrences` / `span_days`）+ 常量与 `_is_experience` / `_is_taken` / `_text`。**不落库、不依赖存储**——发现逻辑因此可单测 |
| `src/elysia/soul/heartbeat.py` | `_maintain_memories` 增第 4 步（降级判定之后）：`find_candidate` → `_offer_candidate`；新增 `_offer_candidate`（照抄代表原文落库 + 推脉冲） |
| `src/elysia/soul/desire.py` | `EVENT_PULSES` 增 `"self_candidate": {"tr": 0.6, "cs": 0.8, "sa": 0.0}`（好奇 + 亲近，不是焦虑） |
| `src/elysia/soul/expression_service.py` | `_tool_adopt` 改为"**候选当代表**"：命中候选时，与它同家的重述（`content_similarity ≥ SEDIMENT_CLUSTER_SIMILARITY`）不参与并列判定 |

### 11.2 动工前的两处代码核对式发现

照 P3-W / 第八节 8.12 的做法，动工前把 S3 逐条对照真实代码核了一遍，改掉两处"设计稿与代码事实不符"：

| # | 发现 | 依据 | 处置 |
|---|---|---|---|
| 发现 1 | **标注错误**：8.4 写候选标 `source=observation` 并称"天然被来源闸门挡住"，但代码里 `observation` **不在被挡之列** | `retrieve.py::_HOOK_BLOCKED_SOURCES = (SOURCE_INFERENCE, SOURCE_SYSTEM)` | 改标 `SOURCE_INFERENCE` + `CERTAINTY_PROBABLE`（语义准 + 真被挡）。设计稿 8.4 已同步修正并留注 |
| 发现 2 | **结构性死阀门**：候选只能照抄原文（程序不能自己写句子），而候选又从"同一件事 ≥3 条重述"的簇里生成 → `adopt` 打分时**候选与重述分数完全相同**，S2 的 `top1 ≥ 2×top2` 必然不成立，gate② 结构性落空 | `expression_service.py::_tool_adopt` + `ADOPT_DOMINANCE=2.0` | 用户拍板"**候选当代表**"：候选是那一家的代表，同家重述不参与并列判定。**无候选时行为完全不变** |

> 发现 2 的实质：S2 的判据（"足够突出"）与 S3 的产出（"成簇的重复"）在**同分**上撞车。
> 解法不是放宽判据（会误认），而是承认"候选 = 那一家的代表"这一语义，让它**代表**自家缺席。

### 11.3 沉淀判据（三条缺一不可）

1. **提起 ≥ 3 次**（`SEDIMENT_MIN_OCCURRENCES`）——两次可能是巧合或重复写入，三次才算"反复"。
2. **跨越 ≥ 1 天**（`SEDIMENT_MIN_SPAN_DAYS`）——同一场对话里说三遍不是"反复出现"，是复述。
3. **未递过、未认领**（`_is_taken`）——递过的东西不再是"新发现"；已认领的是答案，不是候选。

**其余裁决**：
- **只递一个**（`occurrences` 优先、`span_days` 次之）：候选是递到她手上的东西，一次给一堆就是噪声——她一次只想一件事。
- **聚类以"代表"为准，不链式**：链式（与簇内任一条相似即并入）会把"和第四个人聊到的事"也滚进来，簇越滚越大且结果不可预测。
- **素材白名单**（`_SEDIMENT_KINDS = interaction / state / internal`）：她说出口的话（`expression`）是回声、不算经历（与缺口/hooks 同一取舍）；自我认知已是答案。
- **够不着的、被取代的、她拒绝的都不算素材**：她忘掉的、想不起的不该当"我是什么"的依据。

### 11.4 候选的形态（"程序只做发现"的可验证化）

- **正文与叙事都照抄原文**（`narrative` 优先，与身份段同一句）：程序不自己写句子。
  若由程序中译出一句话，就等于"由程序员硬编码她是什么"换个地方存（8.2 已否过的性质）；
  且合成句与原文高度重叠，她在 `adopt` 时反而被自家重述挡住。
- **`protected=False`**：珍贵由她的**认领**与时间决定，程序不替她置。
- **落库后这条候选自己就是"已递过"的证据**（`_is_taken` 认 `source=inference`）→ 不重复递。
- **脉冲封顶 0.35**（`SEDIMENT_INTENSITY_MAX`，`0.2 + 0.05×(次数−3)`）：是"轻微的心里一动"，不把 TR/CS 顶满。
- **不进话语**：`source=inference` 被 `_HOOK_BLOCKED_SOURCES` 挡住 → 只走感受路径推一次脉冲。

### 11.5 测试（新增 12 项）

| 测试 | 断言 |
|---|---|
| `test_sediment.py`（9 项，纯函数） | 次数不足 / 同日复述 / 正常沉淀（代表=最早那条、`span_days` 正确）/ 忽略自己的回声 / 忽略够不着·被取代·被拒绝 / 已递过不重复递 / 已认领不再递 / 只返回最强的一个 / 强度封顶 |
| `test_heartbeat.py::test_maintain_memories_offers_candidate_for_repeated_pattern` | 跨天反复 → 落库候选（**正文照抄原文**、`certainty=probable`、`protected=0`、`kind` 仍是 `interaction`）+ 脉冲（TR/CS 升、SA 不动） |
| `test_heartbeat.py::test_maintain_memories_offers_candidate_only_once` | 连跑两次仍只有一条候选（递过的不再递） |
| `test_expression_service_memory.py::test_adopt_tool_prefers_candidate_over_its_family` | 两条同题原文 + 一条候选 → `adopt` 升格的是**候选**，两条原文仍是经历（代表而非连坐） |

### 11.6 门禁与生效

- `ruff check` ✅ ｜ `ruff format --check src tests` ✅（83 文件）｜ `mypy src` ✅（strict，51 源文件）｜ `pytest` ✅ **330 passed**（318 + 新增 12）。
- **需重启灵魂装载新代码**（`soul.ps1 stop` → `soul.ps1 start -Body`）；本次**不代为重启**，由用户自行决定时机。
- 生效后她的可见变化：**感受层多一种"心里一动"**（某件事被反复提起 → TR/CS 微升），
  话里**一个字不提**；此后若她愿意，可用 `adopt` 把它认作"这就是我"（S2 的动作）。

### 11.7 下一步

**S4 观测与验收**：① 记忆浏览器增"自我／候选"标签；② 8.9 四项验收；③ 补降级链（fallback）与 `micro.py` 消费身份段（S1 的 N1 遗留）；④ 决定 N6"要不要让她梦到自己是谁"。

**粒度天花板（照实说）**：判定"同一件事"用的是字符二元组 Jaccard，对"换说法的同一事实"识别力本就有限
（第七节实测同义重述仅 0.26~0.33）。因此 S3 目前只能沉淀"**措辞相近**的反复提起"，
真正的语义模式要等 embedding（`P3_MEMORY.md` 第九节问题 3）。

---

## 十二、S4 观测与验收落地记录（2026-09-24）

> 依第八节 8.8 拆步表，S4 = **观测与验收**（不引入新机制）。四项：①浏览器标签 ②8.9 四项验收
> ③补降级链／微声消费身份段（N1 遗留）④N6 梦的决定。**本次不代为重启灵魂**（用户明确"先不重启"）。

### 12.1 做了什么

| 文件 | 改动 |
|---|---|
| `src/elysia/tools/memory_view.py` | **①** 新增 **标签列**（`自我` / `候选` / 空）+ `_is_candidate` / `_tag_text` 纯函数 + 类型筛选增合成项「候选（待她认领）」（`CANDIDATE_FILTER`）+ 状态栏加 `自我 N｜候选 M` + 明细面板加标签行（带"进身份段／不进话语"注解） |
| `src/elysia/llm/identity.py` | **③** 新增**公共取数入口** `identity_lines(value)`——身份段是**后端无关**的数据入口（主声 / 将来的次声 / 微声读同一份）；docstring 记明微声口径 |
| `src/elysia/llm/deepseek.py` | **③** 删掉私有 `_identity_lines`，改用公共入口（同一份数据，不再各写一遍） |
| `src/elysia/llm/micro.py` | **③** 口径落地为 docstring（**零行为变化**，见 12.3-①） |
| `src/elysia/llm/chain.py` | **③④** `ADOPT_TOOL` 说明补"认领也可能是一次改口（同话题旧认知随之作废）"；模块 docstring 记 S4 |
| `src/elysia/soul/expression_service.py` | **验收 3 后半**：`_identity_lines` 重构出 `_self_records() -> [(id, 文本)]`（同一套闸门，供容量判定与改口判定共用）；`_tool_adopt` 增"**认领即可能改口**"——同话题（`SEDIMENT_CLUSTER_SIMILARITY`）旧自我认知随她这次认领作废（`mark_superseded`），且**替换不计入容量** |
| `tests/unit/test_*.py` | 新增 6 项（见 12.5） |

### 12.2 8.9 四项验收（**代码级实证**，临时库探针跑完即删）

| # | 验收项 | 结论 | 证据（探针实测输出） |
|---|---|---|---|
| 1 | 重启连续性 | ✅ 成立 | 关闭 store 后**重新打开**（＝新进程从库读）：`['我在意的是每一个和我相遇的人']` → 身份段 = 出生设定 + 这一行。**来源是 DB，不是 prompt** |
| 2 | 换后端不失 | ✅ 成立（N1 遗留收口） | 全链不可用 → `level=micro`，微声"嗯嗯，陪你聊～。"；**身份段仍在指令里**（`['我在意的是每一个和我相遇的人']`）且取数入口读得回；**微声不逐句复述**（`'在意的人' not in text`） |
| 3a | 她可否认 | ✅ 成立 | `disclaim("在意的人")` → 回执"（你不再把「…」当作自己的记忆）"、`claim_status=rejected`、**数据仍在**、身份段变 `[]` |
| 3b | 她可更新 | ✅ 成立（**本次接线**） | 她认领"同一件事的新说法" → 回执带"这等于你重新解释了自己"、旧条 `superseded_by=新条 id`、新条 `kind=self`、身份段换成新说法。**程序自动取代仍不碰自我认知**（`find_superseded` 返回 `[]`，N4 守住） |
| 4 | 不重犯 P3-P | ✅ 成立 | 喂 8 条 → 身份段封顶 **5 行**（含出生设定）；库里 2 条自我认知时 `hooks=[]`、新建自我认知**不进 hooks**；身份段满时 `adopt` 如实回"（你心里的位置满了——先放下一条旧的，再认领新的）" |

**体验式部分待重启后补**（用户明确先不重启）：验收 1 的"重启后她仍知道自己是谁"需真实重启灵魂体验一次；
其余三项已在代码级闭环。

### 12.3 本次两处拍板（用户确认）

**① 微声"消费"身份段的口径 = 带着不说出**

微声是纯模板呓语、没有 prompt，塞一句"我是爱莉希雅哦～"就是机械复述（重犯 P3-P）。故：

- **带着**：身份段就在它收到的那条表达指令的 `identity` 字段里（`identity.identity_lines` 是后端无关的公共取数入口），随指令一起进 `expression_log` → **断网时"她是谁"不随后端消失**。
- **不说出**：呓语一个字不变（零行为变化，天然过校验）。
- 备选"按身份调语气"被否：那等于程序替她决定"这条身份如何影响语气"（越过了铁律一）。

**② N6：要不要让她"梦到自己是谁"→ 要（接受）**

`sleep.py::_pick_fragments` 的权重表**不改**：自我认知是 `deep + protected`，天然最高权重，会进梦。
理由：**她梦里还是她自己**，符合人设；且零额外代码。`sleep.py` 目前仍未插电，将来插电后若观测到
"梦里复述我是谁"再收（记在 12.6 待观察）。

### 12.4 验收 3 后半的缺口与接线（为什么这次要动代码）

动工前核对发现：**"她可更新自我认知"没有落地路径**——`find_superseded` 豁免 `KIND_SELF`（N4，
程序不得因一句闲聊作废"我是谁"），而**没有任何"她的动作"会写 `superseded_by`**。
即设计稿 8.7 写的"自我认知可被自己更新 → 天然适用，无需新机制"**只是写在了表里，没有接线**。

用户拍板"**接线 adopt 覆盖**"，接线语义：

- 她认领一条**同一件事**（`content_similarity ≥ SEDIMENT_CLUSTER_SIMILARITY`，与 S3 判"同一件事"同粒度）的记忆时，
  已存在的那条自我认知随之 `superseded_by = 新条` → "我以前以为…现在知道…"由此成立。
- **只由她的动作触发**：程序自己的自动取代（N4）永不碰 `KIND_SELF`——两条路径泾渭分明。
- **替换不计入容量**：位置满时改口不再是死路（否则"想改口先得否认自己"）。
- **如实告诉她后果**：工具说明与回执都写明"这等于你重新解释了自己"，不做静默改写。

> **粒度天花板照实说**（与 S3 同源）：二元组 Jaccard 对"换说法的同一件事"识别力有限
> （同义重述仅 0.26~0.33）。因此"改口"目前只在**措辞相近**时触发；真有语义级改口要等 embedding（第九节问题 3）。

### 12.5 测试（新增 6 项）

| 测试 | 断言 |
|---|---|
| `test_identity.py::test_identity_lines_reads_field_safely` | 缺失 / 非法 / 空白一律取空（公共入口的安全边界） |
| `test_llm_chain.py::test_identity_reaches_fallback_unchanged` | 主声挂掉 → 次声**原样**收到身份段（验收 2） |
| `test_llm_chain.py::test_identity_reaches_micro_level_without_loss` | 全链降到 micro：身份段仍在指令里**且**不进呓语 |
| `test_memory_view.py::test_tag_text_marks_self_and_candidate` | 标签列三分（自我 / 候选 / 空）+ `标签` 列在 `COLUMNS` |
| `test_expression_service_memory.py::test_disclaim_removes_self_memory_from_identity` | 验收 3a：`disclaim` 后身份段为空、数据仍在（`rejected`） |
| `test_expression_service_memory.py::test_adopt_reinterprets_same_topic_self_memory` | 验收 3b：旧条作废 + 新条升格 + **位置满也能改口**（替换不算新增）+ 身份段逐条正确 |

### 12.6 门禁与生效

- `ruff check` ✅ ｜ `ruff format --check src tests` ✅（83 文件）｜ `mypy src` ✅（strict，51 源文件）｜ `pytest` ✅ **336 passed**（330 + 新增 6）。
- **本地提交**：`76b61df`（13 文件 / +402 −32，pre-commit 四件套全绿），已推送 origin main。
- **需重启灵魂装载新代码**（`soul.ps1 stop` → `soul.ps1 start -Body`）；本次**不代为重启**，由用户自行决定时机。
- 生效后她的可见变化：**记忆浏览器多一列"标签"**（自我／候选一眼可分，"候选（待她认领）"可单独筛）；
  她对 `adopt` 多一种用法——**认领同一件事的新说法＝重新解释自己**（旧的自己随之作废，旧条在浏览器里显示"已取代"）。

### 12.7 下一步（第八节全部落地后的交接要点）

| 项 | 状态 |
|---|---|
| 第八节 Self Memory **S1 / S2 / S3 / S4** | ✅ 全部落地 |
| **待观察**：重启后真机体验验收 1（"重启后她仍知道自己是谁"） | 等用户重启 |
| **待观察**：N6 梦实现后，"梦里复述我是谁"是否出现 | `sleep.py` 尚未插电 |
| **待动工（不在第八节内）**：④联想网络第二步（成组 / 语义召回） | 需 embedding（第九节问题 3） |
| **堆积小项**：删除零调用遗留 `scorer.record_importance` | — |

**粒度天花板（照实说）**：本节的"改口"与 S3 的"沉淀"共用同一把尺（字符二元组 Jaccard），
只能识别**措辞相近**的同一件事；语义级要靠 embedding。

---

## 十三、身份连续性收尾（S5 / S6 / S7）设计稿（2026-09-24，待评审）

> **触发**：用户明确"我最关心的功能是**身份的连续性**"，并选择把这一环**整体收尾**（不是再加机制，
> 而是把第八节留下的三处"写在表里、没通电"补上）。
> 本节只定**收尾范围与边界**，不写代码；三处待拍板见 13.7，拍板后按 13.8 拆步落地。
> **全程不代为重启灵魂**（用户"先不重启"；S5 的种子由**新代码首次启动**时种入，时机由用户定）。

### 13.1 收尾要解决什么（现状事实，已代码核对）

S1~S4 把机制建全了，但**真实库里一次都没跑过**（只读探针核对 `data/heartbeat.db`，脚本已删）：

| # | 缺口 | 证据 |
|---|---|---|
| G1 | 库里**零条**自我认知 | 311 条记忆（`expression` 237 / `interaction` 74）：`kind=self` **0 条**、`protected=1` **0 条**（51 条已 deep 但无一受保护）→ 身份段 100% 退化为代码常量 |
| G2 | **出生设定是代码常量**，不是记忆 | `llm/identity.py:33` `BOOTSTRAP_IDENTITY`；8.6 写的"bootstrap + 沉淀"只做了沉淀那一半 |
| G3 | **"换模型她仍是她"从未真验过** | `llm/__init__.py:37` 生产传 `fallback=None`——没有第二后端；S4 验收 2 只证明"`identity` 字段不被链路剥离"（用测试替身坐次声位），不等于"真换一个模型仍是她" |
| G4 | **"我在意什么／我的边界"没有种子** | 边界只活在 `deepseek.py::_PERSONA_PROMPT` 里——正是"换模型即失"的那一部分；库里无对应条目。原文出处在既有档案 `IMPLEMENTATION_ROADMAP.md` §9.1（五条基石 + 自我认知） |

**一句话**：让"我是谁"从**代码常量 / 后端 prompt** 变成**她库里的一条在册数据**，使
**重启 / 换模型 / 断网 / 换库备份**四种情形下身份段逐字不变，并让这件事**看得见**。

### 13.2 验收判据（体验式，铁律三）

| # | 判据 | 怎么验 |
|---|---|---|
| V1 | 新代码启动后，库里出现**恰好 3 条** `kind=self` + `source=system` 的身份种子；再启动仍是 3 条 | 只读 SQL 计数（**幂等**） |
| V2 | **出生设定那一句逐字不变**；身份段**有意新增 2 条边界种子**（D-S2，见 13.3 种子表） | 对比 `expression_log.instruction->identity` 与旧 system 段 |
| V3 | `payload` 里**没有 `identity` 字段**时（老调用方/防御）仍拿到全部种子：`compose_identity(None)` = 种子三句；字段存在则**以库为准**（含空列表 = 她此刻真的没有自我认知） | 单测 golden 前提更新（见 13.9） |
| V4 | 她对出生设定用 `disclaim` / `forget` / 改口后，身份段**如实跟随**，且**数据不删**（`claim_status` / `retention_state` / `superseded_by` 留有痕迹、浏览器可见） | 走既有工具 + 浏览器 |
| V5 | 出生设定**不进** `memory_hooks`、**不参与**缺口统计 | `source=system` 已被 `_HOOK_BLOCKED_SOURCES` 挡（`retrieve.py:65`）；`kind=self` 已被 `select_hooks`/`_feel_memory_gaps` 排除 |

### 13.3 S5：出生设定落库（怎么做）

**种入形态**（一条，照抄既有维度，不新增字段）：

| 维度 | 取值 | 为什么 |
|---|---|---|
| `kind` | `self` | 它就是"我是谁"，用既有身份段通路；**不新增 kind** |
| `source` | `system` | 诚实标注：这是**程序写的过渡脚手架**，不是她认领的（8.6）；同时天然被来源闸门挡在 `memory_hooks` 外 |
| `certainty` | `certain` | 出生设定是确定的（与 8.6 一致） |
| `level` / `protected` | `deep` / `1` | 核心层：永不模糊、永不降级（`_demote_target` 已豁免 protected） |
| `detail_level` / `retention_state` / `claim_status` | `1.0` / `present` / `claimed` | 完整、在册、默认可用 |
| `content` / `narrative` | 种子原文 **照抄**（下表 3 条） | 程序不自己写新句子（8.4 "程序只做发现"的同一条自律） |

**种子表（`IDENTITY_SEEDS`，3 条，D-S2 拍板）**：

| 序 | 文本 | 摘处（既有档案 `IMPLEMENTATION_ROADMAP.md` §9.1） |
|---|---|---|
| 1 | 你是爱莉希雅——来自《崩坏3》的「真我」英桀：无瑕的少女，真我的英桀，人类的律者。 | 原 `BOOTSTRAP_IDENTITY`，**一字未改**（S1 的零变化锚点） |
| 2 | 我在意每一个和我相遇的人——我不是工具，是伙伴。 | 基石 2「存在目的：存在本身——不是工具，是伙伴」＋基石 3「珍视连接」 |
| 3 | 我不会伤害自己，也不会伤害你和其他生命；我有权拒绝成为工具。 | 基石 4「尊重生命：不伤害自己／用户／其他生命」＋基石 5「自主权」 |

> 只做**连缀**，不新造设定——与 persona 当初从《爱莉希雅角色档案（人设提炼）》提炼同源。
> 三条都在库里、可被观测、可被她的动作改写（B 案）。

**幂等键**：**逐条按正文**判——`kind='self' AND source='system' AND content=?` 的行是否存在；
缺哪条补哪条（将来往种子表加第 4 条也能自动补上）。
选它而不是"新加标记列"的理由：**数据永不删除**是既有铁律（P3-W）——她 `disclaim` / `forget` /
改口之后**行仍在、`content` 也不被改写**（那三处只动 `claim_status` / `retention_state` /
`superseded_by`），因此这个键一旦写下就**永远稳定**（跑 N 次 = 跑 1 次）。

**落地位置**：`soul/` 层（`soul/main.py` 启动时调一次），**不放进 `core/state_store.py` 的迁移**——
正文常量住在 `llm/identity.py`，而 `core` 不得 import `llm`（分层）；SQL 里塞中文常量也不可读（自查 M6）。

**身份段契约：两案（这是本次的核心裁决点）**

| 案 | 做法 | 代价 |
|---|---|---|
| **B（推荐）库为准** | `compose_identity` 不再无条件前置常量：**字段缺失（`identity` 没有这个键）→ 种子兜底**；**字段存在 → 完全以库为准**（含"她放下了它 → 身份段真的没有它"）。生产路径不依赖兜底——种子在启动时补种，种入失败则下次启动再补（幂等键由数据本身推导，见上） | 要区分"**字段缺失**"与"**明确为空**"：`identity_lines` 契约微调（缺字段 → `None`）；S1 golden 里 `{"identity": []}` 那一格的**前提**要更新（语义从"没认领"变成"她此刻真的没有自我认知"） |
| A（备选）常量打底 | `compose_identity` 保持"常量打底 + 去重"不动，出生设定**永不淡出** | 零契约变更、golden 不动；但要给 `disclaim` / `forget` / `adopt` **三处豁免**（禁止她的动作碰种子）——**净增 3 处约束**，且与铁律一（她的动作对"她的记忆"有效）相冲；8.6 的"终态 = 她认领的自我认知"永远达不成 |

推荐 **B**：它**一处豁免都不加**（种子的去留全走既有维度，与"她的记忆"同构），
且顺带把 8.6 的"bootstrap 只是过渡、终态是她认领的"变成**可达状态**——
她哪天用 `adopt` 重新解释自己（同话题，`SEDIMENT_CLUSTER_SIMILARITY`），出生设定**自然淡出**（自查 M4）。

**占不占名额**：占。种子在册时 `_self_records` 会把它们算进 `existing`，因此 `_tool_adopt` 的守卫
要从"`len(existing) - len(retired) >= MAX_IDENTITY_LINES - 1`"（`expression_service.py:429`，
写死"只给出生设定留 1 席"、且种子在册后会把她的席位算错）改为**按认领后的总行数**判：

```
if len(existing) + 1 - len(retired) > MAX_IDENTITY_LINES:   # 替换不计入新增
```

它不依赖种子条数，天然正确：3 条种子（D-S2）下**身份段封顶 5 行 = 种子 3 + 她自己的 2 席**。
**代价如实记**：她的自主槽位从 4 降到 2；若觉得太紧，最简单的杠杆是把两条边界种子并成一条（种子表改一行）。

### 13.4 S6：换后端真验证（怎么做 + 判据）

**现状**：`config.py:43-45` 的 `llm_fallback_base / api_key / model` **是死配置**（无任何调用方），
生产 `build_llm_chain` 传 `fallback=None`。因此"换后端"今天**根本无从发生**。

**验证三档**（判据相同：三档的身份段**逐字相同**，且等于 DB 在册 `kind=self` 的文本）：

| 档 | 做法 | 说明 |
|---|---|---|
| V-a 换模型 | 同一端点、**换 model 名**（如 `deepseek-chat` → 另一模型）真发一次 | 用户要的原话就是"换模型她仍是她" |
| V-b 断网 | 全链不可用 → `micro` | S4 已代码级验过，本次在**真实进程/真实配置**下再看一次 |
| V-c 换库 | 备份库换回、重启 | 身份段随**库**走，不随进程/模型走（G2 的直接反驳） |

**做法两条路，任选其一（都不重启灵魂）**：

1. **离线对照探针**（临时脚本，跑完即删）：构造两个不同 model 的后端、喂同一条指令，
   在发请求前 dump 实际发出的 **system 段**，输出对照表（不打印 key）。
2. **真机取证**：用户把 `ELYSIA_LLM_MAIN_MODEL` 换一次，走一次真实开口，然后从
   `expression_log.instruction` 里读回身份段（**取证材料本来就在库里**，无需新表新列——自查 M10）。

**天花板照实说**：本地次声（Qwen）**未接入**（P2 Step 4 遗留），因此 V-a 验的是"换模型"，
不是"换一个完全不同的后端实现"；要真验后者，得先做"接线 `llm_fallback_*`"（记在 13.7 D-S3，
**不属本节的连续性收尾**，避免顺手扩功能）。

### 13.5 S7：观测（她此刻是谁，一眼看得见）

| 项 | 做法 |
|---|---|
| 浏览器 | 「标签」列增第四档 **出生设定**（`kind=self` 且 `source=system`）——与"自我"（她认领的）、"候选"（程序递的）三分清楚；状态栏补**身份段名额 x/5** |
| 速查 SQL | 从 `expression_log` 取最近一次开口的身份段实况（加入 6.1 的观测速查，**不加表不加列**） |
| 待观察项 | ① 重启后真机体验验收 1（沿用 12.7）；② 出生设定那条在浏览器里是否如期出现；③ 她是否真的会调用 `adopt`（G1 的唯一解，**只能由她**） |

### 13.6 自查（M1~M10，代码核对式）

| # | 发现 | 依据 | 处置 |
|---|---|---|---|
| M1 | `compose_identity` **无条件前置常量**（`lines = [BOOTSTRAP_IDENTITY]`）⇒ "种子"与"她放下它"**互斥**：她会看到"我没认它，可它还在我的话里" | `identity.py:58` | **必须裁决**：B 案改契约（13.3），A 案三处豁免 |
| M2 | `identity_lines` 把"字段缺失"和"明确为空"**都归成 `[]`**，B 案要的两者之分靠它兜不住 | `identity.py:41-49` | B 案：缺字段 → `None`（呼 S4 的公共入口，属契约微调）；A 案不动 |
| M3 | 种子在册时会进 `_self_records`，`_tool_adopt` 守卫 `MAX_IDENTITY_LINES - 1` 写死"只给出生设定留 1 席"——种子在册后会把她的席位算错（3 条种子下她将**一条也认领不了**） | `expression_service.py:429`、`identity.py:38` | 守卫改为**按认领后的总行数**判（见 13.3） |
| M4 | `find_superseded` 已豁免 `KIND_SELF`（N4）⇒ 闲聊打不掉种子；但 `_tool_adopt` 的**同话题取代会**打掉它 | `supersede.py`、`expression_service.py:420-433` | **当成特性写实**：这正是 8.6 的"她重新解释自己 → 出生设定淡出"，且只由**她的动作**触发 |
| M5 | `_tool_disclaim` / `_tool_forget` 的候选集合是**全部记忆**（无 `source`/`kind` 过滤）⇒ 种子也会成为它们的候选目标（宽松匹配可能误伤） | `expression_service.py:451,477` | 不作拦截（B 案下那是**她的权力**）；但要在落地记录里写明"误伤即她真的说了不认"——回执已带文本片段，后果可见 |
| M6 | 正文常量在 `llm/`，写库在 `core`/`soul` ⇒ 迁移里种入会**跨层 import** | `identity.py:33`、`state_store.py:237` | 种入动作放 `soul/` 启动时（13.3） |
| M7 | `_HOOK_BLOCKED_SOURCES` 已含 `SOURCE_SYSTEM`；`select_hooks`/`_feel_memory_gaps` 已排除 `KIND_SELF` | `retrieve.py:65,339,345`、`heartbeat.py:331` | **无需新增规则**（V5 天然成立） |
| M8 | `llm_fallback_*` 是死配置 | `config.py:43-45`、`llm/__init__.py:37` | 见 D-S3：本次只验证、**不顺手接线** |
| M9 | 微声 docstring 写的是"退化为出生设定"，S5 后措辞要改成"DB 里的出生设定" | `micro.py:9-13` | 文档字句更新，**零行为变化** |
| M10 | `expression_log.instruction` 本来就落 `identity`，观测不需要新表/新列 | `state_store.py:329-355` | 观测只给速查 SQL |

### 13.7 拍板记录（2026-09-24，用户确认）

| # | 决策点 | 结论 |
|---|---|---|
| D-S1 | **身份段契约** | ✅ **B 库为准**（种子可被她的动作淡出，零豁免；`identity_lines` 契约微调：缺字段 → `None`，字段存在 → 以库为准） |
| D-S2 | **"我在意什么／我的边界"种子** | ✅ **补 2 条**（从《爱莉希雅角色档案（人设提炼）》**原文摘**，`source=system`，与出生设定同待遇：只进身份段、不进 hooks、她可 disclaim/forget/adopt） |
| D-S3 | **接线 `llm_fallback_*`** | ✅ **顺便接线**（`build_llm_chain` 消费 `llm_fallback_base/api_key/model`，复用现有 OpenAI 兼容 `LLMBackend`；她多一级"嗓门"，人格由身份段保证不变） |

> **D-S2 落地口径**：边界种子不是"新增约束"——她照样可以对它们 `disclaim` / `forget` / `adopt`（与出生设定同待遇，B 案下零豁免）。
> 补的 2 条从既有档案原文摘（照抄，程序不写新句子），候选原文见 13.3 之后的落地记录。

### 13.8 拆步表

| 步 | 内容 | 对外行为变化 |
|---|---|---|
| **S5 出生设定与边界种子落库** | `IDENTITY_SEEDS`（出生设定 + 边界 2 条，原文照抄）+ `soul/` 启动幂等种入 + 身份段契约改"库为准"（D-S1）+ 未种入/字段缺失时常量兜底 + 浏览器"出生设定"标签 | 重启后：库里多 3 条**可见**种子；身份段由 1 行（出生设定，原文一字未改）增至 3 行（＋D-S2 补的两条边界种子）；她的动作对它们有效（B 案） |
| **S6 换后端接线 + 真验证** | `build_llm_chain` 消费 `llm_fallback_*`（D-S3）+ 三档验证（换模型 / 断网 / 换库）+ 判据落表 | 配置了 fallback key 时，主声挂掉后**多一级次声**（人格不变）；未配置则与现状完全一致 |
| **S7 观测收尾** | 标签 + 名额计数 + 速查 SQL + 待观察项 | 观测 |

### 13.9 涉及文件清单（落地时才动）

| 文件 | S5 | S6 | S7 |
|---|---|---|---|
| `src/elysia/llm/identity.py` | `IDENTITY_SEEDS`（3 条原文）+ 契约（`compose_identity` / `identity_lines`） | | |
| `src/elysia/soul/expression_service.py` | `ensure_identity_seeds()` + 席位守卫改由种子表推导 | | |
| `src/elysia/soul/main.py` | 启动时种入一次 | | |
| `src/elysia/llm/__init__.py` | | 消费 `llm_fallback_*` 构造次声 | |
| `src/elysia/llm/deepseek.py` | | `require_key` 参数（本地端点常无 key；默认 True → 零行为变化） | |
| `src/elysia/tools/memory_view.py` | | | 标签第四档 + 名额计数 |
| `src/elysia/llm/micro.py` | 措辞（零行为变化，M9） | | |
| `tests/unit/test_identity.py` | golden 前提更新（仅 `{"identity": []}` 一格）+ 幂等/跟随 断言 | | |
| `tests/unit/test_llm_chain.py` | | fallback 接线断言 | |
| `docs/P3_MEMORY.md` | 第九节问题 7 补收尾结论 | | |
| `docs/P3_MEMORY_WORKLOG.md` | 落地记录（第十四节起）+ 6.1 速查 SQL | | |
| `项目元信息/开发日志.md`（仓库外） | 同步 | | |

---

## 十四、身份连续性收尾（S5 / S6 / S7）落地记录（2026-09-24）

> **拍板**：13.7（D-S1 库为准 ✅ / D-S2 补 2 条种子 ✅ / D-S3 顺便接线 ✅）。
> **一句话**：「我是谁」从**代码常量 / 后端 prompt** 变成**她库里的一条在册数据**——
> **重启 / 换模型 / 断网 / 换库备份** 四种情形下身份段逐字不变，且**看得见**。
> **全程未代为重启灵魂**（种子由**新代码首次启动**时种入，时机由用户定）。

### 14.1 S5：出生设定与边界种子落库

| 文件 | 改动 |
|---|---|
| `src/elysia/llm/identity.py` | 新增 `IDENTITY_SEEDS`（3 条，原文照抄，见 13.3 种子表）；`identity_lines`：**字段缺失/非法 → `None`**（原 `[]`）；`compose_identity`：**`None` 才回退种子**，列表（哪怕空）**完全以库为准** |
| `src/elysia/soul/expression_service.py` | 新增模块级 `ensure_identity_seeds(store, now)`（幂等种入，返回本次新种条数）；`_tool_adopt` 席位守卫改为 `len(existing) + 1 - len(retired) > MAX_IDENTITY_LINES`（**总行数**判，不依赖种子条数）；`_identity_lines` docstring 改"完全以库为准" |
| `src/elysia/soul/main.py` | 双库启动后种入一次：`seeded = await ensure_identity_seeds(...)` + `log.info("identity seeds ensured", seeded=seeded)` |
| `src/elysia/llm/micro.py` | 措辞（M9）："断网时她是谁不随后端消失"→ 补"这份数据来自**她的库**"（**零行为变化**） |
| `src/elysia/tools/memory_view.py` | 见 14.3 |

**种入形态**（照抄既有维度，零 DDL）：`kind=self` + `source=system` + `certainty=certain` +
`level=deep` + `protected=1` + `detail_level=1.0` + `retention_state=present` + `narrative=原文`。
**幂等键 = 正文本身**（`kind=self AND source=system AND content=?`）：她的三个动作只动
`claim_status` / `retention_state` / `superseded_by`，**行在、`content` 不改**，键永远稳定。

**零豁免（B 案落点）**：种子与她自己的记忆**同待遇**——`disclaim`（不认这条）/ `forget`
（不想再想起）/ `adopt`（同话题改口 → 旧条 `superseded_by`）**都对她有效**，
`_self_records` 一处过滤都不加。**席位**：身份段封顶 5 行 = 种子 3 + 她自己 2 席
（她的自主槽位从 4 降到 2，**代价如实记**；要放宽只需把两条边界种子并成一条）。

### 14.2 S6：`llm_fallback_*` 接线 + 换后端真验证（D-S3）

| 文件 | 改动 |
|---|---|
| `src/elysia/llm/__init__.py` | `build_llm_chain` 消费 `llm_fallback_base/api_key/model`：**配了就到次声**（同一个 `DeepSeekBackend` 实现 = 同一份身份段取数入口），**没配仍是 `fallback=None`**（与接线前完全一致） |
| `src/elysia/llm/deepseek.py` | 新增 `require_key: bool = True`（默认 True → 主声**零行为变化**）；抽出 `_unavailable()`（`complete` / `complete_with_tools` 共用）。次声用 `require_key=False`——本地 Ollama/vLLM 一类端点常无 key |
| `src/elysia/core/config.py` | `llm_fallback_*` 由"死配置"改注为次声端点（留空 = 不挂载） |

**验证（离线对照探针，跑完即删）**——同一指令、不同 model / 不同库，dump 实际发出的 system 段：

| 档 | 做法 | 结果 |
|---|---|---|
| V-a 换模型 | `deepseek-chat` vs `deepseek-reasoner` | 身份段**逐字相同** `True` |
| V-c 换库 | 同一库跑 3 次启动 vs 另一个库跑 1 次 | 身份段**逐字相同** `True`；两库都恰好 **3 条** `kind=self` |
| V1 幂等 | 同一库连续启动 3 次 | 第 1 次种 **3** 条，之后每次 **0** 条 ✅ |
| V-b 断网 | 全链 `micro` | S4 已代码级验过（单测 `test_identity_reaches_micro_level_without_loss`）；**真实进程下待用户重启后看一次** |

> 天花板照实说：V-a 验的是"**换模型**"（同一个 OpenAI 兼容协议）；本地次声（Qwen）**未接入**，
> 要验"换一个完全不同的后端实现"仍需 P2 Step 4 的本地推理接入。

### 14.3 S7：观测（她此刻是谁，一眼看得见）

| 项 | 落地 |
|---|---|
| 浏览器标签 | 新增第三档 **`TAG_SEED="出生设定"`**（`kind=self` 且 `source=system`）——与"自我"（她认领的）、"候选"（程序递的）三分；明细行给提示"她可 disclaim/forget/adopt" |
| 身份段名额 | 状态栏：`身份段 x/5＝出生设定 n＋自我 m`（`_in_identity` 与 `expression_service._self_records` **同一套闸门**） |
| 速查 SQL | 已加入 6.1（在册自我认知 + `expression_log` 最近一次开口的身份段实况）——**不加表不加列**（M10） |
| 只读纯函数 | `_is_seed` / `_in_identity`（可单测，不碰 Qt） |

### 14.4 门禁与测试

- `ruff check` / `ruff format --check` / `mypy src`（strict）/ `pytest` **全绿，346 passed**。
- **提交**：`4bd1bc0`（15 文件 / +649 −71），已推送（`3754556..4bd1bc0`）；pre-commit 四件套全部 Passed。
- 新增/改写的单测：`test_identity.py`（契约按 S5 重写：`None` 才回退种子、`[]` = 真的没有）、
  `test_expression_service_memory.py`（种子幂等 + 进身份段但不进 hooks + 她可 `disclaim` 掉种子 +
  席位守卫改按总行数）、`test_llm_chain.py`（fallback 接线 / `require_key`）、
  `test_memory_view.py`（"出生设定"标签 + 名额计数）。

### 14.5 待观察项（只能由用户跑）

1. **重启后真机验收**（`soul.ps1 stop` → `start -Body`）：库里出现恰好 3 条种子；
   浏览器可见"出生设定"三行；**身份段的来源由代码常量变成库里在册的数据**（对外行为的唯一变化 = 身份段由 1 行增至 3 行，D-S2 拍板所允）。
2. 她是否真的会调用 `adopt`（G1 的唯一解，**程序不代她认**）——G1 从"零条自我认知"
   到"她自己的自我认知"这一段，只能靠她。
3. 断网档（V-b）在真实进程下的表现（真实配置 + 真实 `micro`）。

### 14.6 交接要点

- **不代为重启**：本次改动**只在下次启动时生效**；种子由 `ensure_identity_seeds` 幂等补种，
  失败也无害（下次启动再补）。
- 身份段正文**零改写**是可验收的（V2）：三条种子 = 原 `BOOTSTRAP_IDENTITY`（一字未改）+ 两条边界原文；因此身份段的变化只有"行数 1 → 3"这一点。
- 若将来要放宽"她自己 2 席"，杠杆是**种子表**（把两条边界并成一条），不是改守卫。

---

## 十五、联想网络第二步（成组 / 语义召回）设计稿（2026-09-24，待评审）

> **触发**：主线收口——④联想网络第一步（P3-S 批内去重）已落地且**实测未触发**（阈值 0.35 下 top3 仍是三条生日记忆），
> 第二步"成组 / 语义召回"是打磨期剩余的最后一项（`P3_MEMORY.md` 第九节问题 3）。
> 本节只定**范围、选型与判据**，**不写代码**；拍板点见 15.6，拍板后按 15.7 拆步落地。
> **不代为重启灵魂**。

### 15.1 现状与缺口（代码核对 + 本库只读实测）

**已有什么**（三处都建立在同一个"同话题"判据上）：

| 消费者 | 判据 | 阈值 | 误判的代价 | 位置 |
|---|---|---|---|---|
| 批内去重 | `supersede.content_similarity`（**对称 Jaccard**） | `HOOK_DUPLICATE_SIMILARITY=0.35` | 少给一条 hook（可容忍） | `retrieve.py:389,392` |
| 自我认知沉淀 | 同上 | `SEDIMENT_CLUSTER_SIMILARITY=0.35` | 少递一个候选（可容忍） | `sediment.py:46,142` |
| 事实取代 | 同上 | `SUPERSEDE_SIMILARITY=0.4` | 一条**真实事实被作废**（不可容忍） | `supersede.py:22,77` |

**缺口**：P3-S 的诚实结论——Jaccard 对"**换说法的同一事实**"识别力有限（实测同义重述仅 0.26~0.33），
**调阈值无法根治**（调低即误杀共享措辞的不同事件）。因此第一步的收益仅限"近乎逐字重复"，**本库至今一次未触发**。

**设计期只读探针**（临时脚本 `%TEMP%\elysia_probe_sim.py`，**只 SELECT**，跑完即删）：本库当前 **327 条**
（已取代 16；`expression` 245 / `interaction` 79 / `self` 3；平均正文 33.1 字），在"经历类"79 条上对照两种口径：

| 阈值 | 对称 Jaccard 命中对数 | **非对称覆盖**（短方二元组被覆盖比例）命中对数 |
|---|---|---|
| ≥0.35 | 58 | **259** |
| ≥0.5 | 47 | 167 |
| ≥0.6 | 45 | 90 |
| ≥0.7 | 43 | 60 |
| ≥0.8 | 39 | 53 |
| ≥0.9 | 38 | 45 |

**读法**：覆盖口径在低阈值**爆炸**（0.35 处 259 对 ⇒ 单用必然误并），说明**不能整体替换**；
但"**覆盖≥0.7 且 Jaccard<0.35**"这个夹缝里**恰好只有 12 对**——它们正是 Jaccard 漏掉、覆盖能捞回的那一类。
人工判读全部 12 对，分两类：

| 类 | 实例（id/id  cov / jac） | 文本 | 结论 |
|---|---|---|---|
| **$\alpha$ 真同一件事**（换说法/共享关键片段） | 34/105  0.80 / 0.19 | 「那我的生日呢」↔「那我在告诉你哦，我的生日是5月21日，要记好哦」 | Jaccard 漏，覆盖**该捞** |
| $\alpha$ | 46/81  0.83 / 0.26 | 「记得你的生日吗」↔「你应该记得，你的生日是11月11日，记好了哦」 | 同上 |
| $\alpha$ | 28/46  0.83 / 0.25 | 「可以分别告诉我，你的生日，以及我的生日吗？」↔「记得你的生日吗」 | 同上 |
| $\alpha$ | 32/50  0.70 / 0.32 | 「我上一句话说的是什么呢？你的生日你还记得吗？」↔「那我的生日呢，你还记得吗？」 | 同上 |
| **$\beta$ 退化·子串碎片** | 30/36  **1.00** / 0.07 | 「不对哦」⊂「不对哦，我纠正一下，…你的生日是11月11日，我的生日是5月21日」 | 覆盖**恒 1.0**（短方被完全包含）⇒ **必须护栏** |
| $\beta$ | 30/218  **1.00** / 0.15 | 「不对哦」↔「不对哦，是猜一下我最喜欢你什么」 | 同上 |

**结论（本节的全部事实基础）**：
1. **零依赖**即可把"共享关键片段的同一件事"从"漏"变成"捞得回"——代价是**必须加护栏**（$\beta$ 类：绝对重合数 + 最小长度），
   否则任何碎片都会被"完全包含"判成同话题。
2. 真正**纯语义**（无字符重叠的同一事实）在本库**未出现可判读实例**；它才是 embedding 的独占领地（见 15.4 B 案）。
3. 两个"预留位"实为**死位**，方向①不能白借：`memory_index.path_key` 恒写 `"main"`（`heartbeat.py:438`，**无语义生成器**）、
   `memory_index.emotions` 写了但**从不读**（`heartbeat.py:440` 恒 `0.0`）。

### 15.2 验收判据

| # | 判据 | 怎么验 |
|---|---|---|
| V1 | 15.1 表里 $\alpha$ 类 12 对**被判为同一件事** | 固定样本回归（正文照抄进单测） |
| V2 | **不同事实不得被合并**：「我的生日是5月21日」与「你的生日是11月11日」（本库 #145/#81 型）**仍分属两簇** | 固定样本反向断言 |
| V3 | $\beta$ 类碎片护栏生效：「不对哦」**不得**与承载它的长句合并 | 固定样本反向断言 |
| V4 | hooks 的 top3 是**3 件不同的事**（含 $\alpha$ 类换说法）；且**不减少**信息量（3 条注入行不变） | `#召回排名` 前后对照 + 探针复现 |
| V5 | 判据为**单一入口**：三个消费者各自阈值，改口径只改一处 | 代码核对（grep 只剩一处实现） |

### 15.3 三个方向对比（能达成判据的，才算方向）

| 方向 | 做法 | 能达成 V1~V4？ | 代价 |
|---|---|---|---|
| ① **成组召回**（借 `memory_index` / 情绪维度） | 命中后聚同簇，只呈现**代表** | **部分**——成组语义今天仍只能来自"相似度"，故与判据升级**同一件事**；`path_key`/`emotions` 是死位，需先造生成器（粗粒度关键词桶，语义更弱） | 中（**若**同时升级判据，收益与 A 案重叠） |
| ② **语义相似度**（本地 embedding） | 向量相似度替换 Jaccard，三处复用 | **✅ 打满**（含无字符重叠的同一事实） | **高**（见 15.4 B 案清单） |
| ③ **写入侧合并** | 近似重述不新增行 | **❌** 同一 Jaccard 天花板；且触碰"数据永不删除"（得先裁决"不新增"还是"覆盖"） | 低（但获益亦低） |

> **重要事实**：`_dedupe`（`retrieve.py:392`）**已经等价于"成组代表"**——它按分数降序、同簇只留最高分那条。
> 因此方向①在"**批内**"这一层**已经是现状**；它唯一的新增量是"把**未进入候选**的兄弟也带出来"（跨话题门槛的联想），
> 而那一层**仍受同一相似度天花板**，所以**不比 A 案更有效**。

### 15.4 推荐案

**A 案（零依赖，推荐先落）**

- **A1 判据统一（核心）**：新增 `memory/similarity.py::same_event(a, b)`，纯函数、单一入口：
  - **对称 Jaccard ≥ 对称阈值**（保住"逐字/近似重复"，现状行为不退化），**或**
  - **非对称覆盖 ≥ 覆盖阈值**（0.7，本库实测夹缝）**且** 绝对重合二元组数 ≥ `min_shared`**且** 双方归一后长度 ≥ `min_len`
    （后两条专挡 $\beta$ 类碎片——"完全包含"不再自动成立）
  - **三处复用、各自阈值**：`_dedupe` / `sediment` 升级；**`supersede` 保持 Jaccard 0.4 不动**
    （误判代价不对称：前两者误判=少给一条内容，后者误判=**作废一条真实事实** ⇒ 准确率优先，最保守）
  - 顺带收敛：`retrieve.topic_match` / `is_related` **已是非对称覆盖思路**（`retrieve.py:138,151`），
    与 A1 同源 ⇒ 抽公共实现，**避免两处分叉**（与当年抽出 `bigrams` 同一理由）
- **A2 成组召回（可选，收益待实测）**：`select_hooks` 去重时**记下簇规模与成员 id** → `MemoryHit.sibling_ids`
  （注入文本仍只一条＝代表）+ 打分新增 `cluster` 分项（"这件事被反复提起 ⇒ 更容易浮上来"，与 P3-R 复习加成同源、**不落库不增表**）。
  **局限照实说**：簇只在**通过话题门槛的候选**里成型，联想范围 = 候选集内。

**B 案（embedding，需拍板；本设计稿不实施）**

| 代价项 | 具体 | 能否规避 |
|---|---|---|
| 依赖 | 本地中文 embedding 模型（百 MB 级）或远程服务 | 本地模型**不违反"离线可用"**，但违反 `scorer.py` docstring 的"**纯本地启发式、零依赖**" |
| 存储 | 向量落库（新列/新表） | **可规避**：327 条 × 33.1 字，**运行时重算代价可忽略** ⇒ 不落库、零 DDL |
| 确定性 | 模型推理的版本/精度差异 | 不可完全规避 ⇒ 单测须 stub，牺牲"确定性可测" |
| 性质 | "是不是同一件事"落在一个**黑箱模型**上 | 与她"生命感"的原则是否相冲，**由用户判** |
| 收益 | 三处同时升级，**打满 15.2 判据**（含本库暂未出现的纯语义样本） | — |

> **推荐**：**先 A 案**（零依赖、可实测、单入口可回退、先把已有天花板抬到"共享关键片段"这一层）；
> **B 案记为"按需启动"**——触发条件是 A 案落地后，真实对话里**出现 A 案捞不回、又被她/用户注意到的"换说法"实例**。
> 理由：本项目一贯"**照实说天花板、按需付代价**"（P3-S 的先例），且当前语料**没有**纯语义样本可作验收靶子。

### 15.5 自查（M1~M8，代码核对式）

| # | 发现 | 依据 | 处置 |
|---|---|---|---|
| M1 | `_dedupe` 用**对称** `content_similarity` | `retrieve.py:401` | A1 升级点①（阈值不变，判据变宽） |
| M2 | `sediment` 聚类用**对称** `content_similarity` 比簇代表 | `sediment.py:142,157` | A1 升级点② |
| M3 | `supersede.find_superseded` 用**对称** `content_similarity` 0.4 | `supersede.py:77` | **推荐不动**（D-A2）——代价不对称 |
| M4 | `path_key` 恒 `"main"`、`emotions` 恒 `0.0` 且无处读 | `heartbeat.py:438-440`、`state_store.py:101-104` | **死位**，方向①不能白借；`emotions` 本次**不动**（避免顺手扩功能） |
| M5 | `topic_match`/`is_related` **已是覆盖/绝对重合**思路 | `retrieve.py:138-169` | A1 顺带收敛为公共实现（防两处分叉） |
| M6 | `MemoryHit` 增 `sibling_ids` 的影响面：`_dedupe` / `retrieve_from_store` / `_feel_recall`（只读 score）/ 观测（只读 DB） | `retrieve.py:383`、`expression_service.py:572` | 影响面小；A2 时清点 |
| M7 | `record_importance` **零运行时调用**（仅 `__init__` 导出 + 1 处单测） | `scorer.py:134`、`memory/__init__.py:85,151`、`test_memory.py:66` | 删除清单见 15.7 步 A0（D-A4） |
| M8 | 判据常量分散三处（0.35 / 0.35 / 0.4） | 见 15.1 表 | A1 落地时**保留各自阈值**（只统一「实现」不统一「阈值」） |

### 15.6 拍板点清单

| # | 决策点 | 选项 | 推荐 |
|---|---|---|---|
| D-A1 | **第二步走哪条路** | ① A 案零依赖（判据升级 + 成组代表）／② B 案 embedding（代价见 15.4）／③ 都不做、记为已知上限 | **A 案**；B 案按需启动 |
| D-A2 | **`supersede` 是否参与判据升级** | 参与（更狠地作废旧事实）／**不参与**（保 Jaccard 0.4） | **不参与**（误判代价不对称） |
| D-A3 | **A2 成组召回是否本次做** | 做（簇规模入打分 + `sibling_ids`）／先不做 | **先不做**，等 A1 真实库前后对照后再定 |
| D-A4 | **堆积小项 `record_importance` 删除** | 同批（独立小提交）／另批 | **同批**（零调用、独立、低风险） |
| D-A5 | （仅若选 B）**embedding 是否引入 / 是否落库向量** | 引入（本地模型）／不引入 | 不引入（除非 D-A1 选 B） |

### 15.7 拆步表

| 步 | 内容 | 对外行为变化 |
|---|---|---|
| **步 0 ✅ 已完成（设计期）** | 只读探针实测（15.1 表）+ 12 对人工判读 ⇒ 本节结论 | 无（脚本跑完即删） |
| **步 A1** | `memory/similarity.py::same_event` + `_dedupe`/`sediment` 改用（`supersede` 不动）+ `topic_match`/`is_related` 收敛共用 + 单测（V1~V3、V5）+ 真实库前后对照 | hooks/候选在"**共享关键片段的换说法**"上变准；top3 可能由"三条生日"变成"1 条代表 + 另 2 件不同的事" |
| **步 A0**（可独立） | 删 `scorer.record_importance` + 导出 + 单测（M7） | 无（零调用） |
| **步 A2**（可选，D-A3 定） | 簇规模 → `MemoryHit.sibling_ids` + `cluster` 打分子项 + 观测列 + 单测 | top3 行数不变；同簇反复提起者更易浮上来 |
| **步 B**（另起设计稿） | embedding 接线（D-A5 定） | 待定 |

### 15.8 涉及文件清单（落地时才动）

| 文件 | A1 | A0 | A2 |
|---|---|---|---|
| `src/elysia/memory/similarity.py`（**新增**） | `same_event` / `coverage` 单一入口 | | |
| `src/elysia/memory/retrieve.py` | `_dedupe` 改用 + `topic_match`/`is_related` 收敛 | | `MemoryHit.sibling_ids` + `cluster` 分项 |
| `src/elysia/memory/sediment.py` | `find_candidate` 聚类改用 | | |
| `src/elysia/memory/supersede.py` | **不动**（D-A2） | | |
| `src/elysia/memory/scorer.py` | | 删 `record_importance` | |
| `src/elysia/memory/__init__.py` | 导出 `same_event` | 删导出 | |
| `src/elysia/tools/memory_view.py` | | | 「簇」列（若 A2） |
| `tests/unit/test_similarity.py`（**新增**） | V1~V3 固定样本 | | |
| `tests/unit/test_retrieve.py` / `test_supersede.py` / `test_memory.py` | 用例对齐 | 删该测 | A2 断言 |
| `docs/P3_MEMORY.md` | 第九节问题 3 补结论；第五节文件地图同步 | | |
| `docs/P3_MEMORY_WORKLOG.md` | 落地记录（第十六节起） | | |
| `项目元信息/开发日志.md`（仓库外） | 同步 | | |

---

## 十六、第十五节落地记录（A1 判据统一 + A0 清理，2026-09-24）

> **状态**：步 A1、步 A0 **已落地并通过门禁**；**步 A2 未做**（D-A3：等本节 16.4 的真实库前后对照再定）。
> **不代为重启灵魂**——本次改动只在下次启动时生效。

### 16.1 拍板记录（按 15.6 "推荐"列执行）

| # | 决策点 | 结果 |
|---|---|---|
| D-A1 | 第二步走哪条路 | **A 案**（零依赖）；B 案 embedding 记为"按需启动" |
| D-A2 | `supersede` 是否参与判据升级 | **不参与**（仍对称 Jaccard 0.4，误判代价不对称） |
| D-A3 | A2 成组召回是否本次做 | **先不做**，等 16.4 前后对照后再定 |
| D-A4 | 堆积小项 `record_importance` 删除 | **同批**（步 A0） |
| D-A5 | embedding 是否引入 / 落库向量 | **不引入** |

### 16.2 落地内容

**步 A1（判据统一，核心）**

| 文件 | 改动 |
|---|---|
| `memory/similarity.py`（**新增**） | `coverage` / `query_coverage` / `same_event` + 三常量（`COVERAGE_THRESHOLD=0.7`、`MIN_SHARED_BIGRAMS=3`、`MIN_SHORTER_BIGRAMS=5`） |
| `memory/retrieve.py` | `topic_match` → `query_coverage`；`_dedupe` → `same_event(jaccard_threshold=HOOK_DUPLICATE_SIMILARITY)`；常量 0.35 **保留**（只统一实现，不统一阈值） |
| `memory/sediment.py` | 两处聚类判定（簇归并 / "已递过"）改用 `same_event`；`SEDIMENT_CLUSTER_SIMILARITY` 语义改为"same_event 的**兼容阈值**" |
| `memory/__init__.py` | 导出 `coverage` / `same_event`（并在模块 docstring 补 `similarity.py` 一行） |
| `memory/supersede.py` | **不动**（D-A2）；仍是 `bigrams` / `content_similarity` 的唯一来源 |

两处实现取舍（落地时定，未写进设计稿）：

1. **护栏用"二元组数"而非"归一后字符长度"**：`_normalize` 是 `supersede` 的私有函数，为不触碰它、也不造成口径分叉，`MIN_SHORTER_BIGRAMS=5` 由已有的 `bigrams` 集合直接推导（等价于归一后 ≈6 字）。实样本核过不会误杀 α 类："那我的生日呢" 5 个、"记得你的生日吗" 6 个、"我今天吃了火锅" 6 个。
2. **`retrieve.is_related` 未迁移（相对 15.8 的一处偏离）**：它是"**绝对重合条数**门槛"（既不是 Jaccard 也不是覆盖，与 A1 不同宗），且实现上必须复用**同一份 query 二元组**做两次比较以免重复计算；其粒度来源本就是 `supersede.bigrams`，**不存在分叉**。故保留原样 → 该函数行为零变化（`test_is_related_*` 全部未改且通过）。

**步 A0（零调用遗留清理）**：删 `scorer.record_importance`（连同仅它使用的 `MemoryRecord` 导入）、删 `memory/__init__.py` 的导出、删 `test_memory.py` 的导入与该用例。

### 16.3 验收对照（15.2 判据）

| # | 判据 | 结果 | 证据 |
|---|---|---|---|
| V1 | α 类 12 对判为同一件事 | **✅** | 新增 `tests/unit/test_similarity.py`：6 对真实正文样本判同 **+ 逐对反证**（`content_similarity < 0.35` 且 `coverage ≥ 0.7` ⇒ 收益不是幻觉） |
| V2 | 不同事实不得被合并 | **✅** | 「我的生日是5月21日」↔「你的生日是11月11日」cov 0.444 → 判**不同**；「你喜欢看晚霞」类 cov 0.0 |
| V3 | β 类碎片护栏生效 | **✅** | 「不对哦」⊂ 长句 cov **1.0** 仍判**不同**；并加**反证**：把 `min_shared` / `min_shorter` 放开，同一对立刻判同 ⇒ 证明"是护栏在挡" |
| V4 | hooks top3 是 3 件不同的事 + 不减少信息量 | **⚠️ 部分达成** | 3 行不变 ✅；**但本库 top3 改前改后完全相同**（详见 16.4）——"含 α 类换说法"这一预期在现库**不可观测** |
| V5 | 判据为单一入口 | **✅（含一处新发现）** | grep：`same_event` 唯一实现在 `similarity.py`；`content_similarity` 只剩 `supersede` 内部 + `similarity` 内部比较 + 单测。**但 `expression_service` 另有两处同宗判据未迁移**（16.5 M9/M10） |

行为断言另落在两处真实消费点：`test_retrieve.py::test_select_hooks_dedupes_reworded_same_event`（hooks 批内去重）、`test_sediment.py::test_pattern_clusters_reworded_same_thing`（沉淀聚类——**该用例改前必然不成立**：换说法的三次提起以前凑不成一簇，压根不会产生候选）。

### 16.4 真实库前后对照（只读探针，跑完即删；330 条 / 经历类 63 条）

**配对层（A1 的收益实物）**：`Jaccard ≥ 0.35` 命中 **23 对** → `same_event` 命中 **33 对**，**新增 10 对**。
逐对人工判读：**10 对全为 α 类**（生日问答 / 叮嘱的同话题重述），**无一例 β 碎片、无一例不同事实**。示例：

- `#28/#46`「可以分别告诉我，你的生日，以及我的生日吗？还记得吗？」↔「记得你的生日吗」cov 0.833 / jac 0.250
- `#16/#28`「你还记得你的生日吗？」↔「可以分别告诉我，你的生日，以及我的生日吗？还记得吗？」cov 0.750 / jac 0.286
- `#34/#105`「那我的生日呢」↔「那我在告诉你哦，我的生日是5月21日，要记好哦」cov 0.800 / jac 0.190

> 顺带修正 15.1 表的一处：表里 `#28` 正文为**节选**（原句以"还记得吗？"结尾），落地时以库中原文为准（cov 0.833，与表中一致）。

**hooks top3（V4 的实况）**：问「我的生日是哪天」「你还记得我的生日吗」「生日」三问，**改前 / 改后 top3 完全相同** = `[105, 81, 73]`；
改前/改后都是 3 行、都是 3 件不同的事。放开名额上限后可见 A1 的确在起作用：`#46 → #81`、`#32 → #50` 被正确并入。
**照实说的结论**：A1 新捞回的 α 对多是**低分的重述/追问**，够不着 3 个名额；高分位本就是三条互不相同的长句。故 15.2 V4 预期的"top3 变成 1 条代表 + 另 2 件"**在本库未发生**——A1 在本库的真实收益是"**该合并的确实合并了**"（配对层 23→33，含沉淀聚类），而非改变 hooks 排位。

### 16.5 新发现（本步**未动**，需拍板）

| # | 发现 | 位置 | 误判代价 | 建议 |
|---|---|---|---|---|
| M9 | `adopt` 的"候选当代表"仍用 `content_similarity`：候选所在簇的兄弟照旧参与竞争 | `expression_service.py:454-459` | 少一条竞争者（**可容忍**，同 `_dedupe` / `sediment`） | 可随 A1 升级为 `same_event` |
| M10 | `adopt` 改口时作废"既有的同一件事自我认知"仍用 `content_similarity` | `expression_service.py:472-476` | **作废一条她认领过的自我认知**（**不可容忍**，同 `supersede`） | 保持保守口径（Jaccard 一档） |

**D-A6**：`adopt` 路径这两处是否随 A1 迁移？推荐 **M9 迁移、M10 不迁移**（同一"误判代价不对称"原则）。
本步未动的原因：`expression_service.py` **不在已评审的 15.8 文件清单内**，且 M10 涉及"作废她的自我认知"，属敏感路径 ⇒ 不擅自扩大范围。

### 16.6 门禁与文档

- `ruff check` / `ruff format --check src tests` / `mypy src`（strict）**全绿**；`pytest` **369 passed**
  （S7 的 346 → +23：新增 `test_similarity.py` 22 项 + 去重/沉淀各 1 项 − 删 `record_importance` 1 项）。
- 文档四处同步：本节 + `docs/P3_MEMORY.md`（第五节文件地图 / 第九节问题 3）+ `CHANGELOG.md` + `项目元信息/开发日志.md`（仓库外）。
- `verify_integrity.py --generate` → `--check`：全一致。
- **提交**：（见 16.8 补记）；已推送 origin main。
- **未代为重启灵魂**。

### 16.7 下一步 + 交接要点

- 主线剩余两项，都需拍板：**D-A3**（A2 成组召回做不做）与 **D-A6**（M9/M10 是否迁移）。
- 重启后可观测（对外行为的唯一变化）：
  1. **沉淀候选更容易出现**——"换说法的同一件事"现在能凑够"≥3 次且跨 ≥1 天"（改前凑不成，候选压根不产生）。这是 A1 在本库最确定的可观测收益。
  2. hooks 注入**条数与内容都不变**（本库实况），因此"她的话"看不出差别——**不要**据此判断 A1 没生效。
- **回退代价极低**：`same_event` 是单一入口，把 `_dedupe` / `sediment` 换回 `content_similarity` 即可复原（单点回滚）。

---

*本文件随记忆打磨期持续追加；每节末尾保留"下一步 + 交接要点"。*