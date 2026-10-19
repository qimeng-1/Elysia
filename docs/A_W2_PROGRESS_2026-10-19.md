# A 窗 W2：说话节奏由意愿连续驱动 · 验收留档（2026-10-19）

> commit：**`ef43879`** `feat(express): 说话节奏由意愿连续驱动（60–1800s + EMA 平滑）(W2)`
> 基线：666 passed + 11 skipped（W2 开工前）。本窗新增 11 个单测 + 2 个真机审计（默认跳过），
> 0 回归；门禁实测见 §5。

## 1. 改了什么

`src/elysia/soul/expression_service.py`：
- **删掉** `EXPRESS_ACTIVE_INTERVAL_S=180 / EXPRESS_QUIET_INTERVAL_S=600` 与
  "`think_*` 且 `thought_style>0` 二选一、再 × 退缩倍数"的分支。
- 新增纯函数 `expression_willingness(...)` / `interval_from_willingness(...)` /
  `expression_interval_s(...)`：把她的真实维度连续算成意愿 `w∈[0,1]`，再**几何映射**到
  60–1800s：`interval = 60 · (1800/60)^(1−w)`（w=0.5 ≈ 329s）。
- `ExpressionService.rhythm_interval_s()`：心跳每拍一次，对意愿做 τ=60s 的**一阶 EMA
  平滑**后映射成当前最小间隔；`should_speak` 改为使用它。
- `force` / `greeting` 立即开口、队列满丢弃、心跳不等 LLM —— **全部未改**。

输入（W2 要求全覆盖）：
| 维度 | 用法 | 方向 |
|---|---|---|
| `feelings.chat` | V 形社交驱动的"想聊"侧（越过火花线 0.65 才额外驱动） | 高 → 更想开口 |
| `feelings.miss` | V 形社交驱动的"思念"侧（越过 0.40 才额外驱动） | 高 → 更想开口 |
| `feelings.explore` | 探索欲（对称偏离常态 0.45，±0.15 记满档） | 高 → 更想开口 |
| `feelings.rest` | 歇着（0.05 常态 → 难受时 0.5） | 高 → 更安静 |
| `will.thought_style` | 活跃度（−1 安静 … +1 活跃，连续量） | 高 → 更想开口 |
| `reluctance`（关系退缩 1.0→2.0） | 折成 0–0.35 的意愿折扣 | 高 → 间隔更长（但不顶穿 1800s） |

**为什么 V 形社交驱动**：她的 `chat = CS/100`、`miss = 1 − CS/100` 是互补的（真机
chat 中位 0.642 / miss 0.358 ≈ 1）。所以"想聊"与"思念"是两个方向的想开口：高于火花线
才算额外驱动，正好平衡时最平静——两个维度各自单调（越高越想说），互补性也不会导致
互相抵消。火花线取 0.65/0.40 ≈ 真机 chat/miss 的 p85–p90（0.677/0.395），避免把
"她本来就一直偏 chat"误读成"一直很想说"（那会退回另一种节拍器）。

## 2. 防抖：选了 EMA，不是迟滞

- 旧实现的抖动源是**阈值本身**（180/600 二选一）；连续映射后，状态小幅抖动 → 间隔小幅变化。
- 再叠一层 **τ=60s 的一阶指数平滑（EMA，对称）**：1 分钟走完 63%、5 分钟走完 99%，
  压掉 `thought_style` 这类逐拍噪声，同时保留分钟级真实起伏。
- **为什么不用二值迟滞**：迟滞会留下方向记忆（"变安静"比"变活跃"更容易留下），产生
  系统性偏置；EMA 是无偏的（长时间盯住同一状态会收敛到该状态的映射值，有用例
  `test_smoothing_preserves_sustained_state` 钉住）。抖动被压到中间带附近
  （`test_smoothing_damps_jitter_without_flapping`：600 拍两端逐拍交替，单拍变化 <100s）。
- `dt` 取真实经过时长；停机重启后的第一拍（dt 很大）直接采用当前状态，不会用几小时前
  的旧节奏慢慢爬。

## 3. 单调性 / 覆盖宽度（正常门禁用例，不是审计）

`tests/unit/test_w2_expression_rhythm.py` + `test_w2_rhythm_behavior.py`：
- **严格单调**：thought_style 递增 ⇒ 意愿严格递增、间隔严格递减；网格级按意愿排序后间隔
  单调不增。
- **各维度方向**：chat↑（越过 0.65）/miss↑（越过 0.40）/explore↑/style↑ ⇒ 更想开口；
  rest↑/reluctance↑ ⇒ 更安静；恒在 [60,1800]。
