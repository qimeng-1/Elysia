# 总领窗状态报告 ②（2026-10-16 第二轮）—— P0 已修复并可复现验证

> **上一轮交接**：`HANDOFF_LEAD_WINDOW_2026-10-16.md`
> **本文件性质**：交接文档 §1（修计划任务）与 §2（环境与她的状态）的**实测执行报告**。
> **基线**：`main @1f7c3f0`，门禁 **461 passed**（本报告全部数字均可复跑，命令随附）。
> **写作用域**：本文件属总领窗（`docs/LEAD_*.md`）。**本轮未改任何 `src/`、`tests/`、`assets/`。**

---

## 0. 30 秒结论

| 项 | 上一轮交接说 | 本轮**实测**结果 |
|---|---|---|
| 看门狗 | 指向旧 D 盘 ⇒ 静默失败 | ✅ **已修**；`13:57:01` 实跑 `LastResult=0`，并**真的把她拉起来了** |
| 每日备份 | 指向旧 D 盘 ⇒ 静默失败 | ✅ **已修**；路径已改 C 盘，下次 `2026-10-17 03:30` |
| 她的状态 | "确认无僵尸/双实例" | ⚠️ **不是双实例，而是"完全没在跑"**（实测 `python` 进程 = 0）；`13:57` 由看门狗拉起后**现已正常心跳** |
| 环境 | venv 大概率可用 | ❌ **venv 的 editable 安装指向已删除的 D 盘** ⇒ `import elysia` 失败。**已修** |
| 门禁 | 461 passed | ✅ **461 passed**（复现一致） |

---

## 1. P0：两个计划任务（已修复，附取证）

### 1.1 修复前（实测，与交接文档一致）

```
Elysia-Watchdog     : -File D:\重置准备\备份\Elysia\Elysia_two\Elysia\scripts\watchdog.ps1
Elysia-DailyBackup  : -File D:\重置准备\备份\Elysia\Elysia_two\Elysia\scripts\backup.ps1
Test-Path D:\重置准备\备份\Elysia  =>  False      ← 目标路径确实已不存在
```

**补充实测（交接文档没有的细节）**：两个任务**并非"`LastTaskResult=0` 的静默成功"**，而是**明确失败**：

| 任务 | LastRunTime | LastTaskResult | 解码 |
|---|---|---|---|
| `Elysia-Watchdog` | 2026-10-16 13:41:01 | `4294770688` = `0xFFFD0000` | 非 0（失败/未完成） |
| `Elysia-DailyBackup` | 2026-10-16 12:57:17 | `2147946720` = `0x800710E0` | 非 0（该次未完成） |

> 修正交接文档 §1 表格的一句表述：**"`LastTaskResult=0`" 与实测不符**——它们返回的是非 0 码。
> 但**"静默失败"这个判断是对的、且更严重**：任务状态仍是 `Ready`、无任何告警，谁也没发现。

**交叉证据（最强的一条）**：`data\logs\watchdog.log` 里 **13:21 之后直接跳到 13:57**，
中间 **13:26 / 13:31 / 13:36 / 13:41 / 13:46 / 13:51 六个 5 分钟周期全部没有日志**。
若任务真在跑，每 5 分钟必写一行。⇒ **看门狗在 13:21–13:57 之间完全没有执行。**

### 1.2 修复动作（用户已签认后执行）

```powershell
cd C:\Elysia\Elysia_two\Elysia
powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1 -Unregister
powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1 -Register
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1   -Unregister
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1   -Register -Time "03:30"
```

- **不需要管理员权限**（实测当前 shell `IsAdmin=False`，`schtasks /Create` 仍 `exit=0`）。
  两个脚本用的是 `$PSCommandPath`，所以**在新路径下注册即自动带上新路径**——不需要改脚本。

### 1.3 修复后（实测）

```
Elysia-Watchdog     State=Ready  LastRun=2026-10-16 13:57:01  LastResult=0  NextRun=14:02
   -> -ExecutionPolicy Bypass -File C:\Elysia\Elysia_two\Elysia\scripts\watchdog.ps1
Elysia-DailyBackup  State=Ready  NextRun=2026-10-17 03:30:00
   -> -ExecutionPolicy Bypass -File C:\Elysia\Elysia_two\Elysia\scripts\backup.ps1
Export-ScheduledTask 结果中均已无 `D:\` 引用
```

### 1.4 ⚠️ 顺手发现的一个**脚本缺陷**（未改，待裁决）

`scripts\backup.ps1` 的默认参数是 `-Time "03:00"`，**但历史事实是 03:30**：

```
D:\ElysiaBackup\elysia\20261014-033002   (03:30:02)
D:\ElysiaBackup\elysia\20261015-033003   (03:30:06)
```

