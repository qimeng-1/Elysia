# OUT-CHANNEL — P0-R20 Telegram 双向桥交接单（通道窗 · 2026-10-19）

> **任务号**：P0-R20（共享任务板 `task-17`）
> **用户原话（2026-10-19）**：*"我需要双向聊天"*；手机+电脑都有 VPN、可共用中继。
> **选型**：Telegram Bot（long polling，无 webhook、不开入站端口）。
> **总领验收**：**通过（有条件）** —— 数据源偏离经核实后批准（§3），气泡碎片防御已补齐（§4）。
> **真机验证**：**未做**。本机到 `api.telegram.org` 不可达（§8），非代码问题；等用户修网络后由总领执行。

---

## 1. 提交

| commit | 标题 | 内容 |
|---|---|---|
| `a45e5ab` | feat(telegram): 双向聊天桥——long polling 收进她的耳朵 + 她的气泡回手机 (P0-R20) | 三个文件首版（624+97+448 行） |
| `e01755b` | fix(telegram): 气泡碎片防御 + 补 time import（P0-R20 补） | 碎片防御 + F821 + 6 条新用例 |

另：`c65efb1` 是总领修的**测试环境依赖**（`PYTHONIOENCODING`），不算本窗产出，但本卡门禁依赖它。
本窗改动文件（`git show --stat a45e5ab e01755b` 可复核）：

| 文件 | 说明 |
|---|---|
| `scripts/telegram_bridge.py` | 新建（最终 676 行，含碎片防御） |
| `scripts/register_telegram_task.ps1` | 新建，UTF-8 **BOM**（首字节 `239,187,191`），默认只打印不注册 |
| `tests/unit/test_telegram_bridge.py` | 新建，523 行，**23 用例**（含 6 条取消测试） |

未碰：`src/**`、`data/*.db` 的手改、`.env`、`pet.py`、`assets/**`；未启停她、无 `job_kill`。
写她的库**仅** `kv['interaction']`，且复用面板 `elysia_panel.write_interaction`（单一写入实现）。

---

## 2. 功能

### 收：Telegram → 她
- `getUpdates` **long polling**（`timeout=25`；不用 webhook、不开任何入站端口）。
- 白名单 chat：`data/run/telegram_chat.txt`。文件不存在 ⇒ **首条消息自动绑定**并回"已绑定"；
  其它 chat_id ⇒ **不写她的库、不回复**，仅记日志。
- 文本消息 ⇒ 复用 `write_interaction` 写 `state.db` 的 `kv['interaction']`（她 1Hz 读到即强制开口）。
- 校验：>500 字**拒绝并告知**（不静默截断）；空/控制字符过滤；非文本（图片/贴纸）忽略。
- 指令：`/status` 回她的简况（醒着/心跳年龄/年龄/今天几句/模式）；`/start` 帮助；
  其它 `/命令` 只回提示、**不写她的库**。

### 发：她 → Telegram
- 轮询 `thought_log(kind='expression')` 的新气泡，**一条一个 `sendMessage`**（多句自然分气泡）。
- 游标**只在内存**：首次启动只建基线，**不补推历史**（重启不刷屏）。
- 发送失败**静默降级**：气泡留在内存待发队列，下一轮重试；游标不倒退、不重复。
- 网络失败**指数退避**：1→2→4→8→16→32→60s 封顶，连续失败只等、绝不退出。
- PID 单实例护栏：PID 存在且命令行是 `telegram_bridge` 才拒启（防双发；PID 复用不误判）。

### 配置与安全
- token：`data/run/telegram_token.txt`（**容忍 UTF-8 BOM**；形状不对 ⇒ **明确拒绝启动**，不半残）。
  日志只打前 8 位；异常消息里**不含 token**（URL 不进入任何日志/异常）。
- 每次请求现建 opener + 连接层失败绕过代理直连重试一次（P0-R11 死代理缓存教训）。
- 前台可跑；`--once` 跑一轮便于联调；`register_telegram_task.ps1` 建 task 定义但**默认不注册**
  （登录触发 + 每 2 分钟幂等重试，靠 PID 护栏防重复）。

---

## 3. ⚠️ 数据源偏离（总领已核实批准，**不要改回 expression_log**）

发回手机的数据源是 **`thought_log(kind='expression')`**，不是任务卡原建议的 `expression_log`。理由：

1. **多句**：R19 把一次 LLM 回复在句边界拆开，第一句立即、其余逐句写 `thought_log`
   （`expression_service.py:380-383`、`flush_utterances:434-442`）；`expression_log` 只有一行整段。
