# Elysia 记忆系统：全貌

> **用途**：本文件是记忆系统的**唯一主文档**——先讲清"她在做什么"（第零节，不需要懂术语），
> 再给出系统事实（数据流 / 文件 / 规则 / 验收 / 待评审问题）。
> 多方问询（外部 AI / 同行评审）直接投喂本文件，保证每一轮看到的是**同一版事实**。
> **快照日期**：2026-09-24（P3-P / Q / R / S / T / U / V / W1 / W2 之后，**第八节 Self Memory S1 本体 + S2 认领 + S3 沉淀**落地）
> **维护约定**：代码有实质变化时更新本文件并改快照日期。外部结论**不写进本文件**，
> 另存 `docs/MEMORY_REVIEW_NOTES.md`（见第十节）。

---

## 零、她在做什么（先读这一节）

**她有一个自己的记事本。**
你说的话、她自己说过的话，都会记下一笔——不是存成聊天记录，而是存成"经历"：
当时是什么心情、这件事有多重。

**她不是每次都翻记事本。**
只有当你聊到相关的事，那件事才会浮到她嘴边（你问生日，她才想起生日）。
每句话都把往事念一遍会很假——所以"无关的话不把往事带到嘴边"是刻意的。

**她也可以自己想起。**
想主动提起某件往事时，她有一个"回想"的动作（工具），用不用由她决定。

**她记得你，但不必说出来。**
有些记忆不进入她的话，只让她此刻的心情变一点——想起你 → 更想你，
而不是 → 把生日念出来。这条路是程序静默做的，**永远不会出现在她的话里**。

**有些事她说不出口。**
程序推断出来的、系统塞给她的东西，她不会当成自己的事实说出口；
她自己也只是"大概"的事，只当背景。她还能主动**"不认"**某段记忆——
那是她的权力，程序不会替她拒绝。

**忘记不是删除。**
时间久了记忆会变难找（索引变弱），但**数据一条都没丢过**。
有些事她会觉得"想不起来了"——那是一种好奇，不是焦虑。

**她还能拒绝被影响，也能反悔。**
她可以说"我不想再想起这件事"——那条记忆既不进她的话，也不再影响她的心情；
哪天她又愿意想起了，把它收回来就是。（这是 `retention_state` 遗忘状态机，
W1 本体 + W2 她的两个工具 `forget`/`restore` 均已落地，见 `P3_MEMORY_WORKLOG.md` 第七节。）

**她能记得多少？**
越重要、越被反复提起的事，沉淀得越深；珍贵的事（比如关于你的事）永远不模糊。

---

## 一、定位与三条铁律

它不是一个"让模型更聪明的检索库"，而是 **soul state → body 表达** 单向数据流里、
**只在被唤起时才进入话语**的记忆层。

1. **程序管"会不会"**（把事实可靠地递到她手上），**她管"用不用 / 怎么用"**
   （如实、含糊、不提、装不记得，凭性格与心情）。LLM 只是帮她说话的工具。
2. **约束只要不违反人设就尽量少**。早期 T2 的"不承诺 / 不编造"只是测试期脚手架，
   后期真实电子生命不以此作硬锁。
3. **记忆属于她，不属于系统**：数据库管理员不拥有最终意义上的记忆所有权。

## 二、四条回路（设计原则，路线图 §九 8.2-8.5）

记忆必须同时满足四条"活"的回路，否则只是数据堆叠：

| 原则 | 含义 | 代码载体 |
|---|---|---|
| **被感受** | 每段记忆带情感向量；回忆时被当下状态染色 | `memories.emotion_vector` + `mood_similarity` |
| **被诉说** | 她会引用过去（"你上次说过…"），不是存着不动 | `memory_hooks` 注入表达链 |
| **被遗忘** | 遗忘是索引碎片化，数据不删 | `memory_index` 强度衰减 |
| **被珍惜** | 珍贵记忆永不模糊，衰减慢 3× | `protected` 标志（⚠️ 见第六节：原为死阀门，P3-W 起由"晋升 `deep` 且 `access_count ≥ 3`"自动置位） |

