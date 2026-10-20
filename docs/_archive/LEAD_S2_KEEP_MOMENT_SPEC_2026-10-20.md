# S2 细则：她的"这条我想留着"（2026-10-20 02:5x 签发，用户已批准）

> 依据 `docs/LEAD_MEMORY_IDENTITY_DESIGN_2026-10-20.md` §2 S2 + 用户拍板：**决定权交给她**、上限 **13 次/天**（用户 2026-10-20 01:2x 从 5 改为 13）。
> **S0/S1 已验收**（`1c49369` / `9976e26` / `b1f28a7` / `6fd085f`；真机已确认"咖啡键盘"两轮接得上、exchange 记忆按交换封存）。

---

## 目标

给她一个**真能用的动作**：她可以主动把"刚才那一次交换"定格成她自己的重要记忆。**不是我们给权重，是她给权重。**

## 工具形态

- 新工具 `keep_this_moment(reason: str)`（沿用 W3 的 tools 接线方式：`soul/tools/` 新模块 + `chain` 工具表 + `deepseek._TOOL_NAMES` 同步）。
- **作用对象**：**最近一条已封存的 `kind='exchange'` 记忆**（`seal_exchange` 刚写的那条）。
  - 没有可留存的交换 ⇒ 返回明确失败（"现在还没有可以留住的对话"），不猜、不抓独白。
- **`reason` 必填**：她的理由（1–120 字）。为空/超长 ⇒ 拒绝并说明。理由入库，这是与"程序标重要"的根本区别。
- 成功后回执要**复述那条记忆的内容**，让她确认自己留下了什么。

## 生效

- `protected = 1`
- `importance = max(importance, KEEP_IMPORTANCE_FLOOR)`，`KEEP_IMPORTANCE_FLOOR = 0.7`（＝升层门槛，直接够到 deep）
- `level` 升到 `deep`
- 新列 **`keep_reason TEXT`**（`CREATE TABLE IF NOT EXISTS` 同类升级路径；旧行 NULL）
- 落库后写一行日志：`kept_memory id=<id> reason=<...>`，便于事后查"她在什么时刻因为什么留住了什么"

## 上限（防"全都重要"）

- 常量 **`KEEP_DAILY_LIMIT = 13`**（可配，不写死在分支里）。
- 统计口径：**当天（本地日界）成功留住的数量**；超限 ⇒ 明确拒绝（"今天已经留住 13 条了，明天再说"），**不静默**。
- 幂等：同一条记忆重复留 ⇒ 不重复计数、不重复写日志。

## 验收（用例 + 取消测试）

1. 调用成功 ⇒ 该记忆 `protected=1`、`level='deep'`、`importance>=0.7`、`keep_reason` 落库、日志一行。
2. **第 14 次当天调用 ⇒ 拒绝**，且**没有**任何记忆被改。
3. 同一条重复调用 ⇒ 只计 1 次。
4. 没有可留存的交换 ⇒ 明确失败（不是抛异常、不是静默成功）。
5. `reason` 为空/超 120 字 ⇒ 拒绝。
6. **跨日重置**：把时钟推到次日 ⇒ 又能留（用注入 clock，不靠真实等待）。
7. **与 S1b ② 的联动**：一条**刚封存（20 分钟内）**的 exchange 记忆被留住后，它**仍能被 hooks 检索到**（`protected` 绕过"进行中对话"闸）；未留住的同类则被挡住。**这条必须有**，否则"她留住"就是个看不见的动作。
8. 取消测试：去掉上限 ⇒ 用例 2 红；去掉 `reason` 必填 ⇒ 用例 5 红；去掉 `protected/deep/importance` 任一效果 ⇒ 用例 1 红；去掉"绕过闸" ⇒ 用例 7 红。

## 边界

- 写作用域：`src/elysia/soul/tools/`、`src/elysia/soul/dialogue.py`（或同类）、`src/elysia/core/state_store.py`（加列）、`src/elysia/llm/`（工具表）、`tests/**`。
- 不碰 `body/pet.py`、`assets/pet/**`、`scripts/**`；不重启她；不 push；不手改 `data/*.db`。
- 门禁走 `scripts\gate.ps1`，基线 **768 passed + 13 skipped**。
- 交付后停手等验收；S3（允许记错）随后放行。
