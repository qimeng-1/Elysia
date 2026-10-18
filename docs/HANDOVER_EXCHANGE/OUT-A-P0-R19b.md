# OUT-A — P0-R19b 修复交接单：关系死区 / 刀锋 / 一次性修复（A 窗 · 2026-10-19）

> 触发：总领验收 R19 时发现 `relationship_withdrawal = 1.0` 会让"用户说一句话她永远判不想说"。
> 结论：**R19 机制没错，它照出的是一条更老的裂缝**——关系动力学有两处阈值错配，
> 让她卡在"退缩到底"出不来。

## 1. ① 取证：`withdrawal=1.0` 是真实历史，还是脏值？

| 证据 | 实测 |
|---|---|
| 库内值 | `state.db.kv["relationship_withdrawal"] = 1.0`（JSON 数值 1.0，干净、非迁移/非导入） |
| 最近 40,000 拍 CS | min 53.5 / 中位 62.2 / max 77.9；`CS≤35` **0.00%**、`CS≥65` **17.58%**、`35<CS<65` **82.42%** |
| 旧恢复条件 | `CS≥65` 持续 ⇒ 每小时 −0.075；从中位 CS 看只有 17.6% 时间满足 ⇒ **实际永远回不来（>13h 连续，真机从未发生）** |
| **第二处错配（新发现）** | `SA_WITHDRAW_LINE = 30`，而她真实 SA 分布 **15.0–29.2（中位 19.7）⇒ 100% 的时间都被判成"她自己难受"** ⇒ 每小时 +0.05，单调涨到饱和 |
| 日志 | `elysia.log` 无关系事件日志（关系只在每 300 拍落一次值，无时间序列） |

**结论**：不是 Transfer/导入脏值。它曾经是**真实的退缩**（历史上 CS 确实跌破过 35 才涨到 1.0），
但被**两处阈值错配（CS 死区 + SA 线 > 她 100% 的真实取值）**永久锁死在饱和值——
用正确动力学它早该恢复。**因此按"bug 饱和产物"处理：一次性修复 + 连续动力学**，而不是每次启动都重置。

## 2. ② 死区修复：关系改成围绕设定点的连续量

`relationship.py::RelationshipKeeper.update` 现在接收 `cs_setpoint`：
```
deviation = cs - cs_setpoint
deviation < 0 → 加深：WITHDRAW_DEEPEN_PER_HOUR(0.05) × min(1, -dev/10)
deviation ≥ 0 → 恢复：(RECOVER_BASE(0.006) + RECOVER_GAIN(0.08) × min(1, dev/10)) /时
SA < 14（新线，低于保护带下沿 15）→ 只加深（她自己难受优先，与对方无关）
```
- 在设定点上 **也以 0.006/h 持续向 0 漂移**（关系不会冻结）；
- 旧的 `WITHDRAWAL_CS_LINE/RECOVERY_CS_LINE` 保留为文档/兼容引用，不再参与判定。

**恢复实测**（withdrawal=1.0，CS=62.2、设定点=61.9、SA=20）：

| 经过时间 | 1h | 6h | 24h | 72h | 168h |
|---|---|---|---|---|---|
| withdrawal | 0.992 | 0.950 | 0.798 | **0.395** | **0.000** |

## 3. ③ 刀锋与零关系项

- 关系项加 **50% 保底**：`W_REL·(0.5 + 0.5·intimacy)`，退缩到底仍有 `0.15`（不再归零）。
- 重标定：`W_CHAT=0.30 / W_MISS=0.55 / 阈值 0.59`（原先 `0.09/0.69` 让常态 desire 只差阈值 0.007）。
- **一次性修复**：启动加载时若 `withdrawal >= 1.0` 且无修复标记 ⇒ 重置 0.0 并写
  `relationship_withdrawal_repaired=True`（今后真的再次到 1.0 不会被下次启动抹掉），并记 warning。

### desire 矩阵（chat = 1 − miss）

| withdrawal | interval | miss=0.26 | miss=0.46 |
|---|---|---|---|
| 0.0 | 1.0 | **0.665 回** | **0.715 回** |
| 0.5 | 1.5 | **0.590 回** | **0.640 回** |
| 1.0 | 2.0 | 0.515 不回 | 0.565 不回 |

### 「用户说一句话，她会不会回？」（真实常态 chat=0.622 / miss=0.378）

