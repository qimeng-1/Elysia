r"""桌宠素材流水线：把"带背景的视频/立绘"变成"可用的透明动画片段"。

**为什么需要它**（2026-10-14 与用户一起摸出来的结论）：

1. `dsh-pet` 那种程度的桌宠靠的是**预渲染 alpha 视频**，而外部 AI（豆包/即梦/可灵）
   产出的是**白底或浅灰底的普通视频**，且**总带水印**（豆包的右下角是"豆包AI生成"，最暗像素
   与背景只差 33）。所以"抠图 + 去水印"是必经的一步。
2. Qt 自己**播不了**带透明通道的 VP9 WebM（实测 `QVideoFrame.toImage()` → `Format_RGB32`，
   alpha 丢失）；PyAV 的默认解码器也不行（要显式用 `libvpx-vp9` 参考解码器才拿得到 `yuva420p`）。
   因此素材的**中间产物用带 alpha 的 PNG 序列**最稳（Qt 原生支持、零解码风险），
   需要省空间时再编码成 alpha WebM。

**抠图算法**（针对"固定镜头 + 近似纯色背景"的 AI 视频，实测有效）：

1. 背景色 = 四边像素的中位数（不是取一个角，避免角落恰好有水印/阴影）
2. 从**边界泛洪填充**：与背景色差在 `tol` 之内的一律判为背景
   —— 用泛洪而不是"全局色键"，是为了**保住角色自身的白衣服/白帽子**（它们是封闭区域，泛洪到不了）
3. `tol` 取 **35** 的依据：豆包水印与背景的最大通道差是 33 ⇒ 34 以上即可吸收水印，
   同时**停在角色的黑描边外**（实测角色只损失 0.2% 像素）。这个数字是量出来的，不是猜的
4. 边缘用一次高斯模糊得到**软 alpha**（消除锯齿硬边），再按 `1-alpha` 反预乘去掉背景溢色
5. 所有帧**统一裁到同一包围盒**（取全序列并集 + 内边距），避免逐帧裁切导致动画抖动

用法::

    # 先看素材底细（规格 / 背景色 / 运动量 / 是否真在动 / 循环性）
    python scripts/asset_pipeline.py inspect "D:\\图片\\Saved Pictures\\呼吸.mp4"

    # 抠成 PNG 序列 + manifest（这是桌宠直接可用的形态）
    python scripts/asset_pipeline.py matte --src "...\\呼吸.mp4" --out assets/pet/costumes/c1/idle

    # 额外编码一份 alpha WebM（更省空间，需要 PyAV 的 libvpx-vp9）
    python scripts/asset_pipeline.py matte --src ... --out ... --webm

退出码: 0 成功 / 1 处理失败 / 2 参数错误
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from collections import deque
from collections.abc import Sequence
from pathlib import Path

import av
from PIL import Image, ImageChops, ImageFilter, ImageStat

# 与背景色差在此之内判为背景。
# 35 = 豆包水印与背景的最大通道差（33）+ 安全余量，实测能吸收水印且只损失 0.2% 角色像素。
DEFAULT_TOLERANCE = 35
# 读不到真实帧率时的兜底值
DEFAULT_FPS_FALLBACK = 24.0
# 软边高斯半径（像素）：太大糊边，太小留锯齿
EDGE_FEATHER = 0.9
# 统一包围盒的外扩像素（给物理动画的位移留余量）
BBOX_PADDING = 10
# 循环判据：剪影（alpha）差 < 此值算"形体能循环"；整图差 < 此值算"看不出跳"
LOOP_ALPHA_OK = 3.0
LOOP_RGB_OK = 8.0
# 运行时 WebP 质量（带 alpha 的有损 WebP，实测体积约为 PNG 的 1/4.5，Qt 原生可读）
WEBP_QUALITY = 92
# 去噪阈值：小于此像素数的前景连通块判为背景噪点
MIN_BLOB_PX = 200
# 孤立块与"主体包围盒"的最大容许间距（保留参数位，见 clean_mask 的说明）
NEAR_MARGIN_PX = 20
# "贴边带"占画面比例：包围盒落在最外 30% 内即视为"贴着边"
EDGE_BAND = 0.30
# 贴边块若小于整帧面积的此比例，判为水印/角标
WATERMARK_AREA_RATIO = 0.05


def _force_utf8_console() -> None:
    """控制台编码兜底：Windows 默认代码页不是 UTF-8 时中文会变乱码。"""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def _mean_diff(a: Image.Image, b: Image.Image) -> float:
    """两图的平均像素差（0-255）。用 ImageStat 避开 Pillow 的 getdata 弃用路径。"""
    return float(ImageStat.Stat(ImageChops.difference(a.convert("L"), b.convert("L"))).mean[0])


def background_color(im: Image.Image) -> tuple[int, int, int]:
    """背景色 = 四边像素的中位数（比取单角稳，避免角落恰有水印或阴影）。"""
    w, h = im.size
    px = im.load()
    border = (
        [px[x, 0] for x in range(0, w, 4)]
        + [px[x, h - 1] for x in range(0, w, 4)]
        + [px[0, y] for y in range(0, h, 4)]
        + [px[w - 1, y] for y in range(0, h, 4)]
    )
    border.sort(key=lambda t: sum(t))
    return border[len(border) // 2]


def background_mask(
    im: Image.Image,
    bg: tuple[int, int, int],
    tol: int,
    exclude: Sequence[tuple[int, int, int, int]] = (),
) -> Image.Image:
    """从四边泛洪填充得到背景掩膜（255 = 背景）。

    泛洪而非全局色键：角色自身的白衣/白帽是**封闭区域**，泛洪进不去 ⇒ 不会被误删。
    水印是"比背景更白"的浅色像素，只要 `tol` 大于它和背景的差，泛洪就能**跨过它**吸收掉；
    但**深色/彩色角标**（如游戏 logo）泛洪拦不住，那类要靠 `exclude` 显式排除。
    """
    w, h = im.size
    px = im.load()
    br, bgc, bb = bg
    limit = tol * 3
    seen = bytearray(w * h)
    queue: deque[tuple[int, int]] = deque()
    for x in range(w):
        queue.append((x, 0))
        queue.append((x, h - 1))
    for y in range(h):
        queue.append((0, y))
        queue.append((w - 1, y))
    while queue:
        x, y = queue.popleft()
        i = y * w + x
        if seen[i]:
            continue
        r, g, b = px[x, y]
        if abs(r - br) + abs(g - bgc) + abs(b - bb) > limit:
            continue
        seen[i] = 1
        if x > 0:
            queue.append((x - 1, y))
        if x < w - 1:
            queue.append((x + 1, y))
        if y > 0:
            queue.append((x, y - 1))
        if y < h - 1:
            queue.append((x, y + 1))
    return Image.frombytes("L", (w, h), bytes(255 if s else 0 for s in seen))


def apply_exclusions(
    mask: Image.Image, exclude: Sequence[tuple[int, int, int, int]]
) -> Image.Image:
    """把显式排除区（x0,y0,x1,y1）刷成背景。

    用于泛洪填充拦不住的**深色/彩色水印与游戏 logo**：它们的颜色与背景差得远，
    会被当成前景保留。显式排除比启发式识别可靠——**参数会记进 manifest**，可复核。
    """
    if not exclude:
        return mask
    out = mask.copy()
    px = out.load()
    w, h = out.size
    for x0, y0, x1, y1 in exclude:
        for y in range(max(0, y0), min(h, y1)):
            for x in range(max(0, x0), min(w, x1)):
                px[x, y] = 255
    return out


def clean_mask(mask: Image.Image, min_blob: int = MIN_BLOB_PX) -> Image.Image:
    """清掉两类"不是她"的前景，返回 255 = 背景的掩膜。

    1. **过小的连通块**：AI 视频背景不是纯色，散布着压缩噪点。它们肉眼看不见，
       却会把统一裁切框撑大（实测让某个离群帧的包围盒从 519px 宽变成 674px）。
    2. **贴边且不大的孤立块**：实测 `雀跃.mp4` 左上角带着「崩坏3」游戏 logo
       ——用户喂给豆包的首帧里有它，AI 把 logo 也一起动起来了。它比噪点大得多、
       颜色也深，泛洪填充拦不住。

    判据用**"贴边 + 面积小"**而不是"离主体远"：实测那个 logo 的包围盒
    `(9,6,201,135)` 与角色的 `(181,122,517,649)` **几乎相接**，距离判据会误放行。
    同时这个策略**保住贴在角色附近的分离特效**（星星、爱心）。
    """
    w, h = mask.size
    src = mask.load()
    labels = bytearray(w * h)
    keep = mask.copy()  # 从原掩膜出发，只清掉该清的（大块保持前景）
    kp = keep.load()

    # ① 先收集所有前景连通块
    blobs: list[tuple[list[int], tuple[int, int, int, int]]] = []
    for y0 in range(h):
        for x0 in range(w):
            i0 = y0 * w + x0
            if src[x0, y0] != 0 or labels[i0]:
                continue
            pixels: list[int] = []
            bx0 = bx1 = x0
            by0 = by1 = y0
            queue: deque[tuple[int, int]] = deque([(x0, y0)])
            labels[i0] = 1
            while queue:
                x, y = queue.popleft()
                pixels.append(y * w + x)
                bx0, by0 = min(bx0, x), min(by0, y)
                bx1, by1 = max(bx1, x), max(by1, y)
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        j = ny * w + nx
                        if not labels[j] and src[nx, ny] == 0:
                            labels[j] = 1
                            queue.append((nx, ny))
            blobs.append((pixels, (bx0, by0, bx1 + 1, by1 + 1)))

    if not blobs:
        return keep

    # ② 主体 = 最大块；其余块若"太小"或"贴边且不大"，判回背景
    #
    # 判"水印/角标"用 **贴边 + 面积小**，而不是"离主体远"——实测 `雀跃.mp4` 的
    # 「崩坏3」logo（bbox 9,6,201,135）与角色（181,122,517,649）的包围盒**几乎相接**，
    # 距离判据会把它当成"主体附近"而保住。
    total = w * h
    main_idx = max(range(len(blobs)), key=lambda i: len(blobs[i][0]))
    for i, (pixels, (bx0, by0, bx1, by1)) in enumerate(blobs):
        if i == main_idx:
            continue  # 主体本身永远保留
        too_small = len(pixels) < min_blob
        hugs_edge = (
            bx1 <= w * EDGE_BAND
            or by1 <= h * EDGE_BAND
            or bx0 >= w * (1 - EDGE_BAND)
            or by0 >= h * (1 - EDGE_BAND)
        )
        small_enough_to_be_a_mark = len(pixels) <= WATERMARK_AREA_RATIO * total
        if too_small or (hugs_edge and small_enough_to_be_a_mark):
            for j in pixels:
                kp[j % w, j // w] = 255
    return keep


def cutout_adaptive(im: Image.Image, tol: int = DEFAULT_TOLERANCE) -> Image.Image:
    """抠图，并自动处理**"卡片式"源图**（外面一层底色 + 里面另一种底色）。

    实测样例：某几张官方立绘是"白色页面 + 粉色圆角卡片"两层底——四角近白
    `(251,247,244)`，卡片是粉 `(252,199,219)`。单色泛洪只能吃掉外层白页，
    于是**整张粉色卡片被当成前景**，抠出来带一圈"光晕"。

    做法：先按外层色泛洪；若剩下的"前景"占了画面绝大部分（说明外层只是薄边、
    里面还有另一层底），就裁到内层包围盒，用**内层角落的颜色**重跑一次。
    """
    w, h = im.size
    bg = background_color(im)
    page = background_mask(im, bg, tol)
    inner = page.point(lambda v: 255 - v).getbbox()
    if inner is not None:
        area_ratio = ((inner[2] - inner[0]) * (inner[3] - inner[1])) / float(w * h)
        if area_ratio > 0.75:  # 整张图几乎都算"前景" ⇒ 里面还有第二层底
            crop = im.crop(inner)
            bg2 = background_color(crop)
            if sum(abs(a - b) for a, b in zip(bg2, bg, strict=False)) > 40:  # 内层底确实不同
                im, bg = crop, bg2
    return cutout(im, bg, tol)


def portrait_clip(src: Path, out_dir: Path, name: str, tol: int) -> bool:
    """把一张立绘做成**单帧片段**的服装包（`<out>/idle/{frames,webp}/0000.*`）。

    为什么做成"单帧片段"而不是新增一种"静态形象"类型：这样**零新代码**——
    `ClipPlayer` 照常加载它，物理动画（呼吸/摇摆/浮动）继续在上面叠加，
    换装、窗口自适应尺寸、菜单全部复用同一条路径。
    """
    with Image.open(src) as raw:
        im = raw.convert("RGB")
    cut = cutout_adaptive(im, tol)
    bbox = cut.getchannel("A").getbbox()
    if bbox is None:
        return False
    tile = cut.crop(bbox)

    frames_dir = out_dir / "idle" / "frames"
    webp_dir = out_dir / "idle" / "webp"
    frames_dir.mkdir(parents=True, exist_ok=True)
    webp_dir.mkdir(parents=True, exist_ok=True)
    tile.save(frames_dir / "0000.png", optimize=True)
    tile.save(webp_dir / "0000.webp", "WEBP", quality=WEBP_QUALITY, method=4)
    (out_dir / "idle" / "manifest.json").write_text(
        json.dumps(
            {
                "source": str(src),
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": "static-portrait",
                "frame_count": 1,
                "fps": 24,
                "size": [tile.width, tile.height],
                "background": list(background_color(im)),
                "tolerance": tol,
                "note": "静态立绘的单帧片段；物理动画（呼吸/摇摆/浮动）仍在其上叠加",
                "master_frames": "frames/*.png（无损母版）",
                "runtime_frames": f"webp/*.webp（q{WEBP_QUALITY}）",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "costume.json").write_text(
        json.dumps(
            {"id": out_dir.name, "name": name, "source": src.name},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  {out_dir.name:<6} {name:<10} 抠出 {tile.width}x{tile.height}  ← {src.name[:26]}")
    return True


def cutout(
    im: Image.Image,
    bg: tuple[int, int, int],
    tol: int,
    erode_px: int = 1,
    exclude: Sequence[tuple[int, int, int, int]] = (),
) -> Image.Image:
    """返回带软 alpha 的 RGBA 抠图。

    软边两步：

    1. **清掩膜**：丢掉过小的噪点块 + 离主体太远的孤立块（水印/角标，见 `clean_mask`）
    2. **吃边**：`MaxFilter` 把"背景"区域向角色内扩张 `erode_px` 像素
       （掩膜里 255 = 背景，取邻域最大值 = 背景吃进角色边缘）。这一步消掉浅色底在角色外圈
       留下的**背景色溢色光圈**——不做这一步，深色背景上会看到一圈白边。
    3. **模糊**：一次轻微高斯，给出抗锯齿的过渡带。

    最后按 `1-alpha` **反预乘**，把边缘像素里混进的背景色扣掉。
    """
    mask = clean_mask(apply_exclusions(background_mask(im, bg, tol), exclude))
    if erode_px > 0:
        mask = mask.filter(ImageFilter.MaxFilter(max(3, erode_px * 2 + 1)))
    alpha = mask.filter(ImageFilter.GaussianBlur(EDGE_FEATHER)).point(lambda a: 255 - a)

    rgba = im.convert("RGBA")
    rgba.putalpha(alpha)

    # 反预乘去溢色：边缘像素 = 角色*a + 背景*(1-a) ⇒ 角色 = (像素 - 背景*(1-a)) / a
    a = alpha.load()
    px = rgba.load()
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            av_ = a[x, y]
            if av_ == 0 or av_ == 255:
                continue
            f = av_ / 255.0
            r, g, b, _ = px[x, y]
            px[x, y] = (
                max(0, min(255, int((r - bg[0] * (1 - f)) / f))),
                max(0, min(255, int((g - bg[1] * (1 - f)) / f))),
                max(0, min(255, int((b - bg[2] * (1 - f)) / f))),
                av_,
            )
    return rgba


def fit_to_canvas(tile: Image.Image, canvas: tuple[int, int]) -> Image.Image:
    """把抠好的图**等比缩放进固定画布**（底边居中）。

    **为什么必须做**：实测同一套服装，各片段里角色在源画面中的大小并不一致——
    `被拎起悬空` 被 AI 画大了约 25%（源内高度 695px vs 待机的 554px）。
    若逐片段各自裁切，换动作时她会**突然变大变小，甚至超出窗口被裁掉**。
    统一缩放进同一画布后，各片段的显示尺寸与锚点就一致了。
    """
    cw, ch = canvas
    scale = min(cw / tile.width, ch / tile.height)
    w, h = max(1, round(tile.width * scale)), max(1, round(tile.height * scale))
    resized = tile.resize((w, h), Image.LANCZOS)
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    out.paste(resized, ((cw - w) // 2, ch - h), resized)
    return out


def video_fps(path: Path) -> float:
    """读视频的真实帧率。

    **不要硬编码 24**：实测用户生成的素材里混有 30fps 的（`被拎起悬空.mp4`），
    写死会让播放速度错 25%。
    """
    container = av.open(str(path))
    stream = container.streams.video[0]
    fps = float(stream.average_rate) if stream.average_rate else DEFAULT_FPS_FALLBACK
    container.close()
    return fps or DEFAULT_FPS_FALLBACK


def read_video_frames(path: Path) -> list[Image.Image]:
    """解码整段视频为 RGB 帧（PyAV 默认解码器足够——这里只要颜色，alpha 是自己算的）。"""
    container = av.open(str(path))
    stream = container.streams.video[0]
    frames = [f.to_image().convert("RGB") for f in container.decode(stream)]
    container.close()
    return frames


def inspect(path: Path) -> int:
    """体检一份素材：规格 / 背景 / 运动量 / 循环性 / 水印可疑区。"""
    if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        with Image.open(path) as im:
            bg = background_color(im.convert("RGB"))
            print(f"  图片 {path.name}")
            print(f"    尺寸 {im.size[0]}x{im.size[1]} | 模式 {im.mode}")
            print(f"    背景色 ≈ {bg} | 有 alpha = {im.mode in ('RGBA', 'LA')}")
        return 0

    frames = read_video_frames(path)
    if not frames:
        print(f"  {path.name}: 解不出帧", file=sys.stderr)
        return 1
    w, h = frames[0].size
    bg = background_color(frames[0])
    diffs = [_mean_diff(frames[i], frames[i - 1]) for i in range(1, len(frames))]
    loop_rgb = _mean_diff(frames[0], frames[-1])
    # 剪影循环性：形体对得上，循环才不跳（颜色细节差一点通常看不出来）
    cut = [cutout(f, bg, DEFAULT_TOLERANCE) for f in (frames[0], frames[-1])]
    loop_alpha = _mean_diff(cut[0].getchannel("A"), cut[1].getchannel("A"))
    moving = sum(1 for d in diffs if d > 1.0)
    has_motion = moving > 5 and max(diffs) > 3
    loops = loop_alpha < LOOP_ALPHA_OK and loop_rgb < LOOP_RGB_OK

    print(f"  视频 {path.name}")
    print(f"    尺寸 {w}x{h} | 帧数 {len(frames)} | 时长 {len(frames) / 24:.2f}s（按 24fps 估）")
    print(f"    背景色 ≈ {bg}")
    print(f"    逐帧运动量: 均值 {sum(diffs) / len(diffs):.2f} / 峰值 {max(diffs):.2f}")
    print(f"    有效运动帧: {moving} / {len(diffs)}")
    print(
        f"    循环性: 剪影差 {loop_alpha:.2f}（<{LOOP_ALPHA_OK:g} 合格）"
        f" / 整图差 {loop_rgb:.2f}（<{LOOP_RGB_OK:g} 合格）"
    )
    print(
        f"    判定: {'✅ 有真实动作' if has_motion else '⚠️ 几乎静止'} + "
        f"{'✅ 可无缝循环' if loops else '⚠️ 循环会跳，需处理'}"
    )
    return 0


def matte_clip(
    src: Path,
    out_dir: Path,
    tol: int,
    make_webm: bool,
    exclude: Sequence[tuple[int, int, int, int]] = (),
    canvas: tuple[int, int] | None = None,
    frames_range: tuple[int, int] | None = None,
    fps_override: float | None = None,
    do_align_tail: bool = False,
) -> int:
    """抠图整段视频 → 统一裁切的 PNG 母版 + WebP 运行时序列 + manifest。"""
    frames = read_video_frames(src)
    if not frames:
        print(f"  {src.name}: 解不出帧", file=sys.stderr)
        return 1
    if frames_range is not None:
        start, end = frames_range
        frames = frames[start:end]
        if not frames:
            print(f"  {src.name}: --frames {start}:{end} 裁完是空的", file=sys.stderr)
            return 1
        print(f"  裁剪帧区间 {start}:{end} → 剩 {len(frames)} 帧")
    bg = background_color(frames[0])
    fps = fps_override if fps_override is not None else video_fps(src)
    print(f"  背景色 = {bg} | 容差 = {tol} | 帧数 = {len(frames)} | 播放 {fps:g}fps")

    cut = [cutout(f, bg, tol, exclude=exclude) for f in frames]
    if do_align_tail:
        keep = align_tail(frames, cut)
        if keep < len(frames):
            tail_a = _mean_diff(cut[0].getchannel("A"), cut[-1].getchannel("A"))
            tail_b = _mean_diff(cut[0].getchannel("A"), cut[keep - 1].getchannel("A"))
            print(
                f"  收尾对齐：{len(frames)} → {keep} 帧"
                f"（首尾剪影差 {tail_a:.2f} → {tail_b:.2f}，越小越容易无缝衔接）"
            )
            frames, cut = frames[:keep], cut[:keep]
    union = None
    for im in cut:
        # ⚠️ 必须只看 **alpha 通道**：RGBA 的 getbbox() 会连 RGB 一起算，
        # 而透明背景像素的 RGB 仍是原背景色 ⇒ 包围盒被撑到整帧
        b = im.getchannel("A").getbbox()
        if b is None:
            continue
        union = (
            b
            if union is None
            else (
                min(union[0], b[0]),
                min(union[1], b[1]),
                max(union[2], b[2]),
                max(union[3], b[3]),
            )
        )
    if union is None:
        print("  抠图后为空——容差可能过大，把角色也当背景了", file=sys.stderr)
        return 1
    w, h = cut[0].size
    box = (
        max(0, union[0] - BBOX_PADDING),
        max(0, union[1] - BBOX_PADDING),
        min(w, union[2] + BBOX_PADDING),
        min(h, union[3] + BBOX_PADDING),
    )
    print(f"  统一裁切 box = {box}（尺寸 {box[2] - box[0]}x{box[3] - box[1]}）")

    # PNG = 无损母版（将来还要再加工）；WebP = 运行时格式（Qt 原生读、体积约 1/4.5）
    frames_dir = out_dir / "frames"
    webp_dir = out_dir / "webp"
    frames_dir.mkdir(parents=True, exist_ok=True)
    webp_dir.mkdir(parents=True, exist_ok=True)
    # **先清空旧帧**：重跑一个更短的片段时，残留的旧帧会与 manifest 不符。
    # 实测：把 notice 从 97 帧裁到 42 帧后，frames/ 里还留着 0042.png~0096.png，
    # 于是"帧数"读出来仍是 97，播放器会把动作演到一段早就该删掉的尾巴上
    # （而且首尾差、时长、内存全都跟着错）。
    for stale_dir in (frames_dir, webp_dir):
        for stale in stale_dir.iterdir():
            if stale.is_file():
                stale.unlink()
    for i, im in enumerate(cut):
        tile = im.crop(box)
        if canvas is not None:
            tile = fit_to_canvas(tile, canvas)
        tile.save(frames_dir / f"{i:04d}.png", optimize=True)
        tile.save(webp_dir / f"{i:04d}.webp", "WEBP", quality=WEBP_QUALITY, method=4)
    out_size = [tile.width, tile.height]

    loop_alpha = _mean_diff(cut[0].getchannel("A"), cut[-1].getchannel("A"))
    loop_rgb = _mean_diff(frames[0], frames[-1])
    loops = loop_alpha < LOOP_ALPHA_OK and loop_rgb < LOOP_RGB_OK

    manifest = {
        "source": str(src),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "frame_count": len(cut),
        "fps": fps,
        "size": out_size,
        "crop_box": list(box),
        "source_size": [w, h],
        "background": list(bg),
        "tolerance": tol,
        "loop_alpha_diff": round(loop_alpha, 3),
        "loop_rgb_diff": round(loop_rgb, 3),
        "loops_seamlessly": loops,
        # 循环不干净时给播放器的建议：**正放 + 倒放**天然无缝（乒乓）。
        # 只对"循环类"片段有意义；单次片段（notice/settle/wave）不需要。
        "suggest_ping_pong": not loops,
        "canvas": list(canvas) if canvas is not None else None,
        "master_frames": "frames/*.png（无损母版）",
        "runtime_frames": f"webp/*.webp（q{WEBP_QUALITY}，Qt 原生可读）",
        "min_blob_px": MIN_BLOB_PX,
        "excluded_regions": [list(r) for r in exclude],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    size_png = sum(f.stat().st_size for f in frames_dir.glob("*.png")) / 1024 / 1024
    size_webp = sum(f.stat().st_size for f in webp_dir.glob("*.webp")) / 1024 / 1024
    print(f"  母版 PNG  {len(cut)} 帧 → {frames_dir}（{size_png:.1f} MB）")
    print(f"  运行时 WebP {len(cut)} 帧 → {webp_dir}（{size_webp:.1f} MB）")
    print(
        f"  循环性: 剪影差 {loop_alpha:.2f} / 整图差 {loop_rgb:.2f}"
        f" ⇒ {'无缝循环 ✓' if loops else '会跳，需处理'}"
    )

    if make_webm:
        webm = out_dir / "clip.webm"
        try:
            _encode_alpha_webm(cut, box, webm)
            if _webm_alpha_is_real(webm):
                print(f"  已编码 alpha WebM → {webm}（{webm.stat().st_size / 1024:.0f} KB）")
            else:
                # 实测踩过：报 yuva420p / 4 平面，但解出来 alpha 恒为 255（全不透明）。
                # 与其交出一个"看着有 alpha 其实没有"的文件，不如删掉并说清楚。
                webm.unlink(missing_ok=True)
                print(
                    "  ⚠️ alpha WebM 未通过自验（透明通道不可信）——已删除。"
                    "运行时请用 webp/ 序列：Qt 原生可读、已验证 alpha 正确",
                    file=sys.stderr,
                )
        except Exception as exc:  # 编码失败不影响 PNG/WebP 序列这份主产物
            print(
                f"  ⚠️ alpha WebM 编码失败（序列帧不受影响）：{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    return 0


def _webm_alpha_is_real(path: Path) -> bool:
    """自验：解回 WebM，alpha 平面是否**真的在动**且**比例合理**。

    ⚠️ 实测教训（2026-10-14）：PyAV 编出的 `yuva420p` WebM **报 4 个平面**，
    但 alpha 平面**逐帧完全相同**、透明像素只占 ~14%（角色之外本该 ~85% 透明）
    ⇒ 那是个**假 alpha**。所以"有没有 <128 的像素"这种检查会误判通过；
    这里改查两件事：跨帧是否有变化 **且** 透明比例是否落在合理区间。
    """
    container = av.open(str(path))
    stream = container.streams.video[0]
    ctx = av.CodecContext.create("libvpx-vp9", "r")
    if stream.codec_context.extradata:
        ctx.extradata = stream.codec_context.extradata
    sigs: set[bytes] = set()
    ratios: list[float] = []
    try:
        for packet in container.demux(stream):
            if packet.size == 0:
                continue
            for frame in ctx.decode(packet):
                if len(frame.planes) < 4:
                    return False
                data = bytes(frame.planes[3])
                sigs.add(data[:4096])  # 取前 4KB 当指纹，够分辨"逐帧是否变化"
                ratios.append(sum(1 for b in data if b < 128) / len(data))
                if len(ratios) >= 5:
                    return len(sigs) >= 2 and 0.3 < min(ratios) < 0.99
    finally:
        container.close()
    return False


def _encode_alpha_webm(
    frames: list[Image.Image], box: tuple[int, int, int, int], dest: Path
) -> None:
    """编码 VP9 + alpha 的 WebM（FFmpeg 用 yuva420p + libvpx-vp9 写 BlockAdditional alpha）。"""
    container = av.open(str(dest), mode="w", format="webm")
    stream = container.add_stream("libvpx-vp9", rate=24)
    stream.width = box[2] - box[0]
    stream.height = box[3] - box[1]
    stream.pix_fmt = "yuva420p"
    stream.options = {"crf": "28", "b:v": "0", "auto-alt-ref": "0"}
    for im in frames:
        frame = av.VideoFrame.from_image(im.crop(box))
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def parse_fps(spec: str) -> float | None:
    """解析 `--fps`（**播放帧率**，与源帧率解耦）；空串返回 None。

    为什么要解耦：裁剪出来的短片段按原速播放会过快——实测 `idle-open` 裁出 29 帧，
    按 24fps 播是 1.2 秒一轮呼吸（≈50 次/分钟，像喘气）。改成 10fps 就是 2.9 秒一轮 ✓
    """
    if not spec.strip():
        return None
    try:
        value = float(spec)
    except ValueError as exc:
        raise SystemExit(f"--fps 需要数字，收到：{spec!r}") from exc
    if value <= 0:
        raise SystemExit(f"--fps 必须为正数，收到：{spec!r}")
    return value


def parse_frames(spec: str) -> tuple[int, int] | None:
    """解析 `--frames START:END`（左闭右开）；空串返回 None。

    用途：素材里常有"只有一段能用"的情况——实测 `睁眼呼吸.mp4` 全程 241 帧，
    但只有 f212–f240 是睁眼的（f20–f204 她闭着眼）。裁到那一段再乒乓播放，
    就能拿到"睁眼待机"而**不必重新生成**。
    """
    if not spec.strip():
        return None
    parts = spec.replace(" ", "").split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise SystemExit(f"--frames 需要 START:END（如 212:241），收到：{spec!r}")
    start, end = int(parts[0]), int(parts[1])
    if end <= start:
        raise SystemExit(f"--frames 的 END 必须大于 START，收到：{spec!r}")
    return start, end


def align_tail(
    frames: Sequence[Image.Image],
    cut: Sequence[Image.Image],
    search_ratio: float = 0.5,
) -> int:
    """把**收尾裁到"最接近首帧姿势"的那一帧**，返回裁剪后的帧数。

    **为什么这才是"衔接自然"的关键**：dsh-pet 的无缝衔接**不是靠交叉淡入淡出**，
    而是因为它的 97 段素材是同一个画师按同一套角色规范画的——
    **每段都从同一个中立姿势开始、也回到那里结束**，所以在片段边界上硬切也看不出接缝。

    我们的素材全是**从同一张首帧生成**的，所以"起点姿势"天然一致
    （frame 0 = 中立姿势）；但每段的**收尾姿势各不相同**（AI 停在哪就是哪）。
    把尾巴裁到最像首帧的那一帧，就把这套素材变成"首尾同姿势"，
    片段之间的硬切也就自然顺了——这比任何混合都更接近 dsh-pet 的效果。

    只在片段后半段搜索（`search_ratio`），避免把动作本身裁掉。
    """
    n = len(cut)
    if n < 4:
        return n
    ref_a = cut[0].getchannel("A")
    ref_rgb = frames[0].convert("RGB")
    start = max(1, int(n * (1.0 - search_ratio)))
    best_i, best_d = n - 1, float("inf")
    for i in range(start, n):
        d = _mean_diff(ref_a, cut[i].getchannel("A")) + _mean_diff(
            ref_rgb, frames[i].convert("RGB")
        )
        if d < best_d:
            best_d, best_i = d, i
    return best_i + 1


def parse_canvas(spec: str) -> tuple[int, int] | None:
    """解析 `--canvas WxH`；空串返回 None，格式错直接退出（不静默忽略）。"""
    if not spec.strip():
        return None
    parts = spec.lower().replace(" ", "").split("x")
    if len(parts) != 2 or not all(p.isdigit() and int(p) > 0 for p in parts):
        raise SystemExit(f"--canvas 需要 WxH（如 480x660），收到：{spec!r}")
    return int(parts[0]), int(parts[1])


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(description="Elysia 桌宠素材流水线（体检 / 抠图 / 编码）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="体检素材")
    p_ins.add_argument("paths", nargs="+", type=Path)

    p_mat = sub.add_parser("matte", help="抠成透明序列帧 + manifest")
    p_mat.add_argument("--src", required=True, type=Path)
    p_mat.add_argument("--out", required=True, type=Path)
    p_mat.add_argument("--tol", type=int, default=DEFAULT_TOLERANCE)
    p_mat.add_argument("--webm", action="store_true", help="额外尝试编码 alpha WebM（会自验）")
    p_mat.add_argument(
        "--canvas",
        default="",
        metavar="WxH",
        help="统一画布尺寸（如 480x660）：各片段等比缩放进同一画布、底边居中。"
        "同一服装的所有片段**必须用同一个**，否则换动作时她会突然变大变小",
    )
    p_mat.add_argument(
        "--fps",
        default="",
        metavar="N",
        help="**播放帧率**覆盖（与源帧率解耦）：裁出的短片段按原速会过快，用它调慢",
    )
    p_mat.add_argument(
        "--align-tail",
        action="store_true",
        help="把收尾裁到最接近首帧姿势的一帧（**单次片段必用**）：dsh-pet 式的无缝衔接"
        "靠的是「每段首尾同姿势」，不是靠混合",
    )
    p_mat.add_argument(
        "--frames",
        default="",
        metavar="START:END",
        help="只取这个帧区间（左闭右开，如 212:241）。素材里只有一段能用时用",
    )
    p_mat.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="x0,y0,x1,y1",
        help="显式排除区（可重复）：用于泛洪填充拦不住的深色水印/游戏 logo",
    )

    p_por = sub.add_parser("portrait", help="把立绘做成单帧片段的服装包（换装用）")
    p_por.add_argument("--src", required=True, type=Path)
    p_por.add_argument(
        "--out", required=True, type=Path, help="服装包目录，如 assets/pet/costumes/c2"
    )
    p_por.add_argument("--name", default="", help="给菜单看的服装名，如「女仆装」")
    p_por.add_argument("--tol", type=int, default=DEFAULT_TOLERANCE)

    args = parser.parse_args(argv)
    if args.cmd == "inspect":
        rc = 0
        for p in args.paths:
            if not p.exists():
                print(f"  不存在: {p}", file=sys.stderr)
                rc = 1
                continue
            rc |= inspect(p)
        return rc
    if args.cmd == "portrait":
        if not args.src.exists():
            print(f"  不存在: {args.src}", file=sys.stderr)
            return 1
        name = args.name or args.out.name
        ok = portrait_clip(args.src, args.out, name, args.tol)
        return 0 if ok else 1
    exclude: list[tuple[int, int, int, int]] = []
    for spec in args.exclude:
        parts = [int(v) for v in spec.replace(" ", "").split(",")]
        if len(parts) != 4:
            print(f"  --exclude 需要 x0,y0,x1,y1 四个整数，收到：{spec!r}", file=sys.stderr)
            return 2
        exclude.append((parts[0], parts[1], parts[2], parts[3]))
    return matte_clip(
        args.src,
        args.out,
        args.tol,
        args.webm,
        tuple(exclude),
        parse_canvas(args.canvas),
        parse_frames(args.frames),
        parse_fps(args.fps),
        args.align_tail,
    )


if __name__ == "__main__":
    sys.exit(main())
