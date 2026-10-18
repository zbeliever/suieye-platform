/**
 * 厂区功能载体建筑 —— 把"流程外的平台功能"在 3D 里落成一栋栋真实的房子。
 *
 * 为什么要有这个文件：
 *   系统里有 14 个功能页，但只有 4 个能挂在工艺段上（磨矿/浮选/精矿/尾矿回水）。
 *   剩下的告警中心、报表、时光机、配药、设置、化验工作台如果只是"左侧一列菜单"，
 *   它们和车间就没有任何空间关系 —— 这正是"左侧栏看着像悬浮的菜单"的根因。
 *   行业做法（火法炼铜/化工厂数字孪生）是：配电室、水站、中控楼这些**建筑本身就是入口**。
 *   所以这里的每栋房子都对应左栏"公辅与中控"里的一个条目，两边进的是同一个门。
 *
 * 建模原则（与设备建模保持一致）：
 *   · 只用基础几何体拼装，不引入外部模型（体积与加载时间可控）
 *   · 重复小件（螺栓/护栏/百叶）用 cluster() 合批成 1 个 draw call
 *   · 建筑是"背景层"，不能抢设备的风头：墙面用低饱和冷灰，只有大屏/警示条允许发光
 *   · 每栋都落到混凝土地台上，并带铭牌 —— 与设备同一套"站在地上"的视觉语言
 */
import * as THREE from 'three'
import {
  MAT, cluster,
  railingGeos, ladderGeos, nameplateMesh, hazardStripeMesh,
} from './equipDetails.js'

// ── 建筑专用材质 ────────────────────────────────────
// ⚠️ 墙面刻意比设备更"素"：设备是主体，建筑是承载它们的背景。
//    如果墙面也用金属高光，整片厂区会一起反光，反而看不出哪台机器在跑。
export const BLD_MAT = {
  // 混凝土基座 / 地坪
  concrete: new THREE.MeshStandardMaterial({ color: 0x8f98a3, roughness: 0.94, metalness: 0.02 }),
  // 外墙（冷灰，微磨砂）
  // ⚠️ 不能太亮：场景底色是深蓝，纯白墙会像"纸盒子"浮在画面上，
  //    而且会把设备（真正的主体）压成配景。这里用中明度冷灰，靠明暗关系后退。
  wall: new THREE.MeshStandardMaterial({ color: 0xb3bcc7, roughness: 0.9, metalness: 0.03 }),
  // 外墙裙（深一档，做踢脚线/勒脚，压住建筑底边）
  skirt: new THREE.MeshStandardMaterial({ color: 0x828c98, roughness: 0.92, metalness: 0.03 }),
  // 屋面 / 女儿墙压顶
  roof: new THREE.MeshStandardMaterial({ color: 0x6d7885, roughness: 0.82, metalness: 0.14 }),
  // 玻璃幕墙：中控室要能看见里面的屏幕墙
  glass: new THREE.MeshStandardMaterial({
    color: 0x6f9fc8, roughness: 0.1, metalness: 0.12,
    transparent: true, opacity: 0.26, side: THREE.DoubleSide,
  }),
  // 窗洞（深色，比玻璃更"实"，用于小窗）
  window: new THREE.MeshStandardMaterial({ color: 0x39485a, roughness: 0.3, metalness: 0.4 }),
  // 发光屏（告警墙/大屏）—— 允许被 bloom 拾取
  screen: new THREE.MeshStandardMaterial({
    color: 0x16304f, emissive: 0x2f7bff, emissiveIntensity: 0.85, roughness: 0.4,
  }),
  screenWarm: new THREE.MeshStandardMaterial({
    color: 0x2e2517, emissive: 0xff9b2f, emissiveIntensity: 0.5, roughness: 0.4,
  }),
  // 百叶 / 通风栅
  louver: new THREE.MeshStandardMaterial({ color: 0x6b7683, roughness: 0.72, metalness: 0.28 }),
  // 门（深色金属）
  door: new THREE.MeshStandardMaterial({ color: 0x47525e, roughness: 0.58, metalness: 0.4 }),
}

/** 单位几何（只创建一次，靠 scale 复用） */
const U = {
  box: new THREE.BoxGeometry(1, 1, 1),
  cyl: new THREE.CylinderGeometry(1, 1, 1, 16),
}

