# S1 细则：让"对话"成为她的一部分（2026-10-20 01:5x 签发，用户已批准 S0–S5）

> 依据：`docs/LEAD_MEMORY_IDENTITY_DESIGN_2026-10-20.md`（用户拍板 A/B/C/D）+ 2026-10-20 01:2x 的机制更正。
> **S0 已完成**（`1c49369`）。本细则只覆盖 **S1**，分三个 commit 交付：**S1a → S1b → S1c**，一次一个，各自过门禁。

---

## 0. 要解决的问题（有真机证据，别修错东西）

1. **她没有对话上下文**：指令里只有 `user_message`（最后一条）。真机当场抓到的证据——你说「一定」，她回：
   > 「一定」这两个字，从前面的对话里掉下来，我却不知道它在指向什么。
2. **你的话被逐条存成"记忆"，再当钩子回喂**：真库 `id=1000541「那你想听什么呢」`、`id=1000534「今天有点想你，你还好吗？」` 都是 `kind=interaction / source=user`、50–62 分钟前，却被标成「（刚刚）」并作为 `memory_hooks` 塞回她的 prompt ⇒ **延迟回声**（她反复重新打招呼/回应早已聊过的话）。
3. **开场主题单一**：`state_brief.day_phase = night` 直接进 prompt，她 90 分钟内 20+ 条以「夜深了/夜里」开场。

---

## S1a（第一 commit）对话上下文

### 数据
- `heartbeat.db` 新增表 `dialogue_turns`：
  | 列 | 类型 | 说明 |
  |---|---|---|
  | `id` | INTEGER PK | |
  | `ts` | REAL | 发生时刻 |
  | `role` | TEXT | `'user'` / `'her'` |
  | `text` | TEXT | 原话（**不裁剪历史**，上下文读取时才截断） |
  | `exchange_id` | INTEGER | 一次"交换"共用同号 |
  | `emotion_vector` | TEXT | 她这侧当时的感受（JSON，user 侧为 NULL） |
  | `intent` | TEXT | 她这侧的意图（user 侧为 NULL） |
- **交换边界**（确定性、可测）：**每来一条新的用户消息 ⇒ `exchange_id += 1`**；在那之前她说的所有气泡都归同一 `exchange_id`（包括多句拆分的每条气泡）。
- 建表走既有的 schema 初始化路径（`CREATE TABLE IF NOT EXISTS`，旧库升级不丢数据）。**不许手改 `data/*.db`**。

### 写入
- 用户消息被心跳拍到（现在走 `kv['interaction']`）时：**同一次 persist 里**写一条 `role='user'` 的 turn，并分配新 `exchange_id`。
- 她的每条气泡落库时（现在写 `thought_log` 的地方）：写一条 `role='her'` 的 turn，带上当次 `emotion_vector` + `intent`，`exchange_id` 用当前值。
- 写失败/表不存在 ⇒ **不得影响她说话**（只能记日志，主流程照旧）。

### 注入
- 表达指令新增字段 **`dialogue_context`**：最近 **20** 条 turn（按时间升序），每条 `{"role": ..., "text": ..., "age_s": ...}`。
  - 单条 `text` 截断 **200 字**；整段总长 ≤ **2000 字**（超了从最旧的开始丢）。
- `deepseek.py` 的 user 段把 `dialogue_context` 带给模型；**system 段的表达层人设加一段明确指令**：
  > 「`dialogue_context` 是你们最近的真实对话（按时间顺序）。回应必须**接得上**它：指代词（它/那个/刚才那句）要指向其中的内容；不要把它原样复述，也不要把已经聊过的话当成新话题重新打招呼。」
- **不进 system 段的数据**：不用 `dialogue_context` 替代 `memory_hooks`；两者并存，语义不同（近期对话 vs 撞上的旧记忆）。

### 验收（全部要有用例）
1. 三轮交换后，payload 的 `dialogue_context` 有 6 条、顺序正确、role 正确、`age_s` 递增。
2. 新用户消息进来时，她那次的 payload 里**必然**包含这条用户消息（不是上一条）。
3. 超过 20 条 / 超过 2000 字 ⇒ 从最旧丢，最近一条永远在。
4. **指代能接上**（用 `FakeBackend` 抓 payload）：连续「我去改代码了」→「改完了」两轮后，payload 里两轮都在；断言第二个 payload 包含第一轮文本（这就是「一定」接不上的反面）。
5. 交换边界：一条用户消息 + 她三句气泡 ⇒ 全部同 `exchange_id`；下一条用户消息 ⇒ 新 id。
6. **取消测试**：把 `dialogue_context` 从 payload 装配里去掉 ⇒ 用例 2/4 必须红，并贴出失败的 payload 片段。

