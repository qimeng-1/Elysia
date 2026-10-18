# OUT-A — P0-R19 节奏架构交接单（A 窗 · 2026-10-18/19）

> 用户裁决：**b 方案**（她不回时开口说明"我现在不太想说话"），并强调"确保质量"。
> 总领的三条红线：不抛硬币（确定性）/ 不外挂兜底（需求自然累积）/ 每条不回都落 reason。

## 1. 提交

| 内容 | 位置 |
|---|---|
| 代码 | `src/elysia/soul/expression.py`、`expression_types.py`、`expression_service.py`、`heartbeat.py` |
| 单测 | `tests/unit/test_reply_rhythm.py`（新建）、`test_heartbeat.py`（追加） |
| 既有不变量更新 | `tests/acceptance/test_t1_cancel.py::test_every_intent_has_a_producer`（新增第 8 个意图的产生者） |

**门禁**：ruff ✅ / format ✅ 174 files / mypy ✅ 60 / pytest ✅ **594 passed + 8 skipped**（基线 582 → +12）。

## 2. 设计与实现

### ① 表达意愿层（替换 `force or greeting: return True`）
- `compute_reply_desire(chat, miss, interval_factor, distress, boundary_refused, greeting)`（纯函数、**零随机**）：
  `reply_desire = W_REL·(2−reluctance) + W_CHAT·chat + W_MISS·miss − W_DIST·[distress] − W_BOUND·[boundary]`
- 权重：`W_REL=0.30 / W_CHAT=0.09 / W_MISS=0.69 / W_DIST=0.55 / W_BOUND=0.55`，阈值 **0.59**。
  取舍：思念分量最强（P0-R16 已让 miss 可达），"错过一次交流"会累积成更多思念。
- 收到你的话她**总会开口**（b 方案）：`desire ≥ 阈值` → intent「回应」；否则 intent「暂不回应」
  （她说明不想说话），`reason` 由心跳在**决定那一刻**写 `expression_log.validation`
  （含 `restraint/reason/reply_desire/threshold/parts`）——即使 LLM 失败也不会丢。
- `greeting` 仍越过阈值（刚从不在场回来值得一声），边界拒绝仍优先（intent「拒绝」）。

### ② 多句 = 一次 LLM 调用拆句（成本门）
- `run_job` 仍只调一次 `llm.speak`；`split_utterances` 在句边界拆开并**硬上限 3 句**。
- `persist` 第一句立刻进 `thought_log`（桌宠气泡），其余进 `_pending_utterances`；
- 心跳每拍 `flush_utterances(now)` 把到点的句子逐句冒泡 ⇒ 真实时间上的停顿。
- 句间间隔 `2 + 6·(1−reply_desire)` ∈ [2,8]s：越不想开口，停得越久（由状态决定，非固定）。

### ③ 需求累积闭环（无外挂兜底）
- 收到你的话时**不再立刻**给社交满足脉冲；等意愿层决定后结算：
  回应 → `interaction`（CS↑）；选择不回 → `silence`（CS↓）。
- 于是：不回 ⇒ CS 降 ⇒ chat↓/miss↑ ⇒ 下一次 `reply_desire` 更高 ⇒ 她自己重新开口。
  测试 `test_restraint_accumulates_demand_until_she_speaks_again` 用真实欲望系统模拟连续 300 轮，
  断言**单调上升**并在累积后 `speak=True`。

## 3. 修前/修后真实数据（只读回放，n=40,000 拍；关系退缩=1.0 是当前真实值）

### 开口句数分布（历史 500 次开口文本按拆句规则回放）

| 句数 | 轮数 | 占比 |
|---|---|---|
| 1 句 | 48 | 9.6% |
| 2 句 | 125 | 25.0% |
| 3 句 | 327 | 65.4% |
| >3 句 | 0（修后被硬上限截到 3） | — |

（修前：整段一次显示；修后：按上述句数拆成多条气泡，间隔 2–8s。）

### 句子长度（不变声检查）