/** 取单位几何副本并按 尺寸→位置 变换 */
function box(sx, sy, sz, pos) {
  const g = U.box.clone()
  g.scale(sx, sy, sz)
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
 * 建筑通用骨架：地台 + 勒脚 + 墙体 + 屋面 + 女儿墙 + 铭牌 + 雨棚台阶
 *
 * @param frontWallRatio 前墙高度占建筑高度的比例。
 *   默认 1（整面实墙）。中控室传 0.34 —— 它上半层是玻璃幕墙，
 *   前墙只需要做到一层窗台高度。
 *   ★ 早期版本把这个 0.34 写死成全局默认，导致化验室/药剂间/配电室
 *     全部"前面敞开"、看起来像四个敞口盒子（截图上就是四个白壳子）。
 */
function buildShell(group, { w, d, h, frontWallRatio = 1 }) {
  const t = 0.16                 // 墙厚
  const plinthH = 0.32           // 混凝土台高
  const roofT = 0.28

  // ① 混凝土地台（比建筑外扩 0.6m，像真的设备基础）
  const plinth = cluster([box(w + 1.2, plinthH, d + 1.2, [0, plinthH / 2, 0])],
    BLD_MAT.concrete, 'bld-plinth')
  if (plinth) group.add(plinth)

  const y0 = plinthH             // 墙体起点

  // ② 四面墙。用 4 块薄板而不是一个实心块
  const walls = []
  const fh = h * frontWallRatio
  walls.push(box(w, h, t, [0, y0 + h / 2, -d / 2 + t / 2]))          // 后墙
  walls.push(box(t, h, d, [-w / 2 + t / 2, y0 + h / 2, 0]))          // 左墙
  walls.push(box(t, h, d, [w / 2 - t / 2, y0 + h / 2, 0]))           // 右墙
  if (fh > 0.01) {
    walls.push(box(w, fh, t, [0, y0 + fh / 2, d / 2 - t / 2]))       // 前墙（可只做下半截）
  }
  const wallMesh = cluster(walls, BLD_MAT.wall, 'bld-wall')
  if (wallMesh) group.add(wallMesh)

  // ③ 勒脚（深一档，压住底边）
  const skirtMesh = cluster([box(w + 0.1, 0.42, d + 0.1, [0, y0 + 0.21, 0])],
    BLD_MAT.skirt, 'bld-skirt')
  if (skirtMesh) group.add(skirtMesh)

  // ④ 屋面板（外扩 0.35 做挑檐）
  const roofMesh = cluster([box(w + 0.7, roofT, d + 0.7, [0, y0 + h + roofT / 2, 0])],
    BLD_MAT.roof, 'bld-roof')
  if (roofMesh) group.add(roofMesh)

  // ⑤ 女儿墙（屋面四周 0.5 高矮墙，屋顶才不会像一块飘着的板）
  const parapet = []
  const pw = w + 0.7, pd = d + 0.7, ph = 0.5, pt = 0.12
  const py = y0 + h + roofT + ph / 2
  parapet.push(box(pw, ph, pt, [0, py, -pd / 2 + pt / 2]))
  parapet.push(box(pw, ph, pt, [0, py, pd / 2 - pt / 2]))
  parapet.push(box(pt, ph, pd, [-pw / 2 + pt / 2, py, 0]))
  parapet.push(box(pt, ph, pd, [pw / 2 - pt / 2, py, 0]))
  const parapetMesh = cluster(parapet, BLD_MAT.roof, 'bld-parapet')
  if (parapetMesh) group.add(parapetMesh)

  // ⑥ 门前雨棚 + 三级台阶（建筑"有入口"的视觉线索）
  const canopyY = y0 + 2.5
  const canopy = cluster([
    box(2.4, 0.12, 1.3, [0, canopyY, d / 2 + 0.55]),
    box(0.1, 2.5, 0.1, [-1.05, y0 + 1.25, d / 2 + 1.1]),
    box(0.1, 2.5, 0.1, [1.05, y0 + 1.25, d / 2 + 1.1]),
  ], BLD_MAT.roof, 'bld-canopy')
  if (canopy) group.add(canopy)

  const steps = []
  for (let i = 0; i < 3; i++) {
    steps.push(box(2.2, 0.11, 0.34, [0, plinthH - 0.055 - i * 0.11, d / 2 + 0.30 + i * 0.34]))
  }
  const stepMesh = cluster(steps, BLD_MAT.concrete, 'bld-steps')
  if (stepMesh) group.add(stepMesh)

  // ⑦ 门（贴在墙体外表面，不能嵌进墙里 —— 嵌进去就等于没做）
  const doorMesh = cluster([box(1.1, 2.1, 0.08, [0, y0 + 1.05, d / 2 + 0.03])],
    BLD_MAT.door, 'bld-door')
  if (doorMesh) group.add(doorMesh)

  return { plinthH, y0, inner: y0 + h * 0.5 }
}

/** 给建筑挂铭牌（与设备同一套铭牌，视觉统一） */
function addPlate(group, text, y, w = 1.5, d = 0.2) {
  const plate = nameplateMesh(text, w, 0.3)
  if (!plate) return
  plate.position.set(0, y, d)
  group.add(plate)
}

/**
 * 中控室（两层，上层玻璃幕墙，内部可见大屏墙）
 * 对应功能：生产总览 / 告警中心 / 报表统计 / 工况时光机
 */
export function buildControlRoom(opt = {}) {
  const w = opt.w || 9.2, d = opt.d || 7.0, h = opt.h || 5.6
  const g = new THREE.Group()
  g.name = 'facility-control_room'
  // 上半层是玻璃幕墙 → 前墙只砌到一层窗台
  buildShell(g, { w, d, h, frontWallRatio: 0.34 })

  const y0 = 0.32
  const upperY = y0 + h * 0.52          // 二层地面高度
  const upperH = h * 0.46

  // ① 二层玻璃幕墙（前 + 左右三面）—— 故意让内部可见
  //    ⚠️ 必须贴在墙**外表面**（d/2 + 0.02）：
  //      早期写成 d/2 - 0.09 落进了 0.16 厚的墙体内部，玻璃整个被墙吞掉，
  //      中控室看起来就是个普通白盒子，"能看见大屏"的核心设计完全没体现。
  const glass = []
  glass.push(box(w - 0.1, upperH, 0.06, [0, upperY + upperH / 2, d / 2 + 0.02]))
  glass.push(box(0.06, upperH, d - 0.2, [-w / 2 - 0.02, upperY + upperH / 2, 0]))
  glass.push(box(0.06, upperH, d - 0.2, [w / 2 + 0.02, upperY + upperH / 2, 0]))
  const glassMesh = cluster(glass, BLD_MAT.glass, 'cr-glass')
  if (glassMesh) { glassMesh.castShadow = false; g.add(glassMesh) }

  // ② 幕墙竖挺（把整片玻璃切成开间，否则像一块塑料膜）
  const mullions = []
  for (let i = 1; i < 6; i++) {
    const x = -w / 2 + (w / 6) * i
    mullions.push(box(0.07, upperH, 0.1, [x, upperY + upperH / 2, d / 2 + 0.06]))
  }
  mullions.push(box(w - 0.1, 0.09, 0.1, [0, upperY + upperH - 0.05, d / 2 + 0.06]))
  const mulMesh = cluster(mullions, BLD_MAT.roof, 'cr-mullions')
  if (mulMesh) g.add(mulMesh)

  // ③ 内部大屏墙（发光）—— 二层靠后墙，透过玻璃看得见，这是"中控室"的核心识别符
  const screens = []
  screens.push(box(w * 0.62, 1.5, 0.1, [0, upperY + 1.45, -d / 2 + 0.45]))
  const screenMesh = cluster(screens, BLD_MAT.screen, 'cr-screen')
  if (screenMesh) { screenMesh.castShadow = false; g.add(screenMesh) }

  // ④ 屏幕墙的边框 + 分割缝
  const bezel = []
  const bw = w * 0.62, bh = 1.5, by = upperY + 1.45
  bezel.push(box(bw + 0.12, 0.07, 0.06, [0, by + bh / 2 + 0.04, -d / 2 + 0.40]))
  bezel.push(box(bw + 0.12, 0.07, 0.06, [0, by - bh / 2 - 0.04, -d / 2 + 0.40]))
  for (let i = 1; i < 4; i++) {
    bezel.push(box(0.05, bh, 0.06, [-bw / 2 + (bw / 4) * i, by, -d / 2 + 0.40]))
  }
  const bezelMesh = cluster(bezel, BLD_MAT.louver, 'cr-bezel')
  if (bezelMesh) g.add(bezelMesh)

  // ⑤ 二层操作工位（长条桌 + 椅子的极简体块）—— 让屏幕墙前面"有人干活"
  const desks = []
  for (let i = -1; i <= 1; i++) {
    const x = i * 1.9
    desks.push(box(1.5, 0.07, 0.62, [x, upperY + 0.72, -d / 2 + 1.75]))
    desks.push(box(0.06, 0.7, 0.5, [x - 0.65, upperY + 0.36, -d / 2 + 1.75]))
    desks.push(box(0.06, 0.7, 0.5, [x + 0.65, upperY + 0.36, -d / 2 + 1.75]))
    // 椅背
    desks.push(box(0.44, 0.5, 0.06, [x, upperY + 0.45, -d / 2 + 2.35]))
  }
  const deskMesh = cluster(desks, BLD_MAT.door, 'cr-desks')
  if (deskMesh) g.add(deskMesh)

  // ⑥ 屋面设备：3 台空调外机 + 1 根避雷针
  const rooftop = []
  for (let i = 0; i < 3; i++) {
    const x = -2.4 + i * 2.2
    rooftop.push(box(1.0, 0.72, 0.7, [x, y0 + h + 0.28 + 0.36, -d / 2 + 1.6]))
  }
  const ahuMesh = cluster(rooftop, BLD_MAT.louver, 'cr-ahu')
  if (ahuMesh) g.add(ahuMesh)
  const mast = cluster([cyl(0.045, 3.2, [w / 2 - 0.9, y0 + h + 0.28 + 1.6, -d / 2 + 1.2])],
    MAT.steel, 'cr-mast')
  if (mast) g.add(mast)

  // ⑦ 二层外挑走廊 + 护栏（工业建筑常见的检修通廊）
  const walk = cluster([box(w + 0.5, 0.1, 0.9, [0, upperY - 0.05, d / 2 + 0.45])],
    BLD_MAT.roof, 'cr-walkway')
  if (walk) g.add(walk)
  // ⚠️ railingGeos 只沿 X 方向、以 y=0/z=0 为基准生成，没有 y/z 选项 ——
  //    必须由调用方把合批后的 mesh 摆到位，否则护栏会躺在建筑内部的地面上。
  const railMesh = cluster(railingGeos(w + 0.5), MAT.galv, 'cr-rail')
  if (railMesh) {
    railMesh.position.set(0, upperY, d / 2 + 0.88)
    g.add(railMesh)
  }

  // ⑧ 铭牌（挂在前墙上方）
  addPlate(g, '中控楼 · 中控室', y0 + h * 0.52 - 0.55, 1.9, d / 2 + 0.1)

  g.userData.facilityId = 'control_room'
  return g
}

/**
 * 化验室（单层，带样品窗与屋面排风）
 * 对应功能：浮选工作台 / 安全工作台
 */
export function buildLab(opt = {}) {
  const w = opt.w || 6.2, d = opt.d || 6.0, h = opt.h || 4.2
  const g = new THREE.Group()
  g.name = 'facility-lab'
  buildShell(g, { w, d, h })
  const y0 = 0.32

  // ① 前墙一排小窗（化验证/实验区）—— 贴在墙外表面才会显出来
  const wins = []
  for (let i = -1; i <= 1; i++) {
    wins.push(box(1.3, 1.1, 0.07, [i * 1.75, y0 + 1.95, d / 2 + 0.03]))
  }
  // 侧墙两扇
  wins.push(box(0.07, 1.0, 1.4, [w / 2 + 0.03, y0 + 1.95, -1.3]))
  const winMesh = cluster(wins, BLD_MAT.window, 'lab-windows')
  if (winMesh) g.add(winMesh)

  // ② 窗台线（把窗和墙分开，避免"贴了块深色板"）
  const sills = []
  for (let i = -1; i <= 1; i++) {
    sills.push(box(1.45, 0.08, 0.14, [i * 1.75, y0 + 1.36, d / 2 + 0.06]))
  }
  const sillMesh = cluster(sills, BLD_MAT.skirt, 'lab-sills')
  if (sillMesh) g.add(sillMesh)

  // ③ 屋面排风筒 + 通风帽
  const vent = cluster([
    cyl(0.22, 1.0, [-1.5, y0 + h + 0.28 + 0.5, -1.2]),
    cyl(0.34, 0.16, [-1.5, y0 + h + 0.28 + 1.05, -1.2]),
    cyl(0.18, 0.9, [1.4, y0 + h + 0.28 + 0.45, -1.0]),
    cyl(0.30, 0.14, [1.4, y0 + h + 0.28 + 0.94, -1.0]),
  ], BLD_MAT.louver, 'lab-vents')
  if (vent) g.add(vent)

  // ④ 室外取样台（一块台面 + 两个样品桶），强化"这是化验的地方"
  const bench = cluster([
    box(1.6, 0.08, 0.7, [w / 2 + 0.9, y0 + 0.85, 0.4]),
    box(0.08, 0.85, 0.08, [w / 2 + 0.2, y0 + 0.42, 0.1]),
    box(0.08, 0.85, 0.08, [w / 2 + 1.6, y0 + 0.42, 0.1]),
    box(0.08, 0.85, 0.08, [w / 2 + 0.2, y0 + 0.42, 0.7]),
    box(0.08, 0.85, 0.08, [w / 2 + 1.6, y0 + 0.42, 0.7]),
    cyl(0.19, 0.42, [w / 2 + 1.35, y0 + 1.06, 0.4]),
    cyl(0.19, 0.42, [w / 2 + 0.62, y0 + 1.06, 0.4]),
  ], BLD_MAT.louver, 'lab-bench')
  if (bench) g.add(bench)

  addPlate(g, '化验室', y0 + 3.1, 1.5, d / 2 + 0.1)
  g.userData.facilityId = 'lab'
  return g
}

/**
 * 药剂间（单层 + 室外药剂桶组 + 通往浮选厂的加药管）
 * 对应功能：配药助手
 */
export function buildDosingRoom(opt = {}) {
  const w = opt.w || 5.6, d = opt.d || 7.0, h = opt.h || 4.4
  const g = new THREE.Group()
  g.name = 'facility-dosing_room'
  buildShell(g, { w, d, h })
  const y0 = 0.32

  // ① 外墙百叶（药剂间必须通风）—— 直接按"贴左墙、沿 Z 展开"的朝向建模，
  //    不要先绕原点旋转再摆位：那样旋转中心错位，百叶会飞出去。
  const louvers = []
  for (let row = 0; row < 3; row++) {
    for (let i = 0; i < 5; i++) {
      louvers.push(box(0.07, 0.09, 1.9, [-w / 2 + 0.10, y0 + 1.3 + row * 0.22, -1.4 + i * 0.42]))
    }
  }
  const louverMesh = cluster(louvers, BLD_MAT.louver, 'dr-louvers')
  if (louverMesh) g.add(louverMesh)

  // ② 室外药剂桶 ×3（PAM / 硫酸铜 / 丁黄药，实际厂里就是这种立式储罐）
  const tanks = new THREE.Group()
  tanks.name = 'dr-tanks'
  const tankColors = [0x6f8fa8, 0x8aa87a, 0xa8927a]
  tankColors.forEach((c, i) => {
    const r = 0.52, th = 1.5
    const body = new THREE.Mesh(
      new THREE.CylinderGeometry(r, r, th, 20),
      new THREE.MeshStandardMaterial({ color: c, roughness: 0.55, metalness: 0.35 })
    )
    body.position.set(-w / 2 - 1.5, y0 + 0.35 + th / 2, -2.0 + i * 1.5)
    body.castShadow = true
    tanks.add(body)
    // 桶顶锥盖
    const cap = new THREE.Mesh(
      new THREE.CylinderGeometry(r * 0.35, r, 0.32, 20),
      new THREE.MeshStandardMaterial({ color: 0x9aa6b2, roughness: 0.5, metalness: 0.45 })
    )
    cap.position.set(body.position.x, y0 + 0.35 + th + 0.16, body.position.z)
    cap.castShadow = true
    tanks.add(cap)
    // 支腿
    const legs = cluster([
      cyl(0.05, 0.35, [-r * 0.62, y0 + 0.17, -r * 0.62]),
      cyl(0.05, 0.35, [r * 0.62, y0 + 0.17, -r * 0.62]),
      cyl(0.05, 0.35, [-r * 0.62, y0 + 0.17, r * 0.62]),
      cyl(0.05, 0.35, [r * 0.62, y0 + 0.17, r * 0.62]),
    ], MAT.steel, 'dr-tanklegs')
    if (legs) { legs.position.set(body.position.x, 0, body.position.z); tanks.add(legs) }
  })
  g.add(tanks)

  // ③ 加药管：从药剂间伸向浮选厂方向（+z 侧），半架空
  const pipeGeos = []
  for (let i = 0; i < 3; i++) {
    pipeGeos.push(cyl(0.07, 4.6, [w / 2 + 0.2, y0 + 1.15 + i * 0.26, 1.6],
      [Math.PI / 2, 0, 0]))
  }
  const pipeMesh = cluster(pipeGeos, MAT.galv, 'dr-pipes')
  if (pipeMesh) g.add(pipeMesh)

  // ④ 管道支吊架
  const brackets = []
  for (let i = 0; i < 3; i++) {
    brackets.push(box(0.08, 0.9, 0.08, [w / 2 + 0.2, y0 + 0.65, 0.2 + i * 1.6]))
  }
  const brMesh = cluster(brackets, MAT.steel, 'dr-brackets')
  if (brMesh) g.add(brMesh)

  // ⑤ 警示条（化学品区域）
  const stripe = hazardStripeMesh(3.0, 0.02, 0.14)
  if (stripe) {
    stripe.position.set(0, y0 + 0.62, d / 2 + 0.02)
    g.add(stripe)
  }

  addPlate(g, '药剂间', y0 + 3.2, 1.3, d / 2 + 0.1)
  g.userData.facilityId = 'dosing_room'
  return g
}

/**
 * 配电室（单层，外侧开关柜 + 变压器 + 高压警示）
 * 对应功能：系统设置
 */
export function buildPowerRoom(opt = {}) {
  const w = opt.w || 5.6, d = opt.d || 7.0, h = opt.h || 4.6
  const g = new THREE.Group()
  g.name = 'facility-power_room'
  buildShell(g, { w, d, h })
  const y0 = 0.32

  // ① 前墙高压百叶（3 组）—— 同样贴外表面
  const vents = []
  for (let i = 0; i < 3; i++) {
    for (let r = 0; r < 4; r++) {
      vents.push(box(1.2, 0.08, 0.07, [-1.7 + i * 1.7, y0 + 1.5 + r * 0.2, d / 2 + 0.03]))
    }
  }
  const ventMesh = cluster(vents, BLD_MAT.louver, 'pr-vents')
  if (ventMesh) g.add(ventMesh)

  // ② 室外落地式开关柜 ×3（配电室最标志性的东西）
  const cabinets = []
  for (let i = 0; i < 3; i++) {
    const x = -w / 2 - 1.2 - 0, z = -1.8 + i * 1.6
    cabinets.push(box(0.85, 1.9, 1.25, [x, y0 + 0.95, z]))
  }
  const cabMesh = cluster(cabinets, BLD_MAT.door, 'pr-cabinets')
  if (cabMesh) g.add(cabMesh)
  // 柜门缝 + 仪表灯
  const cabDetail = []
  for (let i = 0; i < 3; i++) {
    const x = -w / 2 - 1.2, z = -1.8 + i * 1.6
    cabDetail.push(box(0.03, 1.7, 0.02, [x + 0.44, y0 + 0.95, z]))
    cabDetail.push(box(0.03, 0.1, 0.16, [x + 0.45, y0 + 1.62, z]))
  }
  const cabD = cluster(cabDetail, BLD_MAT.screen, 'pr-cablamps')
  if (cabD) { cabD.castShadow = false; g.add(cabD) }

  // ③ 干式变压器（带散热片）+ 护栏
  const xf = new THREE.Group()
  xf.name = 'pr-transformer'
  const core = new THREE.Mesh(new THREE.BoxGeometry(1.15, 1.35, 1.9),
    new THREE.MeshStandardMaterial({ color: 0x6f7b88, roughness: 0.6, metalness: 0.4 }))
  core.position.set(w / 2 + 1.6, y0 + 0.68, 1.4)
  core.castShadow = true
  xf.add(core)
  const fins = []
  for (let i = 0; i < 6; i++) {
    fins.push(box(0.06, 1.1, 0.5, [w / 2 + 1.0 + i * 0.22, y0 + 0.68, 1.4]))
  }
  const finMesh = cluster(fins, MAT.steel, 'pr-fins')
  if (finMesh) xf.add(finMesh)
  // 高压套管
  const bush = []
  for (let i = -1; i <= 1; i++) {
    bush.push(cyl(0.09, 0.55, [w / 2 + 1.6 + i * 0.35, y0 + 1.62, 1.4]))
  }
  const bushMesh = cluster(bush, BLD_MAT.skirt, 'pr-bushings')
  if (bushMesh) xf.add(bushMesh)
  // 围栏（同样由调用方摆位：railingGeos 只在原点沿 X 生成）
  const xfRail = cluster(railingGeos(2.6), MAT.galv, 'pr-xf-rail')
  if (xfRail) { xfRail.position.set(w / 2 + 1.6, y0, 2.55); xf.add(xfRail) }
  g.add(xf)

  // ④ 高压危险警示条
  const stripe = hazardStripeMesh(2.6, 0.02, 0.14)
  if (stripe) {
    stripe.position.set(0, y0 + 0.7, d / 2 + 0.02)
    g.add(stripe)
  }

  addPlate(g, '配电室', y0 + 3.3, 1.3, d / 2 + 0.1)
  g.userData.facilityId = 'power_room'
  return g
}

/**
 * 监控立杆（杆 + 双摄像头 + 探照灯）
 * 对应功能：直播墙
 */
export function buildCctvPole(opt = {}) {
  const h = opt.h || 8.4
  const g = new THREE.Group()
  g.name = 'facility-cctv'

  // ① 混凝土基墩
  const base = cluster([box(1.1, 0.45, 1.1, [0, 0.225, 0])], BLD_MAT.concrete, 'cctv-base')
  if (base) g.add(base)

  // ② 立杆（下粗上细）
  const pole = new THREE.Mesh(
    new THREE.CylinderGeometry(0.09, 0.14, h, 14),
    new THREE.MeshStandardMaterial({ color: 0x8e9aa8, roughness: 0.55, metalness: 0.45 })
  )
  pole.position.y = 0.45 + h / 2
  pole.castShadow = true
  g.add(pole)

  // ③ 横臂 + 两台摄像机（一近一远，像真的云台）
  const armY = 0.45 + h - 0.55
  const arm = cluster([cyl(0.055, 1.5, [0.62, armY, 0], [0, 0, Math.PI / 2])],
    MAT.steel, 'cctv-arm')
  if (arm) g.add(arm)

  const cams = []
  const camMat = new THREE.MeshStandardMaterial({ color: 0x2f3a45, roughness: 0.45, metalness: 0.5 })
  ;[-0.1, 1.28].forEach((x) => {
    // 机身
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.2, 0.2), camMat)
    body.position.set(x, armY - 0.16, 0)
    body.castShadow = true
    g.add(body)
    // 镜头（朝 +z，指向厂区）
    const lens = new THREE.Mesh(
      new THREE.CylinderGeometry(0.075, 0.09, 0.12, 14),
      new THREE.MeshStandardMaterial({ color: 0x11161c, roughness: 0.2, metalness: 0.7 })
    )
    lens.rotation.x = Math.PI / 2
    lens.position.set(x, armY - 0.16, 0.16)
    g.add(lens)
    // 雨刷/遮阳罩
    cams.push(box(0.46, 0.05, 0.26, [x, armY - 0.05, 0.02]))
  })
  const camTop = cluster(cams, BLD_MAT.roof, 'cctv-camtop')
  if (camTop) g.add(camTop)

  // ④ 探照灯（夜间照明，也让立杆顶部不那么空）
  // ⚠️ 发光强度必须压得很低：场景开了 bloom，杆子又在画面近处，
  //    初版 emissiveIntensity=0.7 + 纯白 → 屏幕上糊出一大团白光，把半个厂区盖住。
  //    这里降到 0.18 并改用暖白，只保留"灯是亮的"这个信息。
  const lamp = new THREE.Mesh(new THREE.BoxGeometry(0.38, 0.12, 0.26),
    new THREE.MeshStandardMaterial({ color: 0x8a8578, emissive: 0xffe0a8, emissiveIntensity: 0.18 }))
  lamp.position.set(-0.34, armY - 0.2, 0.06)
  lamp.rotation.z = 0.22
  g.add(lamp)

  // ⑤ 检修爬梯（贴杆到 3m 高）
  const ladder = cluster(ladderGeos(3.0, { rungs: 7 }), MAT.galv, 'cctv-ladder')
  if (ladder) { ladder.position.set(-0.22, 0.45, 0); g.add(ladder) }

  // ⑥ 杆身色标（黄黑环，提示"这是固定设施"）
  const band = hazardStripeMesh(0.6, 0.02, 0.1)
  if (band) { band.position.y = 1.8; g.add(band) }

  g.userData.facilityId = 'cctv'
  return g
}
