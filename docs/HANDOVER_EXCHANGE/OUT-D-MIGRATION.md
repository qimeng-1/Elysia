# 会话交接：桌宠窗迁移交接（OUT-D-MIGRATION）

> **性质**：**不是**任务交付单（无任务号）—— 这是「窗口迁移 + 环境故障」的交接，
> 按 `HANDOVER_INTERFACE.md` 模板 4「阻塞单」的精神写。
> **窗口**：**D 桌宠窗**（迁移前那个旧会话）
> **时间**：2026-10-16 约 13:50（用户告知新位置 `C:\Elysia` 之后立即整理）
> **状态**：⚠️ **已停止；改动未提交、门禁未跑**（原因见 §1、§3）

---

## 1. 发生了什么

| 项 | 内容 |
|---|---|
| **搬家** | 仓库根 `D:\重置准备\备份\Elysia\Elysia_two\Elysia` → **`C:\Elysia\Elysia_two\Elysia`**；总领窗工作目录 → **`C:\Elysia`**（用户操作） |
| **本窗环境失效** | `C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe` **ENOENT** + ripgrep provider failure ⇒ **跑不了 git / 门禁 / 进程检查 / SQLite 查询**，只剩文件读写 |
| **已核实随迁** | git 分支 `main`、HEAD **`1f7c3f0`**、`docs/`、`assets/pet/costumes/`、`data/` |
| **未随迁（路径不变）** | `D:\ElysiaAssets\`（Spine 烘焙台+解包源+探针）、`D:\图片\Saved Pictures\`、`C:\Users\123\Desktop\Elysia_Project\`、`D:\ElysiaBackup\elysia\`（**含 `.env`**） |

---

## 1b. 【重要·口径已变】真实记忆迁到本机，本机 = 主机器

用户 2026-10-16 告知：**她的真实记忆文件已从另一台机器迁到本机**，**本机今后是主机器**。

⇒ 旧口径「本地 `data/*.db` 是测试数据 / 方向只能他机 → 这里」**已作废**，并就地改在这几处：

| 文件 | 改成了什么 |
|---|---|
| `docs/PET_SESSION.md` §3、§6 | 恢复提示词与红线重写：**data/ 是她的一生**；不许用旧测试库覆盖；重置前先备份；同时只允许一台机器跑她 |
| `docs/HANDOVER.md` §8.4 | 同上（并把未推送提交数 50 → 55） |
| `docs/PROJECT_BOARD_INDEX.md` §1、§8.2、§9 #6 | 口径行、红线、开放问题 #6 全部反转 |

**新口径一句话**：`data/*.db` **是她的一生，不是测试数据**；**绝不允许用任何旧测试库/示例库覆盖它**；
任何「恢复 / 重置 / 清理」**动手前先备份**；**同一时刻只允许一台机器跑她**（他机旧副本须确认已停用，否则会分叉）。

⚠️ **本窗无法核实**库内行数 / 文件大小 / WAL 状态（shell 失效）⇒ 新窗第一件事核这个（见 §7 步骤）。
⚠️ 也因此**无法确认他机那份旧副本是否还在跑**——这是留给用户/总领窗的悬项。

---

## 2. 本窗留下的未提交改动（**新窗必须收尾**）

| 文件 | 改了什么 |
|---|---|
| `docs/PET_SESSION.md` | 新增 **§0 迁移交接**；§3 恢复提示词改新路径；§4 状态速查更新（55 提交 / 纯桌宠 85~89 MB / 接灵魂 137 MB / 主声已通 / 最小可玩闭环）；§5 命令路径；§6 红线 +2；§7 重写（F8 已实际复现、#11、迁移待办） |
| `docs/HANDOVER.md` | 头部搬家说明；§1 数字更正（50 片段 / 内存两档 / 她开口）；**新增 §3.5 踩坑 #31–#34** |
| `docs/PROJECT_BOARD_INDEX.md` | 基线改 `1f7c3f0`/55；§5 五窗工作目录改 `C:\Elysia\*`；§6 目录标题；§9 新增 **#12**；§10 开盘提示词改路径 |
| `docs/HANDOVER_INTERFACE.md` | 门禁命令 `cd` 改 `C:\Elysia\Elysia_two\Elysia` |
| `C:\Elysia\00_总纲入口.md` | 路径改 `C:\Elysia`（**仓库外**，不进 git） |

> 全部为**文档改动**，**未碰 `src/`、`assets/`、`scripts/`**。

---

## 3. 门禁四件套（④）：**无法提供**

- **原因**：本窗 shell ENOENT（§1）⇒ 四件套一条都跑不了。
- ⇒ 本窗**不声称门禁绿**。请新窗 `git status` → 跑四件套 → 提交。
- 参考基线（迁移前最后一次全绿）：`ruff ✓ / format 125 ✓ / mypy strict 60 ✓ / pytest 461 passed`。

## 4. 激活性取证（⑤，迁移前实测，**迁移后未复核**）

- **主声**：`data/heartbeat.db::expression_log` —— id 20/21/23 `level=main`（真 LLM），id 19/22 `level=micro`（罐装）。
- **在场**：`data/state.db::kv('body_status')` 5s 刷新 ⇒ 她知道自己在场。
- **交互通路**：打字/点击经 `kv('interaction')` 到达；**同一拍窗内后写覆盖前写**（F8，已实测复现）。
- ⚠️ 三进程（灵魂/身体/桌宠）**现在是否还在跑 = 未核实**（无 shell）。

## 5. 未做与已知限制（⑥）

- 未提交、未跑门禁、未复核三进程存活。
- **未交付**用户要的"跑一天看主声真实用量"（需 SQLite 查询能力）。
- 未修：**F8**（任务板 T-004/T-005）、**#11 降级不可观测**（T-007）、F-A 在场语义、F-C 生命周期。
- `docs/STATUS_MATRIX.md` **未动**（归总领窗 T-001）。
- ⚠️ **旧路径可能仍有残留**：本窗 ripgrep 失效，**无法全仓搜索**。已修的只有
  `PET_SESSION.md` / `HANDOVER.md` / `PROJECT_BOARD_INDEX.md` / `HANDOVER_INTERFACE.md` / `00_总纲入口.md`。
  建议新窗第一件事跑一把：`rg -n "D:\\\\重置准备" docs/ README.md CHANGELOG.md IDEAS.md`
  （`LEAD_GOVERNANCE.md` / `TASK_BOARD.md` / `STATUS_MATRIX.md` / `OPERATION_GUIDE.md` /
  `DEVELOPMENT_PRACTICES.md` / `PROJECT_ENGINEERING_STANDARD.md` / `PET_INTEGRATION_ASSESSMENT.md`
  都**可能**有，本窗未核）。

## 6. 阻塞或依赖（⑦）

- **阻塞类型**：**门禁环境问题** —— 旧窗 shell 失效，交接无法完成"一个文件 + 一个 commit"里的 commit 那一半。
- **需要谁做什么**：
  1. **D 桌宠窗新会话**（工作目录 `C:\Elysia\Elysia_two\Elysia`）：`git status` → 门禁 → 一次 commit 收尾；
  2. 若新窗 shell **同样**失效 ⇒ 属**全局环境问题**，需用户修环境（`powershell.exe` / ripgrep 缺失）。

## 7. 建议验收步骤（⑧）

```powershell
cd C:\Elysia\Elysia_two\Elysia
git status --short                      # 期望看到 §2 表里的 4 个 md
uv run ruff check ; uv run ruff format --check ; uv run mypy src ; uv run pytest   # 期望 461 passed
git add docs/PET_SESSION.md docs/HANDOVER.md docs/PROJECT_BOARD_INDEX.md docs/HANDOVER_INTERFACE.md
git commit -m "docs: 桌宠窗交接——迁移路径修正 + 桌宠状态速查更新 + 新增 4 条踩坑"
python -m elysia.body.pet               # 桌面验收
```

## 8. 自检写作用域（⑨）

- **是否越界：否**。只改本窗作用域内的 `docs/PET_*.md` / `docs/HANDOVER.md`，以及
  `PROJECT_BOARD_INDEX.md` / `HANDOVER_INTERFACE.md` 的**路径修正**+一条开放问题（无结构改动）。
- 未碰 `src/**`、`assets/**`、`scripts/**`、`tests/**`。
