# 桌宠（body）交接文档

> 面向"接手继续做的人"。写清**当前状态、怎么跑、素材体系、踩过的坑、怎么加素材、已弃用的东西**。
> 最后更新：2026-10-15
>
> 👉 **长期在这个仓库里调桌宠的人，先看 [PET_SESSION.md](PET_SESSION.md)**（专属会话用法、
> 状态速查、一句话恢复上下文、纪律与红线）；本文件是**完整参考**。

---

## 1. 现在是什么状态

桌面宠物 **Elysia**，PySide6 单窗口，**纯桌宠模式**（不连灵魂数据库）。

| 项 | 值 |
|---|---|
| 启动 | `python -m elysia.body.pet`（默认 **纯桌宠**，不读灵魂状态） |
| 想接灵魂状态 | `python -m elysia.body.pet --with-soul-data`（需灵魂在跑） |
| 关闭 | 窗口上**右键 → 退出**，或杀掉 `elysia.body.pet` 进程 |
| 当前素材 | **6 套 Spine 服装**（s1~s6），共 26 个片段 |
| 内存 | **~88 MB**（滑动窗口按需解码，且**只保留"当前 + 正在淡出"两个片段**的窗口 —— 见 §3.3 #15b） |
| 窗口 | 各套自适应（s1 714×674，最窄 s5 491×661）：多出来的是**动作摆幅空间**，不裁切的代价 |

**验证"跑的是纯桌宠"**：python 进程里含 `soul` 的应为 0 个；`data/*.db` 的修改时间不应变化。

---

## 2. 素材体系

### 2.1 服装与片段

| 服装 | 名字 | 片段 |
|---|---|---|
| `s1` | 花冠白裙 | idle / walk / wave / cheer / notice / settle-bridge / land / float |
| `s2` | 女仆装 | idle / **idle-hunmi** / walk / wave / cheer / notice / settle-bridge / land / float |
| `s3` | 黑白马甲 | idle / walk / wave / cheer / notice / settle-bridge / land / float |
| `s4` | 紫黑装 | idle / walk / wave / cheer / notice / settle-bridge / land / float |
| `s5` | 白衫深裤 | idle / walk / wave / cheer / notice / settle-bridge / land / float |
| `s6` | 紫羽装 | idle / **idle-cry** / walk / wave / cheer / notice / settle-bridge / land / float |

- 全部来自 **Spine 骨骼烘焙**（见 §5.1），**同一套骨骼系统** ⇒ 各片段角色大小与站位天然一致
  （角色高统一 560px、脚底同线；这正是 AI 逐段生成做不到的）
- **循环片段 100% 无缝**（首尾剪影差 ≤ 0.06）；`land`/`notice` 是单次片段、不参与循环
- 目录：`assets/pet/costumes/<id>/<片段>/{webp/*.webp, manifest.json}` + `costume.json`
  —— **不入 git**（版权 + 体积）
- **不存 PNG 母版**：运行时只读 `webp/`，而真母版是 `.skel` + `bake.js` + `canvas.json`
  （几分钟可重烘）。存一份 PNG 把体积撑到 4.5 倍（实测 605.7 MB vs 161.5 MB），不值得
- `land` / `float` 是**派生片段**：不新画素材，在骨骼层加位移与压缩（等同动画师 K 根骨骼），
  用 `node scripts/spine_bake.js <outfit> --derive land|float` 生成

### 2.2 触发逻辑（`src/elysia/body/pet.py`）

