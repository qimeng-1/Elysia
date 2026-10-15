// 为一套服装计算**共享画布**：取该服装所有动画、所有采样帧内容包围盒的并集 + 留白。
//
// 为什么必须这样算（实测缺陷）：旧做法只用 **setup pose** 的包围盒定画布尺寸，
// 而动画摆幅更大（头发/裙摆甩出去）⇒ 内容被画布**平切**。实测已入库片段：
//   s1/idle 145 帧里 46 帧触底、s1/walk 49/49 帧触左右、s2/idle 57 帧触顶。
// 另外旧代码的 `MARGIN=14` 在 2x 超采样画布上等于只有 7px 成品留白。
//
// 产出 out/<outfit>/canvas.json：{ scale, CW, CH, sx, sy, anchor }，由 bake.js 读取，
// 于是**同一套服装的所有片段共用同一画布** ⇒ ① 永不被裁 ② 尺寸与站位天然一致。
//
// 用法: node canvas.js [outfit...]       （不给参数则处理全部 6 套）
const fs = require("fs"), path = require("path");
const spine = require("@esotericsoftware/spine-canvas");

const DIR = "D:/ElysiaAssets/spine_elysia";
const OUTFITS = process.argv.slice(2).length
  ? process.argv.slice(2)
  : ["elvisa_1", "elvisa_2", "elvisa_3", "elvisa_4", "elvisa_5", "elvisa_6"];
const TARGET_H_OUT = 560; // 成品角色高度
const MARGIN_OUT = 16; // 成品留白（比原来的 7px 宽，给摆幅留余量）
const SAMPLES = 16; // 每个动画采样多少帧
const MIN_DUR = 0.05; // 时长小于此视为空动画（0_left/0_right 就是）

function loadData(name) {
  const atlas = new spine.TextureAtlas(fs.readFileSync(path.join(DIR, name + ".atlas"), "utf8"));
  for (const page of atlas.pages) {
    const img = new (require("canvas").Image)();
    img.src = fs.readFileSync(path.join(DIR, page.name));
    const tex = new spine.CanvasTexture(img);
    tex.getImage = () => img;
    page.setTexture(tex);
  }
  const bin = new spine.SkeletonBinary(new spine.AtlasAttachmentLoader(atlas));
  const buf = fs.readFileSync(path.join(DIR, name + ".skel"));
  const bytes = new Uint8Array(buf.length);
  bytes.set(buf);
  return bin.readSkeletonData(bytes);
}

for (const outfit of OUTFITS) {
  const data = loadData(outfit);
  // 基准：setup pose 的底部中心（作为所有片段共用的锚点）
  const setup = new spine.Skeleton(data);
  setup.setToSetupPose();
  setup.updateWorldTransform();
  const so = new spine.Vector2(), ss = new spine.Vector2();
  setup.getBounds(so, ss, []);
  const scale = TARGET_H_OUT / ss.y;
  const ax = so.x + ss.x / 2;
  const ay = so.y;

  // 并集：所有动画 × 采样帧（在骨架空间算，最后统一换算，避免逐帧建大画布）
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  let frames = 0;
  const stateData = new spine.AnimationStateData(data);
  for (const anim of data.animations) {
    if (anim.duration < MIN_DUR) continue;
    for (let k = 0; k < SAMPLES; k++) {
      const sk = new spine.Skeleton(data);
      sk.setToSetupPose();
      sk.x = 0; sk.y = 0; sk.scaleX = 1; sk.scaleY = 1;
      const st = new spine.AnimationState(stateData);
      st.setAnimation(0, anim.name, true);
      st.update((anim.duration * k) / SAMPLES);
      st.apply(sk);
      sk.updateWorldTransform();
      const o = new spine.Vector2(), s = new spine.Vector2();
      sk.getBounds(o, s, []);
      // 相对锚点、换算到"画布向下为正"的成品像素
      const rx0 = (o.x - ax) * scale, rx1 = (o.x + s.x - ax) * scale;
      const ry0 = -(o.y + s.y - ay) * scale, ry1 = -(o.y - ay) * scale;
      minx = Math.min(minx, rx0); maxx = Math.max(maxx, rx1);
      miny = Math.min(miny, ry0); maxy = Math.max(maxy, ry1);
      frames++;
    }
  }
  if (!frames) {
    console.log(`  ${outfit}: 没有可用动画`);
    continue;
  }
  const CW = Math.ceil(maxx - minx) + MARGIN_OUT * 2;
  const CH = Math.ceil(maxy - miny) + MARGIN_OUT * 2;
  // 锚点在画布内的位置
  const AX = -minx + MARGIN_OUT, AY = -miny + MARGIN_OUT;
  const out = {
    outfit, scale,
    CW, CH,
    // bake.js 直接用这两个做骨架平移（canvas_y = sy - src_y*scale）
    sx: AX - ax * scale,
    sy: AY + ay * scale,
    anchor: { ax, ay, AX, AY },
    union_px: { minx: Math.round(minx), maxx: Math.round(maxx), miny: Math.round(miny), maxy: Math.round(maxy) },
    samples: frames,
    target_height_px: TARGET_H_OUT,
    margin_px: MARGIN_OUT,
  };
  const dir = path.join(__dirname, "out", outfit);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "canvas.json"), JSON.stringify(out, null, 2));
  console.log(`  ${outfit}: 采样 ${frames} 帧 | 摆幅 x[${Math.round(minx)},${Math.round(maxx)}] y[${Math.round(miny)},${Math.round(maxy)}]` +
              ` ⇒ 画布 ${CW}x${CH}（角色高仍 ${TARGET_H_OUT}，留白 ${MARGIN_OUT}）`);
}
