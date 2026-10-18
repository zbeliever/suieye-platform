/**
 * 厂区空间构筑物 —— 厂房骨架、地坪标线、管廊、围墙。
 *
 * 这一层解决的是"观感廉价"的根因：
 *   之前设备虽然做得细致（法兰/护栏/铭牌/状态灯都有），但它们散落在一片
 *   150×150 的深色网格面上 —— 那是"展厅"的语汇，不是"车间"。
 *   真实选矿厂里，设备永远处在**柱网、吊车梁、操作平台、地坪分区**构成的空间中。
 *   加上这一层，同样的设备会立刻从"摆件"变成"产线上的机组"。
 *
 * 取舍（很重要）：
 *   摄像头是从东南上方俯视厂区的，如果给整个车间盖满实心屋面，设备全被挡死。
 *   所以这里只做**半开放厂房**：
 *     · 柱网 + 屋架 + 吊车梁：完整（它们只是杆件，几乎不遮挡）
 *     · 屋面：只铺靠北的一跨，且半透明（远侧形成"进深"，近侧保持通视）
 *   这也是工业数字孪生的通行做法 —— 建筑是"壳"，设备是"魂"。
 */
import * as THREE from 'three'
import {
  MAT, cluster, ladderGeos, hazardStripeMesh,
} from './equipDetails.js'

// ── 构筑物材质 ──────────────────────────────────────
export const SHED_MAT = {
  // 钢结构柱/梁：比设备深一档，作为"框架"后退
  // ⚠️ 不能亮：初版 0x6a7683 + 强方向光 → 柱子白得抢过设备，
  //    整张图变成"一片白栏杆里夹着几台设备"。压到中深灰才轮到设备当主角。
  steel: new THREE.MeshStandardMaterial({ color: 0x4f5a66, roughness: 0.66, metalness: 0.4 }),
  // 屋架/檩条：更细更暗
  truss: new THREE.MeshStandardMaterial({ color: 0x424c57, roughness: 0.7, metalness: 0.36 }),
  // 半透明屋面板（默认不用：见 buildWorkshopFrame 里 roofPanels 的说明）
  roofPanel: new THREE.MeshStandardMaterial({
    color: 0x9fb4c8, roughness: 0.5, metalness: 0.1,
    transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false,
  }),
  // 地坪标线（黄/白漆）—— 用 Basic 材质，不打光，颜色最准
  line: new THREE.MeshBasicMaterial({ color: 0xd8a83a, transparent: true, opacity: 0.85 }),
  lineWhite: new THREE.MeshBasicMaterial({ color: 0xcfd8e3, transparent: true, opacity: 0.5 }),
  // 排水沟盖板
  grate: new THREE.MeshStandardMaterial({ color: 0x4c5561, roughness: 0.8, metalness: 0.3 }),
  // 管廊管道
  pipeBlue: new THREE.MeshStandardMaterial({ color: 0x4d84b8, roughness: 0.55, metalness: 0.35 }),
  pipeGreen: new THREE.MeshStandardMaterial({ color: 0x4f9b76, roughness: 0.55, metalness: 0.35 }),
  pipeGrey: new THREE.MeshStandardMaterial({ color: 0x8e9aa6, roughness: 0.6, metalness: 0.4 }),
}

const U = { box: new THREE.BoxGeometry(1, 1, 1), cyl: new THREE.CylinderGeometry(1, 1, 1, 12) }

function box(sx, sy, sz, pos, rot) {
  const g = U.box.clone()
  g.scale(sx, sy, sz)
  if (rot) {
    g.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(
      new THREE.Euler(rot[0] || 0, rot[1] || 0, rot[2] || 0)))
  }
  if (pos) g.translate(pos[0] || 0, pos[1] || 0, pos[2] || 0)
  return g
}

function cyl(r, h, pos, rot) {
  const g = U.cyl.clone()
  g.scale(r, h, r)
  if (rot) {
    g.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(
      new THREE.Euler(rot[0] || 0, rot[1] || 0, rot[2] || 0)))
  }
  if (pos) g.translate(pos[0] || 0, pos[1] || 0, pos[2] || 0)
  return g
}