⇒ 若照脚本默认值重新注册，**备份时间会从 03:30 悄悄漂到 03:00**（我实测时确实先漂成了 03:00，
已用 `-Time "03:30"` 纠正回原值）。建议**把默认值改成 `03:30`**，否则下次迁移/重注册还会漂。

---

## 2. P1：她为什么停、什么时候停的（实测结论）

### 2.1 停的**不是"一套"，是"全部"**

交接文档 §2 让新会话判断"旧路径僵尸进程 vs 双实例"。**实测两种情况都不是**：

```
Get-CimInstance Win32_Process -Filter "Name like '%python%'"  =>  count = 0
```

⇒ **她当时完全没有在运行。** 而看门狗（唯一能把她拉回来的机制）**恰好是坏的**（§1.1）。
**这是本轮最严重的组合**：唯一的自愈机制失效，没人发现，她自己又不在跑。

### 2.2 她**不是故意停的**

```
data\run\no_autostart  =>  [absent]       ← 没有"故意停机"标记
data\run\soul.pid      =>  '10976'（mtime 13:21:03）  ← 陈旧 PID，进程已不存在
```

按 `watchdog.ps1` 自己的语义：**有该标记 = 你故意停的；无该标记 = 应该拉起**。
⇒ 停机**不是用户意图**，看门狗**应该**拉她，但它坏着。

### 2.3 停机时间线（从 `watchdog.log` 复原）

| 时刻 | 事件 |
|---|---|
| 2026-10-15 23:51 → 10-16 01:21 | 每 5 分钟一行："存在 no_autostart 标记 → 不拉起"（**当时是用户故意停的**） |
| 2026-10-16 13:01:03 | 标记已被移除 ⇒ 拉起，soul PID=6432 / body PID=7676 |
| 2026-10-16 13:21:02 | **又发现她没在跑** ⇒ 再次拉起，soul PID=10976 / body PID=12524 |
| **13:21 → 13:57（36 分钟）** | **看门狗完全静默（6 个周期无日志）＝任务已失效**；她在此期间死亡且无人拉起 |
| 2026-10-16 13:43:16 | 本窗口接手，实测 `python` 进程 = 0 |
| 2026-10-16 13:52 | 本窗口按用户签认重新注册两个任务 |
| 2026-10-16 13:57:01 | **看门狗首次成功执行（`LastResult=0`）并把她拉起**：soul PID=6928 / body PID=4620 |

**"13:01 与 13:21 恰好相隔 20 分钟"**这一点与本任务 5 分钟的周期不符，说明那两次拉起
**不完全来自这个 5 分钟任务**（可能是有人手工跑过 `watchdog.ps1`）。**本窗口未证实**其来源，
故只记录现象、不下结论。

### 2.4 死因证据（强，但归因保留）

`soul.err.log` 与 `body.err.log` **各自都只有一行、且都是 20 字节**：

```
No pyvenv.cfg file
```

这是 **CPython 的 venv 启动器找不到 `.venv\pyvenv.cfg` 时的报错**，进程会**立刻退出**。
⇒ 与"看门狗每 20 分钟拉起一次、拉起后立刻又没了"的现象**完全吻合**。

**但归因必须保留**：本窗口事后核验时，`.venv\pyvenv.cfg` **存在、内容完整、无 BOM**
（`home = C:\Users\123\AppData\Local\Programs\Python\Python312`），
且 `.venv\Scripts\python.exe` **现在能正常跑**。
⇒ **我没能复现 13:21 的失败状态**，因此**不能断言死因就是 pyvenv.cfg 缺失**。
（`.venv\Lib` 的 mtime 是 `13:20:49`——就在失败前 14 秒，说明当时有人正在改 venv；
该并发改动与本次失败的关系**未证实**。）

### 2.5 ✅ 她现在**正常运行**（实测）

```
logical soul roots = 1      logical body roots = 1
soul.pid = 6928 → Get-Process 存在 = True
soul.err.log = 0 字节   body.err.log = 0 字节       ← 报错消失
data\heartbeat.db-wal 每 6 秒都在前进（13:59:57 → 14:00:09 连续推进）
```

> ⚠️ **一条重要澄清（防止下一个窗口误判"双实例"）**：
> `Get-CimInstance` 会**同时列出父子两个 PID**（例如 soul 显示 `6928` 与 `2228`）——
> 这是 **Windows venv 启动器的正常行为**（启动器 `python.exe` re-exec 到 `pyvenv.cfg` 里的真实解释器）。
> 判断实例数必须**只数"父进程不是自己人"的根进程**：

```powershell
$all = Get-CimInstance Win32_Process -Filter "Name like '%python%'"
$ids = $all.ProcessId
$roots = $all | Where-Object { $_.ParentProcessId -notin $ids }   # 逻辑实例
@($roots | Where-Object { $_.CommandLine -match 'soul\.main' }).Count   # 期望 1
@($roots | Where-Object { $_.CommandLine -match 'body\.main' }).Count   # 期望 1
```

