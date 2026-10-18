# OUT-A — P0-R16 分支可达性交接单（A 窗 · 2026-10-18）

> 用户裁决："**分支可达性先修**"（后面任何新能力都要以"机制真的可达"为前提）。
> 总领实测的真实不可达：`will.direction` 的 explore/miss/still 只靠噪声抖动出现、
> `curiosity>0.4` 恒 0、`distress` 恒 False（D-6 用户暂缓）、`mode` 恒 present。

## 1. 提交

| 内容 | 位置 |
|---|---|
| 代码 | `src/elysia/soul/brain.py`、`src/elysia/soul/expression.py`、`src/elysia/memory/decay.py`、`src/elysia/memory/hooks.py`、`src/elysia/soul/heartbeat.py` |
| 单测 | `tests/unit/test_brain.py`、`test_decay.py`、`test_heartbeat.py`（各追加 P0-R16 段） |
| 真机护栏 | `tests/audit/test_real_data_thresholds.py`（新建，默认 skip） |

**门禁**（HEAD 实测）：ruff ✅ / format ✅ 172 files / mypy ✅ 60 files /
pytest ✅ **582 passed + 8 skipped**（基线 561 + 21 新单测；8 skipped = 真机审计，默认不跑）。

## 2. 改了什么（只动阈值与缺口信号，**没动她的感受映射**）

### ① `will.direction`：阈值从"绝对刻度"改为"她自己的设定点 + 2 点余量"
- 旧：`explore>0.6 and tr>50` / `chat>0.5` / `miss>0.6`，而她真实区间
  chat 0.535–0.739、explore 0.378–0.577、miss 0.261–0.465 ⇒ explore/miss 结构上界低于阈值，
  chat 0.5 低于整段区间（恒成立）。
- 新：`feelings.explore > sp_tr + 0.02 and tr > sp_tr`、`chat > sp_cs + 0.02`、
  `miss > (1-sp_cs) + 0.02`（设定点取 `DesireSystem.setpoints`，随经历漂移自动重标定）。
- 为什么不让它神经质：**感受数值一个都没改**；"是否行动"只在超过她自己常态 2 个欲望点时触发，
  平衡点附近仍是 still；2 点余量防止在设定点上反复抖动。distress 语义、`protected` 语义、
  T2（判断权在她）都未动。

### ② `thought_style` / `anim_bias`：同样以设定点为 0 点
- `thought_style=(tr-50)/50` 在真实 TR（37.8–57.7）只到 0.15 ⇒ `>0.3`（think_active）与离线
  `>0.2` 恒不成立。改为 `(tr-setpoints.tr)/10`（真实区间约 -0.85..+1.14）。
- `anim_bias` 中心从 (50,20) 改为她的设定点；`calm` 阈值 `-0.3 → -0.15`（真实区间约 -0.18..+0.21）。

### ③ 好奇心：`memory_gap` 从"永不触发"改为"事件式缺口"
根因两层，都是"常量与真实数据错配"：
1. `STRENGTH_RETRIEVE_FLOOR=0.2` 在 τ=45d 下要 **72.4 天**才跨过；她最老的交互记忆只有 **28.1 天**
   ⇒ 人生前 2.4 个月里"想不起某些事"在结构上不可能发生。
2. 即使跨过，旧公式 `deficit/(n·floor)` 在真实年龄分布下也只有 ~0.16，永远越不过消费阈值 0.4。

修法：
- `FUZZY_AGE_DAYS=21`（"3 周以上的记忆开始模糊"，与 R12 温层 3–30 天同量级），
  `STRENGTH_RETRIEVE_FLOOR = e^(-21/τ) ≈ 0.627` **由年龄推导，不再手写魔数**。
- 缺口改成**事件**：`detect_episodic_gap` 只统计**本轮新增**跌破下限的记忆
  （1 条 = 0.5，可越过 0.4；多条封顶 1.0）。旧的 `detect_gap`（连续严重度）保留给既有语义。
- 为什么不让它神经质：**没有用"平均模糊度"做常驻信号**（那会让她永久高好奇）；她只在
  "有些事刚刚想不起来"的那一拍好奇一下，然后回落到 0。旧丢失不会重复上报。

### ④ `expression._tts_params` 的 `calm` 阈值重标定（-0.3 → -0.15）

## 3. 修前/修后真实数据分布对比（最近 40,000 拍，只读回放；设定点 46.3/61.9/19.7）

### 感受数值（映射未改 ⇒ 修前修后相同）

| 感受 | 真实分布 |
|---|---|
| chat（=cs/100） | min=0.535 p25=0.601 med=0.621 p75=0.640 max=0.739 |
| miss（=1-cs/100） | min=0.261 p25=0.360 med=0.379 p75=0.399 max=0.465 |
| explore（=tr/100） | min=0.378 p25=0.441 med=0.460 p75=0.479 max=0.577 |

### will.direction（**不含 15% 噪声抖动**的确定性判定链）