/**
 * 厂房骨架：柱网 + 屋架 + 吊车梁 + 天车 + 半透明屋面板（仅北侧一跨）
 *
 * @param {object} o
 *   xs: 柱列 X 坐标数组；zs: 柱行 Z 坐标数组
 *   h:  柱高；craneY: 吊车梁标高
 *   roofFromZ: 从哪一行柱开始铺屋面板（默认只铺最北一跨）
 */
export function buildWorkshopFrame(o = {}) {
  // ══ 柱网布局：只立"门式刚架"，柱子全在设备区外侧 ══
  // ★ 这一版是踩过坑改出来的：
  //   设备占据 x∈[-9.5, 10.5]、z∈[-5.5, 10]，而旧柱网 xs 取到 -1.5/4.5/10.5、
  //   zs 取到 5.5 —— 柱子直接插进设备之间，加上 6.4m 高的吊车梁横穿，
  //   俯视时整片设备被切成格子（老板原话："厂房骨架对设备有点遮挡"）。
  //   现在 xs 只保留 ±15 附近两列（设备范围之外），zs 只排到设备前沿之前，
  //   中间完全敞开 —— 就是工业数字孪生常见的剖视做法：结构作框架，设备当主角。
  const xs = o.xs || [-15.6, 13.4]
  const zs = o.zs || [-10.5, -3.0, 4.5]
  const h = o.h ?? 8.6
  const craneY = o.craneY ?? 6.8
  const roofFromZ = o.roofFromZ ?? -8.5
  const roofToZ = o.roofToZ ?? -1.5
  const withRoof = o.withRoof ?? false

  // ★ 跨距变量必须在最前面声明：
  //   吊车轨道（第③段）和屋架（第⑤段）都要用 x0/x1/span，
  //   如果留到第⑤段才 const 声明，第③段先引用就会踩暂时性死区
  //   （ReferenceError: Cannot access 'x0' before initialization），
  //   整个 initScene 抛异常 → 设备永远停在"正在加载"。
  const x0 = xs[0], x1 = xs[xs.length - 1]
  const span = x1 - x0

  const g = new THREE.Group()
  g.name = 'workshop-frame'

  // ① 柱（H 型钢的简化：腹板 + 两侧翼缘，比纯圆柱更像厂房柱）
  const cols = []
  for (const x of xs) {
    for (const z of zs) {
      // 腹板
      cols.push(box(0.1, h, 0.42, [x, h / 2, z]))
      // 翼缘
      cols.push(box(0.3, h, 0.06, [x, h / 2, z - 0.21]))
      cols.push(box(0.3, h, 0.06, [x, h / 2, z + 0.21]))
      // 柱脚底板
      cols.push(box(0.62, 0.08, 0.72, [x, 0.04, z]))
      // 柱脚加劲肋
      cols.push(box(0.34, 0.34, 0.06, [x, 0.2, z - 0.21]))
      cols.push(box(0.34, 0.34, 0.06, [x, 0.2, z + 0.21]))
    }
  }
  const colMesh = cluster(cols, SHED_MAT.steel, 'shed-columns')
  if (colMesh) g.add(colMesh)

  // ② 柱间支撑（横向 X 撑，只在两端开间，符合真实厂房做法）
  const braces = []
  const diag = (x0, z0, x1, y0, y1, z1) => {
    const dx = x1 - x0, dy = y1 - y0, dz = z1 - z0
    const len = Math.hypot(dx, dy, dz)
    const mid = [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2]
    return { len, mid, dx, dy, dz }
  }
  for (const x of [xs[0], xs[xs.length - 1]]) {
    for (let i = 0; i < zs.length - 1; i++) {
      const seg = diag(x, zs[i], x, h * 0.92, 0.1, zs[i + 1])
      const geo = U.cyl.clone()
      geo.scale(0.035, seg.len, 0.035)
      // 让圆柱对齐到两点连线
      const q = new THREE.Quaternion().setFromUnitVectors(
        new THREE.Vector3(0, 1, 0),
        new THREE.Vector3(seg.dx, seg.dy, seg.dz).normalize())
      geo.applyQuaternion(q)
      geo.translate(seg.mid[0], seg.mid[1], seg.mid[2])
      braces.push(geo)
    }
  }
  const braceMesh = cluster(braces, SHED_MAT.truss, 'shed-braces')
  if (braceMesh) g.add(braceMesh)

  // ③ 吊车梁（行车轨道）—— 沿 **Z** 铺在两侧柱列上
  //    ⚠️ 旧实现沿 X 在每个柱列各铺一道，等于每隔 7m 就有一条横梁吊在设备头顶，
  //       既不符合真实构造（行车轨道是两侧通长的），也是画面里最主要的遮挡。
  //       正确做法：轨道在 x0 / x1 两根柱列上通长布置，中间完全空出来。
  const craneBeams = []
  const zLen = zs[zs.length - 1] - zs[0]
  const zMid = (zs[0] + zs[zs.length - 1]) / 2
  for (const x of [x0, x1]) {
    craneBeams.push(box(0.32, 0.46, zLen + 0.8, [x, craneY, zMid]))
    // 牛腿（托住轨道的挑出块）
    for (const z of zs) {
      craneBeams.push(box(0.42, 0.34, 0.4, [x, craneY - 0.4, z]))
    }
  }
  const craneMesh = cluster(craneBeams, SHED_MAT.steel, 'shed-cranebeam')
  if (craneMesh) g.add(craneMesh)

  // ④ 钢轨（轨道顶面，同样沿 Z）
  const rails = []
  for (const x of [x0, x1]) {
    rails.push(box(0.1, 0.09, zLen + 0.8, [x, craneY + 0.27, zMid]))
  }
  const railMesh = cluster(rails, SHED_MAT.truss, 'shed-rails')
  if (railMesh) g.add(railMesh)

  // ⑤ 屋架（每道刚架顶上一榀桁架：上下弦 + 竖杆 + 拱形上弦）
  const trusses = []
  // 跨度从 24m 涨到 29m，桁架要相应加深，否则看起来像根细线撑着大屋顶
  const trussH = o.trussH ?? 1.55
  for (const z of zs) {
    // 下弦
    trusses.push(box(span, 0.1, 0.1, [(x0 + x1) / 2, h, z]))
    // 上弦（做成微拱：分段折线）
    const n = 8
    for (let i = 0; i < n; i++) {
      const xa = x0 + (span / n) * i
      const xb = x0 + (span / n) * (i + 1)
      const ya = h + trussH * Math.sin(Math.PI * (i / n))
      const yb = h + trussH * Math.sin(Math.PI * ((i + 1) / n))
      const dx = xb - xa, dy = yb - ya
      const len = Math.hypot(dx, dy)
      const geo = U.cyl.clone()
      geo.scale(0.045, len, 0.045)
      const q = new THREE.Quaternion().setFromUnitVectors(
        new THREE.Vector3(0, 1, 0), new THREE.Vector3(dx, dy, 0).normalize())
      geo.applyQuaternion(q)
      geo.translate((xa + xb) / 2, (ya + yb) / 2, z)
      trusses.push(geo)
    }
    // 竖杆
    for (let i = 1; i < n; i++) {
      const x = x0 + (span / n) * i
      const yTop = h + trussH * Math.sin(Math.PI * (i / n))
      trusses.push(box(0.05, yTop - h, 0.05, [x, (yTop + h) / 2, z]))
    }
  }
  const trussMesh = cluster(trusses, SHED_MAT.truss, 'shed-trusses')
  if (trussMesh) g.add(trussMesh)

  // ⑥ 檩条（承托屋面板的细梁）—— **只铺最后一跨**
  //    通长铺会在设备上方织出一张 10 道横梁的网，从俯视角看就是一片格栅，
  //    比柱子更烦人。只保留后一跨，既能看出"这是有屋面的厂房"，又不在正面成网。
  const purlins = []
  const nP = 9
  const pzFrom = zs[0], pzTo = zs[1]
  for (let i = 0; i <= nP; i++) {
    const x = x0 + (span / nP) * i
    const y = h + trussH * Math.sin(Math.PI * (i / nP)) + 0.1
    purlins.push(box(0.07, 0.09, Math.abs(pzTo - pzFrom), [x, y, (pzFrom + pzTo) / 2]))
  }
  const purlinMesh = cluster(purlins, SHED_MAT.truss, 'shed-purlins')
  if (purlinMesh) g.add(purlinMesh)

  // ⑦ 半透明屋面板（默认关闭）
  //   ★ 为什么不铺屋面：铺满会把设备全遮死；只铺一跨又会在画面上方
  //     留下一块孤零零的半透明矩形，像 UI 出错。所以默认只保留**檩条+屋架**
  //     （屋面结构本身就是最好的"这是厂房"信号），需要时用 withRoof 打开。
  if (withRoof) {
    const panels = []
    const pz0 = roofFromZ, pz1 = roofToZ
    for (let i = 0; i < nP; i++) {
      const xa = x0 + (span / nP) * i
      const xb = x0 + (span / nP) * (i + 1)
      const ya = h + trussH * Math.sin(Math.PI * (i / nP)) + 0.16
      const yb = h + trussH * Math.sin(Math.PI * ((i + 1) / nP)) + 0.16
      const dx = xb - xa, dy = yb - ya
      const len = Math.hypot(dx, dy)
      const geo = new THREE.PlaneGeometry(len, Math.abs(pz1 - pz0))
      geo.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(
        new THREE.Euler(-Math.PI / 2, 0, Math.atan2(dy, dx), 'XYZ')))
      geo.translate((xa + xb) / 2, (ya + yb) / 2, (pz0 + pz1) / 2)
      panels.push(geo)
    }
    const panelMesh = cluster(panels, SHED_MAT.roofPanel, 'shed-roof')
    if (panelMesh) { panelMesh.castShadow = false; g.add(panelMesh) }
  }

  // ⑧ 天车（小车 + 吊钩）—— 停在**最后一跨**（设备区后方），
  //    放在中间会正好吊在设备头顶，又是一处遮挡。
  const crane = new THREE.Group()
  crane.name = 'shed-crane'
  const cx = x0 + span * 0.34
  const cz = zs[0] + 1.4
  const trolley = cluster([
    // 桥架主梁：横跨两根轨道之间（不能只做一小段，否则像悬空的一块铁）
    box(span - 0.7, 0.44, 0.95, [x0 + span / 2, craneY + 0.5, cz]),
    box(1.15, 0.68, 1.15, [cx, craneY + 1.0, cz]),   // 小车
  ], SHED_MAT.steel, 'crane-body')
  if (trolley) crane.add(trolley)
  const cable = cluster([cyl(0.02, 2.0, [cx, craneY + 1.3 - 1.0, cz])], SHED_MAT.truss, 'crane-cable')
  if (cable) crane.add(cable)
  const hook = cluster([box(0.3, 0.26, 0.3, [cx, craneY - 0.15, cz])], SHED_MAT.steel, 'crane-hook')
  if (hook) crane.add(hook)
  g.add(crane)

  // ⑨ 柱爬梯（检修上屋面用，只在一根角柱上，避免重复堆细节）
  const ladder = cluster(ladderGeos(craneY, { rungs: 12, cage: false }), MAT.galv, 'shed-ladder')
  if (ladder) {
    ladder.position.set(xs[1], 0, zs[0] + 0.36)
    g.add(ladder)
  }

  return g
}