2. **"选择不回"（R19 b 方案）**：`heartbeat.py:1130-1135` 落的是 `expression_log.llm_text=''` 的空行；
   她真正说的"现在不太想说话"只进 `thought_log`（`expression_service.py:397-399` 跳过 expression 落库）。
   ⇒ 只读 `expression_log` 会让"她说了、只是不想回"在手机上变成沉默，等于把 b 方案抹掉。
3. **总领真机核实**（2026-10-19）：
   ```
   thought_log(kind=expression) = 713
   expression_log               = 681（其中 1 条空文本）
   #1000309 '你还没睡吗？'
   #1000308 '夜深了，我正闲着发呆呢。'   ← 同一句话被拆成多行进 thought_log
   ```
   结论："你对。保留 `thought_log`，不要改回 expression_log。"（总领原话）

---

## 4. 气泡碎片防御（e01755b）

**根因**在 R19 的 `split_utterances`（会把 `…`/`♪` 切成独立段），A 窗已用 R19d（`c6c4425`）修根因：
实义字符下限 + 并入相邻句。本窗的过滤是**桥侧独立双保险**（桌宠/推送若有同样问题也各自兜）。

规则（`is_bubble_fragment`）：
- **0 个实义字符**（纯标点/符号：`…`、`♪`、`。`）⇒ 一定是碎片：
  - 待发队列里有前一条 ⇒ **并入它**；
  - 前一条**已发出** ⇒ `editMessageText` **并进上一条消息**（不新增气泡）；
  - 没有前一条 ⇒ 挂起（carry）等下一段（**开头碎片挂到下一段**）；超过 10s 仍无可并才单独发（内容不静默丢）。
- **多段回复里的 1 字段** ⇒ 并入相邻段（与 R19 下限语义一致，兜旧数据）。
- **整条回复就是一个 1 字短句**（真库 `thought_log` 有 `'嗯。'`）⇒ **保留**。这是她真实的一次开口，
  不能因为"短"就吞进上一条。

> 阈值语义与总领最初的"<2 实义字符"略有收窄（0 vs <2）：实测她的真实气泡里有 `'嗯。'`
> （1 个实义字符），按 <2 会被吞掉。故"0 = 纯符号碎片，1 字整句保留"。若总领要求严格 <2，改一个常量即可。

取消测试（均实测变红，文件哈希 `D10492B3…90D8D13` 证前/证后一致）：
1. 去掉碎片过滤 ⇒ `test_fragment_segments_never_sent_alone` 红：`assert 3 == 2`（发出独立 `'…'`）。
2. 让"并入上一条"永不生效 ⇒ `test_trailing_fragment_folds_into_last_sent` 红：
   `assert [] == edits`（`♪` 没有被并进上一条，会退化成单独气泡）。
3. 验收指定输入 `['…名字…','…','我有点想你了。']` ⇒ 只发 2 条：`['…名字……','我有点想你了。']`，`'…'` 不单独成气泡。
4. 覆盖 `_flush_carry_if_due` 两行（见 §5 F821）；`'嗯。'` 保留有独立用例。

---

## 5. 🔴 修掉一个会让桥运行时崩溃的 bug（总领发现，e01755b）

`ruff check` F821：`import time` 在早前 `ruff --fix`（当时未使用）时被删掉，而
`_absorb_parts` / `_flush_carry_if_due` 会用 `time.monotonic()` ⇒ 一旦走到就是
`NameError: name 'time' is not defined`，**桥当场崩**。首版 17 个用例走的是假 HTTP 层、没踩到这两条路径。

已修：补 `import time` + **新增 6 条用例**覆盖碎片防御的两条分支（含 carry 超时路径）。
教训（与总领一致）：**门禁真有用；"我的用例全绿"不等于"代码没 bug"——每条新分支都要有用例踩到。**

---

## 6. 取消测试总览（6 条）

| # | 用例 | 还原旧写法后的红证 |
|---|---|---|
| 1 | `test_whitelist_blocks_non_owner_and_never_writes` | `- ignored / + delivered`（陌生人的话被写进她的库） |
| 2 | `test_bom_token_is_read_correctly` | `BridgeConfigError: token 形状不对（\ufeff1234567…）`（utf-8-sig + lstrip 两层一起去掉） |
| 3 | `test_send_failure_is_silent_then_retries_pending` | `TelegramError: sendMessage 不可达（URLError）` 从 `poll_expression_once()` 冒出 |
| 4 | `test_first_poll_does_not_backfill_history` | `assert 100 == 0`（100 条历史气泡全发） |
| 5 | `test_fragment_segments_never_sent_alone` | `assert 3 == 2`（发出独立 `…`） |
| 6 | `test_trailing_fragment_folds_into_last_sent` | `assert [] == edits`（`♪` 单独成气泡） |