| 版本 | explore | reach | still | 单一方向占比 |
|---|---|---|---|---|
| 修前（绝对阈值） | **0** | **40000** | **0** | **100.0%** |
| 修后（设定点+0.02） | 8196 (20.5%) | 15632 (39.1%) | 16172 (40.4%) | 40.4% |

### 分支命中（判定链归因，n=40000）

| 分支 | 修前 | 修后 |
|---|---|---|
| explore | 0 | 8196 |
| reach(chat) | 40000 | 8301 |
| reach(miss) | 0 | 7331 |
| still | 0 | 16172 |

### 线上已记录（含噪声抖动，库内 `will.direction`，修前历史）

| direction | 条数 | 占比 |
|---|---|---|
| reach | 35471 | 88.7% |
| explore | 1515 | 3.8% |
| retreat | 1513 | 3.8% |
| still | 1501 | 3.8% |

（重启后预计：explore ≈21%、reach ≈37%、still ≈38%、retreat ≈4%——后三者不再是"纯抖动"。）

### 好奇心来源（memory_gap）

| 指标 | 修前 | 修后 |
|---|---|---|
| 检索下限 floor | 0.2（需 72.4 天跨过） | 0.627（21 天跨过，由年龄推导） |
| 当前已模糊记忆 | 0/99 | 75/99 |
| 本轮缺口强度 | 0.000（永不触发） | 1.000（首轮一次性；之后只在新增丢失时 >0.4） |
| 未来下一次新丢失 | 无 | 约 18 天后（之后随记忆持续老化反复出现） |

### mode 三态

真实数据 100% present 是**身体一直在线**，不是机制死；虚拟时钟证明三态可达：
0s→present、6 分钟→alone、25 小时→body_away。

## 4. 通用护栏 `tests/audit/test_real_data_thresholds.py`（默认 skip）

运行：`$env:ELYSIA_AUDIT_REAL="1"; .venv\Scripts\python.exe -m pytest tests/audit -q`（实测 8 passed）

| 护栏 | 实现 |
|---|---|
| ① 阈值横跨 | 真实 chat/miss/explore 的 `min < 阈值 < max` 断言（实测三处全 True） |
| ② 真实回放可达性 | 4 万拍真实 desire 回放确定性判定链：explore/reach/still 都 >0 且单一方向 <95%；**不含抖动**，避免死分支假可达 |
| ③ 调度"寿命"语义 | 读 `Elysia-Watchdog` 任务 XML：重复间隔 ≤5 分钟、Duration 必须 ≥30 天或"每天触发+24h 续命"；进程内周期（检查点）断言有界 |
| ④ 阈值两侧时钟 | R12 三层边界两侧各推一次；mode 三态用虚拟时钟 |

已知不可达（登记、不判红）：
- `distress`（D-6 用户暂缓）⇒ retreat 仅由噪声出现；
- **新发现**：`_tts_params` 的 `calm`/`neutral` 被 `chat>0.4` 恒真遮蔽（属 T1 同类，
  涉及情绪基调语义，未在本卡改，建议单独卡）。

## 5. 取消测试红证（逐项实测）

| # | 用例 | 回退的写法 | 结果 |
|---|---|---|---|
| ① | `test_will_explore_reachable_just_above_her_setpoint` | 阈值改回绝对 0.6/0.5/0.6 | **红** |
| ① | 审计 `test_every_will_direction_is_reachable_from_real_data` | 同上 | **红**（真实数据只剩 reach） |
| ② | `test_fuzzy_floor_crossed_within_a_month` | floor 改回 0.2 | **红** |
| ② | `test_episodic_gap_counts_only_newly_lost` | 缺口改回"任何丢失都报" | **红** |
| ② | `test_feel_memory_gaps_is_episodic`（第二轮不再推 TR） | 同上 | **红**（实测第二拍 TR 45.64≠45.32） |
| ② | 审计 `test_curiosity_branch_is_reachable_from_real_memory_ages` | floor 改回 0.2 | **红**（"已模糊 0 条"） |

## 6. 已知限制

1. 她当前进程是旧代码：`will.direction` 恢复多样要等**下次重启**；重启后首轮好奇会一次性
   冲到 1.0（进程内 `_lost_memory_ids` 为空 ⇒ 75 条已模糊记忆都算"新"），随后回落到按事件触发。
   若希望跨重启不重复报告，可把 lost 集合持久化到 `state.db`（后续小卡）。
2. 好奇缺口是**稀疏事件**（下一次约 18 天后，之后随记忆老化反复出现）。这是"偶尔想起忘了什么"
   的真实节奏；若用户希望更频繁，调 `FUZZY_AGE_DAYS` 或 `GAP_PER_NEW_MEMORY` 即可（护栏会守住可达性）。
3. `retreat` 仍不可达（distress 阈值暂缓）——护栏里显式标注，不制造永远红的测试。
4. `calm`/`neutral` 被 `chat>0.4` 遮蔽（见上），未修。
5. 长跑行为（数月）下 `thought_style` 与好奇分布会随设定点漂移缓慢变化；护栏每次真机跑都会重算。