/**
 * 地坪标线：安全通道、设备分区框、排水沟。
 * 全部是贴地的薄面（y≈0.02），不参与阴影、不挡视线，但能把"旷野"变成"车间"。
 */
export function buildFloorMarkings(o = {}) {
  const g = new THREE.Group()
  g.name = 'floor-markings'
  g.position.y = 0.02

  const lane = (x, z, w, d) => {
    const geo = new THREE.PlaneGeometry(w, d)
    geo.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(-Math.PI / 2, 0, 0)))
    geo.translate(x, 0, z)
    return geo
  }

  // ① 两条主安全通道（黄色，宽 1.6m）：一条东西向，一条南北向
  const yellow = [
    lane(0, 9.6, 30, 0.14),        // 南侧通道上边线
    lane(0, 8.0, 30, 0.14),        // 南侧通道下边线
    lane(-11.4, 3.0, 0.14, 14),    // 西侧通道左边线
    lane(-9.8, 3.0, 0.14, 14),     // 西侧通道右边线
  ]
  const yMesh = cluster(yellow, SHED_MAT.line, 'floor-lane')
  if (yMesh) { yMesh.castShadow = false; g.add(yMesh) }

  // ② 设备分区框（白色细线，每个工艺段一个框，暗示"这些机器是一组"）
  const white = []
  const zone = (cx, cz, w, d) => {
    white.push(lane(cx, cz - d / 2, w, 0.08))
    white.push(lane(cx, cz + d / 2, w, 0.08))
    white.push(lane(cx - w / 2, cz, 0.08, d))
    white.push(lane(cx + w / 2, cz, 0.08, d))
  }
  zone(-8.2, -5.5, 18.0, 4.4)    // 磨矿 + 浮选（北排）
  zone(-3.0, 5.5, 15.0, 5.0)     // 浓密 + 皮带（南排）
  zone(6.5, 8.0, 12.0, 6.0)      // 水处理区
  const wMesh = cluster(white, SHED_MAT.lineWhite, 'floor-zone')
  if (wMesh) { wMesh.castShadow = false; g.add(wMesh) }

  // ③ 排水沟（带盖板的长条），沿南侧通道外侧
  const grate = []
  for (let i = 0; i < 26; i++) {
    grate.push(box(1.0, 0.06, 0.5, [-14 + i * 1.06, 0.03, 10.9]))
  }
  const grateMesh = cluster(grate, SHED_MAT.grate, 'floor-grate')
  if (grateMesh) { grateMesh.castShadow = false; g.add(grateMesh) }

  // ④ 危险区域警示条（浓密机周边）
  const stripe = hazardStripeMesh(6.0, 0.02, 0.18)
  if (stripe) {
    stripe.rotation.x = -Math.PI / 2
    stripe.position.set(-3.0, 0.02, 8.6)
    g.add(stripe)
  }

  return g
}

