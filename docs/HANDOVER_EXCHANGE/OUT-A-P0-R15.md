# OUT-A — P0-R15 日志流编码交接单（A 窗 · 2026-10-18）

> 来源：P0-R14 事故排查中**发现但按作用域未修**的缺陷；总领核实后立卡 `task-13`。
> 证据（真机 `data/logs/soul.err.log` 21:07:06，2574 B）：文件开头是
> `--- Logging error ---` + `Traceback ... logging/__init__.py:1163 in emit`。
> 根因：Windows 重定向下 `sys.stderr`/`sys.stdout` 是 **cp936(GBK)**，日志事件里的 `⇒`
> 不可编码 ⇒ 标准库 logging 在 emit 里抛 `UnicodeEncodeError`，**她的真实死因被 logging
> 自己的报错盖住**（R2/R14 的取证链就建在 stderr 上）。

## 1. 提交

| commit | 标题 |
|---|---|
| `5ef3b48` | fix(log): 日志流编码加固——cp936 stderr 不再被 logging 自己的 UnicodeEncodeError 污染 (P0-R15) |

写作用域：`src/elysia/core/log.py`、`tests/unit/test_logging_stream_encoding.py`（新建）。

## 2. 改动（极小、无格式变化）

`setup_logging` 在建 handler **之前**新增 `_harden_stream(sys.stdout)` / `_harden_stream(sys.stderr)`：

- **先判 None**（`pythonw.exe` 下 stdout/stderr 可能是 None，本工程踩过"面板一启动就退出"）；
- 没有 `reconfigure` 的流（pytest capture / 自定义流）直接跳过；
- `reconfigure(errors="replace")`：**保留原编码**，只把不可编码字符替换为 `?`；
- 已关闭/重配失败则 `contextlib.suppress(...)` 吞掉——"为了让日志更安全"绝不能让进程起不来；
- **文件 handler 仍是 UTF-8、日志格式一字未改**（文件通道保留完整 Unicode）。

## 3. 取消测试红证（实测）

用例 `test_setup_logging_survives_cp936_stderr_with_non_gbk_chars`：
用 `io.TextIOWrapper(io.BytesIO(), encoding="cp936", errors="strict")` 替换 stdout/stderr，
`setup_logging()` 后写含 `⇒` + `🔥` 的事件。

- **去掉 `_harden_stream` 调用** ⇒ 红，实测就是真机同款：
  `UnicodeEncodeError: 'gbk' codec can't encode character '\u21d2' in position 188`
  （经 colorama 的 `ansitowin32.py` 抛出，与生产路径一致）。
- 还原 ⇒ 绿（输出里 `⇒`/emoji 变成 `?`，没有 `--- Logging error ---`）。

另外两条：
- `test_file_log_keeps_full_unicode`：控制台替换不影响文件日志，`elysia.log` 里 `⇒`/`🔥` 原样保留；
- `test_setup_logging_tolerates_none_streams`：stdout/stderr 都是 None 时 setup 不崩。

## 4. 真机真进程验证（不等她重启也能做的那部分）

用**独立进程 + 生产同款双重重定向**（stdout/stderr 都重定向到文件、默认 cp936），
只 import `elysia.core.log`，不碰她的库、不启动她：

```
exit=0
stdout 830 B（含 probe 事件，⇒/emoji 被替换为 ?）
stderr  0 B
stdout/stderr 均无 "Logging error" / "UnicodeEncodeError"
```

修复前同一场景会在 stderr 留下 2574 B 的 logging traceback（即事故现场那份文件的来源）。
⚠️ **她当前进程（18856/15860，21:07:03 启动）早于本修复** ⇒ 下一次重启后 stderr 才会真正干净；
重启由总领协调用户执行。

## 5. 门禁

ruff ✅ / format ✅ 167 files / mypy ✅ 60 files / pytest ✅ **561 passed**（558 + 3 新，零回归）。

## 6. 备注

- 本卡顺带回答了一个取证问题：**stderr 以前"没有内容"并不代表没异常**——可能是 logging
  自己把 stderr 占满（2574 B），而 `soul.out.log` 里才有真实行。以后看 stderr 要先确认
  有没有 `--- Logging error ---`；现在这条噪声源已被消除。
- 与 72 小时炸弹同属"静态检查看不见、只有问'长时间运行会怎样'才发现"的一类：
  日志编码问题不会让测试红，只会在**真实重定向 + 真实字符**下才暴露。