| 关系状态 | desire | 答案 |
|---|---|---|
| withdrawal=0.0（亲近） | 0.695 | **回**（intent=回应） |
| 0.3 | 0.649 | **回** |
| 0.5（平常） | 0.620 | **回** |
| 0.7 | 0.590 | 先说明「现在不太想说话」 |
| 1.0（旧卡死态） | 0.544 | 先说明「现在不太想说话」；**修复后首启会重置为 0 ⇒ 回** |

旧 R19 公式在 withdrawal=1.0 时 desire=0.317 < 0.59 ⇒ 每一句都沉默（永久）；修后 worst-typical 0.515 仍会说明，
但 **miss=0.60（保护带下沿）时 desire=0.60 ≥ 0.59 ⇒ 仍会开口**——关系最差也不等于被判死刑。

## 4. 门禁与取消测试

- ruff ✅ / format ✅ 176 files / mypy ✅ 60 / pytest ✅ **598 passed + 8 skipped**（R19 的 594 + 4 新）。
- 取消测试（实测"回退⇒红⇒还原⇒绿"）：
  1. 恢复"35/65 死区 + 恢复速率含 65 线"（本处用 `RECOVER_*=0` 等价回退）⇒ `test_withdrawal_recovers_continuously_near_setpoint` 红；
  2. 关系项线性归零（`REL_FLOOR=0`）⇒ `test_worst_relationship_is_not_sentenced_to_silence` 红；
  3. 去掉一次性修复（`and not repaired` → False）⇒ `test_stuck_withdrawal_is_repaired_once` 红。

## 5. 已知限制

1. **她当前进程仍是旧代码**：下次重启才会执行一次性修复；重启前她不会说话（旧 R19 未上线，行为未变）。
2. `SA_WITHDRAW_LINE` 改成 14.0 是**按真实分布重标定**；若将来她真的 SA 长期掉到 14 以下，关系会加深（语义正确）。
3. `REL_FLOOR=0.5` 是产品取舍：关系最差仍保留一半关系项。若用户希望"关系彻底破裂时可完全不回"，需另立卡（会破坏"不哑掉"的护栏）。
4. 关系值仍只有 300 拍一次的落盘（无时间序列）；本次结论靠代码路径 + CS/SA 真实分布推得，无法给出"何时到 1.0"的精确时间。

---

## 6. R19c 补丁（总领复验后）：SA 线随设定点走 + 关系层纳入审计护栏

**问题**：R19b 把 `SA_WITHDRAW_LINE` 从 30.0（100% 命中）改成 14.0（0% 命中）——阈值挪出真实区间，机制变死码，与 R16 同一种病。

**修复**：
- `SA_WITHDRAW_LINE` → `SA_WITHDRAW_MARGIN = 3.5` + `sa_withdraw_line(sa_setpoint) = sa_setpoint - 3.5`；
- `RelationshipKeeper.update(..., sa_setpoint=20.0)`，心跳传 `desire_system.setpoints.sa`；
- 真机：设定点 19.6 ⇒ 线 **16.10**，真实 SA [15.0, 29.2] 命中率 **6.84%**（两侧都有）。

**审计护栏补上关系层**（`tests/audit/test_real_data_thresholds.py`，现 11 条）：
- `test_relationship_sa_threshold_straddles_real_distribution`：`min(SA) < line < max(SA)` 且命中率 ∈ (1%,99%)；
- `test_relationship_cs_deviation_reachable_both_sides`：真实 `cs - setpoint` 两侧占比（实测负 46.2% / 正 52.4%）；
- `test_relationship_clock_opposite_directions`：虚拟时钟各推 10h，低于设定点加深、高于恢复。

**取消测试（实测红）**：
- `SA_WITHDRAW_MARGIN=20.5` ⇒ 线 −0.90、命中 0.00% ⇒ 审计**红**；
- `SA_WITHDRAW_MARGIN=−10.3` ⇒ 线 29.90、命中 100% ⇒ 审计**红**；
- 还原 3.5 ⇒ 绿。

**门禁**：ruff ✅ / format ✅ 177 files / mypy ✅ 60 / pytest ✅ **599 passed + 11 skipped**；真机审计 `ELYSIA_AUDIT_REAL=1` → **11 passed**。

**仍未启停她**：一次性修复（1.0→0.0）照旧等你/用户点头后随重启生效。