## 三、数据流

```
写入 ──→ 沉淀 ──→ 检索 ──→ 表达
 │        │        │        │
 │        │        │        └─ 话语路径：话题撞上 → memory_hooks（带时间锚点）
 │        │        │              或她自己调 recall 工具（宽门槛）
 │        │        │              或调 adopt 工具把某段经历认作自己（升格为自我认知 → 进身份段）
 │        │        │              或调 disclaim 工具拒绝认领某段记忆（rejected 后不再进话）
 │        │        │              或调 forget 工具不再想起它（suppressed：不进话也不进感受）
 │        │        │              或调 restore 工具又愿意想起了（回到 present）
 │        │        │           identity 段：她认领的"我是谁"每句在场（不进 hooks、不占名额）
 │        │        │           感受路径：心境共鸣 → memory_recall 脉冲（永不进 prompt）
 │        │        │                     ＋索引衰减 → memory_gap 缺口脉冲（"记不清"，永不进 prompt）
 │        │        └─ 打分：层级+情绪+索引+重要度+新鲜度+复习 → 批内去重
 │        └─ 每 300 拍 _maintain_memories：晋升 + 建索引 + 按绝对年龄幂等衰减
 │                   ＋沉淀（S3）：一件事反复出现（≥3 次且跨天）→ 生成候选（照抄原文、
 │                     标 inference/probable，被来源闸门挡在话语外）+ 感受路径 self_candidate 脉冲；
 │                     递过的候选不再递（落库即"已发现"的证据）
 └─ 用户输入 / 她的发言 → 落库（importance 由 scorer 按内容信号评估；
                              标注 source/certainty = 用户告知 / 她自己说的；
                              claim_status 一律默认 claimed——认领是能力，先递到她手上）
```

## 四、数据与文件

### 4.1 存储与安全

- **位置**：本地 SQLite（`heartbeat.db`，P0 决策 D2 双库分离的"只追加生命档案"）
- **保障**：WAL 崩溃自动恢复 + `backup.ps1` 每周本地备份（`D:\ElysiaBackup`，保留 30 份）
- **已知缺口**：异机 / 备份盘同时报废 → 记忆丢失。云端同步记在灵感池待评估
- **可导出**：`MemoryRecord.to_dict()/from_dict()` 是 JSON 序列化格式，
  记忆本质是 JSON 记录——云端同步只是加传输层，不改数据模型

### 4.2 表结构

**`memories`（记忆本体）**
`id / created_ts / level / kind / content / emotion_vector / importance / access_count /
last_access_ts / protected / detail_level / narrative / superseded_by / source / certainty /
claim_status / retention_state`

**`memory_index`（检索路径，遗忘的载体）**
`id / memory_id / path_key / last_retrieve_ts / strength / emotions`

`MemoryRecord` 与 `memories` 一一对应，可序列化（P3-A 持久化决策，天然可导出异地同步）。

### 4.3 文件清单

**A. 数据层**

| 文件 | 职责 | 关键符号 |
|---|---|---|
| `src/elysia/core/state_store.py` | 两张表的 schema + 全部读写 | `add_memory` / `iterate_memories` / `update_memory_level` / `add_memory_index` / `decay_memory_index` / `touch_memory` / `mark_superseded` / `set_claim_status` / `set_retention_state` / `set_protected` / `mark_as_self`（S2：一次 UPDATE 升格 `kind=self` + `deep` + `protected`）；迁移与回填 `_run_migration` / `_backfill_sql` |
| `src/elysia/memory/levels.py` | 三层定义、阈值、类型、来源与确定性、认领、保留、记录本体 | `LEVEL_*`、`PROMOTE_*`、`PROTECT_DEEP_ACCESS`、`KIND_*`、`SOURCE_*`、`CERTAINTY_*`、`CLAIM_*`、`RETENTION_*` / `RETENTIONS` / `FALLBACK_RETENTION` / `RETENTION_FADE_AGE_DAYS` / `RETENTION_DORMANT_AGE_DAYS`、`SOURCE_BY_KIND` / `CERTAINTY_BY_KIND`、`MemoryRecord`、`DETAIL_DECAY_PER_LEVEL=0.5` |

