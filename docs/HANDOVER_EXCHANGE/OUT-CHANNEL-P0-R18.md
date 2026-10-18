# OUT-CHANNEL — P0-R18 通道步 1-3 交接单（通道窗 · 2026-10-18）

> **任务号**：P0-R18（共享任务板 `task-15`）
> **用户原话（2026-10-18）**：*"平时只要电脑不在旁边，我无法知道她在干什么，也无法和她说话。"*
> **本卡范围**：通道方案（`docs/LEAD_CHANNEL_PLAN_2026-10-18.md`）步 1、2、3；步 4-5 按用户裁决归后续卡。
> **总领验收**：**通过**（含两次真机写入，见 §6）。

---

## 1. 提交

| commit | 标题 |
|---|---|
| `c2f7884` | feat(panel): 通道步1-3：面板对她说话 + 手机token鉴权 + 她开口时推送手机 (P0-R18) |

提交内只含 4 个文件（逐路径 `git add`，未 `git add -A`）：

| 文件 | 变更 |
|---|---|
| `scripts/elysia_panel.py` | +627 |
| `scripts/elysia_panel.html` | +137 |
| `scripts/push_notifier.py` | 新建 281 行 |
| `tests/unit/test_panel_channel.py` | 新建 553 行，**14 用例** |

未碰：`src/elysia/**`、`docs/LEAD_*.md`、`docs/TASK_BOARD.md`、`data/*.db`、`.env`、`pet.py`、`assets/**`、任何 `.ps1`。
总领 `a6327d8`（`_verify_up()`）原样保留，本卡在其上叠加。

---

## 2. 三步实现与语义

### 步 2（最高优先）：对她说

`POST /api/say {"text": "..."}` → 写 `state.db` 的 `kv['interaction'] = {"ts": <now>, "text": <原文>}`
（她的心跳每拍 1Hz 读这一行；`ts` 更新即被刺激、强制开口 —— 与桌宠 `body/pet.py:_write_interaction` 同一条通道）。

**写库纪律**：
- 面板其余部分**全部 `mode=ro`**（只读设计不变）；这是**唯一**的写入口，用独立读写连接 + `PRAGMA busy_timeout=5000`；
- **写后回读核对**：提交成功但内容不是我们写的 ⇒ 同样报失败（"提交成功 ≠ 写对了"）；
- 库不存在 ⇒ 明确失败，**绝不新建空库冒充成功**；不是她的库（无 `kv` 表）⇒ 明确说"这看起来不是她的状态库"。

**返回值语义**（为将来"她可以选择不回复 / 回复多句"预留）：

| 字段 | 含义 |
|---|---|
| `wrote` | 消息**真的**写进她的库了吗 |
| `responded` | 等待窗口内她开口了吗：`true` / `false` / `null`（读不到 `expression_log`，无法确认） |
| `ok` | **只有写入失败才是 `false`**。`wrote=true, responded=false` ⇒ `ok=true`：**"她这次没有开口"是合法状态，不是错误** |

写入成功后轮询 `expression_log` 最多 10 秒（先取基线再写，避免她抢先回应被漏算）；
没等到就如实返回 `title="已送到 —— 她这次没有开口"`，并附 `soul` 段（她当时是否活着、心跳多久前），不猜。

**输入校验**：`text` 必须是字符串；去控制字符（保留换行，防日志/终端注入）；去首尾空白；拒空；**上限 500 字**（超限拒绝而非静默截断）。

**HTTP 状态**：写成功（含她没开口）= 200；输入不合法 = 400；写库失败 = 500；未授权 = 401；限速封禁 = 429。

### 步 1：手机能看（含强制鉴权）

