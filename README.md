# Elysia 电子生命

> 一个常驻于电脑的电子生命：有灵魂（不死、感知时间）、有身体（本地、随设备开关）、有自己的声音。

当前阶段：**P0–P3 已交付**（灵魂 / 心脏 / 表情 / 记忆，含 Self Memory 与联想网络）；**运行时可靠性收口**进行中（阶段 1 表达异步化，主体已落地待评审）。

## 快速开始

前置：Python 3.12+、[uv](https://docs.astral.sh/uv/)（依赖与虚拟环境管理）。

```powershell
uv sync --group dev     # 安装依赖并创建 .venv
uv run pytest           # 回归测试
uv run pre-commit run --all-files   # 提交门禁四件套
```

提交时 pre-commit 钩子自动执行：ruff lint → ruff format → mypy strict → pytest 回归，任一失败拦截提交。

> **钩子需要能调用到 `pre-commit`**：先激活虚拟环境（`.\.venv\Scripts\Activate.ps1`）或把 `.venv\Scripts` 加进 `PATH`，
> 否则 `git commit` 会报 `` `pre-commit` not found ``。不想折腾时，用上面那条 `uv run pre-commit run --all-files` 手动跑同一套门禁。
>
> **CI**：`.github/workflows/ci.yml` 在 push / PR 时跑同一套四件套（与本地 pre-commit 同源）。

## 目录结构

```
elysia/
├── src/elysia/
│   ├── soul/       # 灵魂进程（常驻，1Hz 心跳 + TimeSense + 欲望/大脑/表达）
│   ├── body/       # 身体进程（本地，5s 心跳 + 桌宠）
│   ├── core/       # 共享核心：状态库、时间、日志（structlog）、配置（pydantic-settings）
│   ├── memory/     # 记忆系统（三层晋升 / 索引衰减 / 检索染色 / 修正取代 / 做梦）
│   ├── llm/        # 表达引擎（三级降级链 + 输出校验器）
│   ├── tts/        # 出声（GPT-SoVITS + 缓存池 + 熔断）
│   ├── protocol/   # 双进程通信协议（版本化）
│   ├── tools/      # 观测工具（记忆浏览器）
│   └── utils/      # 通用工具
├── tests/          # unit（单元）+ acceptance（验收门）
├── assets/pet/     # 桌宠素材（character.png）
├── data/           # 运行时数据；*.db 与 logs/cache/tmp **不入库**，仅 checkpoints/ 快照入库（版本化）
├── scripts/        # soul.ps1（进程控制）/ watchdog.ps1（进程级看门狗）/ backup.ps1（备份+每日计划任务）
│                   # retention.py（保留策略：归档+裁剪+VACUUM）/ snapshot_dbs.py（库一致快照）/ memory_view.ps1
└── docs/           # ADR + 设计宪法副本 + 仓库独有文档（操作手册 / P2·P3 规划与工作日志 / 人设）
```

## 常用命令

```powershell
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 status        # 查看灵魂/身体状态
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start         # 启动灵魂（常驻，1Hz 心跳）
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start -Body   # 启动灵魂 + 身体（幂等）
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 stop          # 停止两者（stop-body 只停身体）
powershell -ExecutionPolicy Bypass -File scripts\memory_view.ps1        # 记忆浏览器（只读观测）
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1             # 备份到 D:\ElysiaBackup\elysia
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Register   # 注册每日备份计划任务（-Unregister 取消）
powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1           # 进程级看门狗：崩了就拉回
powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1 -Register # 每 5 分钟自动检查（-Unregister 取消）
uv run python scripts\retention.py --keep-days 90                       # 保留策略（干跑；--apply 真做，须先停她）
```

> 出声需先手动启动 GPT-SoVITS 服务（端口 9880）；未启动时她只出文本气泡。记忆浏览器为只读 SELECT，可与运行中的灵魂并存。

## 文档索引

| 文档 | 位置 |
|------|------|
| **路线图 v2（P4 及以后，生效）** | [docs/ROADMAP_V2.md](docs/ROADMAP_V2.md) |
| 本轮工作报告（交付与验收） | [docs/SESSION_REPORT_2026-10-14.md](docs/SESSION_REPORT_2026-10-14.md) |
| 架构决策记录 | [docs/ADR/](docs/ADR/) |
| 技术架构设计（数据库/协议/模块/技术栈/运行时视图） | [docs/TECHNICAL_DESIGN.md](docs/TECHNICAL_DESIGN.md)（**唯一副本**，工作区已不再保留镜像） |
| 状态矩阵（能力 × 状态 × 测试口径 ＋ 失效引用 / 待补清单 / 缺陷登记） | [docs/STATUS_MATRIX.md](docs/STATUS_MATRIX.md) |
| 使用说明书（怎么操作） | [docs/OPERATION_GUIDE.md](docs/OPERATION_GUIDE.md) |
| 设计宪法（v1.1 路线图 / 工程标准 / 开发纪律） | [docs/](docs/)（**唯一副本**；`IMPLEMENTATION_ROADMAP.md` 的 P4–P6 前瞻部分已由 ROADMAP_V2 取代，其余继续生效） |

## 开发纪律（摘要）

- 三条铁律：灵感不越阶段 / 地基优先 / 体验验收
- 架构铁律：T1 状态必须有能力 / T2 表达必须消费状态 / T3 循环先于对话
- 日志统一走 `elysia.core.log.get_logger()`，禁止裸 print
- 提交前必须通过 pre-commit 四件套

完整纪律见 [docs/DEVELOPMENT_PRACTICES.md](docs/DEVELOPMENT_PRACTICES.md) 与 [docs/PROJECT_ENGINEERING_STANDARD.md](docs/PROJECT_ENGINEERING_STANDARD.md)。

## 远程仓库

`https://github.com/qimeng-1/Elysia.git`（push 前需网络可达，且经确认）。