| 什么时候 | 播什么 | 代码位置 |
|---|---|---|
| 无人打扰 | 在 **待机池**里加权随机轮换，每 18–45 秒一次 | `IDLE_WEIGHTS` / `_maybe_rotate_clip` |
| 点她一下 | **回应池**随机抽一个（cheer / wave …），播完回待机 | `CLICK_POOL` / `_on_character_click` |
| 按住拖动 | **`float`**（没有就只用物理漂浮手感） | `DRAG_CLIP_CANDIDATES` / `_begin_drag_clip` |
| 松手 | **`land`**，且**不溶解**（落地是瞬间冲击）；没有就直接回待机 | `LAND_CLIP` / `_end_drag_clip` |
| 鼠标移入 | **`notice`**（1.4 秒 + 25 秒冷却） | `NOTICE_CLIP` / `enterEvent` |
| 动作演完 | 先垫一拍 **`settle-bridge`**（1.67 秒）再回待机——"动作链"缓冲 | `BRIDGE_CLIP` / `_after_once` |
| 每 70–200 秒 | 在桌面上**走/跑一小段**（90–260px） | `_wander_clip` / `_wander_tick` |
| 气息状态变化 | 切到该状态对应的片段池（**接灵魂状态**，非 DSH 状态） | `STATE_CLIPS` |

> **缺片段会自动降级**：找不到就跳过、保持现状，不会报错也不会白屏。
> 新增片段后**必须确认它有调用点**——本项目已经犯过两次"入库了却没有调用点"的错
> （`grab`、`notice`），现在新增片段前先查 `IDLE_WEIGHTS` / `CLICK_POOL` / `*_CLIP` 常量。

### 2.3 待机池权重（`IDLE_WEIGHTS`）

```
idle-open 8（豆包素材的睁眼待机）  idle 4（Spine 主待机）
idle-hunmi 1（昏睡，仅 s2）        idle-cry 1（哭泣，仅 s6）
look-around 2  think 2  settle 1
```

---

## 3. 踩过的坑（**最有价值的一节，别再踩**）

### 3.1 素材制作

| # | 坑 | 真相 / 对策 |
|---|---|---|
| 1 | **喂给 AI 的首帧必须干净** | 喂了带 logo 的图，AI 会把 logo **一起动起来**（`雀跃.mp4` 的「崩坏3」logo 被动了 12,555 像素） |
| 2 | 提示词写"动作幅度"要明确 | 写"极轻微"→ 输出几乎静止，白费一次生成（实测有效运动帧 2/140） |
| 3 | 别写"自然地眨两次眼" | 模型让**她闭了 6.7 秒的眼**（241 帧里只有开头 17 帧、结尾 29 帧睁开） |
| 4 | 问"落地动作"要写"**只落地一次**" | 否则会给"弹跳循环"（10 秒 3 次弹跳），没法当一次落地用 |
| 5 | 待机类别做 10 秒 | 长片段磁盘/加载成本线性增长；3–5 秒最合适 |
| 6 | 逐段生成的素材**大小不一致** | 各段 AI 把角色画成不同大小（首帧高度 512~537，差 5%）。用**一个共享缩放**只会原样保留差异——必须**每段各自缩放**对齐同一目标高度（见 `asset_pipeline.py` 的 `build_costume`） |
| 7 | "底边对齐"会**抹掉纵向运动** | 逐帧把内容底边贴到画布底 → 跳跃/漂浮的上移全没了。必须用**共享的源空间→画布仿射变换** |

### 3.2 渲染 / 播放

| # | 坑 | 真相 / 对策 |
|---|---|---|
| 8 | **交叉溶解的合成数学** | 写成"旧帧按 1-α、新帧按 α"两次 `SourceOver`，实际得到 `新×α + 旧×(1-α)²`——少掉的 `α(1-α)` 变成**透明度**，每次切换她整个人会**闪成半透明**（比硬切还难受）。正解：**旧帧全不透明画、新帧按 α 压上去**；α≥1 时**不画旧帧**（否则永久残影） |
| 9 | **单次片段播完回待机是硬切** | `advance()` 里直接改 `_current` 会**绕过淡出启动**。而这是最常见的过渡 |
| 10 | 固定 dt | 定时器一有偏差（Qt 默认 `CoarseTimer` 有 ±5%），动画速度就跟着错（实测快 5%）。必须用 `perf_counter()` 的**真实 dt** + `Qt.PreciseTimer` |
| 11 | 30fps 渲染上限 | 24fps 素材在 30fps tick 上，帧停留 33/66ms 跳（标准差 13ms）。提到 16ms(~60fps) 后降到 7.8ms |
| 12 | 提高帧率会让物理变快 | `lerp k=0.06`、`bounce *= 0.88` 原本按"每 1/30 秒"标定，必须按 `dt/ref` 换算成幂 |
| 13 | 有真动画时**不要**再叠加物理呼吸/晃动 | 那层物理是"给静态立绘注入生命"用的；两层叠加会非常突兀。用**主增益** `_idle_gain`：真动画→0，单帧立绘→1（lerp 平滑过渡） |

