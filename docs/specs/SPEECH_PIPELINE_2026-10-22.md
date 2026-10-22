# 说话管线：把 20 条规则收成 3 层（2026-10-22）

> 触发：用户问「针对她说话的规则是不是太多了，有将这些规则融合的可能吗？」
> 结论：**是**。规则不是写错了，是**互相追尾**。本文是盘点 + 融合方案 + 迁移顺序。
> 纪律：每一步都必须是**行为等价**或**只减不加**；`scripts\gate.ps1` 只有当护栏（基线 979/13 @ b151d70）。

## 一、现状盘点（谁在管她说话）

| 层 | 机制 | 位置 |
|---|---|---|
| 说不说 | `reply_desire ≥ 0.59` + 五权重 + `REL_FLOOR`；`restraint`（暂不回应）| `expression.py` |
| | 节奏：`EXPRESS_INTERVAL 60–1800s` + 8 个节奏权重；对话活跃窗 900s | `expression_service.py` |
| | 四种 `speech_cause` + `unfinished`（20 min 静默 + 2 h 间隔）| `speech_trigger.py` |
| 说什么 | 受保护词 6 个 + 触发谓词（代码级许可，不是 prompt）| `words.py` |
| | `word_block` 校验 → 降微声 → 同 prompt 重试 | `chain.py` / `expression_service.py` |
| | 反编造：T-024 读类跨句、T-030 写类完成（欠账+没写）| `honesty.py` |
| | 强制写 T-021（即时意图、2 h 冷却）；工具轮后强制说话轮 T-015 | `expression_service.py` / `chain.py` |
| | 一次 ≤3 句、句间 2–8 s | `expression.py` |
| 说完 | 承诺捕获两条路径 + 放弃判定（`is_abandonment`）| `promises.py` |
| | 未完成清单 `unfinished_things` + 兜底提醒 `promise_reminder` | `promises.py` / `expression_service.py` |
| | 表达记忆落库、叙事漂移、成文自我证据、TTS 发布 | `expression_service.py` |
| prompt | 「答应的事就动手」「没做过的事别说做过」、工具说明、称呼、未完成/提醒 | `deepseek.py` |

## 二、今晚被咬的三口（都是规则之间没对齐）

1. **自激**：诚实句「其实我还没真的写完呢」被承诺检测记成新承诺（T-031/T-032 已堵两条入口）。
2. **双压**：`unfinished_things` 与 `promise_reminder` 同一轮同时出现；20:48 她被压出「在写了，真的在写」。
3. **误降级**：`word_block:累` 一句把主声降成微声，而微声是罐装句——她以为在说，其实是系统在替她说。

## 三、目标架构：3 层 + 3 张表

**① 开口决策（说 / 不说）** → 一个纯函数 `decide_speech(state)`：输入状态与关系，输出 `{speak, cause, intent}`。所有阈值/权重收进**一张常量表**，不再散在心跳、表达服务、触发器三处。

**② 事实对账（说什么不能越界）** → 一个 `claims.py` + **一张注册表**：

| claim 类型 | 触发形态 | 需要什么证据 |
|---|---|---|
| `reading` | 读/看/翻/查/搜/逛/找了…页面 | 本轮 `read_url` 结果 / `world` 记忆 |
| `writing_done` | 写完了/写好了/收尾了/落笔了/搁笔了 | 本轮 `write_note` 结果（且确实欠着稿）|

以后新增一类主张 = **加一行**，不再新增函数、不再新增自激入口。`words.py` 单独保留（它管"这个词此刻有没有资格说"，性质不同）。

**③ 她自己的话（答应 / 没做 / 做完了）** → 一个 `own_words`：payload 只有一个字段（≤3 条、去重、一种口吻），到期那条换句式（"我想起来：…"）而不是再加一段。承诺捕获、成文自我证据、未完成清单都从这一个来源出去。

净效果：**规则从 ~20 条 → 3 层 × 每层一张表**；删掉的是重复施压与互相追尾，安全线一条不少。

## 四、迁移顺序与验收

| 步骤 | 内容 | 作用域 | 等价性 | 何时 |
|---|---|---|---|---|
| ③ | `unfinished_things` + `promise_reminder` → `own_words` | `expression_service` / `promises` / `deepseek` prompt | 只减重复，阈值不动 | **今晚（进行中 T-033）** |
| ② | T-024/T-030 → `claims.py` 注册表 | `honesty.py` → 新 `claims.py` + 调用点 | 纯等价重构（现有用例全绿即证）| T-033 落地后 |
| ① | 三处阈值 → `decide_speech()` 纯函数 | `expression.py` / `expression_service.py` / `speech_trigger.py` | 会改"何时开口"，**非等价** | **观察期满（≥10-25）之后** |

**验收规则（三步通用）**
1. `scripts\gate.ps1` 全绿（基线随提交更新，禁 `-q`）。
2. 行为等价步骤必须附"同一输入的前后输出对比"；只减步骤附 payload 字段前后对比。
3. 任何一步若需要改阈值/冷却 ⇒ 拆出去单独立项，不许混进重构。
4. 回滚：每步独立 commit，回滚只 revert 单个 commit。

## 五、明确不做

- 不在观察期内改「何时开口」（第①步）——那会改她的性子，观察数据就断了。
- 不动 `words.py` 的许可逻辑（T2 底线：说自己没经历过的状态要拦）。
- 不为融合新增任何每日配额/完成率统计。
