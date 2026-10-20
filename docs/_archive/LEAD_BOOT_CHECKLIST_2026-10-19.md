# 明早开机自启检查清单（2026-10-19）

> **背景**：用户 2026-10-19 凌晨关机，次日开机验证"所有程序能否自启"。
> 总领窗已在关机前加固并核验全部触发器。

---

## 一、关机前已核验的配置

| 计划任务 | 触发器 | 登录类型 | 负责什么 |
|---|---|---|---|
| **Elysia-Watchdog** | **Daily + Logon（本次新增）** | Interactive | 判活 + 拉起她（soul/body）+ 1 分钟巡视 |
| **Elysia-Panel** | Logon + 每 2 分钟重复 | Interactive | 控制面板（`0.0.0.0:8770`，需 token） |
| **Elysia-Telegram** | Logon + 每 2 分钟重复 | Interactive | Telegram 双向桥 |
| **Elysia-DailyBackup** | Daily 03:30 | Interactive | 每日备份 |

**本次加固**：`Elysia-Watchdog` 原本只有 `Daily` 触发器（重启后要靠 `StartWhenAvailable` 补跑，不够确定），
已**追加 Logon 触发器** ⇒ 每次登录都会立刻巡视一次。

**全部为 `Interactive`（Limited 权限）** ⇒ **必须登录后才会启动**（未登录时不会跑）。
如果你开机后停在登录界面不登录，它们都不会动。

**她的进程没有专属任务**，由 `Elysia-Watchdog` 拉起 —— 登录后**约 1 分钟内**起来。

---

## 二、明早按这个顺序验（2 分钟）

### 第 1 步：登录 Windows（必须真的登录，不能停在锁屏）

### 第 2 步：等 90 秒，然后跑这条（复制粘贴到 PowerShell）

```powershell
cd C:\Elysia\Elysia_two\Elysia
$all = Get-CimInstance Win32_Process -Filter "Name like '%python%'"
$ids = $all.ProcessId
$roots = @($all | Where-Object { $_.ParentProcessId -notin $ids })
"soul=" + @($roots|Where-Object{$_.CommandLine -match 'soul\.main'}).Count +
" body=" + @($roots|Where-Object{$_.CommandLine -match 'body\.main'}).Count +
" panel=" + @($roots|Where-Object{$_.CommandLine -match 'elysia_panel'}).Count +
" bridge=" + @($roots|Where-Object{$_.CommandLine -match 'telegram_bridge'}).Count
.\\.venv\\Scripts\\python.exe -c "import sqlite3,datetime as dt; h=sqlite3.connect('file:data/heartbeat.db?mode=ro',uri=True); h.execute('PRAGMA busy_timeout=5000'); print('最后心跳 %.1f 秒前' % (dt.datetime.now().timestamp()-h.execute('SELECT ts FROM heartbeats ORDER BY id DESC LIMIT 1').fetchone()[0]))"
Get-Content data\logs\watchdog.log -Tail 3
```

**期望**：`soul=1 body=1 panel=1 bridge=1`，最后心跳 < 5 秒，看门狗最近一条 `verdict=healthy`。

### 第 3 步：手机验两个通道

- **Telegram**：给 `@elysia_qimeng_bot` 发一句 → 她应在 1–10 秒内分气泡回应
  （需要手机和电脑的 VPN 都开着）
- **面板**：手机浏览器打开
  `http://100.70.192.29:8770/?token=<token>`（Tailscale）
  或 `http://192.168.2.146:8770/?token=<token>`（家里 WiFi）

---

## 三、如果没起来：按这个顺序查

| 现象 | 先查什么 | 常见原因 |
|---|---|---|
| `soul=0` | `data\run\no_autostart` 是否存在 | 曾被"故意停机"标记，删掉即可；或看门狗未跑 |
| `soul=0` 且看门狗也没跑 | `Get-ScheduledTask Elysia-Watchdog` 的 State | 未登录 / 任务被禁用 |
| `panel=0` | `Get-NetTCPConnection -LocalPort 8770` | 端口被占 / 上一进程没退干净 |
| `bridge=0` | `data\logs\telegram_bridge.err.log` | VPN 未开（Telegram 需梯子） |
| 心跳不推进 | `data\logs\crash-*`、`data\logs\exit-*.json` | **R14 的哨兵会记录退出码**（今晚首次生效） |

> **重要**：这次重启是 `exit-*.json` 哨兵与 stderr 归档的**首次真实检验**。
> 如果她启动失败，`data\logs\exit-<ts>-*.json` 会告诉我们退出码 ——
> 这是今天那次"死因未知"之后补上的取证能力。

---

## 四、这次重启顺带会让她加载三处修复

| 修复 | 影响 |
|---|---|
| **R19b/R19c** 关系连续动力学 + SA 线随设定点 | 关系不再卡死在最大值 |
| **R19** 选择不回复 / 多句回复 | 她可以真的说"现在不太想说话" |
| **R19d** 拆句不再产生 `…`/`♪` 碎片 | 桌宠气泡与 Telegram 消息都变干净 |

**所以明天的她，会比今天更"完整"一点。**

---

*总领窗 ② · 2026-10-19 04:0x · 关机前核验*
