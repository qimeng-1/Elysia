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
├── scripts/        # soul.ps1（进程控制）/ memory_view.ps1（记忆浏览器）/ backup.ps1（**手动**备份 D 盘，保留 30 份；未实现定时器）
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
```

> 出声需先手动启动 GPT-SoVITS 服务（端口 9880）；未启动时她只出文本气泡。记忆浏览器为只读 SELECT，可与运行中的灵魂并存。

## 文档索引

| 文档 | 位置 |
|------|------|
| 架构决策记录 | [docs/ADR/](docs/ADR/) |
| 技术架构设计（数据库/协议/模块/技术栈/运行时视图） | [docs/TECHNICAL_DESIGN.md](docs/TECHNICAL_DESIGN.md)（**唯一副本**，工作区已不再保留镜像） |
| 状态矩阵（能力 × 状态 × 测试口径） | [docs/STATUS_MATRIX.md](docs/STATUS_MATRIX.md) |
| 使用说明书（怎么操作） | [docs/OPERATION_GUIDE.md](docs/OPERATION_GUIDE.md) |
| 设计宪法（路线图 / 工程标准 / 开发纪律） | [docs/](docs/)（**唯一副本**；工作区 `设计规划/`、`工程标准/` 镜像目录已删除） |

## 开发纪律（摘要）

- 三条铁律：灵感不越阶段 / 地基优先 / 体验验收
- 架构铁律：T1 状态必须有能力 / T2 表达必须消费状态 / T3 循环先于对话
- 日志统一走 `elysia.core.log.get_logger()`，禁止裸 print
- 提交前必须通过 pre-commit 四件套

完整纪律见 [docs/DEVELOPMENT_PRACTICES.md](docs/DEVELOPMENT_PRACTICES.md) 与 [docs/PROJECT_ENGINEERING_STANDARD.md](docs/PROJECT_ENGINEERING_STANDARD.md)。

## 远程仓库

`https://github.com/qimeng-1/Elysia.git`（push 前需网络可达，且经确认）。
