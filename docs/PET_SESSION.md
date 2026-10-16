# 桌宠专属会话（怎么用这个窗口）

> **本窗口职责边界（用户 2026-10-16 指定）**：本会话**只负责桌宠板块** ——
> `src/elysia/body/**`、`assets/pet/**`、`docs/PET_*.md`、`docs/HANDOVER.md`。
> 跨板块的规划、派活与验收由**总领窗**负责（工作目录 `C:\Elysia`），
> 总纲见仓库内 `docs/PROJECT_BOARD_INDEX.md`。
> 两条协作铁律：**总领窗只读+规划，写只在本窗口发生**；**同一时刻只有一个窗口在写同一文件**。

> 配套：[HANDOVER.md](HANDOVER.md)（完整交接文档，30 条踩坑记录都在这儿）
> 本文件只解决一件事：**让"调桌宠"这件事跨会话、跨上下文上限地长期做下去。**

---

## 0. 迁移交接（2026-10-16，一次性，完成后可删本节）

**仓库搬家了**（用户操作），新位置：

| 项 | 迁移前 | 迁移后 |
|---|---|---|
| 仓库根 | `D:\重置准备\备份\Elysia\Elysia_two\Elysia` | **`C:\Elysia\Elysia_two\Elysia`** |
| 总领窗工作目录 | `D:\重置准备\备份\Elysia` | **`C:\Elysia`** |
| 入口指针 | 同上目录 `00_总纲入口.md` | `C:\Elysia\00_总纲入口.md` |