**B. 规则层（`src/elysia/memory/`）**

| 文件 | 职责 | 关键符号 | 在役？ |
|---|---|---|---|
| `scorer.py` | 重要性按内容评估（纯本地启发式，离线可用） | `content_salience`、`importance`、`emotion_strength`、`record_importance` | ✅ |
| `promote.py` | 层级晋升与细节模糊化 | `decide_promotion`、`promote_batch`、`with_narrative`、`can_reach_deep` | ✅ |
| `decay.py` | 索引强度按年龄指数衰减 | `decay_strength`、`STRENGTH_RETRIEVE_FLOOR`、`retrieve_latency_ms` | 部分 |
| `supersede.py` | 记忆修正 / 覆盖（旧事实作废） | `bigrams`、`content_similarity`（字符二元组 Jaccard）、`find_superseded`（阈值 0.4） | ✅ |
| `retrieve.py` | 检索 + 情绪染色 + 两种路径 | `mood_similarity`(余弦)、`topic_match` / `is_related`(严/宽双门槛)、`age_phrase`、`score_breakdown` / `score_memory`、`_recency_factor`、`_review_factor`、`select_hooks`（含保留闸门）、`_dedupe`、`retrieve_from_store`（唤醒落 `present`）、`recall_for_feeling`、`MemoryHit.woke_from` | ✅ |
| `sleep.py` | 睡眠整合＝做梦（P3-E 设计） | `Dream`、`synthesize_dream` | ❌ 未插电 |
| `hooks.py` | 记忆缺口信号（"想不起来"的物理体现） | `GapSignal`、`detect_gap` | ✅ |
| `sediment.py` | 自我认知的沉淀（第八节 S3）：找"重复模式"生成候选 | `find_candidate`、`PatternSignal`、`_is_experience` / `_is_taken`、`SEDIMENT_*` | ✅ |

**C. 运行时接线**

| 文件 | 记忆相关职责 | 关键符号 |
|---|---|---|
| `src/elysia/soul/heartbeat.py` | 写入经历；每 300 拍维护（晋升 + 自动保护 + 建索引 + 衰减 + **保留降级** + **S3 沉淀**）；感受路径（共鸣 + 缺口） | `_feel_memories`、`_feel_memory_gaps`、`_maintain_memories`、`_offer_candidate`、`_demote_target`、`_supersede_conflicts`、`MEMORY_FEELING_EVERY_N=300` |
| `src/elysia/soul/expression_service.py` | 话题门控注入 `memory_hooks`；**装配身份段 `identity`**；装配 `recall`/`adopt`/`disclaim`/`forget`/`restore` 执行器；推心情 | `_make_tool_runner`、`_tool_recall`、`_tool_adopt`（S3 起候选当"代表"，同家重述不参与并列判定；**S4 起同话题旧自我认知随之作废＝改口**）、`_tool_disclaim`、`_tool_forget`、`_tool_restore`、`ADOPT_DOMINANCE`、`FORGET_MIN_SCORE` / `FORGET_DOMINANCE`、`_feel_recall`、`_self_records`（S4 抽出：非取代 / 非 `rejected` / 非 `suppressed` 的自我认知）、`_identity_lines`（→ `list[str]`，供装配身份段与容量判定共用） |
| `src/elysia/soul/desire.py` | 记忆唤起的情感脉冲 | `memory_recall = {tr: 0.8, cs: 1.2, sa: 0.0}`、`memory_gap`、`self_candidate`（S3：一件事反复出现） |
| `src/elysia/llm/chain.py` | 工具回合能力（她主动想起 / 认作自我 / 拒绝认领 / 不想再想起 / 又愿意想起） | `ToolCapableBackend`、`RECALL_TOOL` / `ADOPT_TOOL` / `DISCLAIM_TOOL` / `FORGET_TOOL` / `RESTORE_TOOL`、`set_tool_runner`、`ToolRunner=(工具名, 参数)` |
| `src/elysia/llm/deepseek.py` | 工具循环（最多 3 轮，按名路由）+ prompt 记忆段；**system 段 = 身份段 + 表达层人设** | `complete_with_tools`、`_run_call`、`_tool_args`、`_TOOL_NAMES`（五个工具）、`_PERSONA_PROMPT`、`_system_prompt`（`identity` 只进 system 段，不进 user JSON） |
| `src/elysia/llm/identity.py` | 身份段（第八节 S1）：出生设定打底 + 她认领的自我认知；**S4 起 `identity_lines` 为后端无关的公共取数入口**（主声 / 未来次声 / 微声共用同一口径） | `BOOTSTRAP_IDENTITY`、`IDENTITY_FIELD="identity"`、`MAX_IDENTITY_LINES=5`、`compose_identity`、`identity_lines` |