- `--host` 新增，**默认 `127.0.0.1` ⇒ 不启用 token ⇒ 默认行为一字未变**（真机 `/api/status` 返回 `auth_required=false` 已验证）。
- **绑非回环地址 ⇒ 强制 token**：
  - `data/run/panel_token.txt`（`data/run/` 已在 `.gitignore`）；首次 `secrets.token_hex(32)`（64 hex），用 `open("x")` 独占创建防两个面板竞态，读不到才回退读对方写的；
  - **所有请求**（含首页 `/`）校验 `Authorization: Bearer` 或 `?token=`；`hmac.compare_digest` 比较；
  - 不对 ⇒ **401，body 只有 `unauthorized`**，不含任何她的状态（真机泄露探针：`soul_alive/age_days/heartbeat/Elysia` 全部未出现）；
  - 限速：每客户端 IP **20 次失败/60 秒 ⇒ 429 封禁 60 秒**，封禁期内**正确 token 也拒**；成功一次即清空失败账。
  - `--token` 可显式指定（隧道/反代场景；在回环上也可显式开鉴权）。
- **修掉一处会挡住手机的操作性缺陷**：原 `POST /api/action` 的 Origin 校验只认 `127.0.0.1/localhost`，手机从 LAN IP 打开时按"关机/重启"会被 **403 拒绝**。改为"Origin 主机 == 请求 Host（或回环）即放行"；非回环本身有 token 兜底，不扩大风险。
- 页面：`?token=` 存入 `sessionStorage`，之后所有 `fetch` 自动附加 token；401 时页面直接提示"需要 token、token 在 `data/run/panel_token.txt`"。手机访问说明写进面板页脚（Tailscale/ZeroTier 组网，别开公网端口）。

### 步 3：她开口时推送手机

- 新模块 `scripts/push_notifier.py`：**只读**轮询 `expression_log` 的 `MAX(id)`，**不写她的库**，不改她任何代码。
- 渠道：`data/run/push_url.txt` 第一个非 `#` 行，或环境变量 `ELYSIA_PUSH_URL`（环境变量优先）。两种形态：
  - URL 含 `{text}` / `{title}` ⇒ URL 编码后 **GET**（Bark 模板式）；
  - 否则 **POST JSON 超集** `{"title","body","text","desp","content","level","ts"}` —— Server酱（title/desp）、Bark（title/body）、Telegram（text）各取所需。
- 游标 = `expression_log.id`：首次只建游标（**不补推历史**，面板半夜重启不会刷屏）；之后同一条只推一次。
- **失败静默降级**：不可达/超时/格式错一律吞掉，只写 `last_error` 供面板显示；`poll_once()` **永不抛异常**。
- 每次请求**现建 opener** 并带"扫代理直连"重试（R11 死代理缓存教训）。
- 面板启动后自动起 daemon 轮询线程（3 秒）；用户在建/改 `push_url.txt` 后**无需重启**即生效。
- 面板"服务"卡片显示 `手机推送 已配置/未配置`；页脚显示降级原因。

---

## 3. 取消测试红证（实测，三处修复各一条）

> 证明方式：临时把修复还原成旧写法 → 跑对应用例 → 必须红 → 用哈希核对过的备份还原。
> `scripts/elysia_panel.py` hash `E2ECB88F…A7217B00` / `scripts/push_notifier.py` hash `CED1F10D…AD3D9A7`，红证前后哈希一致。

1. **去掉写 interaction** ⇒ `test_say_reaches_expression_log_within_10s` 红：
   红在 `assert result["responded"] is True`，实测输出
   `AssertionError: 她没在 10 秒内开口：10 秒内 expression_log 没有新行…`
   用例本体：真实 `SoulHeartbeat` 耳朵 + 真实 `ExpressionService.persist`（只把 LLM 换成假 handler），
   `panel.say` 从另一线程写库 → 10 秒内 `expression_log` 出现"我在呢"，且 `kv` 内容与写入一致。
   反向对照 `test_say_without_write_never_reaches_her`：不写 kv ⇒ `expression_log` 计数 0（证明绿不是后台噪声）。
   ⚠️ 红证过程中发现一个假绿风险并已加固：模拟时钟 `speed=60` 时 10 秒真实等待 = 600 虚拟秒，
   正好触发她自己的内部冲动（`EXPRESS_QUIET_INTERVAL_S=600`）。已把测试里的 `_last_express_ts` 压到窗口外，
   现在只测"被你说话强制开口"这一条因果链。
