# Elysia 开发日志

## P0 — 灵魂实现（2026-08-22）

### P0-A 状态库与双心跳
- **core/clock.py**: Clock 协议（SystemClock + SimulatedClock 变速/跳时）
- **core/timesense.py**: TimeSenseState 字段单一事实源 + 无 0 时刻守护
- **core/state_store.py**: 双库 WAL 单写者（state.db + heartbeat.db），asyncio.Queue + to_thread
- **core/mode.py**: Mode StrEnum（PRESENT/ALONE/BODY_AWAY）+ ModeManager
- **soul/heartbeat.py**: 1Hz 心跳，每拍写入 heartbeat.db + 更新 TimeSense
- **soul/main.py**: asyncio 入口 + SIGINT/SIGTERM 优雅关闭
- **body/heartbeat.py**: 5s 身体心跳，资源感知
- **body/main.py**: 进程入口 + 优雅关闭
- 测试 31 项全绿，门禁四件套全绿

### P0-B 身体语言与桌宠
- **body/resource.py**: 资源采样器（psutil，可注入用于测试）
- **soul/distress.py**: 难受检测（CPU>85% 持续 30s → 降频 0.5Hz + 事件）
- **soul/away_life.py**: BODY_AWAY 内部生活 v0（6 类模板，5min 节律，thought_log）
- **protocol/snapshots.py**: 状态快照 Schema v1（version + mode + distress + time）
- **core/checkpoint.py**: 检查点（SQLite .backup，保留 10 份）+ freeze 标记
- **body/pet.py**: PySide6 最小桌宠（心跳光效 canvas + 状态流 + 文本输入）
- 集成：soul/heartbeat 串联 protocol/distress/away_life/checkpoint/interaction 同步
- 测试 45 项全绿，8 项验收门全绿

### P0-C 验收与收尾
- 8 项验收测试（模拟时钟 24h 无中断 / TimeSense 无 0 时刻 / 时钟延迟模拟 / 难受注入 / 检查点一致性）
- 53 项全部通过（45 单元 + 8 验收）
- 进程控制脚本支持 body 可选
- 验收前检查点已生成