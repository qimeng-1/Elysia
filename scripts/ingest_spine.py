"""把 Spine 烘焙出来的透明序列帧入库成一套服装包。

与 `asset_pipeline.py` 的关系：**不需要抠图**——Spine 渲出来本身就是透明 RGBA，
而且同一套骨骼的缩放/锚点由 `bake.js` 统一给定，所以这里只做：

    源 PNG 序列 → frames/（无损母版）+ webp/（运行时）+ manifest.json

用法：
    uv run python scripts/ingest_spine.py --src "D:/ElysiaAssets/spine_bake/out/elvisa_1" \
        --out assets/pet/costumes/s1 --name "花冠白裙（Spine）" \
        --clip idle=1_idle --clip walk=2_walking_default \
        --clip wave=4_wavehands --clip cheer=3_applaud
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

WEBP_QUALITY = 92
LOOP_ALPHA_OK = 3.0
LOOP_RGB_OK = 8.0


def _mean_diff(a: Image.Image, b: Image.Image) -> float:
    return float(ImageStat.Stat(ImageChops.difference(a.convert("L"), b.convert("L"))).mean[0])


def ingest_clip(
    src_dir: Path,
    out_dir: Path,
    clip_id: str,
    downscale: int = 1,
    start: int = 0,
    end: int = 0,
) -> dict[str, object]:
    """一个动画 → 一个片段目录（frames/webp/manifest）。

    `downscale > 1`：烘焙器是**超采样**渲的（为了消掉三角形缝隙），这里降采样回目标尺寸。
    `start/end`：只取这个帧区间（`end=0` 表示到末尾）——用来避开原动画里不好看的相位。
    """
    files = sorted(p for p in src_dir.glob("*.png"))
    if not files:
        raise SystemExit(f"  {clip_id}: 源目录没有 PNG：{src_dir}")
    if start or end:
        files = files[start : (end or len(files))]
        if not files:
            raise SystemExit(f"  {clip_id}: 裁剪 {start}:{end} 后没有帧")
    src_manifest = {}
    mf = src_dir / "manifest.json"
    if mf.is_file():
        src_manifest = json.loads(mf.read_text(encoding="utf-8"))
    fps = float(src_manifest.get("fps", 24))

    frames_dir, webp_dir = out_dir / "frames", out_dir / "webp"
    for d in (frames_dir, webp_dir):
        if d.is_dir():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    first = last = None
    size = None
    for i, path in enumerate(files):
        with Image.open(path) as raw:
            im = raw.convert("RGBA")
        if downscale > 1:
            im = im.resize(
                (max(1, im.width // downscale), max(1, im.height // downscale)),
                Image.LANCZOS,
            )
        size = list(im.size)
        im.save(frames_dir / f"{i:04d}.png", optimize=True)
        im.save(webp_dir / f"{i:04d}.webp", "WEBP", quality=WEBP_QUALITY, method=4)
        if i == 0:
            first = im.copy()
        last = im.copy()
    assert first is not None and last is not None

    loop_a = _mean_diff(first.getchannel("A"), last.getchannel("A"))
    loop_rgb = _mean_diff(first, last)
    loops = loop_a < LOOP_ALPHA_OK and loop_rgb < LOOP_RGB_OK
    manifest = {
        "source": src_manifest.get("source", str(src_dir)),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": "spine-baked",
        "frame_count": len(files),
        "fps": fps,
        "size": size,
        "downscale": downscale,
        "trimmed_frames": [start, end] if (start or end) else None,
        "loop_alpha_diff": round(loop_a, 3),
        "loop_rgb_diff": round(loop_rgb, 3),
        "loops_seamlessly": loops,
        "suggest_ping_pong": not loops,
        "spine": {k: v for k, v in src_manifest.items() if k.startswith("spine_")},
        "master_frames": "frames/*.png（无损母版）",
        "runtime_frames": f"webp/*.webp（q{WEBP_QUALITY}）",
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"  {clip_id:<8} {len(files):>4}帧 {fps:>4g}fps {len(files) / fps:>5.2f}s "
        f"{size[0]}x{size[1]}  {'无缝循环' if loops else '循环跳(靠溶解/乒乓)'}"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 Spine 烘焙结果入库成服装包")
    ap.add_argument("--src", required=True, type=Path, help="bake.js 的输出目录（含各动画子目录）")
    ap.add_argument("--out", required=True, type=Path, help="服装包目录，如 assets/pet/costumes/s1")
    ap.add_argument("--name", required=True, help="菜单里显示的名字")
    ap.add_argument(
        "--downscale",
        type=int,
        default=1,
        help="烘焙器超采样的倍数（用它降采样回目标尺寸；bake.js 默认 2x ⇒ 这里填 2）",
    )
    ap.add_argument(
        "--clip",
        action="append",
        required=True,
        metavar="ID=目录名",
        help="片段映射，可重复，如 --clip idle=1_idle",
    )
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    mapping: list[tuple[str, str, int, int]] = []
    for spec in args.clip:
        if "=" not in spec:
            print(f"  --clip 需要 ID=目录名[:起:止]，收到：{spec!r}", file=sys.stderr)
            return 2
        cid, sub = spec.split("=", 1)
        parts = sub.split(":")
        start, end = 0, 0
        if len(parts) >= 2 and parts[1].strip():
            start = int(parts[1])
        if len(parts) >= 3 and parts[2].strip():
            end = int(parts[2])
        mapping.append((cid.strip(), parts[0].strip(), start, end))

    sizes = set()
    for cid, sub, start, end in mapping:
        m = ingest_clip(args.src / sub, args.out / cid, cid, args.downscale, start, end)
        sizes.add(tuple(m["size"]))  # type: ignore[arg-type]
    if len(sizes) > 1:
        print(f"  ⚠️ 各片段尺寸不一致：{sizes}（换动作时角色大小会跳）", file=sys.stderr)

    (args.out / "costume.json").write_text(
        json.dumps(
            {
                "id": args.out.name,
                "name": args.name,
                "source": f"Spine 烘焙：{args.src}",
                "note": "同一套骨骼烘焙 ⇒ 各片段大小与站位天然一致",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  完成：{len(mapping)} 个片段 → {args.out}（{args.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
