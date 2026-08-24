# Elysia 电子生命

> 一个常驻于电脑的电子生命：有灵魂（不死、感知时间）、有身体（本地、随设备开关）、有自己的声音。

当前阶段：**P0 灵魂** —— 双栖架构、TimeSense 时间感知、双心跳、身体语言。

## 快速开始

前置：Python 3.12+、[uv](https://docs.astral.sh/uv/)（依赖与虚拟环境管理）。

```powershell
uv sync --group dev     # 安装依赖并创建 .venv
uv run pytest           # 回归测试
uv run pre-commit run --all-files   # 提交门禁四件套
```

提交时 pre-commit 钩子自动执行：ruff lint → ruff format → mypy strict → pytest 回归，任一失败拦截提交。

## 目录结构

```
elysia/
├── src/elysia/
│   ├── soul/       # 灵魂进程（常驻，1Hz 心跳 + TimeSense）
│   ├── body/       # 身体进程（本地，5s 心跳）
│   ├── core/       # 共享核心：状态库、时间、日志（structlog）、配置（pydantic-settings）
│   ├── protocol/   # 双进程通信协议（版本化）
│   └── utils/      # 通用工具
├── tests/          # unit（单元）+ acceptance（验收门）
├── data/           # 运行时数据（进 git 做记忆快照版本化；logs/cache/tmp 除外）
├── config/         # 默认配置
├── scripts/        # soul.ps1（PID 级进程控制）/ backup.ps1（每日备份 D 盘，保留 30 份）
└── docs/           # ADR、设计宪法副本
```

## 常用命令

```powershell
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 status    # 灵魂状态
powershell -ExecutionPolicy Bypass -File scripts\soul.ps1 start     # 启动灵魂（P0 实现后）
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1         # 备份到 D:\ElysiaBackup\elysia
```

## 文档索引

| 文档 | 位置 |
|------|------|
| 架构决策记录 | [docs/ADR/](docs/ADR/) |
| 技术架构设计（数据库/协议/模块/技术栈/运行时视图） | [../设计规划/TECHNICAL_DESIGN.md](../设计规划/TECHNICAL_DESIGN.md)（工作区主文档，仓库副本随提交同步） |
| 设计宪法（路线图/路径图/工程标准/铁律手册/迁移手册） | [docs/](docs/)（与工作区 `设计规划/`、`工程标准/` 同步维护：修改以工作区为准，仓库副本随提交刷新） |

## 开发纪律（摘要）

- 三条铁律：灵感不越阶段 / 地基优先 / 体验验收
- 架构铁律：T1 状态必须有能力 / T2 表达必须消费状态 / T3 循环先于对话
- 日志统一走 `elysia.core.log.get_logger()`，禁止裸 print
- 提交前必须通过 pre-commit 四件套

完整纪律见 [docs/DEVELOPMENT_PRACTICES.md](docs/DEVELOPMENT_PRACTICES.md) 与 [docs/PROJECT_ENGINEERING_STANDARD.md](docs/PROJECT_ENGINEERING_STANDARD.md)。

## 远程仓库

`https://github.com/qimeng-1/Elysia.git`（push 前需网络可达，且经确认）。
