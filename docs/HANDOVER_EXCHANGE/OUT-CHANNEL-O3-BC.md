# OUT-CHANNEL — O3 `no_autostart` 跨重启失效 + B+C 语音字幕/载体选择（通道窗 · 2026-10-19 深夜）

> **状态**：代码与测试完成、门禁全绿，**尚未 commit**（按总领指令：等 W2 落地、统一重启前一起提交，
> 避免桥在重启时半新半旧）。W2 已由 A 窗落地（`ef43879` / `6330786`）。
> **重启需求**：`telegram_bridge.py` 需重启加载新代码；`soul.ps1` / `watchdog.ps1` 由看门狗每分钟执行，
> 脚本改动**下一次巡视即生效**（不重启机器、不启停她）。GPT-SoVITS 9880 保持运行（PID 19052）。

---

## 1. 变更文件（均未提交）

| 文件 | 内容 |
|---|---|
| `scripts/no_autostart.ps1` | **新建**：标记读写的单一事实源（boot_id + 旧格式兼容 + `-SelfTest`），UTF-8 **BOM** |
| `scripts/soul.ps1` | `stop` 改为写 JSON 标记（`Set-NoAutostartMarker`）；dot-source 共用模块 |
| `scripts/watchdog.ps1` | 启动判断改为 `Get-NoAutostartState`；过期标记记 `stale_marker_ignored` 后自动拉起 |
| `scripts/tts_voice.py` | B+C：新增 `decide_medium(_with_reason)`、配置字段（全部可调）、`--check` 打印阈值 |
| `scripts/telegram_bridge.py` | B：`send_voice(caption=)` + 每块字幕一一对应；C：读 `expression_log` 状态决定载体+日志 |
| `tests/unit/test_tts_voice.py` | 16 -> **25 用例**（9 条新增：decide 边界/状态/字幕/长消息走文字/过期状态） |

未碰 `src/`、她进程、A 窗测试。运行时配置：`data/run/tts.json`（B+C 字段已合并，UTF-8 无 BOM）。

---

## 2. O3 `no_autostart` 跨重启失效（用户已批准补漏）

**问题**：`soul.ps1 stop` 写的标记跨重启存活 ⇒ 机器重启后看门狗永远
`verdict=stopped_by_user | action=none`，安静地不干活。

**语义**：用户主动停机只抑制**当次开机会话**；跨一次重启自动失效。

**实现**（`scripts/no_autostart.ps1`，soul/watchdog 共用，不写两遍）：
- 标记 JSON：`{"stopped_at": "<ISO>", "boot_id": "<当次开机时间秒级>", "reason": "..."}`（UTF-8 无 BOM）。
- `boot_id = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime`。
- 读取：`boot_id` 不同 ⇒ `stale`（不抑制）；相同 ⇒ `active`（抑制）。
- **向后兼容旧 0 字节空文件**：用 mtime 与开机时间比 —— 早于开机 ⇒ `legacy_stale`（放行），晚于 ⇒ `legacy_active`（抑制）。
- 读不到开机时间 ⇒ **保守**（标记仍视为有效，尊重用户停机）。
- 看门狗在过期时写日志：`stale_marker_ignored | state=stale | boot_id=... | marker_boot_id=...`，然后正常自动拉起。

**自测（取消测试等价物）**：
```powershell
powershell -ExecutionPolicy Bypass -File scripts\no_autostart.ps1 -SelfTest
```
覆盖 5 例（同会话 JSON 抑制 / 跨重启 JSON 放行 / 旧空文件且旧 放行 / 旧空文件且新 抑制 / 不存在 absent）：
```
PASS  same-boot JSON: active
PASS  cross-boot JSON: stale => not active
PASS  legacy empty + old mtime: stale
PASS  legacy empty + new mtime: active
PASS  missing marker: absent
SELFTEST RESULT: 5 passed, 0 failed   (exit 0)
```
**红证**（临时还原：忽略 boot_id + 旧标记一律当有效）：
```
FAIL  cross-boot JSON: stale => not active
FAIL  legacy empty + old mtime: stale
SELFTEST RESULT: 3 passed, 2 failed   (exit 1)
```
还原后 sha256 与原文件逐一相同（`626D637F…D8D1`），三个 `.ps1` 均 UTF-8 BOM（239,187,191）、`PSParser` exit 0。
现有 `test_watchdog.py` / `test_soul_ps1.py` 全绿（旧空文件 mtime=now ⇒ 仍按"当次会话有效"处理，行为不变）。

**真机现状（只读）**：当前标记不存在（`State=absent`）；开机时间 `2026-10-16 13:19:48`；
看门狗日志最近为 `verdict=healthy`（她 PID 21580 正常，非本改动造成）。

---

## 3. B —— 语音带字幕（底座）

- `send_voice(chat_id, ogg, *, filename="voice.ogg", caption="")`：multipart 里加 `caption` 字段，
  **纯文本、不带 parse_mode**（她的话里 `_ * [ ]` 会让 Telegram 解析整条失败）。
