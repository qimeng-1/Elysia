# 桌宠素材约定（assets/pet/）

> 本目录只放**元数据与说明**；真正的角色素材**不入库**（见根目录 `.gitignore` 的说明：
> 版权 + 体积两条理由）。

## 目录约定

```
assets/pet/
├── character.png              # 旧的单张立绘（历史遗留，仍是缺素材时的回退）
├── README.md                  # 本文件
└── costumes/                  # ← 不入库，由 scripts/asset_pipeline.py 生成
    └── <costume-id>/          # 一套服装 = 一个包（对应换装功能的一项）
        └── <clip-id>/         # 一段动作
            ├── manifest.json  # 来源 / 裁切框 / 容差 / 循环性 / 排除区（可复核）
            ├── frames/*.png   # 无损母版序列（将来重新加工用）
            └── webp/*.webp    # **运行时**序列（Qt 原生可读，体积约 PNG 的 1/4.5）
```

**为什么是序列帧而不是视频**：实测（2026-10-14）两条路都不通——
Qt 自己播 alpha WebM 会**丢透明通道**（`QVideoFrame.toImage()` → `Format_RGB32`）；
PyAV 编出来的 `yuva420p` WebM 报 4 平面但 alpha **逐帧完全相同、比例只有 14%**，
是个假 alpha。而 **WebP 序列**：Qt 原生读得到、alpha 正确、体积只有 PNG 的 1/4.5。

## 怎么生成

```powershell
cd <项目根>

# ① 先体检素材（规格 / 背景色 / 运动量 / 循环性）
uv run python scripts\asset_pipeline.py inspect "D:\图片\Saved Pictures\呼吸.mp4"

# ② 抠成透明序列帧
uv run python scripts\asset_pipeline.py matte `
    --src "D:\图片\Saved Pictures\呼吸.mp4" `
    --out assets\pet\costumes\c1\idle

# ③ 若素材里有泛洪填充拦不住的深色水印/游戏 logo，用 --exclude 显式排除
uv run python scripts\asset_pipeline.py matte --src ... --out ... --exclude 0,0,201,135
```

## 对素材的要求（给生产端的规格）

| 项 | 要求 |
|---|---|
| 视频 | 单动作、2–6 秒、**镜头固定**、**纯色背景**（纯白 `#FFFFFF` 或纯绿最佳）、≥720p、24/30fps |
| 循环段 | 首尾姿态一致（`inspect` 会给出剪影差与整图差，两者都合格才算无缝） |
| 图片 | **PNG 带透明**最佳；退而求其次用**纯色背景**（不要 JPG、不要带文字的图） |
| 禁忌 | 文字水印、录屏 UI、镜头运动、多角色、大幅度位移 |

> AI 生成（豆包/即梦/可灵）产出的视频**总带平台水印**，而且**如果喂进去的首帧本身带 logo，
> AI 会把 logo 也一起动起来**（实测「崩坏3」logo 就是这样混进 `雀跃.mp4` 的）。
> 所以：给 AI 的首帧要**先抠干净、去掉一切文字与 logo**。

## 命名建议

- `<costume-id>`：`c1` / `c2`…，一套服装一个包（换装功能的单位）
- `<clip-id>`：按**用途**命名，便于状态机映射
  - `idle` 待机呼吸（循环） · `sleep` 睡眠（循环）
  - `cheer` 点击回应·开心 · `shy` 害羞 · `wave` 打招呼
  - `tired` 疲惫（循环） · `miss` 思念远望（循环）
  - `drag` 被拖拽悬空 · `land` 落地压缩
