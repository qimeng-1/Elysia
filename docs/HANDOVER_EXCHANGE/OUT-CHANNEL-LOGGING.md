# OUT-CHANNEL — 桥日志落盘 + 语音观测（通道窗 · 2026-10-20）

> **来源**：总领真机发现（2026-10-20 00:2x）：B+C 已端到端生效（短句 6 字走语音、长句 47 字走文字），
> 但 `data/logs/telegram_bridge.log` 停在 04:07:11 / 629B，而新桥 00:19:18 起的 ——
> `medium=voice reason=<rule>` 这些观测行**全部丢失**。
> **要求**：桥自己写日志文件，并让面板看得到语音载体决策；不重启桥（由总领统一重启加载）。

---

## 1. 根因（写入交接单留档）

桥计划任务用 **`pythonw.exe`** 启动，其**父进程是 Task Scheduler（svchost）** ⇒ 进程**没有 stdout**。
旧实现只依赖 `logging.basicConfig(stream=sys.stdout)`（且没有任何文件 handler）⇒
所有 `log.info/warning`（含本卡最关键的 `medium=voice|text reason=...`）**全部丢失**。
**结论：桥的日志必须自己落盘，不能依赖 stdout 重定向。** 本次已改。

---

## 2. 变更（只动 `scripts/` + 我的测试）

| 文件 | 内容 |
|---|---|
| `scripts/telegram_bridge.py` | `setup_logging()`：`RotatingFileHandler` 写 `data/logs/telegram_bridge.log`（1MB 轮转 / 保留 3 份，时间+级别）；**幂等**（同文件不重复挂 handler）；启动配置摘要；`medium` 行带 `intent=`；语音失败行带 `detail=` |
| `scripts/elysia_panel.py` | `/api/status` 的 `channel` 新增 `voice` 小节：最近 `medium`/`reason`/是否失败/缓存文件数/最近语音时间（只读解析桥日志 + 缓存目录） |
| `scripts/elysia_panel.html` | 服务卡片加「语音载体」「语音缓存」两行（手机上也看得到） |
| `tests/unit/test_tts_voice.py` | 25 -> **30 用例**：handler 幂等、`medium` 行落盘、启动摘要不含 token、轮转不抛异常、面板 voice 观测 |

### 日志行为
- 写入 `data/logs/telegram_bridge.log`，格式 `%(asctime)s %(levelname)s %(message)s`，UTF-8；
  `>1MB` 轮转、保留 3 份（`telegram_bridge.log.1..3`）。
- **不依赖 stdout**（pythonw 下 stdout 为空）；`log.propagate = False`，只走文件，避免双写。
- 启动摘要（**token 绝不出现**）：
  `tts.enabled=True max_chars_for_voice=30 text_intents=… prefer_voice_intents=… api_url=… api_style=… state_max_age_s=…`
  配置缺失/非法/关闭也会各写一行（`tts.config=missing|invalid` / `tts.enabled=False`）。
- 每条气泡：`medium=voice reason=<rule> len=N intent=<...>`；
  语音失败降级：`medium=text reason=voice_failed detail=<last_voice_error> len=N intent=<...>`。
- `--once` 额外向 stdout 打一行结果（前台联调方便；pythonw 下自动跳过）。

### 面板 `channel.voice` 字段
`{configured, cache_files, last_voice_ts, medium, reason, failed, last_medium_ts, log_path}` ——
来源为桥日志尾 256KB + `data/cache/tts_voice/*.ogg`；桥不监听端口，这是跨进程只读观测。

---

## 3. 取消测试红证（均实测变红；还原后 sha256 与原文一致）

1. **不防重复 handler**（去掉同文件守卫）⇒ `test_setup_logging_is_idempotent` RED：
   `assert 2 == 1`（同一日志文件被挂了两个 RotatingFileHandler ⇒ 双写）。
2. **不挂文件 handler**（回到"只依赖 stdout"）⇒ `test_medium_line_is_written_to_disk` RED：
   `FileNotFoundError: ...bridge.log`（日志行根本没落盘 —— 正是真机观测缺口的复现）。

还原核对：`telegram_bridge.py` sha256 前后均为 `D9BF1763…94AD`。

---

## 4. 门禁四件套（当前工作树）

```
uv run --no-sync ruff check              -> All checks passed!                            (exit 0)
uv run --no-sync ruff format --check     -> 200 files already formatted                   (exit 0)
.\.venv\Scripts\python.exe -m mypy src   -> Success: no issues found in 63 source files   (exit 0)
.\.venv\Scripts\python.exe -m pytest     -> 732 passed, 13 skipped in 98.63s (0:01:38)    (exit 0)
```
加数说明：基线 686+13；A 窗 W3 新增（`mypy` 60→63 files 可旁证）+41；本卡 +5 ⇒ 732+13。
（`test_tts_voice.py` 单跑 30 全绿。）

---

## 5. 重启与生效范围

- **桥需要重启**才能加载"日志落盘 + medium 行"（当前运行桥仍是旧代码；按总领要求本窗没有重启）。
- **面板需要重启**才能加载 `/api/status` 的 `voice` 小节与界面两行（面板是独立 pythonw 进程）。
- 重启后验证：
  ```powershell
  Get-Content data\logs\telegram_bridge.log -Tail 20        # 应看到 tts.enabled=... 与 medium=... 行
  Invoke-RestMethod http://127.0.0.1:8770/api/status | % channel.voice   # 面板 voice 小节
  ```
- 若日志里出现 `medium=text reason=voice_failed detail=<code>`，可据此定位：`http_error/timeout/bad_audio/ffmpeg_missing/ffmpeg_failed/...`。

---

## 6. 已知限制 / 备注

1. 面板 `voice` 小节的"最近一次"是**桥日志里的最后一条 `medium=` 行**；桥没跑过时为空（`media=null`）。
2. `channel.voice.failed` 仅表示**最后一次**载体决策是 `voice_failed`，不是历史失败次数。
3. 轮转按 1MB/3 份：按她的节奏（约 16 分钟一句）日志增长很慢，足够。
4. 本卡不改 `src/`；`test_dual_heartbeat_interleave` 的 flake 属 A 窗既有问题，未碰。
5. 日志中不含 token；启动摘要只打印 api_url 等配置项（已用测试断言）。
