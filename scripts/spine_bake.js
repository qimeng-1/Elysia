// Spine → 序列帧烘焙器
// 用法:
//   node bake.js <outfit> <动画名> [fps] [targetHeight]        # 直接烘某个动画
//   node bake.js <outfit> --derive land [fps] [targetHeight]   # 派生：落地压缩（用 1_idle 为基底）
//   node bake.js <outfit> --derive float [fps] [targetHeight]  # 派生：失重漂浮（同上）
//
// 输出: out/<outfit>/<名字>/0000.png ...（透明 RGBA，统一尺寸与锚点）
//
// 关键设计：
//  · 缩放与锚点**从 setup pose 算一次**，所有动画共用 ⇒ 角色大小/站位天然一致
//  · 输出高度对齐到 targetHeight，与现有服装包同一个基准 ⇒ 换装时大小不跳
//  · Y 轴翻转（Spine Y 向上、canvas Y 向下）
//  · **派生动画不新画素材**：在骨骼层加位移/压缩（等同于动画师 K 根骨骼），
//    因此派生片段与其它片段的画布尺寸**完全一致**（不会因画布变大而被窗口裁掉）
//    —— 这是刻意的约束：位移幅度必须塞进烘焙留白（MARGIN）以内
const fs = require("fs"), path = require("path");
const spine = require("@esotericsoftware/spine-canvas");
const { createCanvas, Image } = require("canvas");

const DIR = "D:/ElysiaAssets/spine_elysia";
const argv = process.argv.slice(2);
const DERIVE_IDX = argv.indexOf("--derive");
const DERIVE = DERIVE_IDX >= 0 ? argv[DERIVE_IDX + 1] : null;
// ⚠️ 只有真的出现 `--derive` 时才过滤掉它和它的值：
//    `indexOf` 找不到时返回 -1，若照旧过滤 `i !== -1 && i !== 0` 会把**服装名**吃掉，
//    于是脚本去打开 `1_idle.atlas` 而失败（实测 32 个烘焙全挂在这上面）
const rest = DERIVE_IDX >= 0
  ? argv.filter((_, i) => i !== DERIVE_IDX && i !== DERIVE_IDX + 1)
  : argv.slice();
const OUTFIT = rest[0] || "elvisa_1";
// 注意：派生模式没有"动画名"这个位置参数，所以 fps/height 的位置要往前挪一格
const ANIM = DERIVE ? "1_idle" : rest[1] || "1_idle";
const FPS = parseInt((DERIVE ? rest[1] : rest[2]) || "24", 10);
const TARGET_H_OUT = parseFloat((DERIVE ? rest[2] : rest[3]) || "560");
const MARGIN = 14; // 画布四周留白：派生动画的位移/压缩必须塞进这个范围

// ── 消除"网格线"────────────────────────────────────────────────
// spine-canvas 用"逐三角形 clip + transform + drawImage"贴图。每个三角形的
// clip 路径都带抗锯齿 ⇒ 相邻三角形之间会留下**一条半透明发丝缝**，
// 整片网格因此显形（实测 cheer 单帧 2017 个 alpha 1~40 的像素，
// 观感就是"编辑器网格线"）。
// 修法两条并用：
//   ① **把每个三角形沿重心向外扩一点**（EDGE_PX），让相邻三角形重叠 ⇒ 缝隙被盖住
//   ② **2 倍超采样渲染**，收尾时降采样 ⇒ 残余的亚像素缝被平均掉
const SUPERSAMPLE = 2;
const TARGET_H = TARGET_H_OUT * SUPERSAMPLE;
const EDGE_PX = 1.0 * SUPERSAMPLE;

// 派生参数（都刻意压在 MARGIN 以内，保证画布尺寸与其它片段一致）
// ⚠️ 单位是**成品像素**，用前要乘 SUPERSAMPLE —— 修饰量作用在超采样后的画布坐标系里，
//    不乘就会出现"以为 12px、实际 6px"的偏差（实测 float 只剩 9px 位移时发现的）
// ⚠️ 必须放在 SUPERSAMPLE 之后：`const` 有暂时性死区，提前引用会 ReferenceError
const LAND_FRAMES = 37; // 1.54s：落下→压缩→阻尼回弹
const LAND_DROP_PX = 10 * SUPERSAMPLE; // 落地前的下落高度（成品像素）
const LAND_SQUASH = 0.16; // 最大压缩比例（纵向，无量纲，不用乘）
const FLOAT_FRAMES = 49; // 2.04s，正好一个 2 秒周期（可无缝循环）
const FLOAT_AMP_PX = 12 * SUPERSAMPLE; // 浮沉幅度（向上偏置，不会低于站姿）
const FLOAT_PERIOD_S = 2.0;

{
  const proto = spine.SkeletonRenderer.prototype;
  const orig = proto.drawTriangle;
  proto.drawTriangle = function (img, x0, y0, u0, v0, x1, y1, u1, v1, x2, y2, u2, v2) {
    const cx = (x0 + x1 + x2) / 3, cy = (y0 + y1 + y2) / 3;
    const push = (x, y) => {
      const dx = x - cx, dy = y - cy;
      const len = Math.hypot(dx, dy) || 1e-6;
      return [x + (dx / len) * EDGE_PX, y + (dy / len) * EDGE_PX];
    };
    const a = push(x0, y0), b = push(x1, y1), c = push(x2, y2);
    return orig.call(this, img, a[0], a[1], u0, v0, b[0], b[1], u1, v1, c[0], c[1], u2, v2);
  };
}

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
// 优先使用 canvas.js 算出的**共享画布**：它取"该服装所有动画所有帧"的并集，
// 所以任何动作都不会被画布裁掉，且同套服装所有片段同尺寸（站位/大小天然一致）。
// 实测旧做法（只用 setup pose 定尺寸）导致：s1/idle 46/145 帧触底、s1/walk 49/49 帧触左右。
const SHARED_PATH = path.join(__dirname, "out", OUTFIT, "canvas.json");
const SHARED = fs.existsSync(SHARED_PATH)
  ? JSON.parse(fs.readFileSync(SHARED_PATH, "utf8"))
  : null;
