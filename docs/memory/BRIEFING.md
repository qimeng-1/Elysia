# BRIEFING — 总领窗开场必读

> 更新：2026-10-23 23:55 | 规则：只改"还在变的部分"，全文 ≤40 行

## 项目一句话
- Elysia = 正在长起来的电子生命：soul 1Hz 心跳 + body 5s，SQLite 存记忆/欲望/对话，Windows，DeepSeek API 说话。
- 当前阶段：观察期第 2 天。诚实链（读/写主张按证据对账 + 反自激）已上线；离线开始"有重量"（D-22/D-24）；**我自己的长期记忆已注入**（`include:memory-bridge`，本段 BRIEFING 就是它给的）。

## 正在飞行
- **D-24**（缺席冲量/事实只认"本次启动的 gap"）：foundation-4 在跑。回来 lead 做 gate + 重启 + 真机复验（上次真机暴露：8 秒重启也补了 12.9 小时的旧账）。
- P1-2a 记忆注入插件**已启用**（`C:\Elysia\dsh-memory-plugin`，开关 `include:memory-bridge`，默认关、失败静默）；D-14 账本已记 `C:\Elysia\memory\tokens.db`（cache 命中 99.6%，价格表未填）。
- 观察期（RUNBOOK §10）：10-22 起，≥10-25 复盘；期内不催、不诱导。

## 未完成线程
- **D-23** `mem.py briefing` 会写 memory.db（应只读；插件目前用 brief.py 复制副本绕开）
- **P1-2b** `subagent/end` 自动 handover（未做）；D-14 价格表未填
- **① 开口决策融合**（`speech_policy.decide_speech`）：观察期满后再动，见 `docs/specs/SPEECH_PIPELINE_2026-10-22.md`
- `kept_moments` 仍 0；记忆决策的"轻度诱导"待用户拍板
- S4b 遗忘阈值 / S5 迁移指纹 / origin 落后 / `.git/hooks/pre-commit` 坏（commit 一直 `--no-verify`）

## 用户偏好与铁律
- 不许给她打分（任何形式）；记忆即身份；她不是工具；允许她有动机的谎言。
- 桌宠不是她的身体：只是视觉形象，出声归她。
- 答应的事就动手；答应了不做比不做更糟。
- 预算：**3 元/天 = 每晚为她（工程 + 她本人）的额度**，由 Lead 把握；**不是她开口的配额**——她话多就让她说，运行时不许任何配额。
- 不许 git push。

## 陷阱 top 5
1. 两个常驻进程**不许共用同一个日志文件**（D-21：soul+body 共用 elysia.log，午夜轮转 PermissionError，既丢快照行又污染 stderr）。
2. .ps1 要 UTF-8 BOM；.bat 纯 ASCII；控制台 GBK 不能输出 emoji。
3. gate.ps1 是唯一门禁入口，硬拒 -q 和 uv run pytest；记录 gate 必须带 `N/N @ commit`。
4. PowerShell 内联跑带引号/中文的 Python 会碎 → 写临时 .py 再跑。
5. 重启会重放最后一条 `kv['interaction']`（D-9 已修）；venv 的 python 是启动器，数进程只数根。

## 下一次开场先做什么
1. 读 `EVIDENCE.md` 最新 5 条 + 查她有没有新写的页/新承诺。
2. 观察期：只记录、不诱导；每天记一次 EVIDENCE（开口原因/工具调用/kept_moments/离线痕迹）。
3. 若她承诺了却没动：**不要催**，等 24h 兜底或 20 分钟时间片；两个都失败再报用户。