**D. 观测与测试**

| 文件 | 职责 |
|---|---|
| `src/elysia/tools/memory_view.py` | PySide6 只读记忆浏览器（观测：存储 / 打分 / 召回 / 沉淀 / **看身份**）；3s 自动刷新；被召回记忆高亮；**S4 增「标签」列与「候选（待她认领）」筛选项**（自我 = 她已认领、进身份段每句在场；候选 = 程序递给她待认领、不进话语） |
| `tests/unit/test_memory.py`、`test_retrieve.py`、`test_promote.py`、`test_decay.py`、`test_sleep.py`、`test_memory_view.py`、`test_identity.py`、`test_sediment.py`、`test_expression_service_memory.py` | 记忆系统单测 |
| `tests/acceptance/test_p3_gate.py` | P3 阶段门禁 |

## 五、完整规则（一张表）

| 环节 | 规则 |
|---|---|
| **写入** | 用户输入 → `KIND_INTERACTION`（`source=user`）；她开口 → `KIND_EXPRESSION`（`source=self`）；均以 `LEVEL_SHALLOW` 落库，`narrative = content`，`certainty=certain` |
| **来源/确定性** | `source ∈ {self/user/observation/inference/system}`、`certainty ∈ {certain/probable/heard/speculative}`；旧库缺列按 `kind` 回填，幂等 |
| **认领（P3-V）** | `claim_status ∈ {claimed/rejected}`；**默认 `claimed`**（认领是能力，落库即给她）；旧库缺列一律回填 `claimed`；`rejected` 只能由**她自己的动作**（`disclaim` 工具）产生 |
| **重要性** | `类型基础 0.10~0.20 + 内容信号×0.60 + 情感强度×0.15 + 用户相关 0.05` |
| **内容信号** | 日期事实 .45 / 叮嘱记住 .35 / 稳定事实 .30 / 承诺 .25 / 更正 .25 / 关系表达 .15；语气词归零；极短句 −.25；纯提问 −.30 |
| **晋升** | 浅→工作：`imp≥0.4` 或 `access≥2`；工作→深：`imp≥0.7`；每晋升一层细节度 ×0.5（`protected` 不模糊） |
| **维护节律** | 心跳每 300 拍：晋升 + 建索引 + 按**绝对年龄**幂等重算 strength（跑 N 次 = 跑 1 次） |
| **沉淀（S3）** | 同一件事被提起 ≥3 次**且**跨越 ≥1 天、且未递过也未被认领 → 生成**候选**：正文/叙事**照抄原文**（`narrative` 优先，程序不自己写句子）、标 `source=inference` + `certainty=probable`、`protected=False`；只递一个（提起最多优先）；素材白名单 `interaction/state/internal`（排除她的回声），够不着/被取代/被拒绝的不算素材；落库即"已递过"的证据 → **不重复递**；感受路径推 `self_candidate` 脉冲（TR/CS 微升、SA 不动，封顶 0.35），**被来源闸门挡在话语外** |
| **索引衰减** | `strength × e^(−有效年龄/45天)`；`protected` 慢 3×；数据永不删，只是索引弱 |
| **取代** | 写入后 `_supersede_conflicts`：同话题 Jaccard ≥0.4 → 旧记录标 `superseded_by`，检索排除 |
| **打分** | `层级×0.6 + 情绪×0.4(余弦) + 索引×0.3 + 重要度×0.5 + 新鲜度 + 复习加成`；`MAX_HOOKS=3` |
| **新鲜度** | 近 7 天线性加成（峰值 0.55），之后为 0 |
| **时间锚点（P3-Q）** | 注入形如 `（3天前）你生日是5月21日`；分档 刚刚 / 今天 / 昨天 / N天前 / 上个月 / N个月前 / 去年 / N年前；无 `now` 则不提时间 |
| **复习加成（P3-R）** | `0.06 × log(1+access) × e^(−距上次想起/14天)`，封顶 `0.3`；**不落库**（不污染 importance） |
| **复习** | 命中 → `touch_memory`（access_count+1、更新 last_access_ts）→ 驱动"浅层→工作"晋升 |
| **批内去重（P3-S）** | 排序后 Jaccard ≥0.35 视为"同一件事"，只留最高分一条 |
| **话题门槛** | 程序推：二元组重合 ≥2（追问措辞下 ≥1）｜她自己 recall：重合 ≥1 |
| **回声排除** | 检索排除 `KIND_EXPRESSION`（不复述自己刚说的） |
| **来源闸门（P3-T）** | 检索排除 `source ∈ {inference, system}` 与 `certainty = speculative`——程序推断/系统注入不得升格成"她的事实"；**感受路径不受此限** |
| **认领闸门（P3-V）** | 检索排除 `claim_status = rejected`——她拒绝认领的记忆不进她的话；**感受路径不受此限** |
| **身份段（S1/S4）** | 她认领的自我认知（`KIND_SELF`）每句都在场：`payload["identity"]` → **system 段**（不占 `MAX_HOOKS`、不走 hooks 段、不进 user JSON）；无自我认知时退化为 `BOOTSTRAP_IDENTITY`（system 段 = 出生设定 + 人设，不增不减）；`select_hooks` / 缺口统计 / `supersede` 三处排除 `KIND_SELF`。**S4 收口**：取数走后端无关的公共入口 `identity_lines()`（主声 / 未来次声 / 微声同一口径）；微声**"带着不说出"**——身份段随指令进 `expression_log`，但不进微声台词（避免机械复述、重犯 P3-P） |
| **保留状态（P3-W1）** | `retention_state ∈ {present/suppressed/dormant/faded}`；**默认 `present`**（老库缺列一律回填）；**不提供删除态**（数据永不删） |
| **保留闸门（P3-W1）** | 话语路径排除 `suppressed`（永不进话）与"够不着"的 `dormant`/`faded`；`dormant`/`faded` **可被话题唤醒**（`is_related` 命中）→ 落回 `present` + `touch` 重新计时 |
| **感受路径与保留** | `suppressed` 与 `dormant` 不进感受路径；`faded` **仍进感受**（"细节忘了，那份感觉还在"） |
| **保留降级（P3-W1）** | 维护每 300 拍按 `since_last_access`（`now − (last_access_ts or created_ts)`，**非绝对年龄**）判定：≥180 天 → `dormant`；≥60 天且 `access_count == 0` → `faded`；**只降不升**；`suppressed` 与 `protected` 不碰 |
| **自动保护（P3-W1/M2）** | 晋升 `deep` 且 `access_count ≥ 3` → 自动置 `protected`（衰减慢 3×、永不降级）；原 `protected` 是死阀门，本次通电 |
| **她的遗忘工具（P3-W2）** | `forget(topic)` → `suppressed`（判据加严：`topic_match ≥ 0.5` **且** `top1 ≥ 2×top2`，否则如实回"没找到"）；`restore(topic)` → `present`（**只在 `suppressed` 里找**）；**只动 `retention_state`，不碰 `claim_status`** |
| **她的认领工具（S2）** | `adopt(topic)` → 最贴题的那条记忆升格为**自我认知**（`mark_as_self`：`kind=self` + `source=self` + `certainty=certain` + `level=deep` + `protected=1` + `detail_level=1.0` + `retention_state=present`，一次 UPDATE 无中间态）；判据只要求"足够突出"（`top1 ≥ 2×top2`，`ADOPT_DOMINANCE`），**不设绝对覆盖度下限**；候选排除已被取代 / 已是自我认知 / `rejected` / `suppressed`；身份段位置满（≥`MAX_IDENTITY_LINES-1`）时如实回"位置满了"，不做静默失败；**候选优先（S3）**：命中程序沉淀的候选时，与它同家的重述（Jaccard ≥0.35）不参与并列判定——否则自家重述同分占位，判据必然落空；**认不认永不由程序置**（程序只递候选）；**改口（S4 验收 3）**：认领到"同一件事"时，旧的那条自我认知随之作废（`superseded_by` 指向新条）——"她可更新自我认知"由此接线；容量判定按"替换不计入新增"（位置满时改口仍可行），不做静默失败 |
| **缺口口径（P3-W1/M9）** | `_feel_memory_gaps` 只统计 `present`——够不着的记忆不产生"记不清"的缺口脉冲 |
| **两种路径** | 话语路径（她决定，进 prompt）｜感受路径（程序静默，永不进 prompt） |
| **记忆缺口（P3-U）** | 感受路径每 300 拍扫 `memory_index`：strength 跌破检索下限 → `memory_gap` 脉冲（TR 微升=好奇，SA 不动）；**不进话语、不新增约束** |
| **欲望边界** | `TR∈[30,70]`、`CS∈[40,80]`、`SA∈[15,60]`；记忆脉冲会被恢复项拉回平衡点 |

