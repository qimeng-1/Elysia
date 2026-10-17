"""入库前全量复查：把"打算入库的每个动画 × 每套服装"的眼睛区域拼成一张总表。

背景：`3_applaud` 在全部 6 套里都是"眉毛横在眼睛中间"（系统性作者问题）。
所以入库前必须把**所有要用的动画**都按全分辨率过一眼，而不是只看缩略图。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def default_bake_dir() -> Path | None:
    """Spine 烘焙输出目录（素材工具用，仓库外路径）。**未配置返回 None**。

    P0-R13（可迁移性）：原本写死本机盘符路径，换机/换盘即失效。
    现在从 `ELYSIA_BAKE_DIR` 或 `--bake` 读；都没有就明确报"未配置"，不猜。
    """
    raw = (os.environ.get("ELYSIA_BAKE_DIR") or "").strip()
    return Path(raw).expanduser() if raw else None


OUTFITS = ["elvisa_1", "elvisa_2", "elvisa_3", "elvisa_4", "elvisa_5", "elvisa_6"]
# 计划入库的动画（key = Spine 动画名，值 = 用途）
ANIMS = [
    ("1_idle", "idle 待机"),
    ("2_walking_default", "walk 走动"),
    ("4_wavehands", "wave 挥手"),
    ("5_special_ani", "cheer 比心"),
    ("3_applaud", "applaud（疑似全坏，复查）"),
    ("1_idle_hunmi", "doze 昏睡（仅 elvisa_2）"),
    ("1_idle_cry", "sad 哭泣（仅 elvisa_6）"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Spine 入库前全量复查（拼一张眼区总表）")
    parser.add_argument(
        "--bake",
        type=Path,
        default=None,
        help="烘焙输出目录（缺省读 ELYSIA_BAKE_DIR；两者都没有则报未配置）",
    )
    args = parser.parse_args(argv)
    bake = args.bake or default_bake_dir()
    if bake is None:
        print(
            "[未配置] 找不到 Spine 烘焙输出目录：请用 --bake 指定，或设环境变量 "
            "ELYSIA_BAKE_DIR（它不在仓库内，换机器要重新指一次）",
            file=sys.stderr,
        )
        return 2
    if not bake.is_dir():
        print(f"[失败] 烘焙输出目录不存在：{bake}", file=sys.stderr)
        return 2

    cw, ch = 170, 92
    sheet = Image.new("RGB", (cw * len(OUTFITS) + 150, ch * len(ANIMS)), (26, 28, 36))
    dr = ImageDraw.Draw(sheet)
    for c, outfit in enumerate(OUTFITS):
        dr.text((150 + c * cw + 6, 4), outfit, fill=(255, 230, 150))
    for r, (anim, label) in enumerate(ANIMS):
        dr.text((6, r * ch + ch // 2 - 6), label[:22], fill=(235, 240, 255))
        for c, outfit in enumerate(OUTFITS):
            files = sorted((bake / outfit / anim).glob("*.png"))
            if not files:
                dr.text((150 + c * cw + 20, r * ch + ch // 2), "—", fill=(120, 126, 140))
                continue
            with Image.open(files[len(files) // 2]) as raw:
                im = raw.convert("RGBA")
            w, h = im.size
            crop = im.crop((int(w * 0.34), int(h * 0.30), int(w * 0.66), int(h * 0.52)))
            bg = Image.new("RGBA", crop.size, (74, 80, 100, 255))
            bg.alpha_composite(crop)
            crop = bg.convert("RGB")
            k = min((cw - 8) / crop.width, (ch - 8) / crop.height)
            crop = crop.resize((int(crop.width * k), int(crop.height * k)), Image.LANCZOS)
            sheet.paste(
                crop,
                (150 + c * cw + (cw - crop.width) // 2, r * ch + (ch - crop.height) // 2),
            )
    out = Path(__file__).with_name("check_all.png")
    sheet.save(out)
    print("  总复查表:", out, sheet.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
