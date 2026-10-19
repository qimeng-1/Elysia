# OUT-CHANNEL — O1/O2 语音气泡 + 收尾杂项交接单（通道窗 · 2026-10-19）

> **来源**：`docs/LEAD_DAY2_PLAN_2026-10-19.md` §O 窗（用户 2026-10-19 晚批准 "TTS 开"）。
> **写作窗**：channel-ops；写作用域 `scripts/`（+ 本交接单）。
> **总领验收**：待验收（真机语音气泡因 Telegram 网络不可达，见 §6）。

---

## 1. 提交

| commit | 标题 | 内容 |
|---|---|---|
| `a348436` | feat(telegram): O1 语音气泡——thought_log -> GPT-SoVITS -> ffmpeg ogg/opus -> sendVoice | 3 文件（+1177） |
| `dc4c141` | chore(backup): T-011 每日备份默认时间 03:00 -> 03:30（一行，UTF-8 BOM 保持） | 1 行 |

文件：
| 文件 | 说明 |
|---|---|
| `scripts/tts_voice.py` | 新建：配置/合成/ffmpeg/缓存/长文本切分/CLI（`--check`/`--init`/`--text`） |
| `scripts/telegram_bridge.py` | 接入语音：发送前先试 `sendVoice`，任一步失败降级发文本；multipart 上传 |
| `tests/unit/test_tts_voice.py` | 新建：**16 用例**（配置/两种 API/ffmpeg/缓存幂等/长文本/multipart/降级/幂等发送） |
| `scripts/backup.ps1` | T-011：`-Time` 默认 03:00 -> 03:30（UTF-8 BOM 保持：首字节 239,187,191） |

运行时（不入库）：`data/run/tts.json`（UTF-8 **无 BOM**，真机路径）；`data/cache/tts_voice/`（缓存）。
未碰 `src/**`、A 窗测试、她进程。

---

## 2. O1 事实核查（全部实测，2026-10-19 晚）

| 项 | 结论 |
|---|---|
| GPT-SoVITS | **在本机**：`D:\下载\GPT-SoVITS-v2pro-20250604` |
| 服务入口 | `一键启动API.bat` -> `runtime\python.exe api.py -hp` -> **`POST http://127.0.0.1:9880/`** JSON `{text, emotion}` -> `audio/wav`；`api_v2.py` 的 `GET /tts` 也在（脚本两种都支持，配置 `api_style`） |
| 模型 | `GPT_weights_v2/【GPT2.0】Elysia-e20.ckpt`（148MB）+ `SoVITS_weights_v2/【GPT2.0】Elysia_e24_s13080.pth`（81MB） |
| 参考音频 | `Elysia_Voice_Model/ref_{default,devoted,gentle,playful}.wav`（4 段克隆参考） |
| ffmpeg | **不在 PATH**；`runtime\ffmpeg.exe` 可用（n4.3.2），ffprobe 同目录 |
| GPU | **RTX 3060 Laptop 6GB**；启动前 0 MiB，API 加载后约 **2513 MiB** |
| 实测 | 3.9s 语音合成 ≈ **2.7–3.2s**；ogg/opus（48k mono）≈ **24KB / 4.2s** |

当前 `9880` **已由本窗启动**（`api.py`，PID 19052，外部服务，未启停她）。不注册自启；用户/总领按需启动。

---

## 3. O1 实现要点

- **配置外置**：`data/run/tts.json`（api_url/api_style/gpt_sovits_root/ffmpeg/语言/emotion/长文本上限/缓存目录/profiles）。
  **写模板不带 BOM**（`--init`；测试断言首字节不是 BOM），读入时用 `utf-8-sig` **容忍** BOM（本工程被 BOM 坑过）。
- **链路**：文本 -> GPT-SoVITS wav -> ffmpeg `-c:a libopus -b:a 48k -ar 48000 -ac 1 -f ogg` -> OGG。
- **失败降级**：合成超时/HTTP/非 WAV/ffmpeg 缺失/失败/上传失败 -> **发文本**，消息不丢（原因码落日志 + `/status` 可见）。
- **幂等**：`sha256(文本 + api_style + url + emotion + 语言 + 码率)` 做缓存 key，落 `data/cache/tts_voice/*.ogg`；
  同一句第二次**不再请求 TTS**。发送侧游标仍在内存，重启不补推历史、同一条 thought 只发一次。
- **长文本按句切**：`split_for_voice(text, max_chars)`（默认 120 字；单句过长硬切），多段各发一条语音，
  避免一个 2MB 语音。
- **`sendVoice` 必须 multipart**（Telegram 官方要求）：手写 multipart（chat_id + voice 文件 + ogg 魔数校验），
  失败重试一次，仍失败降级文本。
- **配置热重载**：`tts.json` mtime 变化自动重读；未配置时静默只发文本（不刷日志）。

### 取消测试红证（均实测变红；正确实现已先 commit）