2. **去掉 token 校验** ⇒ `test_non_loopback_requires_token_and_401_leaks_nothing` 红：
   `Failed: DID NOT RAISE HTTPError`（无 token 请求 `/api/status` 直接 200）。
3. **去掉推送失败兜底** ⇒ `test_push_unreachable_degrades_silently_…` 红：
   `urllib.error.URLError: <urlopen error timed out>` 从 `poll_once()` 冒出（不再静默降级）。
   正向路径另有 `test_push_posts_her_words_to_webhook`：本地假 webhook 收到她的话、字段超集正确、同一条不重复推。

其余用例覆盖：默认回环不鉴权（`DEFAULT_HOST=="127.0.0.1"`、`resolve_auth` 返回 None）、Bearer/query 两种 token、
暴力猜解封禁、`/api/say` 她没开口仍 200、空消息 400、写失败/非状态库显式报错、输入清洗、推送配置读取与 URL 脱敏、页面含"对她说"与 token 接线。

---

## 4. 门禁四件套实测输出（最终，2026-10-18 23:44-23:46）

| # | 命令 | 实测 |
|---|---|---|
| 1 | `uv run --no-sync ruff check` | **红，但不在本窗**：`tests/unit/test_reply_rhythm.py:203` B905 + RUF007（A 窗 P0-R19 的 `??` 未入库新文件，mtime 23:34:58；总领已确认非本窗问题，并已转告 A 窗）。**本窗 3 个 `.py` 单独跑：`All checks passed!`** |
| 2 | `uv run --no-sync ruff format --check` | `174 files already formatted`（exit 0） |
| 3 | `.venv\Scripts\python.exe -m mypy src` | `Success: no issues found in 60 source files`（exit 0） |
| 4 | `.venv\Scripts\python.exe -m pytest` | **`593 passed, 8 skipped in 90.51s`（exit 0）** |

- 开工基线：`561 passed`（`ruff check`/`mypy` 全绿；`ruff format --check` 当时因总领未格式化的 `LEAD_CHANNEL_PLAN` 红，已由总领修复）。
- 本窗 +14 用例；其余 +18 passed / +8 skipped 来自 A 窗并发新增（P0-R16，commit `ea65d63` 及在写文件）。
- 中间态记录（如实）：23:29 时 `mypy` 曾红 5 条，**全部**在 A 窗未提交的 `src/elysia/soul/expression_service.py`；其提交后转绿。
- 坑位提醒（总领已采纳）：`pyproject.toml` 的 `addopts` 已是 `-q`，命令行**不要再加 `-q`**（会变 `-qq`，吞掉 `N passed` 汇总行）。

---

## 5. 真机取证（总领执行，2026-10-18 23:52 前后，两次均用她真实库）

**第一次**（8799 新面板，验证本卡新代码）：
```
HTTP 200  耗时 4.6s
{"ok": true, "wrote": true, "responded": true, "title": "她回应了",
 "detail": "main：今天啊……还不错，有人来问这句话，就已经算好的一天了～♪ 你呢？",
 "reply": {"id": 1000231, "level": "main", "intent": "回应", ...}}
库内取证: #1000231 23:52:25 [main] 今天啊……还不错，有人来问这句话，就已经算好的一天了～♪ 你呢？
kv[interaction] = {"ts": 1792338741.3087566, "text": "今天过得怎么样"}
```

**第二次**（升级后的 8770 正式面板，验证"生效"）：
```
HTTP 200  4.6s  responded=True
她说: 嗯，是我在说呀～刚才声音有点轻，你听清了吗？
```
（她接住了上下文——TTS 未开，这是真实状态，不是罐装句。）

**总领验收结论**：`/api/status` 的 `channel` 段正确（`say_max_chars=500`、`say_wait_s=10.0`、`auth_required=false` 默认回环行为未变、`push.configured=false`）；首页含"对她说"与 `/api/say`；`test_panel_channel.py` 14 用例全过；未碰 `src/**`。