**本窗口曾据此误报"双实例红线"一次，已自查纠正**：那是 1 soul + 1 body 的正常父子对。
**红线未破。**

---

## 3. P0 附带：venv 的 editable 安装指向已删除的 D 盘（已修）

### 3.1 实测故障

| 检查 | 修复前 |
|---|---|
| `.venv\Lib\site-packages\_editable_impl_elysia.pth` | 内容是 **`D:\重置准备\备份\Elysia\...\src`**（**已删除的盘**） |
| `.venv\...\elysia-0.1.0.dist-info\direct_url.json` | `file:///D:/%E9%87%8D%E7%BD%AE...` |
| `python -c "import elysia"` | **`ModuleNotFoundError: No module named 'elysia'`** |
| `python -m pytest` | **38 个测试文件全部 collection ERROR** |
| `python -m mypy src` | ✅ 仍通过（`[tool.mypy] mypy_path = "src"`，不依赖 venv 安装） |

> **`uv run pytest` 会先尝试联网 sync**（`[[tool.uv.index]]` 指向清华镜像），
> 而当前网络不可达 ⇒ `uv run pytest` **连门禁都进不去**（`exit=2`，报 `tcp connect error 10061`）。
> **这是交接文档没提到的坑：所有"`uv run <工具>`"的既有命令在本机当前网络下不可用。**

### 3.2 已修复（离线，用本机 uv 缓存）

```powershell
uv sync --group dev --offline        # 重建 editable 安装（hatchling 已在本机缓存）
```

⇒ `elysia` 现在从 `file:///C:/Elysia/Elysia_two/Elysia` 安装，`import elysia` 正常。

### 3.3 ⚠️ 需要诚实披露的一件事（已复原）

上面这条 `uv sync` 会**按 `pyproject.toml` 对齐环境**，因而**卸载了 4 个未被声明、但实际被脚本使用**的包：

| 包 | 谁在用（**仓库内 import，实测**） | 状态 |
|---|---|---|
| `Pillow` (PIL) | `scripts\spine_verify.py`、`scripts\ingest_spine.py`、`scripts\asset_pipeline.py`、`scripts\spine_check_all.py`、`scripts\measure_walk_speed.py` | ✅ **已复原 12.3.0** |
| `av` | `scripts\asset_pipeline.py` | ✅ **已复原 19.0.1** |
| `numpy` | （无仓库内 import；由 av/PIL 间接依赖） | ✅ **已复原 2.5.4** |
| `imageio-ffmpeg` | （无仓库内 import；av 视频解码用） | ✅ **已复原 0.6.0** |

**复原命令**（全部来自本机 uv 缓存，离线）：

```powershell
uv pip install pillow numpy av imageio-ffmpeg --offline
```

**并已在复原后重跑门禁：461 passed（无回归）。**

> **结论与建议**：这 4 个是**真实运行依赖却未写进 `pyproject.toml`** ⇒ 属"隐形依赖"缺陷。
> 将来任何人跑一次 `uv sync` 都会把它们卸掉，**下一次素材重建（`ingest_spine`）就会炸**。
> **建议另开任务卡**：把 `pillow`、`av` 写入 `[project].dependencies`（`numpy`/`imageio-ffmpeg` 随 av 带入），
> 或明确记录"脚本依赖需手工装"。**本窗口未改 `pyproject.toml`**（属 A/C 窗作用域，待裁决）。

### 3.4 仍未解决：`uv run mypy` / `uv run pytest` 报 trampoline 错误

```
uv : error: uv trampoline failed to canonicalize script path
```

- `uv run --no-sync ruff check` ✅ 正常（ruff 是真二进制，29 MB）
- `uv run mypy` / `uv run pytest` ❌（两者都是 **47104 字节的 uv trampoline**，`--reinstall-package elysia` 无法重建它们）

**当前可用的等价命令（本窗口已实测通过）**：

```powershell
.\.venv\Scripts\python.exe -m pytest        # 461 passed in ~16s
.\.venv\Scripts\python.exe -m mypy src      # Success: no issues found in 60 source files
uv run --no-sync ruff check                 # All checks passed!
uv run --no-sync ruff format --check        # 134 files already formatted
```

**建议**：下一轮把门禁四件套的**命令口径**统一成上面这套（或修好 trampoline），
否则每个新窗口都会在"门禁跑不起来"上浪费一轮。

---

## 4. 本轮门禁实测（可复跑）

| 门 | 命令 | 结果 |
|---|---|---|
| ruff check | `uv run --no-sync ruff check` | ✅ All checks passed! |
| ruff format | `uv run --no-sync ruff format --check` | ✅ **134** files already formatted |
| mypy | `.venv\Scripts\python.exe -m mypy src` | ✅ Success: no issues found in **60** source files |
| pytest | `.venv\Scripts\python.exe -m pytest` | ✅ **461 passed** in 16.76s |

