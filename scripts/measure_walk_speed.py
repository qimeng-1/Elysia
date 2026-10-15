"""测出"走动片段"自身隐含的地面速度，用来校准漫游位移速度（否则会滑步）。

原理：Spine 的走动动画通常在**原地**播（角色不位移），着地的那只脚相对画面
**向后移动**，其移动速度就等于"她如果真在走、身体该前进的速度"。
所以：
  1. 逐帧取**接触点**：内容包围盒**最下面若干行**的 alpha 加权 x 质心
  2. 相邻帧求速度 v = Δx · fps
  3. 换脚瞬间会有跳变 ⇒ 用中位数（并剔除 > 3 倍中位数的跳变）取稳健值
中位数就是"她走路时地面在画面里掠过的速度"，也就是漫游该用的 px/s。

用法：uv run python scripts/measure_walk_speed.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from PIL import Image

COSTUMES = Path("assets/pet/costumes")
CONTACT_ROWS = 6  # 取包围盒最下面几行作为"接触点"


def contact_x(path: Path) -> float | None:
    with Image.open(path) as raw:
        im = raw.convert("RGBA")
    a = im.getchannel("A")
    bb = a.getbbox()
    if bb is None:
        return None
    x0, _y0, x1, y1 = bb
    band = a.crop((x0, max(bb[1], y1 - CONTACT_ROWS), x1, y1))
    px = band.load()
    w, h = band.size
    num = den = 0.0
    for y in range(h):
        for x in range(w):
            v = px[x, y]
            if v > 40:
                num += (x + x0) * v
                den += v
    return num / den if den else None


def measure(clip_dir: Path) -> dict[str, float] | None:
    webp = clip_dir / "webp"
    files = sorted(webp.glob("*.webp"))
    mf = clip_dir / "manifest.json"
    if not files or not mf.is_file():
        return None
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    fps = float(manifest.get("fps", 24))
    xs = [contact_x(p) for p in files]
    xs = [x for x in xs if x is not None]
    if len(xs) < 4:
        return None
    vels = [(xs[i + 1] - xs[i]) * fps for i in range(len(xs) - 1)]
    med = statistics.median(vels)
    # 剔除"换脚跳变"（明显偏离中位数者）
    scale = statistics.median([abs(v) for v in vels]) or 1.0
    keep = [v for v in vels if abs(v) <= 3 * scale]
    return {
        "frames": len(files),
        "fps": fps,
        "span_px": round(max(xs) - min(xs), 1),
        "v_median": round(statistics.median(keep), 1) if keep else 0.0,
        "v_absmean": round(statistics.fmean([abs(v) for v in keep]), 1) if keep else 0.0,
        "raw_median": round(med, 1),
    }


def main() -> int:
    print("  走动片段的隐含地面速度（px/s，画面像素 = 屏幕像素，角色高统一 560）")
    print(
        f"  {'服装':<6} {'帧':>4} {'fps':>4} {'接触点跨度':>10} {'稳健中位':>9} "
        f"{'剔除跳变后均值':>14}"
    )
    speeds: list[float] = []
    for cdir in sorted(COSTUMES.iterdir()):
        if not cdir.is_dir():
            continue
        r = measure(cdir / "walk")
        if not r:
            continue
        v = r["v_absmean"] or r["v_median"]
        speeds.append(v)
        print(
            f"  {cdir.name:<6} {r['frames']:>4} {r['fps']:>4.0f} "
            f"{r['span_px']:>10.1f} {r['v_median']:>9.1f} {v:>14.1f}"
        )
    if speeds:
        median = statistics.median(speeds)
        print(f"\n  各套中位数 = {median:.1f} px/s | 范围 {min(speeds):.0f}~{max(speeds):.0f}")
        # 读代码里的**真实常量**，别硬编码：改了速度这里要能反映出来
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
            from elysia.body.pet import WANDER_SPEED_PX_S

            delta = (WANDER_SPEED_PX_S - median) / median * 100
            verdict = "基本相称 ✓" if abs(delta) <= 10 else f"**偏差 {delta:+.0f}% ⇒ 会滑步**"
            print(f"  代码里的 WANDER_SPEED_PX_S = {WANDER_SPEED_PX_S} px/s ⇒ {verdict}")
        except Exception as exc:  # pragma: no cover - 只是提示，不该阻塞测量
            print(f"  （读不到代码常量：{exc}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