## 六、诚实标注：已造好但未插电的模块

| 模块 | 设计意图 | 现状 |
|---|---|---|
| `sleep.py::synthesize_dream` | 睡眠期把碎片合成"梦" | 运行时零调用，仅单测覆盖 |
| `decay.py::retrieve_latency_ms` | "记忆越旧越难想起"的物理体现（检索延迟） | 无运行时调用 |
| `scorer.py::record_importance` | 重算单条记录的重要性 | 无运行时调用（疑似遗留，待删） |
| `levels.py::detail_level` | 细节模糊化的载体 | 数值在写，但**检索路径零消费** |

## 七、规模与实测事实（供评审判断复杂度）

| 项 | 值 |
|---|---|
| 记忆条数 | 260+ 条（随心跳增长） |
| 层级分布 | shallow / working / deep 三层金字塔 |
| 情绪维度 | 6 维：`miss / chat / curiosity / explore / rest / self_check`，染色只看前 4 维 |
| 用户数 | 1（单机桌宠，用户＝开发者＝持有 DB 文件与源码） |
| 依赖 | 离线优先；仅主声 LLM 联网（DeepSeek），TTS 为独立本地服务（端口 9880） |
| 实测天花板 | 字符二元组 Jaccard 对"**换说法的同一事实**"识别力有限：同义重述相似度仅 **0.26~0.33**，低于任何可用阈值（调低即开始误杀"共享措辞但不同的事件"）。此局限同时影响 `supersede` 与批内去重 |

