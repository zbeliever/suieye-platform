<script setup>
import { ref, computed, onMounted, onBeforeUnmount, shallowRef, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { CSS2DRenderer, CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import api from '../api/index.js'
// 工业细节构件库：法兰/护栏/爬梯/平台/铭牌/警示条/电机/齿轮 —— 每簇合批成 1 个 draw call
import {
  MAT as DETAIL_MAT, P, addGeos, cluster,
  flangeGeos, flangePairGeos, ribGeos, railingGeos, ladderGeos, platformGeos, postGeos, ringGeos,
  shellBoltGeos,
  motorGeos, handwheelGeos, gearGeos,
  boltGeos, blockGeos, cylGeos, rodGeos, boltRowGeos,
  nameplateMesh, hazardStripeMesh,
} from '../utils/equipDetails.js'
// 功能载体建筑：中控室 / 化验室 / 药剂间 / 配电室 / 监控立杆
// —— 让"流程外的平台功能"在厂区里有真实的落点，而不是只存在于左栏列表里
import {
  buildControlRoom, buildLab, buildDosingRoom, buildPowerRoom, buildCctvPole,
} from '../utils/facilityBuildings.js'
// 厂区空间：厂房骨架 / 地坪标线 / 管廊 / 围墙
// —— 设备再细，摆在一片空旷网格上也只是"陈列品"；这一层把旷野变成车间
import {
  buildWorkshopFrame, buildFloorMarkings, buildPipeRack, buildPerimeter,
} from '../utils/workshopStructures.js'

// ── 页面状态 ────────────────────────────────────────
const router = useRouter()
const canvasContainer = ref(null)
const selectedEquipment = ref(null)
const trendChartRef = ref(null)
const repairFormRef = ref(null)
const showLabels = ref(true)
const autoRotate = ref(false)
// （曾有一个 viewMode = ref('3d') 的残留，全文件无任何消费者，已删）

// ══════════════════════════════════════════════════
//  工艺流程分段（3D 首页的"点流程进功能"唯一真源）
//
//  设计原则：
//   ① 段 = 真实工艺环节，不是页面分组。段内设备共享同一个功能落点，
//      因为现场工程师的心智是"我在磨矿段"而不是"我在看粒度分析页"。
//   ② 每段必须真有对应功能页 —— 没有页面的设备不单独成段，
//      而是并入它服务的那一段（渣浆泵 → 浮选段，见 segOf 的 pump_1）。
//   ③ 段的顺序 = 物料流向，与 3D 布局（北排→南排→水处理区）一致。
// ══════════════════════════════════════════════════
const PROCESS_SEGMENTS = [
  {
    id: 'grinding',
    name: '磨矿段',
    short: '磨矿',
    // 磨矿是"矿浆的源头"，落点是粒度分析工作台
    equipIds: ['ball_mill'],
    route: '/workbench/grinding',
    routeLabel: '粒度分析',
    desc: '矿石经球磨机磨细至 -200 目占 70%，P80 决定后续浮选解离度',
    color: '#165dff',
  },
  {
    id: 'flotation',
    name: '浮选段',
    short: '浮选',
    // ★ 渣浆泵并入本段：它的职责是"输送浮选给矿矿浆"（后端 desc 原文），
    //   本身就是浮选的进料环节，没有任何以泵为主体的功能页。
    //   单独成段会点不出东西 —— 那正是本次要消灭的"点了没反应"。
    equipIds: ['flotation_1', 'flotation_2', 'agitator', 'pump_1'],
    route: '/monitor',
    routeLabel: '实时品位监控',
    desc: '药剂搅拌后由渣浆泵送入浮选槽，粗选与扫选两段回收铅锌矿物',
    color: '#00b42a',
    primary: true,
  },
  {
    id: 'concentrate',
    name: '精矿输送',
    short: '精矿',
    equipIds: ['conveyor'],
    route: '/safety',
    routeLabel: '皮带安全巡检',
    desc: '精矿经皮带输送机外运，沿线 4 路机位监测人员与皮带状态',
    color: '#ff7d00',
  },
  {
    id: 'tailings',
    name: '尾矿与回水',
    short: '尾矿回水',
    // 尾矿到回水是连续的一条水线，合并成一段：
    // 浓密机底流去尾矿库，溢流进汇集池再回用 —— 现场就是一条水系。
    equipIds: ['thickener', 'tailings_pool', 'water_return', 'water_dosing'],
    route: '/tailings',
    routeLabel: '尾矿监测',
    desc: '浓密机分离底流与溢流，溢流经汇集池澄清、加药后回用于磨矿补水',
    color: '#722ed1',
    // 段内还有第二个落点：水处理部分是独立的水质监测页
    altRoute: { route: '/water', label: '水质监测', equipIds: ['tailings_pool', 'water_return', 'water_dosing'] },
  },
]

/** 设备 → 所属工艺段（反向索引，O(1) 查） */
const SEG_OF_EQUIP = (() => {
  const m = new Map()
  for (const seg of PROCESS_SEGMENTS) {
    for (const eid of seg.equipIds) m.set(eid, seg)
  }
  return m
})()

/** 某台设备点「进入」时应跳哪个功能页
 *  优先用段内更精确的 altRoute（如尾矿段里的水池 → 水质监测） */
function routeFor(equipId) {
  const seg = SEG_OF_EQUIP.get(equipId)
  if (!seg) return null
  if (seg.altRoute && seg.altRoute.equipIds.includes(equipId)) {
    return { route: seg.altRoute.route, label: seg.altRoute.label, seg }
  }
  return { route: seg.route, label: seg.routeLabel, seg }
}

/** 当前选中设备所属的工艺段（驱动高亮与按钮文案） */
const activeSegment = computed(() => {
  const id = selectedEquipment.value?.id
  return id ? (SEG_OF_EQUIP.get(id) || null) : null
})

/** 当前选中设备的跳转目标（undefined 时按钮不渲染） */
const selectedRoute = computed(() => {
  const id = selectedEquipment.value?.id
  return id ? routeFor(id) : null
})

/** 右下设备清单折叠状态（默认收起：3D 画面才是主角） */
const equipDockOpen = ref(false)

/** 设备清单距底距离 = 操作日志实际高度 + 间距。
 *  不能写死数值：日志面板高度随视口自适应（max-height:40vh），写死会在矮屏压住日志、
 *  在高屏留出一道空缝。这里实测后回填，随 ResizeObserver 一起更新。 */
const dockBottom = ref('206px')

/** 日志显示条数：随视口高度收敛，矮屏只留 5 条，别让辅信息吃掉 3D 主体面积 */
const viewH = ref(window.innerHeight)
const logRows = computed(() => (viewH.value < 820 ? 5 : viewH.value < 960 ? 7 : 8))

/** 每段的聚合工况（给 3D 上方的段位条用） */
const segmentStats = computed(() => {
  return PROCESS_SEGMENTS.map((seg) => {
    const eqs = seg.equipIds
      .map((eid) => equipments.value.find((e) => e.id === eid))
      .filter(Boolean)
    const avg = eqs.length
      ? Math.round(eqs.reduce((s, e) => s + (e.health || 0), 0) / eqs.length)
      : null
    const bad = eqs.filter((e) => e.status === 'danger').length
    const warn = eqs.filter((e) => e.status === 'warning').length
    return { ...seg, count: eqs.length, avgHealth: avg, bad, warn }
  })
})

/** 工艺链导航数据：段 + 段内功能入口 + 段内设备（供左栏展开）
 *  功能入口 = 段主功能 + 可选的 altRoute（尾矿段里水设备单独走水质监测） */
const navSegments = computed(() =>
  segmentStats.value.map((seg) => {
    const entries = [{ path: seg.route, label: seg.routeLabel }]
    if (seg.altRoute) entries.push({ path: seg.altRoute.route, label: seg.altRoute.label })
    const eqs = seg.equipIds
      .map((eid) => equipments.value.find((e) => e.id === eid))
      .filter(Boolean)
      .map((e) => ({ id: e.id, name: e.name, status: e.status, health: e.health }))
    return { ...seg, entries, eqs }
  })
)

/** 当前展开的工艺段（手风琴：同时只展开一段，避免左栏无限变长） */
const expandedSeg = ref(null)

/** 点工艺段行 = 相机飞过去 + 展开/收起设备与功能 */
function toggleSegment(seg) {
  expandedSeg.value = expandedSeg.value === seg.id ? null : seg.id
  focusSegment(seg)
}

/** 公辅与中控：把"流程外功能"按**厂区里的物理载体**归位
 *
 *  ★ 这是本次重构的核心思路：3D 厂区不是设备的陈列柜，而是整个系统的入口。
 *    所以功能不该以"平铺菜单"的形式挂在左侧，而该挂到它真实发生的**那间屋子**上：
 *      · 报表/告警/时光机/总览  → 中控室（都是中控楼里的大屏与工位）
 *      · 配药助手               → 药剂间（药剂制备的地方）
 *      · 系统设置               → 配电室（机柜与开关柜所在）
 *      · 浮选/安全工作台         → 化验室（取样化验与工艺调整）
 *      · 直播墙                 → 监控立杆（4 路摄像头的挂载点）
 *    左侧按这个结构排列，就与 3D 场景里的建筑一一对应 —— 左侧是"厂区导览"，
 *    不再是"功能清单"。后续阶段会在 3D 里把这些建筑实体建出来，点建筑进同一页。
 *
 *  每项 entries 是该建筑里能进的平台功能；单入口的载体只放一个。 */
const NAV_FACILITIES = [
  {
    id: 'control_room',
    name: '中控室',
    tag: '中控楼 · 二层',
    icon: 'Monitor',
    desc: '全厂监视与调度中心',
    entries: [
      { path: '/dashboard',    label: '生产总览' },
      { path: '/alarms',       label: '告警中心' },
      { path: '/reports',      label: '报表统计' },
      { path: '/timemachine',  label: '工况时光机' },
    ],
  },
  {
    id: 'lab',
    name: '化验室',
    tag: '中控楼 · 一层',
    icon: 'DataAnalysis',
    desc: '取样化验与工艺调整',
    entries: [
      // 磨矿工作台已有独立的工艺段落点（磨矿段 → 粒度分析），这里不重复列
      { path: '/workbench/flotation', label: '浮选工作台' },
      { path: '/workbench/safety',    label: '安全工作台' },
    ],
  },
  {
    id: 'dosing_room',
    name: '药剂间',
    tag: '浮选厂房 · 北侧',
    icon: 'FirstAidKit',
    desc: '药剂制备与添加',
    entries: [{ path: '/dosing', label: '配药助手' }],
  },
  {
    id: 'power_room',
    name: '配电室',
    tag: '公辅区 · 东侧',
    icon: 'Cpu',
    desc: '供配电与机柜',
    entries: [{ path: '/settings', label: '系统设置' }],
  },
  {
    id: 'cctv',
    name: '监控立杆',
    tag: '厂区沿线 · 4 路',
    icon: 'VideoCamera',
    desc: '全流程摄像点位',
    entries: [{ path: '/livewall', label: '直播墙' }],
  },
]

// ── 弹窗状态 ────────────────────────────────────────
const showTrendDialog = ref(false)
const showMaintenanceDialog = ref(false)
const showRepairDialog = ref(false)
const trendData = ref([])
const trendLoading = ref(false)
const trendRange = ref(24)          // 1 / 6 / 24 / 168 小时
const trendMetric = ref('all')      // all / temperature / vibration / power / health
let selectedTrendEquip = null        // 趋势弹窗当前设备

const predictionData = ref(null)
const predictionLoading = ref(false)
const predictionChartRef = ref(null)

const maintenanceLoading = ref(false)
const repairSubmitting = ref(false)
const alarmSoundEnabled = ref(false)

// ── hover 悬浮信息卡 ──
const hoveredEquipment = ref(null)
const hoveredId = ref(null)
const hoverCardRef = ref(null)
const hoverPos = ref({ x: 0, y: 0 })   // 用响应式 style 绑定定位：v-if 首次渲染时 DOM 尚未存在，
                                       // 手动写 style 会漏掉第一帧 → 卡片闪在容器左上角

function healthLevel(h) {
  if (h >= 80) return 'good'
  if (h >= 50) return 'warn'
  return 'bad'
}
function statusLabel(s) {
  return { running: '运行', warning: '预警', stopped: '停机', danger: '危险' }[s] || s
}

// ── 操作日志 ────────────────────────────────────────
const operationLogs = ref([])

// ── 维护记录（从后端按设备加载） ──
const maintenanceRecords = ref([])

// ── 报修表单 ────────────────────────────────────────
const repairForm = ref({
  priority: 'high',
  category: '',
  description: '',
  reporter: '操作员',
})
const repairRules = {
  category: [{ required: true, message: '请选择故障类型', trigger: 'change' }],
  description: [{ required: true, message: '请描述故障现象', trigger: 'blur' }],
}

// ── 报修工单中心（提交后可见、可指派、可维修关闭） ──────
const showOrderCenter = ref(false)
const orderLoading = ref(false)
const orderList = ref([])
const orderStats = ref({ total: 0, open: 0, pending: 0, assigned: 0, repairing: 0, fixed: 0, rejected: 0 })
const orderFilter = ref('open')        // open / all / pending / assigned / repairing / fixed / rejected
const orderKeyword = ref('')
const selectedOrder = ref(null)        // 工单详情（含流转时间线）
const orderDetailLoading = ref(false)
const showOrderTransitionDialog = ref(false)
const orderTransitioning = ref(false)
const orderTransition = ref({
  action: '', action_label: '', need_note: false, need_assignee: false,
  note_label: '处理说明', note_placeholder: '',
  operator: '操作员', assignee: '', note: '', action_taken: '', downtime_minutes: null,
})
const pendingOrderCount = computed(() => orderStats.value.open || 0)

// ── 设备数据（从后端 API 动态加载） ───────────────
const equipments = ref([])
const equipmentStats = ref({ total: 0, running: 0, warning: 0, danger: 0, avg_health: 0 })
const lastUpdateTime = ref(null)
const isLoading = ref(true)
/** 3D 场景初始化失败时的错误文案（为空表示正常） */
const sceneError = ref('')
let refreshTimer = null
let dockObserver = null   // 监听操作日志高度变化 → 同步设备清单位置
const REFRESH_INTERVAL = 5000 // 5 秒刷新一次

/**
 * 设备是否"已停机" —— 启停按钮文案 / 按钮色 / 实际动作 / 停机暂停转动 的**唯一权威判定**。
 *
 * ⚠️ 后端的 `status` 把「健康度」和「启停」折叠进了同一个字段：
 *    running / warning / danger 三者**都是"在转"**，只有 stopped 才是停机
 *    （见 backend/api/routers/equipment.py：先按健康度派生 danger/warning/running，
 *      再被 control_state==='stopped' 覆盖成 stopped）。
 * 所以判定"能不能启动"只能看 `stopped`。
 * 把 `running` 当成"在运行"会出现：**预警/故障设备按钮显示「启动」、点下去执行「停机」**
 * —— 面板和弹窗对不上（实测踩过这个 bug，且预警设备是多数，几乎必现）。
 */
const isEquipStopped = (eq) => !!eq && eq.status === 'stopped'

/** 从后端拉取设备数据 */
async function fetchEquipmentData() {
  try {
    const data = await api.get('/equipment')
    if (data && data.equipment) {
      // 首次加载：创建 3D 模型
      const isFirstLoad = equipments.value.length === 0
      equipments.value = data.equipment
      equipIndex = new Map(data.equipment.map(e => [e.id, e]))
      equipmentStats.value = data.stats || {}
      lastUpdateTime.value = new Date().toLocaleTimeString()

      if (isFirstLoad && scene) {
        // 首次拿到数据后，在场景中放置设备
        placeAllEquipments()
      } else if (!isFirstLoad) {
        // 后续更新：只刷新数据，不重建模型
        updateEquipmentData()
      }
    }
  } catch (e) {
    console.error('获取设备数据失败:', e)
  } finally {
    if (isLoading.value) isLoading.value = false
  }
}

/** 将所有设备放入 3D 场景（仅首次调用） */
function placeAllEquipments() {
  equipments.value.forEach((equip, idx) => {
    // 如果已存在则跳过
    if (equipmentMeshes.has(equip.id)) return

    const model = createEquipmentModel(equip)
    model.position.set(...equipPos(equip.id, equip))
    scene.add(model)
    equipmentMeshes.set(equip.id, model)

    // 设备基础（混凝土基座）—— 让设备"站在地上"，而不是浮在场地上
    createPedestal(model)

    // 统一材质质感（提升金属度、降低粗糙度）
    polishMaterials(model)

    // 异常设备状态光柱
    createStatusBeam(model, equip)

    // ── 标签（孪生读数牌：序号 + 名称 + 健康度）──
    // ★ 挂高只按"设备本体"算：光柱高 6.2m、基座宽 1m+，把它们算进包围盒会让异常设备的牌子
    //   飘到光柱顶端（7m），和本体彻底脱钩 —— 这正是"标签对应不明确"的主因。
    // ★ 而且"本体顶"要取**主体块**（槽体/筒体/池壁）的顶面，不是 group 最高点：
    //   浮选槽顶部走道 + 立式电机把 group 顶到 3.85m，牌子就被抬到空中，
    //   既离开了本体，也压到了旁边的工艺管道上。
    const topY = bodyTopY(model)
    const labelY = Math.max(topY + 0.55, 1.6)
    const label = createLabel(equip.name, equip.status, equip.health, idx + 1, equip.location_tag, equip.id)
    label.position.set(0, labelY, 0)
    model.add(label)

    // 读数牌可点：视角重叠时远处的设备点不到（被近处拾取盒挡住），
    // 但它的牌子一定看得见且从不了遮挡 → 点牌子 = 选中那台设备，对应关系零歧义
    label.element.addEventListener('click', (ev) => {
      ev.stopPropagation()
      focusEquipment(equip.id)
    })

    // ★ 双击牌子 = 直接进这台设备所属的功能页。
    //   必须单独绑在标签上：标签是 pointer-events:auto 且 stopPropagation 的，
    //   双击事件**根本冒泡不到画布** —— 只给 canvas 加 dblclick 的话，
    //   用户去双击那个看得见的名字牌会毫无反应（这几乎是所有人的第一反应）。
    //   标签是画面里唯一"看得见又点得到"的东西，所以它是双击的主入口。
    label.element.addEventListener('dblclick', (ev) => {
      ev.stopPropagation()
      const target = routeFor(equip.id)
      if (target) router.push(target.route)
    })
    label.element.title = `单击查看详情 · 双击进入「${routeFor(equip.id)?.label || '功能页'}」`

    // 引线（屏幕空间 SVG）+ 布局登记
    const leaderEls = registerLabel(equip, model, label, topY)

    // 地面定位环（位置锚点 + 告警呼吸）
    createLocatorRing(model, equip)

    // 健康度热力（设备本体配色 = 当前健康度）
    applyTwinTint(model, equip, false)

    // 告警脉冲
    if (equip.alarm) {
      createAlarmPulse(model, equip)
    }

    // 拾取代理盒（拾取只测这一个盒子，不再全场景递归射线）
    buildPickProxy(model, equip)

    // 特效引用缓存 —— animate 每帧要用的东西一次性找好，避免每帧 getObjectByName 递归遍历
    const beamParts = []
    model.traverse(o => { if (o.name === 'statusBeam' && o.material) beamParts.push(o) })
    model.userData.fx = {
      light: model.getObjectByName('statusLight'),
      ring: model.getObjectByName('locatorRing'),
      glow: model.getObjectByName('locatorGlow'),
      arc: model.getObjectByName('locatorArc'),
      beamParts,
      labelEl: label.element,
      leaderEls,
    }
  })
  // 管路
  createPipelines()
  // 场景几何已定 → 刷新一次阴影贴图（此后 autoUpdate 关闭）
  if (renderer) renderer.shadowMap.needsUpdate = true
  applyLabelFocus()
}

/** 数据更新时同步 3D 场景状态（不重建模型） */
function updateEquipmentData() {
  equipments.value.forEach(equip => {
    const group = equipmentMeshes.get(equip.id)
    if (!group) return

    // 更新 userData 引用（供详情面板使用）
    group.userData.equip = equip

    // 更新状态灯颜色
    const light = group.getObjectByName('statusLight')
    if (light) {
      const colorMap = { running: 0x4caf50, warning: 0xff9800, stopped: 0x9e9e9e, danger: 0xf44336 }
      light.material.color.setHex(colorMap[equip.status] || 0x4caf50)
    }

    // 更新标签（名称 + 实时健康度读数）与配色
    group.children.forEach(child => syncLabel(child, equip))

    // 健康度热力 + 定位环颜色（数字孪生：场景随实时数据改变，不是静态装饰）
    applyTwinTint(group, equip, selectedEquipment.value?.id === equip.id)
    const tintHex = equip.status === 'stopped' ? 0x9e9e9e : healthTint(equip.health).hex
    const locRing = group.getObjectByName('locatorRing')
    if (locRing) locRing.material.color.setHex(tintHex)
    const locArc = group.getObjectByName('locatorArc')
    if (locArc) locArc.material.color.setHex(tintHex)

    // 更新告警脉冲：新增/移除
    const hasPulse = alarmPulses.some(p => p.equipId === equip.id)
    if (equip.alarm && !hasPulse) {
      createAlarmPulse(group, equip)
    } else if (!equip.alarm && hasPulse) {
      // 移除脉冲
      const idx = alarmPulses.findIndex(p => p.equipId === equip.id)
      if (idx >= 0) {
        const { mesh } = alarmPulses.splice(idx, 1)[0]
        group.remove(mesh)
        mesh.geometry.dispose()
        mesh.material.dispose()
      }
    }

    // 停机 → 暂停该设备的旋转部件（数字孪生：不转的机器在画面上真的别转）
    // ★ 按显式 equipId 绑定（push 时登记）。原来靠 mesh.parent 层级反推归属：
    //   只要某个构件将来挂深一层（二级子组），判定就失效 → 停机后部件照转，且无任何报错。
    rotatingParts.forEach(rp => {
      if (rp.equipId === equip.id) rp.paused = isEquipStopped(equip)
    })
  })
  // syncLabel 的 cssText 会抹掉聚焦态样式 → 整批刷完后重新落一次
  applyLabelFocus()

  // ★ 详情面板的数据源必须跟着换新：`equipments.value` 每 5s 整批替换成**新对象**，
  //   而 selectedEquipment 仍指向上一批里的旧对象 —— 面板里的功率/转速/流量/健康度
  //   会**永远停在选中那一刻**（面板写着"实时"，数字却冻结，比不刷新更糟）。
  //   ⚠️ 拿不到新对象时**保持原对象**，绝不能置 null —— 否则轮询一瞬间面板会闪退。
  if (selectedEquipment.value) {
    const fresh = equipIndex.get(selectedEquipment.value.id)
    if (fresh) selectedEquipment.value = fresh
  }
}

// ══════════════════════════════════════════════════
//  设备操作功能
// ══════════════════════════════════════════════════

/** 查看趋势 — 调 history API，支持时间范围 */
async function showTrend(equip) {
  selectedTrendEquip = equip
  trendRange.value = 24
  trendMetric.value = 'all'
  showTrendDialog.value = true
  await loadTrendData()
  addLog(`查看 ${equip.name} 趋势数据`)
}

/** 拉取趋势数据 + 渲染图表（时间范围切换时复用） */
async function loadTrendData() {
  const equip = selectedTrendEquip
  if (!equip) return
  trendLoading.value = true
  try {
    const data = await api.get(`/equipment/${equip.id}/history`, {
      params: { hours: trendRange.value, metric: trendMetric.value },
    })
    trendData.value = data.points || []
    await nextTick()
    renderTrendChart()
  } catch (e) {
    console.error('获取趋势数据失败:', e)
    trendData.value = []
  } finally {
    trendLoading.value = false
  }
}

/** 时间范围切换 */
async function onTrendRangeChange(h) {
  trendRange.value = h
  await loadTrendData()
}

/** 指标切换 */
async function onTrendMetricChange(m) {
  trendMetric.value = m
  await loadTrendData()
}

/** 查看维护记录 — 从后端按设备加载 */
async function showMaintenance(equip) {
  showMaintenanceDialog.value = true
  maintenanceLoading.value = true
  maintenanceRecords.value = []
  try {
    const data = await api.get(`/equipment/${equip.id}/maintenance`)
    maintenanceRecords.value = data.records || []
  } catch (e) {
    console.error('获取维护记录失败:', e)
    maintenanceRecords.value = []
  } finally {
    maintenanceLoading.value = false
  }
  addLog(`查看 ${equip.name} 维护记录`)
}

/** 打开报修工单 */
function openRepairDialog(equip) {
  repairForm.value = { priority: 'high', category: '', description: '', reporter: '操作员' }
  showRepairDialog.value = true
  // 清掉上一次的校验红字，避免"点了提交没反应"的错觉
  nextTick(() => repairFormRef.value?.clearValidate())
}

/** 提交报修工单 — 落库后端返回工单号，随后刷新工单中心与角标 */
async function submitRepair() {
  if (!selectedEquipment.value) return
  const equip = selectedEquipment.value

  // 校验失败要给出可见反馈（原先被 catch 静默吞掉，点了等于没反应）
  if (repairFormRef.value) {
    try {
      await repairFormRef.value.validate()
    } catch (e) {
      ElMessage.warning('请先补全表单：故障类型、故障描述')
      return
    }
  }

  repairSubmitting.value = true
  try {
    const data = await api.post(`/equipment/${equip.id}/repair`, repairForm.value)
    const oid = data?.order?.order_id || ''
    showRepairDialog.value = false
    ElMessage({
      type: 'success',
      message: `工单 ${oid} 已提交，点击顶部「报修工单」可跟踪维修进度`,
      duration: 5000,
    })
    await fetchOrders()
    if (showOrderCenter.value) await fetchOrderDetail(oid)
  } catch (e) {
    // 网络 / 服务端错误：axios 拦截器已弹提示
  } finally {
    repairSubmitting.value = false
  }
}

// ─────────────────────────────────────────────────────────
// 工单中心（列表 / 详情 / 闭环流转）
// ─────────────────────────────────────────────────────────

const ORDER_STATUS_TAG = {
  pending: 'danger',
  assigned: 'warning',
  repairing: 'primary',
  fixed: 'success',
  rejected: 'info',
}

function orderStatusType(s) {
  return ORDER_STATUS_TAG[s] || 'info'
}

function actionBtnType(action) {
  return { fix: 'success', reject: 'info', reopen: 'warning', assign: 'primary', start: 'primary' }[action] || 'primary'
}

/** 挂单时长文案：<1h 显示分钟，<24h 显示小时，否则显示天 */
function ageText(o) {
  const h = o.age_hours || 0
  if (!o.is_open) return o.closed_at ? `闭环 ${o.closed_at.slice(5, 16)}` : '已闭环'
  if (h < 1) return `挂单 ${Math.max(1, Math.round(h * 60))} 分钟`
  if (h < 24) return `挂单 ${h.toFixed(1)} 小时`
  return `挂单 ${(h / 24).toFixed(1)} 天`
}

/** 拉工单列表 + 统计 */
async function fetchOrders() {
  orderLoading.value = true
  try {
    const p = new URLSearchParams()
    p.set('status', orderFilter.value)
    if (orderKeyword.value.trim()) p.set('keyword', orderKeyword.value.trim())
    const data = await api.get(`/repairs?${p.toString()}`)
    orderList.value = data.orders || []
    orderStats.value = data.stats || orderStats.value
  } catch (e) {
    orderList.value = []
  } finally {
    orderLoading.value = false
  }
}

/** 拉工单详情（含流转时间线与回写记录） */
async function fetchOrderDetail(orderId) {
  if (!orderId) return
  orderDetailLoading.value = true
  try {
    selectedOrder.value = await api.get(`/repairs/${orderId}`)
  } catch (e) {
    selectedOrder.value = null
  } finally {
    orderDetailLoading.value = false
  }
}

/** 打开工单中心 */
async function openOrderCenter() {
  showOrderCenter.value = true
  await fetchOrders()
  if (orderList.value.length) {
    await fetchOrderDetail(orderList.value[0].order_id)
  } else {
    selectedOrder.value = null
  }
}

/** 选中一条工单 */
async function selectOrder(o) {
  if (!o) return
  await fetchOrderDetail(o.order_id)
}

/** 打开流转弹窗（可执行动作由后端 next_actions 下发） */
function openOrderTransition(a) {
  const meta = {
    assign: { note_label: '派单说明', note_placeholder: '如：已通知维修班组，预计今日处理' },
    start: { note_label: '开工说明', note_placeholder: '如：已停机挂牌，开始拆检' },
    fix: { note_label: '维修说明', note_placeholder: '必填：故障原因、更换件、试机结果' },
    reject: { note_label: '驳回原因', note_placeholder: '必填：为何无需维修（如误报、重复报修）' },
    reopen: { note_label: '重开原因', note_placeholder: '必填：问题复现情况' },
  }[a.action] || {}
  orderTransition.value = {
    action: a.action,
    action_label: a.label,
    need_note: !!a.need_note,
    need_assignee: !!a.need_assignee,
    note_label: meta.note_label || '处理说明',
    note_placeholder: meta.note_placeholder || '',
    operator: orderTransition.value.operator || '操作员',
    assignee: selectedOrder.value?.assignee || '',
    note: '',
    action_taken: '',
    downtime_minutes: null,
  }
  showOrderTransitionDialog.value = true
}

/** 提交流转 */
async function submitOrderTransition() {
  const t = orderTransition.value
  if (!t.operator.trim()) {
    ElMessage.warning('请填写操作人')
    return
  }
  if (t.need_assignee && !t.assignee.trim()) {
    ElMessage.warning('请填写维修负责人')
    return
  }
  if (t.need_note && !t.note.trim()) {
    ElMessage.warning(`请填写${t.note_label}`)
    return
  }
  orderTransitioning.value = true
  try {
    const body = {
      action: t.action,
      operator: t.operator.trim(),
      assignee: t.assignee.trim() || null,
      note: t.note.trim() || null,
      action_taken: t.action_taken.trim() || null,
      downtime_minutes: t.downtime_minutes,
    }
    const data = await api.post(`/repairs/${selectedOrder.value.order_id}/transition`, body)
    const doneText = {
      assigned: '已指派，等待维修班组接单',
      repairing: '已开始维修',
      fixed: '已修复闭环，维护记录已同步回写',
      rejected: '已驳回',
      pending: '已重新打开',
    }[data.order.status]
    showOrderTransitionDialog.value = false
    ElMessage.success(`工单 ${data.order.order_id} ${doneText || data.order.status_label}`)
    await fetchOrders()
    await fetchOrderDetail(data.order.order_id)
    // 「维修完成」会回写维护记录与台账，同步刷新维护记录与设备数据
    if (t.action === 'fix') {
      fetchEquipmentData()
      if (showMaintenanceDialog.value && selectedEquipment.value) {
        showMaintenance(selectedEquipment.value)
      }
    }
    refreshLogs()
  } catch (e) {
    // 拦截器已提示（409 冲突 / 400 参数）
  } finally {
    orderTransitioning.value = false
  }
}

/** 导出维护记录 CSV（真导出，原先是个无 handler 的死按钮） */
function exportMaintenanceCsv() {
  const equip = selectedEquipment.value
  if (!equip) return
  const rows = maintenanceRecords.value
  if (!rows.length) {
    ElMessage.warning('暂无维护记录可导出')
    return
  }
  const esc = (v) => {
    const s = v === null || v === undefined ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const header = ['日期', '类型', '负责人', '内容', '结果', '来源工单']
  const body = rows.map((r) => [r.date, r.type, r.technician, r.content, r.result, r.source_order_id]
    .map(esc).join(','))
  const csv = [header.join(','), ...body].join('\r\n')
  const blob = new Blob([`\ufeff${csv}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${equip.name}_维护记录_${new Date().toLocaleDateString('sv-SE')}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  ElMessage.success(`已导出 ${rows.length} 条维护记录`)
}


/**
 * 设备启停控制 — 持久化到后端，刷新不还原。
 * 判定口径与按钮共用 `isEquipStopped`（见文件顶部），不允许再有第二套判断。
 */
async function toggleEquipment(equip) {
  const stopped = isEquipStopped(equip)
  const action = stopped ? '启动' : '停机'
  try {
    await ElMessageBox.confirm(
      `确认${action} ${equip.name}？${stopped ? '\n启动前请确认现场安全！' : '\n停机后相关工艺流程将中断！'}`,
      `设备${action}`,
      { confirmButtonText: `确认${action}`, cancelButtonText: '取消', type: stopped ? 'success' : 'warning' }
    )
  } catch (e) {
    return                       // 用户取消
  }
  try {
    await api.post(`/equipment/${equip.id}/control`, {
      action: stopped ? 'start' : 'stop',
      operator: '操作员',
    })
    // ★ 不本地赋 status：`new_status` 只是启停态（running/stopped），而前端的 `status`
    //   是"健康度 + 启停"的合成态。直接写 running 会把一台低健康度设备**谎报成健康**，
    //   直到下一次轮询才纠正。回后端重取一次，以服务端派生结果为准。
    await fetchEquipmentData()
    ElMessage.success(`${equip.name} 已${action}`)
    addLog(`${action} ${equip.name}`, stopped ? 'success' : 'warning')
  } catch (e) {
    ElMessage.error(`${action}失败：${e?.message || e}`)
  }
}

/** 刷新设备统计 */
function refreshStats() {
  const list = equipments.value
  equipmentStats.value = {
    total: list.length,
    running: list.filter(e => e.status === 'running').length,
    warning: list.filter(e => e.status === 'warning').length,
    danger: list.filter(e => e.status === 'danger').length,
    stopped: list.filter(e => isEquipStopped(e)).length,
    avg_health: Math.round(list.reduce((s, e) => s + e.health, 0) / list.length),
  }
}

/** 操作日志：统一从后端拉（启停 / 报修 / 维护 / 工单流转都已落库，刷新不丢） */
async function refreshLogs() {
  try {
    const data = await api.get('/equipment/all/logs?limit=12')
    operationLogs.value = (data.logs || []).map((x) => ({
      time: (x.time || '').slice(5, 16),   // MM-DD HH:MM（跨天不至于误读）
      action: x.action,
      operator: x.operator,
      type: x.type,
    }))
    // 日志面板到这一刻才渲染出来 → 重新对齐设备清单高度
    nextTick(syncDockBottom)
  } catch (e) {
    // 拉取失败时保留现有列表
  }
}

/** 兼容旧调用点：动作本身已在后端记日志，这里只需重新拉取 */
function addLog() {
  refreshLogs()
}

// ── Three.js 核心 ───────────────────────────────────
let scene, camera, renderer, labelRenderer, controls, composer, bloomPass
let animationId = null
let ambientDust = null             // 空气中漂浮的微尘（空间感）
let leaderSvg = null               // 屏幕空间引线层（SVG）
const labelItems = []              // 标签布局项：{ el, anchor, line, dot, w, h, dim }
const equipmentMeshes = new Map()   // id → THREE.Group
let equipIndex = new Map()          // id → 当前设备数据（供动画循环读实时流量，避免每帧遍历）
const rotatingParts = []            // 需要旋转的部件
const alarmPulses = []              // 告警脉冲动画对象

// ── 颜色配置 ───────────────────────────────────────
const STATUS_COLORS = {
  running: { base: 0x4caf50, emissive: 0x1b5e20 },   // 绿色
  warning: { base: 0xff9800, emissive: 0xe65100 },   // 橙色
  stopped: { base: 0x9e9e9e, emissive: 0x424242 },   // 灰色
  danger:  { base: 0xf44336, emissive: 0xb71c1c },   // 红色
}
// ── 深色工业场景配色（3D 视口自成一套世界观，与浅色 UI 形成对比而非冲突）──
const SKY_TOP = 0x0c1420        // 天顶（近黑，压住画面头部）
const SKY_HORIZON = 0x2b4258    // 地平线（冷青灰，给空间一个"远"）
const FLOOR_COLOR = 0x171d26    // 地面（比天空略亮一点点，让设备轮廓浮起来）
const GRID_COLOR = 0x2c3a4b     // 网格主线
const GRID_SUB = 0x1f2937       // 网格副线
const STRUCTURE_COLOR = 0x607d8b
const FOG_COLOR = 0x121a24      // 雾色 = 地平线附近色，远处自然淡出

/** ★ 厂区布局（视图层参数，不是业务数据）
 *  原布局 7 台设备挤在 ±6 的方圆内：定位环互相重叠、边缘标签被裁、没有纵深。
 *  现按工艺流程排成"前后两排 + 药剂侧线"，每台设备有独立领地：
 *    后排 z=-7：磨矿 → 泵送 → 粗选 → 扫选（物料自右向左）
 *    前排 z=+7：浓密 → 输送（底流去向）
 *    侧线 x=-15：药剂站 → 粗选 / 扫选（两条加药支管）
 *  改布局只动这一张表，管线端点由设备 id 反查，不会再出现"改了坐标忘了改管子"。 */
/** 场景中心 = 相机轨道轴心的默认归属地（沿用原来调好的全景轴心）。
 *  设备落位 x∈[-9.5,10.5] / z∈[-5.5,5.5]，y=1.7 让轴心落在设备中部而不是地面。
 *  注意这里是**视觉轴心**不是设备坐标的平均值 —— 球磨机模型自身很长（局部 ±5.7），
 *  平均坐标会被它拽偏，所以这个值是看出来的、不是算出来的。 */
const SCENE_CENTER = new THREE.Vector3(-0.5, 1.7, 0)

/** 设备在厂区里的落位（视图层参数，不是业务数据）
 *
 *  布局逻辑 = 真实工艺流程的空间投影：
 *    ① 北排（z=-5.5）：磨矿 → 输送 → 浮选选别  ——  球磨机(10.5) · 泵(5.0) · 1#槽(-0.5) · 2#槽(-6.0)
 *    ② 南排（z=+5.5）：尾矿处理 → 水处理      ——  浓密机(-3.0) · 皮带(3.5) · 尾矿池(9.5)
 *    ③ 药剂区（z≈0）：搅拌槽(-9.5) 单独在西侧，向两台浮选槽供药
 *    ④ 回水区（z=+9.5~10.5）：回水池(3.5) · 加药装置(9.5) —— 水系统末端
 *
 *  ⚠️ 水处理设备必须往 **+z 方向（厂区南侧）** 排，不能塞进北排：
 *     北排已有 4 台且 x 跨度 -6~10.5，再插进去会与浮选槽/泵重叠，
 *     视觉上"两台设备焊在一起"。南排 z=+5.5 已有浓密机和皮带，
 *     所以回水池进一步下压到 z=+9.5，形成第三排"水处理区"。
 */
const EQUIP_LAYOUT = {
  ball_mill:     [ 10.5, 0, -5.5 ],
  pump_1:        [  5.0, 0, -5.5 ],
  flotation_1:   [ -0.5, 0, -5.5 ],
  flotation_2:   [ -6.0, 0, -5.5 ],
  thickener:     [ -3.0, 0,  5.5 ],
  conveyor:      [  3.5, 0,  5.5 ],
  agitator:      [ -9.5, 0,  0.0 ],
  // ── 水处理区（南侧第三排）──
  tailings_pool: [  9.5, 0,  5.5 ],   // 尾矿浓密机溢流汇集池，承接浓密机来水
  water_return:  [  3.5, 0,  9.5 ],   // 回水池，全厂回水汇合
  water_dosing:  [  9.5, 0, 10.0 ],   // 加药装置，紧邻尾矿池给 PAM
}
/** 取设备在 3D 里的坐标（优先布局表，其次后端 position，最后原点） */
function equipPos(id, equip) {
  return EQUIP_LAYOUT[id] || equip?.position || [0, 0, 0]
}

/** 功能载体建筑的位置与朝向
 *
 *  摆位原则（按相机在东南方向 (21, 14, 26.5) 俯视来组织）：
 *   · 中控楼（中控室 + 化验室）放**北侧远景**：作为厂房背景，同时不遮挡设备
 *   · 药剂间放**西侧**，紧邻浮选段（agitator 在 x=-9.5），加药管往东接进浮选
 *   · 配电室放**东侧偏北**，靠近用电大户球磨机（x=10.5），但要让开桁架视线
 *   · 监控立杆放**南侧前沿**两处，模拟厂区周界监控
 *  这些位置都留出了设备本体（x -9.5~10.5 / z -5.5~10）的活动空间，不会互相穿插。 */
const FACILITY_LAYOUT = {
  control_room: { pos: [-3.0, 0, -16.5], rotY: 0 },
  lab:          { pos: [-13.5, 0, -14.5], rotY: 0 },
  dosing_room:  { pos: [-18.5, 0, 1.5],  rotY: Math.PI / 2 },
  // ★★ 2026-10-18 配电室从 (18,0,-1) 移到 (12.9,0,-12.1)：
  //    原位置卡在厂房骨架东侧立柱与球磨机（10.5,-5.5）之间，
  //    默认视角里整栋楼被桁架和球磨机挡死，只剩屋顶一条缝 ——
  //    左栏「配电室 → 系统设置」在 3D 里等于不可见（QA 实测点击命中球磨机）。
  //    往北收、同时贴着厂房东北角内侧：既在骨架外，也留在画面内。
  // ★★ 2026-10-19 再往西北收 (15.5,-13.5) → (12.9,-12.1)：
  //    上一版仍出框 —— 实测楼体右缘投影 x1=1485 / 1658 / 2002，
  //    而 3D 视口右界只有 1420 / 1580 / 1900（三种尺寸**全部**溢出），
  //    屏幕上只剩孤零零一个蓝色徽标贴着右边缘，楼体整个在画外。
  //    往 −X/−Z 各收 2.6/1.4 后楼体右缘回到视口内，且不与球磨机遮挡。
  // ★★ 2026-10-19 第二轮：上面那步还不够 —— 楼体虽入框，但**西半边被厂房钢柱
  //    + 桁架压住**，屏幕上只剩一块灰顶。配电室本身是 4×4 的小平房（rotY=-90° 后
  //    占 X 14±2 / Z -12.1±2），再往西必然撞上球磨机（10.5,-5.5），往南又回到原问题。
  //    最终解不是继续挪，而是**把相机拉远一档**（见 fitHomeDistance 的 FIT_MIN_SCALE），
  //    让东北角那片空地在画面里留出余量。位置保持 (12.9,-12.1)。
  power_room:   { pos: [ 12.9, 0, -12.1], rotY: -Math.PI / 2 },
  cctv:         { pos: [ 14.5, 0, 15.5], rotY: 0 },
}
/** 第二根监控立杆（对角布置才像周界覆盖） */
const CCTV_SPOTS = [
  { pos: [ 14.5, 0, 15.5], rotY: Math.PI * 0.85 },
  { pos: [-18.5, 0, 14.0], rotY: -Math.PI * 0.15 },
]

// ══════════════════════════════════════════════════
//  设备建模工厂 — 用基础几何体组合成选矿设备
// ══════════════════════════════════════════════════

/** 创建浮选槽 */
function createFlotationCell(equip) {
  const group = new THREE.Group()

  // 槽体（长方体）
  const tankGeo = new THREE.BoxGeometry(3, 2.2, 3)
  const tankMat = new THREE.MeshStandardMaterial({
    color: 0x5b7486, roughness: 0.5, metalness: 0.42
  })
  const tank = new THREE.Mesh(tankGeo, tankMat)
  tank.position.y = 1.1
  tank.castShadow = true
  group.add(tank)

  // 槽体边框线
  const edges = new THREE.EdgesGeometry(tankGeo)
  const lineMat = new THREE.LineBasicMaterial({ color: 0x46596a, linewidth: 1 })
  group.add(new THREE.LineSegments(edges, lineMat))

  // 搅拌轴
  const shaftGeo = new THREE.CylinderGeometry(0.08, 0.08, 2.5, 16)
  const shaftMat = new THREE.MeshPhongMaterial({ color: 0xb0bec5, shininess: 80 })
  const shaft = new THREE.Mesh(shaftGeo, shaftMat)
  shaft.position.y = 2.3
  group.add(shaft)
  rotatingParts.push({ mesh: shaft, axis: 'y', speed: 0.03, equipId: equip.id })

  // 搅拌叶轮（底部）
  const impellerGeo = new THREE.BoxGeometry(1.8, 0.12, 0.25)
  const impellerMat = new THREE.MeshPhongMaterial({ color: 0xffa726, shininess: 60 })
  for (let i = 0; i < 4; i++) {
    const blade = new THREE.Mesh(impellerGeo, impellerMat)
    blade.position.y = 0.2
    blade.rotation.y = (Math.PI / 2) * i
    group.add(blade)
    rotatingParts.push({ mesh: blade, axis: 'y', speed: 0.03, equipId: equip.id })
  }

  // 进料管
  const inletGeo = new THREE.CylinderGeometry(0.18, 0.18, 1.5, 12)
  const pipeMat = new THREE.MeshPhongMaterial({ color: 0x78909c, shininess: 40 })
  const inlet = new THREE.Mesh(inletGeo, pipeMat)
  inlet.rotation.z = Math.PI / 2
  inlet.position.set(-2, 1.8, 0)
  group.add(inlet)

  // 出料口
  const outletGeo = new THREE.CylinderGeometry(0.22, 0.22, 0.8, 12)
  const outlet = new THREE.Mesh(outletGeo, pipeMat)
  outlet.rotation.x = Math.PI / 2
  outlet.position.set(0, 0.5, 1.9)
  group.add(outlet)

  // 矿浆液面（波动）
  const liquidGeo = new THREE.PlaneGeometry(2.7, 2.7, 10, 10)
  const liquidMat = new THREE.MeshPhongMaterial({
    color: 0x4a2a1a, transparent: true, opacity: 0.78,
    shininess: 80, side: THREE.DoubleSide, depthWrite: false
  })
  const liquid = new THREE.Mesh(liquidGeo, liquidMat)
  liquid.rotation.x = -Math.PI / 2
  liquid.position.y = 1.5
  liquid.renderOrder = 2
  group.add(liquid)
  liquidSurfaces.push({ mesh: liquid, baseY: 1.5, phase: Math.random() * 6.28, amp: 0.04, k: 1, equipId: equip.id })

  // 泡沫层（顶部半透明）
  const foamGeo = new THREE.BoxGeometry(2.62, 0.34, 2.62)
  const foamMat = new THREE.MeshPhongMaterial({
    color: 0xb2a173, transparent: true, opacity: 0.4,
    shininess: 100
  })
  const foam = new THREE.Mesh(foamGeo, foamMat)
  foam.position.y = 2.3
  group.add(foam)

  // ══════════════════════════════════════════════════
  //  工业细节：3 个合批簇 + 铭牌 + 警示条 = 只多 5 个 draw call
  // ══════════════════════════════════════════════════
  const steel = []
  const galv = []
  const dark = []

  // ① 进 / 出料配对法兰 —— "这是可拆的接管"
  addGeos(steel, flangePairGeos(0.26, { t: 0.06, bolts: 8 }), P([-2.74, 1.8, 0], [0, 0, Math.PI / 2]))
  addGeos(steel, flangePairGeos(0.3, { t: 0.06, bolts: 8 }), P([0, 0.5, 2.28], [Math.PI / 2, 0, 0]))

  // ② 槽壁竖向加强筋（避开进料管与铭牌位置）
  for (const x of [-1.0, 1.0]) {
    addGeos(steel, blockGeos(0.11, 2.16, 0.08, [x, 1.08, 1.53]))
    addGeos(steel, blockGeos(0.11, 2.16, 0.08, [x, 1.08, -1.53]))
  }
  for (const z of [-0.95, 0.95]) {
    addGeos(steel, blockGeos(0.08, 2.16, 0.11, [1.53, 1.08, z]))
    addGeos(steel, blockGeos(0.08, 2.16, 0.11, [-1.53, 1.08, z]))
  }

  // ③ 槽沿角钢一圈 + 基座裙板 + 地脚螺栓
  addGeos(steel, blockGeos(3.16, 0.1, 0.13, [0, 2.25, 1.53]))
  addGeos(steel, blockGeos(3.16, 0.1, 0.13, [0, 2.25, -1.53]))
  addGeos(steel, blockGeos(0.13, 0.1, 3.16, [1.53, 2.25, 0]))
  addGeos(steel, blockGeos(0.13, 0.1, 3.16, [-1.53, 2.25, 0]))
  // ⚠️ 不在这里做"基座裙板 + 地脚螺栓"：它们落在 y≈0.1，会被基座(高 0.24)整块吃掉，
  //    只白算三角形不产生任何画面。地脚螺栓统一由 createPedestal 层表达。

  // ④ 液位计（管 + 上下两端接头）
  addGeos(steel, cylGeos(0.035, 1.5, [-1.62, 1.15, -0.7]))
  addGeos(steel, cylGeos(0.06, 0.12, [-1.62, 1.92, -0.7]))
  addGeos(steel, cylGeos(0.06, 0.12, [-1.62, 0.38, -0.7]))

  // ⑤ 顶部走道 + 护栏 + 支柱 + 直爬梯（镀锌件）
  const walkY = 2.45
  const walkZ = 2.05
  addGeos(galv, platformGeos(3.4, 0.95), P([0, walkY, walkZ]))
  addGeos(galv, railingGeos(3.4, { h: 1.05, posts: 4 }), P([0, walkY, walkZ + 0.46]))
  for (const px of [-1.5, -0.75, 0, 0.75, 1.5]) {
    addGeos(galv, postGeos(0, walkY, 0.05), P([px, 0, walkZ + 0.4]))
  }
  addGeos(galv, ladderGeos(walkY, { w: 0.45, rungs: 8 }), P([1.88, 0, walkZ + 0.2]))

  // ⑥ 搅拌驱动（桥架 + 减速机 + 立式电机）+ 刮板机构 + 出料阀
  addGeos(dark, blockGeos(1.15, 0.14, 1.15, [0, 2.29, 0]))
  addGeos(dark, blockGeos(0.72, 0.62, 0.72, [0, 2.66, 0]))
  addGeos(dark, motorGeos(0.28, 0.86, { fins: 12 }), P([0, 3.4, 0]))
  addGeos(dark, cylGeos(0.055, 3.36, [0, 2.6, 0.75], [0, 0, Math.PI / 2]))   // 刮板轴
  for (let i = 0; i < 7; i++) {
    addGeos(dark, blockGeos(0.06, 0.16, 0.6, [-1.35 + i * 0.45, 2.52, 0.75]))
  }
  addGeos(dark, blockGeos(0.24, 0.24, 0.24, [1.8, 2.6, 0.75]))                // 刮板驱动
  addGeos(dark, cylGeos(0.17, 0.22, [0, 0.5, 2.3], [Math.PI / 2, 0, 0]))      // 出料阀体
  addGeos(dark, cylGeos(0.05, 0.4, [0, 0.72, 2.3]))                           // 阀杆
  addGeos(dark, handwheelGeos(0.24), P([0, 0.94, 2.3]))

  const steelMesh = cluster(steel, DETAIL_MAT.steel, 'detail-steel')
  if (steelMesh) group.add(steelMesh)
  const galvMesh = cluster(galv, DETAIL_MAT.galv, 'detail-galv')
  if (galvMesh) group.add(galvMesh)
  const darkMesh = cluster(dark, DETAIL_MAT.dark, 'detail-dark')
  if (darkMesh) group.add(darkMesh)

  // ⑦ 铭牌（名称 + 位号）+ 基座黄黑警示条
  const plate = nameplateMesh(`${equip.name} · ${equip.location_tag || ''}`, 0.8, 0.22)
  plate.position.set(0, 1.35, 1.56)
  group.add(plate)
  // 贴在槽壁上部（槽沿角钢下方），避开被基座吃掉的下缘
  const stripe = hazardStripeMesh(3.16, 0.02, 0.12)
  stripe.position.set(0, 2.02, 1.575)
  group.add(stripe)

  addStatusLight(group, equip.status, 4.1)

  group.userData = { equipId: equip.id, equip }
  return group
}

/** 创建球磨机 */
function createBallMill(equip) {
  const group = new THREE.Group()

  // 筒体（大圆柱）
  // 注：初版写的是 `transparent: true, opacity: 0.92` —— 意图是"能看见筒内研磨介质"。
  //     但 polishMaterials 里 `opacity < 0.92 才保留`（0.92 不满足），于是它被强制转成
  //     不透明；这个"半透明"从来没生效过，只是留了个误导后人的意图。现已直接写成不透明。
  const bodyGeo = new THREE.CylinderGeometry(1.2, 1.2, 5, 32)
  const bodyMat = new THREE.MeshStandardMaterial({
    color: 0x455a64, roughness: 0.35, metalness: 0.7
  })
  const body = new THREE.Mesh(bodyGeo, bodyMat)
  body.rotation.z = Math.PI / 2
  body.position.y = 1.5
  body.castShadow = true
  group.add(body)
  // ★ 旋转轴必须是 x，不是 z。轴已由 rotation.z=π/2 摆到世界 X 向；
  //   欧拉序 XYZ 下 R = RX·RY·RZ，继续累加 rotation.z 等于绕 Z 翻跟头
  //   （磨机变成滚筒洗衣机）。累加 rotation.x 时 RX 作用在 RZ 之外，
  //   轴向量 (-1,0,0) 绕 X 不变 → 才是真正的"绕自身轴自转"。
  rotatingParts.push({ mesh: body, axis: 'x', speed: 0.01, equipId: equip.id })

  // 筒体端盖
  const capGeo = new THREE.CylinderGeometry(1.2, 1.2, 0.15, 32)
  const capMat = new THREE.MeshPhongMaterial({ color: 0x37474f, shininess: 40 })
  ;[-2.55, 2.55].forEach(x => {
    const cap = new THREE.Mesh(capGeo, capMat)
    cap.rotation.z = Math.PI / 2
    cap.position.set(x, 1.5, 0)
    group.add(cap)
  })

  // ⚠️ 已删除两处"看不见的几何"：
  //  ① 筒内衬板条纹 —— 初版用 rotation.z 让它"绕圈"，实际是每根都斜着从筒壳里穿出来，
  //     在画面上就是一圈白色斜杆（像太阳光芒），既不贴壁也不在筒内。改成外部的
  //     环向加强箍（见下方 ⑧），语义一样是"筒体有结构"，但真的看得见。
  //     注：把条纹作为 body 子节点、用局部 Y 当轴向也能贴对，但筒体已改为不透明，
  //     贴在筒内的东西一律不可见 —— 那是"白算三角形"，不如不建。
  //  ② 筒内钢球（研磨介质）—— 同理，筒体不透明后完全不可见。已移除。

  // 轴承座
  const bearingGeo = new THREE.BoxGeometry(0.6, 0.8, 0.6)
  const bearingMat = new THREE.MeshPhongMaterial({ color: 0xffa000, shininess: 60 })
  ;[-2.8, 2.8].forEach(x => {
    const bearing = new THREE.Mesh(bearingGeo, bearingMat)
    bearing.position.set(x, 0.4, 0)
    group.add(bearing)
  })

  // 地基
  const baseGeo = new THREE.BoxGeometry(6, 0.4, 2)
  const baseMat = new THREE.MeshPhongMaterial({ color: 0x37474f })
  const base = new THREE.Mesh(baseGeo, baseMat)
  base.position.y = 0.2
  group.add(base)

  // 真齿形大齿圈（替代原来的光滑圆环）—— "齿"是传动系统最直接的语义。
  // 几何以自身圆心为原点烘焙，用 rotation.x 才能绕自己转（轴沿 X）。
  const girthList = []
  addGeos(girthList, gearGeos(1.42, 40, 0.4, { hub: false }), P(null, [0, 0, Math.PI / 2]))
  const girthGear = cluster(girthList, DETAIL_MAT.dark, 'girthGear')
  girthGear.position.set(2.55, 1.5, 0)
  group.add(girthGear)
  rotatingParts.push({ mesh: girthGear, axis: 'x', speed: 0.012, equipId: equip.id })

  // ══════════════════════════════════════════════════
  //  工业细节：3 个合批簇 + 铭牌 + 警示条 = 只多 5 个 draw call
  // ══════════════════════════════════════════════════
  const steel = []
  const galv = []
  const dark = []

  // ① 筒体对接法兰（16 螺栓）—— "筒体是分段装配的"
  for (const dx of [-1.15, 1.15]) {
    addGeos(steel, flangeGeos(1.29, { t: 0.1, bolts: 16 }), P([dx, 1.5, 0], [0, 0, Math.PI / 2]))
  }
  // ② 给矿 / 排矿端配对法兰（10 螺栓）
  for (const dx of [-2.74, 2.74]) {
    addGeos(steel, flangePairGeos(0.58, { t: 0.08, bolts: 10, boltRing: 0.72 }),
      P([dx, 1.5, 0], [0, 0, Math.PI / 2]))
  }
  // ③ 轴承座压盖螺栓
  for (const bx of [-2.8, 2.8]) {
    for (const dx of [-0.16, 0.16]) {
      for (const dz of [-0.2, 0.2]) {
        addGeos(steel, boltGeos(0.055, 0.1, [bx + dx, 0.84, dz]))
      }
    }
  }
  // ④ 底座地脚螺栓（前后两排）
  addGeos(steel, boltRowGeos(9, -2.7, 2.7, 0.44, 0.86))
  addGeos(steel, boltRowGeos(9, -2.7, 2.7, 0.44, -0.86))

  // ⑤ 筒体环向加强箍（2 道）+ 人孔盖 —— 补回"筒体是有结构的"这个语义。
  //    箍与筒体同轴（轴沿世界 X）→ 用 P(x, rotY=π/2) 把法线从 +Y 扳到 +X。
  for (const hx of [-1.85, 1.85]) {
    addGeos(steel, ringGeos(1.24), P([hx, 1.5, 0], [0, Math.PI / 2, 0]))
  }
  // 人孔盖（带 8 螺栓圈）：贴在外侧筒壁上，法线 +Z
  addGeos(steel, flangeGeos(0.3, { t: 0.07, bolts: 8 }), P([-0.55, 1.5, 1.19], [Math.PI / 2, 0, 0]))

  // ⑥ 检修平台 + 护栏 + 直爬梯（镀锌件，与机身分色）
  const platY = 2.45
  const platZ = -1.72
  addGeos(galv, platformGeos(5.6, 0.95), P([0, platY, platZ]))
  addGeos(galv, railingGeos(5.6, { h: 1.05, posts: 6 }), P([0, platY, platZ - 0.46]))
  for (const px of [-2.5, -1.25, 0, 1.25, 2.5]) {
    addGeos(galv, postGeos(0, platY, 0.055), P([px, 0, platZ - 0.3]))
  }
  addGeos(galv, ladderGeos(platY, { w: 0.5, rungs: 8, cage: true }), P([2.86, 0, platZ]))

  // ⑦ 传动组：小齿轮 + 减速机 + 联轴器护罩 + 主电机（散热片）+ 基础墩 + 稀油站
  addGeos(dark, gearGeos(0.52, 18, 0.36), P([3.15, 1.5, 0], [0, 0, Math.PI / 2]))
  addGeos(dark, blockGeos(0.45, 0.5, 0.45, [3.15, 1.25, 0]))        // 小齿轮支座
  addGeos(dark, blockGeos(1.0, 1.0, 0.95, [3.98, 1.5, 0]))          // 减速机
  addGeos(dark, blockGeos(0.5, 0.46, 0.46, [4.65, 1.5, 0]))         // 联轴器护罩
  addGeos(dark, motorGeos(0.4, 1.1, { fins: 12 }), P([5.3, 1.5, 0], [0, 0, Math.PI / 2]))
  addGeos(dark, blockGeos(2.3, 1.0, 1.4, [4.25, 0.5, 0]))           // 驱动基础墩
  addGeos(dark, blockGeos(0.8, 0.45, 0.6, [-2.1, 0.47, 1.25]))      // 稀油站撬块
  addGeos(dark, cylGeos(0.12, 0.5, [-1.7, 0.72, 1.25]))             // 油泵

  // ⑧ 筒体外部衬板螺栓阵 —— 独立一簇，但**挂在 body 下**（继承自转），
  //    这是"筒体真的在转"的唯一可见证据。坐标用 body 局部系（轴 +Y）。
  //    不能并进 steel 簇：那一簇是 group 的子节点、不跟着转，螺栓就会"浮"在转动筒体上。
  const boltList = []
  addGeos(boltList, shellBoltGeos(1.21, { rows: 9, perRow: 7 }))
  const shellBolts = cluster(boltList, DETAIL_MAT.steel, 'detail-shellbolts')
  if (shellBolts) body.add(shellBolts)

  const steelMesh = cluster(steel, DETAIL_MAT.steel, 'detail-steel')
  if (steelMesh) group.add(steelMesh)
  const galvMesh = cluster(galv, DETAIL_MAT.galv, 'detail-galv')
  if (galvMesh) group.add(galvMesh)
  const darkMesh = cluster(dark, DETAIL_MAT.dark, 'detail-dark')
  if (darkMesh) group.add(darkMesh)

  // ⑨ 铭牌（名称 + 位号）+ 黄黑警示条
  // ⚠️ 必须贴在"基座(高 0.24)以上"的面：设备自身底板只有 0.4 高、下缘被基座吃掉，
  //    贴在底板上等于看不见。驱动基础墩正面高 1.0，是唯一完整可见的平整面。
  const plate = nameplateMesh(`${equip.name} · ${equip.location_tag || ''}`, 0.95, 0.26)
  plate.position.set(3.85, 0.62, 0.71)
  group.add(plate)
  const stripe = hazardStripeMesh(0.7, 0.02, 0.12)
  stripe.position.set(4.95, 0.62, 0.71)
  group.add(stripe)

  addStatusLight(group, equip.status, 3.6)
  group.userData = { equipId: equip.id, equip }
  return group
}

/** 创建渣浆泵 */
function createSlurryPump(equip) {
  const group = new THREE.Group()

  // 泵壳（蜗壳形状用椭球近似）
  const shellGeo = new THREE.SphereGeometry(0.9, 24, 18, 0, Math.PI * 2, 0, Math.PI * 0.7)
  const shellMat = new THREE.MeshPhongMaterial({
    color: 0x26a69a, shininess: 70, transparent: true, opacity: 0.85
  })
  const shell = new THREE.Mesh(shellGeo, shellMat)
  shell.scale.set(1, 0.8, 1.1)
  shell.position.y = 0.7
  shell.castShadow = true
  group.add(shell)

  // 泵座
  const seatGeo = new THREE.CylinderGeometry(1, 1.15, 0.4, 24)
  const seatMat = new THREE.MeshPhongMaterial({ color: 0x37474f })
  const seat = new THREE.Mesh(seatGeo, seatMat)
  seat.position.y = 0.2
  group.add(seat)

  // 进口法兰
  const flangeGeo = new THREE.CylinderGeometry(0.35, 0.35, 0.12, 20)
  const flangeMat = new THREE.MeshPhongMaterial({ color: 0x78909c, shininess: 50 })
  const inletFlange = new THREE.Mesh(flangeGeo, flangeMat)
  inletFlange.rotation.x = Math.PI / 2
  inletFlange.position.set(0, 0.9, 1)
  group.add(inletFlange)

  // 出口管
  const outPipeGeo = new THREE.CylinderGeometry(0.25, 0.25, 1.2, 16)
  const outPipe = new THREE.Mesh(outPipeGeo, flangeMat)
  outPipe.rotation.z = Math.PI / 2
  outPipe.position.set(1, 0.9, 0)
  group.add(outPipe)

  // 电机
  // ⚠️ 原码是**竖直**圆柱且悬在 y 0.4~1.4 —— 泵的驱动必须是水平的（轴与泵轴同轴），
  //    而且要有底座。这里改水平 + 补电机支座，否则整台泵读起来像"球上插了根柱子"。
  const motorGeo = new THREE.CylinderGeometry(0.4, 0.4, 0.95, 20)
  const motorMat = new THREE.MeshPhongMaterial({ color: 0x5c6bc0, shininess: 60 })
  const motor = new THREE.Mesh(motorGeo, motorMat)
  motor.rotation.z = Math.PI / 2
  motor.position.set(-2.05, 0.9, 0)
  group.add(motor)

  // 联轴器（挪到泵壳之外 —— 原来藏在蜗壳里，等于不存在）
  const couplingGeo = new THREE.CylinderGeometry(0.15, 0.15, 0.4, 12)
  const couplingMat = new THREE.MeshPhongMaterial({ color: 0xffb74d, shininess: 80 })
  const coupling = new THREE.Mesh(couplingGeo, couplingMat)
  coupling.rotation.z = Math.PI / 2
  coupling.position.set(-1.35, 0.9, 0)
  group.add(coupling)
  rotatingParts.push({ mesh: coupling, axis: 'z', speed: 0.05, equipId: equip.id })

  // 叶轮指示（内部）
  const impellerHub = new THREE.CylinderGeometry(0.2, 0.2, 0.15, 12)
  const impellerMat = new THREE.MeshPhongMaterial({ color: 0xef5350 })
  const hub = new THREE.Mesh(impellerHub, impellerMat)
  hub.position.set(0, 0.5, 0)
  group.add(hub)
  rotatingParts.push({ mesh: hub, axis: 'y', speed: 0.06, equipId: equip.id })

  // ══════════════════════════════════════════════════
  //  工业细节：3 个合批簇 + 铭牌 + 警示条
  // ══════════════════════════════════════════════════
  const steel = []
  const galv = []
  const dark = []

  // ① 泵壳分半面螺栓圈（贴在蜗壳正面）—— 蜗壳泵的"脸"，最一眼认出的特征
  addGeos(steel, flangeGeos(0.6, { t: 0.07, bolts: 10 }), P([0, 0.72, 0.92], [Math.PI / 2, 0, 0]))
  // ② 进口 / 出口配对法兰 —— "这是可拆的接管"
  addGeos(steel, flangePairGeos(0.36, { t: 0.07, bolts: 10 }), P([0, 0.9, 1.2], [Math.PI / 2, 0, 0]))
  addGeos(steel, flangePairGeos(0.3, { t: 0.06, bolts: 8 }), P([1.62, 0.9, 0], [0, 0, Math.PI / 2]))
  // ③ 轴承箱 + 支座（泵壳与电机之间，真泵这一段是独立部件）
  addGeos(dark, cylGeos(0.26, 0.42, [-0.98, 0.9, 0], [0, 0, Math.PI / 2]))
  addGeos(dark, blockGeos(0.44, 0.5, 0.6, [-0.98, 0.5, 0]))
  // ④ 联轴器护罩：做成"底板 + 两侧板"的槽形，别把联轴器整个盖死（否则又变成看不见的几何）
  addGeos(galv, blockGeos(0.5, 0.03, 0.42, [-1.35, 0.6, 0]))
  addGeos(galv, blockGeos(0.5, 0.42, 0.03, [-1.35, 0.8, 0.2]))
  addGeos(galv, blockGeos(0.5, 0.42, 0.03, [-1.35, 0.8, -0.2]))
  // ⑤ 电机支座（原来电机悬空）+ 电机散热片（原来是个光圆柱）
  //    支座高 0.42（y 0.24~0.66）：底座比 0.24 矮的部分会被混凝土基座吃掉，
  //    而电机半径 0.4、轴心在 0.9，支面再高就把电机埋掉一半。
  addGeos(dark, blockGeos(1.15, 0.42, 0.9, [-2.0, 0.45, 0]))
  for (let i = 0; i < 10; i++) {
    const a = (i / 10) * Math.PI * 2
    addGeos(dark, blockGeos(0.78, 0.04, 0.1,
      [-2.05, 0.9 + Math.cos(a) * 0.44, Math.sin(a) * 0.44], [a, 0, 0]))
  }
  // ⑥ 出口压力表（表管 + 表盘）
  addGeos(galv, cylGeos(0.03, 0.34, [1.0, 1.32, 0]))
  addGeos(galv, cylGeos(0.11, 0.06, [1.0, 1.5, 0]))

  const steelMesh = cluster(steel, DETAIL_MAT.steel, 'detail-steel')
  if (steelMesh) group.add(steelMesh)
  const galvMesh = cluster(galv, DETAIL_MAT.galv, 'detail-galv')
  if (galvMesh) group.add(galvMesh)
  const darkMesh = cluster(dark, DETAIL_MAT.dark, 'detail-dark')
  if (darkMesh) group.add(darkMesh)

  // ⑦ 铭牌 + 警示条：贴电机支座正面（高 0.42，y>0.24，是唯一完整可见的平整面）
  const plate = nameplateMesh(`${equip.name} · ${equip.location_tag || ''}`, 0.68, 0.18)
  plate.position.set(-2.0, 0.52, 0.46)
  group.add(plate)
  const stripe = hazardStripeMesh(0.6, 0.02, 0.1)
  stripe.position.set(-2.0, 0.33, 0.46)
  group.add(stripe)

  addStatusLight(group, equip.status, 1.8)
  group.userData = { equipId: equip.id, equip }
  return group
}

/** 创建皮带输送机 */
function createConveyor(equip) {
  const group = new THREE.Group()

  // 机架（长梁）
  // ⚠️ 原码的竖直关系是错的：机架 1.2 / 托辊 1.0 / 皮带 1.32 —— 托辊吊在皮带**下面一截**，
  //    根本托不到带，远看像三根互不相干的横杆。正确关系是「机架在下 → 托辊在机架上 →
  //    皮带骑在托辊上」。这里把机架压到 1.08、托辊抬到 1.20（托辊顶 = 皮带底 1.28）。
  const frameGeo = new THREE.BoxGeometry(8, 0.15, 0.8)
  const frameMat = new THREE.MeshPhongMaterial({ color: 0x5d4037, shininess: 30 })
  const frame = new THREE.Mesh(frameGeo, frameMat)
  frame.position.y = 1.08
  frame.castShadow = true
  group.add(frame)

  // 托辊
  const rollerGeo = new THREE.CylinderGeometry(0.08, 0.08, 0.7, 10)
  const rollerMat = new THREE.MeshPhongMaterial({ color: 0xb0bec5, shininess: 80 })
  for (let x = -3.5; x <= 3.5; x += 1) {
    const roller = new THREE.Mesh(rollerGeo, rollerMat)
    roller.rotation.x = Math.PI / 2
    roller.position.set(x, 1.2, 0)
    group.add(roller)
    rotatingParts.push({ mesh: roller, axis: 'z', speed: 0.04, equipId: equip.id })
  }

  // 皮带
  const beltGeo = new THREE.BoxGeometry(7.8, 0.08, 0.55)
  const beltMat = new THREE.MeshPhongMaterial({ color: 0x283593, shininess: 40 })
  const belt = new THREE.Mesh(beltGeo, beltMat)
  belt.position.y = 1.32
  group.add(belt)

  // 物料底层（皮带上的矿石静态层）
  const oreGeo = new THREE.BoxGeometry(5, 0.15, 0.4)
  // ⚠️ MeshPhongMaterial **没有 roughness 属性**（那是 Standard/Physical 的）。
  //    之前写成 roughness: 0.9 → three 每次实例化都打一条
  //    "'roughness' is not a property of THREE.MeshPhongMaterial" 警告，且参数被静默忽略。
  //    Phong 的世界里"粗糙"对应低 shininess：矿石要哑光 → shininess 8（默认 30）。
  const oreMat = new THREE.MeshPhongMaterial({ color: 0x795548, shininess: 8 })
  const ore = new THREE.Mesh(oreGeo, oreMat)
  ore.position.set(-0.5, 1.43, 0)
  group.add(ore)

  // 流动矿料颗粒（在皮带表面循环移动）
  const particleGeo = new THREE.SphereGeometry(0.07, 6, 5)
  const particleMat = new THREE.MeshPhongMaterial({ color: 0x6d4c41 })
  for (let i = 0; i < 18; i++) {
    const p = new THREE.Mesh(particleGeo, particleMat)
    p.position.set(-3.5 + (i / 18) * 7, 1.5, (Math.random() - 0.5) * 0.32)
    group.add(p)
    conveyorParticles.push({ mesh: p, baseZ: p.position.z, phase: Math.random() * 6.28, equipId: equip.id })
  }

  // 支腿
  const legGeo = new THREE.CylinderGeometry(0.06, 0.08, 1.0, 8)
  const legMat = new THREE.MeshPhongMaterial({ color: 0x607d8b })
  ;[-3.5, 0, 3.5].forEach(x => {
    ;[-0.35, 0.35].forEach(z => {
      const leg = new THREE.Mesh(legGeo, legMat)
      leg.position.set(x, 0.5, z)
      group.add(leg)
    })
  })

  // 头部滚筒
  const drumGeo = new THREE.CylinderGeometry(0.2, 0.2, 0.85, 18)
  const drumMat = new THREE.MeshPhongMaterial({ color: 0xffa000, shininess: 60 })
  const headDrum = new THREE.Mesh(drumGeo, drumMat)
  headDrum.rotation.x = Math.PI / 2
  headDrum.position.set(4, 1.16, 0)
  group.add(headDrum)
  rotatingParts.push({ mesh: headDrum, axis: 'z', speed: 0.04, equipId: equip.id })

  // 尾部滚筒
  const tailDrum = new THREE.Mesh(drumGeo, drumMat)
  tailDrum.rotation.x = Math.PI / 2
  tailDrum.position.set(-4, 1.16, 0)
  group.add(tailDrum)
  rotatingParts.push({ mesh: tailDrum, axis: 'z', speed: 0.04, equipId: equip.id })

  // 张紧装置
  const tensionGeo = new THREE.BoxGeometry(0.5, 0.6, 0.4)
  const tensionMat = new THREE.MeshPhongMaterial({ color: 0xff5722 })
  const tension = new THREE.Mesh(tensionGeo, tensionMat)
  tension.position.set(-4.3, 0.8, 0)
  group.add(tension)

  // ══════════════════════════════════════════════════
  //  工业细节：3 个合批簇 + 铭牌 + 警示条
  // ══════════════════════════════════════════════════
  const steel = []
  const galv = []
  const dark = []

  // ① 机架桁架化：腿间斜撑（之字形：x=0 处为峰，两端为谷）+ 每对腿的横向连梁
  //    原来 6 根光腿杵着 → 一眼"积木"。桁架是输送机最标志性的骨架语言。
  for (const z of [-0.35, 0.35]) {
    addGeos(galv, rodGeos([-3.5, 0.14, z], [0, 1.0, z], 0.035))
    addGeos(galv, rodGeos([0, 1.0, z], [3.5, 0.14, z], 0.035))
    addGeos(galv, blockGeos(0.07, 0.07, 0.7, [-3.5, 0.98, 0]))
    addGeos(galv, blockGeos(0.07, 0.07, 0.7, [0, 0.98, 0]))
    addGeos(galv, blockGeos(0.07, 0.07, 0.7, [3.5, 0.98, 0]))
  }
  // ② 托辊支架（每个托辊两端各一个轴承座）—— 托辊"挂在空中"是最假的细节
  for (let x = -3.5; x <= 3.5; x += 1) {
    for (const z of [-0.38, 0.38]) {
      addGeos(steel, blockGeos(0.13, 0.17, 0.07, [x, 1.18, z]))
    }
  }
  // ③ 尾部导料槽 + 防尘罩（受料点）—— 槽帮 + 罩顶
  addGeos(steel, blockGeos(1.7, 0.55, 0.06, [-3.1, 1.68, 0.42]))
  addGeos(steel, blockGeos(1.7, 0.55, 0.06, [-3.1, 1.68, -0.42]))
  addGeos(galv, blockGeos(1.7, 0.05, 0.9, [-3.1, 1.97, 0]))
  // ④ 头部清扫器（刮板 + 支臂）—— "皮带上不返料"的语义
  addGeos(steel, blockGeos(0.07, 0.12, 0.62, [4.24, 1.0, 0]))
  addGeos(steel, blockGeos(0.32, 0.05, 0.05, [4.06, 1.0, 0.28]))
  addGeos(steel, blockGeos(0.32, 0.05, 0.05, [4.06, 1.0, -0.28]))
  // ⑤ 拉绳开关 + 沿线的拉绳（含支架）—— 长距离输送机的标配安全件
  for (const x of [-3.5, 0, 3.5]) {
    addGeos(galv, blockGeos(0.06, 0.06, 0.16, [x, 0.8, 0.47]))
  }
  addGeos(galv, cylGeos(0.014, 7.4, [0, 0.8, 0.54], [0, 0, Math.PI / 2]))
  addGeos(dark, blockGeos(0.17, 0.26, 0.15, [-2.4, 0.8, 0.54]))
  addGeos(dark, blockGeos(0.17, 0.26, 0.15, [2.4, 0.8, 0.54]))
  // ⑥ 支腿底脚板 + 地脚螺栓（落在基座顶面 y=0.24 之上）
  for (const x of [-3.5, 0, 3.5]) {
    for (const z of [-0.35, 0.35]) {
      addGeos(steel, blockGeos(0.3, 0.05, 0.3, [x, 0.27, z]))
      addGeos(steel, boltGeos(0.04, 0.07, [x - 0.1, 0.32, z - 0.1]))
      addGeos(steel, boltGeos(0.04, 0.07, [x + 0.1, 0.32, z + 0.1]))
    }
  }

  const steelMesh = cluster(steel, DETAIL_MAT.steel, 'detail-steel')
  if (steelMesh) group.add(steelMesh)
  const galvMesh = cluster(galv, DETAIL_MAT.galv, 'detail-galv')
  if (galvMesh) group.add(galvMesh)
  const darkMesh = cluster(dark, DETAIL_MAT.dark, 'detail-dark')
  if (darkMesh) group.add(darkMesh)

  // ⑦ 铭牌 + 警示条：贴导料槽的 +Z 槽帮（唯一一块又大又平、且不会被基座吃掉的面）
  const plate = nameplateMesh(`${equip.name} · ${equip.location_tag || ''}`, 0.76, 0.2)
  plate.position.set(-3.1, 1.78, 0.455)
  group.add(plate)
  const stripe = hazardStripeMesh(0.66, 0.02, 0.1)
  stripe.position.set(-3.1, 1.53, 0.455)
  group.add(stripe)

  addStatusLight(group, equip.status, 2.4)
  group.userData = { equipId: equip.id, equip }
  return group
}

/** 创建浓密机 */
function createThickener(equip) {
  const group = new THREE.Group()

  // 池体（大圆柱）
  const tankGeo = new THREE.CylinderGeometry(2.2, 2, 1.5, 36)
  const tankMat = new THREE.MeshStandardMaterial({
    color: 0x0277bd, transparent: true, opacity: 0.6, roughness: 0.5, metalness: 0.4
  })
  const tank = new THREE.Mesh(tankGeo, tankMat)
  tank.position.y = 0.75
  tank.castShadow = true
  group.add(tank)

  // 池壁加强圈
  const ringGeo = new THREE.TorusGeometry(2.1, 0.06, 8, 48)
  const ringMat = new THREE.MeshPhongMaterial({ color: 0x01579b, shininess: 60 })
  const ring = new THREE.Mesh(ringGeo, ringMat)
  ring.rotation.x = Math.PI / 2
  ring.position.y = 1.4
  group.add(ring)

  // 中心柱
  const columnGeo = new THREE.CylinderGeometry(0.15, 0.2, 3, 16)
  const columnMat = new THREE.MeshPhongMaterial({ color: 0x90a4ae, shininess: 50 })
  const column = new THREE.Mesh(columnGeo, columnMat)
  column.position.y = 1.5
  group.add(column)

  // 耙臂
  const armGeo = new THREE.BoxGeometry(2, 0.06, 0.12)
  const armMat = new THREE.MeshPhongMaterial({ color: 0xbdbdbd, shininess: 40 })
  for (let i = 0; i < 4; i++) {
    const arm = new THREE.Mesh(armGeo, armMat)
    arm.position.y = 0.3
    arm.rotation.y = (Math.PI / 2) * i
    arm.translateX(1)
    group.add(arm)
    rotatingParts.push({ mesh: arm, axis: 'y', speed: 0.005, equipId: equip.id })
  }

  // 中心驱动桥
  // ⚠️ 原码桥长 2.5、起点在 x=-0.15 → 只是"从中心伸到池边"的半截悬臂，两端无支承，
  //    走道也不成立。真浓密机是**全跨桥**：两端搭在池壁上，驱动台车在池边（周边传动）。
  const bridgeGeo = new THREE.BoxGeometry(4.6, 0.15, 0.3)
  const bridgeMat = new THREE.MeshPhongMaterial({ color: 0x78909c })
  const bridge = new THREE.Mesh(bridgeGeo, bridgeMat)
  bridge.position.set(0, 2.8, 0)
  group.add(bridge)

  // 驱动装置
  const driveGeo = new THREE.BoxGeometry(0.5, 0.6, 0.5)
  const driveMat = new THREE.MeshPhongMaterial({ color: 0xffb300, shininess: 60 })
  const drive = new THREE.Mesh(driveGeo, driveMat)
  drive.position.set(2.3, 2.65, 0)
  group.add(drive)

  // ⚠️ 已删除"底流锥"：它挂在 y=-0.4，整个埋在混凝土基座(0~0.24)里，
  //    只白算三角形、画面上一个像素都不产生。底流改由外接管 + 阀门表达（见下）。

  // 矿浆液面（波动）
  const liquidGeo = new THREE.PlaneGeometry(3.8, 3.8, 12, 12)
  const liquidMat = new THREE.MeshPhongMaterial({
    color: 0x37474f, transparent: true, opacity: 0.7,
    shininess: 60, side: THREE.DoubleSide, depthWrite: false
  })
  const liquid = new THREE.Mesh(liquidGeo, liquidMat)
  liquid.rotation.x = -Math.PI / 2
  liquid.position.y = 1.2
  liquid.renderOrder = 2
  group.add(liquid)
  liquidSurfaces.push({ mesh: liquid, baseY: 1.2, phase: Math.random() * 6.28, amp: 0.05, k: 1, equipId: equip.id })

  // 溢流槽
  const launderGeo = new THREE.TorusGeometry(2.35, 0.12, 8, 48)
  const launderMat = new THREE.MeshPhongMaterial({ color: 0x4fc3f7, shininess: 60 })
  const launder = new THREE.Mesh(launderGeo, launderMat)
  launder.rotation.x = Math.PI / 2
  launder.position.y = 1.45
  group.add(launder)

  // ══════════════════════════════════════════════════
  //  工业细节：3 个合批簇 + 1 个会转的耙架桁架 + 铭牌 + 警示条
  // ══════════════════════════════════════════════════
  const steel = []
  const galv = []
  const dark = []

  // ① 溢流堰齿板（周边锯齿堰）：30 片小竖板立在池沿上 —— 浓密机最标志性的细节
  for (let i = 0; i < 30; i++) {
    const a = (i / 30) * Math.PI * 2
    addGeos(steel, blockGeos(0.05, 0.2, 0.04,
      [Math.cos(a) * 2.24, 1.62, Math.sin(a) * 2.24], [0, -a, 0]))
  }
  // ② 全跨走道 + 双侧护栏 + 支柱 + 直爬梯（含护笼）
  const walkY = 3.02
  addGeos(galv, platformGeos(4.7, 0.85), P([0.15, walkY, 0]))
  addGeos(galv, railingGeos(4.7, { h: 1.05, posts: 6 }), P([0.15, walkY, 0.46]))
  addGeos(galv, railingGeos(4.7, { h: 1.05, posts: 6 }), P([0.15, walkY, -0.46]))
  addGeos(galv, postGeos(0, walkY, 0.055), P([-2.15, 0, 0.42]))
  addGeos(galv, postGeos(0, walkY, 0.055), P([-2.15, 0, -0.42]))
  addGeos(galv, ladderGeos(walkY, { w: 0.45, rungs: 9, cage: true }), P([2.5, 0, 0]))
  // ③ 底流管 + 配对法兰 + 闸阀 + 手轮 + 管墩（替代被删掉的埋地底流锥）
  addGeos(steel, cylGeos(0.16, 1.5, [2.5, 0.5, 0], [0, 0, Math.PI / 2]))
  addGeos(steel, flangePairGeos(0.2, { t: 0.05, bolts: 8 }), P([3.0, 0.5, 0], [0, 0, Math.PI / 2]))
  addGeos(dark, cylGeos(0.15, 0.3, [3.4, 0.5, 0], [0, 0, Math.PI / 2]))
  addGeos(steel, cylGeos(0.04, 0.32, [3.4, 0.72, 0]))
  addGeos(dark, handwheelGeos(0.2), P([3.4, 0.9, 0]))
  addGeos(dark, blockGeos(0.4, 0.26, 0.5, [3.0, 0.37, 0]))
  // ④ 中心柱顶部的提耙/传动端（原来柱子是一根光杆戳到底）
  addGeos(dark, blockGeos(0.5, 0.34, 0.5, [0, 3.17, 0]))

  const steelMesh = cluster(steel, DETAIL_MAT.steel, 'detail-steel')
  if (steelMesh) group.add(steelMesh)
  const galvMesh = cluster(galv, DETAIL_MAT.galv, 'detail-galv')
  if (galvMesh) group.add(galvMesh)
  const darkMesh = cluster(dark, DETAIL_MAT.dark, 'detail-dark')
  if (darkMesh) group.add(darkMesh)

  // ⑤ 耙架桁架（4 根斜拉杆）：真浓密机的耙臂不是悬臂，上面有拉杆吊着。
  //    ★ 四根合批成 **1 个 Mesh**，几何各自以"池中心"为原点预摆好，
  //      于是整簇绕自身 origin 转 = 跟着耙臂一起转，只多 1 个 draw call。
  //      （若拆成 4 个独立 Mesh 塞进 rotatingParts，每根都会绕自己的中心转 → 散架）
  const trussList = []
  for (let i = 0; i < 4; i++) {
    const a = (Math.PI / 2) * i
    addGeos(trussList, rodGeos(
      [0, 2.35, 0],
      [Math.cos(a) * 1.7, 0.36, Math.sin(a) * 1.7], 0.035))
  }
  const rakeTruss = cluster(trussList, DETAIL_MAT.steel, 'detail-raketruss')
  if (rakeTruss) {
    group.add(rakeTruss)
    rotatingParts.push({ mesh: rakeTruss, axis: 'y', speed: 0.005, equipId: equip.id })   // 与耙臂同速
  }

  // ⑥ 铭牌 + 警示条：贴驱动装置(高 0.6)的 +Z 正面
  const plate = nameplateMesh(`${equip.name} · ${equip.location_tag || ''}`, 0.56, 0.16)
  plate.position.set(2.3, 2.72, 0.26)
  group.add(plate)
  const stripe = hazardStripeMesh(0.5, 0.02, 0.1)
  stripe.position.set(2.3, 2.47, 0.26)
  group.add(stripe)

  addStatusLight(group, equip.status, 4.25)
  group.userData = { equipId: equip.id, equip }
  return group
}

/** 创建搅拌槽 */
function createAgitator(equip) {
  const group = new THREE.Group()

  // 槽体（带锥底的圆柱）
  const tankGeo = new THREE.CylinderGeometry(1.4, 1.1, 2.2, 28)
  const tankMat = new THREE.MeshPhongMaterial({
    color: 0x6d4c41, transparent: true, opacity: 0.8, shininess: 30
  })
  const tank = new THREE.Mesh(tankGeo, tankMat)
  tank.position.y = 1.3
  tank.castShadow = true
  group.add(tank)

  // 锥底
  const coneGeo = new THREE.ConeGeometry(1.1, 0.6, 28)
  const cone = new THREE.Mesh(coneGeo, tankMat)
  cone.position.y = 0.4
  group.add(cone)

  // 搅拌轴
  const shaftGeo = new THREE.CylinderGeometry(0.06, 0.06, 3.2, 12)
  const shaftMat = new THREE.MeshPhongMaterial({ color: 0xcfd8dc, shininess: 80 })
  const shaft = new THREE.Mesh(shaftGeo, shaftMat)
  shaft.position.y = 2.4
  group.add(shaft)
  rotatingParts.push({ mesh: shaft, axis: 'y', speed: 0.04, equipId: equip.id })

  // 桨叶（双层）
  const bladeGeo = new THREE.BoxGeometry(0.9, 0.06, 0.18)
  const bladeMat = new THREE.MeshPhongMaterial({ color: 0x8d6e63, shininess: 40 })
  ;[1.6, 0.9].forEach(y => {
    for (let i = 0; i < 4; i++) {
      const blade = new THREE.Mesh(bladeGeo, bladeMat)
      blade.position.y = y
      blade.rotation.y = (Math.PI / 2) * i + (y === 1.6 ? Math.PI / 4 : 0)
      group.add(blade)
      rotatingParts.push({ mesh: blade, axis: 'y', speed: 0.04, equipId: equip.id })
    }
  })

  // 挡板（4块固定在槽壁）
  const baffleGeo = new THREE.BoxGeometry(0.08, 1.8, 0.15)
  const baffleMat = new THREE.MeshPhongMaterial({ color: 0x3e2723 })
  for (let i = 0; i < 4; i++) {
    const baffle = new THREE.Mesh(baffleGeo, baffleMat)
    const angle = (Math.PI / 2) * i
    baffle.position.set(Math.cos(angle) * 1.32, 1.4, Math.sin(angle) * 1.32)
    baffle.rotation.y = angle
    group.add(baffle)
  }

  // 矿浆液面（波动）
  const liquidGeo = new THREE.PlaneGeometry(2.6, 2.6, 10, 10)
  const liquidMat = new THREE.MeshPhongMaterial({
    color: 0x5d4037, transparent: true, opacity: 0.75,
    shininess: 50, side: THREE.DoubleSide, depthWrite: false
  })
  const liquid = new THREE.Mesh(liquidGeo, liquidMat)
  liquid.rotation.x = -Math.PI / 2
  liquid.position.y = 2.0
  liquid.renderOrder = 2
  group.add(liquid)
  liquidSurfaces.push({ mesh: liquid, baseY: 2.0, phase: Math.random() * 6.28, amp: 0.04, k: 1, equipId: equip.id })

  // 加药管
  const dosingGeo = new THREE.CylinderGeometry(0.04, 0.04, 1.5, 8)
  const dosingMat = new THREE.MeshPhongMaterial({ color: 0x66bb6a })
  const dosing = new THREE.Mesh(dosingGeo, dosingMat)
  dosing.position.set(1.6, 2.5, 0)
  group.add(dosing)

  // 电机支架
  const mountGeo = new THREE.BoxGeometry(0.6, 0.15, 0.6)
  const mountMat = new THREE.MeshPhongMaterial({ color: 0x455a64 })
  const mount = new THREE.Mesh(mountGeo, mountMat)
  mount.position.y = 3.8
  group.add(mount)

  // 电机
  const motorGeo = new THREE.CylinderGeometry(0.25, 0.25, 0.6, 16)
  const motorMat = new THREE.MeshPhongMaterial({ color: 0x5c6bc0, shininess: 60 })
  const motor = new THREE.Mesh(motorGeo, motorMat)
  motor.position.y = 4.15
  group.add(motor)

  // ══════════════════════════════════════════════════
  //  工业细节：3 个合批簇 + 铭牌 + 警示条
  // ══════════════════════════════════════════════════
  const steel = []
  const galv = []
  const dark = []

  // ① 槽壁环向加强箍（3 道）—— 槽体是锥形，箍半径必须跟着该高度处的实际半径走
  for (const y of [0.9, 1.5, 2.1]) {
    const r = 1.1 + 0.3 * (y - 0.2) / 2.2
    addGeos(steel, ringGeos(r + 0.03), P([0, y, 0], [Math.PI / 2, 0, 0]))
  }
  // ② 槽壁人孔（带螺栓圈）—— 液体罐类设备的标配
  addGeos(steel, flangeGeos(0.26, { t: 0.06, bolts: 8 }), P([0, 1.5, 1.28], [Math.PI / 2, 0, 0]))
  // ③ 加药管：配对法兰 ×2 + 电磁流量计（加药量要计量，这是真工艺需求，不是装饰）
  addGeos(steel, flangePairGeos(0.09, { t: 0.04, bolts: 6 }), P([1.6, 2.44, 0]))
  addGeos(steel, flangePairGeos(0.09, { t: 0.04, bolts: 6 }), P([1.6, 2.76, 0]))
  addGeos(galv, cylGeos(0.1, 0.24, [1.6, 2.6, 0]))
  // ④ 侧出料管 + 闸阀 + 手轮 —— 锥底出口整个埋在基座里，只能从侧面出料
  addGeos(steel, cylGeos(0.1, 1.2, [1.2, 0.5, 0], [0, 0, Math.PI / 2]))
  addGeos(dark, cylGeos(0.15, 0.28, [1.85, 0.5, 0], [0, 0, Math.PI / 2]))
  addGeos(steel, cylGeos(0.04, 0.3, [1.85, 0.72, 0]))
  addGeos(dark, handwheelGeos(0.2), P([1.85, 0.9, 0]))
  // ⑤ 顶部检修平台 + 护栏 + 支柱 + 直爬梯
  const walkY = 2.6
  addGeos(galv, platformGeos(2.2, 0.9), P([0, walkY, 1.95]))
  addGeos(galv, railingGeos(2.2, { h: 1.05, posts: 3 }), P([0, walkY, 2.4]))
  addGeos(galv, postGeos(0, walkY, 0.055), P([-0.95, 0, 2.3]))
  addGeos(galv, postGeos(0, walkY, 0.055), P([0.95, 0, 2.3]))
  addGeos(galv, ladderGeos(walkY, { w: 0.45, rungs: 8 }), P([1.45, 0, 2.1]))
  // ⑥ 搅拌驱动十字桥架（电机原来只靠一块 0.15 厚的小板吊在槽顶上）+ 电机散热片
  addGeos(dark, blockGeos(3.0, 0.16, 0.24, [0, 2.5, 0]))
  addGeos(dark, blockGeos(0.24, 0.16, 3.0, [0, 2.5, 0]))
  for (let i = 0; i < 10; i++) {
    const a = (i / 10) * Math.PI * 2
    addGeos(dark, blockGeos(0.03, 0.5, 0.09,
      [Math.cos(a) * 0.29, 4.15, Math.sin(a) * 0.29], [0, -a, 0]))
  }
  // ⑦ 平台外沿挡板（给铭牌一块又大又平的可见面）
  addGeos(galv, blockGeos(2.3, 0.42, 0.06, [0, 2.35, 2.4]))

  const steelMesh = cluster(steel, DETAIL_MAT.steel, 'detail-steel')
  if (steelMesh) group.add(steelMesh)
  const galvMesh = cluster(galv, DETAIL_MAT.galv, 'detail-galv')
  if (galvMesh) group.add(galvMesh)
  const darkMesh = cluster(dark, DETAIL_MAT.dark, 'detail-dark')
  if (darkMesh) group.add(darkMesh)

  // ⑧ 铭牌 + 警示条
  const plate = nameplateMesh(`${equip.name} · ${equip.location_tag || ''}`, 0.8, 0.2)
  plate.position.set(0, 2.44, 2.44)
  group.add(plate)
  const stripe = hazardStripeMesh(0.7, 0.02, 0.1)
  stripe.position.set(0, 2.2, 2.44)
  group.add(stripe)

  addStatusLight(group, equip.status, 4.6)
  group.userData = { equipId: equip.id, equip }
  return group
}

// ══════════════════════════════════════════════════════════════════
//  水处理段设备（2026-10-17 新增，把水系统接入工艺链）
//  对应后端 water.py 的 W3(尾矿浓密溢流) / W5(回水池) 站点，
//  以及 PAM 投加（W3 的 focus 里明确写了"PAM 残留"）。
// ══════════════════════════════════════════════════════════════════

/** 创建辐流式水池（尾矿溢流水池 / 回水池共用）
 *
 *  为什么做成"半敞开 + 可见水面"而不是封闭罐：
 *  水处理池在真实厂区就是敞口砼池，一眼能认出"这是池子不是反应罐"；
 *  而且水面能承载波动动画，与浓密机的液面共用同一套 liquidSurfaces 机制。
 *
 *  ⚠️ 池壁用 CylinderGeometry 的 openEnded=false 会变成一个实心柱，
 *     看起来就是个水泥墩子。必须用 openEnded=true 并单独加底，
 *     否则内部水面被池壁完全遮住 —— 那就白做了一个池子。
 */
function createWaterPool(equip, opts = {}) {
  const group = new THREE.Group()
  const R = opts.radius || 2.6          // 池半径
  const H = opts.height || 1.5          // 池深（池壁高）
  const waterR = R - 0.18               // 水面半径（略小于池壁内径）

  // ① 池壁：开口圆柱 + 双面渲染（内壁也要看得见）
  const wallMat = new THREE.MeshPhongMaterial({
    color: 0x8d9aa5, shininess: 18, side: THREE.DoubleSide,
  })
  const wall = new THREE.Mesh(
    new THREE.CylinderGeometry(R, R, H, 48, 1, true), wallMat)
  wall.position.y = H / 2
  wall.castShadow = true
  group.add(wall)

  // ② 池底
  const bottom = new THREE.Mesh(
    new THREE.CylinderGeometry(R, R, 0.12, 48), wallMat)
  bottom.position.y = 0.06
  group.add(bottom)

  // ③ 池壁顶圈（带厚度，池沿才不"纸片"）
  const rim = new THREE.Mesh(
    new THREE.TorusGeometry(R, 0.09, 10, 56),
    new THREE.MeshPhongMaterial({ color: 0x9eabb5, shininess: 40 }))
  rim.rotation.x = Math.PI / 2
  rim.position.y = H
  group.add(rim)

  // ④ 水面（波动，复用浓密机的机制）
  const waterY = H * 0.72
  const water = new THREE.Mesh(
    new THREE.PlaneGeometry(waterR * 2, waterR * 2, 14, 14),
    new THREE.MeshPhongMaterial({
      color: opts.waterColor || 0x2f4858, transparent: true, opacity: 0.82,
      shininess: 90, side: THREE.DoubleSide, depthWrite: false,
    }))
  water.rotation.x = -Math.PI / 2
  water.position.y = waterY
  water.renderOrder = 2
  group.add(water)
  liquidSurfaces.push({ mesh: water, baseY: waterY, phase: Math.random() * 6.28, amp: 0.035, k: 1, equipId: equip.id })

  // ⑤ 出水堰 + 锯齿堰板（池子的标志性细节，也解释"水从哪出去"）
  for (let i = 0; i < 28; i++) {
    const a = (i / 28) * Math.PI * 2
    const tooth = new THREE.Mesh(
      new THREE.BoxGeometry(0.06, 0.18, 0.04),
      new THREE.MeshPhongMaterial({ color: 0x9eabb5 }))
    tooth.position.set(Math.cos(a) * (R - 0.1), H + 0.1, Math.sin(a) * (R - 0.1))
    tooth.rotation.y = -a
    group.add(tooth)
  }

  // ⑥ 走道 + 护栏 + 爬梯（合批）
  const galv = []
  const steel = []
  const dark = []
  const walkY = H + 0.02
  addGeos(galv, platformGeos(R * 2 + 0.5, 0.8), P([0, walkY, 0]))
  addGeos(galv, railingGeos(R * 2 + 0.5, { h: 1.05, posts: 7 }), P([0, walkY, 0.42]))
  addGeos(galv, ladderGeos(walkY, { w: 0.45, rungs: 7, cage: true }), P([R + 0.15, 0, 0]))
  // ⑦ 进水 / 出水管（配对法兰，说明这池子有来水有去水）
  addGeos(steel, cylGeos(0.14, 1.4, [-R - 0.6, 0.7, 0], [0, 0, Math.PI / 2]))
  addGeos(steel, flangePairGeos(0.17, { t: 0.05, bolts: 8 }), P([-R - 0.1, 0.7, 0], [0, 0, Math.PI / 2]))
  addGeos(steel, cylGeos(0.12, 1.2, [R + 0.5, 0.55, 0], [0, 0, Math.PI / 2]))
  // ⑧ 池边阀门 + 手轮
  addGeos(dark, cylGeos(0.13, 0.26, [R + 0.5, 0.55, 0], [0, 0, Math.PI / 2]))
  addGeos(steel, cylGeos(0.035, 0.28, [R + 0.5, 0.75, 0]))
  addGeos(dark, handwheelGeos(0.17), P([R + 0.5, 0.9, 0]))

  const galvMesh = cluster(galv, DETAIL_MAT.galv, 'detail-galv')
  if (galvMesh) group.add(galvMesh)
  const steelMesh = cluster(steel, DETAIL_MAT.steel, 'detail-steel')
  if (steelMesh) group.add(steelMesh)
  const darkMesh = cluster(dark, DETAIL_MAT.dark, 'detail-dark')
  if (darkMesh) group.add(darkMesh)

  // ⑨ 铭牌 + 警示条：贴在池壁正面（池壁够高，读得清）
  const plate = nameplateMesh(`${equip.name} · ${equip.location_tag || ''}`, 0.9, 0.22)
  plate.position.set(0, H * 0.62, R + 0.02)
  group.add(plate)
  const stripe = hazardStripeMesh(0.8, 0.02, 0.1)
  stripe.position.set(0, H * 0.42, R + 0.02)
  group.add(stripe)

  addStatusLight(group, equip.status, H + 1.5)
  group.userData = { equipId: equip.id, equip }
  return group
}

/** 创建水处理加药装置：溶解罐 + 计量泵 + 药剂桶
 *
 *  真实 PAM 加药系统 = 溶解罐（带搅拌）+ 计量泵 + 药剂储存。
 *  做成"罐 + 泵 + 桶"三件套，才不会被误认成又一个搅拌槽
 *  （场景里已有 agitator，两个如果长得一样就分不清谁是干什么的）。
 */
function createDosingUnit(equip) {
  const group = new THREE.Group()

  // ① 溶解罐（带锥顶，与搅拌槽的圆柱+锥底区分开）
  const tankMat = new THREE.MeshPhongMaterial({
    color: 0x7e8c99, transparent: true, opacity: 0.85, shininess: 45,
  })
  const tank = new THREE.Mesh(new THREE.CylinderGeometry(0.95, 0.95, 1.7, 28), tankMat)
  tank.position.y = 1.15
  tank.castShadow = true
  group.add(tank)
  const cone = new THREE.Mesh(new THREE.ConeGeometry(0.97, 0.42, 28), tankMat)
  cone.position.y = 2.21
  group.add(cone)

  // ② 罐顶搅拌电机（小，PAM 溶解转速低）
  const motor = new THREE.Mesh(
    new THREE.CylinderGeometry(0.2, 0.2, 0.42, 16),
    new THREE.MeshPhongMaterial({ color: 0x4a5a6a, shininess: 60 }))
  motor.position.y = 2.62
  group.add(motor)
  const shaft = new THREE.Mesh(
    new THREE.CylinderGeometry(0.045, 0.045, 1.5, 10),
    new THREE.MeshPhongMaterial({ color: 0xcfd8dc, shininess: 80 }))
  shaft.position.y = 1.2
  group.add(shaft)
  rotatingParts.push({ mesh: shaft, axis: 'y', speed: 0.02, equipId: equip.id })

  // ③ 计量泵（隔膜泵，横卧小电机 + 泵头）
  const pumpHead = new THREE.Mesh(
    new THREE.SphereGeometry(0.26, 18, 14),
    new THREE.MeshPhongMaterial({ color: 0x4db6ac, shininess: 70 }))
  pumpHead.position.set(1.75, 0.42, 0)
  group.add(pumpHead)
  const pumpMotor = new THREE.Mesh(
    new THREE.CylinderGeometry(0.2, 0.2, 0.55, 16),
    new THREE.MeshPhongMaterial({ color: 0x5c6bc0, shininess: 60 }))
  pumpMotor.rotation.z = Math.PI / 2
  pumpMotor.position.set(2.45, 0.42, 0)
  group.add(pumpMotor)

  // ④ 药剂桶（PAM 干粉桶，小圆桶 + 盖）
  const drum = new THREE.Mesh(
    new THREE.CylinderGeometry(0.34, 0.34, 0.72, 20),
    new THREE.MeshPhongMaterial({ color: 0x1565c0, shininess: 30 }))
  drum.position.set(-1.5, 0.42, 0.55)
  group.add(drum)
  const lid = new THREE.Mesh(
    new THREE.CylinderGeometry(0.36, 0.36, 0.06, 20),
    new THREE.MeshPhongMaterial({ color: 0x0d47a1 }))
  lid.position.set(-1.5, 0.81, 0.55)
  group.add(lid)

  // ⑤ 工业细节合批
  const steel = []
  const galv = []
  const dark = []
  // 罐壁人孔 + 液位计
  addGeos(steel, flangeGeos(0.2, { t: 0.05, bolts: 8 }), P([0, 1.3, 0.95], [Math.PI / 2, 0, 0]))
  addGeos(galv, cylGeos(0.05, 1.5, [0.98, 1.25, 0]))
  // 罐底出料管 + 计量泵接管
  addGeos(steel, cylGeos(0.075, 0.9, [0.7, 0.3, 0], [0, 0, Math.PI / 2]))
  addGeos(steel, flangePairGeos(0.09, { t: 0.04, bolts: 6 }), P([1.15, 0.3, 0], [0, 0, Math.PI / 2]))
  addGeos(steel, cylGeos(0.06, 0.7, [2.1, 0.42, 0], [0, 0, Math.PI / 2]))
  // 泵底座 + 罐底支座
  addGeos(dark, blockGeos(1.2, 0.3, 0.6, [2.05, 0.2, 0]))
  addGeos(dark, blockGeos(1.6, 0.24, 1.6, [0, 0.16, 0]))
  // 计量泵压力表
  addGeos(galv, cylGeos(0.025, 0.26, [1.75, 0.72, 0]))
  addGeos(galv, cylGeos(0.09, 0.05, [1.75, 0.86, 0]))

  const steelMesh = cluster(steel, DETAIL_MAT.steel, 'detail-steel')
  if (steelMesh) group.add(steelMesh)
  const galvMesh = cluster(galv, DETAIL_MAT.galv, 'detail-galv')
  if (galvMesh) group.add(galvMesh)
  const darkMesh = cluster(dark, DETAIL_MAT.dark, 'detail-dark')
  if (darkMesh) group.add(darkMesh)

  // ⑥ 铭牌 + 警示条
  const plate = nameplateMesh(`${equip.name} · ${equip.location_tag || ''}`, 0.85, 0.2)
  plate.position.set(0, 0.9, 0.96)
  group.add(plate)
  const stripe = hazardStripeMesh(0.75, 0.02, 0.1)
  stripe.position.set(0, 0.7, 0.96)
  group.add(stripe)

  addStatusLight(group, equip.status, 3.1)
  group.userData = { equipId: equip.id, equip }
  return group
}

// ── 辅助函数 ───────────────────────────────────────

/** 在设备顶部添加状态指示灯 */
function addStatusLight(group, status, height) {
  const colors = { running: 0x4caf50, warning: 0xff9800, stopped: 0x9e9e9e, danger: 0xf44336 }
  const geo = new THREE.SphereGeometry(0.12, 16, 12)
  const mat = new THREE.MeshBasicMaterial({ color: colors[status] || colors.running, transparent: true })
  const light = new THREE.Mesh(geo, mat)
  light.position.y = height
  light.name = 'statusLight'
  group.add(light)
  return light
}

/** 创建设备标签（CSS2D）——孪生体上的读数牌：序号 + 名称 + 实时健康度
 *  序号与左侧设备列表同源（同一个 equipments 顺序），跨视图对得上号。 */
function createLabel(name, status, health, idx, tag, equipId) {
  const div = document.createElement('div')
  div.className = `equip-label label-${status}`
  if (equipId) div.dataset.equipId = equipId
  const idxTxt = idx ? String(idx).padStart(2, '0') : ''
  div.innerHTML =
    (idxTxt ? `<i class="el-idx">${idxTxt}</i>` : '') +
    `<span class="el-name">${name}</span>` +
    `<span class="el-health">${health == null ? '—' : Math.round(health)}</span>`
  div.style.cssText = labelCss(status)
  if (tag) div.title = `${name} · 位号 ${tag}`
  const label = new CSS2DObject(div)
  label.position.set(0, 3.5, 0)   // 实际挂高由调用方按本体高度设定
  return label
}

/** 标签配色（按状态），集中一处便于 3D 与列表口径一致 */
function labelCss(status) {
  return `
    /* ★★ position:absolute 不可省 —— 这是"标签横向错位、引线接不上"的根因。
       CSS2DRenderer 靠给元素写 transform 来定位，但**要求元素本身是绝对定位**；
       这里 cssText 没写 position 时它是 inline-flex 行内元素，会被父容器的文档流
       （前面还有别的标签、还有 HUD 节点）往里挤，于是 translate(-50%,-100%)
       作用在一个**已经被挤偏的盒**上：标签整体右移半个多宽，引线仍画在原 _sx 处，
       视觉上就是"牌子飘到右边、线指在牌子左边"，实测偏 57px（恰为半个标签宽）。
       同时去掉 top/left——位置由每帧的 transform 决定，写死会让标签贴住容器左上角。 */
    position: absolute;
    top: 0;
    left: 0;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 7px 3px 6px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
    pointer-events: auto;
    cursor: pointer;
    user-select: none;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    line-height: 1.4;
    ${status === 'danger' ? 'background:#ffece8;color:#d92f2f;border:1px solid #f7a2a2;' :
      status === 'warning' ? 'background:#fff7e6;color:#994b00;border:1px solid #ffd6a0;' :
      status === 'stopped' ? 'background:#f2f3f5;color:#86909c;border:1px solid #e5e6eb;' :
      'background:rgba(255,255,255,0.92);color:#1d2129;border:1px solid #e5e6eb;'}
  `
}

/** 同步标签内容（名称 + 健康度）与配色 */
function syncLabel(child, equip) {
  if (!child.isCSS2DObject || !child.element) return
  const el = child.element
  const nameEl = el.querySelector('.el-name')
  const healthEl = el.querySelector('.el-health')
  if (nameEl) nameEl.textContent = equip.name
  if (healthEl) healthEl.textContent = equip.health == null ? '—' : Math.round(equip.health)
  el.className = `equip-label label-${equip.status}`
  el.style.cssText = labelCss(equip.status)
  if (equip.location_tag) el.title = `${equip.name} · 位号 ${equip.location_tag}`
  // cssText 会抹掉聚焦态的 opacity / className 会抹掉 is-focus → 由调用方在刷新末尾重设
}

/** 建厂设备模型（根据类型分发） */
function createEquipmentModel(equip) {
  switch (equip.type) {
    case 'flotation': return createFlotationCell(equip)
    case 'mill':      return createBallMill(equip)
    case 'pump':      return createSlurryPump(equip)
    case 'conveyor':  return createConveyor(equip)
    case 'thickener': return createThickener(equip)
    case 'agitator':  return createAgitator(equip)
    // 水处理段：两个池子体量不同（尾矿溢流池小、回水池大），靠 id 区分尺度
    case 'pool':      return createWaterPool(equip, equip.id === 'water_return'
      ? { radius: 3.0, height: 1.7, waterColor: 0x2a5566 }
      : { radius: 2.4, height: 1.4, waterColor: 0x33414d })
    case 'dosing':    return createDosingUnit(equip)
    default:          return createFlotationCell(equip)
  }
}

// ══════════════════════════════════════════════════
//  环境美术 —— 天空 / 地面 / 微尘（画面质感来源）
// ══════════════════════════════════════════════════

/** 地面贴图：径向渐变，中心略亮、边缘沉入暗部（把视觉重心压在厂区上） */
function makeGroundTexture() {
  const size = 1024
  const c = document.createElement('canvas')
  c.width = c.height = size
  const ctx = c.getContext('2d')
  const g = ctx.createRadialGradient(size / 2, size / 2, size * 0.04, size / 2, size / 2, size * 0.52)
  g.addColorStop(0.0, '#28313d')
  g.addColorStop(0.38, '#1a222c')
  g.addColorStop(0.72, '#111721')
  g.addColorStop(1.0, '#080c12')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, size, size)
  const tex = new THREE.CanvasTexture(c)
  tex.colorSpace = THREE.SRGBColorSpace
  return tex
}

/** 渐变天空球：天顶近黑、地平线冷青灰（随相机转动，给场景以"远"） */
function createSkyDome() {
  const mat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    fog: false,
    uniforms: {
      topColor: { value: new THREE.Color(SKY_TOP) },
      horizonColor: { value: new THREE.Color(SKY_HORIZON) },
      offset: { value: 14.0 },
      exponent: { value: 0.9 },
    },
    vertexShader: `
      varying vec3 vWorldPos;
      void main() {
        vec4 wp = modelMatrix * vec4(position, 1.0);
        vWorldPos = wp.xyz;
        gl_Position = projectionMatrix * viewMatrix * wp;
      }`,
    fragmentShader: `
      uniform vec3 topColor;
      uniform vec3 horizonColor;
      uniform float offset;
      uniform float exponent;
      varying vec3 vWorldPos;
      void main() {
        float h = normalize(vWorldPos + vec3(0.0, offset, 0.0)).y;
        float t = pow(max(h, 0.0), exponent);
        vec3 col = mix(horizonColor, topColor, t);
        // 地平线辉光：地平线附近一条柔和亮带，给空间一个"远方"
        float glow = exp(-pow((h - 0.015) * 7.0, 2.0));
        col += vec3(0.05, 0.072, 0.098) * glow;
        // 天顶极淡的冷光，避免上半死黑
        col += vec3(0.012, 0.02, 0.036) * pow(max(h, 0.0), 0.5);
        gl_FragColor = vec4(col, 1.0);
      }`,
  })
  const dome = new THREE.Mesh(new THREE.SphereGeometry(400, 32, 16), mat)
  dome.name = 'skyDome'
  dome.renderOrder = -1
  return dome
}

/** 空气微尘：散布的细小光点，缓慢上浮 —— 让空场景不"死" */
function createAmbientDust() {
  const N = 300
  const arr = new Float32Array(N * 3)
  for (let i = 0; i < N; i++) {
    arr[i * 3] = (Math.random() - 0.5) * 76
    arr[i * 3 + 1] = Math.random() * 24 + 0.5
    arr[i * 3 + 2] = (Math.random() - 0.5) * 60
  }
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.BufferAttribute(arr, 3))
  const mat = new THREE.PointsMaterial({
    color: 0x9fd8ff, size: 0.08, transparent: true, opacity: 0.3,
    depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true,
  })
  const pts = new THREE.Points(geo, mat)
  pts.name = 'ambientDust'
  // ★ 微尘是纯氛围，必须退出拾取与命中链：它是 300 个随机分布的点，
  //   总有一些正好挡在设备与相机之间 —— 实测锚点射线最先撞上的就是它（dist 19.4），
  //   于是"点设备的牌子"偶尔会变成"什么都没点中"，属于随机复现的幽灵 bug。
  pts.raycast = () => {}
  return pts
}

// ══════════════════════════════════════════════════
//  场景初始化
// ══════════════════════════════════════════════════

function initScene() {
  const container = canvasContainer.value
  if (!container) return
  const w = container.clientWidth
  const h = container.clientHeight

  // 场景 —— 渐变天空 + 距离雾，给空间纵深
  scene = new THREE.Scene()
  scene.fog = new THREE.Fog(FOG_COLOR, 36, 110)

  // 相机（抬高拉远 + 略收窄视角，把两排厂区一次框住，透视更"正经"）
  // ★★ 2026-10-18 相机后撤：原 (16.5,11,20.5)/45° 时配电室（x=18 东侧）
  //    投影到屏幕 x=2203，**整个落在 1920 视口之外** —— 用户看不见也点不到，
  //    左栏「配电室 → 系统设置」这条入口在 3D 里等于不存在。
  //    实测 (21,14,26.5)/48° 是"15 个关键点（10 设备锚点 + 5 载体楼顶）全部入框"
  //    最保守的一组：只把它往外拉了约 30%，没动视野角，构图比例不变。
  camera = new THREE.PerspectiveCamera(48, w / h, 0.1, 1000)
  camera.position.copy(HOME_POS())
  camera.lookAt(HOME_AIM)
  fitHomeDistance()

  // WebGL 渲染器
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setSize(w, h)
  // ★ 着色器自检关闭 —— 只为消掉 ANGLE/HLSL 编译器噪声：
  //    某些驱动（含本机软件渲染）会对 three 内置 shader 吐一条无害的
  //    "warning X4122: sum of 0.996094 and ... cannot be represented accurately in double precision"，
  //    three 会把它当 "THREE.WebGLProgram: Program Info Log" warning 打到控制台 ——
  //    看着像报错，其实是驱动层的常量折叠精度提示，**应用层改不了**，
  //    且演示时（启动平台.bat 跑 vite dev）会真实出现在评委的 devtools 里。
  //    ⚠️ 改自定义着色器（如天空球）时临时改回 true，可拿到完整的编译诊断信息。
  renderer.debug.checkShaderErrors = false
  // ★ 上限压到 1.5：2x 像素比在 2K/4K 屏上会把后期链的填充率抬到 4 倍，是卡顿的第一大来源；
  //   1.5 在观感上几乎无差（画面上叠了 bloom，柔和度本来就高）
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5))
  renderer.shadowMap.enabled = true
  // ⚠️ PCFSoftShadowMap 自 three r186 起已移除（源码标注 @deprecated，运行时会打
  //    "PCFSoftShadowMap has been removed. Using PCFShadowMap instead." 并自动降级）。
  //    直接写 PCFShadowMap，消除警告、行为一致。
  renderer.shadowMap.type = THREE.PCFShadowMap
  // ★ 场景几何与主光都是静态的（相机移动不影响光源视角的阴影贴图）
  //   → 关掉自动更新，只在设备放置完成时刷一次，省掉每帧一遍深度重绘
  renderer.shadowMap.autoUpdate = false
  // ⚠️ 但必须"先渲染一次"再进渲染循环：animate() 紧随 initScene() 启动，
  //    而设备数据是异步拉的 —— 中间那几帧**地面已在画**（floor.receiveShadow=true），
  //    它的程序里带着 sampler2DShadow，而阴影贴图还从未渲染过（autoUpdate=false 时一直是 null）
  //    → three 只能绑定空纹理 → 首帧 2~3 次
  //    "GL_INVALID_OPERATION: Mismatch between texture format and sampler type"。
  //    （实测：报错集中在首帧 100ms 内、之后 23 秒零复现 —— 就是这个竞态）
  //    这里先渲染一次空场景的深度（1024² 一次，成本可忽略），贴图从第 1 帧起就存在；
  //    设备放置完成后再置一次（见 buildScene 末尾），把设备阴影补上。
  renderer.shadowMap.needsUpdate = true
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.22
  container.appendChild(renderer.domElement)

  // 引线层（SVG）：标签做屏幕空间避让后位置会偏离设备，3D 空间里的线就不再指向标签，
  // 所以引线必须画在屏幕空间 —— 从设备锚点连到标签实际落点，永远正确。
  leaderSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  leaderSvg.setAttribute('class', 'leader-overlay')
  container.appendChild(leaderSvg)

  // CSS2D 标签渲染器（DOM 顺序在 SVG 之后 → 标签层压在引线之上）
  labelRenderer = new CSS2DRenderer()
  labelRenderer.setSize(w, h)
  labelRenderer.domElement.style.position = 'absolute'
  labelRenderer.domElement.style.top = '0'
  labelRenderer.domElement.style.pointerEvents = 'none'
  container.appendChild(labelRenderer.domElement)

  // 控制器
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.maxPolarAngle = Math.PI / 2.05
  controls.minDistance = 8
  controls.maxDistance = 65
  controls.target.copy(SCENE_CENTER)
  // controls 就绪后按当前宽高比再校一次机位（相机先于 controls 创建，那时算不了 target）
  fitHomeDistance()

  // ── 光照系统：半球光/环境光压低 → 靠主光塑造明暗面，金属才有"体积"；
  //    主光偏暖打侧上，冷暖对冲（补光偏蓝）让灰调设备不再糊成一团 ──
  const hemi = new THREE.HemisphereLight(0x9dc4e8, 0x0d1319, 0.58)
  scene.add(hemi)

  const ambient = new THREE.AmbientLight(0xffffff, 0.26)
  scene.add(ambient)

  const mainLight = new THREE.DirectionalLight(0xfff6ea, 1.65)
  mainLight.position.set(19, 25, 13)
  mainLight.castShadow = true
  // 1024 足够：画面有雾 + bloom，且阴影是柔和软影，2048 的锐度看不出来却贵一倍
  mainLight.shadow.mapSize.width = 1024
  mainLight.shadow.mapSize.height = 1024
  mainLight.shadow.camera.near = 1
  mainLight.shadow.camera.far = 95
  mainLight.shadow.camera.left = -32
  mainLight.shadow.camera.right = 32
  mainLight.shadow.camera.top = 32
  mainLight.shadow.camera.bottom = -32
  mainLight.shadow.bias = -0.0012
  mainLight.shadow.normalBias = 0.02
  scene.add(mainLight)

  const fillLight = new THREE.DirectionalLight(0x7fb2ff, 0.42)
  fillLight.position.set(-14, 12, -9)
  scene.add(fillLight)

  const rimLight = new THREE.DirectionalLight(0xffcf9a, 0.55)
  rimLight.position.set(-9, 8, 20)
  scene.add(rimLight)

  // ── 地面：深色带微反射 + 径向渐变贴图（中心略亮 = 视觉重心落回厂区）──
  const floorGeo = new THREE.PlaneGeometry(150, 150)
  const floorMat = new THREE.MeshStandardMaterial({
    color: 0xffffff, roughness: 0.66, metalness: 0.22,
    map: makeGroundTexture(),
  })
  const floor = new THREE.Mesh(floorGeo, floorMat)
  floor.rotation.x = -Math.PI / 2
  floor.receiveShadow = true
  scene.add(floor)

  // 网格（2 米格；线压得很淡，只做"地面有刻度"的暗示，密度过高会像地砖）
  const grid = new THREE.GridHelper(150, 75, GRID_COLOR, GRID_SUB)
  grid.position.y = 0.02
  grid.material.opacity = 0.27
  grid.material.transparent = true
  scene.add(grid)

  // ── 渐变天空球（随相机转动，比纯色背景多一层"空间感"）──
  scene.add(createSkyDome())

  // ── 环境贴图（金属反射；强度压低，否则室内白环境会把地面和设备一起提亮）──
  const pmrem = new THREE.PMREMGenerator(renderer)
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
  if ('environmentIntensity' in scene) scene.environmentIntensity = 0.42

  // ── 空气微尘（缓慢上浮的光点，给画面"呼吸感"）──
  ambientDust = createAmbientDust()
  scene.add(ambientDust)

  // ── 后期处理：UnrealBloom 只让高亮体（定位环 / 状态灯 / 液面 / 流体粒子）发光 ──
  composer = new EffectComposer(renderer)
  composer.addPass(new RenderPass(scene, camera))
  bloomPass = new UnrealBloomPass(
    bloomVector(w, h),   // 半分辨率：bloom 本身就是模糊结果，降到一半看不出差别，但填充率省 4 倍
    0.24,   // strength：辉光强度（只做"提亮边缘"，不做出光晕）
    0.46,   // radius：扩散半径
    0.98    // threshold：再抬高一点，挡掉"大平面镜面高光"被误放大成光斑
  )
  composer.addPass(bloomPass)
  composer.addPass(new OutputPass())

  // ── 工艺分区：细边环 + 极淡底 + 文字标识（像厂区地坪划线，克制但有质感）──
  // 颜色语义只留给"健康度"一种，分区一律中性冷灰，不抢戏。
  const ZONES = [
    { pos: [  8.0, 0, -5.5], name: '磨矿 · 泵送' },
    { pos: [ -3.2, 0, -5.5], name: '粗选 · 扫选' },
    { pos: [  0.2, 0,  5.5], name: '浓密 · 输送' },
    { pos: [ -9.5, 0,  0.0], name: '药剂制备' },
  ]
  ZONES.forEach(z => {
    const R = 4.3
    const disc = new THREE.Mesh(
      new THREE.CircleGeometry(R, 64),
      new THREE.MeshBasicMaterial({
        color: 0x546e7a, transparent: true, opacity: 0.055, side: THREE.DoubleSide,
        depthWrite: false,
        polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2
      })
    )
    disc.rotation.x = -Math.PI / 2
    disc.position.set(z.pos[0], 0.09, z.pos[2])
    disc.renderOrder = 1
    disc.raycast = () => {}
    scene.add(disc)

    const rim = new THREE.Mesh(
      new THREE.RingGeometry(R - 0.1, R, 80),
      new THREE.MeshBasicMaterial({
        color: 0x6b8494, transparent: true, opacity: 0.2, side: THREE.DoubleSide,
        depthWrite: false,
        polygonOffset: true, polygonOffsetFactor: -3, polygonOffsetUnits: -3
      })
    )
    rim.rotation.x = -Math.PI / 2
    rim.position.set(z.pos[0], 0.1, z.pos[2])
    rim.renderOrder = 2
    rim.raycast = () => {}
    scene.add(rim)

    const div = document.createElement('div')
    div.textContent = z.name
    div.style.cssText = 'font-size:12.5px;font-weight:600;color:rgba(198,220,240,0.78);text-shadow:0 1px 8px rgba(0,0,0,0.95),0 0 2px rgba(0,0,0,0.9);font-family:sans-serif;pointer-events:none;letter-spacing:3px;white-space:nowrap;'
    const zlabel = new CSS2DObject(div)
    zlabel.position.set(z.pos[0], 0.05, z.pos[2] + R - 0.5)
    scene.add(zlabel)
  })

  // ── 厂区空间（厂房骨架 / 地坪 / 管廊 / 围墙）──
  // 顺序有讲究：先地坪（贴地面、带透明），再骨架，最后围墙 ——
  // 透明物的渲染次序影响观感，地坪标线必须先于钢结构入场景。
  scene.add(buildFloorMarkings())
  scene.add(buildWorkshopFrame())
  scene.add(buildPipeRack())
  scene.add(buildPerimeter())

  // ── 功能载体建筑（中控室/化验室/药剂间/配电室/监控立杆）──
  // 它们是静态的、与实时数据无关，所以在这里一次性建好；
  // 不放进 placeAllEquipments，避免每次 5 秒轮询都重建一遍。
  buildFacilities()

  // ── 放置设备（延迟到数据加载后由 placeAllEquipments 执行） ──
  // 设备模型创建和放置在 fetchEquipmentData → placeAllEquipments 中完成

  // ── 连接管路（装饰性，延迟执行） ──
  // createPipelines() 在 placeAllEquipments 末尾调用

  // 开发期调试句柄（仅 dev；QA 脚本用它读渲染统计、做优化开关的 A/B 对比）
  if (import.meta.env.DEV) {
    window.__eq3d = {
      renderer, scene, camera, composer, bloomPass, pickTargets, hoveredId, hoverPos, labelItems,
      controls,            // 相机轨道控制器：QA 用它读"当前绕谁转"（controls.target）
      facilityMeshes,      // 功能载体建筑 group（QA 用它数建筑、读位置）
      // 按屏幕坐标做一次真实拾取 —— QA 用它验证"引线锚点到底落在哪台设备上"
      pickAt: (x, y) => pickEquipmentAt(x, y),
      // "这一点上有没有这台设备"（不管建筑是否在前面）—— QA 验标签锚点用，语义见 equipmentUnderPoint
      equipUnder: (x, y) => equipmentUnderPoint(x, y),
      // 按屏幕坐标列出射线**命中链**（远近顺序）—— 排查"锚点打在别的设备上"用
      rayHits: (x, y) => {
        if (!canvasContainer.value || !camera) return []
        const rect = canvasContainer.value.getBoundingClientRect()
        mouse.x = ((x - rect.left) / rect.width) * 2 - 1
        mouse.y = -((y - rect.top) / rect.height) * 2 + 1
        raycaster.setFromCamera(mouse, camera)
        return raycaster.intersectObjects(scene.children, true)
          .filter(h => h.object.visible !== false)
          .slice(0, 5)
          .map(h => ({
            name: h.object.name || h.object.type,
            equipId: h.object.userData.equipId || h.object.parent?.userData?.equipId || null,
            dist: +h.distance.toFixed(2),
          }))
      },
      // 量化"每次 hover 检测"的射线开销：旧实现 = 全场景递归（含 300 点微尘），新实现 = 7 个盒子
      benchRaycast: (n = 200) => {
        const rc = new THREE.Raycaster()
        rc.setFromCamera(new THREE.Vector2(0.12, -0.06), camera)
        let t0 = performance.now()
        for (let i = 0; i < n; i++) rc.intersectObjects(scene.children, true)
        const legacy = (performance.now() - t0) / n
        t0 = performance.now()
        for (let i = 0; i < n; i++) rc.intersectObjects(pickTargets, false)
        const next = (performance.now() - t0) / n
        return {
          n,
          legacyMs: +legacy.toFixed(3),
          nextMs: +next.toFixed(3),
          speedup: next > 0 ? +(legacy / next).toFixed(1) : null,
        }
      },
      info: () => {
        let meshes = 0, points = 0, lines = 0
        scene.traverse(o => {
          if (o.isMesh) meshes++
          else if (o.isPoints) points++
          else if (o.isLine) lines++
        })
        return {
          meshes, points, lines,
          geometries: renderer.info.memory.geometries,
          textures: renderer.info.memory.textures,
          programs: renderer.info.programs ? renderer.info.programs.length : 0,
          pixelRatio: renderer.getPixelRatio(),
          bloom: bloomPass ? bloomPass.resolution.toArray() : null,
          shadowAutoUpdate: renderer.shadowMap.autoUpdate,
        }
      },
      // QA 探针：从 NDC 坐标打一条射线，回答"屏幕上这个点是什么物体"
      probe: (nx, ny) => {
        const rc = new THREE.Raycaster()
        rc.setFromCamera(new THREE.Vector2(nx, ny), camera)
        return rc.intersectObjects(scene.children, true)
          .filter(h => h.object.name !== 'pickProxy')   // 拾取盒是隐藏 AABB，会挡住真实几何
          .slice(0, 5).map(h => ({
          name: h.object.name || '(anon)',
          type: h.object.type,
          color: h.object.material && h.object.material.color
            ? '#' + h.object.material.color.getHexString() : null,
          dist: +h.distance.toFixed(2),
        }))
      },
      // QA：单独关掉 bloom，用来判断"某个亮斑是几何体本身还是后期辉光放大出来的"
      setBloom: (on) => {
        if (bloomPass) bloomPass.enabled = !!on
        return !!on
      },
      // 相机推近/拉远（QA 拍近景用）：沿当前视线方向缩放，f<1 = 推近
      zoom: (f) => {
        camera.position.sub(controls.target).multiplyScalar(f).add(controls.target)
        controls.update()
        return +camera.position.distanceTo(controls.target).toFixed(2)
      },
      // 工业细节 A/B 开关：off = 隐藏所有 detail-* 簇与齿圈 → 同一机位拍"改造前"
      setDetails: (on) => {
        let n = 0
        scene.traverse(o => {
          if (!o.isMesh) return
          if (o.name.startsWith('detail-') || o.name === 'girthGear') {
            o.visible = !!on
            if (on) n++
          }
        })
        return n
      },
      // QA：显式选中 / 取消选中。
      // 为什么需要：原先 QA 脚本靠在画布上"盲点一个空白点"来取消选中，实测多次点到
      // 左侧设备卡上（点出过"想拍浮选槽、结果选中搅拌槽"），于是整组截图作废。
      // 选中与否直接决定有没有叠提亮层，必须可控。
      select: (key) => {
        const eq = equipments.value.find(x => x.id === key || (x.name || '').includes(key))
        if (!eq || !equipmentMeshes.has(eq.id)) return 'MISS'
        focusEquipment(eq.id)          // 含"按设备形态飞过去"的机位，不要拆成只选中
        return eq.id
      },
      deselect: () => { deselectEquipment(); return 'OK' },
      // QA：直接读前端持有的设备数据 —— 用来判断"面板显示的和列表里的到底是不是同一个对象"
      // （equipments 每 5s 整批换新对象，若 selectedEquipment 没跟着换，面板就会显示旧值）
      equipState: (id) => {
        const inList = equipments.value.find(e => e.id === id)
        const sel = selectedEquipment.value
        return {
          listStatus: inList ? inList.status : null,
          listHealth: inList ? inList.health : null,
          listHasStopped: inList ? inList.status === 'stopped' : null,
          selId: sel ? sel.id : null,
          selStatus: sel ? sel.status : null,
          sameObject: !!(sel && inList && sel === inList),
          indexHas: inList ? equipIndex.has(inList.id) : null,
          indexSize: equipIndex.size,
          listIds: equipments.value.map(e => e.id),
        }
      },
      // QA：球磨机筒体的**世界轴向** —— 证明"绕自身轴自转"而不是翻跟头。
      //   轴向量应恒为 (±1,0,0)（水平）。若写成累加 rotation.z，轴会在 XY 平面内
      //   转起来（h 会飙到 5m、d 只剩 2.4m）。静态截图看不出来，必须实测。
      millAxis: () => {
        const g = equipmentMeshes.get('ball_mill')
        if (!g) return 'MISS'
        const b = g.children.find(c => c.isMesh && c.geometry &&
          c.geometry.type === 'CylinderGeometry' && c.geometry.parameters.radiusTop === 1.2)
        if (!b) return 'MISS'
        const ax = new THREE.Vector3(0, 1, 0).applyQuaternion(b.quaternion)
        const geo = b.geometry.boundingBox || (b.geometry.computeBoundingBox(), b.geometry.boundingBox)
        const box = geo.clone().applyMatrix4(b.matrixWorld)
        return {
          rotX: +b.rotation.x.toFixed(3), rotZ: +b.rotation.z.toFixed(3),
          axis: [+ax.x.toFixed(3), +ax.y.toFixed(3), +ax.z.toFixed(3)],
          h: +(box.max.y - box.min.y).toFixed(2),
        }
      },
      // 量化细节带来的几何量（三角形按索引数/3 统计）
      stats: () => {
        let meshes = 0, detailMeshes = 0, tris = 0, detailTris = 0
        scene.traverse(o => {
          if (!o.isMesh || !o.geometry) return
          const g = o.geometry
          const t = (g.index ? g.index.count : (g.attributes.position ? g.attributes.position.count : 0)) / 3
          meshes++
          tris += t
          if (o.name.startsWith('detail-') || o.name === 'girthGear') {
            detailMeshes++
            detailTris += t
          }
        })
        return {
          meshes, detailMeshes,
          triangles: Math.round(tris),
          detailTriangles: Math.round(detailTris),
        }
      },
      // A/B 开关：on=true 回到"优化前"的预算（bloom 全分辨率 + 阴影每帧重算）
      setLegacy: (on) => {
        renderer.shadowMap.autoUpdate = !!on
        if (on) renderer.shadowMap.needsUpdate = true
        if (bloomPass) {
          const s = renderer.getDrawingBufferSize(new THREE.Vector2())
          const k = on ? 1 : BLOOM_SCALE
          bloomPass.setSize(Math.floor(s.x * k), Math.floor(s.y * k))
        }
        return 'ok'
      },
    }
  }
}

const pickTargets = []    // 拾取代理盒（每设备 1 个，hover/点击射线只测它们）
const particleFlows = []  // 管内流动粒子
const pipelines = []      // 管线（含上下游设备，用于选中时高亮物料链）
const liquidSurfaces = []   // 矿浆液面（波动）
const conveyorParticles = [] // 皮带矿料颗粒
const ballMillBalls = []    // 球磨机钢球
const UP_AXIS = new THREE.Vector3(0, 1, 0)   // 锥体默认朝 +Y，用于对齐流向

// ── 后期处理预算 ─────────────────────────────────
// bloom 的模糊链按这个比例降分辨率。UnrealBloomPass 内部有 5 级 mip，全分辨率下
// 每帧要跑十几次全屏 pass；减半后填充率降到 1/4，而 bloom 本身是"糊"的，看不出损失。
const BLOOM_SCALE = 0.5
function bloomVector(w, h) {
  return new THREE.Vector2(
    Math.max(64, Math.floor(w * BLOOM_SCALE)),
    Math.max(64, Math.floor(h * BLOOM_SCALE))
  )
}

/** 创建设备间的连接管路（按真实物料流向 + 流动粒子）
 *  ★ 端点由设备 id 反查布局表，不再硬编码坐标 —— 重排厂区时管线自动跟随。 */
function createPipelines() {
  const baseMat = new THREE.MeshStandardMaterial({ color: 0xc0cfdb, transparent: true, opacity: 0.72, roughness: 0.24, metalness: 0.9 })
  // fromId/toId = 这条管线的上下游设备，供"选中设备高亮物料链"使用
  // ⚠️ 顺序有意义：highlightPipelines 靠 fromId/toId 推导上下游，
  //    新增水处理链时要保证"上游→下游"方向正确，否则高亮会反。
  const flows = [
    { fromId: 'ball_mill',   toId: 'pump_1',      mat: 0xa1887f, media: '矿浆' },
    { fromId: 'pump_1',      toId: 'flotation_1', mat: 0xa1887f, media: '矿浆' },
    { fromId: 'flotation_1', toId: 'flotation_2', mat: 0x4fc3f7, media: '中矿' },
    { fromId: 'flotation_2', toId: 'thickener',   mat: 0xb0bec5, media: '尾矿' },
    { fromId: 'thickener',   toId: 'conveyor',    mat: 0xb0bec5, media: '底流' },
    { fromId: 'agitator',    toId: 'flotation_1', mat: 0x81c784, media: '药剂' },
    { fromId: 'agitator',    toId: 'flotation_2', mat: 0x81c784, media: '药剂' },
    // ── 水处理链（尾矿溢流 → 汇集池 → 回水池 → 回用补水）──
    { fromId: 'thickener',     toId: 'tailings_pool', mat: 0x64b5f6, media: '溢流' },
    { fromId: 'tailings_pool', toId: 'water_return',  mat: 0x64b5f6, media: '澄清水' },
    { fromId: 'water_dosing',  toId: 'tailings_pool', mat: 0x81c784, media: 'PAM' },
    // 回水回用于磨矿补水（闭环：水从尾矿回到磨矿，这是"回用"的物理含义）
    { fromId: 'water_return',  toId: 'ball_mill',     mat: 0x4dd0e1, media: '回水' },
  ]
  flows.forEach(f => {
    const s = new THREE.Vector3(...equipPos(f.fromId))
    const e = new THREE.Vector3(...equipPos(f.toId))
    const dist = s.distanceTo(e)
    const mid = s.clone().add(e).multiplyScalar(0.5)
    // 拱高随跨度增长：跨排/跨区的长管线走"高架管廊"，短接则贴地，层次更清楚
    mid.y = Math.max(s.y, e.y) + 1.6 + dist * 0.16
    const curve = new THREE.QuadraticBezierCurve3(s, mid, e)
    const tubeGeo = new THREE.TubeGeometry(curve, 40, 0.105, 10, false)
    // 每条管独立材质：选中设备时要单独高亮它这一条链
    const tubeMat = baseMat.clone()
    const tube = new THREE.Mesh(tubeGeo, tubeMat)
    tube.castShadow = true
    tube.name = 'pipeline'
    tube.userData = { media: f.media, fromId: f.fromId, toId: f.toId }
    scene.add(tube)
    pipelines.push({ tube, media: f.media, fromId: f.fromId, toId: f.toId })

    // 流动粒子（5 个锥体，锥尖朝向物料流向，速度随上游设备实时流量变化）
    for (let i = 0; i < 5; i++) {
      const p = new THREE.Mesh(
        new THREE.ConeGeometry(0.075, 0.24, 10),
        new THREE.MeshStandardMaterial({ color: f.mat, emissive: f.mat, emissiveIntensity: 1.0, roughness: 0.3 })
      )
      p.name = 'flowParticle'
      p.userData.media = f.media
      scene.add(p)
      particleFlows.push({ curve, mesh: p, t: i / 5, baseSpeed: 0.0022, srcId: f.fromId, media: f.media })
    }
  })
}

// ══════════════════════════════════════════════════
//  数字孪生层
//  —— 场景不再只是"模型摆放"，而是实时数据的镜像：
//     ① 设备本体配色 = 当前健康度（热力）
//     ② 地面定位环 = 设备在孪生体中的位置锚点 + 告警呼吸
//     ③ 管线流速 = 该设备当前真实流量
//     ④ 相机机位 = 每台设备的观测角度（不是统一偏移）
// ══════════════════════════════════════════════════

/** 健康度 → 热力色
 *  剂量刻意压得很低：设备本体只做"极淡的活性染色"，
 *  健康度的主要语义由地面定位环 + 标签读数承载 —— 避免整场刷成调色盘。 */
function healthTint(h) {
  const v = Number(h)
  if (!Number.isFinite(v)) return { hex: 0x9e9e9e, i: 0.02, css: '#9e9e9e' }
  if (v >= 80) return { hex: 0x00b42a, i: 0.04 + (100 - v) / 20 * 0.03, css: '#00b42a' }
  if (v >= 50) return { hex: 0xff7d00, i: 0.07 + (80 - v) / 30 * 0.05, css: '#ff7d00' }
  return { hex: 0xf53f3f, i: 0.13 + (50 - v) / 50 * 0.09, css: '#f53f3f' }
}

/** 不参与健康度染色的物体（状态灯/定位环/告警脉冲各自有色彩语义） */
const SKIP_TINT = new Set([
  'statusLight', 'locatorRing', 'locatorGlow', 'locatorArc', 'alarmPulse', 'pedestal', 'statusBeam',
  // 工业细节里的"非机身件"：镀锌护栏/爬梯/平台、铭牌、黄黑警示条。
  // 三者都参与染色的话会被机身色吞掉，层次就没了 —— 保持本色反而更像真厂区。
  'detail-galv', 'detail-nameplate', 'detail-hazard',
])

/** 把健康度热力写进设备材质（选中时改用中性提亮，不夺色相） */
function applyTwinTint(group, equip, isSelected) {
  if (!group || !equip) return
  const tint = equip.status === 'stopped'
    ? { hex: 0x9e9e9e, i: 0.02 }
    : healthTint(equip.health)
  group.traverse(child => {
    if (!child.isMesh || !child.material || !child.material.emissive) return
    if (SKIP_TINT.has(child.name)) return
    if (child.renderOrder >= 2) return      // 液体面/泡沫面：保持原材质，染了会像发光的果冻
    if (isSelected) {
      // 选中 = 整体提亮（中性冷白），保留设备原色与健康度色相
      child.material.emissive.setHex(0x39506a)
      child.material.emissiveIntensity = 0.62
    } else {
      child.material.emissive.setHex(tint.hex)
      child.material.emissiveIntensity = tint.i
    }
  })
}

/** 统一材质质感 + 工业配色归一
 *  建模时每台设备各用各的颜色（棕/青/绿/黄），拼在一起像玩具。
 *  这里把所有"结构件"向同一套钢蓝灰收敛：保留 45% 原色以区分部件，其余归一到钢蓝。 */
const STEEL_BLUE = new THREE.Color(0x62798b)
function polishMaterials(group) {
  group.traverse(child => {
    if (!child.isMesh || !child.material) return
    if (SKIP_TINT.has(child.name)) return
    const m = child.material
    if (!m.isMeshStandardMaterial) return
    if (m.transparent && m.opacity < 0.92) return          // 液面/泡沫等半透明件保持原样
    if (m.transparent) { m.transparent = false; m.opacity = 1 }  // 半透明"准不透明"结构件：半透明会让圆柱/盒子看成锥体
    m.color.lerp(STEEL_BLUE, 0.66)                          // 配色归一
    m.metalness = 0.42                                      // 工业设备是"涂装漆面"而非抛光金属：金属度过高会反射环境色，把配色带偏
    // ⚠️ roughness 不能太低：主光(1.65) 打在球磨机这种大光滑圆柱上会打出**镜面高光尖峰**，
    //    线性亮度越过 bloom 阈值后被放大成一团白斑（探针实测：不是穿模、不是高光贴图，是后期辉光）。
    //    0.64 让高光变宽变钝，峰值掉到阈值以下，同时更像"喷过漆的设备"而不是抛光钢管。
    m.roughness = 0.64
    m.envMapIntensity = 0.7
    m.needsUpdate = true
  })
}

/** 异常设备状态光柱：从基座向上的一束光
 *  只给"非正常"设备 —— 全都出柱子会糊成一片，异常设备才需要被一眼看到。
 *  这是数字孪生大屏的通用语言：把抽象健康度变成一个"竖着的、看得见的量"。 */
function createStatusBeam(group, equip) {
  const v = Number(equip.health)
  const bad = equip.status === 'danger' || equip.status === 'warning'
  if (!bad) return null
  const h = equip.status === 'danger' ? 6.2 : 4.6
  const tint = healthTint(v)
  const beam = new THREE.Mesh(
    new THREE.CylinderGeometry(0.17, 0.36, h, 20, 1, true),
    new THREE.MeshBasicMaterial({
      color: tint.hex, transparent: true, opacity: 0.15,
      side: THREE.DoubleSide, depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
  )
  beam.position.y = h / 2 + 0.24
  beam.renderOrder = 2
  beam.name = 'statusBeam'
  beam.raycast = () => {}
  group.add(beam)

  // 柱顶亮环（给光柱一个"落点"，否则顶部断得突兀）
  const cap = new THREE.Mesh(
    new THREE.RingGeometry(0.17, 0.34, 32),
    new THREE.MeshBasicMaterial({
      color: tint.hex, transparent: true, opacity: 0.42,
      side: THREE.DoubleSide, depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
  )
  cap.rotation.x = -Math.PI / 2
  cap.position.y = h + 0.24
  cap.renderOrder = 3
  cap.name = 'statusBeam'
  cap.raycast = () => {}
  group.add(cap)
  return beam
}

/** 设备基础：一块深色混凝土基座
 *  没有基座的设备在 3D 里会"飘"在网格场地上 —— 这是场景显廉价的主因之一。 */
function createPedestal(group) {
  const box = new THREE.Box3().setFromObject(group)
  const size = box.getSize(new THREE.Vector3())
  const bc = box.getCenter(new THREE.Vector3())
  // 基座比设备包围盒"内收"一圈才像基础而不是给设备垫了块同尺寸的板子。
  // ⚠️ 但不能只按 0.66 收：长条设备（皮带宽 9m）会收掉 1.5m/侧，
  //    最外侧的支腿直接落到混凝土外面 —— 那比没有基座还假。
  //    所以再加一条"单侧最多收 1.2m"的下限。
  const shrink = (s) => Math.max(Math.max(s * 0.66, s - 2.4) + 0.8, 1.4)
  const padX = shrink(size.x)
  const padZ = shrink(size.z)
  // 基座要跟着"本体中心"走，而不是 group 原点：球磨机加了传动组后
  // 包围盒整体偏向 +X，若仍以原点为心，驱动基础墩会悬在基座外面。
  const lx = bc.x - group.position.x
  const lz = bc.z - group.position.z
  const pad = new THREE.Mesh(
    new THREE.BoxGeometry(padX, 0.24, padZ),
    new THREE.MeshStandardMaterial({ color: 0x333f4b, roughness: 0.94, metalness: 0.06 })
  )
  pad.position.set(lx, 0.12, lz)
  pad.castShadow = true
  pad.receiveShadow = true
  pad.name = 'pedestal'
  pad.raycast = () => {}
  group.add(pad)

  // 基座压边（略深一圈，做出厚度感）
  const edge = new THREE.Mesh(
    new THREE.BoxGeometry(padX + 0.18, 0.08, padZ + 0.18),
    new THREE.MeshStandardMaterial({ color: 0x222b34, roughness: 0.95, metalness: 0.04 })
  )
  edge.position.set(lx, 0.04, lz)
  edge.receiveShadow = true
  edge.name = 'pedestal'
  edge.raycast = () => {}
  group.add(edge)
}

/** 地面定位环：底环（常驻，颜色=健康度）+ 扫描弧（选中时出现并旋转）
 *  这是健康度在 3D 里的**主载体** —— 比给设备本体刷色更干净、更可读。
 *  半径按设备包围盒自适应：否则球磨机(6m)会把环整个盖住。 */
function createLocatorRing(group, equip) {
  const tint = healthTint(equip.health)
  const size = new THREE.Box3().setFromObject(group).getSize(new THREE.Vector3())
  const R = Math.min(Math.max(size.x, size.z) * 0.5, 2.1) + 0.3

  // ① 外层光晕：加色混合，只负责"发光"（不承担颜色判读）
  const glow = new THREE.Mesh(
    new THREE.RingGeometry(R - 0.08, R + 0.34, 64),
    new THREE.MeshBasicMaterial({
      color: tint.hex, transparent: true, opacity: 0.11,
      side: THREE.DoubleSide, depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
  )
  glow.rotation.x = -Math.PI / 2
  glow.position.y = 0.27
  glow.renderOrder = 3
  glow.name = 'locatorGlow'
  glow.raycast = () => {}
  group.add(glow)

  // ② 内层实环：普通混合 + 高不透明度 —— 保证"绿就是绿"。
  //    加色混合会被深蓝灰地面带偏成青色，让人把"健康"读成"异常"。
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(R, R + 0.15, 64),
    new THREE.MeshBasicMaterial({
      color: tint.hex, transparent: true, opacity: 0.5,
      side: THREE.DoubleSide, depthWrite: false,
    })
  )
  ring.rotation.x = -Math.PI / 2
  ring.position.y = 0.28
  ring.renderOrder = 4
  ring.name = 'locatorRing'
  ring.raycast = () => {}      // 不参与拾取，避免扩大误触热区
  group.add(ring)

  const arc = new THREE.Mesh(
    new THREE.RingGeometry(R + 0.26, R + 0.46, 64, 1, 0, Math.PI * 0.36),
    new THREE.MeshBasicMaterial({
      color: tint.hex, transparent: true, opacity: 0,
      side: THREE.DoubleSide, depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
  )
  arc.rotation.x = -Math.PI / 2
  arc.position.y = 0.29
  arc.renderOrder = 5
  arc.name = 'locatorArc'
  arc.raycast = () => {}
  group.add(arc)
}

/** 设备在 3D 里的视觉高度（标签挂高、定位环半径都用它，避免写死数值） */
function equipVisualHeight(group) {
  return new THREE.Box3().setFromObject(group).getSize(new THREE.Vector3()).y
}

/** 非"本体"的附加物：光柱 / 基座 / 引线 / 标签 —— 算包围盒时要排除，
 *  否则光柱（6.2m）会把标签顶到空中，基座会让定位环半径虚胖。 */
const NON_BODY_NAMES = new Set(['statusBeam', 'pedestal', 'leaderLine', 'leaderDot', 'pickProxy'])

/** 只框"机器本体"的包围盒（世界坐标） */
function bodyBox(group) {
  const box = new THREE.Box3()
  group.updateWorldMatrix(true, true)
  group.children.forEach((ch) => {
    if (ch.isCSS2DObject || NON_BODY_NAMES.has(ch.name)) return
    if (!ch.geometry) return
    box.expandByObject(ch)
  })
  return box
}

/** 本体顶部高度（group 局部坐标） —— 取主容器子件的顶面，理由见 bodyAnchor */
function bodyTopY(group) {
  const a = bodyAnchor(group)
  return Math.max(a.y - group.position.y, 0.6)
}

/**
 * 读数牌锚点 —— 落在**设备本体正中心的上方一点**，不是 group 包围盒的最高点。
 *
 * ★★ 这是"标签指错设备"的真正根因，比拾取盒重叠隐蔽得多：
 *    每台设备都带一堆附属件 —— 顶部走道（浮选槽 walkY=2.45、还外伸到 z=2.53）、
 *    立式电机（球磨机顶到 3.85）、拉绳开关（皮带机外挂 z=0.54）、
 *    **合批后的细节簇**（detail-galv 一个簇就横跨整机，包围盒中心根本不代表本体）。
 *    用 group 或簇的包围盒取"顶面中心"，锚点就会漂到走道/电机上方，
 *    在画面上往往正好压在旁边的工艺管道上 —— 于是线看起来是从管道长出来的。
 *
 * 做法：横向**一律退回设备布局原点**（那是建模者放这台机器的位置，绝对居中），
 *      只让纵向取"主要容器子件"的顶面高度。横向不猜，就不会漂。
 */
function bodyAnchor(group) {
  group.updateWorldMatrix(true, true)

  // ★★ 开口池体（回水池 / 尾矿溢流池 / 加药装置）单独处理：
  //    它们的"主体"是一个圆筒**壳**，包围盒顶面正好在池沿，而横向中心是**池腔正中**。
  //    于是锚点落在池子内部 —— 引线从池面正中往下"扎"进水里，看起来像插在水里，
  //    实测回水池的锚点射线一路穿过池壁打到池底（QA 报"引线穿越他机"）。
  //    判据：主容器是"筒体"且顶面中心**是空的**（射线打不到它的顶盖）→ 抬到池沿之上。
  let topY = null
  let mainCh = null
  let mainVol = 0
  group.children.forEach((ch) => {
    if (ch.isCSS2DObject || NON_BODY_NAMES.has(ch.name)) return
    if (!ch.isMesh || !ch.geometry) return
    const b = new THREE.Box3().setFromObject(ch)
    if (b.isEmpty()) return
    const s = b.getSize(new THREE.Vector3())
    const vol = s.x * s.y * s.z
    // 只接受"够肥"的子件：单块体积超过 1.2m³，排除细杆/法兰/绳索
    if (vol < 1.2) return
    if (topY == null || b.max.y > topY) topY = b.max.y
    if (vol > mainVol) { mainVol = vol; mainCh = ch }
  })
  if (topY == null) {
    const b = bodyBox(group)
    topY = b.isEmpty() ? group.position.y + 2 : b.max.y
  }

  // 池体检测：主容器是高位圆筒（Cylinder）且半径与高度同量级 → 判断顶面有没有盖
  if (mainCh && mainCh.geometry && /Cylinder/i.test(mainCh.geometry.type)) {
    const geo = mainCh.geometry
    const p = geo.parameters || {}
    const rTop = p.radiusTop ?? 0
    const hCyl = p.height ?? 0
    const isPool = Math.abs(rTop - (p.radiusBottom ?? 0)) < 0.05 && rTop > hCyl * 0.45
    if (isPool) {
      // 顶面中心有没有实体（有盖 → 射线会立刻命中；没盖 → 穿透到池底或更远）
      const ray = new THREE.Raycaster()
      const from = new THREE.Vector3(group.position.x, topY + 2.5, group.position.z)
      ray.set(from, new THREE.Vector3(0, -1, 0))
      const lid = ray.intersectObject(mainCh, false)
        .some((hh) => hh.point.y > topY - 0.35)
      // 池沿 + 护栏：池沿约等于 topY，护栏通常再高 1.0~1.2m。
      // 抬到 topY + 1.15 让锚点落在护栏顶上 —— 视觉上"从池口的栏杆长出来"。
      if (!lid) topY += 1.15
    }
  }

  // 横向用 group 原点的 x/z（= 布局表坐标 = 设备几何中心）
  return new THREE.Vector3(group.position.x, topY + 0.06, group.position.z)
}

/**
 * 注册读数牌：建一条屏幕空间引线（SVG line + 锚点圆）并登记布局项。
 *
 * 引线为什么画在屏幕空间而不是 3D 里：标签要做屏幕空间避让，位置会偏离设备正上方，
 * 3D 空间里的线就不再指向标签了。屏幕空间的线从设备锚点直接连到标签实际落点，永远正确。
 */
function registerLabel(equip, group, label, topY) {
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'line')
  line.setAttribute('class', 'leader-line')
  const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle')
  dot.setAttribute('class', 'leader-dot')
  // 2.6 半径在 1920 下只有 5px，混在设备零件里像一个亮点。放大到 3.6 才读得出"这是定位点"
  dot.setAttribute('r', '3.6')
  // ★ 标记归属设备：这个圆点画在**设备锚点**的屏幕坐标上，
  //   是"设备本体在画面上的确切位置"。标签本身会因为避让偏离本体，
  //   拿标签中心去点会打到它背后的设备（QA 实测：双击渣浆泵跳进了球磨机的页面）。
  //   有了这个属性，自动化测试才能打到"真的那台设备"。
  dot.setAttribute('data-equip-id', equip.id)
  if (leaderSvg) {
    leaderSvg.appendChild(line)
    leaderSvg.appendChild(dot)
  }
  // 锚点 = 设备**主体块**顶面中心（世界坐标），由 bodyAnchor 计算。
  // 不能用整个 group 的最高点：顶部走道/立式电机/外挂开关会把锚点顶到管道上方。
  const anchor = bodyAnchor(group)
  labelItems.push({ id: equip.id, el: label.element, anchor, line, dot, w: 92, h: 22 })
  return [line, dot]
}

/** 标签底边抬离锚点的像素高度 —— 太低压根看不出引线方向，太高会把牌子推到别的设备头顶 */
const LABEL_LIFT = 26

/**
 * 标签布局：屏幕空间避让。
 *
 * CSS2D 的本质是"一个 3D 点对一个 DOM 节点"，两台设备在屏幕上靠近时，标签会直接叠在一起
 * —— 这就是"标签对应不明确"的根因（实测 1#/2# 浮选槽的标签屏距只有 18px，完全重合）。
 * 这里每帧算一遍标签的屏幕矩形，重叠的向上让位，再用屏幕空间引线连回设备锚点。
 */
function relayoutLabels() {
  const c = canvasContainer.value
  if (!c || !camera || !labelItems.length) return
  const W = c.clientWidth
  const H = c.clientHeight
  // 每 20 帧量一次尺寸（读 offsetWidth 会强制布局，不能每帧读）
  const measure = (frameNo % 20) === 0

  // ⚠️ 工具栏「标签」按钮的真实现 —— 之前 showLabels 只被按钮切换、**没有任何消费者**，
  //    点了只变色、标签纹丝不动（典型的假按钮）。判定并入 _vis 后，
  //    隐藏分支（清 opacity / visibility）会自动接管，无需额外的 watcher。
  const labelsOn = showLabels.value

  const items = []
  labelItems.forEach(it => {
    const p = it.anchor.clone().project(camera)
    it._sx = (p.x * 0.5 + 0.5) * W
    it._sy = (-p.y * 0.5 + 0.5) * H
    it._vis = labelsOn && p.z <= 1 && it._sx > -90 && it._sx < W + 90 && it._sy > -70 && it._sy < H + 70
    if (measure) {
      it.w = it.el.offsetWidth || 92
      it.h = it.el.offsetHeight || 22
    }
    items.push(it)
  })

  // 按锚点纵坐标排序 → 避让顺序稳定，不会因遍历顺序变化而抖动
  items.sort((a, b) => a._sy - b._sy)
  const placed = []
  items.forEach(it => {
    if (!it._vis) {
      it.line.style.opacity = 0
      it.dot.style.opacity = 0
      it.el.style.visibility = 'hidden'
      return
    }
    if (it.el.style.visibility === 'hidden') it.el.style.visibility = ''
    if (it.line.style.opacity) it.line.style.opacity = ''   // 清掉隐藏时的内联 0，让 .dim 类重新生效
    if (it.dot.style.opacity) it.dot.style.opacity = ''
    const w = it.w || 92
    const h = it.h || 22
    // ★ 抬牌高度：9px 太贴近锚点，引线短到看不出方向，人眼只能靠"大概在正上方"猜。
    //   抬到 26px 后引线有一段可见长度，"这条线从哪台机器的顶盖长出来"一眼可读。
    //   同时保持尽量低 —— 抬太高会把牌子推到别的设备头顶，那才是真的错位。
    // ★★ 2026-10-18：原来**只做纵向让位**（牌子固定在同一列的竖线上）
    //   —— 结果"搅拌槽(07)"的牌子正正压在"浓密机(06)"这台机器头上，
    //   引线却是搅拌槽自己的（从它顶部的驱动头往下 26px）。老板看到的
    //   "标签和设备不对应"就是这里：牌子的位置说明它属于浓密机，线说它属于搅拌槽。
    //   现在改为**先竖后横**两级避让：
    //     ① 纵向：与已放置的牌子重叠 → 往上让
    //     ② 横向：若让到位后牌子矩形罩住了**别的设备锚点**，左右平移
    //        （优先往画面外侧/远离中线一侧，避免又被自己让回去）
    //   横向平移后引线仍是斜的（从锚点直达牌子底边），指向关系反而更明确。
    let ly = it._sy - LABEL_LIFT              // 标签底边的屏幕 y
    for (let guard = 0; guard < 24; guard++) {
      const l = it._sx - w / 2, r = it._sx + w / 2, t = ly - h, b = ly
      const hit = placed.find(q => !(r < q.l || l > q.r || b < q.t || t > q.b))
      if (!hit) break
      ly = hit.t - 5                         // 与已放置的牌子重叠 → 往上让位
    }
    // ② 横向：牌子不能"坐"在别的设备头上
    //    ⚠️ 只允许"往上再往两侧"避让，**绝不能往下**：
    //      牌子一旦落到锚点下方，引线就变成"从机器头顶往下垂"，指向关系彻底反了。
    let cx = it._sx
    const nearX = w / 2 + 6
    const others = items.filter(q => q !== it && q._vis)
    for (let guard = 0; guard < 8; guard++) {
      const covered = others.find(o =>
        Math.abs(cx - o._sx) < nearX && o._sy > ly - h && o._sy < ly + 4)
      if (!covered) break
      // 往"离它更远"的一侧挪：设备在本机右侧就向左让，反之向右
      cx += (covered._sx > it._sx ? -1 : 1) * (nearX - Math.abs(cx - covered._sx) + 10)
    }
    // ⚠️ 不要在这里回写 it._cx —— 这个属性在循环里被读写（others 引用同一个对象），
    //    改了会让后面设备的判据跟着变，产生顺序依赖的抖动。牌子实际中心直接用局部 cx。
    it._cx = cx        // 仅供 QA 探针读（牌子实际中心），不参与本帧后续计算
    placed.push({ l: cx - w / 2, r: cx + w / 2, t: ly - h, b: ly })

    // 覆盖 CSS2DRenderer 写的 transform（它只做点对点，不做避让）
    it.el.style.transform =
      `translate(-50%, -100%) translate(${cx.toFixed(1)}px, ${ly.toFixed(1)}px)`
    // ★ 引线必须从**标签底边**连回设备锚点。
    //   标签用 translate(-50%,-100%) 定位，ly 是它的**底边** y；
    //   早期把 y2 也写成 ly 就对了，但若标签内部有任何 margin/padding 影响 offsetHeight，
    //   或者以后改成非 -100% 的定位方式，就会脱开。这里显式用「底边 = ly」，
    //   并在量测阶段实测标签矩形校正，避免"线悬在牌子上方一截"的悬浮感。
    // ★★ x2 必须用 cx（牌子实际中心）而不是 _sx（锚点横坐标）——
    //   做了横向避让之后两者不再相等，写 _sx 会让线斜到牌子外面去。
    const topY2 = it._vis ? ly : it._sy
    it.line.setAttribute('x1', it._sx.toFixed(1))
    it.line.setAttribute('y1', it._sy.toFixed(1))
    it.line.setAttribute('x2', cx.toFixed(1))
    it.line.setAttribute('y2', topY2.toFixed(1))
    it.dot.setAttribute('cx', it._sx.toFixed(1))
    it.dot.setAttribute('cy', it._sy.toFixed(1))
  })
}

/**
 * 拾取代理盒 —— 每台设备一个不可见 AABB。
 * hover/点击的射线只测这几个盒子，不再 intersectObjects(scene.children, true)
 * （那会把地面、天空球、网格、300 点微尘和全部设备子网格一起算：又慢，又容易被非设备物体抢命中）。
 *
 * ★★ 代理盒必须**只罩本体、且不放大**，这是"点 A 选中 B"的根因所在：
 *    - 原实现把 X/Z 各乘 1.2，长条设备（宽 10.59 的皮带机）横向被撑到 12.7m，
 *      而相邻设备的间距只有 6m 左右 → 两个盒子大面积互相覆盖，射线先命中谁都看运气。
 *      实测传送带的拾取盒覆盖到了回水池身上，标签锚点射线打中的是 water_return。
 *    - 但完全贴住本体又点不中：细杆件（支腿、拉绳）之间的空隙会让射线穿过去，
 *      用户点在设备"中间"反而落空。所以改为：
 *      ① 横向只收不放（宁可小一点，也不要覆盖邻居），并夹在半个格距内
 *      ② **竖向放大**吸收零件之间的缝（没有人会对"头顶一点空气"主张这是另一台设备）
 *      ③ 代理盒永远不进阴影/发光通道，material.visible=false 即不进渲染列表。
 */
function buildPickProxy(group, equip) {
  const b = bodyBox(group)
  if (b.isEmpty()) return
  const size = b.getSize(new THREE.Vector3())
  const center = b.getCenter(new THREE.Vector3())
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(
      Math.min(Math.max(size.x, 1.6), PROXY_MAX_XZ),
      Math.max(size.y * 1.25, 2.4),
      Math.min(Math.max(size.z, 1.6), PROXY_MAX_XZ)
    ),
    new THREE.MeshBasicMaterial({ visible: false })   // material.visible=false → 不进渲染列表
  )
  // 竖直方向以本体为中心微扩（ground 之上留一点，吸收支腿缝）
  mesh.position.set(center.x, Math.max(center.y, 0.6), center.z)
  mesh.name = 'pickProxy'
  mesh.userData.equipId = equip.id
  mesh.userData.equip = equip
  scene.add(mesh)
  pickTargets.push(mesh)

  // ★★ 再给"矮胖/长条"设备补一个精修盒（retina box）：
  //    单一 AABB 对传送带这种 8m×1m 的长条只能取外包，
  //    相邻的回水池在 Z 方向只差 4m，两个外包盒必然交叠 ——
  //    实测落在皮带上的点，射线先穿过了回水池的盒子。
  //    这里额外挂一个**中心区**小盒（各方向 60%），命中优先级高于外包盒：
  //    用户在设备中心区域点击时，永远归它自己，不受邻居外包盒影响。
  const inner = new THREE.Mesh(
    new THREE.BoxGeometry(
      Math.min(Math.max(size.x * 0.6, 1.2), PROXY_MAX_XZ),
      Math.max(size.y * 0.8, 1.6),
      Math.min(Math.max(size.z * 0.6, 1.0), PROXY_MAX_XZ)
    ),
    new THREE.MeshBasicMaterial({ visible: false })
  )
  inner.position.copy(mesh.position)
  // ⚠️ 名字必须是 pickProxy（与外包盒同名）。这里写成 'facilityProxy' 会让 QA 的
  //    "载体代理数"把设备精修盒也算进去（16 → 26），并且点建筑会误取 userData.facility。
  inner.name = 'pickProxy'
  // 优先级：数字越大越先被选中（pickEquipmentAt 里按这个排序）
  mesh.userData.pickPriority = 0
  inner.userData.pickPriority = 1
  inner.userData.equipId = equip.id
  inner.userData.equip = equip
  scene.add(inner)
  pickTargets.push(inner)
}

/** 拾取盒横向最大边长：设备布局是约 6m 的格距，超过格距就会覆盖到邻居身上 */
const PROXY_MAX_XZ = 5.4

/** 功能载体建筑：id → group（供相机聚焦与高亮） */
const facilityMeshes = new Map()

/**
 * 建出 5 处功能载体并摆位。
 *
 * 为什么建筑也要"可拾取"：
 *   左栏的"公辅与中控"已经列了这些功能，但如果 3D 里的房子点不动，
 *   左侧和场景就还是两套东西 —— 那正是要消灭的"菜单感"。
 *   这里给每栋建筑挂一个隐形 AABB 代理盒（和设备同一套机制），
 *   点建筑 = 左栏对应条目亮起并滚入视野；双击 = 直接进功能。
 */
function buildFacilities() {
  const meta = new Map(NAV_FACILITIES.map((f) => [f.id, f]))
  const defs = [
    { id: 'control_room', make: buildControlRoom },
    { id: 'lab',          make: buildLab },
    { id: 'dosing_room',  make: buildDosingRoom },
    { id: 'power_room',   make: buildPowerRoom },
  ]
  for (const d of defs) {
    const m = meta.get(d.id)
    if (!m) continue
    const g = d.make()
    const L = FACILITY_LAYOUT[d.id]
    g.position.set(L.pos[0], L.pos[1], L.pos[2])
    g.rotation.y = L.rotY
    g.userData.facility = m
    scene.add(g)
    facilityMeshes.set(d.id, g)
    g.updateMatrixWorld(true)
    addFacilityPickProxy(g, m)
  }

  // 监控立杆两根（对角覆盖厂区周界），共用同一份元数据
  const cctvMeta = meta.get('cctv')
  CCTV_SPOTS.forEach((spot, i) => {
    const g = buildCctvPole()
    g.position.set(spot.pos[0], spot.pos[1], spot.pos[2])
    g.rotation.y = spot.rotY
    g.userData.facility = cctvMeta
    scene.add(g)
    if (i === 0) facilityMeshes.set('cctv', g)
    g.updateMatrixWorld(true)
    addFacilityPickProxy(g, cctvMeta)
    addPolePickProxy(g, cctvMeta, 8.4)
  })
}

/** 立杆专用拾取盒 —— 罩住 8.4m 高**杆身的中上段**，而不是整根的外包盒
 *
 *  ★ 为什么需要：立杆是 φ0.14 的细杆 + 顶上一个横臂，外包盒几乎只有杆身那么窄，
 *    屏幕上宽不到 3px。而它下半截又常被水池/设备挡住 —— 用户看着杆子点下去，
 *    射线先打到 8m 外的尾矿池（实测），立杆等于点不中。
 *    这里单独加一个 1.6m×1.6m 的"可点柱"，覆盖从 3m 到杆顶的高度：
 *    既保证杆子上半段整块可点，又不会像整栋楼那样把地面也吞进去。 */
function addPolePickProxy(group, facility, h) {
  const m = new THREE.Mesh(
    new THREE.BoxGeometry(1.6, h - 3.0, 1.6),
    new THREE.MeshBasicMaterial({ visible: false })
  )
  m.position.set(group.position.x, (h + 3.0) / 2, group.position.z)
  m.name = 'facilityProxy'
  m.userData.facility = facility
  m.userData.facilityId = facility.id
  scene.add(m)
  pickTargets.push(m)
}

/** 建筑拾取代理：按建筑**实际包围盒**生成（含屋顶，不外扩）
 *
 *  ★ 曾经只罩"底部 4m"，理由是怕大盒子误命中 —— 那是错的：
 *    · 盒子不会超过建筑本身（Box3 就是从建筑几何算出来的），不会罩住楼顶上的空气
 *    · 而只罩底部会带来更严重的问题：远处建筑的下半截**被前面的设备挡住**，
 *      射线先打到设备，于是"看得见的楼顶点不动、看不见的楼底才点得动" ——
 *      实测中控室和化验室就这样完全点不中。
 *    所以这里给满高度，横向留 4% 余量（避免贴边点不中）。 */
function addFacilityPickProxy(group, facility) {
  const b = new THREE.Box3().setFromObject(group)
  if (b.isEmpty()) return
  const size = b.getSize(new THREE.Vector3())
  const center = b.getCenter(new THREE.Vector3())
  const proxy = new THREE.Mesh(
    new THREE.BoxGeometry(
      Math.max(size.x * 1.04, 1.2),
      Math.max(size.y * 1.02, 1.6),
      Math.max(size.z * 1.04, 1.2)
    ),
    new THREE.MeshBasicMaterial({ visible: false })
  )
  proxy.position.copy(center)
  proxy.name = 'facilityProxy'
  proxy.userData.facility = facility
  proxy.userData.facilityId = facility.id
  scene.add(proxy)
  pickTargets.push(proxy)

  // ★★ 建筑徽标 —— "载体被设备挡住就点不动"的**兜底入口**。
  //    现象（QA 实测）：相机飞到配电室/监控立杆的近景后，建筑的拾取盒
  //    **整块**落在设备后面（配电室距离 16.7m，球磨机在 6.9m），画面里
  //    那一带已经被机器占满 —— 用户看着楼、点下去进的是球磨机页面。
  //    楼不能挪，所以给每栋楼补一个**始终浮在楼顶上方**的小徽标：
  //    图标 + 名称，可点可双击，语义与点楼完全相同。
  //    这也让"左栏列表 ↔ 3D 场景"多了一层一一对应的视觉锚点。
  const badge = document.createElement('div')
  badge.className = 'facility-badge'
  badge.dataset.fid = facility.id
  badge.innerHTML = `<i class="fb-dot"></i><span>${facility.name}</span>`
  const badgeObj = new CSS2DObject(badge)
  // 徽标高度用**世界坐标**反算：group 可能带旋转（配电室 rotY=-90°），
  // 直接写 size.y 会按 group 自身坐标系挂上去，可能挂到楼的侧面去。
  badgeObj.position.set(0, b.max.y - group.position.y + 0.5, 0)
  group.add(badgeObj)
  badge.addEventListener('click', (ev) => {
    ev.stopPropagation()
    selectFacility(facility)
  })
  badge.addEventListener('dblclick', (ev) => {
    ev.stopPropagation()
    if (facility.entries && facility.entries.length) router.push(facility.entries[0].path)
  })
}

/** 标签聚焦：选中 > 悬停。聚焦项发亮、其余淡出，配合引线把"牌 ↔ 设备"钉死 */function applyLabelFocus() {
  const focusId = selectedEquipment.value?.id || hoveredId.value || null
  equipmentMeshes.forEach((group, gid) => {
    const fx = group.userData.fx
    if (!fx) return
    const on = !focusId || focusId === gid
    if (fx.labelEl) {
      fx.labelEl.style.opacity = on ? '1' : '0.22'
      fx.labelEl.classList.toggle('is-focus', !!focusId && focusId === gid)
    }
    if (fx.leaderEls) {
      const isFocus = !!focusId && focusId === gid
      fx.leaderEls.forEach(el => {
        el.classList.toggle('dim', !on)
        el.classList.toggle('is-focus', isFocus)
      })
    }
  })
}

/** 选中设备时高亮它上下游的物料管线（数字孪生的"连接"层：一台设备影响谁、被谁影响） */
function highlightPipelines(equipId) {
  pipelines.forEach(p => {
    const on = !!equipId && (p.fromId === equipId || p.toId === equipId)
    p.tube.material.emissive.setHex(on ? 0x3b82f6 : 0x000000)
    p.tube.material.emissiveIntensity = on ? 1.25 : 0
    p.tube.material.opacity = on ? 0.98 : 0.72
  })
}

/** 每台设备的观测机位（az 方位角° / el 仰角° / dist 距离）
 *  角度原则：从厂区**外侧**看，否则视线会穿过中间的浮选槽把目标挡住 */
const CAM_VIEWS = {
  ball_mill:     { az: 38,  el: 24, dist: 13 },   // 长筒体，斜侧看才看得见衬板与端盖
  flotation_1:   { az: 46,  el: 28, dist: 10 },
  flotation_2:   { az: 132, el: 28, dist: 10 },
  pump_1:        { az: 64,  el: 24, dist: 10 },
  conveyor:      { az: 252, el: 22, dist: 12 },   // 长胶带，从厂区外侧看流向
  thickener:     { az: 34,  el: 32, dist: 11 },   // 圆柱池体，俯视看池面
  agitator:      { az: 200, el: 26, dist: 10 },
  // 水处理池：抬高俯角才看得见水面（el 低于 28 会被池壁挡住）
  tailings_pool: { az: 318, el: 38, dist: 11 },
  water_return:  { az: 300, el: 40, dist: 13 },
  water_dosing:  { az: 330, el: 26, dist: 9 },
}

/** 飞到某台设备的机位 */
function orbitCameraFor(equip) {
  const v = CAM_VIEWS[equip.id] || { az: 45, el: 28, dist: 12 }
  const az = THREE.MathUtils.degToRad(v.az)
  const el = THREE.MathUtils.degToRad(v.el)
  const target = new THREE.Vector3(...equipPos(equip.id, equip))
  target.y = 1.2
  const pos = target.clone().add(new THREE.Vector3(
    Math.cos(el) * Math.cos(az) * v.dist,
    Math.sin(el) * v.dist,
    Math.cos(el) * Math.sin(az) * v.dist
  ))
  animateCamera(pos, target)
}

let _alarmAudioCtx = null
/** 告警提示音（Web Audio API，可开关） */
function playAlarmBeep() {
  if (!alarmSoundEnabled.value) return
  try {
    _alarmAudioCtx = _alarmAudioCtx || new (window.AudioContext || window.webkitAudioContext)()
    const ctx = _alarmAudioCtx
    if (ctx.state === 'suspended') ctx.resume()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.connect(gain); gain.connect(ctx.destination)
    osc.type = 'sine'; osc.frequency.value = 880
    gain.gain.setValueAtTime(0.18, ctx.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4)
    osc.start(); osc.stop(ctx.currentTime + 0.4)
  } catch (e) {}
}

/** 创建告警脉冲效果 */
function createAlarmPulse(model, equip) {
  // 地面警示光环扩散（不用包裹式球体 —— 那会像给设备套了个气球，挡住本体）
  const geo = new THREE.RingGeometry(0.46, 0.6, 64)
  const mat = new THREE.MeshBasicMaterial({
    color: 0xf44336, transparent: true, opacity: 0, side: THREE.DoubleSide,
    depthWrite: false, blending: THREE.AdditiveBlending,
  })
  const pulse = new THREE.Mesh(geo, mat)
  pulse.rotation.x = -Math.PI / 2
  pulse.position.y = 0.32
  pulse.renderOrder = 6
  pulse.name = 'alarmPulse'
  pulse.raycast = () => {}
  model.add(pulse)
  alarmPulses.push({ mesh: pulse, phase: 0, equipId: equip.id })
  playAlarmBeep()
}

// ══════════════════════════════════════════════════
//  动画循环
// ══════════════════════════════════════════════════

let frameNo = 0

function animate() {
  animationId = requestAnimationFrame(animate)

  frameNo++
  const time = performance.now() * 0.001
  // 液面顶点 / 微尘这类"缓慢的细节"隔帧更新：视觉无差，省掉一半顶点上传
  const slowTick = (frameNo & 1) === 0

  // 旋转部件（支持暂停）
  rotatingParts.forEach(part => {
    if (!part.paused) {
      if (part.axis === 'y') part.mesh.rotation.y += part.speed
      else if (part.axis === 'z') part.mesh.rotation.z += part.speed
      else if (part.axis === 'x') part.mesh.rotation.x += part.speed
    }
  })

  // 地面警示光环：由内向外扩散并淡出（周期约 1.6s）
  alarmPulses.forEach(({ mesh }) => {
    const t = (time * 0.62) % 1
    mesh.material.opacity = (1 - t) * 0.6
    mesh.scale.setScalar(1 + t * 3.4)
  })

  // 管内流动粒子：流速 = 上游设备的实时流量（数字孪生：管线是数据，不是装饰）
  particleFlows.forEach(p => {
    const src = equipIndex.get(p.srcId)
    let k = 0.4
    if (src) {
      if (src.status === 'stopped') k = 0    // 上游停机 → 断流；粒子停住，而不是"慢速还在流"
      else k = THREE.MathUtils.clamp((src.metrics?.flow_rate || 0) / 300, 0.4, 2.2)
    }
    p.t = (p.t + p.baseSpeed * k) % 1
    p.mesh.position.copy(p.curve.getPointAt(p.t))
    // 锥尖朝向曲线切线 = 物料实际流向
    p.mesh.quaternion.setFromUnitVectors(UP_AXIS, p.curve.getTangentAt(p.t).normalize())
  })

  // 地面定位环：告警呼吸 + 选中时扫描弧旋转（引用取自 fx 缓存，不再每帧 getObjectByName 递归查找）
  const selectedId = selectedEquipment.value?.id
  equipmentMeshes.forEach(group => {
    const equip = group.userData.equip
    const fx = group.userData.fx
    if (!equip || !fx || !fx.ring) return
    const ring = fx.ring
    const arc = fx.arc
    const selected = selectedId === equip.id
    const alarming = equip.status === 'danger' || equip.status === 'warning'

    const core = selected ? 0.78 : (alarming ? 0.68 : 0.46)
    ring.material.opacity += (core - ring.material.opacity) * 0.12
    ring.scale.setScalar(selected ? 1.04 : 1)

    if (fx.glow) {
      let g = selected ? 0.26 : (alarming ? 0.2 : 0.105)
      if (alarming || selected) g *= 0.68 + Math.abs(Math.sin(time * 2.0)) * 0.6
      fx.glow.material.opacity += (g - fx.glow.material.opacity) * 0.12
    }

    // 选中时扫描弧旋转
    if (arc) {
      const tArc = selected ? 0.9 : 0
      arc.material.opacity += (tArc - arc.material.opacity) * 0.15
      if (arc.material.opacity > 0.01) arc.rotation.z -= 0.032
    }

    // 异常设备光柱缓慢呼吸（"有东西在读它"的暗示）；设备复原 / 停机 → 平滑淡出到 0
    //   （不能只在 alarming 时更新 opacity —— 停机后 status 不再触发更新，
    //     光柱会**残留在最后一次的透明度上**常亮，和"已停机"的灰色标签自相矛盾）
    if (fx.beamParts.length) {
      const o = alarming ? 0.1 + Math.abs(Math.sin(time * 1.05)) * 0.08 : 0
      fx.beamParts.forEach(ob => { ob.material.opacity += (o - ob.material.opacity) * 0.1 })
    }
  })

  // 矿浆液面波动（顶点正弦波）—— 隔帧更新；停机设备液面平滑趋静（搅拌停了）
  if (slowTick) {
    liquidSurfaces.forEach(item => {
      const { mesh, phase, amp } = item
      const eq = equipIndex.get(item.equipId)
      const stopped = !!eq && eq.status === 'stopped'
      item.k += ((stopped ? 0 : 1) - item.k) * 0.06
      if (item.k < 0.005) item.k = 0      // 渐进收敛永远到不了 0，硬归零才是"完全静止"
      const pos = mesh.geometry.attributes.position
      for (let i = 0; i < pos.count; i++) {
        const x = pos.getX(i)
        const y = pos.getY(i)
        const z = (Math.sin(x * 2.5 + time * 2 + phase) * amp
                  + Math.cos(y * 2.5 + time * 1.5 + phase) * amp * 0.6) * item.k
        pos.setZ(i, z)
      }
      pos.needsUpdate = true
    })
  }

  // 皮带矿料颗粒（沿皮带循环移动）；皮带停机 → 矿料静止（皮带停了物料不流动）
  conveyorParticles.forEach(p => {
    const eq = equipIndex.get(p.equipId)
    if (eq && eq.status === 'stopped') return
    p.mesh.position.x += 0.03
    if (p.mesh.position.x > 3.6) p.mesh.position.x = -3.6
    p.mesh.position.y = 1.5 + Math.sin(time * 3 + p.phase) * 0.015
  })

  // 球磨钢球翻滚（筒体局部坐标内极坐标运动）
  ballMillBalls.forEach(({ mesh, baseAng, baseR, baseY, phase }) => {
    const ang = baseAng + time * 0.5 + phase
    // 横截面在 (x, z)，轴向在 y —— 与建模时的局部坐标约定一致
    mesh.position.x = baseR * Math.cos(ang)
    mesh.position.z = baseR * Math.sin(ang)
    mesh.position.y = baseY + Math.sin(time * 1.2 + phase) * 0.08
  })

  // 自动旋转
  if (autoRotate.value) {
    controls.autoRotate = true
    controls.autoRotateSpeed = 0.5
  } else {
    controls.autoRotate = false
  }

  // 状态灯闪烁（danger 状态）
  equipmentMeshes.forEach((group) => {
    const light = group.userData.fx?.light
    if (light && group.userData.equip?.status === 'danger') {
      light.material.opacity = Math.sin(time * 4) > 0 ? 1 : 0.2
    }
  })

  // 空气微尘缓慢上浮 + 轻微横漂（场景的"呼吸感"）—— 隔帧更新
  if (slowTick && ambientDust) {
    const attr = ambientDust.geometry.attributes.position
    const a = attr.array
    for (let i = 0; i < a.length; i += 3) {
      a[i + 1] += 0.0065 * 2
      a[i] += Math.sin(time * 0.22 + a[i + 2] * 0.5) * 0.0024 * 2
      if (a[i + 1] > 26) a[i + 1] = 0.4
    }
    attr.needsUpdate = true
  }

  controls.update()
  if (composer) composer.render()
  else renderer.render(scene, camera)
  labelRenderer.render(scene, camera)
  // 必须在标签渲染之后：避让要覆盖 CSS2DRenderer 写上的 transform
  relayoutLabels()
}

// ══════════════════════════════════════════════════
//  交互事件
// ══════════════════════════════════════════════════

const raycaster = new THREE.Raycaster()
const mouse = new THREE.Vector2()

let pointerDownAt = null    // 左键按下时的位置 → 区分"转视角"和"点击选中"
let dragging = false        // 正在拖拽旋转
let hoverRaf = 0            // hover 检测的 rAF 句柄（mousemove 高频，合并到每帧一次）
const lastPointer = { x: 0, y: 0 }

/**
 * 射线拾取设备 —— 只测设备代理盒，返回命中的设备。
 *
 * ★★ 命中顺序必须按"设备优先"排，不能只认最近的盒子：
 *    建筑代理盒（facilityProxy，中控室/配电室那些）体量大、往往比设备更靠近相机，
 *    若直接取 hits[0]，站在厂房前面点设备会被建筑"抢答"。
 *    实测尾矿池的锚点射线第一命中就是 facilityProxy（dist 5.2），设备本身在 16.7 之外。
 *    设备是操作主体、建筑是入口容器 —— 同一射线上两者都命中时，设备优先。
 */
/**
 * 射线拾取（设备 + 建筑，**一次射线判远近**）
 *
 * ★★ 为什么必须合并成一次：早先是两个函数各自跑一遍射线 ——
 *    pickEquipmentAt 只要有设备就返回（"设备优先"），pickFacilityAt 只要有建筑就返回。
 *    调用方先问设备、设备没有才问建筑。这在"设备被楼挡住"时是对的
 *    （点楼前面的人不该选到楼里的人），但在"设备在楼后面"时完全错了：
 *    实测监控立杆的拾取盒在 13.9m，尾矿池/加药装置在 20.5m，
 *    画面里杆子清清楚楚在前面 —— 点下去却进了「水处理加药装置」。
 *    所以判据必须是**距离**：谁离相机近就是谁。同一位置并列时设备优先
 *    （设备是操作主体、建筑只是入口容器）。
 *
 * 返回 { equip, facility }，可同时为 null。
 */
function pickAtPoint(clientX, clientY) {
  const EMPTY = { equip: null, facility: null }
  if (!canvasContainer.value || !camera || !pickTargets.length) return EMPTY
  const rect = canvasContainer.value.getBoundingClientRect()
  mouse.x = ((clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((clientY - rect.top) / rect.height) * 2 + 1
  raycaster.setFromCamera(mouse, camera)
  const hits = raycaster.intersectObjects(pickTargets, false)
  if (!hits.length) return EMPTY

  // 设备候选：按优先级筛（精修盒 > 外包盒），同优先级才比距离
  const eqHits = hits.filter(h => h.object.userData.equip)
  let bestEquip = null
  if (eqHits.length) {
    const topPri = Math.max(...eqHits.map(h => h.object.userData.pickPriority || 0))
    bestEquip = eqHits.filter(h => (h.object.userData.pickPriority || 0) === topPri)[0]
  }
  const bestFacility = hits.find(h => h.object.userData.facility) || null

  if (bestEquip && bestFacility) {
    // 距离差 < 0.4m 视为并列 → 设备优先（避免"点在机器的地脚螺栓上却选中了楼"）
    if (bestEquip.distance <= bestFacility.distance + 0.4) {
      return { equip: bestEquip.object.userData.equip, facility: null }
    }
    return { equip: null, facility: bestFacility.object.userData.facility }
  }
  return {
    equip: bestEquip ? bestEquip.object.userData.equip : null,
    facility: bestFacility ? bestFacility.object.userData.facility : null,
  }
}

function pickEquipmentAt(clientX, clientY) {
  return pickAtPoint(clientX, clientY).equip
}

/** 只要"这一点上有没有落着这台设备"，不管建筑是否挡在前面
 *
 *  ★ 与 pickEquipmentAt 的差别是**语义**不是实现：
 *    pickEquipmentAt 回答"用户点下去该选谁"（建筑真在前面时就不该选设备）；
 *    本函数回答"这个设备的锚点是不是就在这里" —— QA 用它验标签对应关系，
 *    锚点落在设备自己的拾取盒里就算对，即使那栋楼恰好在更前面。
 *    （实测：尾矿池/加药装置在厂房东侧，建筑代理盒在当前机位下盖在它们前面，
 *      用 pickEquipmentAt 验锚点会得到 null，误判成"标签指错设备"。） */
function equipmentUnderPoint(clientX, clientY) {
  if (!canvasContainer.value || !camera || !pickTargets.length) return null
  const rect = canvasContainer.value.getBoundingClientRect()
  mouse.x = ((clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((clientY - rect.top) / rect.height) * 2 + 1
  raycaster.setFromCamera(mouse, camera)
  const eqHits = raycaster.intersectObjects(pickTargets, false)
    .filter(h => h.object.userData.equip)
  if (!eqHits.length) return null
  const topPri = Math.max(...eqHits.map(h => h.object.userData.pickPriority || 0))
  return eqHits.filter(h => (h.object.userData.pickPriority || 0) === topPri)[0]
    .object.userData.equip
}

/** 射线拾取建筑：与设备共用同一次射线结果（判据见 pickAtPoint） */
function pickFacilityAt(clientX, clientY) {
  return pickAtPoint(clientX, clientY).facility
}

/** 左栏当前"被 3D 点亮的"公辅条目（点击建筑后短暂高亮，把两侧钉在一起） */
const hotFacilityId = ref(null)
let hotFacilityTimer = null

/** 点建筑 = 相机飞过去 + 左栏对应条目高亮并滚入视野 */
function selectFacility(facility) {
  if (!facility) return
  hotFacilityId.value = facility.id
  // 3D 徽标同步点亮 —— CSS2D 的 DOM 在画布层外面，Vue 的 :class 管不到它，
  // 必须手动切（否则左栏亮了、画面里的楼没反应，两边还是割裂的）
  document.querySelectorAll('.facility-badge').forEach((el) => {
    el.classList.toggle('is-hot', el.dataset.fid === facility.id)
  })

  const g = facilityMeshes.get(facility.id)
  if (g) {
    const c = new THREE.Box3().setFromObject(g).getCenter(new THREE.Vector3())
    // 从前上方靠近：近景看清建筑立面，同时不钻进楼里
    animateCamera(new THREE.Vector3(c.x + 11, c.y + 8.5, c.z + 15), c)
  }

  nextTick(() => {
    const el = document.querySelector(`.facility[data-fid="${facility.id}"]`)
    if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  })

  if (hotFacilityTimer) clearTimeout(hotFacilityTimer)
  hotFacilityTimer = setTimeout(() => { hotFacilityId.value = null }, 3000)
}

/** 切换 hover 目标（只在真正变化时才动 DOM/材质，避免每帧无谓重排） */
function setHovered(equip) {
  const id = equip?.id || null
  if (hoveredId.value === id) return
  hoveredId.value = id
  hoveredEquipment.value = equip || null
  applyLabelFocus()
  if (canvasContainer.value) canvasContainer.value.style.cursor = id ? 'pointer' : ''
}

/** 直写悬浮卡位置。
 *  hover 时鼠标每帧都在动，若走响应式就会把整个页面组件（含侧栏/HUD/列表）重渲染一遍，
 *  既慢又有时序问题 —— 位置只改这一个元素的 style 即可。 */
function placeHoverCard(x, y) {
  hoverPos.value = { x, y }        // 仅作记录（QA 读取用），模板不再绑定它
  const el = hoverCardRef.value
  if (el) {
    el.style.left = `${Math.round(x)}px`
    el.style.top = `${Math.round(y)}px`
  }
}

/** 刷新悬浮卡 */
function updateHover(clientX, clientY) {
  if (!canvasContainer.value) return
  if (dragging || selectedEquipment.value) { setHovered(null); return }
  const equip = pickEquipmentAt(clientX, clientY)
  setHovered(equip)
  if (!equip) return
  const rect = canvasContainer.value.getBoundingClientRect()
  const CARD_W = 208, CARD_H = 152
  let x = clientX - rect.left + 16
  let y = clientY - rect.top + 16
  if (x + CARD_W > rect.width - 8) x = clientX - rect.left - CARD_W - 16
  if (y + CARD_H > rect.height - 8) y = clientY - rect.top - CARD_H - 16
  placeHoverCard(Math.max(8, x), Math.max(8, y))
}

function onPointerDownEvent(event) {
  if (event.button !== 0) return
  pointerDownAt = { x: event.clientX, y: event.clientY }
  dragging = false
}

function onPointerUpEvent() {
  pointerDownAt = null
}

function onMouseMove(event) {
  if (!canvasContainer.value || !camera) return
  // 鼠标落在读数牌上：直接锁定它对应的设备（牌 ↔ 设备，零歧义），卡片挪到牌子左侧不挡牌
  const labelEl = event.target && event.target.closest ? event.target.closest('.equip-label') : null
  if (labelEl) {
    const eq = equipIndex.get(labelEl.dataset.equipId)
    setHovered(eq || null)
    if (eq) {
      const rect = canvasContainer.value.getBoundingClientRect()
      placeHoverCard(
        Math.max(8, Math.min(event.clientX - rect.left - 216, rect.width - 216)),
        Math.max(8, event.clientY - rect.top + 18)
      )
    }
    return
  }
  // ★ 同理：鼠标落在浮层上时清掉 3D 悬浮态 —— 否则把鼠标移过详情面板，
  //   面板背后的设备还会被"点亮"并弹出悬浮卡，像是点到了别的东西。
  if (event.target !== renderer.domElement) {
    setHovered(null)
    return
  }
  // 按住左键 = 正在转视角：不弹悬浮卡（卡片跟着甩会晃得看不清）
  if (event.buttons === 1) {
    dragging = true
    setHovered(null)
    return
  }
  lastPointer.x = event.clientX
  lastPointer.y = event.clientY
  if (hoverRaf) return
  hoverRaf = requestAnimationFrame(() => {
    hoverRaf = 0
    updateHover(lastPointer.x, lastPointer.y)
  })
}

function onMouseLeave() {
  setHovered(null)
}

function onMouseClick(event) {
  if (!canvasContainer.value) return
  // ★★ 只响应"真正打在 3D 画布上"的点击。
  //   `canvasContainer` 是所有浮层的**共同祖先**（详情面板 / 顶部工具栏 / 操作日志都在它里面），
  //   事件会从按钮一路冒泡到这里。不筛 target 的话，点详情面板里的「停机 / 启动 / 报修」，
  //   射线会重新打向**面板背后**的 3D 场景：
  //     · 命中设备 → 选中态被换成那台设备
  //     · 没命中   → 直接取消选中，面板消失
  //   表现就是「弹窗里写的是 A 设备、面板已经变成 B / 面板直接没了」。
  //   实测复现：点皮带机的停机按钮，选中态变成了 1#浮选槽。这是"启停按钮和弹窗对不上"的**真根因**。
  if (event.target !== renderer.domElement) return
  // 拖过视角（位移 > 6px）→ 不算点击，免得转个角度就把设备选上
  if (pointerDownAt) {
    const moved = Math.hypot(event.clientX - pointerDownAt.x, event.clientY - pointerDownAt.y)
    if (moved > 6) return
  }
  if (dragging) return
  const equip = pickEquipmentAt(event.clientX, event.clientY)
  if (equip) {
    setHovered(null)
    selectEquipment(equip)
    highlightEquipment(equip.id)
    return
  }
  // 没点中设备 → 看看是不是点了某栋功能载体建筑
  const fac = pickFacilityAt(event.clientX, event.clientY)
  if (fac) {
    deselectEquipment()
    selectFacility(fac)
    return
  }
  // 点击空白处取消选择
  deselectEquipment()
}

/** 双击设备 → 直接进入它所属工艺段的功能页
 *
 *  交互分工（避免"单击就跳走"导致误操作）：
 *    · 单击 = 看（弹详情卡：指标 / 趋势 / 预测维护，仍留在 3D 里）
 *    · 双击 = 进（一步到功能页，现场演示最快的那条路）
 *  两条路的落点由 routeFor() 统一决定，和详情卡上的「进入XXX」按钮完全一致。
 *  ⚠️ 与单击一样必须筛 target + 判拖拽：双击的第二下同样会冒泡、
 *     也有人在双击时手抖拖了一点视角。 */
function onCanvasDblClick(event) {
  if (!canvasContainer.value || !renderer) return
  if (event.target !== renderer.domElement) return
  if (pointerDownAt) {
    const moved = Math.hypot(event.clientX - pointerDownAt.x, event.clientY - pointerDownAt.y)
    if (moved > 6) return
  }
  const equip = pickEquipmentAt(event.clientX, event.clientY)
  if (equip) {
    const target = routeFor(equip.id)
    if (target) router.push(target.route)
    return
  }
  // 双击建筑 → 直接进该建筑的首个功能（如双击中控室 → 生产总览）
  const fac = pickFacilityAt(event.clientX, event.clientY)
  if (fac && fac.entries && fac.entries.length) {
    router.push(fac.entries[0].path)
  }
}

function selectEquipment(equip) {
  selectedEquipment.value = equip
  applyLabelFocus()
  if (equip) loadPrediction(equip)
}

function highlightEquipment(id) {
  // 聚焦 = ①选中设备热力增强 ②其余设备标签淡出 ③上下游物料管线高亮
  equipmentMeshes.forEach((group, gid) => {
    applyTwinTint(group, group.userData.equip, gid === id)
  })
  applyLabelFocus()
  highlightPipelines(id)
}

function resetHighlights() {
  equipmentMeshes.forEach(group => {
    applyTwinTint(group, group.userData.equip, false)
  })
  applyLabelFocus()
  highlightPipelines(null)
}

/** 聚焦到指定设备 */
function focusEquipment(id) {
  const group = equipmentMeshes.get(id)
  if (!group) return
  const equip = group.userData.equip
  if (!equip) return
  orbitCameraFor(equip)      // 按设备形态飞到观测机位（不是统一偏移）
  selectEquipment(equip)
  highlightEquipment(id)
}

/** 进入当前选中设备对应的功能页
 *
 *  为什么是"显式按钮"而不是"再点一次设备就跳"：
 *  单击跳转会让人不敢点设备看读数（这是 3D 首页最高频的动作），
 *  而且视角重叠时容易误触。按钮把"看"和"进"两件事分开，
 *  也不需要用户去猜"再点一次会发生什么"。
 */
function enterSegment(equipId) {
  const target = routeFor(equipId || selectedEquipment.value?.id)
  if (!target) return
  router.push(target.route)
}

/** 点击段位条：飞到该段第一台设备并选中，再给用户看详情 */
function focusSegment(seg) {
  const first = seg.equipIds.find((eid) => equipmentMeshes.has(eid))
  if (first) focusEquipment(first)
}

/** 直接进入某段的功能页（段位条上的"进入"快捷入口） */
function enterSegmentDirect(seg) {
  router.push(seg.route)
}

/** 相机运动的"世代号"：任何新的镜头/轴心动作一开，旧动画自动失效。
 *  否则「刚点设备卡（900ms 飞行）→ 立刻点空白取消选中」会让两个动画同时写
 *  camera.position / controls.target，画面抖一下且最终落点不确定。 */
let _camMotionSeq = 0

/** 相机飞行：easeInOutCubic + 中段弧线抬升，像无人机飞过去而不是线性插值 */
function animateCamera(targetPos, lookAt) {
  const seq = ++_camMotionSeq
  const startPos = camera.position.clone()
  const startTarget = controls.target.clone()
  const duration = 900
  const startTime = performance.now()
  const lift = Math.min(6, startPos.distanceTo(targetPos) * 0.22)

  function step() {
    if (seq !== _camMotionSeq) return          // 已被更新的动作接管
    const t = Math.min((performance.now() - startTime) / duration, 1)
    const ease = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2

    camera.position.lerpVectors(startPos, targetPos, ease)
    camera.position.y += Math.sin(Math.PI * ease) * lift
    controls.target.lerpVectors(startTarget, lookAt, ease)

    if (t < 1) requestAnimationFrame(step)
  }
  step()
}

/** 重置相机视角 */
function resetCamera() {
  // ★ 必须与初始机位一致 —— 之前"复位"回到 (14,10,14)，比首屏更近：
  //   复位后配电室又滑出画面，用户会以为"复位把楼弄丢了"。
  // ★ 也不能写死 (21,14,26.5)：窄屏首屏是后撤过的，写死复位会把画面拉近一截。
  animateCamera(HOME_POS(), HOME_AIM.clone())
  selectedEquipment.value = null
  resetHighlights()
}

/** 全景距离：与初始机位(14,10,14)→(0,1,0) 的 ~28 同量级 */
const OVERVIEW_DIST = 26

/** 把"绕谁转"的轴心平滑送回场景中心（保持当前观察方向，把距离归位到全景）。
 *
 * ⚠️ 为什么必须有这个函数：
 *   `orbitCameraFor()` 会把 `controls.target` 设成**被点的那台设备**，
 *   而除了「复位」按钮，代码里**再没有任何地方把它改回来**。
 *   于是「点过皮带输送机 → 取消选中 → 拖拽旋转 / 开自转」时，
 *   整个厂区会一直**绕着皮带输送机甩**，界面上却完全看不出发生了什么。
 *   （实测：取消选中后 controls.target 仍残留 (3.5, 1.2, 5.5)，正是皮带机坐标）
 *
 * 为什么连相机位置一起动：
 *   只改 target 的话，相机原地不动 → 与新轴心的距离可能**小于 minDistance(8)**，
 *   OrbitControls 会把相机硬拉近 → 取消选中后变成"钻进厂区中间"。
 *   实测正是如此（取消后距轴心 8.00 = minDistance，说明被夹过）。
 *   所以这里沿着**当前视线方向**重新定一个全景距离：俯仰/方位角（用户调好的角度）不丢，
 *   距离和轴心一起归位，结果确定、不受限位影响。
 */
function recenterOrbit(duration = 420) {
  if (!controls) return
  const fromT = controls.target.clone()
  const fromP = camera.position.clone()
  const settling = fromT.distanceTo(SCENE_CENTER) < 0.02 &&
    Math.abs(fromP.distanceTo(fromT) - OVERVIEW_DIST) < 2.5
  if (settling) return                                     // 已经在全景，不折腾
  // 观察方向 = 相机相对旧轴心的方向；沿它把相机摆到"以场景中心为轴心的全景距离"上
  const dir = fromP.clone().sub(fromT).normalize()
  const toT = SCENE_CENTER.clone()
  const toP = toT.clone().add(dir.multiplyScalar(OVERVIEW_DIST))

  const seq = ++_camMotionSeq                              // 接管相机运动，取消在飞的镜头动画
  const t0 = performance.now()
  const step = () => {
    if (seq !== _camMotionSeq) return
    const t = Math.min((performance.now() - t0) / duration, 1)
    const e = 1 - Math.pow(1 - t, 3)                       // easeOutCubic
    controls.target.lerpVectors(fromT, toT, e)
    camera.position.lerpVectors(fromP, toP, e)
    if (t < 1) requestAnimationFrame(step)
  }
  step()
}

/** 取消选中 —— **统一入口**：面板、高亮、旋转轴心 三件事一起复位。
 *  ⚠️ 别再各处写 `selectedEquipment.value = null; resetHighlights()`：
 *    漏掉 recenterOrbit 就会退回"厂区绕着某台设备甩"的 bug。 */
function deselectEquipment() {
  selectedEquipment.value = null
  resetHighlights()
  recenterOrbit()
}

// ══════════════════════════════════════════════════
//  生命周期
// ══════════════════════════════════════════════════

/** 渲染趋势图表（含温度/振动/健康度阈值红线） */
function renderTrendChart() {
  if (!trendChartRef.value || trendData.value.length === 0) return
  const chart = echarts.init(trendChartRef.value)
  const times = trendData.value.map(d => (d.time || '').slice(11, 16))
  const health = trendData.value.map(d => d.health)
  const temp = trendData.value.map(d => d.temperature != null ? Number(d.temperature).toFixed(1) : null)
  const vib = trendData.value.map(d => d.vibration != null ? Number(d.vibration).toFixed(1) : null)

  const series = [{
    name: '健康度', type: 'line', smooth: true, data: health, yAxisIndex: 0,
    lineStyle: { width: 2.5 }, itemStyle: { color: '#00b42a' },
    areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
      colorStops: [{ offset: 0, color: 'rgba(76,175,80,0.25)' }, { offset: 1, color: 'rgba(76,175,80,0)' }] } },
    markLine: { silent: true, symbol: 'none', data: [
      { yAxis: 50, lineStyle: { color: '#f53f3f', type: 'dashed', width: 1 }, label: { formatter: '危险线', position: 'end', fontSize: 10 } },
    ] }
  }]
  if (temp.some(v => v != null)) series.push({
    name: '温度°C', type: 'line', smooth: true, data: temp, yAxisIndex: 1,
    lineStyle: { width: 1.5, type: 'dashed' }, itemStyle: { color: '#f53f3f' },
    markLine: { silent: true, symbol: 'none', data: [
      { yAxis: 60, lineStyle: { color: '#e91e63', type: 'dotted', width: 1 }, label: { formatter: '温警60°C', position: 'end', fontSize: 10 } },
    ] }
  })
  if (vib.some(v => v != null)) series.push({
    name: '振动mm/s', type: 'bar', data: vib, yAxisIndex: 1,
    itemStyle: { color: '#ff7d00', opacity: 0.6 }, barWidth: '40%',
    markLine: { silent: true, symbol: 'none', data: [
      { yAxis: 3.5, lineStyle: { color: '#f53f3f', type: 'dotted', width: 1 }, label: { formatter: '振警3.5', position: 'end', fontSize: 10 } },
    ] }
  })

  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: series.map(s => s.name), top: 0 },
    grid: { top: 40, right: 20, bottom: 30, left: 50 },
    xAxis: { type: 'category', data: times, axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '健康度 %', min: 0, max: 100, position: 'left' },
      { type: 'value', name: '温度/振动', position: 'right' },
    ],
    series,
  }, true)
  window.__trendChart = chart
}

/** 加载预测维护数据 */
async function loadPrediction(equip) {
  if (!equip) { predictionData.value = null; return }
  predictionLoading.value = true
  predictionData.value = null
  try {
    const data = await api.get(`/equipment/${equip.id}/prediction`, { params: { horizon_days: 14 } })
    predictionData.value = data
    await nextTick()
    renderPredictionChart()
  } catch (e) {
    predictionData.value = null
  } finally {
    predictionLoading.value = false
  }
}

/** 渲染预测健康度衰减曲线 */
function renderPredictionChart() {
  if (!predictionChartRef.value || !predictionData.value?.forecast) return
  const chart = echarts.init(predictionChartRef.value)
  const forecast = predictionData.value.forecast
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { top: 16, right: 12, bottom: 24, left: 32 },
    xAxis: { type: 'category', data: forecast.map(f => (f.date || '').slice(5)), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', min: 0, max: 100 },
    series: [{
      type: 'line', smooth: true, data: forecast.map(f => f.health),
      lineStyle: { width: 2.5 }, itemStyle: { color: '#5347c4' },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [{ offset: 0, color: 'rgba(83,71,196,0.25)' }, { offset: 1, color: 'rgba(83,71,196,0)' }] } },
      markLine: { silent: true, symbol: 'none', data: [
        { yAxis: 50, lineStyle: { color: '#f53f3f', type: 'dashed' }, label: { formatter: '危险', position: 'end', fontSize: 10 } },
        { yAxis: 70, lineStyle: { color: '#ff7d00', type: 'dotted' }, label: { formatter: '关注', position: 'end', fontSize: 10 } },
      ] },
    }]
  })
}

onMounted(() => {
  // ★ 场景初始化必须兜住异常：initScene 里是一长串建模代码（柱网/建筑/管线），
  //   任何一处抛错都会中断后面所有步骤 —— 而 UI 还停在"正在加载设备数据..."，
  //   看起来像"后端挂了"。实测踩过：一个变量声明顺序错了，整页黑屏且无任何提示。
  //   这里把错误显式抛到界面上，至少能立刻看出是哪一段炸了。
  try {
    initScene()           // 初始化 3D 场景（地面/灯光/相机/厂房/建筑）
    animate()             // 启动渲染循环
  } catch (err) {
    console.error('[Equipment3D] 场景初始化失败', err)
    isLoading.value = false
    sceneError.value = `3D 场景初始化失败：${err?.message || err}`
    return
  }
  fetchEquipmentData()  // 首次拉取数据 → 放置设备
  refreshLogs()         // 操作日志（后端落库，刷新不丢）
  fetchOrders()         // 工单角标：未闭环工单数

  // 定时刷新（5 秒）
  refreshTimer = setInterval(fetchEquipmentData, REFRESH_INTERVAL)

  if (canvasContainer.value) {
    canvasContainer.value.addEventListener('click', onMouseClick)
    canvasContainer.value.addEventListener('dblclick', onCanvasDblClick)
    canvasContainer.value.addEventListener('mousemove', onMouseMove)
    canvasContainer.value.addEventListener('pointerdown', onPointerDownEvent)
    canvasContainer.value.addEventListener('pointerup', onPointerUpEvent)
    canvasContainer.value.addEventListener('pointercancel', onPointerUpEvent)
    canvasContainer.value.addEventListener('mouseleave', onMouseLeave)
  }
  window.addEventListener('resize', onResize)

  // 设备清单贴住操作日志上沿（日志高度随视口自适应，必须实测而非写死）
  nextTick(() => {
    syncDockBottom()
    if (window.ResizeObserver) {
      dockObserver = new ResizeObserver(syncDockBottom)
      // 日志面板可能还没渲染，轮询到它出现再挂观察者
      const tryObserve = (left = 12) => {
        const logEl = document.querySelector('.op-log-panel')
        if (logEl) dockObserver.observe(logEl)
        else if (left > 0) setTimeout(() => tryObserve(left - 1), 400)
      }
      tryObserve()
    }
  })
})

/** 实测操作日志面板高度 → 把设备清单顶到它上方。
 *  ★★ 两个坑（都靠实测数值才暴露，肉眼看截图分辨不出来）：
 *   1) 选择器范围：`.op-log-panel` 挂在 `.main-layout` 下，**不在 `.viewport` 里**。
 *      早期写成 canvasContainer.querySelector(...) 永远查不到 → 一直回落 12px → 压住日志。
 *      这里改用整页查询。
 *   2) 时序：日志面板是 v-if="operationLogs.length" 渲染的，首帧还不存在。
 *      必须轮询等它出现再测，否则同样回落成 12px。 */
let dockRetry = 0
function syncDockBottom() {
  const logEl = document.querySelector('.op-log-panel')
  if (!logEl) {
    dockBottom.value = '12px'
    if (dockRetry < 10) { dockRetry += 1; setTimeout(syncDockBottom, 400) }
    return
  }
  dockRetry = 0
  dockBottom.value = `${Math.round(logEl.getBoundingClientRect().height) + 22}px`
}

onBeforeUnmount(() => {
  if (animationId) cancelAnimationFrame(animationId)
  if (refreshTimer) clearInterval(refreshTimer)
  window.removeEventListener('resize', onResize)
  if (dockObserver) { dockObserver.disconnect(); dockObserver = null }
  if (canvasContainer.value) {
    canvasContainer.value.removeEventListener('click', onMouseClick)
    canvasContainer.value.removeEventListener('dblclick', onCanvasDblClick)
    canvasContainer.value.removeEventListener('mousemove', onMouseMove)
    canvasContainer.value.removeEventListener('pointerdown', onPointerDownEvent)
    canvasContainer.value.removeEventListener('pointerup', onPointerUpEvent)
    canvasContainer.value.removeEventListener('pointercancel', onPointerUpEvent)
    canvasContainer.value.removeEventListener('mouseleave', onMouseLeave)
  }
  if (hoverRaf) cancelAnimationFrame(hoverRaf)
  // 清理 Three.js 资源
  if (composer) composer.dispose?.()
  if (bloomPass) bloomPass.dispose?.()
  if (renderer) renderer.dispose()
  if (scene) scene.traverse(obj => { if (obj.geometry) obj.geometry.dispose(); if (obj.material) obj.material.dispose() })
})

function onResize() {
  viewH.value = window.innerHeight
  syncDockBottom()
  if (!canvasContainer.value || !camera || !renderer || !labelRenderer) return
  const w = canvasContainer.value.clientWidth
  const h = canvasContainer.value.clientHeight
  camera.aspect = w / h
  camera.updateProjectionMatrix()
  renderer.setSize(w, h)
  labelRenderer.setSize(w, h)
  if (composer) composer.setSize(w, h)
  // composer.setSize 会把 bloom 重置成全分辨率 → 这里再压回半分辨率
  if (bloomPass) bloomPass.setSize(Math.floor(w * BLOOM_SCALE), Math.floor(h * BLOOM_SCALE))
  fitHomeDistance()
}

/**
 * 按视口宽高比自适应机位距离 —— 保证**配电室**（东北角那栋楼）始终完整落在画面内。
 *
 * ★ 为什么必须做：透视相机的水平视野 = 垂直 fov × aspect。笔记本 1440×900 时
 *   3D 视口只有约 928×560（左栏吃掉 500px），aspect 掉到 1.66，
 *   水平半角从 33° 缩到 29° —— 配电室被挤出右边界，画面上整栋楼不见了。
 *   这里按 aspect 反算需要拉远多少，并叠加一个 FIT_MIN_SCALE 的保底后撤。
 *
 * 只动**相机距离**，不动 fov：广角会带来明显透视畸变，厂房会"歪"。
 */
const HOME_AIM = new THREE.Vector3(-0.5, 1.7, 0)
const HOME_DIR = new THREE.Vector3(21 - HOME_AIM.x, 14 - HOME_AIM.y, 26.5 - HOME_AIM.z)
const HOME_DIST = HOME_DIR.length()      // 1920 基准距离
const HOME_ASPECT = 1408 / 979           // 1920 下 3D 视口的宽高比

/**
 * 统一机位倍率。
 * ★★ 为什么在 1920 上也要退：配电室（东北角 12.9,-12.1）在基准机位下
 *    楼体右缘投影 x1≈1902，视口右界 1900 —— 贴着边缘只露一条缝；
 *    1440/1600 更惨（满屏只剩一个蓝色徽标在画外缘）。
 *    而配电室主体又被厂房钢柱压住西半边，挪位置只会撞上球磨机。
 *    退 8% 换来的余量，比继续微调位置更可靠：三个尺寸一次全落在框内。
 */
const FIT_MIN_SCALE = 1.08

/**
 * 按当前相机宽高比算出「首屏 / 复位」的落点。
 * ★ 复位也走这里 —— 否则窄屏复位回的是 1920 的机位，配电室又滑出画面。
 */
function HOME_POS() {
  const k = (camera && camera.aspect < HOME_ASPECT) ? HOME_ASPECT / camera.aspect : 1
  const dist = HOME_DIST * Math.min(Math.max(k, FIT_MIN_SCALE), 1.55)
  return HOME_DIR.clone().normalize().multiplyScalar(dist).add(HOME_AIM)
}

function fitHomeDistance() {
  if (!camera) return
  camera.position.copy(HOME_POS())
  // ⚠️ controls 在 initScene 里比相机晚创建：首帧调用时它还不存在，
  //    直接写 controls.target 会抛 "Cannot read properties of undefined"，
  //    整个 3D 初始化就此中断（页面上只剩一句"3D 场景初始化失败"）。
  if (controls) {
    controls.target.copy(HOME_AIM)
    controls.update()
  } else {
    camera.lookAt(HOME_AIM)
  }
}

// ── 计算属性 ──────────────────────────────────────
const stats = equipmentStats

/** 数据同步时刻：用本机时间（后端时戳为无时区的 UTC，直接显示会误导为"8 点的旧数据"） */
const dataClock = computed(() => lastUpdateTime.value || '—')

/** 取某台设备的当前流量（HUD 用，与 3D 场景中管线粒子速度同源） */
function eqFlow(id) {
  const e = equipments.value.find(x => x.id === id)
  return e?.metrics?.flow_rate ?? null
}

const statusText = { running: '运行中', warning: '预警', stopped: '已停机', danger: '故障' }

function statusTagType(status) {
  return { running: 'success', warning: 'warning', stopped: 'info', danger: 'danger' }[status] || 'info'
}

function healthColor(h) {
  if (h >= 80) return '#00b42a'
  if (h >= 50) return '#ff7d00'
  return '#f53f3f'
}
</script>

<template>
  <div class="equipment-page">
    <!-- ══ 顶部工具栏 ══ -->
    <div class="toolbar">
      <div class="toolbar-left">
        <h2 class="page-title">
          <span class="icon"></span> 设备监测中心
        </h2>
        <div class="stat-chips">
          <!-- 四档 = 后端 status 字段的完整取值域。别写成"运行/预警/故障/停机"：
               warning/danger 的设备**也在运行**，叫"运行"会让同一行自相矛盾 -->
          <el-tag :type="'success'" effect="dark" size="small">
            正常 {{ stats.running }}/{{ stats.total }}
          </el-tag>
          <el-tag v-if="stats.warning" type="warning" effect="dark" size="small">
            预警 {{ stats.warning }}
          </el-tag>
          <el-tag v-if="stats.danger" type="danger" effect="dark" size="small">
            故障 {{ stats.danger }}
          </el-tag>
          <!-- 停机是一档独立状态：不加这一条，"运行+预警+故障"会凑不齐总数，看起来像丢设备 -->
          <el-tag v-if="stats.stopped" type="info" effect="dark" size="small">
            停机 {{ stats.stopped }}
          </el-tag>
          <el-tag effect="plain" size="small">均健康 {{ stats.avg_health ?? '—' }}%</el-tag>
          <span class="refresh-hint" v-if="lastUpdateTime">
            {{ lastUpdateTime }}
          </span>
        </div>
      </div>
      <div class="toolbar-right">
        <el-badge :value="pendingOrderCount" :hidden="!pendingOrderCount" :offset="[-4, 4]" class="order-badge">
          <el-button size="small" type="primary" plain @click="openOrderCenter">
            报修工单
          </el-button>
        </el-badge>
        <el-button-group>
          <el-button :type="showLabels ? 'primary' : ''" size="small" @click="showLabels = !showLabels">
            标签
          </el-button>
          <el-button
            :type="autoRotate ? 'primary' : ''"
            size="small"
            title="镜头绕当前轴心旋转：未选中设备时绕厂区中心，选中某台设备时绕那台设备"
            @click="autoRotate = !autoRotate"
          >
            自转
          </el-button>
          <el-button :type="alarmSoundEnabled ? 'primary' : ''" size="small" @click="alarmSoundEnabled = !alarmSoundEnabled">
            {{ alarmSoundEnabled ? '告警音' : '静音' }}
          </el-button>
          <el-button size="small" @click="resetCamera">复位</el-button>
        </el-button-group>
      </div>
    </div>

    <!-- ══ 主内容区 ══ -->
    <div class="main-layout">
      <!-- 左侧内栏：厂区导览（工艺链 + 公辅载体）
           ★ 设计意图：这一栏不是"功能清单"，而是**厂区的目录**。
             上面一截是物料流经的工艺链（与 3D 里的管线一一对应），
             下面一截是中控室/化验室/药剂间/配电室/监控立杆这些**建筑**，
             每个功能都挂在它真实发生的那间屋子里。
             这样无论从左侧点、还是从 3D 里点建筑，进的是同一个门。 -->
      <div class="side-panel">
        <div class="panel-header">
          厂区导览
          <span class="panel-sub">工艺链 · 功能落点</span>
        </div>
        <div class="side-scroll">
          <!-- 一、工艺链：纵向物料流，段与段之间用时间轴连线贯穿 -->
          <div class="side-group">
            <div class="side-group-title">工艺流程</div>
            <div class="chain">
              <div
                v-for="seg in navSegments"
                :key="seg.id"
                class="chain-node"
                :class="{ 'is-primary': seg.primary, 'is-active': activeSegment?.id === seg.id, 'is-open': expandedSeg === seg.id }"
              >
                <div class="chain-row" @click="toggleSegment(seg)">
                  <i class="chain-dot" :style="{ background: seg.color }"></i>
                  <span class="chain-name">{{ seg.name }}</span>
                  <span v-if="seg.count" class="chain-health" :style="{ color: seg.bad ? '#f53f3f' : (seg.avgHealth < 60 ? '#ff7d00' : '#5c6b7f') }">
                    {{ seg.avgHealth ?? '—' }}<em>%</em>
                  </span>
                  <el-icon class="chain-caret" :class="{ 'is-open': expandedSeg === seg.id }"><ArrowRight /></el-icon>
                </div>

                <!-- 段摘要：台数 + 异常，收起时也看得到 -->
                <div class="chain-meta">
                  <span>{{ seg.count }} 台设备</span>
                  <span v-if="seg.bad" class="seg-bad">{{ seg.bad }} 故障</span>
                  <span v-else-if="seg.warn" class="seg-warn">{{ seg.warn }} 预警</span>
                  <span v-else class="seg-ok">全部正常</span>
                </div>

                <!-- 展开区：功能入口 + 设备清单 -->
                <div v-show="expandedSeg === seg.id" class="chain-drawer">
                  <div class="drawer-label">进入功能</div>
                  <button
                    v-for="en in seg.entries"
                    :key="en.path"
                    class="fn-chip is-main"
                    :style="{ '--c': seg.color }"
                    @click.stop="enterSegmentDirect({ ...seg, route: en.path, routeLabel: en.label })"
                  >
                    <span>{{ en.label }}</span>
                    <el-icon><ArrowRight /></el-icon>
                  </button>
                  <div class="drawer-label">段内设备</div>
                  <div
                    v-for="eq in seg.eqs"
                    :key="eq.id"
                    class="eq-mini"
                    @click.stop="focusEquipment(eq.id)"
                  >
                    <i class="eq-dot" :class="`dot-${eq.status}`"></i>
                    <span class="eq-mini-name">{{ eq.name }}</span>
                    <span class="eq-mini-h" :style="{ color: healthColor(eq.health) }">{{ eq.health }}%</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 二、公辅与中控：功能在厂区里的物理载体 -->
          <div class="side-group">
            <div class="side-group-title">公辅与中控</div>
            <div
              v-for="f in NAV_FACILITIES"
              :key="f.id"
              class="facility"
              :class="{ 'is-hot': hotFacilityId === f.id }"
              :data-fid="f.id"
            >
              <div class="fac-row">
                <el-icon class="fac-icon"><component :is="f.icon" /></el-icon>
                <span class="fac-name">{{ f.name }}</span>
                <span class="fac-tag">{{ f.tag }}</span>
              </div>
              <div class="fac-chips">
                <router-link
                  v-for="en in f.entries"
                  :key="en.path"
                  :to="en.path"
                  class="fn-chip"
                >{{ en.label }}</router-link>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 中间 3D 视图 -->
      <div class="viewport" ref="canvasContainer">
        <!-- 加载状态 -->
        <div v-if="isLoading" class="loading-overlay">
          <div class="loading-spinner"></div>
          <span>正在加载设备数据...</span>
        </div>
        <!-- 场景初始化失败：不能只是黑屏，必须说清是哪一步炸了 -->
        <div v-else-if="sceneError" class="loading-overlay is-error">
          <span>{{ sceneError }}</span>
        </div>

        <!-- 数字孪生 HUD：色标解释 + 工况 + 数据同步时刻（3D 视图内部的数据锚定） -->
        <div class="twin-hud" v-if="!isLoading">
          <div class="hud-head">
            <span class="hud-live"></span>
            <span class="hud-title">数字孪生同步</span>
            <span class="hud-clock">{{ dataClock }}</span>
          </div>
          <div class="hud-row">
            <span class="hud-k">健康度热力</span>
            <span class="hud-scale">
              <i class="sw-h sw-g"></i><em>≥80</em>
              <i class="sw-h sw-o"></i><em>50–80</em>
              <i class="sw-h sw-r"></i><em>&lt;50</em>
            </span>
          </div>
          <div class="hud-row">
            <span class="hud-k">运行工况</span>
            <span class="hud-v">
              <b class="hv-run">{{ stats.running }}</b> 正常 ·
              <b class="hv-warn">{{ stats.warning }}</b> 预警 ·
              <b class="hv-bad">{{ stats.danger }}</b> 故障<template v-if="stats.stopped"> ·
                <b class="hv-off">{{ stats.stopped }}</b> 停机</template>
            </span>
          </div>
          <div class="hud-row">
            <span class="hud-k">矿浆主管</span>
            <span class="hud-v">{{ eqFlow('ball_mill') ?? '—' }} m³/h · 管线粒子流速随此实时变化</span>
          </div>
        </div>

        <!-- hover 悬浮信息卡
             元素常驻 DOM：位置由 JS 直接写 style（鼠标高频移动时不触发整页 re-render，
             也避免"数据更新了但 style 没跟上"的时序问题）；只有切换设备时才更新内容。
             选中设备时让位给详情面板，避免两块信息同时压屏 -->
        <div
          class="hover-card"
          ref="hoverCardRef"
          :class="{ 'is-on': !!hoveredEquipment && !selectedEquipment }"
        >
          <template v-if="hoveredEquipment">
            <div class="hover-card-title">
              <span class="hc-name">{{ hoveredEquipment.name }}</span>
              <span class="hc-tag">{{ hoveredEquipment.location_tag || '—' }}</span>
            </div>
            <div class="hover-card-row">
              <span>健康度</span>
              <span :class="`hc-${healthLevel(hoveredEquipment.health)}`">{{ hoveredEquipment.health.toFixed(0) }}%</span>
            </div>
            <div class="hover-card-row">
              <span>温度</span>
              <span>{{ hoveredEquipment.metrics.temperature != null
                ? hoveredEquipment.metrics.temperature.toFixed(1) + ' °C' : '—' }}</span>
            </div>
            <div class="hover-card-row">
              <span>振动</span>
              <span>{{ hoveredEquipment.metrics.vibration != null
                ? hoveredEquipment.metrics.vibration.toFixed(1) + ' mm/s' : '—' }}</span>
            </div>
            <div class="hover-card-row">
              <span>状态</span>
              <span :class="`st-${hoveredEquipment.status}`">{{ statusLabel(hoveredEquipment.status) }}</span>
            </div>
            <div class="hover-card-hint">单击看详情 · 双击进功能</div>
          </template>
        </div>

        <!-- 设备详情弹窗（选中时显示） -->
        <transition name="slide-fade">
          <div v-if="selectedEquipment" class="detail-panel">
            <div class="detail-header">
              <span class="detail-name">{{ selectedEquipment.name }}</span>
              <el-tag :type="statusTagType(selectedEquipment.status)" size="small" effect="dark">
                {{ statusText[selectedEquipment.status] }}
              </el-tag>
              <button class="close-btn" @click="deselectEquipment()"></button>
            </div>
            <p class="detail-desc">{{ selectedEquipment.desc }}</p>

            <!-- ★ 主行动：进入本设备所属工艺段的功能页。
                 必须放在**面板最顶部、滚动区之外** —— 它是 3D 首页的核心用途，
                 而面板内容（画像/指标/预测维护/图表）在 768 高度下必然要滚，
                 放在底部就等于"要滚到底才看得见"，等于主功能藏在角落里。 -->
            <button
              v-if="selectedRoute"
              class="detail-cta"
              :style="{ '--cta': activeSegment?.color || '#165dff' }"
              @click="enterSegment(selectedEquipment.id)"
            >
              <span class="cta-text">进入{{ selectedRoute.label }}</span>
              <span class="cta-arrow">›</span>
            </button>

            <!-- 设备画像 -->
            <div class="equip-profile">
              <div class="profile-row"><span class="pl">型号</span><span class="pv">{{ selectedEquipment.model || '—' }}</span></div>
              <div class="profile-row"><span class="pl">厂家</span><span class="pv">{{ selectedEquipment.manufacturer || '—' }}</span></div>
              <div class="profile-row"><span class="pl">位号</span><span class="pv">{{ selectedEquipment.location_tag || '—' }}</span></div>
              <div class="profile-row"><span class="pl">安装</span><span class="pv">{{ selectedEquipment.install_date || '—' }}</span></div>
              <div class="profile-row"><span class="pl">累计运行</span><span class="pv">{{ selectedEquipment.total_hours ? selectedEquipment.total_hours.toLocaleString() + ' h' : '—' }}</span></div>
              <div class="profile-row"><span class="pl">上次检修</span><span class="pv">{{ selectedEquipment.last_maintain || '—' }}</span></div>
              <div class="profile-row"><span class="pl">MTBF</span><span class="pv">{{ selectedEquipment.mtbf_hours ? selectedEquipment.mtbf_hours.toLocaleString() + ' h' : '—' }}</span></div>
            </div>

            <!-- 关键指标：用 != null 而非真值判断 —— 停机后转速/功率/流量都是真实的 0，
                 真值判断会把 0 当"无此项"隐藏，面板上就只剩温度，看不出"机器真的停了" -->
            <div class="metrics-grid">
              <div v-if="selectedEquipment.metrics.temperature != null" class="metric-item">
                <span class="metric-label">温度</span>
                <span class="metric-value" :class="{ warn: selectedEquipment.metrics.temperature > 50 }">
                  {{ selectedEquipment.metrics.temperature }}°C
                </span>
              </div>
              <div v-if="selectedEquipment.metrics.vibration != null" class="metric-item">
                <span class="metric-label">振动</span>
                <span class="metric-value" :class="{ warn: selectedEquipment.metrics.vibration > 3 }">
                  {{ selectedEquipment.metrics.vibration }} mm/s
                </span>
              </div>
              <div v-if="selectedEquipment.metrics.power != null" class="metric-item">
                <span class="metric-label">功率</span>
                <span class="metric-value">{{ selectedEquipment.metrics.power }} kW</span>
              </div>
              <div v-if="selectedEquipment.metrics.rpm != null" class="metric-item">
                <span class="metric-label">转速</span>
                <span class="metric-value">{{ selectedEquipment.metrics.rpm }} rpm</span>
              </div>
              <div v-if="selectedEquipment.metrics.flow_rate != null" class="metric-item">
                <span class="metric-label">流量</span>
                <span class="metric-value">{{ selectedEquipment.metrics.flow_rate }} m³/h</span>
              </div>
              <div class="metric-item">
                <span class="metric-label">健康度</span>
                <el-progress
                  :percentage="selectedEquipment.health"
                  :stroke-width="10"
                  :color="healthColor(selectedEquipment.health)"
                />
              </div>
            </div>

            <!-- 告警信息 -->
            <div v-if="selectedEquipment.alarm" class="alarm-banner">
              <el-icon><WarningFilled /></el-icon>
              <span>{{ selectedEquipment.alarm }}</span>
            </div>

            <!-- 预测维护 -->
            <div class="prediction-card" v-loading="predictionLoading">
              <div class="pred-header">预测维护 <span class="pred-conf" v-if="predictionData">置信度 {{ (predictionData.confidence * 100).toFixed(0) }}%</span></div>
              <template v-if="predictionData">
                <div class="pred-next" v-if="predictionData.next_maintenance">
                  <span class="pred-label">建议检修</span>
                  <el-tag size="small" :type="predictionData.next_maintenance.urgency === 'urgent' ? 'danger' : predictionData.next_maintenance.urgency === 'planned' ? 'warning' : 'success'">
                    {{ predictionData.next_maintenance.date }}
                  </el-tag>
                  <span class="pred-reason">{{ predictionData.next_maintenance.reason }}</span>
                </div>
                <div class="pred-risks" v-if="predictionData.risks && predictionData.risks.length">
                  <div v-for="r in predictionData.risks" :key="r.factor" class="risk-item" :class="'risk-' + r.severity">
                    <span class="risk-dot"></span>
                    <span>{{ r.desc }}</span>
                  </div>
                </div>
                <div v-else class="pred-ok">状态良好，按计划巡检即可</div>
                <div ref="predictionChartRef" class="pred-chart" v-if="predictionData.forecast && predictionData.forecast.length"></div>
                <div class="pred-recs" v-if="predictionData.recommendations && predictionData.recommendations.length">
                  <div v-for="(rec, i) in predictionData.recommendations" :key="i" class="rec-item">• {{ rec }}</div>
                </div>
              </template>
              <div v-else-if="!predictionLoading" class="pred-empty">加载中...</div>
            </div>

            <!-- 操作按钮 -->
            <div class="detail-actions">
              <el-button size="small" @click="showTrend(selectedEquipment)">
                查看趋势
              </el-button>
              <el-button size="small" @click="showMaintenance(selectedEquipment)">
                维护记录
              </el-button>
              <el-button
                size="small"
                :type="isEquipStopped(selectedEquipment) ? 'success' : 'warning'"
                @click="toggleEquipment(selectedEquipment)"
              >
                {{ isEquipStopped(selectedEquipment) ? '启动' : '停机' }}
              </el-button>
              <el-button size="small" type="danger" @click="openRepairDialog(selectedEquipment)">
                报修
              </el-button>
            </div>
          </div>
        </transition>

        <!-- 设备列表（右下折叠）：3D 里找一台具体设备时仍需要清单，
             但它是辅助工具，不该占用左侧黄金位置 -->
        <div class="equip-dock" :class="{ 'is-open': equipDockOpen }" :style="{ bottom: dockBottom }">
          <button class="dock-toggle" @click="equipDockOpen = !equipDockOpen">
            <el-icon><component :is="equipDockOpen ? 'ArrowDown' : 'ArrowUp'" /></el-icon>
            <span>设备清单</span>
            <em>{{ equipments.length }}</em>
          </button>
          <div v-show="equipDockOpen" class="dock-list">
            <div
              v-for="(eq, idx) in equipments"
              :key="eq.id"
              class="dock-card"
              :class="{ active: selectedEquipment?.id === eq.id, hovered: hoveredId === eq.id }"
              @click="focusEquipment(eq.id)"
            >
              <i class="card-status-dot" :class="`dot-${eq.status}`"></i>
              <span class="dock-idx">{{ String(idx + 1).padStart(2, '0') }}</span>
              <span class="dock-name">{{ eq.name }}</span>
              <span class="dock-health" :style="{ color: healthColor(eq.health) }">{{ eq.health }}%</span>
            </div>
          </div>
        </div>

        <!-- 视图提示 -->
        <div class="view-hint">
          拖拽旋转 · 滚轮缩放 · 单击设备看详情 · 双击设备进功能 · 点左侧工艺段飞向机位
        </div>
      </div>

      <!-- 右下角操作日志 -->
      <div class="op-log-panel" v-if="operationLogs.length">
        <div class="log-header">操作日志</div>
        <div class="log-list">
          <div v-for="(log, idx) in operationLogs.slice(0, logRows)" :key="idx" class="log-item" :class="`log-${log.type}`">
            <span class="log-time">{{ log.time }}</span>
            <span class="log-action">{{ log.action }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ 趋势弹窗 ═══ -->
    <el-dialog v-model="showTrendDialog" :title="`${selectedEquipment?.name || ''} — 运行趋势`" width="760px" top="8vh" destroy-on-close>
      <div class="trend-toolbar">
        <el-radio-group v-model="trendRange" size="small" @change="loadTrendData">
          <el-radio-button :value="1">1h</el-radio-button>
          <el-radio-button :value="6">6h</el-radio-button>
          <el-radio-button :value="24">24h</el-radio-button>
          <el-radio-button :value="168">7d</el-radio-button>
        </el-radio-group>
        <el-radio-group v-model="trendMetric" size="small" @change="loadTrendData">
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="temperature">温度</el-radio-button>
          <el-radio-button value="vibration">振动</el-radio-button>
          <el-radio-button value="health">健康度</el-radio-button>
        </el-radio-group>
      </div>
      <div v-loading="trendLoading" style="min-height: 200px;">
        <div v-if="trendData.length === 0 && !trendLoading" style="text-align: center; padding: 40px; color: #999;">
          暂无趋势数据
        </div>
        <div v-else ref="trendChartRef" style="width: 100%; height: 340px;"></div>
      </div>
    </el-dialog>

    <!-- ═══ 维护记录弹窗 ═══ -->
    <el-dialog v-model="showMaintenanceDialog" :title="`${selectedEquipment?.name || ''} — 维护记录`" width="680px" top="8vh">
      <el-table :data="maintenanceRecords" stripe size="small" max-height="400" v-loading="maintenanceLoading">
        <el-table-column prop="date" label="日期" width="110" />
        <el-table-column prop="type" label="类型" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.type === '故障维修' ? 'danger' : row.type === '定期保养' ? 'warning' : 'info'">
              {{ row.type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="technician" label="负责人" width="80" />
        <el-table-column prop="content" label="内容" show-overflow-tooltip />
        <el-table-column prop="result" label="结果" width="80">
          <template #default="{ row }">
            <span :style="{ color: row.result === '已修复' ? '#67c23a' : '#86909c' }">{{ row.result }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="source_order_id" label="来源工单" width="150">
          <template #default="{ row }">
            <span v-if="row.source_order_id" class="wo-link">{{ row.source_order_id }}</span>
            <span v-else style="color:#c9cdd4">—</span>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="showMaintenanceDialog = false">关闭</el-button>
        <el-button type="primary" :disabled="!maintenanceRecords.length" @click="exportMaintenanceCsv">
          导出记录
        </el-button>
      </template>
    </el-dialog>

    <!-- ═══ 报修工单弹窗 ═══ -->
    <el-dialog v-model="showRepairDialog" :title="`报修 — ${selectedEquipment?.name || ''}`" width="520px" top="15vh">
      <el-form :model="repairForm" :rules="repairRules" ref="repairFormRef" label-width="90px" size="default">
        <el-form-item label="故障类型" prop="category">
          <el-select v-model="repairForm.category" placeholder="选择故障类型" style="width: 100%;">
            <el-option label="机械故障" value="mechanical" />
            <el-option label="电气故障" value="electrical" />
            <el-option label="温度异常" value="thermal" />
            <el-option label="振动异常" value="vibration" />
            <el-option label="泄漏" value="leak" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-radio-group v-model="repairForm.priority">
            <el-radio value="critical">紧急</el-radio>
            <el-radio value="high">高</el-radio>
            <el-radio value="medium">中</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="故障描述" prop="description">
          <el-input v-model="repairForm.description" type="textarea" :rows="3" placeholder="请详细描述故障现象、发生时间等..." />
        </el-form-item>
        <el-form-item label="报修人">
          <el-input v-model="repairForm.reporter" placeholder="报修人姓名" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showRepairDialog = false">取消</el-button>
        <el-button type="primary" :loading="repairSubmitting" @click="submitRepair">提交工单</el-button>
      </template>
    </el-dialog>

    <!-- ═══ 工单中心（报修闭环：提交后可见、可指派、可维修关闭） ═══ -->
    <el-dialog
      v-model="showOrderCenter"
      title="报修工单中心"
      width="1000px"
      top="7vh"
      class="order-center-dialog"
      @closed="selectedOrder = null"
    >
      <div class="order-center">
        <!-- 左：统计 + 筛选 + 列表 -->
        <div class="oc-left">
          <div class="oc-stats">
            <div class="oc-stat oc-stat-open">
              <span class="oc-stat-num">{{ orderStats.open || 0 }}</span>
              <span class="oc-stat-label">未闭环</span>
            </div>
            <div class="oc-stat">
              <span class="oc-stat-num">{{ orderStats.pending || 0 }}</span>
              <span class="oc-stat-label">待处理</span>
            </div>
            <div class="oc-stat">
              <span class="oc-stat-num">{{ orderStats.repairing || 0 }}</span>
              <span class="oc-stat-label">维修中</span>
            </div>
            <div class="oc-stat">
              <span class="oc-stat-num">{{ orderStats.fixed || 0 }}</span>
              <span class="oc-stat-label">已修复</span>
            </div>
          </div>

          <div class="oc-toolbar">
            <el-radio-group v-model="orderFilter" size="small" @change="fetchOrders">
              <el-radio-button value="open">未闭环</el-radio-button>
              <el-radio-button value="all">全部</el-radio-button>
              <el-radio-button value="fixed">已修复</el-radio-button>
              <el-radio-button value="rejected">已驳回</el-radio-button>
            </el-radio-group>
            <el-input
              v-model="orderKeyword"
              size="small"
              placeholder="搜工单号 / 设备 / 描述"
              clearable
              @keyup.enter="fetchOrders"
              @clear="fetchOrders"
            >
              <template #append>
                <el-button @click="fetchOrders">搜索</el-button>
              </template>
            </el-input>
          </div>

          <div class="oc-list" v-loading="orderLoading">
            <div
              v-for="o in orderList"
              :key="o.order_id"
              class="oc-item"
              :class="[`oc-item-${o.status}`, { active: selectedOrder?.order_id === o.order_id }]"
              @click="selectOrder(o)"
            >
              <div class="oc-item-row1">
                <span class="oc-oid">{{ o.order_id }}</span>
                <el-tag size="small" :type="orderStatusType(o.status)" effect="dark">
                  {{ o.status_label }}
                </el-tag>
              </div>
              <div class="oc-item-row2">{{ o.equipment_name }} · {{ o.category_label }}</div>
              <div class="oc-item-row3">
                <span class="oc-prio" :class="`prio-${o.priority}`">{{ o.priority_label }}</span>
                <span class="oc-age">{{ ageText(o) }}</span>
              </div>
            </div>
            <div v-if="!orderLoading && !orderList.length" class="oc-empty">
              当前筛选下没有工单
            </div>
          </div>
        </div>

        <!-- 右：详情 + 流转 -->
        <div class="oc-right" v-loading="orderDetailLoading">
          <template v-if="selectedOrder">
            <div class="oc-detail-head">
              <span class="oc-detail-id">{{ selectedOrder.order_id }}</span>
              <el-tag size="small" :type="orderStatusType(selectedOrder.status)" effect="dark">
                {{ selectedOrder.status_label }}
              </el-tag>
            </div>

            <div class="oc-grid">
              <div class="oc-field"><label>设备</label><span>{{ selectedOrder.equipment_name }}</span></div>
              <div class="oc-field"><label>故障类型</label><span>{{ selectedOrder.category_label }}</span></div>
              <div class="oc-field"><label>优先级</label><span>{{ selectedOrder.priority_label }}</span></div>
              <div class="oc-field"><label>报修人</label><span>{{ selectedOrder.reporter }}</span></div>
              <div class="oc-field"><label>维修负责人</label><span>{{ selectedOrder.assignee || '未指派' }}</span></div>
              <div class="oc-field"><label>提交时间</label><span>{{ selectedOrder.created_at }}</span></div>
            </div>

            <div class="oc-desc">{{ selectedOrder.description }}</div>

            <div class="oc-section-title">流转记录</div>
            <div class="oc-timeline">
              <div v-for="t in selectedOrder.timeline" :key="t.id" class="oc-tl-item">
                <span class="oc-tl-dot" :class="`dot-${t.to_status}`"></span>
                <div class="oc-tl-body">
                  <div class="oc-tl-head">
                    <b>{{ t.action_label }}</b>
                    <span class="oc-tl-flow">{{ t.from_status_label || '新建' }} → {{ t.to_status_label }}</span>
                  </div>
                  <div class="oc-tl-meta">{{ t.created_at }} · {{ t.operator }}</div>
                  <div class="oc-tl-note" v-if="t.note">{{ t.note }}</div>
                </div>
              </div>
            </div>

            <template v-if="selectedOrder.linked_maintenance && selectedOrder.linked_maintenance.length">
              <div class="oc-section-title">已回写维护记录</div>
              <div class="oc-linked">
                <div v-for="m in selectedOrder.linked_maintenance" :key="m.id" class="oc-linked-item">
                  <el-tag size="small" type="success" effect="plain">{{ m.type }}</el-tag>
                  <span>{{ m.date }}</span>
                  <span>{{ m.technician }}</span>
                  <span class="oc-linked-result">{{ m.result }}</span>
                </div>
              </div>
            </template>

            <div class="oc-actions" v-if="selectedOrder.next_actions && selectedOrder.next_actions.length">
              <el-button
                v-for="a in selectedOrder.next_actions"
                :key="a.action"
                size="small"
                :type="actionBtnType(a.action)"
                @click="openOrderTransition(a)"
              >
                {{ a.label }}
              </el-button>
            </div>
            <div class="oc-actions" v-else>
              <span class="oc-done-hint">
                工单已闭环 · {{ selectedOrder.handled_by || '—' }} · {{ selectedOrder.handled_at || '—' }}
              </span>
            </div>
          </template>
          <div v-else class="oc-empty">左侧选择一条工单查看详情</div>
        </div>
      </div>
    </el-dialog>

    <!-- ═══ 工单流转弹窗 ═══ -->
    <el-dialog
      v-model="showOrderTransitionDialog"
      :title="`${orderTransition.action_label} — ${selectedOrder?.order_id || ''}`"
      width="500px"
      top="18vh"
      append-to-body
    >
      <el-alert
        v-if="orderTransition.action === 'fix'"
        type="success"
        :closable="false"
        show-icon
        title="完成后将自动生成一条「故障维修」维护记录，并更新设备台账的最近维护时间"
        style="margin-bottom: 14px;"
      />
      <el-alert
        v-else-if="orderTransition.action === 'reject'"
        type="warning"
        :closable="false"
        show-icon
        title="驳回后工单不再进入维修队列，原因会写入流转记录备查"
        style="margin-bottom: 14px;"
      />
      <el-form label-width="90px" size="default">
        <el-form-item label="操作人" required>
          <el-input v-model="orderTransition.operator" placeholder="岗位或姓名" />
        </el-form-item>
        <el-form-item label="维修负责人" v-if="orderTransition.need_assignee" required>
          <el-input v-model="orderTransition.assignee" placeholder="如：维修工-李" />
        </el-form-item>
        <el-form-item :label="orderTransition.note_label" :required="orderTransition.need_note">
          <el-input
            v-model="orderTransition.note"
            type="textarea"
            :rows="3"
            :placeholder="orderTransition.note_placeholder"
          />
        </el-form-item>
        <template v-if="orderTransition.action === 'fix'">
          <el-form-item label="处置措施">
            <el-input v-model="orderTransition.action_taken" placeholder="如：更换 6224 轴承 + 重新找正" />
          </el-form-item>
          <el-form-item label="停机时长">
            <el-input-number v-model="orderTransition.downtime_minutes" :min="0" :max="10000" :step="10" />
            <span class="oc-unit">分钟</span>
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="showOrderTransitionDialog = false">取消</el-button>
        <el-button type="primary" :loading="orderTransitioning" @click="submitOrderTransition">
          确认{{ orderTransition.action_label }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script>
// 类型名称映射（模板中引用）
const typeNames = {
  flotation: '浮选槽',
  mill: '球磨机',
  pump: '渣浆泵',
  conveyor: '皮带机',
  thickener: '浓密机',
  agitator: '搅拌槽',
  pool: '水处理池',
  dosing: '加药装置',
}
</script>

<style scoped>
.equipment-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 40px);
  gap: 0;
}

/* ── 工具栏 ── */
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #fff;
  border-radius: 8px;
  box-shadow: var(--shadow-sm);
  margin-bottom: 12px;
}
.toolbar-left { display: flex; align-items: center; gap: 16px; }
.page-title {
  margin: 0; font-size: 18px; font-weight: 700; color: #1a1a2e;
  display: flex; align-items: center; gap: 6px;
}
.stat-chips { display: flex; gap: 6px; align-items: center; }
.refresh-hint {
  font-size: 11px;
  color: #aaa;
  margin-left: 4px;
}

/* ── 主布局 ── */
.main-layout {
  display: flex;
  flex: 1;
  gap: 12px;
  min-height: 0;
}

/* ── 左侧内栏：功能导航 ── */
.side-panel {
  width: 240px;
  background: #fff;
  border-radius: 8px;
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  /* ★ 必须裁切 + 圆角落在外层：否则 .side-scroll 的内容会越过圆角描边。
     （滚动由 .side-scroll 负责，外层裁的是它的容器轮廓，不影响滚动） */
  overflow: hidden;
  flex-shrink: 0;
}
.panel-header {
  flex-shrink: 0;
  padding: 11px 14px;
  font-size: 13px;
  font-weight: 600;
  color: #607d8b;
  border-bottom: 1px solid #eee;
  background: #ffffff;
  display: flex;
  align-items: baseline;
  gap: 8px;
}
/* 副标题：说明这一栏到底是什么（是"厂区目录"，不是功能清单） */
.panel-sub {
  font-size: 10px;
  font-weight: 400;
  color: #a9b2bd;
  letter-spacing: 0.2px;
}
.side-scroll {
  flex: 1;
  overflow-y: auto;
  /* ★ 768 高度下内容比容器高约 4px —— 恰好触发一条滚动条，
     而这条滚动条除了"看起来有东西没显示完"没有任何信息量。
     收紧 padding 把这 4px 吃掉，1366x768 下不再出滚动条。 */
  padding: 8px 8px 10px;
  min-height: 0;
  scrollbar-width: thin;
}
/* 仅在真的需要时才露出滚动条（内容真的超了），避免细微误差带来的视觉噪音 */
.side-scroll::-webkit-scrollbar { width: 6px; }
.side-scroll::-webkit-scrollbar-thumb {
  background: #d6dbe3; border-radius: 3px;
}
.side-scroll::-webkit-scrollbar-track { background: transparent; }
.side-group { margin-bottom: 12px; }
.side-group:last-child { margin-bottom: 0; }
.side-group-title {
  font-size: 11px;
  color: #99a3ae;
  font-weight: 600;
  letter-spacing: 0.6px;
  padding: 0 6px 6px;
}

/* ══════ 工艺链（纵向时间轴）══════
   设计意图：4 个工艺段不是 4 个并列的按钮，而是**同一条物料流上的 4 个站点**。
   所以用一条贯穿的竖线把它们串起来，节点圆点用各段主题色 ——
   这比"色条 + 卡片"更能表达"从磨矿到回水"的先后关系。 */
.chain { position: relative; }
.chain-node {
  position: relative;
  padding-bottom: 2px;
}
/* 连线：从本节点圆点下沿连到下一个节点；最后一段不画，避免悬空一截 */
.chain-node:not(:last-child)::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 21px;
  bottom: 0;
  width: 1px;
  background: #e2e8f0;
}
.chain-dot {
  position: absolute;
  left: 0;
  top: 11px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  /* 白圈把连线"打断"，圆点才像站点而不是线上的一个色块 */
  box-shadow: 0 0 0 3px #fff;
  z-index: 1;
}
.chain-row {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 4px 2px 18px;
  cursor: pointer;
  border-radius: 5px;
  transition: background 0.16s;
}
.chain-row:hover { background: #f5f8ff; }
.chain-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: #2b3648;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.chain-node.is-primary .chain-name { color: #165dff; }
.chain-node.is-active .chain-name { color: #165dff; }
.chain-health {
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.chain-health em { font-style: normal; font-size: 10px; color: #98a2b0; margin-left: 1px; }
.chain-caret {
  font-size: 11px;
  color: #c2cad4;
  flex-shrink: 0;
  transition: transform 0.2s, color 0.2s;
}
.chain-node:hover .chain-caret { color: #165dff; }
.chain-caret.is-open { transform: rotate(90deg); color: #165dff; }
.chain-meta {
  display: flex;
  gap: 7px;
  font-size: 11px;
  color: #9aa4b1;
  padding: 0 4px 8px 18px;
}
.seg-bad { color: #f53f3f; font-weight: 600; }
.seg-warn { color: #ff7d00; font-weight: 600; }
.seg-ok { color: #00b42a; }

/* 展开区：这块是"点开一段之后才出现"的二级内容 */
.chain-drawer {
  margin: 0 4px 8px 18px;
  padding: 8px 9px 9px;
  background: #f7f9fc;
  border: 1px solid #edf1f7;
  border-radius: 6px;
}
.drawer-label {
  font-size: 10px;
  color: #a3adb9;
  letter-spacing: 0.4px;
  margin: 0 0 5px;
}
.drawer-label + .fn-chip + .drawer-label,
.fn-chip + .drawer-label { margin-top: 9px; }
/* 功能入口小药丸：主入口用段主题色描边，一眼区分"这是能进去的" */
.fn-chip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  width: 100%;
  padding: 5px 9px;
  margin-bottom: 5px;
  font-size: 12px;
  font-family: inherit;
  color: #2b3648;
  background: #fff;
  border: 1px solid #e3e8ef;
  border-radius: 5px;
  cursor: pointer;
  text-decoration: none;
  transition: border-color 0.16s, color 0.16s, background 0.16s;
}
.fn-chip:hover {
  border-color: var(--c, #165dff);
  color: var(--c, #165dff);
  background: #fff;
  text-decoration: none;
}
.fn-chip .el-icon { font-size: 11px; opacity: 0.7; }
.fn-chip.is-main { border-color: var(--c, #165dff); color: var(--c, #165dff); font-weight: 600; }

/* 段内设备小行 */
.eq-mini {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 4px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  color: #4f5b66;
}
.eq-mini:hover { background: #eef4ff; }
.eq-dot {
  width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
  background: #00b42a;
}
.eq-dot.dot-warning { background: #ff7d00; }
.eq-dot.dot-danger { background: #f53f3f; }
.eq-dot.dot-stopped { background: #9e9e9e; }
.eq-mini-name { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.eq-mini-h { font-size: 11px; font-weight: 600; font-variant-numeric: tabular-nums; flex-shrink: 0; }

/* ══════ 公辅载体：功能在厂区里的"那间屋子" ══════ */
.facility {
  padding: 7px 6px 8px;
  border-radius: 6px;
  transition: background 0.16s;
}
.facility:hover { background: #f9fbfe; }
/* 在 3D 里点了这栋建筑 → 左栏对应条目亮一下，把"场景 ↔ 目录"钉在一起。
   没有这个反馈，用户点完建筑只会觉得"镜头动了"，不知道它对应左栏哪一条。 */
.facility.is-hot {
  background: #eef4ff;
  box-shadow: inset 0 0 0 1px rgba(22, 93, 255, 0.35);
}
.fac-row {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 6px;
}
.fac-icon { font-size: 14px; color: #7a8699; flex-shrink: 0; }
.fac-name { font-size: 13px; font-weight: 600; color: #2b3648; flex-shrink: 0; }
.fac-tag {
  flex: 1;
  min-width: 0;
  font-size: 10px;
  color: #a9b2bd;
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.fac-chips { display: flex; flex-wrap: wrap; gap: 5px; padding-left: 21px; }
.fac-chips .fn-chip {
  width: auto;
  justify-content: center;
  margin-bottom: 0;
  padding: 4px 9px;
}

/* ══════ 矮屏紧凑化 ══════
   左栏是"4 段 + 5 处载体"的固定结构，内容高度基本恒定；
   768 高度下可用区只有 ~627px，实测溢出 42px → 会多出一条滚动条。
   这里按**视口高度**收（不是宽度）：1920x1080 完全不受影响，
   1366x768 这种矮屏才触发，把 42px 从各处内距里省回来。 */
@media (max-height: 820px) {
  .side-group { margin-bottom: 10px; }
  .chain-row { padding-top: 4px; }
  .chain-meta { padding-bottom: 6px; }
  .chain-drawer { padding: 6px 8px 7px; margin-bottom: 6px; }
  .facility { padding: 5px 6px 6px; }
  .fac-row { margin-bottom: 5px; }
}

/* 设备状态点（沿用旧样式，被折叠清单复用） */
.card-status-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}
.dot-running { background: #00b42a; box-shadow: 0 0 6px rgba(76,175,80,0.4); }
.dot-warning { background: #ff7d00; box-shadow: 0 0 6px rgba(255,152,0,0.4); }
.dot-danger { background: #f53f3f; animation: pulse-dot 1.2s infinite; }
.dot-stopped { background: #9e9e9e; }
@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(1.3); }
}

/* ── 右下设备清单（折叠）──
   ⚠️ 必须落在操作日志面板（.op-log-panel，bottom:12 right:12 宽 260）的**上方**。
      两者都靠右下，早期版本 bottom:14 直接压住日志文字，日志被遮掉大半行。
      这里用 bottom:206 抬到日志实际高度（约 196）之上，并右对齐到同一列。 */
.equip-dock {
  position: absolute;
  right: 12px;
  bottom: 206px;
  width: 260px;
  background: rgba(20, 24, 31, 0.88);
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 6px;
  overflow: hidden;
  backdrop-filter: blur(6px);
  z-index: 13;
  /* ★ 展开时整体高度受视口约束，超出的部分由 .dock-list 自己滚 ——
     否则 10 台设备会把面板顶到视口之外，连折叠按钮都点不到（收不回去）。 */
  display: flex;
  flex-direction: column;
  max-height: calc(100% - 226px);
}
.dock-toggle {
  flex-shrink: 0;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  background: none;
  border: none;
  color: #cfd8e3;
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
}
.dock-toggle:hover { background: rgba(255,255,255,0.06); }
.dock-toggle span { flex: 1; text-align: left; }
.dock-toggle em {
  font-style: normal;
  font-size: 11px;
  color: #94a3b4;
  background: rgba(255,255,255,0.08);
  padding: 0 5px;
  border-radius: 8px;
}
.dock-list {
  flex: 1;
  min-height: 0;          /* ★ flex 子项必须给 0，否则 overflow 不生效 */
  max-height: 232px;
  overflow-y: auto;
  padding: 4px;
  border-top: 1px solid rgba(255,255,255,0.08);
}
.dock-card {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 7px;
  border-radius: 5px;
  cursor: pointer;
  transition: background 0.16s;
}
.dock-card:hover { background: rgba(255,255,255,0.08); }
.dock-card.active { background: rgba(22,93,255,0.28); }
.dock-idx {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 10px;
  color: #7d8b9c;
}
.dock-name { flex: 1; font-size: 12px; color: #dbe3ec; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dock-health { font-size: 11px; font-weight: 600; font-variant-numeric: tabular-nums; }

/* ── 3D 视口 ── */
.viewport {
  flex: 1;
  position: relative;
  background: #1a1d23;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: var(--shadow-lg);
}
.viewport canvas { display: block; width: 100% !important; height: 100% !important; }

/* ── 数字孪生 HUD ── */
.twin-hud {
  position: absolute;
  top: 14px;
  left: 14px;
  min-width: 252px;
  background: rgba(20, 24, 31, 0.74);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  padding: 10px 12px 11px;
  color: #e8ecf2;
  z-index: 8;
  pointer-events: none;
}
.hud-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.09);
  font-size: 12px;
  font-weight: 600;
}
.hud-live {
  width: 7px; height: 7px; border-radius: 50%;
  background: #00b42a;
  animation: hud-pulse 1.8s infinite;
}
@keyframes hud-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(0, 180, 42, 0.55); }
  70%  { box-shadow: 0 0 0 7px rgba(0, 180, 42, 0); }
  100% { box-shadow: 0 0 0 0 rgba(0, 180, 42, 0); }
}
.hud-clock {
  margin-left: auto;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.6);
}
.hud-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  line-height: 1.95;
}
.hud-k { width: 58px; flex-shrink: 0; color: rgba(255, 255, 255, 0.48); }
.hud-v { color: rgba(255, 255, 255, 0.88); }
.hud-v b { font-weight: 700; }
.hv-run { color: #4fd15c; }
.hv-warn { color: #ffa83d; }
.hv-bad { color: #ff6b6b; }
.hv-off { color: #b6c2cf; }
.hud-scale { display: flex; align-items: center; }
.hud-scale em {
  font-style: normal;
  color: rgba(255, 255, 255, 0.72);
  margin: 0 8px 0 4px;
}
.hud-scale em:last-child { margin-right: 0; }
.sw-h { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }
.sw-g { background: #00b42a; }
.sw-o { background: #ff7d00; }
.sw-r { background: #f53f3f; }

/* ── 详情面板 ── */
.detail-panel {
  position: absolute;
  top: 16px;
  /* ★ 右边界必须让开右下角两件浮层（设备清单 + 操作日志，都右对齐 right:12 宽 260）：
     早期 right:16 让详情面板与它们**完全重叠**，选中设备时三块面板叠成一团看不清。
     这里右移 284px（= 260 宽 + 12 边距 + 12 间隙），详情面板独占右侧「内列」。 */
  right: 284px;
  width: 300px;
  background: rgba(255,255,255,0.96);
  backdrop-filter: blur(12px);
  border-radius: 8px;
  box-shadow: var(--shadow-lg);
  padding: 18px;
  /* ★ z-index 保证压在 3D canvas / 标签之上。原本是为了压过 op-log-panel，
     现在两者已不重叠，仍保留高档位以防后续新增浮层。 */
  z-index: 30;
  /* ★ 内容再长也不能越过视口：否则底部那排操作按钮滚不到，等于按钮凭空消失 */
  max-height: calc(100% - 32px);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.detail-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.detail-name { font-size: 16px; font-weight: 700; color: #1a1a2e; }
.detail-desc {
  font-size: 12px; color: #777; line-height: 1.5;
  margin: 0 0 14px; padding-bottom: 10px; border-bottom: 1px solid #eee;
}

/* ── 详情面板主行动按钮（进入所属工艺段功能页）──
   刻意不用 el-button：这里需要整行通栏 + 段主题色描边 + 右端箭头，
   Element 的默认实心蓝会把同面板里的 5 个次级操作全压成背景。
   用 --cta 承接当前段配色（磨矿蓝 / 浮选绿 / 输送橙 / 尾矿紫）。 */
.detail-cta {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  margin: 0 0 12px;
  padding: 8px 11px;
  border: none;
  border-radius: 6px;
  background: var(--cta, #165dff);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
  box-shadow: 0 2px 8px color-mix(in srgb, var(--cta, #165dff) 30%, transparent);
  transition: filter 0.16s, transform 0.16s;
}
.detail-cta:hover { filter: brightness(1.08); }
.detail-cta:active { transform: translateY(1px); }
.cta-text { flex: 1; min-width: 0; }
.cta-arrow { font-size: 16px; line-height: 1; opacity: 0.85; }

.metrics-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 14px;
}
.metric-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.metric-label { font-size: 11px; color: #999; }
.metric-value { font-size: 15px; font-weight: 700; color: #333; }
.metric-value.warn { color: #994b00; }
.alarm-banner {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 10px; border-radius: 6px;
  background: #ffece8; color: #d92f2f;
  font-size: 12px; font-weight: 500; margin-bottom: 14px;
}
.detail-actions { display: flex; gap: 6px; }
.close-btn {
  margin-left: auto; cursor: pointer; border: none; background: none;
  font-size: 16px; color: #999; padding: 2px 4px;
}
.close-btn:hover { color: #333; }

/* ── 视图提示 ── */
.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  background: rgba(26, 29, 35, 0.9);
  color: #fff;
  font-size: 14px;
  z-index: 20;
}
/* 初始化失败：红字提示具体哪一步炸了，而不是永远转圈 */
.loading-overlay.is-error {
  color: #ffb4b4;
  font-size: 13px;
  line-height: 1.7;
  padding: 0 40px;
  text-align: center;
}
.loading-spinner {
  width: 40px; height: 40px;
  border: 3px solid rgba(255,255,255,0.15);
  border-top-color: #4a90d9;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* hover 悬浮信息卡 —— 常驻 DOM，靠 is-on 显隐（位置由 JS 直写 style） */
.hover-card {
  position: absolute;
  z-index: 30;
  pointer-events: none;
  left: 0;
  top: 0;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.16s ease;
  background: rgba(28, 33, 42, 0.95);
  color: #fff;
  border-radius: 6px;
  padding: 10px 12px;
  min-width: 160px;
  font-size: 12px;
  box-shadow: var(--shadow-lg);
  border: 1px solid rgba(255,255,255,0.08);
  backdrop-filter: blur(8px);
}
.hover-card.is-on { opacity: 1; visibility: visible; }
.hover-card-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 7px;
  padding-bottom: 6px;
  border-bottom: 1px solid rgba(255,255,255,0.12);
}
.hc-name { font-size: 13px; font-weight: 600; }
.hc-tag {
  font-size: 10.5px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  color: rgba(255,255,255,0.45);
  white-space: nowrap;
}
.hover-card-hint {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed rgba(255,255,255,0.14);
  font-size: 10.5px;
  letter-spacing: 0.3px;
  color: rgba(255,255,255,0.42);
}
.hover-card-row {
  display: flex;
  justify-content: space-between;
  margin: 4px 0;
  gap: 16px;
}
.hover-card-row span:first-child { color: rgba(255,255,255,0.55); }
.hc-good { color: #00b42a; font-weight: 600; }
.hc-warn { color: #ff7d00; font-weight: 600; }
.hc-bad  { color: #f53f3f; font-weight: 600; }
.st-running { color: #66bb6a; }
.st-warning { color: #ffa726; }
.st-stopped { color: #9e9e9e; }
.st-danger  { color: #ef5350; font-weight: 600; }
.view-hint {
  position: absolute;
  bottom: 12px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 12px;
  color: rgba(255,255,255,0.4);
  background: rgba(0,0,0,0.3);
  padding: 4px 14px;
  border-radius: 8px;
  pointer-events: none;
}

/* ── 过渡动画 ── */
.slide-fade-enter-active { transition: all 0.3s ease-out; }
.slide-fade-leave-active { transition: all 0.2s ease-in; }
.slide-fade-enter-from { opacity: 0; transform: translateX(20px); }
.slide-fade-leave-to { opacity: 0; transform: translateX(20px); }

/* ── 卡片状态颜色 ── */
.card-running { border-left: 3px solid #00b42a; }
.card-warning { border-left: 3px solid #ff7d00; }
.card-danger { border-left: 3px solid #f53f3f; }
.card-stopped { border-left: 3px solid #9e9e9e; }

/* ── 操作日志面板 ── */
.op-log-panel {
  position: absolute;
  bottom: 12px;
  right: 12px;
  width: 260px;
  background: rgba(255,255,255,0.95);
  border-radius: 6px;
  box-shadow: var(--shadow-md);
  overflow: hidden;
  z-index: 10;
  /* ★ 高度必须受视口约束：768 高度下 8 条日志 = 215px，
     会从小尺寸的 3D 视图里挖掉近 1/3 的面积（那才是页面主体）。
     这里按剩余空间自适应，矮屏自然只显示 6 条。 */
  display: flex;
  flex-direction: column;
  max-height: min(40vh, 196px);
}
.log-header {
  flex-shrink: 0;
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 600;
  color: #1d2129;
  background: #f7f8fa;
  border-bottom: 1px solid #eee;
}
.log-list {
  flex: 1;
  min-height: 0;          /* ★ flex 子项归零，否则 max-height 拦不住内容 */
  overflow-y: auto;
}
.log-item {
  display: flex; gap: 8px;
  padding: 5px 12px;
  font-size: 11px;
  border-bottom: 1px solid #f2f3f5;
}
.log-time { color: #999; white-space: nowrap; font-family: monospace; }
.log-action {
  color: #4f5b66;
  /* 长动作名（"工单 WO-… 开始维修（待处理 → 维修中）"）最多两行，
     否则一条就撑出 60px，把面板顶到三行就满 */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.4;
}
.log-success .log-action { color: #00b42a; font-weight: 500; }
.log-warning .log-action { color: #ff7d00; font-weight: 500; }
.log-danger .log-action { color: #f53f3f; font-weight: 500; }

/* ── 详情操作按钮组 ── */
.detail-actions {
  display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap;
}
.detail-actions .el-button { flex: 1; min-width: 80px; }

/* ── 弹窗内表格紧凑化 ── */
:deep(.el-dialog__body) { padding: 16px 20px; }
:deep(.el-table th) { background: #fafafa !important; }

/* ── 设备画像 ── */
.equip-profile {
  display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px;
  padding: 8px 10px; margin-bottom: 12px;
  background: #f7f8fa; border-radius: 6px; border-left: 3px solid #5347c4;
}
.profile-row { display: flex; justify-content: space-between; font-size: 11px; }
.pl { color: #909499; }
.pv { color: #1d2129; font-weight: 500; }

/* ── 预测维护 ── */
.prediction-card {
  margin-bottom: 12px; padding: 10px;
  background: #faf8ff; border-radius: 6px; border: 1px solid #ece6ff;
}
.pred-header {
  font-size: 12px; font-weight: 600; color: #5347c4;
  margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;
}
.pred-conf { font-size: 10px; color: #999; font-weight: 400; }
.pred-next {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  font-size: 11px; margin-bottom: 6px;
}
.pred-label { color: #666; }
.pred-reason { color: #888; font-size: 10px; }
.pred-risks { display: flex; flex-direction: column; gap: 4px; }
.risk-item {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; padding: 4px 6px; border-radius: 4px;
}
.risk-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.risk-high { background: #ffece8; color: #d92f2f; }
.risk-high .risk-dot { background: #f53f3f; }
.risk-critical { background: #fce4ec; color: #ad1457; }
.risk-critical .risk-dot { background: #e91e63; }
.risk-medium { background: #fff7e6; color: #994b00; }
.risk-medium .risk-dot { background: #ff7d00; }
.pred-ok { font-size: 11px; color: #00b42a; padding: 4px 6px; }
.pred-chart { width: 100%; height: 110px; margin: 6px 0; }
.pred-recs { display: flex; flex-direction: column; gap: 2px; }
.rec-item { font-size: 10px; color: #555; line-height: 1.5; }
.pred-empty { font-size: 11px; color: #aaa; text-align: center; padding: 12px; }

/* ── 趋势工具栏 ── */
.trend-toolbar {
  display: flex; justify-content: space-between; align-items: center;
  gap: 12px; margin-bottom: 12px;
}

/* ── 工单中心 ── */
.order-badge { margin-right: 10px; }
.order-badge :deep(.el-badge__content) {
  font-size: 10px; height: 15px; line-height: 15px; padding: 0 5px;
}

.order-center {
  display: grid;
  grid-template-columns: 344px minmax(0, 1fr);
  height: min(474px, 56vh);
}

/* 左栏：统计 + 筛选 + 列表 */
.oc-left {
  display: flex; flex-direction: column; gap: 10px;
  padding-right: 16px; border-right: 1px solid #e5e6eb; min-height: 0;
}
.oc-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.oc-stat {
  display: flex; flex-direction: column; align-items: center;
  padding: 8px 4px; background: #f7f8fa; border: 1px solid #eef0f3; border-radius: 4px;
}
.oc-stat-open { background: #fff2f0; border-color: #ffd6d1; }
.oc-stat-num {
  font-size: 18px; font-weight: 700; color: #1d2129; line-height: 1.2;
  font-family: ui-monospace, Menlo, Consolas, monospace;
}
.oc-stat-open .oc-stat-num { color: #f53f3f; }
.oc-stat-label { font-size: 11px; color: #86909c; }
.oc-toolbar { display: flex; flex-direction: column; gap: 8px; }
.oc-list {
  flex: 1; min-height: 0; overflow-y: auto;
  display: flex; flex-direction: column; gap: 8px; padding-right: 2px;
}
.oc-item {
  border: 1px solid #e5e6eb; border-left: 3px solid #c9cdd4; border-radius: 4px;
  padding: 8px 10px; background: #fff; cursor: pointer;
  transition: background .15s, border-color .15s;
}
.oc-item:hover { background: #f7f8fa; }
.oc-item.active { background: #e8f3ff; border-color: #165dff; }
.oc-item-pending { border-left-color: #f53f3f; }
.oc-item-assigned { border-left-color: #ff7d00; }
.oc-item-repairing { border-left-color: #165dff; }
.oc-item-fixed { border-left-color: #00b42a; }
.oc-item-rejected { border-left-color: #c9cdd4; }
.oc-item-row1 { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
.oc-oid { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; font-weight: 600; color: #1d2129; }
.oc-item-row2 { font-size: 12px; color: #4e5969; margin-top: 3px; }
.oc-item-row3 { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; font-size: 11px; }
.oc-prio { padding: 0 5px; border-radius: 2px; }
.prio-critical { background: #ffece8; color: #d92f2f; }
.prio-high { background: #fff7e6; color: #994b00; }
.prio-medium { background: #f2f3f5; color: #4e5969; }
.oc-age { color: #86909c; }
.oc-empty { text-align: center; color: #c9cdd4; font-size: 12px; padding: 40px 0; }

/* 右栏：详情 + 流转 */
.oc-right {
  padding-left: 18px; min-height: 0; overflow-y: auto;
  display: flex; flex-direction: column;
}
.oc-detail-head { display: flex; align-items: center; gap: 10px; padding-bottom: 10px; border-bottom: 1px solid #eef0f3; }
.oc-detail-id { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 14px; font-weight: 700; }
.oc-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 16px; margin: 12px 0; }
.oc-field { display: flex; gap: 6px; font-size: 12px; }
.oc-field label { color: #86909c; flex-shrink: 0; }
.oc-field span { color: #1d2129; }
.oc-desc { background: #f7f8fa; border-radius: 4px; padding: 8px 10px; font-size: 12px; color: #4e5969; line-height: 1.6; }
.oc-section-title {
  font-size: 12px; font-weight: 600; color: #1d2129;
  margin: 14px 0 8px; padding-left: 8px; border-left: 3px solid #165dff;
}
.oc-timeline { display: flex; flex-direction: column; }
.oc-tl-item { display: flex; gap: 10px; padding-bottom: 12px; position: relative; }
.oc-tl-item:not(:last-child)::before {
  content: ''; position: absolute; left: 4px; top: 12px; bottom: 0; width: 1px; background: #e5e6eb;
}
.oc-tl-dot {
  width: 9px; height: 9px; border-radius: 50%; margin-top: 3px; flex-shrink: 0;
  background: #c9cdd4; z-index: 1; box-shadow: 0 0 0 2px #fff;
}
.dot-pending { background: #f53f3f; }
.dot-assigned { background: #ff7d00; }
.dot-repairing { background: #165dff; }
.dot-fixed { background: #00b42a; }
.dot-rejected { background: #86909c; }
.oc-tl-body { flex: 1; min-width: 0; }
.oc-tl-head { font-size: 12px; color: #1d2129; display: flex; gap: 8px; align-items: baseline; }
.oc-tl-flow { font-size: 11px; color: #86909c; }
.oc-tl-meta { font-size: 11px; color: #86909c; margin-top: 2px; font-family: ui-monospace, Menlo, Consolas, monospace; }
.oc-tl-note {
  font-size: 11px; color: #4e5969; background: #f7f8fa;
  border-radius: 3px; padding: 4px 8px; margin-top: 4px; line-height: 1.5;
}
.oc-linked { display: flex; flex-direction: column; gap: 6px; }
.oc-linked-item {
  display: flex; align-items: center; gap: 10px; font-size: 12px; color: #4e5969;
  background: #f6ffed; border: 1px solid #d9f2c9; border-radius: 4px; padding: 6px 10px;
}
.oc-linked-result { color: #00b42a; font-weight: 600; margin-left: auto; }
.oc-actions { margin-top: auto; padding-top: 16px; display: flex; gap: 8px; flex-wrap: wrap; }
.oc-done-hint { font-size: 12px; color: #86909c; }
.oc-unit { margin-left: 8px; font-size: 12px; color: #86909c; }
.wo-link { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; color: #165dff; }
</style>

<!-- 非 scoped 样式：CSS2D 标签渲染在 Vue 组件外部，scoped 无效 -->
<style>
/* ── 屏幕空间引线层（由 JS 动态创建，不在 Vue 模板里 → 必须用非 scoped 样式）── */
.leader-overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  overflow: visible;
}
/* 三档：默认清晰可读 → 聚焦亮起 → 其余设备淡出
   ⚠️ 早期是 1px / 0.42 透明：在深色画面上几乎看不见，于是"标签挂在空中"——
      观众只能猜这个牌子属于哪台设备。引线的职责就是**指认归属**，
      弱到看不见等于没有。这里改成 1.6px + 0.72，并给端点加实心锚点圆。 */
.leader-line {
  stroke: rgba(196, 220, 244, 0.95);
  stroke-width: 1.6;
  stroke-linecap: round;
  /* 深色场景上叠一层暗描边，压住设备亮面，保证线在任何背景上都读得出来 */
  paint-order: stroke;
  opacity: 0.72;
  transition: opacity 0.25s ease;
}
.leader-dot {
  fill: rgba(226, 240, 255, 1);
  stroke: rgba(20, 30, 44, 0.85);
  stroke-width: 1.2;
  opacity: 0.85;
  transition: opacity 0.25s ease;
}
.leader-line.is-focus { opacity: 0.92; }
.leader-dot.is-focus { opacity: 1; }
.leader-line.dim { opacity: 0.07; }
.leader-dot.dim { opacity: 0.1; }

/* 载体建筑徽标 —— 悬在楼顶上方的小标签，是"楼被设备挡住"时唯一可点的入口。
   刻意做得比设备读数牌轻：它是"区域名"不是"设备读数"，不该抢设备的信息权重。
   ⚠️ 必须 position:absolute（理由同 .equip-label：CSS2D 靠 transform 定位，
      元素若在文档流里会被前面的节点挤偏，牌子与楼对不上）。 */
.facility-badge {
  position: absolute;
  top: 0;
  left: 0;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 7px 2px 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid rgba(22, 93, 255, 0.22);
  color: #2b4c7e;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
  white-space: nowrap;
  cursor: pointer;
  pointer-events: auto;
  user-select: none;
  box-shadow: 0 2px 8px rgba(20, 40, 80, 0.14);
  transition: background 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
}
.facility-badge .fb-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #165dff;
  box-shadow: 0 0 0 3px rgba(22, 93, 255, 0.12);
}
.facility-badge:hover {
  background: #fff;
  border-color: #165dff;
  transform: translateY(-1px);
}
.facility-badge.is-hot {
  background: #165dff;
  border-color: #165dff;
  color: #fff;
}
.facility-badge.is-hot .fb-dot { background: #fff; box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.3); }

.equip-label {
  display: inline-flex !important;
  align-items: center !important;
  gap: 5px !important;
  max-width: 224px !important;
  box-sizing: border-box !important;
  z-index: 2;
  cursor: pointer;
  /* 聚焦/淡出走 transition；transform 由 CSS2DRenderer 每帧重写，不能在这里做缩放 */
  transition: opacity 0.25s ease, box-shadow 0.22s ease, font-size 0.22s ease;
}
/* 序号徽标 —— 与左侧设备列表同一套编号，跨视图对得上号 */
.equip-label .el-idx {
  font-style: normal;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.5px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.09);
  opacity: 0.85;
}
/* 孪生读数牌上的健康度数字 */
.equip-label .el-health {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 11px;
  font-weight: 700;
  padding: 0 5px;
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.07);
}
/* 聚焦态：悬停/选中的设备，它的牌子浮到最上并加一圈主色描边 */
.equip-label.is-focus {
  font-size: 12.5px !important;
  z-index: 9;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35), 0 0 0 2px rgba(22, 93, 255, 0.55);
}
</style>
