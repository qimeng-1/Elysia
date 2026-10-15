// 预检：对每套服装列动画/时长、查图集区域是否齐全、并渲染缩略图拼图供肉眼检查
// 检查项（都是这次踩过坑之后加的）：
//   ① 空动画（时长≈0，如 elvisa_1 的 0_left/0_right）
//   ② 骨架引用的图集区域是否齐全（缺了会在解析时炸）
//   ③ 每个动画的实际画面（用来看"眉毛穿眼"这类作者问题）
//   ④ 各套服装的动画集合是否一致
const fs = require("fs"), path = require("path");
const spine = require("@esotericsoftware/spine-canvas");
const { createCanvas, Image } = require("canvas");

const DIR = "D:/ElysiaAssets/spine_elysia";
const OUTFITS = ["elvisa_1", "elvisa_2", "elvisa_3", "elvisa_4", "elvisa_5", "elvisa_6"];
const CELL = 132, COLS = 4, SUPERSAMPLE = 2;

// 与 bake.js 相同的缝隙修复：三角形沿重心外扩
{
  const proto = spine.SkeletonRenderer.prototype;
  const orig = proto.drawTriangle;
  const EDGE = 2.0;
  proto.drawTriangle = function (img, x0, y0, u0, v0, x1, y1, u1, v1, x2, y2, u2, v2) {
    const cx = (x0 + x1 + x2) / 3, cy = (y0 + y1 + y2) / 3;
    const push = (x, y) => {
      const dx = x - cx, dy = y - cy, len = Math.hypot(dx, dy) || 1e-6;
      return [x + (dx / len) * EDGE, y + (dy / len) * EDGE];
    };
    const a = push(x0, y0), b = push(x1, y1), c = push(x2, y2);
    return orig.call(this, img, a[0], a[1], u0, v0, b[0], b[1], u1, v1, c[0], c[1], u2, v2);
  };
}

function loadAtlas(name, report) {
  const text = fs.readFileSync(path.join(DIR, name + ".atlas"), "utf8");
  const atlas = new spine.TextureAtlas(text);
  for (const page of atlas.pages) {
    const img = new Image();
    img.src = fs.readFileSync(path.join(DIR, page.name));
    const tex = new spine.CanvasTexture(img);
    tex.getImage = () => img;
    page.setTexture(tex);
    report.pageSize = [img.width, img.height];
  }
  return atlas;
}

function checkOutfit(name) {
  const report = { name, regions: 0, pages: 0, bones: 0, slots: 0, anims: [], missing: [] };
  let atlas, data;
  try {
    atlas = loadAtlas(name, report);
    report.regions = atlas.regions.length;
    report.pages = atlas.pages.length;
    // 拦截 findRegion：骨架引用了但图集里没有的区域会被记下来
    const origFind = atlas.findRegion.bind(atlas);
    atlas.findRegion = (n) => {
      const r = origFind(n);
      if (!r && report.missing.length < 6) report.missing.push(n);
      return r;
    };
    const bin = new spine.SkeletonBinary(new spine.AtlasAttachmentLoader(atlas));
    const buf = fs.readFileSync(path.join(DIR, name + ".skel"));
    const bytes = new Uint8Array(buf.length);
    bytes.set(buf);
    data = bin.readSkeletonData(bytes);
    report.bones = data.bones.length;
    report.slots = data.slots.length;
  } catch (e) {
    report.error = e.message;
    return report;
  }

  const anims = data.animations.map((a) => ({ name: a.name, dur: a.duration }));
  report.anims = anims.map((a) => `${a.name}(${a.dur.toFixed(2)}s)`);

  // 渲染缩略图：每个动画取 4 帧
  const usable = anims.filter((a) => a.dur > 0.05);
  const rows = usable.length;
  const sheet = createCanvas(CELL * COLS, CELL * rows + 18 * rows);
  const sctx = sheet.getContext("2d");
  sctx.fillStyle = "#20222c";
  sctx.fillRect(0, 0, sheet.width, sheet.height);

  // 统一变换（从 setup pose 定标，所有动画共用）
  const probe = new spine.Skeleton(data);
  probe.setToSetupPose();
  probe.updateWorldTransform();
  const off = new spine.Vector2(), size = new spine.Vector2();
  probe.getBounds(off, size, []);
  const targetH = 560 * SUPERSAMPLE;
  const scale = targetH / size.y;
  const CW = Math.ceil(size.x * scale) + 28;
  const CH = Math.ceil(size.y * scale) + 28;
  const sx = CW / 2 - (off.x + size.x / 2) * scale;
  const sy = CH - 28 + off.y * scale;
  const canvas = createCanvas(CW, CH);
  const ctx = canvas.getContext("2d");
  const stateData = new spine.AnimationStateData(data);

  usable.forEach((a, r) => {
    for (let c = 0; c < COLS; c++) {
      ctx.clearRect(0, 0, CW, CH);
      const sk = new spine.Skeleton(data);
      sk.setToSetupPose();
      sk.scaleX = scale;
      sk.scaleY = -scale;
      sk.x = sx;
      sk.y = sy;
      const st = new spine.AnimationState(stateData);
      st.setAnimation(0, a.name, true);
      st.update((a.dur * c) / COLS);
      st.apply(sk);
      sk.updateWorldTransform();
      const ren = new spine.SkeletonRenderer(ctx);
      ren.premultipliedAlpha = true;
      ren.triangleRendering = true;
      ren.draw(sk);
      const thumb = createCanvas(CELL - 8, CELL - 8);
      const tctx = thumb.getContext("2d");
      tctx.fillStyle = "#3c4258";
      tctx.fillRect(0, 0, CELL - 8, CELL - 8);
      const k = Math.min((CELL - 12) / CW, (CELL - 12) / CH);
      tctx.drawImage(canvas, (CELL - 8 - CW * k) / 2, (CELL - 8 - CH * k) / 2, CW * k, CH * k);
      sctx.drawImage(thumb, c * CELL + 4, r * (CELL + 18) + 4);
      if (c === 0) {
        sctx.fillStyle = "#eef2ff";
        sctx.font = "13px sans-serif";
        sctx.fillText(a.name.slice(0, 18), c * CELL + 6, r * (CELL + 18) + CELL + 16);
      }
    }
  });
  const outDir = path.join(__dirname, "precheck");
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, name + ".png"), sheet.toBuffer("image/png"));
  report.sheet = path.join(outDir, name + ".png");
  return report;
}

for (const o of OUTFITS) {
  const r = checkOutfit(o);
  console.log(`\n=== ${r.name} ===`);
  if (r.error) { console.log("  **解析失败**:", r.error); continue; }
  console.log(`  图集 ${r.pages} 页 / ${r.regions} 区域（贴图 ${r.pageSize ? r.pageSize.join("x") : "?"}）`);
  console.log(`  骨骼 ${r.bones} / 槽位 ${r.slots}`);
  console.log(`  动画 ${r.anims.length}: ${r.anims.join(" ")}`);
  const stubs = r.anims.filter((a) => /\(0\.00s\)/.test(a));
  if (stubs.length) console.log(`  ⚠️ 空动画(${stubs.length}): ${stubs.join(" ")}`);
  if (r.missing.length) console.log(`  ⚠️ 图集缺失区域: ${r.missing.join(", ")}`);
  console.log(`  预检图: ${r.sheet}`);
}
