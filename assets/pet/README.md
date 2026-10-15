# 桌宠素材约定（assets/pet/）

> 本目录只放**元数据、说明与兜底立绘**；真正的角色素材（`costumes/`）**不入库**——
> 版权 + 体积两条理由（见根目录 `.gitignore`）。

## 先读这个

**[../../docs/HANDOVER.md](../../docs/HANDOVER.md)** —— 桌宠的完整交接文档：
当前状态、怎么跑、6 套服装与触发逻辑、**20+ 条踩坑记录**、素材授权、怎么加新素材、已弃用清单。

## 目录约定

```
assets/pet/
├── character.png          # 旧单张立绘（无服装包时的兜底回退）
├── README.md              # 本文件
├── _retired/              # 撤下的素材（保留在本地但不再启用，不入库）
└── costumes/              # ← 不入库
    └── <服装 id>/          # 一套服装 = 一个包（换装功能的一项）
        ├── costume.json   # {"id": ..., "name": "菜单里显示的名字"}
        └── <片段 id>/
            ├── manifest.json  # 帧数 / fps / 尺寸 / 循环性 / 来源（可复核）
            ├── frames/*.png   # 无损母版序列（重新加工用）
            └── webp/*.webp    # **运行时**序列（Qt 原生可读，约 PNG 的 1/4.5）
```

**片段 id 决定行为**（详见交接文档 §2.2）：

| id | 行为 |
|---|---|
| `idle*`（`idle` / `idle-open` / `idle-hunmi` / `idle-cry` / `look-around` / `think` / `settle`） | 待机池，加权随机轮换（循环） |
| `notice` / `settle` / `land` / `wave` / `cheer` / `shy` / `surprise` / `pout` | **播一次**就回待机 |
| `float`（其次 `grab`） | 按住拖拽期间 |
| `run`（其次 `walk`） | 漫游走动 |

> ⚠️ **新增片段后务必确认它有调用点**（去 `pet.py` 查 `IDLE_WEIGHTS` / `CLICK_POOL` / `DRAG_CLIP_CANDIDATES`
> / `WANDER_CLIP_CANDIDATES` / `STATE_CLIPS`）。本项目犯过两次"入库了却没有调用点"的错（`grab`、`notice`）。

## 为什么是 **WebP 序列帧**而不是视频

实测（2026-10-14）两条视频路线都不通：

- **Qt 自己播 alpha WebM 会丢透明通道**：`QVideoFrame.toImage()` → `Format_RGB32`，alpha 直接没了
- **PyAV 编的 `yuva420p` WebM 是假 alpha**：报 4 平面，但 alpha 帧逐帧完全相同、透明比例只有 14%

而 **WebP 序列**：Qt 原生读得到、alpha 正确、体积约为 PNG 的 1/4.5 ✓

> 补充（2026-10-15）：**真** VP9-alpha WebM 是能产出的——`ffmpeg -c:v libvpx-vp9 -pix_fmt yuva420p
> -auto-alt-ref 0`（用 `imageio-ffmpeg` 自带的 ffmpeg 即可，实测 97 帧 = 901 KB、跨帧 alpha 真实）。
> 这条链路是给"dsh-pet pet pack"准备的，当前未采用（见交接文档 §7）。

## 素材从哪来

**当前主线**：Spine 骨骼烘焙 —— `scripts/spine_bake.js`（含三角形缝隙修复）+ `scripts/ingest_spine.py`。
**备选链路**：AI 视频抠图 —— `scripts/asset_pipeline.py`（`inspect` / `matte` / `costume` / `portrait`），
两条都用 `--canvas` / 每段各自缩放对齐同一角色高度，避免"换动作时大小跳"。
用法与要求见交接文档 §5。
