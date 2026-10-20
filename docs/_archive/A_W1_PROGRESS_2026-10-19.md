# A 窗 W1：罐装句改造 · 验收与取消测试留档（2026-10-19）

> 交付窗：`a-foundation-2`（A 地基窗，上下文已重建）。唯一任务：W1。
> commit：**`e2b094f`** `fix(express): 罐装句池扩充 + 状态选句 + LLM 重试 + 降级留痕 (W1)`
> 基线：624 passed + 11 skipped（开工前实测 88.91s）。本窗新增 26 个用例，0 回归。

## 1. 改了什么

| 文件 | 内容 |
|---|---|
| `src/elysia/llm/micro.py` | 每个 intent 4 个变体（8 个 intent，含「暂不回应」）；尾缀池按 `miss/rest/chat/curious/neutral` 分档（各 ≥3 句）；新增 `mood_tier` / `pick_variant(intent, emotion_vector, recent)`：按她的真实感受维度分档选语域 + `recent` 降权 + 连续两条不得相同。纯模板、确定性、不拼用户原话（T2）。 |
| `src/elysia/soul/expression_service.py` | `run_job` 的 LLM 段改为 `_speak_with_retry`：整条链降到微声 ⇒ **同 prompt 重试 1 次**（`LLM_RETRY_TIMES=1`），仍失败才用"状态选句 + 反重复"的微声；重试期间不重复触发 `on_degrade`（一次开口只计一次降级感受）。`expression_log.validation.reason` 写固定代码（`llm_timeout`/`llm_http`/`llm_empty`/`validate_word_block`/`retry_exhausted` 等），原始原因写 `validation.degrade_detail`。`_recent_micro` 为进程内环形缓冲（不落库）。 |
| `tests/unit/test_w1_micro_variants.py`（新） | 池规模/分档命中/确定性/空 recent/反重复/穷举容量/T2 保护词不出现。 |
| `tests/unit/test_w1_retry_and_reason.py`（新） | 重试用第二次结果、主声成功不重试、两次失败⇒`retry_exhausted`、降级感受只计一次、worker 内重试不卡心跳拍、各原因代码落库、连续微声不重复。 |
| `tests/unit/test_llm_proxy_heal.py` | 既有用例断言按新契约迁移（见 §3），用例数不变。 |

红线遵守：未碰 `body/pet.py`、`assets/pet/**`；未 push；未起停她的进程；未改 `word_block` 拦截语义；未新增第三方依赖；未改她的库（153 条历史 `(无记录)` 行**未回溯**——她正在运行，不写她的库；新行起写清原因）。

## 2. 门禁四件套（commit 依据与实测尾行原文）

Lead 于 22:5x 独立复跑（commit 前）：

```
uv run --no-sync ruff check            -> All checks passed!
uv run --no-sync ruff format --check   -> 187 files already formatted
.\.venv\Scripts\python.exe -m mypy src -> Success: no issues found in 60 source files
.\.venv\Scripts\python.exe -m pytest   -> 666 passed, 11 skipped in 110.23s
pytest tests/unit/test_w1_*.py         -> 26 passed
```

本窗 commit 前同口径复跑（22:44 左右）：

```
uv run --no-sync ruff check            -> All checks passed!
uv run --no-sync ruff format --check   -> 187 files already formatted
.\.venv\Scripts\python.exe -m mypy src -> Success: no issues found in 60 source files
.\.venv\Scripts\python.exe -m pytest   -> 666 passed, 11 skipped in 91.62s (0:01:31)
```

**加数成立**：666 = 624 基线 + 本窗 26 + O 窗 16（`tests/unit/test_tts_voice.py`），0 回归。
说明：commit 时刻工作树里 O 窗正在做自己的取消测试，`scripts/telegram_bridge.py` 处于临时态；
本窗的 commit 依据是上面 22:5x 的独立全量绿，不依赖 O 窗临时文件。O 窗随后又落了
`a348436`/`dc4c141` 两个自己的 commit。

另：仓库 `.git/hooks/pre-commit` 的 `INSTALL_PYTHON` 仍指向旧机器路径
（`D:\重置准备\备份\...`），本机跑不了 hook；本次 commit 用 `--no-verify`，门禁由**人工四件套**保证。

## 3. `test_llm_proxy_heal.py` 为什么不是"放宽断言"

旧断言 `assert "10061" in validation["reason"]` 依赖"单次尝试的原始失败原因原样进 reason"。
W1 后 `reason` 是**固定代码**（两次都失败 ⇒ `retry_exhausted`），后端原文迁到 `degrade_detail`。
新断言是两条、且更严：

1. `validation["reason"] == "retry_exhausted"`（**新增严格等值**，旧实现下永远不成立）；
2. `"10061" in validation["degrade_detail"]`（原证据不丢，只换字段）。

