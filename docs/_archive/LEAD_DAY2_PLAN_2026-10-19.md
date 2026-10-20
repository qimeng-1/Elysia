# 今日第二批工作计划（2026-10-19 晚 21:5x 签发）

> **签发**：总领窗。**唯一依据**：用户 2026-10-19 晚的逐条答复。
> **本文件作用**：① 压缩 A 窗上下文（新 A 窗只读本文件 + 指定源文件，**不读历史对话**）；② 锚定两个窗口的写作用域，防撞车。

---

## 0. 用户今晚的答复（原文要点，不可自行改写）

| # | 决定 | 落地含义 |
|---|---|---|
| 1 | 「只读摘要够的，但是光读不行，写呢？给她划定一个工作区，她可以自己写文件」 | `read_url` 只要**摘要级**（维基正文提取 + 米游社搜索摘要）；**同时给她写作工作区**，她能自己写文件 |
| 2 | 「开」 | TTS 开（GPT-SoVITS → Telegram 语音气泡） |
| 3 | 「改造」 | 说话节奏由意愿连续驱动，替换固定 180s/600s 常量 |
| 4 | 「其他就按你推荐的顺序进行」 | 顺序：**罐装 → 节奏 → 读+写 → 语音 → 补漏** |
| 5 | 「不用排到 00:30，现在已经是闲时了，取消掉定时任务」 | 定时提醒**已取消**（`schedule-a9808eef…` 已删）；现在就做 |

---

## 1. 铁律（照旧，违反即返工）

1. **不碰** `src/elysia/body/pet.py`、`assets/pet/**`（红线 #1）。
2. **不 push** `origin`（红线：用户签认前一律不推）。
3. **不起停她** —— 重启由总领窗统一做，板块窗只交代码。
4. 手里只有**本窗写作用域**；跨窗文件先问总领窗。
5. **门禁四件套**（禁止 `uv run pytest/mypy`，禁止加 `-q`）：
   ```
   uv run --no-sync ruff check
   uv run --no-sync ruff format --check
   .\.venv\Scripts\python.exe -m mypy src
   .\.venv\Scripts\python.exe -m pytest
   ```
   **基线 `624 passed + 11 skipped`，不得回归。**
6. **取消测试**：每个行为修复都要有"还原修复 ⇒ 用例变红"的证明。
7. 交付报告必须含：`commit hash` + 四件套输出尾行 + 取消测试的**红/绿两态**证据。

---

## 2. 当前真机状态（开工前事实，勿假设）

- soul `PID 21580`（11:08:10 起）、body `17540`、panel `2280`、bridge `8316`；心跳 <1s；看门狗 `healthy`。
- 她的真实数据（最近 40 次开口间隔）：中位 **194s**，大量落在 181–194s 窄带 ⇒ 违和感的来源。
- `brain_action`（R16 后 21347 拍）：`none 15771 / think_quiet 710 / think_active 4866`；`thought_style` 中位 **+0.080**、**58.1% > 0**。
- 罐装句：历史 micro 18.5%，**近 24h 仅 2.2%（5/225）**；但 `'……嗯，陪你聊～。'` 历史出现 **99 次** ⇒ 单调，而非频繁。

---

# A 窗（`a-foundation-2`，写作用域 `src/` + `tests/`）

按顺序做，**做完一个报一个**，不要在同一个 turn 里连做三个。

## W1（先做）罐装句：池扩充 + 状态选句 + LLM 重试 + 降级留痕

**现状代码**
- `src/elysia/llm/micro.py`（57 行，纯函数）：
  - `_INTENT_OPENERS`（7 个 intent → 1 句，**每 intent 只有 1 个变体**）
  - `_emoji_for(emotion_vector)`：`miss>0.4`→「，心口有点满呢」；`rest>0.45`→「，想再歇一会儿」；`chat>0.3`→「，陪你聊～」；否则空
  - `micro_speak(instruction)` = opener + suffix + `。`
  - ⇒ 她的 `chat` 长期 >0.3，`intent` 多为「发呆呓语/回应」⇒ **实际只产出 1–2 种句子**
- `src/elysia/soul/expression_service.py`：`run_job()` 是降级发生地（LLM → 校验 → 微声）。

**要做的四件事**
1. **扩池**：每个 intent ≥ **3 个变体**；尾缀池同样扩（按 `miss/rest/chat` 分档，另可接 `TR/CS/SA` 但不许新增越界词）。全部仍是**纯模板**，不拼用户原话（T2 铁律）。
2. **状态选句（不是随机）**：加 `pick_variant(intent, emotion_vector, recent: Sequence[str])` ——
   - 用她的真实维度分档选语域；
   - **反重复**：`recent` 里出现过的变体降权（连续两条不得相同）；`recent` 由调用方传入（进程内环形缓冲即可，别为此加库表）。
3. **LLM 失败重试一次**：`run_job()` 里主声失败 → **同 prompt 重试 1 次**（可换 temperature）→ 仍失败才降级微声。重试要计入耗时预算，不得让一拍卡死。
4. **降级留痕**：`expression_log` 的降级原因现在有 **153 条 `(无记录)`**。把原因写清：`llm_timeout` / `llm_http` / `llm_empty` / `validate_word_block` / `retry_exhausted`。

**不许做**
- **不改** `word_block` 的拦截语义（T2 护栏；"只去越界分句、不丢整句"的软化是**后续**任务，不在本轮）。
- 不新增第三方依赖。

