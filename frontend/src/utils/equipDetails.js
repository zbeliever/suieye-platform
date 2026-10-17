/**
 * 选矿设备「工业细节构件库」。
 *
 * 为什么要有这个文件
 * ────────────────────────────────────────────────
 * 原来 7 台设备全是 5~35 个基础几何体硬拼（圆柱 + 盒子），零工业细节。
 * 人眼识别"这是台什么设备"靠的是**部件语义** —— 法兰、护栏、爬梯、铭牌、
 * 警示条纹、散热片。缺了这些，再多面数也只是"玩具积木"。
 *
 * 关键约束（性能）
 * ────────────────────────────────────────────────
 * 每个构件簇内部用 mergeGeometries 合批 → **1 个簇 = 1 个 draw call**。
 * 一台设备加 7 类细节只多出 5 个 draw call，不会重演"卡顿"。
 *
 * 用法
 * ────────────────────────────────────────────────
 *   const steel = []
 *   addGeos(steel, flangeGeos(0.3, { bolts: 12 }), P([2.5, 1.5, 0], [0, 0, Math.PI / 2]))
 *   group.add(cluster(steel, MAT.steel, 'detail-steel'))
 *
 * 约定
 * ────────────────────────────────────────────────
 * - 所有构件几何都在**局部坐标系**里生成，默认朝向 +Y；调用方用 P(pos, rot) 摆放。
 * - 需要独立材质/贴图的构件（铭牌、警示条）直接返回 Mesh，不走合批。
 * - 这些 Mesh 的 name 前缀 `detail-`，其中 galv / nameplate / hazard 三类
 *   已加入 Equipment3D.vue 的 SKIP_TINT —— 不参与健康度染色与配色归一，
 *   否则镀锌护栏会被染成和设备本体一个色，"层次"就没了。
 */
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'

// ── 材质基座（每次建簇会 clone，避免跨设备共享被 polishMaterials 越染越偏） ──
export const MAT = {
  steel: new THREE.MeshStandardMaterial({ color: 0x93a3b3, roughness: 0.5, metalness: 0.5 }),   // 法兰 / 筋板 / 地脚
  // 护栏/爬梯/平台：镀锌件。⚠️ 不能太亮+太金属 —— 场景用 RoomEnvironment 做环境光，
  // 高金属度会把护栏反成一片死白，反而比机身抢眼，"细节"变成"噪音"。
  galv: new THREE.MeshStandardMaterial({ color: 0x94a2b0, roughness: 0.62, metalness: 0.3 }),
  dark: new THREE.MeshStandardMaterial({ color: 0x424d59, roughness: 0.6, metalness: 0.42 }),   // 机电 / 箱体 / 刮板
}

// ── 单位几何（只建一次，全部靠 scale 复用） ──
const U = {
  box: new THREE.BoxGeometry(1, 1, 1),
  cyl: new THREE.CylinderGeometry(1, 1, 1, 18),
  cyl6: new THREE.CylinderGeometry(1, 1, 1, 6),
  ring: new THREE.TorusGeometry(1, 0.05, 8, 48),
}

/** 取一份单位几何的副本，按 尺寸 → 旋转 → 平移 施加变换 */
function g(unit, sx, sy, sz, pos, rot) {
  const o = unit.clone()
  o.scale(sx, sy, sz)
  if (rot) {
    o.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(
      new THREE.Euler(rot[0] || 0, rot[1] || 0, rot[2] || 0)))
  }
  if (pos) o.translate(pos[0] || 0, pos[1] || 0, pos[2] || 0)
  return o
}

/** 位置 + 欧拉角 → 摆放矩阵 */
export function P(pos, rot) {
  return new THREE.Matrix4().compose(
    new THREE.Vector3(pos ? pos[0] : 0, pos ? pos[1] : 0, pos ? pos[2] : 0),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(
      rot ? rot[0] : 0, rot ? rot[1] : 0, rot ? rot[2] : 0)),
    new THREE.Vector3(1, 1, 1)
  )
}