/**
 * 架空管廊：立柱 + 多层横梁 + 三根工艺管。
 * 位置沿西侧走，把药剂间/回水与主厂房在视觉上连起来。
 */
export function buildPipeRack(o = {}) {
  const len = o.len ?? 26
  const z = o.z ?? -11.5
  const h = o.h ?? 4.2
  const g = new THREE.Group()
  g.name = 'pipe-rack'

  // ① 立柱（每 4m 一根，H 型钢简化）
  const posts = []
  const n = Math.max(2, Math.round(len / 4))
  for (let i = 0; i <= n; i++) {
    const x = -len / 2 + (len / n) * i
    posts.push(box(0.12, h, 0.3, [x, h / 2, z]))
    posts.push(box(0.26, h, 0.05, [x, h / 2, z - 0.16]))
    posts.push(box(0.26, h, 0.05, [x, h / 2, z + 0.16]))
    posts.push(box(0.5, 0.07, 0.5, [x, 0.035, z]))
  }
  // ② 横梁两层
  for (let lv = 0; lv < 2; lv++) {
    const y = h * (0.55 + lv * 0.42)
    posts.push(box(len, 0.1, 0.12, [0, y, z - 0.55]))
    posts.push(box(len, 0.1, 0.12, [0, y, z + 0.55]))
  }
  const postMesh = cluster(posts, SHED_MAT.steel, 'rack-posts')
  if (postMesh) g.add(postMesh)

  // ③ 三层管道（蓝=矿浆 / 绿=水 / 灰=气）
  const pipes = []
  const pipeSpec = [
    { y: h * 0.55 + 0.24, z: z - 0.55, r: 0.16, mat: 'pipeBlue' },
    { y: h * 0.55 + 0.24, z: z + 0.55, r: 0.13, mat: 'pipeGreen' },
    { y: h * 0.97 + 0.2,  z: z - 0.25, r: 0.11, mat: 'pipeGrey' },
    { y: h * 0.97 + 0.2,  z: z + 0.25, r: 0.09, mat: 'pipeGrey' },
  ]
  for (const p of pipeSpec) {
    const geo = cyl(p.r, len, [0, p.y, p.z], [0, 0, Math.PI / 2])
    const m = cluster([geo], SHED_MAT[p.mat], 'rack-pipe')
    if (m) g.add(m)
  }
  // ④ 管道法兰节点（每 8m 一个，让长管有"接口"而不是一根光管）
  const flanges = []
  for (let i = 0; i <= n; i += 2) {
    const x = -len / 2 + (len / n) * i
    for (const p of pipeSpec) {
      flanges.push(cyl(p.r * 1.35, 0.12, [x, p.y, p.z], [0, 0, Math.PI / 2]))
    }
  }
  const flangeMesh = cluster(flanges, MAT.steel, 'rack-flanges')
  if (flangeMesh) g.add(flangeMesh)

  return g
}

