# 桌宠专属会话（怎么用这个窗口）

> 配套：[HANDOVER.md](HANDOVER.md)（完整交接文档，25 条踩坑记录都在这儿）
> 本文件只解决一件事：**让"调桌宠"这件事跨会话、跨上下文上限地长期做下去。**

---

## 1. 上下文满了会怎样

**不用做任何事** —— DSH 会自动把长对话压缩成摘要后继续（本会话已经经历过至少一次压缩）。

补充事实（已核实）：

| 事实 | 值 |
|---|---|
| 本会话 id | `97056061-dbde-4bd7-bb8c-bdf867944bc1` |
| 记录文件 | `C:\Users\123\.dsh\sessions\--D-~91CD~7F6E~51C6~5907-~5907~4EFD-Elysia-Elysia_two--\session-97056061-….jsonl.zstd` |
| 体积 | 约 **7.9 MB**（压缩后）—— **完整历史都在盘上**，可恢复 |

**关键认识：真正的记忆不在对话里，在文件里。**
所以下面第 3 节那段话 + 仓库里的文档，才是"这个窗口"能长期存在的真正原因。

## 2. 这个窗口的定位与纪律

**只做桌宠相关的事**（素材 / 动画 / 交互 / 性能 / 五官细节）。
其他议题另开窗口 —— 混进来会挤占上下文，也会让压缩摘要把主线冲淡。

每次改动**必须**做到：

1. **门禁全绿**：`uv run ruff check` / `ruff format --check` / `uv run mypy src` / `uv run pytest`
2. **素材验收**：动过服装包就跑 `uv run python scripts/spine_verify.py`
   （检查画布裁切/尺寸一致/行为齐全/循环差 —— 每一项都对应一次真实事故）
3. **桌面验收**：改完**重启桌宠**（`python -m elysia.body.pet`）并让用户看效果
4. **更新文档**：`docs/HANDOVER.md`（结构/坑）与 `docs/PET_SESSION.md`（状态速查）保持真实
5. **确认调用点**：新增片段/功能后，去 `pet.py` 查 `IDLE_WEIGHTS` / `CLICK_POOL` /
   `DRAG_CLIP_CANDIDATES` / `WANDER_CLIP_CANDIDATES` / `STATE_CLIPS`，去 `anim.py` 查 `BRIDGE_CLIP`
   —— 本项目犯过两次"入库了却没有调用点"的错（`grab`、`notice`）
6. **批量脚本要逐步验证产出**：曾经因为 `argv.indexOf` 返回 -1 的 bug 让 32 个烘焙全失败，
   而我只看摘要没看每步行，还拿旧数据做了半天测量

## 3. 一句话恢复上下文（新会话或很久没来时，把这段贴给我）

```
继续桌宠项目。工作目录 D:\重置准备\备份\Elysia\Elysia_two\Elysia。
先读 docs/PET_SESSION.md 与 docs/HANDOVER.md，再跑一次门禁和桌面验收，
然后按我的反馈继续改。素材授权红线：Spine 与同人素材仅本地使用、不进公开仓库、不外传。
```

> 恢复旧会话（不是新开）时，跟我说一句「**继续桌宠**」即可 —— 我会把会话目标重新激活。

## 4. 状态速查（2026-10-15）

