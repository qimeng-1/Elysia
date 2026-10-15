// Spine → 序列帧烘焙器
// 用法: node bake.js <outfit> <animName> [fps] [targetHeight]
// 输出: out/<outfit>/<animName>/0000.png ...（透明 RGBA，统一尺寸与锚点）
//
// 关键设计：
//  · 缩放与锚点**从 setup pose 算一次**，所有动画共用 ⇒ 角色大小/站位天然一致
//    （这正是 AI 生成素材做不到、而用户抱怨"大小变化不一致"的根因）
//  · 输出高度对齐到 targetHeight，与现有服装包同一个基准 ⇒ 换装时大小不跳
//  · Y 轴翻转（Spine Y 向上、canvas Y 向下）
const fs = require("fs"), path = require("path");
const spine = require("@esotericsoftware/spine-canvas");
const { createCanvas, Image } = require("canvas");

const DIR = "D:/ElysiaAssets/spine_elysia";
const OUTFIT = process.argv[2] || "elvisa_1";
const ANIM = process.argv[3] || "1_idle";
const FPS = parseInt(process.argv[4] || "24", 10);
const TARGET_H = parseFloat(process.argv[5] || "560");
const MARGIN = 14; // 画布四周留白（防动作出画）

function loadData(name) {
  const atlas = new spine.TextureAtlas(fs.readFileSync(path.join(DIR, name + ".atlas"), "utf8"));
  for (const page of atlas.pages) {
    const img = new Image();
    img.src = fs.readFileSync(path.join(DIR, page.name));
    const tex = new spine.CanvasTexture(img);
    // spine-canvas 的 CanvasTexture 没实现 getImage()，而 spine-core 会调它
    tex.getImage = () => img;
    page.setTexture(tex);
  }
  const bin = new spine.SkeletonBinary(new spine.AtlasAttachmentLoader(atlas));
  const buf = fs.readFileSync(path.join(DIR, name + ".skel"));
  const bytes = new Uint8Array(buf.length);
  bytes.set(buf);
  return bin.readSkeletonData(bytes);
}

const data = loadData(OUTFIT);
const anim = data.animations.find((a) => a.name === ANIM);
if (!anim) {
  console.error("  没有动画:", ANIM, "| 可选:", data.animations.map((a) => a.name).join(", "));
  process.exit(1);
}

// ① 从 setup pose 定标：缩放 + 画布尺寸 + 锚点
const probe = new spine.Skeleton(data);
probe.setToSetupPose();
probe.updateWorldTransform();
const off = new spine.Vector2(), size = new spine.Vector2();
probe.getBounds(off, size, []);
const scale = TARGET_H / size.y;
const cx = off.x + size.x / 2;
const CW = Math.ceil(size.x * scale) + MARGIN * 2;
const CH = Math.ceil(size.y * scale) + MARGIN * 2;
// setup 包围盒的"底部中心" ⇒ 画布底部中心
const sx = CW / 2 - cx * scale;
const sy = CH - MARGIN + off.y * scale;

console.log(`  ${OUTFIT} / ${ANIM}`);
console.log(`  setup 包围盒 ${size.x.toFixed(0)}x${size.y.toFixed(0)} 偏移(${off.x.toFixed(0)},${off.y.toFixed(0)})` +
            ` ⇒ 缩放 ${scale.toFixed(4)} | 画布 ${CW}x${CH}`);
console.log(`  动画时长 ${anim.duration.toFixed(3)}s @ ${FPS}fps`);

// ② 逐帧渲染。
// **多渲一帧**：循环动画在 t=时长 处的姿势等于 t=0，补上这一帧循环才真正闭合
// （实测 48 帧版 walk 的首尾剪影差 3.49，差一点就不算无缝；
//   补帧后首尾同图 ⇒ 天然无缝，也避免被播放器当成"循环跳"改用乒乓——那会变成倒着走）
const N = Math.max(2, Math.round(anim.duration * FPS)) + 1;
const outDir = path.join(__dirname, "out", OUTFIT, ANIM);
fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(outDir, { recursive: true });

const canvas = createCanvas(CW, CH);
const ctx = canvas.getContext("2d");
const stateData = new spine.AnimationStateData(data);
let minPx = Infinity, maxPx = 0;
for (let i = 0; i < N; i++) {
  ctx.clearRect(0, 0, CW, CH);
  const sk = new spine.Skeleton(data);
  sk.setToSetupPose();
  sk.scaleX = scale;
  sk.scaleY = -scale;
  sk.x = sx;
  sk.y = sy;
  const st = new spine.AnimationState(stateData);
  st.setAnimation(0, ANIM, true);
  st.update(i / FPS);
  st.apply(sk);
  sk.updateWorldTransform();
  const r = new spine.SkeletonRenderer(ctx);
  r.premultipliedAlpha = true;
  r.triangleRendering = true;
  r.draw(sk);
  const d = ctx.getImageData(0, 0, CW, CH).data;
  let n = 0;
  for (let k = 3; k < d.length; k += 4) if (d[k] > 8) n++;
  minPx = Math.min(minPx, n); maxPx = Math.max(maxPx, n);
  fs.writeFileSync(path.join(outDir, String(i).padStart(4, "0") + ".png"), canvas.toBuffer("image/png"));
}
fs.writeFileSync(
  path.join(outDir, "manifest.json"),
  JSON.stringify({
    source: `Spine ${OUTFIT} / ${ANIM}`,
    frame_count: N, fps: FPS, size: [CW, CH],
    spine_anim_duration_s: anim.duration,
    spine_scale: scale, costume_source: OUTFIT,
    character_height_px: TARGET_H,
  }, null, 2)
);
console.log(`  输出 ${N} 帧 → ${outDir} | 非透明像素 ${minPx}~${maxPx}`);
