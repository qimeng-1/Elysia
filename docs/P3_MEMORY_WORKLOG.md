# P3 记忆打磨 — 工作日志与交接

> **用途**：记忆打磨期（2026-09-20 起）的滚动工作日志。每天追加一节。
> **接手方式**：新窗口请先读本文件 → `docs/P3_MEMORY_PLAN.md` → `CHANGELOG.md` 的 P3-K/L/M/N → 再动代码。
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

## 二、今天新增的坑与教训

1. **沙箱会杀死 GUI 应用**：`scripts/memory_view.ps1` 在沙箱里启动时，Qt 初始化要读 `C:\ProgramData\NVIDIA Corporation\Drs\nvAppTimestamps` → 被沙箱拒绝 → **进程静默死掉**（脚本已打印 `[ok] memory viewer started`，但窗口几秒后消失，后台任务报 `exit code 1`）。**结论：启动 PySide6 GUI 必须非沙箱运行**（或用 `dangerouslyDisableSandbox`）。
2. **改代码后必须重启进程才生效**：曾出现"文档说已修好、浏览器里却没变化"——真实原因有二：①运行中的灵魂是旧代码（P3-L 的迁移列都没建）②运行中的浏览器加载的是旧打分代码。**灵魂改代码 → `soul.ps1 stop` → `start -Body`；浏览器改代码 → 关窗口重开。**
3. **`pre-commit run --all-files` 只检查 git 已跟踪文件**，新建的**未跟踪**文件会被跳过 → 首次门禁"全绿"但 commit 时才暴露 lint 问题。**新建文件后先 `git add` 再跑门禁。**
4. **PowerShell 5.1 以 ANSI 解码无 BOM 的 `.ps1`** → 中文乱码导致解析失败（`MissingArrayIndexExpression`）。**`.ps1` 脚本一律纯 ASCII**（或确保 UTF-8 BOM）。
5. **GUI 启动失败会静默吞错**（`Start-Process`）→ 脚本现在 2 秒后检查 `HasExited` 并打印日志尾部，否则用户完全无法排查。
6. **路径必须是绝对路径**：用户在 `C:\Users\Lakeside_Fu` 下用 `scripts\memory_view.ps1` 相对路径 → "实际参数不存在"。
7. **`ruff SIM118` 陷阱**：`sqlite3.Row` 迭代的是"值"而非列名，`for k in row.keys()` 不能简化成 `for k in row`（会取错）。需先 `keys = row.keys()` 存变量。
8. **本仓库的忽略规则**：`data/heartbeat.db`、`data/state.db`、`*.db-wal/shm`、`data/{logs,cache,tmp,run}/` 已忽略；但**自造的备份文件名（如 `heartbeat.db.p3n-bak-*`）不匹配任何规则，会出现在 `git status`** → 备份请放到 `data/tmp/`。

---

## 三、当前状态快照（接手即用）

### 3.1 进程与数据

| 项 | 值 |
|---|---|
| 灵魂 PID | 12884（新代码） |
| 身体 PID | 20552 |
| 记忆浏览器 | 18636（垫片）+ 4194/4196（worker），**非沙箱启动** |
| 记忆条数 | 126 条；层级 shallow 75 / working 30 / deep 21 |
| 备份 | `data/tmp/heartbeat.db.p3n-bak-20260921-202058` |

重启命令：
```
powershell -ExecutionPolicy Bypass -File a:\WorkPlace\Elysia\elysia\scripts\soul.ps1 stop
powershell -ExecutionPolicy Bypass -File a:\WorkPlace\Elysia\elysia\scripts\soul.ps1 start -Body
powershell -ExecutionPolicy Bypass -File a:\WorkPlace\Elysia\elysia\scripts\memory_view.ps1
```

### 3.2 记忆系统完整规则（一张表读完）

| 环节 | 规则 | 位置 |
|------|------|------|
| **写入** | 用户输入 → `KIND_INTERACTION`，她开口 → `KIND_EXPRESSION`，均以 `LEVEL_SHALLOW` 落库，`narrative = content` | `soul/heartbeat.py`、`soul/expression_service.py` |
| **重要性** | `类型基础 + 内容信号×0.60 + 情感×0.15 + 用户相关 0.05`（内容主导） | `memory/scorer.py::importance` |
| **晋升** | 浅层→工作：`imp≥0.4` **或** `access≥2`；工作→深层：`imp≥0.7`；每晋升一层细节度 ×0.5（protected 不模糊） | `memory/promote.py`、`memory/levels.py` |
| **维护节律** | 心跳每 300 拍（约 5 分钟）= 晋升 + 建索引 + 索引衰减 | `soul/heartbeat.py::_maintain_memories` |
| **索引衰减（遗忘）** | `strength × e^(−有效年龄/45天)`；protected 慢 3×；floor=0.2（数据永不删，只是索引弱） | `memory/decay.py` |
| **情绪染色** | 记忆情感向量 vs 当下感受的**余弦**（只比方向） | `memory/retrieve.py::mood_similarity` |
| **打分** | `层级×0.6 + 情绪×0.4 + 索引×0.3 + 重要度×0.5 + 新鲜度`；`MAX_HOOKS=3` | `memory/retrieve.py::score_breakdown` |
| **新鲜度** | 近 7 天 `+0.55`，之后线性衰减到 0 | `memory/retrieve.py::_recency_factor` |
| **回声排除** | 检索排除 `KIND_EXPRESSION`（不复述自己刚说的） | `memory/retrieve.py::select_hooks` |
| **取代** | 同话题（字符二元组 Jaccard ≥0.4）的新事实取代旧事实；被取代者不召回、不晋升、不建索引 | `memory/supersede.py`、`state_store.mark_superseded` |
| **复习** | 命中 → `touch_memory`（access_count+1、更新 last_access_ts）——**目前只影响"浅层→工作"晋升，不进得分** | `state_store.touch_memory` |