| 项 | 值 |
|---|---|
| 启动 | `python -m elysia.body.pet`（纯桌宠，不读灵魂） |
| 关闭 | 右键 → 退出 |
| 素材 | **6 套 Spine 服装**（s1~s6）、**50 个片段**、循环片段全部无缝 |
| 行为 | **7 类全部有素材**：待机 / 漫游 / 点击回应 / 拖拽漂浮 / 落地 / 动作后缓冲 / 鼠标移入 |
| 素材体积 | **94.6 MB**（不存冗余 PNG 母版；此前 767 MB） |
| 内存 | ~88 MB |
| 门禁 | ruff / format(124) / mypy strict(60) / **pytest 461 passed** |
| 素材验收 | `spine_verify.py` **全部通过**（触边 0、尺寸一致、行为齐全） |
| 提交 | 本地领先 `origin/main` **44 个**（远程仍是 `91bf19d`） |
| 已撤下 | 9 套 AI/立绘服装在 `assets/pet/_retired/`（297 MB，可恢复） |
| 清理出的过期物 | `D:\ElysiaAssets\_obsolete\`（45.6 MB，可删） |

**触发逻辑一句话版**：待机池加权轮换（18–45 秒）→ 点她 = `CLICK_POOL` 随机 →
按住 = `float` 漂浮 → 松手 = `land`（**硬切不溶解**，阻尼压缩回弹）→
鼠标移入 = `notice`（1.4 秒 + 25 秒冷却）→ 动作演完垫 `settle-bridge`（1.75 秒）再回待机 →
每 70–200 秒走一小段（`run`/`walk`）。

## 5. 常用命令

```powershell
cd D:\重置准备\备份\Elysia\Elysia_two\Elysia
$env:Path = "$env:USERPROFILE\.local\bin;$PWD\.venv\Scripts;$env:PATH"

# 跑起来 / 看门禁
python -m elysia.body.pet
uv run pytest ; uv run ruff check ; uv run ruff format --check ; uv run mypy src

# Spine 素材：预检 → **算共享画布** → 烘焙 → 全分辨率复查 → 入库 → 验收
#   全套流程与踩坑见 HANDOVER §5.1；**算共享画布**这步别跳，跳过会内容被平切
node scripts/spine_precheck.js
node scripts/spine_canvas.js                       # 写 out/<outfit>/canvas.json
node scripts/spine_bake.js elvisa_1 1_idle 24 560
node scripts/spine_bake.js elvisa_1 --derive land 24 560
python scripts/spine_check_all.py
uv run python scripts/ingest_spine.py --src <烘焙输出> --out assets/pet/costumes/<id> `
    --name "<名>" --downscale 2 --no-master --clip idle=1_idle --clip walk=2_walking_default
uv run python scripts/spine_verify.py              # ← 加了素材就跑这个

# 桌面截图（离屏，用来看眼睛/网格线这类细节）
$env:QT_QPA_PLATFORM="offscreen"   # 仅在离屏脚本里设，别影响真机启动
```

## 6. 红线（不能碰）

1. **授权**：Spine 运行时需 Spine Editor 授权；模型作者注明「解包资源，仅供学习使用」
   ⇒ 素材**只放本地**、不进公开仓库、不外传。仓库里只放不含素材的脚本
2. **她的真实数据在他机**：本地 `data/*.db` 是测试数据（576 心跳 / 22 记忆）。
   **绝不能把本地库拷到他机**（会用测试数据覆盖她的真实生命）。方向只能：他机 → 这里
3. **别在两台机器上同时跑她**（会分叉）
4. 用户自己的原始素材（`D:\图片\Saved Pictures`）**不主动删**，要问

## 7. 还没做完的（下次可以接着做）

1. **`applaud`（鼓掌）用不了**：6 套**全部**是"眉毛/眼睑横穿眼睛"（系统性作者问题，
   已逐帧全分辨率确认）⇒ 点击回应只有"挥手"和"比心"两种。要更多花样只能等作者修或找新素材
2. **接灵魂状态**：`STATE_CLIPS` 映射已写好，用 `--with-soul-data` 启动才生效
3. **`WANDER_SPEED_PX_S = 110` 是估的** —— 若看着"滑步"要和走动步频对齐
4. **共享画布让窗口变大**（s1: 528×634 → 714×674，多出的是动作摆幅空间）。
   若嫌大，可在 `spine_canvas.js` 收窄采样范围，但会重新引入裁切风险
5. **推不推送**：45 个提交待用户决定
6. 可选：给 DSH 写个 `/pet` 插件命令；或走"运行时实时渲染 Spine"路线（HANDOVER §5）
