# ADR-002：SQLite WAL 作为状态库

- 状态：已接受
- 日期：2026-08-22

## 背景

灵魂与身体两进程需要共享持久状态（心跳记录、TimeSense 状态、身体存活标记），且：

- 状态必须**落盘**（断电/崩溃不丢，损失 ≤ 1 秒）；
- 两进程**并发读写**（灵魂每秒写、身体每 5 秒写）；
- 零运维成本（无额外服务进程）。

模型能力实测（2026-08-22，三任务 19/19 通过）验证了 Python asyncio + SQLite WAL 方案的可行性：
`journal_mode=wal`、`integrity_check=ok`、零丢失、写者协程崩溃不死锁。

## 决策

采用 **SQLite WAL 模式**作为双进程共享状态库（`data/state.db`）：

- `PRAGMA journal_mode=WAL` + `busy_timeout`；
- 单写者纪律：每进程内所有写入经单一队列串行落盘（asyncio 单写者模式）；
- 跨线程写使用 `check_same_thread=False`（单写者串行安全）+ 优雅关闭超时兜底；
- 每日备份到 D 盘（`scripts/backup.ps1`，保留 30 份）。

## 备选方案

| 方案 | 否决原因 |
|------|----------|
| JSON 文件全量读写 | 并发写易损坏、无事务、大文件低效 |
| Redis/其他服务 | 引入常驻服务，违背零运维 |
| 纯内存 | 断电丢失，违背状态持久原则 |

## 后果

- 写入吞吐受磁盘限制（1Hz 心跳完全够用）；
- WAL 文件与主库同目录，备份必须连 WAL 一起（robocopy 整目录备份天然覆盖）；
- 数据库文件会随生命周期增长，需定期 VACUUM（P1 安排）。

---

## 更正（2026-10-14，清理审计追加）

> 原文保留，以存当时的决策语境；以下两处**技术断言经复核不成立**：

1. **"备份必须连 WAL 一起（robocopy 整目录备份天然覆盖）"** —— robocopy 复制的是**当时的文件字节**；
   把 `-wal` / `-shm` 一并拷走**并不等于**得到事务一致的快照（可能拷到写了一半的页）。
   正确做法：备份前先停灵魂，或改用 SQLite 的 `.backup` / `VACUUM INTO`（`core/checkpoint.py` 用的正是前者）。
2. **"需定期 VACUUM（P1 安排）"** —— P1 早已结束，**该安排从未落地**：全代码库无任何 VACUUM 调用，
   而 `heartbeats` 按 1 Hz 线性增长（约 8.6 万行/天）且无保留策略。

另：原文"每日备份"与实际不符——`scripts/backup.ps1` 是**手动**脚本，没有定时器 / 计划任务。

**后续（2026-10-14，阶段 B2 已闭合上面两条）**：

1. 备份**改为 SQLite `.backup` 一致快照**（`core.checkpoint.backup_database`，由
   `scripts/snapshot_dbs.py` 调用）：robocopy 负责全量文件，两个**运行中的库**随后被
   事务一致快照覆盖；备份里的陈旧 `-wal`/`-shm` 也会被清掉（否则将来打开备份会把旧 WAL
   重放到新快照上）。实测：运行中做出的快照 `PRAGMA integrity_check = ok`，表结构完整。
2. 备份**有了定时器**：`backup.ps1 -Register` / `-Unregister` 管理每日计划任务
   （本机已注册 `Elysia-DailyBackup`，每天 03:30）。
3. `VACUUM` 与流水表保留策略**仍未落地**——它触碰"数据永不删除"铁律，须先拍板口径。