| # | 用例 | 还原旧写法后的红态 |
|---|---|---|
| 1 | `test_same_text_is_synthesized_only_once` | 去掉缓存读取 -> 同一句请求 TTS **2 次**：`assert 2 == 1`（"被重复合成了"） |
| 2 | `test_voice_failure_falls_back_to_text_and_keeps_message` | 语音失败不降级 -> `poll_expression_once() == 0`、文本也没发（消息丢） |
| 3 | `test_send_voice_uses_multipart_and_never_leaks_token` | 把 sendVoice 改回 JSON -> `TelegramError: sendVoice 不可达（ValueError）` |

其余 13 条覆盖：配置模板 BOM-free/缺失与坏 JSON 明确报错/两种 api_style 参数/非 WAV 拒绝/ffmpeg 缺失与失败/
长文本切分/语音成功只发 sendVoice/长文本多气泡/token 不进异常/游标不重发。

### ⚠️ 取消测试纪律（总领要求写进交接单）

**做取消测试前，必须先把"正确实现" commit 掉；再做临时还原。**
还原方式：`git checkout -- <file>`（已入库）或 `git stash`（未入库）；
**工作树在你不干活时必须永远是绿的**，绝不能带着"取消修复"的临时改动离开或 commit。

本卡实际事故：我在做红证时备份 `Copy-Item` 写错（3 个位置参数），导致还原失败，
`telegram_bridge.py` 一度残留 `send_voice` 的 JSON 分支（注释"取消修复：改回 JSON"），
被总领当场抓到。已用反向替换 + sha256 双文件核对还原（匹配还原前 hash），残留标记搜索 0 命中。
**教训：临时改动也必须有可验证的回滚路径（hash/`git stash`），且先 commit 正确实现。**

---

## 4. 门禁四件套（交付状态，原文尾行）

```
uv run --no-sync ruff check              -> All checks passed!                              (exit 0)
uv run --no-sync ruff format --check     -> 187 files already formatted                     (exit 0)
.\.venv\Scripts\python.exe -m mypy src   -> Success: no issues found in 60 source files     (exit 0)
.\.venv\Scripts\python.exe -m pytest     -> 666 passed, 11 skipped in 90.62s (0:01:30)      (exit 0)
```
666 = 基线 624 + A 窗 R19/W1 新增 26 + 本窗 16（`tests/unit/test_tts_voice.py`），总领已确认加数。
（坑位：`addopts` 已含 `-q`，命令行不要再加 `-q`。）

---

## 5. O2 收尾杂项

- **微信推送归档确认**：`data/run/push_url.serverchan.bak` **在**；`data/run/push_url.txt` **0 字节（空）**
  ⇒ 推送通道已停、不再发送；备份文件保留可追溯。
- **T-011**：`scripts/backup.ps1` 默认 `-Time` 由 `03:00` 改为 `03:30`（一行）；
  文件保持 **UTF-8 BOM**（首字节 239,187,191），`PSParser` 解析 exit 0。

---

## 6. 真机验证边界

**已做（本地端到端）**：
- `python scripts/tts_voice.py --check` 全 ok（配置/ffmpeg/服务/缓存）；
- `--text "今天过得怎么样？我在呢，陪你聊一会儿。"` 实际产出 **23553 字节** ogg，`OggS` 魔数，
  ffprobe 确认 `codec_name=opus / sample_rate=48000 / channels=1 / duration=4.1065s`。

**未做（前置条件缺失，不是代码问题）**：`api.telegram.org` 本机 **TCP 443 不通**（真机实测），
因此**语音气泡无法真的发到 Telegram**；等用户网络恢复后，本卡代码即可直接生效。
验证命令（网络恢复后）：
```powershell
# 1) 服务在跑（一键启动API.bat），2) 配置在 data/run/tts.json
.\.venv\Scripts\python.exe scripts\tts_voice.py --check
.\.venv\Scripts\python.exe scripts\tts_voice.py --text "我在呢" --out data\cache\tts_voice\_sample.ogg
# 3) 桥常驻：她下一次开口应收到一条语音气泡；/status 里"语音：开"
.\.venv\Scripts\python.exe scripts\telegram_bridge.py
```

---

## 7. 未做与已知限制

1. **TTS 服务不自启**（`一键启动API.bat` 由人启动；本卡未注册计划任务）。服务不跑时桥自动降级文本。
2. Telegram 网络仍不可达（用户 Clash 规则问题）⇒ 语音气泡真机未验证。
3. 语音只覆盖 Telegram 桥；桌宠/其它通道是否出声不在本卡。
4. 单条语音上限 120 字（可配）；超长按句切多条。
5. 缓存无上限清理（`data/cache/tts_voice/`；单条约 24KB，日常量级可忽略；后续可加按天清理）。
6. 启动 GPT-SoVITS 占约 2.5GB 显存；与她的其它 GPU 用途可能冲突时由总领/用户决定是否常开。

## 8. 自检写作用域

变更仅：`scripts/tts_voice.py`（新）、`scripts/telegram_bridge.py`、`scripts/backup.ps1`、
`tests/unit/test_tts_voice.py`（新）、本交接单。未碰 `src/**`、A 窗测试、她的进程。
`git show --stat a348436 dc4c141` 可复核；`git status` 本窗文件干净。