/** 把一组几何按摆放矩阵塞进目标数组（不传矩阵则原样塞入） */
export function addGeos(target, geos, matrix) {
  for (const it of geos) {
    if (matrix) {
      const c = it.clone()
      c.applyMatrix4(matrix)
      target.push(c)
    } else {
      target.push(it)
    }
  }
  return target
}

/** 合批成 1 个 Mesh；材质 clone 保证染色互不影响 */
export function cluster(geos, material, name) {
  if (!geos || !geos.length) return null
  const merged = mergeGeometries(geos, false)
  geos.forEach(x => x.dispose())
  const mesh = new THREE.Mesh(merged, material.clone())
  mesh.name = name
  mesh.castShadow = true
  return mesh
}

// ══════════════════════════════════════════════════
//  L1 通用结构件
// ══════════════════════════════════════════════════

/**
 * 法兰盘 + 螺栓圈（盘面法线 +Y，中心在原点）
 * @param r 盘半径
 */
export function flangeGeos(r, o = {}) {
  const t = o.t ?? 0.09
  const bolts = o.bolts ?? 8
  const ring = r * (o.boltRing ?? 0.74)
  const bd = o.boltD ?? Math.max(0.028, r * 0.085)
  const out = [g(U.cyl, r, t, r)]
  for (let i = 0; i < bolts; i++) {
    const a = (i / bolts) * Math.PI * 2
    out.push(g(U.cyl6, bd, t * 2.2, bd, [Math.cos(a) * ring, 0, Math.sin(a) * ring]))
  }
  return out
}

/** 一对配对法兰（中间留螺栓缝）—— 管线断点处用，是"这是可拆的"最直观的语义 */
export function flangePairGeos(r, o = {}) {
  const off = (o.t ?? 0.09) * 0.62
  return [
    ...flangeGeos(r, o).map(x => x.translate(0, off, 0)),
    ...flangeGeos(r, o).map(x => x.translate(0, -off, 0)),
  ]
}

/** 加强筋立板：沿 X 长 w，高 h，厚 t，y=0 为底 */
export function ribGeos(w, h, t = 0.05) {
  return [g(U.box, w, h, t, [0, h / 2, 0])]
}

/**
 * 环向加强箍 / 抱箍（圆环，法线 +Y）。
 * 筒体类设备绕自己轴摆放时传 rot=[0,0,Math.PI/2]；横向箍筒体传 rot=[Math.PI/2,0,0]。
 */
export function ringGeos(r) {
  return [g(U.ring, r, r, r)]
}

/**
 * 筒体外部衬板螺栓阵（螺旋排布）。
 * 生成在**筒体自身局部坐标**：轴为 +Y，横截面在 (x,z)。
 *
 * 为什么需要它：筒体不透明 + 无贴图时，一个光溜溜的圆柱自转**在画面上完全看不出来**，
 * 设备就成了"静止模型"。衬板螺栓是磨机筒体最标志性的外观特征，既是真细节，
 * 又给自转提供了可见参照物 —— 一物两用。
 */
export function shellBoltGeos(r, o = {}) {
  const rows = o.rows ?? 9
  const perRow = o.perRow ?? 7
  const len = o.len ?? 4.6
  const br = o.boltR ?? 0.085
  const bh = o.boltH ?? 0.05
  const twist = o.twist ?? 0.26            // 每排的螺旋错位（真机是螺旋排布，不是整圈对齐）
  const out = []
  for (let i = 0; i < rows; i++) {
    const a0 = (i / rows) * Math.PI * 2
    for (let j = 0; j < perRow; j++) {
      const t = perRow === 1 ? 0 : j / (perRow - 1)
      const a = a0 + t * twist
      out.push(g(U.cyl6, br, bh, br,
        [Math.cos(a) * r, -len / 2 + t * len, Math.sin(a) * r],
        [0, -a, -Math.PI / 2]))            // +Y 径向朝外：(0,1,0) → (cos a, 0, sin a)
    }
  }
  return out
}

