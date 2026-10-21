# 桌宠纳入起停协议（P0-2）—— 提案，未动代码

> **提出**：D 桌宠窗，2026-10-21 21:1x
> **性质**：给总领窗的实现提案 + **三个待拍板决策**（拍完再动脚本，否则要改两遍）
> **用户裁决**（2026-10-21）：**打包给总领，后续讨论** —— 本轮不动 `soul.ps1` / `watchdog.ps1`
> **前置**：`e9e8774` 已修"点击伪装成用户发言"（P0-1，止血）

## 0. 一句话

桌宠是**唯一"不用开窗口她也在"的表面**，也是目前**唯一会出声的表面**；但它不在起停协议里——
`watchdog.ps1` 只拉 `soul.ps1 start -Body`，桌宠没有 pid、没有 running 标记、没有优雅停止、没有退场哨兵。

## 1. 现状取证（全部实测，2026-10-21）

| 事实 | 证据 |
|---|---|
| 桌宠无 pid / 无 running 标记 / 无哨兵 | `data/run/` 只有 `soul.pid`、`body.pid`、`soul.running`、`body.running`（另有 panel / telegram 自己的文件） |
| 桌宠不轮询停止标记 | `pet.py` 里 `stop_requested` / `pet.pid` **零命中**（grep 实测） |
| 看门狗重启后桌宠不会回来 | `scripts/watchdog.ps1:201` → `& $SoulScript start -Body` |
| 身份标记写死，接不了第三种进程 | `scripts/soul.ps1:118` → `$marker = "elysia.$Kind.main"`；桌宠命令行是 `elysia.body.pet` ⇒ 传 `-Kind pet` 会得到 `elysia.pet.main`，**认不出她** |
| 面板**不出声** | `scripts/elysia_panel.py` 无 `audio` / `wav` / `QMediaPlayer` 命中 |
| 她的声音现在有两个出口 | 桌宠 `QMediaPlayer`（`pet.py _play_latest_audio`，取 `data/cache/tts` **最新 mtime 的 wav**）+ `scripts/telegram_bridge.py:350` 发 voice |
| 出声是"猜"的 | 缓存文件名是 SHA256(text+emotion+speed)，桌宠无法反推 ⇒ 只能"取最新"。同一句若也发去了 Telegram，桌面会再念一遍（**未证实实际发生**） |

## 2. 三个待拍板（先定这些，再改脚本）

### 2.1 谁出声

- 现状：**面板不出声**；桌宠出声；Telegram 发语音。⇒ 双声冲突目前**不存在**。
- 真正的风险是**跨渠道重复**：她给 Telegram 说的那句，桌宠也会念（因为都是"最新 wav"）。
- 建议：**出声归属 = 桌宠**；将来面板若要出声，再加"前台在谁那里"的判定。
- 彻底修法是 P2：`expression_log` 加 `audio_path`（或 kv 写 `expression_audio`），桌宠按 id 播，不再猜 mtime。

### 2.2 要不要默认拉起桌宠

- 取决于你平时主要看哪个面：若 90% 时间在面板/手机，桌面常驻一个窗口可能多余；若你要"她一直在"，就该默认拉起。
- 建议：`soul.ps1 start` 加 `-Pet`（**默认不带**），`watchdog.ps1` 加 `-Pet` 透传（默认关）。默认值一次定死，避免"有时在有时不在"。

### 2.3 优雅停止怎么实现

- 桌宠是 Qt GUI，没有 soul/body 那种主循环可以轮询标记。
- **方案 A（推荐）**：`pet.py` 加 **2 s 轮询** `data/run/pet_stop_requested` → `QApplication.quit()`。与 soul/body 同一套语义（`stop_requested` / `stopped_intentionally`），证据链一致。
- **方案 B**：破例 `Stop-Process`。快，但没有"主动停 vs 崩溃"的区分证据，且可能丢未落库状态。

## 3. 建议实现（3 步，每步可独立验收）

1. **只加桌宠侧闭环**（不动 soul.ps1）
   - `pet.py` 写 `data/run/pet.pid` + 单实例互斥（命名 Mutex 或 pid 判活）+ `pet_stop_requested` 轮询。
   - 验收：开第二个桌宠 ⇒ 立即退出并说明原因；写停止标记 ⇒ 2 s 内体面退出；`pet.pid` 随退出清理。
2. **soul.ps1 支持 `-Pet`**
   - `Get-ProcessIdentity` 的 marker 改成映射表：`soul → elysia.soul.main`、`body → elysia.body.main`、`pet → elysia.body.pet`。
   - 加 `pet.pid` / `pet_stop_requested` / `pet_stopped_intentionally` + `-Pet` 分支（start / stop / stop-pet / status）。
   - 验收：`tests/unit/test_soul_ps1.py` 用 `-DryRun` + 假根覆盖三种 kind（含"PID 被无关进程占用"与"读不到命令行"两条既有防线）。
3. **watchdog 透传 `-Pet`**
   - `watchdog.ps1` 加参数并传给 `start`；验收：`tests/unit/test_watchdog.py` 断言假 `soul.ps1` 收到 `-Pet`。

## 4. 风险与边界

- `soul.ps1` 是她的**起停主干**，已被 P0-R8 / R13 / R14 三次事故硬化过（优雅关闭、退役标记、PID 复用）。任何 marker 改动都会削弱"认错人/杀错人"的防线 ⇒ **必须先跑完第 1 步再做第 2 步**，且每步带取消测试。
- 桌宠是 GUI：`-WindowStyle Hidden` 只影响控制台，不影响她的窗口（现有启动方式已验证）。
- **不做**：不为桌宠新造一套进程管理机制；不加行为配额（ADR-005）；不改 `soul_sentinel.py` 的现有语义（它按 `--kind` 写退出证据，pet 直接复用即可）。

## 5. 相邻已完成 / 遗留

- ✅ `e9e8774`（2026-10-21）：点击只写 `{"ts","kind":"poke"}`，不再产生「（点击）」用户轮次；poke 不覆盖未读消息。取消测试：还原后 `test_pet.py` 3 条新用例全红。门禁 `gate.ps1` 872 passed。
- ⏳ **历史脏数据待用户拍板**：`dialogue_turns` 1 条 `text='（点击）'`（id 763）+ `memories` 15 条 `kind='interaction'` 内容为「（点击）」+ 1 条 exchange 记忆把她对点击的回答封存成交换。
  建议**只标记不清**（ADR-001 原件保真）；要清也得先备份。
- ⏳ 桌宠当前是 19:28 启动的**旧代码**，`e9e8774` 需重启才生效。