---

## 6. 实际生效范围

- ✅ **`/api/say` 已在 8770 正式面板生效**（总领停旧 pythonw → 重启计划任务后，第二次真机 4.6s `responded=True`）。
- ✅ 8799 测试面板已由总领关闭，端口已释放。
- ✅ 默认回环行为未变：8770 现在**不要求 token**（`auth_required=false`）。
- ⚠️ **手机"能看/能按"还需以非回环方式启动面板**：8770 目前按原计划任务参数启动，仍是 `127.0.0.1`（手机访问不到）。
  要用手机：面板加 `--host 0.0.0.0` 启动（会强制 token，输出/文件里取 `data/run/panel_token.txt`），
  手机打开 `http://<电脑 Tailscale/内网 IP>:8770/?token=<token>`；推荐 Tailscale/ZeroTier，别开公网端口。**这一步涉及暴露面，需用户拍板后再改启动方式。**
- ⚠️ **推送（步 3）`configured=false`**：代码就绪但用户尚未配 webhook。写 `data/run/push_url.txt`（或设 `ELYSIA_PUSH_URL`）后 3 秒内自动生效（无需重启面板）。
- ⚠️ **推送只在面板进程活着时工作**：24h 推送需给 `push_notifier` 建计划任务 —— 总领已记为后续卡，本卡不追加。

---

## 7. 未做与已知限制

1. 未配置任何 webhook ⇒ 推送功能**待用户选渠道**（Bark/Server酱/Telegram；格式见 §2 步 3）。
2. 推送依赖面板常驻；独立计划任务未建（后续卡）。
3. 手机直连需用户拍板 `--host 0.0.0.0`（暴露面）；本卡只提供能力与鉴权护栏，不擅自改启动参数。
4. token 文件权限继承目录默认，未做额外 ACL 加固；安全边界 = Tailscale/内网 + 不暴露公网。
5. 首次 `--host 0.0.0.0` 大概率触发 Windows 防火墙提示（只允许私有网络）。
6. 面板 `POST /api/say` 是阻塞式（最多 10 秒）——她用主声时通常 1-5 秒返回；若 LLM 慢于 10 秒，如实返回"她这次没有开口"（她仍会在稍后落库一句，只是这次请求没等到）。
7. 未修改 `elysia_dash.py`（总领批准），状态采集仍只读 `data/`，与本卡写入路径同源。
8. 无真机 webhook 正向取证（本地假 receiver 已覆盖 POST 载荷与去重）；真渠道需用户 URL 到位后补一次。

---

## 8. 建议验收步骤（总领已执行完毕）

1. 用新代码在空闲端口起面板（如 `--no-open --port 8799`），不碰 8770、不碰她；
2. 记录 `expression_log` 最大 id → `POST /api/say` → 读返回 → 再查 `id > before` 的新行；
3. 确认 `responded=true` 且库里真有新表达（已取证：`#1000231`）；
4. 关闭临时面板；升级正式面板（只重启面板进程，不动她）；
5. 默认回环下 `auth_required=false`；另起 `auth_token` 实例验证无 token 401、有 token 200。

---

## 9. 自检写作用域

本卡变更仅 4 个文件，全部在任务卡写作用域内：
`scripts/elysia_panel.py`、`scripts/elysia_panel.html`、`scripts/push_notifier.py`（新建）、`tests/unit/test_panel_channel.py`（新建）。
`git show --stat c2f7884` 可复核；无顺带优化、无越界文件。

---

## 10. 发现但未修（已口头报总领）

1. A 窗 `tests/unit/test_reply_rhythm.py` 的 ruff 红（作用域外，总领已转告）。
2. 8770 曾是旧代码，需重启**面板**（非她）才生效 —— 已由总领完成。
3. `README.md` / `docs/OPERATION_GUIDE.md` 在本窗开工前即为 `M`，未触碰。
4. `ruff format --check` 会格式化 Markdown 代码块（`LEAD_CHANNEL_PLAN` 当日的红即此机制），以后新文档写完顺手跑一次。