**随迁的**：git 仓库（HEAD `1f7c3f0`，分支 `main`）、全部文档、`assets/pet/costumes/`（94.6 MB）
与 `_retired/`（296.8 MB）等 gitignore 内容、`data/`。
**没随迁（仍在原处，路径不变）**：`D:\ElysiaAssets\`（Spine 烘焙台 + 解包源素材 + 探针）、
`D:\图片\Saved Pictures\`（用户原始素材）、`C:\Users\123\Desktop\Elysia_Project\`（旧版工程）、
`D:\ElysiaBackup\elysia\`（备份，**含 `.env`**）。

⚠️ **三条必须知道的事**：

1. **旧窗口（迁移前那个会话）的 shell 坏了**：`C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe`
   与 ripgrep 都 **ENOENT** ⇒ 那个窗口**跑不了 git / 门禁 / 进程检查**，只能读写文件。
   ⇒ 迁移后的**最后一轮文档改动是「未提交」状态**（见 §7），**新窗口第一件事就是跑门禁并提交**。
2. **旧会话记录**（`C:\Users\123\.dsh\sessions\--D-~91CD~7F6E~51C6~5907-…`）里的路径全是旧路径，
   只当历史看；**别照抄里面的绝对路径**。
3. 迁移前最后一次实跑状态（她主声已通、最小可玩闭环）见 §4 —— 但**迁移后无人复核**，
   新窗口要按 §4 的命令重新验一遍。

---

## 1. 上下文满了会怎样

**不用做任何事** —— DSH 会自动把长对话压缩成摘要后继续（旧会话已经历过至少一次压缩）。

### 1.1 想**手动**压缩（用户 2026-10-15 问过）

DSH 内置手动压缩，**入口是命令**（不是 agent 工具，AI 自己按不了）：

| 依据 | 内容 |
|---|---|
| Host 服务 `compaction` | `compactIfNeeded()`（自动）、**`compactNow(agent, signal, sourceCommandId?)`**（手动）、**`compactRegion(start, end, agent)`**（按会话区间压） |
| 会话节点类型 | 同时存在 `compaction`（自动）与 **`manual-compaction`**（手动），说明手动压缩会在会话里留一条记录 |
| 参数名 `sourceCommandId` | 手动压缩是**命令驱动**的 ⇒ 在输入框打 **`/`** 看命令列表，找「压缩 / compact」那一项 |

**怎么做到"只保留近 N 轮"**：底层有 `compactRegion(start, end)` ✓，但界面**不一定**暴露成"保留最近 N 轮"的旋钮 ✗。
**最彻底、最可控的等价做法仍是：开新会话 + 粘贴 §3 那段话** ✓ —— 那样历史真的清零，只留你想要的。

**关键认识：真正的记忆不在对话里，在文件里。**
所以下面第 3 节那段话 + 仓库里的文档，才是"这个窗口"能长期存在的真正原因。

## 2. 这个窗口的定位与纪律

**只做桌宠相关的事**（素材 / 动画 / 交互 / 性能 / 五官细节）。
其他议题另开窗口 —— 混进来会挤占上下文，也会让压缩摘要把主线冲淡。

### 2.0 省 token 的工作方式（用户 2026-10-15 指定）

| 约定 | 说明 |
|---|---|
| **只报结论** | 大表格 / 长日志**写进文件**，回复只给结论与关键数字；命令输出一律截断（`Select-Object -Last N`） |
| **长任务交子代理** | 调研 / 试错 / 批量生成交给 `subagent`（独立上下文，不污染主线），主线只收结果 |
| **要"只留近 N 轮"怎么办** | DSH 的压缩是**摘要式**：会话节点类型里确实有内置的 `manual-compaction`，但**没有"砍到最近 N 轮"的旋钮**。等价做法 = **开新会话** + 粘贴 §3 那段话（= 真正只保留你想要的上下文）✓ |
| **别往对话里塞大素材** | 图片、长表格一律落盘给路径 |

每次改动**必须**做到：

1. **门禁全绿**：`uv run ruff check` / `ruff format --check` / `uv run mypy src` / `uv run pytest`
2. **素材验收**：动过服装包就跑 `uv run python scripts/spine_verify.py`
   （检查画布裁切/尺寸一致/行为齐全/循环差 —— 每一项都对应一次真实事故）
3. **桌面验收**：改完**重启桌宠**（`python -m elysia.body.pet`）并让用户看效果
4. **更新文档**：`docs/HANDOVER.md`（结构/坑）与 `docs/PET_SESSION.md`（状态速查）保持真实
5. **确认调用点**：新增片段/功能后，去 `pet.py` 查 `IDLE_WEIGHTS` / `CLICK_POOL` /
   `DRAG_CLIP_CANDIDATES` / `WANDER_CLIP_CANDIDATES` / `STATE_CLIPS`，去 `anim.py` 查 `BRIDGE_CLIP`
   —— 本项目犯过两次"入库了却没有调用点"的错（`grab`、`notice`）
6. **批量脚本要逐步验证产出**：曾经因为 `argv.indexOf` 返回 -1 的 bug 让 32 个烘焙全失败，
   而我只看摘要没看每步行，还拿旧数据做了半天测量
7. **跑不了门禁就别假装绿**：本仓搬过一次家，旧窗口的 shell 直接 ENOENT ⇒ 那一轮改动只能标"未提交"，
   由下一个能跑命令的窗口补门禁 + 提交

## 3. 一句话恢复上下文（新会话或很久没来时，把这段贴给我）

```
继续桌宠项目（只做桌宠板块）。工作目录 C:\Elysia\Elysia_two\Elysia。
先读 docs/PET_SESSION.md（含 §0 迁移交接）、docs/HANDOVER.md、docs/PROJECT_BOARD_INDEX.md，
然后跑一次门禁（ruff / format / mypy / pytest）+ 桌宠桌面验收，并检查 git 是否有未提交改动，
之后按我的反馈继续改。