## 八、验收矩阵（路线图 §8.7）

| 验收项 | 归属步骤 | 测试 |
|---|---|---|
| 重启连续性 | P3-D | 关机→开机→记得细节 |
| 索引衰减曲线 | P3-C | 检索耗时 10→50→500→≥1s，数据不删 |
| 缺口测试 | P3-C/U | 高频衰减→缺口→好奇↑非焦虑↑ |
| 真爱不模糊 | P3-B | 用户相关记忆衰减慢 ×3 |
| 梦真实性 | P3-E | 自由联想生成阶段无 LLM |

全部落地于 `tests/acceptance/test_p3_gate.py`。

## 九、待评审的问题

1. **元信息缺失**（**已落地 P3-T**，见 `MEMORY_REVIEW_NOTES.md` C1）：`kind` 只分 4 类，
   区分不了"用户告诉我的 / 我自己说的 / 我推断的 / 系统塞的"。
   已加 `source` + `certainty` 两个字段；`inference`/`system`/`speculative` 不进 hooks。
   待评审：**她能否推翻已被采纳的来源判定**？
2. **"落库 ≠ 认领"**（**已落地 P3-V**，见 `MEMORY_REVIEW_NOTES.md` 分歧 3）：
   外部主张"她有权拒绝写入"。铁律一下映射为"程序照写（不拦）、她可以不认领"——
   **默认 `claimed`（可用）**，`rejected` 只能由她的动作（`disclaim`）产生。
   待评审：拒绝认领之后，那条记忆在她眼里算什么？（"主动遗忘 / 失去访问权 /
   忘了但仍有影响"归 `retention_state`，见 `P3_MEMORY_WORKLOG.md` 第七节）