### 3.3 关键文件地图

```
src/elysia/memory/
├── levels.py        MemoryRecord 数据模型 + 三层/晋升常量 + superseded_by 字段
├── scorer.py        importance() 内容信号评估（今天重写）
├── promote.py       晋升决策 + 细节模糊化（纯函数）
├── decay.py         索引衰减纯函数（τ=45天 / protected 慢3× / floor 0.2）
├── retrieve.py     打分 + 选 hooks + retrieve_from_store（今天改打分）
└── supersede.py     同话题取代判定（字符二元组 Jaccard）
src/elysia/soul/
├── heartbeat.py            心跳循环：写入记忆 + _supersede_conflicts + _maintain_memories
└── expression_service.py   表达链：检索 → 注入 memory_hooks → LLM → TTS → 写记忆
src/elysia/core/state_store.py   HeartbeatStore：add_memory / touch_memory / mark_superseded
                                 / add_memory_index / iterate_memory_index + 幂等迁移
src/elysia/tools/memory_view.py  记忆浏览器（只读观测）
scripts/{soul,memory_view}.ps1   进程控制（纯 ASCII，勿加中文）
```

---

## 四、下一步：②时间锚点 ③复习加强 ④联想网络

> 五维度打磨清单：**①记得住 ✅ ②想得起来 ③准确率 ✅ ④记忆浏览器 ✅**（浏览器已于今天完成）
> 剩下的三项按此顺序做——②是③④的观测前提（hooks 里能看到时间和来源才好验证）。

### ② 时间锚点（hooks 带时间标签）

- **缺口**：`default_narrative()` 只返回 `narrative` 或 `content`，注入给 LLM 的 hooks 是"她生日是 5月21日"这种**无时间信息**的事实。她因此说不出"你上个月告诉我的"，也无法判断新旧、无法自然表达"好久没提这件事了"。
- **建议**：给 `MemoryHit` 加 `age_days` 字段；`select_hooks` 里用 `created_ts` 与 `now` 算出；注入 `memory_hooks` 时改成带锚点的形式（如 `（3 天前）她生日是 5月21日`）。相对时间文案建议 `刚刚 / 今天 / 昨天 / N天前 / 上个月 / 去年`，避免精确到分钟（那不像人）。
- **涉及**：`memory/retrieve.py`（MemoryHit + select_hooks）、`soul/expression_service.py`（payload 注入处）。
- **验收**：①探针/浏览器能看到 hooks 带时间 ②对话里出现"你上次说的…"这类自然表达。
- **注意**：给 LLM 的描述里已有 `day_phase`，确认"现在是什么时候"的参照齐备，否则"3 天前"没有基准。

### ③ 复习加强（检索反哺重要度）

- **缺口**：`touch_memory` 只递增 `access_count`，而 access 仅参与"浅层→工作"这一级晋升；**被反复想起不会让记忆变得更重要，也不影响得分**（实测 `access=22` 的记忆与 `access=0` 的记忆打分无差别）。
- **建议**：命中时小幅提升重要度（如 `importance += 0.02`，封顶 1.0），或新增"复习加成"分项（如 `REVIEW_COEF × log(1+access)`，按 `last_access_ts` 衰减，避免"越被想起越容易被想起"的正反馈失控）。
- **涉及**：`state_store.touch_memory`（扩展为可回写 importance）、`memory/retrieve.py::retrieve_from_store`（命中循环）。
- **验收**：连续两次召回同一条 → 第三次它的重要度/得分确实更高；单测覆盖"复习 → 分升"与"衰减后回落"。

### ④ 联想网络（成组召回 / 同话题去重）

- **缺口**：`select_hooks` 只按分数取 top3 且**不去重** → 同一次对话的碎片会占满 3 个名额（今天实测 #52/#48/#50 三连），等于 3 个 hook 只传递了 1 条信息。
- **建议**（两步走）：
  1. **批内去重**（便宜、马上见效）：召回后用 `supersede.content_similarity`（字符二元组 Jaccard）算批内相似度，超阈值（约 0.3~0.4）只保留分数最高的一条。
  2. **成组召回**（真正的联想）：以命中的记忆为中心，借 `memory_index`（可扩展 `path_key`）或共同情绪维度把相关记忆成组——"一起想起一整件事"而不是碎片。
- **涉及**：`memory/retrieve.py::select_hooks`（先做 1）、`memory_index`（成组）、必要时新增 `memory/associate.py`。
- **验收**：top3 是 3 件**不同**的事；探针可复现。

### 遗留小项

- `docs/PERSONA.md` 已被 git 跟踪（曾随某次提交进入仓库）。是否改为本地保留（`git rm --cached`）**待用户决定**，不要擅自处理。

---

## 五、验证与观测速查

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

*本文件随记忆打磨期持续追加；每节末尾保留"下一步 + 交接要点"。*