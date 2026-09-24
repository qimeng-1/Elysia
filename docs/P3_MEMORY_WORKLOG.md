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

*本文件随记忆打磨期持续追加；每节末尾保留"下一步 + 交接要点"。*