/**
 * 护栏（沿 X 方向，长度 L，y=0 为站立面）
 * @param L 长度
 */
export function railingGeos(L, o = {}) {
  const h = o.h ?? 1.05
  const rr = o.railR ?? 0.03
  const n = o.posts ?? Math.max(2, Math.round(L / 1.1) + 1)
  const rotZ = [0, 0, Math.PI / 2]
  const out = []
  out.push(g(U.cyl, rr, L, rr, [0, h, 0], rotZ))               // 顶部扶手
  if (o.mid !== false) out.push(g(U.cyl, rr * 0.78, L, rr * 0.78, [0, h * 0.56, 0], rotZ))
  for (let i = 0; i < n; i++) {
    const x = -L / 2 + (i / (n - 1)) * L
    out.push(g(U.cyl, rr, h, rr, [x, h / 2, 0]))               // 立柱
  }
  if (o.toe !== false) out.push(g(U.box, L, 0.1, 0.014, [0, 0.05, 0]))  // 踢脚板
  return out
}

/** 直爬梯（沿 +Y，高度 H；cage = 带护笼） */
export function ladderGeos(H, o = {}) {
  const w = o.w ?? 0.5
  const rr = o.railR ?? 0.032
  const n = o.rungs ?? Math.max(3, Math.round(H / 0.34))
  const out = [
    g(U.cyl, rr, H, rr, [-w / 2, H / 2, 0]),
    g(U.cyl, rr, H, rr, [w / 2, H / 2, 0]),
  ]
  for (let i = 0; i < n; i++) {
    const y = 0.3 + (i / Math.max(1, n - 1)) * Math.max(0.1, H - 0.55)
    out.push(g(U.cyl, rr * 0.72, w, rr * 0.72, [0, y, 0], [0, 0, Math.PI / 2]))
  }
  if (o.cage) {
    // ⚠️ 护笼的"骨架"是**等径环 + 竖向条**。
    //    初版把环半径写成 `w * (0.95 + i * 0.16)` 逐级放大 → 远处读成一根弹簧/锥体，
    //    比不加还糟。真实护笼是三~四道等径 hoop，环之间用竖条连成笼。
    const cr = w * 1.18                 // 护笼半径（恒定）
    const y0 = H * 0.42
    const y1 = H * 0.99
    const hoops = o.hoops ?? 3
    for (let i = 0; i < hoops; i++) {
      const y = y0 + (i / Math.max(1, hoops - 1)) * (y1 - y0)
      out.push(g(U.ring, cr, cr, cr, [0, y, 0], [Math.PI / 2, 0, 0]))
    }
    // 竖条只布置在"背向爬梯"的半圈（爬梯正面要留出口）
    const bars = o.cageBars ?? 7
    for (let i = 0; i < bars; i++) {
      const a = Math.PI * 0.25 + (i / (bars - 1)) * Math.PI * 1.5
      out.push(g(U.cyl, 0.02, y1 - y0, 0.02,
        [Math.cos(a) * cr, (y0 + y1) / 2, Math.sin(a) * cr]))
    }
  }
  return out
}

/** 检修平台（w 沿 X，d 沿 Z，y=0 为台面，含周边边梁） */
export function platformGeos(w, d, o = {}) {
  const t = o.t ?? 0.055
  const e = 0.07
  return [
    g(U.box, w, t, d, [0, -t / 2, 0]),
    g(U.box, w, e, 0.05, [0, -t - e / 2, d / 2]),
    g(U.box, w, e, 0.05, [0, -t - e / 2, -d / 2]),
    g(U.box, 0.05, e, d, [w / 2, -t - e / 2, 0]),
    g(U.box, 0.05, e, d, [-w / 2, -t - e / 2, 0]),
  ]
}

/** 平台支柱（沿 Y，从 y0 到 y1） */
export function postGeos(y0, y1, r = 0.05) {
  return [g(U.cyl, r, y1 - y0, r, [0, (y0 + y1) / 2, 0])]
}

