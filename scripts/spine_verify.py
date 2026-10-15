"""服装包验收检查 —— 把这次踩过的缺陷固化成自动检查，防止回归。

检查项（每一项都对应一次真实事故）：
  1. **画布边缘裁切**：内容触到画布任一边 ⇒ 被切。
     事故：旧烘焙只用 setup pose 定画布，s1/idle 46/145 帧触底、s1/walk 49/49 帧触左右。
  2. **同套服装各片段尺寸不一致** ⇒ 换动作时角色大小/站位会跳。
  3. **必需的行为片段缺失** ⇒ 该行为降级（拖拽只有物理漂浮、松手直接回待机…）。
  4. **循环片段的循环性**：首尾差异过大而 manifest 却声称无缝 ⇒ 切换时会跳。
  5. **片段有 webp 帧但 manifest 帧数对不上** ⇒ 动作会演到早该删的尾巴上。
  6. **运行时只读 webp/**：缺 webp 目录则该片段形同不存在。

用法：
    uv run python scripts/spine_verify.py            # 全部服装
    uv run python scripts/spine_verify.py s1 s2      # 指定服装
退出码非 0 表示有检查未通过。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

COSTUMES = Path("assets/pet/costumes")
# 行为 → 候选片段 id（顺序即优先级，与 pet.py 里的常量一致）
REQUIRED: dict[str, tuple[str, ...]] = {
    "待机": ("idle-open", "idle", "wait", "breathe"),
    "漫游": ("run", "walk"),
    "点击回应": ("cheer", "wave", "shy", "surprise", "pout", "poke", "click"),
    "拖拽": ("float", "grab"),
    "落地": ("land",),
    "动作后缓冲": ("settle-bridge", "settle"),
    "鼠标移入": ("notice",),
}
LOOP_PREFIXES = ("idle", "wait", "breathe", "run", "walk", "sleep", "float")
EDGE_TOLERANCE = 0  # 允许触边的帧数上限
LOOP_ALPHA_OK = 3.0
LOOP_RGB_OK = 8.0


def _mean_diff(a: Image.Image, b: Image.Image) -> float:
    return float(ImageStat.Stat(ImageChops.difference(a.convert("L"), b.convert("L"))).mean[0])


def check_clip(clip_dir: Path) -> dict[str, object]:
    webp = clip_dir / "webp"
    files = sorted(webp.glob("*.webp")) if webp.is_dir() else []
    mf = clip_dir / "manifest.json"
    manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else {}
    if not files:
        return {"error": "没有 webp 帧"}
    size = Image.open(files[0]).size
    touch = 0
    first = last = None
    for i, p in enumerate(files):
        with Image.open(p) as raw:
            im = raw.convert("RGBA")
        bb = im.getchannel("A").getbbox()
        if bb is None:
            continue
        if bb[0] <= 0 or bb[1] <= 0 or bb[2] >= size[0] or bb[3] >= size[1]:
            touch += 1
        if i == 0:
            first = im.copy()
        last = im.copy()
    res: dict[str, object] = {
        "size": size,
        "frames": len(files),
        "edge_touch": touch,
        "manifest_frames": manifest.get("frame_count"),
    }
    if first is not None and last is not None:
        res["loop_alpha"] = round(_mean_diff(first.getchannel("A"), last.getchannel("A")), 2)
        res["loop_rgb"] = round(_mean_diff(first, last), 2)
    return res


def main(argv: list[str]) -> int:
    targets = argv[1:] or sorted(d.name for d in COSTUMES.iterdir() if d.is_dir())
    problems: list[str] = []
    for cid in targets:
        cdir = COSTUMES / cid
        clips = sorted(d.name for d in cdir.iterdir() if d.is_dir() and (d / "webp").is_dir())
        name = cid
        cj = cdir / "costume.json"
        if cj.is_file():
            name = json.loads(cj.read_text(encoding="utf-8")).get("name", cid)
        print(f"\n=== {cid} {name}（{len(clips)} 片段）===")
        sizes = {}
        for clip in clips:
            r = check_clip(cdir / clip)
            if "error" in r:
                problems.append(f"{cid}/{clip}: {r['error']}")
                print(f"  {clip:<14} **{r['error']}**")
                continue
            sizes.setdefault(r["size"], []).append(clip)
            loop = ""
            if clip.startswith(LOOP_PREFIXES):
                seamless = (
                    r.get("loop_alpha", 99) < LOOP_ALPHA_OK and r.get("loop_rgb", 99) < LOOP_RGB_OK
                )
                loop = f"循环差 {r.get('loop_alpha')}/{r.get('loop_rgb')}" + (
                    "" if seamless else "  **声称无缝但差异过大**"
                )
                if not seamless:
                    problems.append(
                        f"{cid}/{clip}: 循环差过大 {r.get('loop_alpha')}/{r.get('loop_rgb')}"
                    )
            if r["edge_touch"] > EDGE_TOLERANCE:
                problems.append(f"{cid}/{clip}: {r['edge_touch']} 帧触到画布边缘（内容被裁）")
            if r["manifest_frames"] not in (None, r["frames"]):
                problems.append(
                    f"{cid}/{clip}: manifest 说 {r['manifest_frames']} 帧，实际 {r['frames']} 帧"
                )
            print(
                f"  {clip:<14} {r['frames']:>4}帧 {r['size'][0]}x{r['size'][1]} "
                f"触边 {r['edge_touch']}  {loop}"
            )
        if len(sizes) > 1:
            detail = "; ".join(f"{s}: {','.join(c)}" for s, c in sizes.items())
            problems.append(f"{cid}: 各片段尺寸不一致 → {detail}")
            print(f"  **尺寸不一致** → {detail}")
        for label, cands in REQUIRED.items():
            if not any(c in clips for c in cands):
                problems.append(f"{cid}: 缺「{label}」片段（候选 {cands}）")
                print(f"  **缺行为**：{label}（候选 {cands}）")

    print("\n" + "=" * 60)
    if problems:
        print(f"未通过 {len(problems)} 项：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("全部通过：无边缘裁切、尺寸一致、行为齐全、循环正常 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