离线全链路（假 HTTP 层，真实 `TelegramAPI` 构造/解析）：
收到「今天过得怎么样」→ 写 kv → 她回两句（`thought_log`）→ 两条 `sendMessage` 气泡；
另覆盖旧格式一行多句拆 3 条、`/status`、超长拒绝、未知命令不转发、退避序列 1→…→60。

---

## 7. 门禁四件套实测输出（交付状态，2026-10-19 03:3x）

| # | 命令 | 实测 |
|---|---|---|
| 1 | `uv run --no-sync ruff check` | `All checks passed!`（exit 0） |
| 2 | `uv run --no-sync ruff format --check` | `179 files already formatted`（exit 0） |
| 3 | `.venv\Scripts\python.exe -m mypy src` | `Success: no issues found in 60 source files`（exit 0） |
| 4 | `.venv\Scripts\python.exe -m pytest` | **`624 passed, 11 skipped in 87.66s`**（exit 0） |

基线核算：总领合并 R19d 后基线 **618 passed + 11 skipped**；本窗新增 6 条（桥 23 条 vs 交付前 17 条）
⇒ 624 = 618 + 6，**零回归**。开工时基线 599+11；本卡累计 +17（首版）+6（补）= +23 条桥用例。
（坑位提醒：`pyproject.toml` 的 `addopts` 已含 `-q`，命令行别再叠加 `-q`——会吞掉汇总行。）

---

## 8. 真机验证：**未做**，且原因不在代码

总领实测这台电脑**连不上 `api.telegram.org`**：
```
直连 = 超时
走用户代理(localhost:7890) = CERTIFICATE_VERIFY_FAILED
同一代理下 ipify = 200、deepseek = 可达 ⇒ 代理本身是好的，只有 Telegram 不通
```
⇒ 用户的 Clash 节点/规则对 Telegram 有问题。**"Telegram 网络不可达"是本卡真机验证的已知前置条件。**
用户修好网络（或换能用 Telegram 的节点/规则）后，真机步骤：

```powershell
# 1) 放 token（UTF-8，带不带 BOM 均可）：data\run\telegram_token.txt
# 2) 联调一轮（只收一次 + 发一次，不常驻）
.\.venv\Scripts\python.exe scripts\telegram_bridge.py --once
# 3) 常驻（先别注册任务）
.\.venv\Scripts\python.exe scripts\telegram_bridge.py
# 手机给 bot 发第一条 → 日志"首次绑定"、收到"已绑定"；再发一句 → 她 1 秒内读到，
# 她的回应 1–3 条气泡回到 Telegram；发 /status 看她的简况。
# 4) 验收后注册计划任务（登录启动 + 每 2 分钟幂等重试；当前未注册）
powershell -ExecutionPolicy Bypass -File scripts\register_telegram_task.ps1 -Register
```

安全提醒：token 等于机器人控制权（拿到就能冒充桥接）；首条消息绑定后若想换人，
删 `data\run\telegram_chat.txt` 再发一条即可重绑。

---

## 9. 未做与已知限制

1. **真机未验**（§8 网络前置条件）。
2. 计划任务**建而未注册**（按卡要求，注册由总领真机验收后执行）。
3. 聊天/推送依赖桥进程常驻；未做开机自启直到显式 `-Register`。
4. 群聊不特殊处理：首个说话的 chat 即白名单；群里她会把群当"你"（单关系模型，见通道方案 §5）。
5. 碎片防御在"整条回复只有一个纯符号"的极端情况下，若从未发过任何气泡，超时后会单独发一次（内容不丢）。
6. 无 webhook、无入站端口；token 文件权限继承目录默认，未做 ACL 加固（建议本机/可信目录）。

## 10. 发现但未修

1. `argparse --help` 在 cp936 下曾因 description 含 `↔` 抛 `UnicodeEncodeError`（真机踩到）——
   已修（去字符 + `_harden_console()` + 回归用例）；class 里的 `↔` 是 CP936 外字符，以后写 CLI 文案要留意。
2. `import time` 被 `ruff --fix` 当未使用删掉、后续又用到（§5，已修 + 用例钉住）。
3. 测试环境依赖（总领 `c65efb1`）：子进程编码必须钉死，门禁不得取决于 `PYTHONIOENCODING`。

## 11. 自检写作用域

变更仅：`scripts/telegram_bridge.py`、`scripts/register_telegram_task.ps1`、
`tests/unit/test_telegram_bridge.py`（+ 本交接单）。全部在任务卡写作用域内；
`git show --stat a45e5ab e01755b` 可复核；无顺带优化、无越界文件。