/** 单颗螺栓头（六角近似），沿 +Y，中心在 pos */
export function boltGeos(r, h, pos) {
  return [g(U.cyl6, r, h, r, pos)]
}

/** 通用方块（箱体 / 筋板 / 桥架 / 基础墩） */
export function blockGeos(sx, sy, sz, pos, rot) {
  return [g(U.box, sx, sy, sz, pos, rot)]
}

/** 通用圆柱（接管 / 轴 / 立柱），默认沿 +Y */
export function cylGeos(r, h, pos, rot) {
  return [g(U.cyl, r, h, r, pos, rot)]
}

/**
 * 两端点之间的圆杆（桁架斜撑 / 耙架拉杆 / 短管）。
 * 内部把方向向量解成「欧拉序 XYZ 下的 [0, φ, θ]」：R = RY(φ)·RZ(θ)，
 * 于是 R·(0,1,0) = (-sinθ·cosφ, cosθ, sinθ·sinφ)。
 * ⚠️ 不要手算角度再写死 —— 斜撑的角度算错一次就是"杆子从设备里斜插出来"，
 *    而且很难靠肉眼发现。用两端点描述，让函数去解。
 */
export function rodGeos(p0, p1, r = 0.04) {
  const a = new THREE.Vector3(p0[0], p0[1], p0[2])
  const b = new THREE.Vector3(p1[0], p1[1], p1[2])
  const dir = b.clone().sub(a)
  const len = dir.length()
  if (len < 1e-6) return []
  const th = Math.acos(Math.max(-1, Math.min(1, dir.y / len)))   // 与 +Y 的夹角
  const ph = Math.atan2(dir.z, -dir.x)                           // 绕 Y 的方位角
  const mid = a.clone().add(b).multiplyScalar(0.5)
  return [g(U.cyl, r, len, r, [mid.x, mid.y, mid.z], [0, ph, th])]
}

/** 一排地脚螺栓：沿 X 从 x0 到 x1 均布 n 颗，落在 (y, z) */
export function boltRowGeos(n, x0, x1, y, z, r = 0.055, h = 0.1) {
  const out = []
  for (let i = 0; i < n; i++) {
    const x = n === 1 ? (x0 + x1) / 2 : x0 + (i / (n - 1)) * (x1 - x0)
    out.push(...boltGeos(r, h, [x, y, z]))
  }
  return out
}

// ══════════════════════════════════════════════════
//  L2 机电件
// ══════════════════════════════════════════════════

/** 电机（散热片 + 端盖 + 接线盒），主轴沿 +Y */
export function motorGeos(r, len, o = {}) {
  const fins = o.fins ?? 14
  const out = [
    g(U.cyl, r, len, r),
    g(U.cyl, r * 1.06, 0.07, r * 1.06, [0, len / 2, 0]),   // 端盖
    g(U.cyl, r * 1.06, 0.07, r * 1.06, [0, -len / 2, 0]),
  ]
  for (let i = 0; i < fins; i++) {
    const a = (i / fins) * Math.PI * 2
    const rad = r * 1.09
    out.push(g(U.box, r * 0.2, len * 0.84, 0.022,
      [Math.cos(a) * rad, 0, Math.sin(a) * rad], [0, -a, 0]))
  }
  if (o.junction !== false) {
    out.push(g(U.box, r * 0.46, r * 0.36, r * 0.46, [r * 0.66, len * 0.12, 0]))
  }
  if (o.shaft !== false) out.push(g(U.cyl, r * 0.16, len * 0.34, r * 0.16, [0, -len * 0.62, 0]))
  return out
}