### 3.3 性能 / 内存

| # | 坑 | 真相 / 对策 |
|---|---|---|
| 14 | **一次性解码整段动画** | 460×620 RGBA 每帧 1.14 MB，9 段 = **937.8 MB**。改为**按需解码 + 16 帧滑动窗口 + tick 内预取** ⇒ 154.5 MB，进程内存 356 → **78 MB**；顺带让"加载片段"变成瞬时 |
| 15 | 重跑更短的片段**残留旧帧** | 只写新帧不清旧目录 → 帧数与 manifest 不符、动作演到早该删的尾巴上。`matte_clip` / `build_costume` 现在**先清空目标目录** |
| 15b | **每个片段的解码窗口都不释放** | `Clip._window` 是**每片段** 16 帧，内存 = **片段数 × 16 × 单帧字节**。共享画布让单帧涨到 1.6 MB（654×614），片段数又从 4~5 增到 8~9 ⇒ **247 MB**（长跑 3000 帧 + 切换 20 轮都稳定 ⇒ **不是泄漏，是这个设计**）。修法：`Clip.release()` + `ClipPlayer._trim_windows()`，只保留"当前 + 正在淡出"两个窗口 ⇒ **247 → 73 MB**，且**与片段数无关** |
| 15c | 别只看"摘要"就以为批量脚本成功了 | 32 个烘焙因参数 bug 全失败，而我 grep 了"输出"没看到行、仍拿旧帧测了半天数据。**跑完批量要逐项确认有产出** |

### 3.4 Spine 烘焙（§7.1 用得到）