红线（2026-10-16 更新）：Spine 与同人素材仅本地使用、不进公开仓库、不外传；
**她的真实记忆已迁到本机，本机是主机器** —— data/ 下是她的一生（不再是测试数据），
任何"恢复/重置/覆盖"动作动手前必须先备份；仍然**同一时刻只允许一台机器跑她**。
```

> 恢复旧会话（不是新开）时，跟我说一句「**继续桌宠**」即可 —— 我会把会话目标重新激活。

## 4. 状态速查（2026-10-16 迁移前最后实测）

| 项 | 值 |
|---|---|
| **新位置** | **`C:\Elysia\Elysia_two\Elysia`**（旧路径已不存在） |
| git | 分支 `main`，HEAD `1f7c3f0`，**领先 `origin/main` 55 个提交**（远程仍是 `91bf19d`） |
| 启动（纯桌宠） | `python -m elysia.body.pet` |
| 启动（接灵魂） | `python -m elysia.body.pet --with-soul-data`（需灵魂在跑） |
| 关闭 | 右键 → 退出 |
| 素材 | **6 套 Spine 服装**（s1~s6）、**50 个片段**、循环片段全部无缝；94.6 MB |
| 行为 | **7 类全部有素材**：待机 / 漫游 / 点击回应 / 拖拽漂浮 / 落地 / 动作后缓冲 / 鼠标移入 |
| 内存 | 纯桌宠 **85~89 MB**；接灵魂时 **137 MB** |
| 门禁 | ruff / format(125) / mypy strict(60) / **pytest 461 passed**（迁移前最后一次全绿） |
| 素材验收 | `spine_verify.py` **全部通过**（触边 0、尺寸一致、行为齐全） |
| 接入工程评估 | **`docs/PET_INTEGRATION_ASSESSMENT.md`**（3 硬伤 + 3 软肋 + 施工计划） |
| **她主声已通** | 2026-10-16 起 `.env` 配了 DeepSeek key ⇒ `expression_log.level = main`（不再只有 micro 罐装句）；实测 3 main / 2 micro。**TTS 未起 ⇒ 只出文字** |
| **最小可玩闭环** | 灵魂(1Hz) + 身体(5s 在场) + 桌宠接灵魂；实测她知道你在场、会主动问候、打字/点击都能到她 |
| 已撤下 | 9 套 AI/立绘服装在 `assets/pet/_retired/`（296.8 MB，可恢复） |
| 清理出的过期物 | `D:\ElysiaAssets\_obsolete\`（45.6 MB，可删） |

**触发逻辑一句话版**：待机池加权轮换（18–45 秒）→ 点她 = `CLICK_POOL` 随机 →
按住 = `float` 漂浮 → 松手 = `land`（**硬切不溶解**，阻尼压缩回弹）→
鼠标移入 = `notice`（1.4 秒 + 25 秒冷却）→ 动作演完垫 `settle-bridge`（1.75 秒）再回待机 →
每 70–200 秒走一小段（`run`/`walk`）。

## 5. 常用命令

```powershell
cd C:\Elysia\Elysia_two\Elysia
$env:Path = "$env:USERPROFILE\.local\bin;$PWD\.venv\Scripts;$env:PATH"

# 跑起来
python -m elysia.body.pet                      # 纯桌宠
python -m elysia.body.pet --with-soul-data     # 接灵魂（气泡显示她的话）

# 最小可玩闭环（三个逻辑进程）
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start -Body
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 status
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop

# 门禁四件套
uv run ruff check ; uv run ruff format --check ; uv run mypy src ; uv run pytest

# Spine 素材：预检 → **算共享画布** → 烘焙 → 全分辨率复查 → 入库 → 验收
#   全套流程与踩坑见 HANDOVER §5.1；**算共享画布**这步别跳，跳过会内容被平切
node scripts/spine_precheck.js
node scripts/spine_canvas.js                       # 写 out/<outfit>/canvas.json
node scripts/spine_bake.js elvisa_1 1_idle 24 560
node scripts/spine_bake.js elvisa_1 --derive land 24 560
python scripts/spine_check_all.py
uv run python scripts/ingest_spine.py --src <烘焙输出> --out assets/pet/costumes/<id> `
    --name "<名>" --downscale 2 --no-master --clip idle=1_idle --clip walk=2_walking_default
uv run python scripts/spine_verify.py              # ← 加了素材就跑这个

# 集成取证探针（临时库起真灵魂，零外部调用）
.venv\Scripts\python.exe D:\ElysiaAssets\_probe\pet_integration_probe.py

# 桌面截图（离屏，用来看眼睛/网格线这类细节）
$env:QT_QPA_PLATFORM="offscreen"   # 仅在离屏脚本里设，别影响真机启动
```

## 6. 红线（不能碰）

1. **授权**：Spine 运行时需 Spine Editor 授权；模型作者注明「解包资源，仅供学习使用」
   ⇒ 素材**只放本地**、不进公开仓库、不外传。仓库里只放不含素材的脚本
