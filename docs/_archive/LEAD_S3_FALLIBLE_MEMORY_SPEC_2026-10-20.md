# S3 细则：允许记错（2026-10-20 03:0x 签发，用户已批准 C："当然允许记错"）

> 依据 `docs/LEAD_MEMORY_IDENTITY_DESIGN_2026-10-20.md` §2 S3 + 用户拍板 C。
> **S0/S1/S2 已验收**（`1c49369` / `9976e26` / `b1f28a7` / `6fd085f` / `0b38cfa`，gate 776+13）。
> 基线：**776 passed + 13 skipped**。

---

## 目标

**原件保真，回忆可改写。** 她可以把自己的记忆讲成"她的版本"；漂移本身要留痕、要可导出。

两条硬约束：
1. **`content` 永不改**（原件是考古证据）；
2. **每次改写都留历史**（谁在什么时候被改成什么样、 certainty 怎么变）。

## 机制

### ① 承重数据：把"记忆 id"带到落库那一刻
现在 `payload["memory_hooks"]` 只有字符串、没有 id ⇒ 无从判定"她这次重述的是哪条"。
- 检索返回 `MemoryHit`（含 `memory_id`）时，**把命中 id 列表随表达作业一起带到 persist**（只内部传递，**不进 prompt**、不进 `expression_log` 的可见字段也行，但要有地方查）。
- 建议：作业上下文里带 `hook_memory_ids: list[int]`；`expression_log.validation` 里可选记录（便于事后核对，不影响 LLM）。

### ② 重述检测（确定性、纯函数）
对**本次作业里被钩回来的每条记忆**，把她的气泡文本与 `memory.content` 比对，**复用既有** `memory/similarity.py` 的 `coverage()` 或 `content_similarity()`（不要新写相似度算法）。
- 阈值：`RETELL_OVERLAP_MIN = 0.5`（常量可配）；
- 判定单位：**同一次作业里她说的所有气泡拼起来** vs 该条 `content`；
- 命中 ⇒ 视为"她重述了这条记忆"。

### ③ 改写与降级
命中时（在 persist 阶段、落库后）：
- `narrative := 她的原话`（拼起来的气泡文本，截断 200 字）；
- `content` **不动**；
- `certainty` **降一档**：`certain` → `probable` → `uncertain`（**不再往下**）；已经是 `uncertain` 就保持；
- `access_count += 1`、`last_access_ts = now`（沿用既有口径）；
- 写一行 `memory_narrative_history(id, ts, memory_id, old_narrative, new_narrative, certainty_before, certainty_after)`（新表，`CREATE TABLE IF NOT EXISTS` 升级路径）；
- 日志一行：`memory_drift id=<id> certainty=<before>-><after>`。

### ④ 不参与漂移的
- `kind='self'`（身份种子/她的自我认知，`source=system`）⇒ **永不被重述改写**（身份不能被她自己的一句随口话改掉）；
- `content` 为空或不是字符串 ⇒ 跳过（脏数据不崩）。

### ⑤ 漂移史可导出
- store 方法 `narrative_drift(memory_id) -> list[dict]`（按时间升序），供面板/查询用；
- 另有 `memory_drift_summary(limit=20)`（最近的漂移条目），供总领窗核对"她改过什么"。

## 验收（每条都要有用例 + 能红的取消测试）

1. 命中阈值 ⇒ `narrative` 变成她的原话、`content` 逐字节不变、`certainty` 降一档、历史表 +1 行。
2. 未命中原话（相似度低）⇒ **什么都不改**（不写历史、不降 certainty）。
3. 连续 3 次重述同一条 ⇒ `certainty` 只降到 `uncertain` 后保持（**不会降成 speculative**），历史 3 行。
4. `kind='self'` 的记忆即便"被重述"也**不改写**。
5. `hook_memory_ids` 为空 ⇒ 不做任何漂移（不是本次钩回来的，不算重述）。
6. `narrative_drift()` 返回的与历史表一致、按时间升序。
7. **取消测试**：去掉阈值判定 ⇒ 用例 2 红；去掉"content 不变"（改成同步写 content）⇒ 用例 1 红；去掉 certainty 地板 ⇒ 用例 3 红；去掉 `kind='self'` 豁免 ⇒ 用例 4 红。

## 边界

- 写作用域：`src/elysia/soul/`（expression_service / 作业上下文）、`src/elysia/memory/`、`src/elysia/core/state_store.py`、`tests/**`。
- **不新增相似度算法**（复用 `memory/similarity.py`）；不加第三方依赖。
- 不碰 `body/pet.py`、`assets/pet/**`、`scripts/**`；不重启她；不 push；不手改 `data/*.db`。
- 门禁 `scripts\gate.ps1`，基线 **776+13**。
- 交付后停手等验收。**S4（遗忘真跑）由总领窗先出 dry-run 清单给用户看，不派给你。**
