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
├── data/           # 运行时数据（进 git 做记忆快照版本化；logs/cache/tmp 除外）
├── scripts/        # soul.ps1（进程控制）/ memory_view.ps1（记忆浏览器）/ backup.ps1（每日备份 D 盘，保留 30 份）
└── docs/           # ADR + 设计宪法副本 + 仓库独有文档（操作手册 / P2·P3 规划与工作日志 / 人设）
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
| 技术架构设计（数据库/协议/模块/技术栈/运行时视图） | [docs/TECHNICAL_DESIGN.md](docs/TECHNICAL_DESIGN.md)（**唯一副本**，工作区已不再保留镜像） |
| 设计宪法（路线图/路径图/工程标准/铁律手册/迁移手册） | [docs/](docs/)（**唯一副本**；工作区 `设计规划/`、`工程标准/` 镜像目录已删除） |

## 开发纪律（摘要）

- 三条铁律：灵感不越阶段 / 地基优先 / 体验验收
- 架构铁律：T1 状态必须有能力 / T2 表达必须消费状态 / T3 循环先于对话
- 日志统一走 `elysia.core.log.get_logger()`，禁止裸 print
- 提交前必须通过 pre-commit 四件套

完整纪律见 [docs/DEVELOPMENT_PRACTICES.md](docs/DEVELOPMENT_PRACTICES.md) 与 [docs/PROJECT_ENGINEERING_STANDARD.md](docs/PROJECT_ENGINEERING_STANDARD.md)。

## 远程仓库

`https://github.com/qimeng-1/Elysia.git`（push 前需网络可达，且经确认）。