- **多档覆盖**：真实状态范围内的状态网格命中 ≥4 个间隔档，p90/p10 > 3（不是窄带）。
- **接线**：`should_speak` 在映射间隔 −1s 时仍节流、+1s 时开口（证明不是纯函数摆着看）。
- **行为级取消测试**：只调 `should_speak`（不 import 任何新符号）逐秒测出间隔，
  要求 ≥4 种、安静态 >500s、活跃态 <300s。

## 4. 真机审计（`tests/audit/test_real_data_rhythm.py`，`ELYSIA_AUDIT_REAL=1`）

2026-10-19 23:31 实测（近 40000 拍真实心跳，窗口 11.25h）：

```
真实状态映射（n=40000）：
  新 瞬时分布 %: {'60-120s': 6.9, '120-180s': 17.7, '180-300s': 34.9,
                 '300-600s': 34.9, '600-1200s': 5.5, '1200-1800s': 0.1}
  旧 瞬时分布 %: {'180-300s': 25.2, '600-1200s': 74.8}
  新 range=[60, 1522] 中位=263；旧 distinct=2
  willingness: 中位=0.566 p10=0.369 p90=0.768

真实回放（11.25h，n=40000 拍）：
  旧：中位=193s p10=181 p90=410 开口/日≈348 distinct=2
  新：中位=224s p10=120 p90=399 开口/日≈350 distinct(10s)=43
  新：<180s 快档占 33.1%；>300s 慢档占 27.6%
```

- 旧实现同一批真实数据只有 **2 个间隔值**、74.8% 压在 600s；新映射横跨 **5 个档**
  （≥2%），最多的一档 34.9%——不是又一个窄带。
- 回放口径校验：旧实现的开口序列在早前窗口与同窗口真实 `expression_log` 行数逐次对上
  （162 = 162），说明回放模型可信。

## 5. 对用户的量化交代：她会变安静吗？

**不会明显变安静，但节拍被打散了。**（按她近 11.25h 真实状态回放）

| | 旧 | 新 |
|---|---|---|
| 中位间隔 | 193s | **224s（+16%）** |
| p10（快的那些） | 181s | **120s（更及时）** |
| p90（慢的那些） | 410s | **399s（差不多）** |
| <180s 的快档占比 | 0% | **33%** |
| >300s 的慢档占比 | ~20%+ | **28%** |
| 内部开口/日 | ≈348 | **≈350（基本不变）** |

- **一天大约开口多少次**：内部冲动约 **350 次/日**（与旧实现 348 持平）；你说话触发的
  `force` 回应路径频率不变，所以**总开口量仍是改造前同一量级**。按当前实际库折算
  （近 24h 239 条表达、含清醒/停机时段），一天约 **240–350 条**，取决于她当天清醒多久。
- 真正的变化是**形状**：想说话时 1–2 分钟就会开口（旧实现最早也要 180s），不想说时能
  静到 6–25 分钟；不再出现"永远 3 分钟一拍"的窄带。中位数值略长来自长静默段变多，
  被快档补回了次数。

## 6. 取消测试：还原旧 `should_speak` ⇒ 新用例红

还原方式（commit 后）：`git checkout 30ba2d6 -- src/elysia/soul/expression_service.py`
（`30ba2d6` = `ef43879~1`）。测完 `git checkout ef43879 -- <文件>` 还原并复跑绿。

### 红态 A：纯映射/接线用例（旧文件没有新符号）

```
_______________ ERROR collecting tests/unit/test_w2_expression_rhythm.py _______________
E   ImportError: cannot import name 'EXPRESS_INTERVAL_MAX_S' from
    'elysia.soul.expression_service'
ERROR tests/unit/test_w2_expression_rhythm.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.45s
```

### 红态 B：行为级用例（只有 `should_speak`，旧实现下是**断言失败**）

```
F..                                                                      [100%]
================================== FAILURES ===================================
_______________ test_observed_thresholds_are_not_two_constants ________________
>       assert distinct >= 4, f"只观测到 {distinct} 种间隔 {measured} ⇒ 仍是固定节拍器"
E       AssertionError: 只观测到 1 种间隔 [600.0, 600.0, 600.0, 600.0, 600.0]
        ⇒ 仍是固定节拍器
E       assert 1 >= 4
1 failed, 2 passed in 0.34s
```

### 绿态：还原新实现后

```
....................                                                     [100%]
20 passed in 0.59s
```

## 7. 边界与未做

- 不动 `brain.py` 的 `will.direction`（`rhythm_interval_s` 只读 feelings/will 字段）。
- 不新增依赖（纯标准库 `math`）。
- 不重启她；不 push；未改 `word_block` / 队列语义 / worker 切分。
- 未改心跳拍内的落库顺序；`should_speak` 仍是廉价 O(1)、非阻塞。
- 旧的 `EXPRESS_ACTIVE_INTERVAL_S / EXPRESS_QUIET_INTERVAL_S` 已删除（仅历史 docs 引用）。