**重试逻辑被写坏时这个用例会红的四条路径**（验收留档）：

1. 根本没重试 ⇒ 走不到 `retry_exhausted` 分支，`reason` 是旧式长句/空 ⇒ 两条都红；
2. 重试了但只取第一次结果 ⇒ 本用例的 reason 可能对，但
   `test_retry_uses_second_attempt_result`（`text == "第二次说成了。"` 且 `calls == 2`）红；
3. `persist` 不再写 `degrade_detail` ⇒ 第 2 条红；
4. `classify_degrade_reason` 退化 ⇒ 第 1 条红。

## 4. 取消测试：还原旧实现 ⇒ 新用例红（原文片段）

还原方式（commit 后）：`git checkout fa1c7db -- src/elysia/llm/micro.py src/elysia/soul/expression_service.py`
（`fa1c7db` = `e2b094f~1`）。测完用 `git checkout e2b094f -- <两个源文件>` 还原。

### 4.1 红态 A：旧 `micro.py` ⇒ 新纯函数用例 collection error

```
_______________ ERROR collecting tests/unit/test_w1_micro_variants.py _______________
E   ImportError: cannot import name 'INTENT_VARIANTS' from 'elysia.llm.micro'
    (C:\Elysia\Elysia_two\Elysia\src\elysia\llm\micro.py)
=========================== short test summary info ============================
ERROR tests/unit/test_w1_micro_variants.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.52s
```

### 4.2 红态 B：旧 `run_job`/旧 `persist` ⇒ 行为用例断言红

```
F.F.FFFFF                                                                [100%]
7 failed, 2 passed in 0.88s
```

（2 个通过的是**回归护栏**：`test_main_success_is_not_retried`（主声成功本就不重试）与
`test_degrade_feeling_is_counted_once_per_job`（旧版单次调用也只计一次）——它们本来就该在旧实现下绿。）

关键失败原文：

```
>       assert backend.calls == 2, "第一次失败后必须同 prompt 再试一次"
E       AssertionError: 第一次失败后必须同 prompt 再试一次
E       assert 1 == 2
tests\unit\test_w1_retry_and_reason.py:137: AssertionError

>       assert validation["reason"] == "llm_http"
E       AssertionError: assert 'main 无文本：网络层...inError 10061' == 'llm_http'
E         - llm_http
E         + main 无文本：网络层（URLError）: WinError 10061

>       assert validation["reason"] == "validate_word_block", "T2 拦截同样要能按词查到"
E       AssertionError: T2 拦截同样要能按词查到
E       assert 'main 被校验拦截：word_block:痛' == 'validate_word_block'

>       assert first.text != second.text, "连续两条微声不得相同（单调感的来源）"
E       AssertionError: 连续两条微声不得相同（单调感的来源）
E       assert '……嗯，陪你聊～。' != '……嗯，陪你聊～。'
```

最后一条正是真机里出现 99 次的那句——旧实现下她连续降级时**只会说同一句**。

### 4.3 绿态：还原新实现后

```
..........................                                               [100%]
26 passed in 0.71s
```

## 5. 量化：她现在能产出多少种句子（旧 vs 新）

读她的真库（`data/heartbeat.db` 只读，近 24h，234 条表达）：

- 档位分布（按新 `mood_tier`）：`chat 205（87.6%）/ miss 27 / neutral 2`；intent：
  `发呆呓语 209（89.3%）/ 回应 20 / 好奇提问 2 / 主动问候 1 / 空 2`。
- 旧版：`发呆呓语 + chat` ⇒ **恒 1 句**「……嗯，陪你聊～。」；全池 7 intent × 4 档 = 28 句。
- 新版：`发呆呓语 + chat` ⇒ **16 句可轮换**（4 开头 × 4 尾缀，连续两条不重复）；
  全池 8 intent × 4 变体 × 18 尾缀 = **576 句**（自洽下限 360 = 8 × 3 变体 × 5 档 × 3 尾缀）。
- 可复跑：`pytest tests/unit/test_w1_micro_variants.py -k "capacity or consecutive"`。
  `capacity` 断言为**自洽式**（`= 开头数 × 尾缀数`、下限随池子规模推导），不钉死 576/16 这类硬数字，
  以后增删变体不会误报；只有"池子缩水到旧量级"才会红。

## 6. 已知边界

- 只在她**降级到微声**时选句；主声/次声正常时微声池不参与（不改变正常开口的内容来源）。
- 重试只在"整条链都降到 micro"时发生（次声接住不重试——她已经说出了真话，且不重复花钱）；
  一次开口最多两次 LLM 往返，跑在表达 worker 的独立任务里，心跳拍不等它。
- 历史 153 条 `expression_log.validation.reason` 为 null 的行**不回溯、不改库**；从本 commit 起的新行写代码。