---

## S1b（第二 commit）记忆卫生：别再拿"刚才的对话"当记忆

1. **② 最近交互不进 hooks**（`memory/retrieve.py::select_hooks`）：
   `kind == 'interaction'` 且 `now - created_ts < DIALOGUE_RECALL_GUARD_S` 且 `not protected` ⇒ 跳过。
   - `DIALOGUE_RECALL_GUARD_S = 900`（常量 + 可配，不写死在分支里）。
   - **必须与 S1a 同批上线**（单独上会抽走她仅有的短期上下文）——你按顺序做，我在两次验收之间不停放行。
2. **③ 记忆按"一次交换一条"写**：停止把用户每条消息逐条镜像成 `memories(kind='interaction')`；改为在一次交换结束时写**一条** `kind='exchange'` 记忆：
   - 内容形如：`你："<原话截断120字>"／我（当时 miss=0.33）："<她的回复截断120字>"`；
   - `source='user'`、`importance` 由该交换的情绪强度给出（沿用既有 `scorer.importance` 口径）；
   - **历史 130 条 interaction 一条都不动**（保留可追溯），只停止产生新碎片（例如单独一条「一定」）。
   - 交换"结束"的判定要确定性、可测：**下一条用户消息到来时封存上一次交换**（与 S1a 的边界同源）；进程重启时把未封存的那次直接封存。
3. **④ `age_phrase` 档位修正**（`memory/retrieve.py::age_phrase`）：≤10 分钟「刚刚」／≤2 小时「不久前」／当天「今天」／其余照旧。**纯展示层，不改检索得分。**
4. **取消测试**：去掉 ② ⇒ "50 分钟前的交互被钩回"用例红；去掉 exchange 写入 ⇒ "一次交换只产生一条记忆、且不再产生逐条碎片"用例红；改回旧档位 ⇒ 标签用例红。

---

## S1c（第三 commit）开场去重：别再句句「夜深了」

- 表达指令新增 **`recent_openings`**：她最近 **8** 条气泡的**开头片段**（每条取前 6 字），按时间升序。
- system 段加指令：
  > 「`recent_openings` 是你最近几次开口的开头。这次**不要**再用相同或高度相似的开头（尤其是"夜深了/夜里/夜色"这类时间开场）。换一个进入方式：直接说事、说你在做什么、或问一个具体的。」
- `state_brief.day_phase` **保留**（时间意识是合理的），但人设里补一句：时间只能作为**背景**，不要拿它当开场白。
- 验收：
  1. payload 有 `recent_openings`，条数与顺序正确，超 8 条只留最近 8 条；
  2. system 段文本包含上述两条指令（用 `FakeBackend` 抓 system 内容断言）；
  3. **真机只读取证**：取她最近 20 条气泡，统计"以夜深/夜里/夜色开头"的比例（现在是 20/24 级别），把这个基线数字写进交接单，供次日对比。
- 取消测试：去掉 `recent_openings` ⇒ 用例 1 红；去掉 system 指令 ⇒ 用例 2 红。

---

## 通用约束

- 写作用域：`src/elysia/**`（`core/` schema、`soul/`、`llm/`、`memory/`）+ `tests/**`；**不碰** `body/pet.py`、`assets/pet/**`、`scripts/**`（除你自己不需要动）。
- 门禁：**用 `scripts\gate.ps1`**（T-013 已交付；它会拒 `-q`/`uv run pytest`）。基线：**739 passed + 13 skipped**，不得回归。
- 每个 commit 单独报：hash、`gate.ps1` 原文摘要、取消测试红/绿原文、以及"依据的 文件:行"。
- 不重启她、不 push、不加第三方依赖、不手改 `data/*.db`。
- 交付完 **S1c 后停手**等我验收再放 S2（她的"这条我想留着"，上限 13 次/天，常量可配）。