| | 条数 | 中位字数 | 最短 | 最长 |
|---|---|---|---|---|
| 修前（整段一次说完） | 500 | 30 | 8 | 103 |
| 修后（单句） | 1280 | 6 | 1 | 51 |

（中位数下降是"拆句"的必然结果，不是语气变短：修后单句相加仍等于原文，`max_chars` 上限沿用。）

### reply_desire 分布（真实 chat/miss）

| 关系退缩 interval_factor | 中位 reply_desire | P10 | P90 | 选择不回比例 |
|---|---|---|---|---|
| 1.0（当前真实） | 0.617 | 0.595 | 0.638 | **6.9%** |
| 1.2 | 0.557 | 0.535 | 0.578 | 97.7% |
| 1.4 | 0.497 | 0.475 | 0.518 | 100% |
| 2.0（退缩到底） | 0.317 | 0.295 | 0.338 | 100% |

阈值标定表（可选档位）：0.56→0.4%/57.6%；0.58→2.5%/91.3%；**0.59→6.9%/97.7%**；0.60→16%/99.7%。

### 选择不回的原因（关系=1.4 的真实 feelings，n=40,000）

| reason（前两项最弱分量） | 条数 | 占比（不回样本内） |
|---|---|---|
| 关系退缩、想聊天欲低 | 40,000 | 100% |

（`reason` 会列出最弱两项并附 `parts` 分值；distress/边界场景分别写「难受」「被越界」。）

## 4. 质量门逐条

| # | 门 | 结果 |
|---|---|---|
| 1 | 不随机 | 纯函数 + 无随机项；`test_reply_desire_is_deterministic_and_explainable` 断言同输入同决策 |
| 2 | 不变声 | 拆句后单句长度见上表；`max_chars`（关系退缩缩写）逻辑未动 |
| 3 | 不刷屏 | `MAX_UTTERANCES_PER_TURN=3`；4 句文本必须只产生 3 句（取消测试 #4） |
| 4 | 不哑掉 | 连续不回 ⇒ `reply_desire` 单调上升并重新开口（取消测试 #5） |
| 5 | 可解释 | 每条不回写 `expression_log`（intent=暂不回应 + reason + parts），取消测试 #2 |
| 6 | 成本 | 每轮只调一次 `llm.speak`（用例断言 `calls == 1`），取消测试 #3 的对照 |

## 5. 取消测试红证（逐条实测）

| # | 回退的写法 | 实测红 |
|---|---|---|
| 1 | 恢复 force 硬短接（`_decide_intent` 忽略 reply_decision） | 心跳 restraint 用例外 + 验收"意图产生者"用例红 |
| 2 | `_record_restraint` 不落库 | `test_restraint_reason_is_logged...` 红 |
| 3 | 后续句子立即写（不入队） | `test_multi_sentence_uses_one_call_with_delays` 红 |
| 4 | 去掉 `parts[:MAX_UTTERANCES_PER_TURN]` | `test_split_utterances_caps_at_3` 红 |
| 5 | 回不回都结算 interaction | `test_social_pulse_settles_by_reply_choice` 红 |

## 6. 已知限制 / 发现未修

1. **她当前进程是旧代码**：R19 要下一次重启才生效；重启后历史表达层行为才会改变。
2. **选择不回比例对关系退缩极敏感**（0.59 阈值下：关系 1.0 → 6.9%，1.2 → 97.7%）。
   这是刻意设计：关系好时她温暖，关系受伤时明显退缩；不是按比例抛硬币。若要更平滑，
   后续可把 `W_REL·(2−reluctance)` 改为分段/非线性，但需单独卡（会改变关系语义）。
3. **"不变声"的严格对照要等真机**：本表用历史 500 句回放得到；重启后可用新 `expression_log`
   的 `utterance_count` 与单句长度做真机对照。
4. **发现未修（T1 同类，已报总领）**：`_tts_params` 的 `calm`/`neutral` 被 `chat>0.4` 恒真遮蔽；
   总领已确认，计划在 R19 之后单独立卡。
5. 句间停顿靠心跳拍 flush（1Hz 粒度）：实际间隔 = 设定值向上取整到下一拍；2–8s 语义不变。