| # | 坑 | 真相 / 对策 |
|---|---|---|
| 16 | Python 绑定**全不可用** | PyPI 的 `spine-python` 是 2013 年版（相对导入都坏了）、`spinepy` 缺编译扩展。**别在这上面浪费时间** |
| 17 | **`@napi-rs/canvas` 画不出三角形贴图** | 它在 `clip+transform+drawImage` 下**产出 0 像素**（最小复现证实），而 Spine 的贴图渲染全靠这个组合。**必须用 `node-canvas`（Cairo）** |
| 18 | `triangleRendering` 默认 false | 此时只画 `RegionAttachment`，**63 个 Mesh 附件全被跳过**（画不出角色） |
| 19 | `CanvasTexture` 缺 `getImage()` | 而 core 的 `MeshAttachment.updateRegion` 会调它 ⇒ 必须 `tex.getImage = () => img` |
| 20 | `TextureAtlas` **不调用**你传的 textureLoader | `page.texture` 默认 null ⇒ 必须手动 `page.setTexture(tex)` |
| 21 | **网格线** | 逐三角形 `clip` 的抗锯齿会在相邻三角形间留**半透明发丝缝**（实测单帧 2017 个 alpha 1~40 的像素）。对策：**三角形沿重心外扩 ~1px** + **2× 超采样渲染后降采样** |
| 22 | 循环动画要**多渲一帧收尾** | 不补帧时 `walk` 首尾剪影差 3.49，会被判为"循环跳"改用乒乓 ⇒ **走路变倒着走**。补上 t=时长 那帧（与 t=0 同姿势）才真正闭合 |
| 23 | `3_applaud` **作者有问题** | 6 套**全部**都是"眉毛/眼睑横穿眼睛"（逐帧全分辨率确认）。**一律排除**，第二个点击回应改用 `5_special_ani` |
| 24 | `0_left` / `0_right` 是**空动画** | 时长 0.00s、只有 3 帧完全相同 ⇒ 转向/张望拿不到素材 |
| 25 | 第 0 帧**不一定是站姿** | `高处落地或弹跳` 是弹跳循环，f0 在半空、角色仅 423px 高；拿它当缩放基准会把该片段放大 32%。用 `borrow_transform_from` 借同机位兄弟片段的基准 |
| 26 | **画布只按 setup pose 定尺寸 ⇒ 内容被平切** | 动画摆幅比 setup pose 大（头发/裙摆甩出去）。实测已入库片段：`s1/idle` **46/145 帧触底**、`s1/walk` **49/49 帧触左右**、`s2/idle` 57 帧触顶。正解：**按"该套服装所有动画所有帧"的并集算共享画布**（`scripts/spine_canvas.js` → `out/<outfit>/canvas.json`，由 `bake.js` 读取）。修后画布 468×574 → 654×614，**触边 0** |
| 27 | `MARGIN` **没乘超采样倍数** | 留白写在 2x 画布上等于只有一半（14 → 7px 成品）。同理派生动画的位移量也必须乘 `SUPERSAMPLE`，否则"以为 12px、实际 6px" |
| 28 | **`argv.indexOf` 找不到时返回 -1** | 我写的 `filter((_, i) => i !== idx && i !== idx+1)` 在无 `--derive` 时会把**第 0 个参数（服装名）吃掉**，于是去打开 `1_idle.atlas` —— **32 个烘焙全挂在这上面**。必须先判 `idx >= 0` |
| 29 | `const` 的**暂时性死区** | 把派生常量写在 `const SUPERSAMPLE` 之前 ⇒ `ReferenceError: Cannot access 'SUPERSAMPLE' before initialization`。而且我当时只 grep 了"输出"行，**没发现脚本其实崩了**，还在对着旧帧测数据 ⇒ **跑完批量脚本要检查每一步是否真有产出**，别只看摘要 |
| 30 | **`play_once` 与 `set_clip` 的淡入淡出差异** | 落地这类"瞬间冲击"必须 `play_once(LAND_CLIP, fade=False)`；带淡入会读成"起跳" |

---

## 4. 素材来源与**授权**（重要）

- **Spine 素材**：用户提供 `爱莉希雅_by_做自我就好_*.rar`
  - 解包后是 **Spine 4.1.24** 骨骼（**不是 Live2D**——包里的 `.moc3` 是 0 字节空文件）
  - 6 套服装 × 9~10 个动画；264~159 骨骼、54~94 个图集区域；全套 3.2 MB
  - 作者在文件名里注明「**解包资源，仅供学习使用**」
- ⚠️ **Spine 运行时许可要求使用者持有 Spine Editor 授权**；模型本身也注明仅供学习
  ⇒ **自己用可以，别分发、别进公开仓库**。仓库里只放**不含素材的转换脚本**
- 解包位置：`D:\ElysiaAssets\spine_elysia\`（在仓库外）

---

## 5. 怎么加新素材

### 5.1 主线：Spine 烘焙

```bash
# 0) 依赖（只需一次）：Node + npm 包，在 D:\ElysiaAssets\spine_bake\
npm install @esotericsoftware/spine-core@4.1 @esotericsoftware/spine-canvas@4.1 canvas

# 1) 预检：图集完整性、动画清单与时长、缩略图
node scripts/spine_precheck.js

# 2) **算共享画布**（关键，别跳过）：取该套服装所有动画所有帧的并集
#    ⇒ 任何动作都不会被画布裁掉，且同套所有片段同尺寸
node scripts/spine_canvas.js            # 写入 out/<outfit>/canvas.json

