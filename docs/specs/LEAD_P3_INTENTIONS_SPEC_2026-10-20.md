# P3 细则：她自己的"打算"与"手"（2026-10-20 23:0x 签发）

> 用户拍板：「批准，且需要**动手能力**」；并且「**不规定次数**」——配额只允许出现在测试里，运行逻辑只保留技术护栏。
> 前置：P1a（`716105b`）、P2（`d50b8e6`）已验收并已重启生效（真机已确认：她自己读了维基并落了两条 `world` 记忆）。
> 基线：**819 passed + 13 skipped**。

---

## 目标

她现在能读、能记，但**"打算做一件事"无法跨越时间**：说完"我晚点去看看"，一小时后就没了。
P3 给她三样东西：**写下打算 → 后台真去做 → 下次开口能讲**。

---

## 3a 声明意图：`set_intention` 工具

```
set_intention(goal: str, action: str, target: str | None = None, kind: str = "task")
```

- `action` 白名单：`read_url` / `write_note`
- `kind` ∈ `task | surprise | conceal | play` —— **这个字段同时是 P1a 的"撒谎动机"来源**（P1b：`surprise/conceal/play` 的意图存在时，无依据的经历主张**放行**并记 `claim_fiction_allowed motive=<id>`）
- `goal`：一句话（≤120 字），就是她自己的说法（"我想去看看维基上关于埃舍尔的页面"）
- 参数校验失败 ⇒ 明确回执，不猜
- 落 `intentions` 表：
  `id, created_ts, goal, action, target, kind, status(pending|done|failed|dropped), attempts, last_attempt_ts, outcome, finished_ts`

## 3b 后台执行器：她的"手"

- soul 侧**独立 asyncio worker**（不是心跳拍，不占 1Hz 循环），每 **30s** 看一次 `pending`（这是检查频率，不是"她多久能做一次"）
- **技术护栏（不是行为配额）**：
  - 同时只跑 **1 个**动作；
  - 单次动作 **≤8s** 超时；
  - 连续失败 **3 次** ⇒ `failed`，不再重试（并写 `outcome` 说明原因）
  - **不设"每天只能做 N 件事"**（用户 2026-10-20 明确否决配额）
- 执行：
  - `read_url(target)` ⇒ 走 P2 的 `record_world_memory`（成功才落；同 URL 24h 去重照旧）⇒ 意图 `done`，`outcome` 存一句"我读到了什么"（≤120 字，取自 world 记忆摘要）
  - `write_note` ⇒ 走 W3 的写作区 ⇒ `done`
  - 失败 ⇒ `attempts+1`、`last_attempt_ts=now`、留 `outcome`=失败原因；未到 3 次保持 `pending`
- 每个动作一行日志：`intention_action id=.. action=.. status=.. ms=..`

## 3c 让她"讲得出来"

- 表达 payload 增加 **`recent_discoveries`**：
  - 最近 **24h** 的 `kind='world'` 记忆（≤5 条，每条 ≤80 字，带"读了《X》：…"）
  - 最近完成/失败的意图（≤3 条，`goal` + `outcome`）
- system 段加一句：**"这些是你最近自己做过/读到的事。要不要说、说多少由你决定；不要把它说成你此刻正在做。"**
- `payload` 里只放文本，不放 id（与 hooks 同理）。

---

## 验收（每条都要用例 + 能红的取消测试）

1. `set_intention` 成功 ⇒ 一行 `pending`；参数非法（未知 action / 空 goal / 超长）⇒ 明确拒绝、不落库。
2. 执行器在检查窗口内执行 `read_url` 意图 ⇒ **新 world 记忆** + 意图 `done` + `outcome` 非空；日志一行 `intention_action`。
3. `write_note` 意图 ⇒ 文件出现在写作区 + `done`。
4. 超时（假抓取器 sleep 超 8s）⇒ `attempts=1`、仍 `pending`；连续 3 次 ⇒ `failed` 且不再重试。
5. **并发 ≤1**：两条 pending 同时存在 ⇒ 同一时刻只一个在执行（用假时钟/假执行体断言）。
6. `recent_discoveries`：完成意图后，下一次表达 payload 里**必须**包含它的 `goal/outcome` 与对应 world 记忆摘要；没有则空列表（不是 None）。
7. **P1b 联动**：存在 `kind='surprise'` 的意图时，无依据的经历主张**放行**且记 `claim_fiction_allowed motive=<id>`；该意图 `done` 后不再放行（旧动机不永久免疫）。
8. 取消测试：去掉执行器调用 ⇒ 用例 2 红（意图永远 `pending`）；去掉 3 次失败熔断 ⇒ 用例 4 红；去掉"动机只在该意图存在期间有效" ⇒ 用例 7 后半红。

---

## 边界

- 写作用域：`src/elysia/soul/`（新 `intentions.py` / 执行器）、`src/elysia/soul/tools/`、`src/elysia/core/state_store.py`（新表 + 迁移）、`src/elysia/llm/`（工具表 + system 一句）、`tests/**`。
- **不碰** `body/pet.py`、`assets/pet/**`、`scripts/**`；不重启她；不 push；不加依赖；不手改 `data/*.db`。
- 门禁 `scripts\gate.ps1`，基线 **819+13**。
- **不要顺手做 P4**（节奏 2.0 是下一片，我要单独验收）。