/**
 * 厂区围墙 + 大门（只做南侧正立面一段，给画面一个"厂区边界"）
 */
export function buildPerimeter() {
  const g = new THREE.Group()
  g.name = 'perimeter'

  const zWall = -27.5   // 北侧远景：作为厂区背景，不进镜头近处挡视线
  const xFrom = -26, xTo = 26
  const wallH = 2.2

  // ① 围墙：墙板 + 压顶 + 每隔 4m 一根立柱
  const wallParts = []
  wallParts.push(box(xTo - xFrom, wallH, 0.22, [0, wallH / 2, zWall]))
  wallParts.push(box(xTo - xFrom, 0.14, 0.34, [0, wallH + 0.07, zWall]))
  for (let x = xFrom; x <= xTo; x += 4) {
    wallParts.push(box(0.34, wallH + 0.34, 0.38, [x, (wallH + 0.34) / 2, zWall]))
  }
  const wallMesh = cluster(wallParts, SHED_MAT.steel, 'perimeter-wall')
  if (wallMesh) g.add(wallMesh)

  // ② 大门（中间断开 + 两片推拉门 + 门柱）
  const gateW = 7.0
  const gate = []
  for (const sx of [-1, 1]) {
    gate.push(box(gateW / 2 - 0.1, 0.1, 0.1, [sx * (gateW / 4 + 0.2), 1.9, zWall]))
    gate.push(box(gateW / 2 - 0.1, 0.1, 0.1, [sx * (gateW / 4 + 0.2), 0.5, zWall]))
    for (let i = 0; i < 5; i++) {
      const x = sx * (gateW / 4 + 0.2) - (gateW / 2 - 0.1) / 2 + (gateW / 2 - 0.1) / 4 * i
      gate.push(box(0.06, 1.5, 0.06, [x, 1.2, zWall]))
    }
  }
  // 门柱（比围墙高，砖砌感）
  gate.push(box(0.8, 3.2, 0.8, [-gateW / 2 - 0.4, 1.6, zWall]))
  gate.push(box(0.8, 3.2, 0.8, [gateW / 2 + 0.4, 1.6, zWall]))
  const gateMesh = cluster(gate, SHED_MAT.steel, 'perimeter-gate')
  if (gateMesh) g.add(gateMesh)

  // ③ 门楣牌（企业标识位）
  const sign = new THREE.Mesh(new THREE.BoxGeometry(3.6, 0.7, 0.12),
    new THREE.MeshStandardMaterial({ color: 0x2a3441, emissive: 0x2f7bff, emissiveIntensity: 0.35 }))
  sign.position.set(0, 2.9, zWall)
  g.add(sign)

  return g
}