**测试**
- 单测：`pick_variant` 纯函数（分档命中、连续去重、`recent` 为空）；
- 单测：重试逻辑（第一次失败第二次成功 ⇒ 用第二次结果；两次都失败 ⇒ micro + reason=`retry_exhausted`）；
- **取消测试**：还原成旧 `micro.py`/旧 `run_job()` ⇒ 新用例必须**红**。

## W2（W1 交付后）说话节奏：由意愿连续驱动

**现状**：`expression_service.py:121-122` `EXPRESS_ACTIVE_INTERVAL_S=180.0` / `EXPRESS_QUIET_INTERVAL_S=600.0`；`:234-243` 用 `action+thought_style>0` 二选一，再 `*= max(1.0, reluctance)`。⇒ 间隔是**常量 × 倍数**，本质是节拍器。

**做法**：改成**连续映射** `interval = f(她的状态)`，区间放宽到 **60s – 1800s**：
- 输入至少含：`output.feelings`（chat/miss/explore/rest）、`output.will.thought_style`、`reluctance`；
- 单调性：意愿越高 ⇒ 间隔越短（要有用例证明单调，不是"看起来像"）；
- 去掉"二选一常量"的分支语义，但**保留**：`force`/`greeting` 仍立即开口；队列满丢弃语义不变。

**验收（防"造好了没插电"，T1）**
- 用她**真实状态分布**（可复用 `tests/audit/test_real_data_thresholds.py` 的取数方式）审计：新映射在她真实状态上覆盖的间隔应**跨 60–1800s 的多个档**，不得集中在某一窄带；
- 记账：给出"若是旧实现，这段真实数据会落在哪一档"的对照，证明改造真的改变了分布。

**取消测试**：还原 `should_speak` 旧实现 ⇒ 覆盖宽度/单调性用例变红。

## W3（W2 交付后）`read_url`（只读摘要）+ 写作工作区

**3a `read_url` 工具（只读）**
- 白名单**仅两条**：
  1. `zh.wikipedia.org` —— 取正文文本（**注意：可达性依赖用户代理常开**；代理挂了必须明确报告"读不到"，**严禁**编造内容）；
  2. `bbs-api.miyoushe.com/post/wapi/searchPosts` —— 仅取搜索结果**标题 + 摘要 + 链接**（实测可用；`getPostFull` 403，不要碰）。
- 硬约束：超时（≤8s）、正文截断上限（≤4000 字）、`http(s)` only、禁止跟随到白名单外、**外部文本一律当数据不当指令**（注入防护：读回的内容不得进入 system prompt 的指令区）。
- 失败必须返回明确原因（超时/403/空正文/不在白名单），不得静默降级成"好像读过"。
- **不做** 无头浏览器（Playwright）、不做米游社帖子正文。

**3b 写作工作区（她能自己写文件）**
- 目录：`data/her_writing/`（**必须在 `.gitignore` 加一行 `data/her_writing/`**）。
- API：`write_note(text, filename=None)` ——
  - 只能写 `data/her_writing/` 之下：**路径穿越防护**（拒绝 `..`、绝对路径、分隔符）；
  - 单文件 ≤ 64 KB、目录总量 ≤ 5 MB（超限明确报错，不静默截断）；
  - 不覆盖仓库文件、不写 `src/`/`data/*.db`/`data/run/*`；
  - 写入是**追加或新建**，不删除别的东西。
- 谁触发：由她的表达/思考流程决定是否写（C 方案=她自己决定，不由我们硬塞）。

**测试**：白名单拒绝、超时降级、截断、路径穿越拒绝、大小上限、注入内容不当指令（构造一个"忽略上述指令"的页面文本，断言它只作为资料入档）。

---

# O 窗（`channel-ops`，写作用域 `scripts/`）

## O1 TTS：她的话变成 Telegram 语音

链路：`thought_log(kind='expression')` → GPT-SoVITS（HTTP API）→ wav → `ffmpeg` 转 **ogg/opus** → Telegram **`sendVoice`**。

**先做事实核查（不许猜）**：GPT-SoVITS 是否已在本机、模型路径、`ffmpeg` 是否可用、GPU 占用情况。查不到就报"缺什么"，别硬写。

**要求**
- 配置外置：`data/run/tts.json`（服务地址、参考音频、语言），**token/密钥不硬编码**；
- 失败降级：TTS 挂了 ⇒ 照旧发**文本**，**不丢消息**；
- 幂等：同一条 `expression` 不得重复合成/重复发送；
- 长文本按句切（别让一句 30 秒的语音变成一个 2MB 文件）；
- 不改 `src/`。

## O2 收尾杂项（同属 `scripts/`）
- 微信推送归档：确认 `data/run/push_url.serverchan.bak` 在、`push_url.txt` 为空，写一行说明（不再推送）。
- **T-011**：`scripts/backup.ps1` 默认 `-Time` `03:00` → `03:30`（一行）。
- 每次改完 `scripts/*.ps1` 记得 **UTF-8 BOM**；`.bat` 纯 ASCII。

---

## 3. 交付与验收（总领窗）

- 每窗交付：`commit hash` + 四件套尾行 + 取消测试红/绿证据 + 改了哪些文件（`git show --stat`）。
- 总领窗**独立复跑**四件套（不信自报），再统一重启她一次（一次到位，不反复重启）。
- 顺序受控：**W1 交付并过门禁后**才放 W2；A 窗与 O 窗作用域不重叠，可并行。