2. **她的真实记忆已在本机（2026-10-16 起，本机 = 主机器）**：用户已把记忆文件迁到本机
   ⇒ `data/*.db` **是她的一生，不再是测试数据**。**绝不允许用任何旧测试库/示例库覆盖它**；
   任何"恢复/重置/清理"动作**动手前先备份**
3. **同一时刻只允许一台机器跑她**（同时跑会分叉；他机若仍有旧副本，先别在那边启动）
4. **迁移后未核实**：库内行数 / 文件大小 / WAL 状态本窗都没核过（shell 失效）⇒ 新窗先核
   `Get-ChildItem data\*.db | Select Name,Length,LastWriteTime` + `heartbeats` / `memories` 计数
5. 用户自己的原始素材（`D:\图片\Saved Pictures`）**不主动删**，要问
6. **`.env` 里有主声 key，且备份 `D:\ElysiaBackup\elysia\` 也含 `.env`** ⇒ 这两处都不外传
7. **MiniMax API 每次调用都要先问用户**（曾烧掉 ¥10）；DeepSeek 主声已获授权自主调用

## 7. 还没做完的（下次可以接着做）

1. **⚠️ 跨板块必读**：**桌宠接灵魂的 P0 三处硬伤**（评估见 `docs/PET_INTEGRATION_ASSESSMENT.md`）：
   - **F8 / F-B 交互丢消息 —— 用户已实际遇到**：点击与打字**共用同一个 `kv('interaction')` 键**
     （`pet.py:1137`）⇒ **点一下桌宠就覆盖掉刚打进去的那句话**；连打多句只有最后一句到得了。
     两条修法：**本窗内可做**（桌宠侧"发件箱 + 已读回执"，靠她 `timesense.last_interaction_ts`
     推进判定已读 ⇒ 不跨板块）；**彻底版**（新增追加表 `interaction_log`，body 只 INSERT、
     soul 按 id 消费 ⇒ 需改 `soul/heartbeat.py`，跨板块，要总领窗授权）
   - **F-A 在场语义**：只有 `body/main.py` 写 `body_status`，桌宠自己**不上报**在场
     ⇒ 只开桌宠时她以为没人；一条陈旧 `body_status` 又会让她在桌宠面前演"离开"
   - **F-C 生命周期**：`soul.ps1` / `watchdog.ps1` / pid / 日志**都不管桌宠**
2. **主声降级不可观测（开放问题 #11）**：`llm/chain.py:257-262` 在主声文本被校验器拦下
   （`claim` / `promise` / `word_block`）或返回空时**静默降级**到 micro，**不写日志**
   ⇒ 想诊断"她这次为什么只说罐装话"没有证据。建议补一条降级原因日志（LLM 板块）
3. **`applaud`（鼓掌）用不了**：6 套**全部**是"眉毛/眼睑横穿眼睛"（系统性作者问题，
   已逐帧全分辨率确认）⇒ 点击回应只有"挥手"和"比心"两种。要更多花样只能等作者修或找新素材
4. **出声（TTS）**：需外部 GPT-SoVITS 服务（`127.0.0.1:9880`）；现在没起 ⇒ 她**只出文字**。
   参考音频与 GPT 权重原在总领窗目录的 `GPT-SoVITS-Elysia_Voice_Model\` 与 `语音文档\`（属声音板块）；
   **随迁后应在 `C:\Elysia\` 下，具体路径由新窗核实**（旧窗 shell 失效，未能确认）
5. **迁移后待办（新窗口第一件事）**：跑门禁 + `git status`，把本轮的**未提交文档改动**（§0、§4、§7 的更新）
   提交掉；并复核 §4 里"迁移前最后实测"的几项在新位置仍然成立
6. ~~`WANDER_SPEED_PX_S = 110` 是估的~~ → **已实测校准为 76 px/s**（`scripts/measure_walk_speed.py`）
7. **共享画布让窗口变大**（s1: 528×634 → 714×674，多出的是动作摆幅空间）。
   若嫌大，可在 `spine_canvas.js` 收窄采样范围，但会重新引入裁切风险
8. **推不推送**：55 个提交待用户决定
9. 可选：给 DSH 写个 `/pet` 插件命令；或走"运行时实时渲染 Spine"路线（HANDOVER §5）
   —— 注：那条调研的子代理**被中断、无产出**，要重跑