# 3) 烘焙（自带缝隙修复：三角形外扩 + 2x 超采样；会自动读 canvas.json）
node scripts/spine_bake.js <outfit> <动画名> 24 560
node scripts/spine_bake.js <outfit> --derive land  24 560   # 派生：落地压缩
node scripts/spine_bake.js <outfit> --derive float 24 560   # 派生：失重漂浮

# 4) 入库前**全分辨率复查**（缩略图看不出"眉毛穿眼"这类问题！）
python scripts/spine_check_all.py

# 5) 入库（--downscale 2 对应烘焙的 2x 超采样；--no-master 不写冗余 PNG 母版）
uv run python scripts/ingest_spine.py --src <烘焙输出目录> --out assets/pet/costumes/<id> \
    --name "<菜单显示名>" --downscale 2 --no-master \
    --clip idle=1_idle --clip walk=2_walking_default --clip wave=4_wavehands \
    --clip cheer=5_special_ani --clip notice=2_walking_left:0:34 \
    --clip settle-bridge=1_idle:0:42 --clip land=land --clip float=float

# 6) **验收**（把关卡固化下来，防回归）
uv run python scripts/spine_verify.py
```

`spine_verify.py` 会检查：**画布边缘裁切**（触边必须为 0）、同套各片段尺寸一致、
7 类行为片段齐全（待机/漫游/点击回应/拖拽/落地/动作后缓冲/鼠标移入）、
循环差、manifest 帧数与实际是否对得上。**加了新素材就跑它。**

> 片段 ID 决定行为（见 §2.2）：`idle*` 循环；`notice/settle/land/wave/cheer/shy/surprise/pout`
> 是"播一次"；`float` 优先于 `grab` 作拖拽；`run` 优先于 `walk` 作漫游。
> **入库后记得确认它有调用点**（§2.2 末尾）。

### 5.2 备选：AI 视频（豆包等）抠图

`scripts/asset_pipeline.py`（**仍可用**，只是当前没在用）：

```bash
uv run python scripts/asset_pipeline.py inspect <视频...>        # 体检：背景/运动/循环性
uv run python scripts/asset_pipeline.py matte --src <视频> --out <片段目录> \
    --canvas WxH --frames START:END --fps N --align-tail