/** 阀门手轮（轮圈 + 辐条 + 轮毂），盘面法线 +Y */
export function handwheelGeos(r, o = {}) {
  const spokes = o.spokes ?? 4
  const out = [
    g(U.ring, r, r, r, null, [Math.PI / 2, 0, 0]),
    g(U.cyl, r * 0.16, 0.07, r * 0.16),
  ]
  for (let i = 0; i < spokes; i++) {
    const a = (i / spokes) * Math.PI * 2
    out.push(g(U.box, r * 0.92, 0.03, 0.035,
      [Math.cos(a) * r * 0.46, 0, Math.sin(a) * r * 0.46], [0, -a, 0]))
  }
  return out
}

/** 真齿形齿轮（轮缘 + 轮毂 + 逐齿），轴向 +Y */
export function gearGeos(r, teeth, width, o = {}) {
  const out = [g(U.cyl, r * 0.9, width, r * 0.9)]
  if (o.hub !== false) out.push(g(U.cyl, r * 0.3, width * 1.7, r * 0.3))
  const th = (Math.PI * 2) / teeth
  const tw = r * th * (o.toothFill ?? 0.5)
  for (let i = 0; i < teeth; i++) {
    const a = i * th
    out.push(g(U.box, r * 0.17, width * 0.9, tw,
      [Math.cos(a) * r, 0, Math.sin(a) * r], [0, -a, 0]))
  }
  return out
}

// ══════════════════════════════════════════════════
//  标识件（独立材质，不走合批）
// ══════════════════════════════════════════════════

/** 铭牌：Canvas 写名称 + 位号，返回独立 Mesh（name = detail-nameplate） */
export function nameplateMesh(text, w = 0.8, h = 0.24) {
  const cw = 512
  const ch = Math.max(64, Math.round(cw * (h / w)))
  const c = document.createElement('canvas')
  c.width = cw
  c.height = ch
  const x = c.getContext('2d')
  x.fillStyle = '#dde3ea'
  x.fillRect(0, 0, cw, ch)
  x.strokeStyle = '#8b95a1'
  x.lineWidth = 8
  x.strokeRect(4, 4, cw - 8, ch - 8)
  x.fillStyle = '#232b34'
  x.textAlign = 'center'
  x.textBaseline = 'middle'
  x.font = `500 ${Math.round(ch * 0.44)}px -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif`
  x.fillText(text, cw / 2, ch / 2 + 1)
  const tex = new THREE.CanvasTexture(c)
  tex.colorSpace = THREE.SRGBColorSpace
  tex.anisotropy = 4
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(w, h),
    new THREE.MeshStandardMaterial({
      map: tex, roughness: 0.74, metalness: 0.12, side: THREE.DoubleSide,
    })
  )
  mesh.name = 'detail-nameplate'
  mesh.raycast = () => {}
  return mesh
}

let _hazardTex = null
function hazardTexture() {
  if (_hazardTex) return _hazardTex
  const c = document.createElement('canvas')
  c.width = 128
  c.height = 64
  const x = c.getContext('2d')
  x.fillStyle = '#f2c53d'
  x.fillRect(0, 0, 128, 64)
  x.strokeStyle = '#242b33'
  x.lineWidth = 15
  x.lineCap = 'butt'
  for (let i = -64; i < 210; i += 34) {
    x.beginPath()
    x.moveTo(i, 64)
    x.lineTo(i + 44, 0)
    x.stroke()
  }
  const t = new THREE.CanvasTexture(c)
  t.colorSpace = THREE.SRGBColorSpace
  t.wrapS = THREE.RepeatWrapping
  t.wrapT = THREE.RepeatWrapping
  _hazardTex = t
  return t
}

/** 黄黑警示条纹（沿 X 的一条薄板），返回独立 Mesh（name = detail-hazard） */
export function hazardStripeMesh(w, th = 0.02, h = 0.14) {
  const tex = hazardTexture().clone()
  tex.needsUpdate = true
  tex.wrapS = THREE.RepeatWrapping
  tex.repeat.set(Math.max(1, w / 0.44), 1)
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(w, h, th),
    new THREE.MeshStandardMaterial({ map: tex, roughness: 0.8, metalness: 0.06 })
  )
  mesh.name = 'detail-hazard'
  mesh.raycast = () => {}
  return mesh
}