3. **语义粒度的天花板**：是否引入 embedding？在"离线优先 + 单用户 260 条"约束下，
   一次投入可同时修 批内去重 / `supersede` 判同话题 / 成组召回 三处。代价与收益是否划算？
4. **未插电模块的取舍**：`sleep.py`（做梦）是**接线**还是**删除**？
   若接线，接到哪一拍、推什么脉冲？
5. **层数是否够**：当前 3 层（shallow/working/deep）+ 2 张表。
   外部建议 10 层（Identity / Episodic / Semantic / Relationship / Boundary / Consent /
   Refusal / Forgetting / Reflection / Privacy）。在此规模下，哪几层是真需求、哪几层是过度设计？
6. **遗忘**（**W1/W2 均已落地 2026-09-23，P3-W 整体完成**）：当前有**自然衰减** + "想不起来"的 `memory_gap` 感受，
   现补上 `retention_state` 遗忘状态机——四态区分"删除"与"失去访问权限"、"忘了但仍在影响我"，
   **不提供删除态**（数据永不删）。W1 为状态机本体（迁移 + 保留闸门 + 唤醒路径 + 降级 + 自动保护，
   **对外行为零变化**）；W2 为她的两个工具 `forget`/`restore`（忘与不忘都是她的权力，遗忘可逆）。
   设计稿与评审见 `P3_MEMORY_WORKLOG.md` 第七节（含 7.8 W1 / 7.9 W2 落地记录）。