uv run python scripts/asset_pipeline.py costume --spec <build.json>   # 整套服装重建（共用变换）
uv run python scripts/asset_pipeline.py portrait --src <立绘> --out <服装目录> --name <名>
```

⚠️ 走这条路务必先读 §3.1 的 6 条教训（尤其"每段各自缩放对齐同一目标高度"）。

---

## 6. 环境与目录

| 位置 | 内容 | 是否入 git |
|---|---|---|
| `assets/pet/costumes/` | **6 套服装素材**（767 MB） | ❌ gitignore |
| `assets/pet/_retired/` | 撤下的 9 套 AI/立绘服装（297 MB，**可恢复**） | ❌ gitignore |
| `assets/pet/character.png` | 旧单张立绘（无素材时的兜底） | ✅ |
| `D:\ElysiaAssets\spine_elysia\` | Spine 原始素材（.skel/.atlas/.png） | 仓库外 |
| `D:\ElysiaAssets\spine_bake\` | Node 烘焙工程（`bake.js`/`precheck.js` + node_modules 76.8 MB） | 仓库外 |
| `D:\ElysiaAssets\_obsolete\` | 本次清理出的过期素材（**可删**） | 仓库外 |

**Python 依赖**：项目 `.venv`。素材相关只用到 `av`（读视频）、`pillow`（图像）；
`imageio-ffmpeg` 可选（仅 `asset_pipeline.py --webm` 的 VP9-alpha 编码用）。

**Node**：v24.12 + npm 11.6。`@esotericsoftware/spine-core|spine-canvas@4.1.56` + `canvas`（Cairo 后端，
**不能用 `@napi-rs/canvas`**，见 §3.4 #17）。

---

## 7. 已弃用 / 已撤下的东西

| 东西 | 状态 | 怎么恢复 |
|---|---|---|
| **豆包提示词清单** `assets/pet/PROMPTS.md` | **已删除**（改用 Spine 后不再需要） | 在 git 历史里（`git show df75ee2:assets/pet/PROMPTS.md`） |
| 9 套 AI/立绘服装（c1~c10） | **已撤下**到 `assets/pet/_retired/` | 移回 `assets/pet/costumes/` 即可 |
| `c1/build.json`（AI 素材的可复现配方） | 已从 git 删除 | `git show 619ce95^:assets/pet/costumes/c1/build.json` |
| 豆包首帧图 | 移到 `D:\ElysiaAssets\_obsolete\elysia_firstframes\` | 移回即可（或用 `portrait` 命令重生成） |
| MiniMax 测试产物 | 移到 `D:\ElysiaAssets\_obsolete\elysia_mm_test\` | —— |
| `spine-python` / `spinepy` / `pybind11` | **已从 .venv 卸载**（坏包，§3.4 #16） | 别装回来 |
| **Spine 转向动画** `0_left`/`0_right` | 空动画，未采用 | 需作者补做 |
| **`3_applaud`** | 6 套全部有"眉毛穿眼"，未采用 | 需作者修 |
| dsh-pet **pet pack**（复用它的引擎）构想 | 未实施（`--webm` 编码链路已验证可产出真 VP9-alpha） | 见 git 历史里的 `imageio-ffmpeg` + `-pix_fmt yuva420p -auto-alt-ref 0` |

---

## 8. 已知限制与未完成项

1. **动作种类**：每套 8~9 个片段，7 类行为已全部有素材（含 `float`/`land`/`settle-bridge`/`notice`）✓
   但 **`applaud`（鼓掌）6 套全废**（§3.4 #23），所以"点击回应"实际只有"挥手"和"比心/眨眼"两种
2. **灵魂状态未接**：`STATE_CLIPS` 的映射（away/unhappy/tired/happy）已写好，
   但要用 `--with-soul-data` 启动且灵魂在跑才生效
3. **未推送**：本地领先 `origin/main` **44 个提交**（远程还是 `91bf19d`）；推送与否待用户决定
4. **她的真实数据在他机**：本地 `data/*.db` 是测试数据（576 心跳 / 22 记忆）；
   **绝不能把本地库拷到他机**（会用测试数据覆盖她的真实生命）。方向只能是他机 → 这里
5. `WANDER_SPEED_PX_S = 110` 是估的，若看着"滑步"需要和走动的步频对齐调整
6. 共享画布让**窗口变大**（s1: 之前 528×634 → 现在 714×674）：多出来的是动作摆幅空间，
   不裁切的代价。若嫌窗口大可以调小 `spine_canvas.js` 的采样范围（会重新引入裁切风险）

---

## 9. 验收清单

```bash
cd <repo>
$env:QT_QPA_PLATFORM="offscreen"     # 只在离屏测试时需要
uv run pytest                        # 期望 461 passed
uv run ruff check && uv run ruff format --check && uv run mypy src

# 桌宠
python -m elysia.body.pet            # 应出现窗口；右键菜单有「换装」6 套
```

**人工验收**（在桌面上）：
1. 启动后她**自己待机**，每 18–45 秒在待机池里换姿势
2. **点她** → 挥手 / 比心（随机）
3. **拖动** → 有漂浮手感（上下浮沉 + 轻微摆动）；松手回待机
4. **右键 → 换装** → 6 套之间切换，**角色大小与站位不应跳动**，窗口自适应
5. **看不到**网格线、看不到"眉毛穿眼"
6. 每 70–200 秒她会**走一小段**