const scale = SHARED ? SHARED.scale : TARGET_H / size.y;
const cx = off.x + size.x / 2;
const CW = SHARED ? SHARED.CW * SUPERSAMPLE : Math.ceil(size.x * scale) + MARGIN * 2;
const CH = SHARED ? SHARED.CH * SUPERSAMPLE : Math.ceil(size.y * scale) + MARGIN * 2;
// setup 包围盒的"底部中心" ⇒ 画布内锚点
const sx = SHARED ? SHARED.sx * SUPERSAMPLE : CW / 2 - cx * scale;
const sy = SHARED ? SHARED.sy * SUPERSAMPLE : CH - MARGIN + off.y * scale;

console.log(`  ${OUTFIT} / ${ANIM}${DERIVE ? `  [派生: ${DERIVE}]` : ""}`);
console.log(`  setup 包围盒 ${size.x.toFixed(0)}x${size.y.toFixed(0)} 偏移(${off.x.toFixed(0)},${off.y.toFixed(0)})` +
            ` ⇒ 缩放 ${scale.toFixed(4)} | 画布 ${CW}x${CH}` +
            `${SHARED ? "（共享画布，取全部动画并集）" : "（setup pose 定尺寸 → 可能被裁，建议先跑 canvas.js）"}`);
console.log(`  动画时长 ${anim.duration.toFixed(3)}s @ ${FPS}fps`);

// ② 逐帧渲染。
// **多渲一帧**：循环动画在 t=时长 处的姿势等于 t=0，补上这一帧循环才真正闭合
// （实测 48 帧版 walk 的首尾剪影差 3.49，差一点就不算无缝；
//   补帧后首尾同图 ⇒ 天然无缝，也避免被播放器当成"循环跳"改用乒乓——那会变成倒着走）
const N = DERIVE
  ? (DERIVE === "land" ? LAND_FRAMES : FLOAT_FRAMES)
  : Math.max(2, Math.round(anim.duration * FPS)) + 1;
const NAME = DERIVE || ANIM;
const outDir = path.join(__dirname, "out", OUTFIT, NAME);
fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(outDir, { recursive: true });

/**
 * 派生动画的骨架级修饰：返回 { dy, sy }（画布坐标的位移、纵向缩放系数）。
 * 不新画素材，等同于动画师 K 根骨骼；幅度都压在 MARGIN 以内。
 */
function deriveModifier(i) {
  const t = i / FPS;
  if (DERIVE === "float") {
    // 向上偏置的浮沉：0 → 最高 → 0，正好一个周期 ⇒ 首尾同姿势、无缝循环
    const u = (t % FLOAT_PERIOD_S) / FLOAT_PERIOD_S;
    return { dy: FLOAT_AMP_PX * (1 - Math.cos(2 * Math.PI * u)) / 2, sy: 1 };
  }
  if (DERIVE === "land") {
    const T_IMPACT = 0.26;
    let dy = 0;
    if (t < T_IMPACT) {
      const u = t / T_IMPACT; // 0..1，加速下落
      dy = LAND_DROP_PX * (1 - u * u);
    }
    const dt = t - T_IMPACT;
    // 落地压缩 + 阻尼振荡回弹（经典 squash & recover）
    const squash = dt >= 0 ? LAND_SQUASH * Math.exp(-dt * 6.0) * Math.cos(dt * 20) : 0;
    return { dy, sy: 1 - squash };
  }
  return { dy: 0, sy: 1 };
}

const canvas = createCanvas(CW, CH);
const ctx = canvas.getContext("2d");
const stateData = new spine.AnimationStateData(data);
let minPx = Infinity, maxPx = 0;
for (let i = 0; i < N; i++) {
  ctx.clearRect(0, 0, CW, CH);
  const sk = new spine.Skeleton(data);
  sk.setToSetupPose();
  const mod = deriveModifier(i);
  sk.scaleX = scale;
  sk.scaleY = -scale * mod.sy;
  sk.x = sx;
  sk.y = sy + mod.dy;
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
    source: `Spine ${OUTFIT} / ${ANIM}${DERIVE ? ` (派生 ${DERIVE})` : ""}`,
    frame_count: N, fps: FPS, size: [CW, CH],
    spine_anim_duration_s: DERIVE ? N / FPS : anim.duration,
    spine_scale: scale, costume_source: OUTFIT,
    character_height_px: TARGET_H,
    supersample: SUPERSAMPLE,
    edge_expand_px: EDGE_PX,
    derived: DERIVE || null,
    derive_params: DERIVE === "land"
      ? { drop_px: LAND_DROP_PX, squash: LAND_SQUASH }
      : (DERIVE === "float" ? { amp_px: FLOAT_AMP_PX, period_s: FLOAT_PERIOD_S } : null),
  }, null, 2)
);
console.log(`  输出 ${N} 帧 → ${outDir} | 非透明像素 ${minPx}~${maxPx} | 超采样 ${SUPERSAMPLE}x`);