- **字幕与音频块一一对应**：`split_for_voice` 切出的每块 ⇒ `caption = 该块原文`；
  超 Telegram 1024 字符上限 ⇒ 截断加 `…`（`_clip_caption`）。
- 保持：失败降级文字、缓存幂等、长文本切块、token 不进日志。

## 4. C —— 她的状态决定"出声还是写字"

- 状态来源：`expression_log` 最近一行（`ts` 距今 ≤ `state_max_age_s` 默认 120s），
  解析 `instruction` JSON 的 `emotion_vector` / `intent`。`thought_log` 无状态列，**没有改它的表结构**。
- 读不到/过期 ⇒ 默认 **voice（B 底座）**，并记 `state_unreadable`（绝不因状态读不到整条不发）。
- 纯函数 `decide_medium_with_reason(text, state, cfg) -> (medium, reason)`（`decide_medium` 只取结论）。
- 规则（顺序即优先级，**全部配置化**）：
  1. `len(text) > max_chars_for_voice`（默认 30）⇒ `text` / `too_long`
  2. `intent ∈ text_intents`（默认 自检报告/好奇提问）⇒ `text` / `info_intent`
  3. 状态缺失/过期 ⇒ `voice` / `state_unreadable`
  4. `miss > voice_miss_threshold`（0.4）⇒ `voice` / `miss_high`
  5. `rest > voice_rest_threshold`（0.45）⇒ `voice` / `rest_high`
  6. `intent ∈ prefer_voice_intents`（默认 思念/主动问候）⇒ `voice` / `prefer_intent`
  7. 其余 ⇒ `voice` / `default_voice`
- 每次发送记一行日志：`medium=voice reason=<rule> len=N`（失败降级记 `medium=text reason=voice_failed`），
  可事后调阈值。`tts_voice.py --check` 打印全部默认值。
- 配置（`data/run/tts.json`，缺字段用默认、不崩）：`max_chars_for_voice` / `text_intents` /
  `prefer_voice_intents` / `voice_miss_threshold` / `voice_rest_threshold` / `state_max_age_s`。

**测试**（本卡新增 9 条，总计 25）：长度 29/30/31、信息型意图、miss/rest 阈值上下各一格
（正好等于阈值不算命中）、状态缺失/空 dict、配置可调、长消息走文字、状态 intent 走文字、
miss 高走语音带字幕、状态过期回退 voice；字幕逐块对应、caption 截断、无 parse_mode。

**红证**（临时还原）：
1. `decide_medium` 恒 voice ⇒ 长度边界 / 信息意图 / 长消息走文字 3 条用例 RED。
2. 去掉 caption 字段 ⇒ 语音成功带字幕 / multipart 字幕 2 条用例 RED（`sendVoice` multipart 解析失败）。
还原后 sha256 与原文逐一相同（`5495A298…E94A` / `AB87868C…F072`）。

---

## 5. 门禁四件套（当前工作树，未提交）

```
uv run --no-sync ruff check              -> All checks passed!                            (exit 0)
uv run --no-sync ruff format --check     -> 192 files already formatted                   (exit 0)
.\.venv\Scripts\python.exe -m mypy src   -> Success: no issues found in 60 source files   (exit 0)
.\.venv\Scripts\python.exe -m pytest     -> 686 passed, 13 skipped in 92.83s (0:01:32)    (exit 0)
```
加数：基线 **666 + 11**；A 窗 W2 落地后 **+11 passed / +2 skipped**；本卡 +9 用例 ⇒ **686 + 13**。
（`test_tts_voice.py` 单跑 25 全绿；禁止 `uv run pytest`、禁止 `-q`，本次按规矩跑。）

---

## 6. 已知限制 / 发现未修

1. **面板/终端仍按"文件存在"显示 `no_autostart`**（`elysia_dash.py:541`、`elysia_console.py`）：
   过期标记会被正确忽略，但界面仍可能显示"已标记故意停机"。本卡按总领边界只动 soul/watchdog/共用模块，
   **未改面板**；建议后续卡让 dash 复用同一套 boot_id 判断（或读脚本输出的 state 文件）。
2. 过期标记**不自动删除**（只忽略 + 记日志），留作审计；若想清理可后续加。
3. B+C 只在 Telegram 桥生效；桌宠/其它通道不在本卡。
4. 语音仍要求 GPT-SoVITS 9880 在跑；当前保持运行（PID 19052，约 2.5GB 显存），是否开机自启由总领/用户定。
5. 桥重启后才会加载 B+C；`no_autostart` 脚本改动下一次看门狗巡视即生效。

## 7. 自检写作用域

变更仅 `scripts/no_autostart.ps1`（新）、`scripts/soul.ps1`、`scripts/watchdog.ps1`、
`scripts/tts_voice.py`、`scripts/telegram_bridge.py`、`tests/unit/test_tts_voice.py`、本交接单。
未碰 `src/`、她的进程、A 窗测试；工作树里这些为我未提交的改动，等总领统一提交/重启。