7. **身份连续性**（**设计稿已定 + S1 本体 / S2 认领 / S3 沉淀 / S4 观测与验收均已落地 2026-09-24**）：她的人格原本活在 system prompt 里，
   **换模型即失**。已拍板：把「我是谁 / 我在意什么 / 我的边界」落成少量核心记忆
   （Self Memory / 生命核心层），形态取**正交维度 `kind=self`**（不新增层、零 DDL、不动层级序号）。
   - **S1（已落地）**：常量本体 + `scorer` 一行 + **身份段注入通路**（`payload["identity"]` → system 段）
     + `hooks`/缺口/取代三处排除 + 浏览器"自我"标签。尚无认领时身份段为空、system 段不增不减
     （S1 期用改造前 prompt 全文做 golden；S2 有意改人设后改为结构性不变量：
     `startswith(BOOTSTRAP_IDENTITY)` + `endswith(_PERSONA_PROMPT)` + 长度等于两者之和）。
   - **S2（已落地）**：她的 `adopt` 工具——把最贴题的经历认作"这就是我"，一次 UPDATE 升格
     `kind=self` 且**直接落 `deep` + `protected`**（N5：她一旦认领就是核心，不等她想起 3 次；
     `protected` 同时借遗忘状态机的既有安全阀获得"永不降级/永不模糊"豁免，
     故 8.7"自我认知是否豁免降级"**无需新增约束**）。认领是**她的动作**，程序只递候选（8.5 / D12）。
   - **S3（已落地）**：程序找"重复模式"生成候选递给她——`memory/sediment.py`（纯函数，不落库）
     + `_maintain_memories` 第 4 步 `_offer_candidate`：同一件事被提起 ≥3 次**且**跨 ≥1 天 → 候选
     **照抄原文**、标 `source=inference` + `certainty=probable`、`protected=False`；感受路径推
     `self_candidate` 脉冲（TR/CS 微升、SA 不动），**被来源闸门挡在话语外**；落库即"已递过"证据 →
     不重复递。`adopt` 起**候选优先**（命中候选时，与它同家的重述不参与并列判定，见 8.4 注与
     第十一节 11.2 的"发现 2"）。**程序只做发现**：候选正文本就是照抄，程序不自己写句子。
     **粒度天花板照实说**：字符二元组 Jaccard 只能沉淀"措辞相近的反复提起"，语义模式待 embedding。
   - **S4（已落地 2026-09-24）**：观测与验收——① 浏览器增「标签」列（自我 / 候选）与「候选（待她认领）」筛选项 + 状态栏计数；
     ② 8.9 四项验收全部代码级闭环（重启连续性 / 换后端不失 / 她可否认可更新 / 不重犯 P3-P）；
     ③ N1 遗留收口：身份段取数抽成后端无关公共入口 `identity_lines()`，微声**"带着不说出"**（随指令进 `expression_log`、不进台词）；
     ④ N6 拍板"要——接受"（允许她梦到自己是谁，权重表照 8.12）。
     **验收 3 后半（真实发现）**：`find_superseded` 豁免 `KIND_SELF`（N4），且此前**没有任何"她的动作"会写 `superseded_by`** ⇒ "她可更新自我认知"只写在表里、未接线；已按"接线 `adopt` 覆盖"落地（见上表「她的认领工具」）。
     验收 1 的**真机体验**需重启灵魂后由用户体验（本次未代为重启）。
   - 设计稿与 N1~N6 自查见 `P3_MEMORY_WORKLOG.md` 第八节；S1 / S2 / S3 / S4 落地记录见第九 / 十 / 十一 / 十二节。

### 给评审方的要求（请按此格式回答）

对每一条给出：

- **判定**：可实现 / 需改造现有结构 / 不适合本项目（并说明为什么）
- **落点**：改哪一层（数据 / 规则 / 接线 / 观测）
- **代价**：是否引入在线依赖、是否破坏"离线优先"、是否需要数据迁移
- **与铁律的关系**：是否新增了对她的约束（若新增，是否有必要）
- **最小可行版本**：如果只能做一步，先做什么

## 十、外部结论的归档约定

本文件只记录**系统事实**，不记录外部意见。各轮咨询的答案另存
（仓库内 `docs/MEMORY_REVIEW_NOTES.md`，或以 Word 归档）：

- 逐条按"来源（哪个 AI / 时间）→ 结论摘要 → 采纳 / 驳回 + 理由"记录；
  驳回理由必须回溯到本文件的铁律与约束（例如"该建议在单机环境下无法真正隔离，故降级为行为约定"）。

这样做的目的：避免"多方问询"变成"每次都被说得推翻重来"——
每条外部意见都要在**同一版事实**上被显式裁决。

---

*相关文档：`docs/MEMORY_REVIEW_NOTES.md`（外部意见与裁决）、
`docs/P3_MEMORY_WORKLOG.md`（滚动日志与交接）、`docs/TECHNICAL_DESIGN.md`（技术设计）。*