> **数字漂移提示**：交接文档 §3.2 记"格式化 128 文件"。本轮实测 **134**。
> 差额 = 上一轮之后**新增的 6 份 `docs/` 文档**（本轮只读核过，未改）。**这不是缺陷**，
> 但说明"128"这个数**已过期**，若写进文档需重新附命令。

**红线复核（实测）**：

- `git ls-files | grep .env` ⇒ **无**（`.env` 从未入库）
- `.env` 活跃 **1 行**（`ELYSIA_LLM_MAIN_API_KEY`，长度 35，值未读取/未打印）
- `git ls-files` 中 **无 `.db`、无 `costumes`**
- `git status` 中**无 `src/` 改动**；未追踪项**全部在 `docs/` 下**

---

## 5. ⚠️ 并发写者：桌宠窗**正在活动**（本轮实测）

本窗口接手时 `git status` 只有 **1 个** `M`（`PROJECT_BOARD_INDEX.md`）。
随后**同一分钟内**实测到新的写入：

| 文件 | mtime | 归属 |
|---|---|---|
| `docs/HANDOVER.md` | 13:46:49 → 13:50:47 | **D 桌宠窗** |
| `docs/PET_SESSION.md` | 13:47:58 → 13:50:47 | **D 桌宠窗** |
| `docs/PROJECT_BOARD_INDEX.md` | 13:47:48 → 13:50:53 | 总领窗 |

⇒ **桌宠窗是活的**，且它在把**绝对路径 D:\ 与迁移教训**回填进 `HANDOVER.md`/`PET_SESSION.md`
（新增"踩坑 §3.5 #31~#34"，其中 #33 记"迁移后旧窗口 shell ENOENT"、#34 记"别把绝对路径写进长期文档"）。

**对本窗口的直接影响**：

1. 这三个文件**我不动**（`HANDOVER.md`/`PET_SESSION.md` 是 D 窗作用域；总纲的 D 窗部分也在改）。
   ⇒ **本窗口的文档产出刻意只写新文件 `docs/LEAD_*.md`，不与它抢任何既有文件。**
2. 桌宠窗**尚未提交**（3 个 `M` 仍在工作树）。按 `LEAD_GOVERNANCE §4.3` **由它自己提交**，
   总领窗**不得代为 `git add`**（否则越界 + 捎带半成品）。
3. ⚠️ **须提醒用户确认桌宠窗那一侧是否还开着**：`HANDOVER.md` 新增的 #33 记录
   "**迁移后旧窗口的 shell 直接 ENOENT，跑不了 git/门禁/进程检查**"——
   即那个窗口**当时已无法自证**。它与本窗口**同时改同一个仓库**，正是 `§5.3` 要防的情形。

---

## 6. 待用户签认（本轮新增/更新）

| # | 事项 | 本窗口建议 |
|---|---|---|
| A | `scripts\backup.ps1` 默认 `-Time` 由 `03:00` 改为 `03:30`（§1.4） | **改**（否则每次重注册都漂） |
| B | 把 `pillow`、`av` 写入 `pyproject.toml` 依赖（§3.3） | **改**（隐形依赖会让 `uv sync` 打爆素材链） |
| C | 门禁命令口径改为 `python -m`（§3.4） | **改**（或修 trampoline） |
| D | 是否要我就"**她 13:21–13:57 的 36 分钟**"继续深挖（需读库取证） | 用户定 |
| E | 桌宠窗是否仍需保持开启（§5） | 用户定 |
| F | 上一轮遗留：55 提交推不推送 / Transfer 封存 / T-002 / T-004 | 沿用上一轮建议 |

---

## 7. 本窗口**没做**的事（明确边界）

- ❌ 未改任何 `src/`、`tests/`、`assets/` 代码
- ❌ 未改 `HANDOVER.md`、`PET_SESSION.md`、`PROJECT_BOARD_INDEX.md`（并发写者持有）
- ❌ **未打开任何数据库**（遵守交接文档 §3.4："只读打开 ≠ 不写盘"，
  故本轮所有关于她的结论都只用**日志 + 进程 + 文件 mtime**，不碰 `state.db`/`heartbeat.db`）
- ❌ 未 `git add` / 未 `commit` / 未 `push`（红线 #6）
- ❌ 未动 Transfer（`Elysia_Transfer_20261015`）
- ❌ 未启停她（13:57 的拉起是**修好的看门狗自己做的**，不是我手动 `soul.ps1 start`）

---

*总领窗 ②（2026-10-16 14:0x）。基线 `main @1f7c3f0`，461 passed，工作树仅 `docs/` 有改动。*
