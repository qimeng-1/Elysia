# T-013 关闭依据 —— 统一门禁入口（通道窗 · 2026-10-20）

> **commit**：`1ace2bf5558faa93fec5c486b8944e2cf548c0e9`（`scripts/gate.ps1` + `scripts/gate.bat`）
> **本交接单**：同 commit 之后的 docs commit。

---

## 1. 结论：不修 trampoline，用"唯一入口 + 硬拒错误用法"关闭 T-013

**T-013 的真实代价不是"命令太长"，而是"命令一错就白跑一遍门禁"**：
- `uv run pytest` / `uv run mypy` 走坏掉的 trampoline（老问题）；
- 命令行再加 `-q` 会与 `pyproject` 的 `addopts = "-q"` 叠成 `-qq`，**汇总行被吞**；
- 结果只能靠人肉记住四条命令，漏一条/多一个 `-q` 就得到一份不可采信的"绿"。

**修 trampoline 的风险高于收益**（会动 uv 安装/启动链，影响所有窗口），而问题根源是"人可以随手写错命令"。
⇒ 新增**唯一门禁入口**，把正确命令**写进代码**，把错误用法**拒绝在代码里**：

1. 入口固定四条命令、固定顺序，四条**全部跑完**（不提前 return）；
2. 每步打印命令、退出码、输出尾 3 行；汇总 `GATE PASSED` / `GATE FAILED (n)`；
3. 任何 `-q`/`-qq`/`--quiet`、`uv run pytest`/`uv run mypy`、`PYTEST_ADDOPTS` 带 `-q`、
   非法 `-Skip` 值 ⇒ **exit 2 并说明原因**（不是文档建议，是硬拒绝）；
4. `-Skip` 只用于快速迭代，且汇总显式标 `SKIPPED: <step> -- NOT FULL GREEN`，**不会被误当成全绿**；
5. `-SelfTest` 用假命令覆盖：失败退出码 / 四条全跑 / 汇总计数 / 跳过标注 / 守卫拒绝。
   **还原守卫 ⇒ 自测 5 条 FAIL**（已实测）。

**⇒ 关闭依据**：正确路径只有一个，错误路径进不去；T-013 的一切事故模式（错命令、`-q` 叠加、漏命令）
在入口处被机械拒绝。以后不需要"记住四条命令"，只需要记住**一个入口**。

---

## 2. 谁该用哪条命令

| 场景 | 用什么 | 说明 |
|---|---|---|
| 交接 / 验收 / 每张卡交付 | **`scripts\gate.ps1`**（或双击 `scripts\gate.bat`） | 唯一权威；输出四件套 + 汇总 |
| Windows 脚本/计划任务调用 | `scripts\gate.bat` | 纯 ASCII 一行转发到 ps1；参数原样透传（危险参数会被 ps1 拒） |
| 快速迭代（例如只调 pytest） | `scripts\gate.ps1 -Skip ruff-check -Skip ruff-format -Skip mypy` | 汇总会写 `NOT FULL GREEN`；**不能当交付证据** |
| 单文件调试 | `.\.venv\Scripts\python.exe -m pytest tests\unit\xxx.py` | 允许，但**不是门禁**、不算全绿 |
| 全部禁止 | `uv run pytest`、`uv run mypy`、给门禁加 `-q` | 入口会 exit 2；trampoline 与 `-qq` 两个坑都在这 |

**固定四条命令（只有 `gate.ps1` 内部使用）**：
```
uv run --no-sync ruff check
uv run --no-sync ruff format --check
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m pytest
```

---

## 3. 自测与红证（-SelfTest）

`powershell -ExecutionPolicy Bypass -File scripts\gate.ps1 -SelfTest`
```
PASS  failure => exit=1
PASS  failure summary has count
PASS  all four steps ran
PASS  only one failure recorded
PASS  skip => exit=0
PASS  skip reduces ran to 3
PASS  skip is listed in summary
PASS  skip marks NOT FULL GREEN
PASS  reject -q
PASS  reject -qq
PASS  reject --quiet
PASS  reject uv run pytest
PASS  reject uv run mypy
PASS  allow clean invocation
SELFTEST RESULT: 14 passed, 0 failed      (exit 0)
```
**红证**（临时去掉守卫：`Get-DangerousArgError` 直接 `return $null`）：
```
FAIL  reject -q
FAIL  reject -qq
FAIL  reject --quiet
FAIL  reject uv run pytest
FAIL  reject uv run mypy
SELFTEST RESULT: 9 passed, 5 failed      (exit 1)
```
还原后 `gate.ps1` sha256 与改前一致（`6DB99282…67050`）；BOM 首字节 `239,187,191`，`PSParser` 解析 exit 0。

---

## 4. 真机实跑（本机，2026-10-20 01:3x）

```
=== Elysia gate ===
root: C:\Elysia\Elysia_two\Elysia

[RUN ] uv run --no-sync ruff check
[EXIT] 0
    All checks passed!

[RUN ] uv run --no-sync ruff format --check
[EXIT] 0
    203 files already formatted

[RUN ] .\.venv\Scripts\python.exe -m mypy src
[EXIT] 0
    Success: no issues found in 63 source files

[RUN ] .\.venv\Scripts\python.exe -m pytest
[EXIT] 0
    ........................................................................ [ 95%]
    ................................                                         [100%]
    739 passed, 13 skipped in 98.65s (0:01:38)

=== SUMMARY ===
ran=4/4 failed=0 skipped=0
GATE PASSED
```
（第一次真跑时 pytest 恰逢 A 窗 W3 在改 `src/elysia/memory/retrieve.py` + 未入库的
`test_s0_expression_not_retrieved.py`，出现 1 红；单跑该文件通过，随后重跑全绿 739。
**这是并行窗口的中间态，不是 gate 的问题** —— 也说明 gate 报红/报绿都如实。）

---

## 5. 备注

- `gate.ps1` **不启停任何进程**（不碰她、不重启桥/面板），只跑四条只读命令。
- `gate.bat` 纯 ASCII（首字节 64,101,99 = `@ec`），避免 cmd/ANSI 编码坑。
- 若未来新增门禁步骤（如 audit slow tests），只改 `gate.ps1` 的 `$steps` 数组；所有窗口自动跟随。
- `test_dual_heartbeat_interleave` 的既有 flake 已由 A 窗 `104dd53` 加固，与本卡无关。
