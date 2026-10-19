<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import api from '../api'
import BrandMark from '../components/BrandMark.vue'
import FrothLive from '../components/FrothLive.vue'
import BackToFactory from '../components/BackToFactory.vue'

const router = useRouter()

/* ================= 时钟 ================= */
const currentTime = ref('')
const currentDate = ref('')
let clockTimer = null

function tick() {
  const now = new Date()
  currentTime.value = now.toLocaleTimeString('zh-CN', { hour12: false })
  currentDate.value = now.toLocaleDateString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'long',
  })
}

/* ================= 数据状态 ================= */
const scenes = ref([])
const backendOk = ref(null)
const stats = ref(null)
const alarms = ref([])
const alarmListEl = ref(null)   // 告警列表容器：按实测可用高度决定显示条数
const alarmRows = ref(4)        // 当前视口下显示的条数（自适应）
const alarmRich = ref(false)    // 空间富余时改用两行式行（消息 + 副信息）
const alarmStats = ref({
  total: 0, new: 0, events: 0, warning: 0, danger: 0,
  todo_danger: 0, todo_warning: 0, in_progress: 0, closed: 0, acknowledged: 0,
})
const waterOverview = ref(null)
const safetyStats = ref(null)

/* 近 30 天品位趋势 */
const trend = ref([])
const trendStat = ref({
  avg: '—', mae: '—', days: 0,
  peak: '—', trough: '—', std: '—', delta: '—', deltaUp: true,
})

/* ================= 流程节点 =================
   metric = 该段的核心实时读数（必须是接口真实字段）
   ledger = 台账/模型口径（数据规模、模型版本、异常数）  */
const processNodes = ref([
  { id: 'grinding',  name: '磨矿段',   metric: '—', ledger: '粒度分析', icon: 'Grid',        status: 'planned' },
  { id: 'flotation', name: '浮选段',   metric: '—', ledger: '品位预测', icon: 'TrendCharts', status: 'planned', primary: true },
  { id: 'safety',    name: '安全巡检', metric: '—', ledger: '皮带/区域', icon: 'Aim',         status: 'planned' },
  { id: 'water',     name: '脱水工段', metric: '—', ledger: '水质监测',  icon: 'Drizzling',   status: 'planned' },
  { id: 'tailings',  name: '尾矿处理', metric: '—', ledger: '底流浓度', icon: 'Files',       status: 'planned' },
])

/* ================= KPI ================= */
const kpis = ref([
  { label: '累计分析',   displayValue: '—', unit: '张', sub: '' },
  { label: '分析组数',   displayValue: '—', unit: '组', sub: '' },
  { label: '平均铅品位', displayValue: '—', unit: '%',  sub: '' },
  { label: '待处理告警', displayValue: '—', unit: '条', sub: '', danger: true },
])

/* ================= 设备汇总 ================= */
const equipSummary = computed(() => {
  const list = equipmentList.value
  if (!list.length) return { total: 0, need: 0, avg: '—' }
  const need = list.filter((e) => e.health < 60).length
  const avg = Math.round(list.reduce((s, e) => s + (e.health || 0), 0) / list.length)
  return { total: list.length, need, avg }
})

/* ================= 设备 ================= */
const equipmentList = ref([])
const hoveredEquip = ref(null)

function scoreClass(h) {
  if (h >= 80) return 'good'
  if (h >= 60) return 'warn'
  return 'bad'
}

/* ================= 告警（严重优先 + 同规则去重，只展示前 4 条） =================
   直播墙是"扫一眼看现在有什么问题"，所以：
   ① 严重优先，同级按时间降序（二级排序必须显式给，否则排序不稳定）
   ② 首轮按「场景 + 规则」去重 —— 否则 48 条 pb_critical 会把前几格全占了，
      呈现出来就是几条一模一样的告警，属于无效重复
   ③ 去重后条数不够填满本视口时，再用剩余条目补位                        */
const alarmFeed = computed(() => {
  const rank = { danger: 0, warning: 1 }
  const sorted = [...alarms.value].sort((a, b) => {
    const d = (rank[a.level] ?? 2) - (rank[b.level] ?? 2)
    if (d !== 0) return d
    return String(b.created_at || '').localeCompare(String(a.created_at || ''))
  })
  const seen = new Set()
  const hero = []
  const rest = []
  for (const a of sorted) {
    const k = `${a.scene_id}|${a.rule_name}`
    if (seen.has(k)) rest.push(a)
    else {
      seen.add(k)
      hero.push(a)
    }
  }
  return [...hero, ...rest]
})
const displayAlarms = computed(() => alarmFeed.value.slice(0, alarmRows.value))

/* 去重后可用的「场景 × 规则」组合数 —— 作为显示条数的上限，避免同一条规则重复刷屏 */
const alarmDistinct = computed(
  () => new Set(alarms.value.map((a) => `${a.scene_id}|${a.rule_name}`)).size
)

/* 按列表容器的实测高度决定「显示几条 + 用哪种行式」。
   列表是 flex:1 + overflow-y:auto，容器高度不随条数变化 → 测量不会形成回环。

   三种形态，按优先级：
   ① 紧凑单行（30px）：能放下的条数 ≤ 可用规则数 → 直接用不同规则铺满，零重复
   ② 两行式（48px）：紧凑会重复刷同一规则、且空间够 → 改用「消息 + 场景·读数·时间」两行，
      把富余高度用来承载信息，而不是把单行撑高留白
   ③ 密排（30px，允许重复）：空间大到两行式也不够铺 → 只能多排，宁可重复也不留白
   效果：1080p 是 4 条紧凑行（与既有版式一致），1440p 大屏是 8 条不重复、信息更足的两行条目。 */
const ALARM_MIN_H = 30   // 紧凑单行行高（= .alarm-item padding + 单行行高）
const ALARM_RICH_H = 48  // 两行式行高（消息行 + 副信息行）
const ALARM_GAP = 5      // = .alarm-list 的 gap
const ALARM_MAX_RICH = 64 // 两行式拉伸上限，超过就不如多排几条
function fitAlarmRows() {
  const el = alarmListEl.value
  if (!el) return
  // 矮屏降级模式下列表是 overflow:visible（高度=内容高度），测量会自我循环
  // （条数 → 高度 → 条数），改用固定条数，由整页滚动承载
  if (getComputedStyle(el).overflowY === 'visible') {
    alarmRows.value = 4
    alarmRich.value = false
    return
  }
  const boxH = el.clientHeight
  if (!boxH) return
  const distinct = Math.max(alarmDistinct.value, 3)
  const rowsCompact = Math.max(3, Math.floor((boxH + ALARM_GAP) / (ALARM_MIN_H + ALARM_GAP)))
  if (rowsCompact <= distinct) {
    alarmRows.value = Math.min(rowsCompact, 30)
    alarmRich.value = false
    return
  }
  const rowsRich = Math.max(3, Math.floor((boxH + ALARM_GAP) / (ALARM_RICH_H + ALARM_GAP)))
  const r = Math.min(distinct, rowsRich)
  const stretch = (boxH - (r - 1) * ALARM_GAP) / r
  if (stretch <= ALARM_MAX_RICH) {
    alarmRows.value = Math.min(r, 30)
    alarmRich.value = true
    return
  }
  alarmRows.value = Math.min(rowsCompact, 30)
  alarmRich.value = false
}

/* 严重度占比（用于分布条）——口径 = 待处理告警（status=new），不是全量  */
const sevPct = computed(() => {
  const d = alarmStats.value.todo_danger || 0
  const w = alarmStats.value.todo_warning || 0
  const t = d + w
  if (!t) return { danger: 0, warning: 0 }
  return { danger: (d / t) * 100, warning: (w / t) * 100 }
})

/* 列表条目的副信息：场景中文名 · 关键读数 · 时间（全部来自接口 brief，取不到就不显示）。
   非当日告警必须带上月日 —— 列表跨度到一周，只显示「09:06」会被误读成今天的。 */
function metaText(a) {
  const b = a.brief || {}
  const parts = [b.scene_label || a.scene_id]
  if (b.metric) parts.push(b.metric)
  const s = String(a.created_at || '')
  if (s.length >= 16) {
    const d = new Date()
    const p = (n) => String(n).padStart(2, '0')
    const today = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
    parts.push(s.slice(0, 10) === today ? s.slice(11, 16) : `${s.slice(5, 10)} ${s.slice(11, 16)}`)
  }
  return parts.join(' · ')
}

/* ================= 图表 ================= */
const trendChartRef = ref(null)
let trendChart = null

/* ================= 刷新 ================= */
let refreshTimer = null
const REFRESH_INTERVAL = 30000
const isRefreshing = ref(false)

/* ================= KPI 数字滚动 ================= */
function animateKPI(index, targetVal, isFloat = false) {
  const kpi = kpis.value[index]
  if (!kpi) return
  const duration = 1200
  const start = performance.now()
  function step(now) {
    const p = Math.min((now - start) / duration, 1)
    const eased = p === 1 ? 1 : 1 - Math.pow(2, -10 * p)
    const cur = targetVal * eased
    kpi.displayValue = isFloat ? cur.toFixed(2) : Math.floor(cur).toLocaleString()
    if (p < 1) requestAnimationFrame(step)
  }
  requestAnimationFrame(step)
}

/* ================= 加载数据 ================= */
async function loadData() {
  try {
    const [scenesRes, statsRes, alarmsRes, equipRes, waterRes, tailingsRes, safetyRes, trendRes] = await Promise.all([
      api.get('/scenes'),
      api.get('/scenes/flotation/stats'),
      api.get('/alarms?limit=60&collapse=1'),
      api.get('/equipment'),
      api.get('/water/overview'),
      api.get('/tailings/overview'),
      api.get('/safety/stats'),
      api.get('/scenes/flotation/trend?days=30'),
    ])

    scenes.value = scenesRes.scenes || []
    backendOk.value = true
    stats.value = statsRes
    alarms.value = alarmsRes.alarms || []
    alarmStats.value = alarmsRes.stats || {}
    equipmentList.value = equipRes.equipment || []
    waterOverview.value = waterRes
    safetyStats.value = safetyRes

    /* 趋势：剔除样本量过少的日期（当日均不足 10 条时，日均值不具代表性） */
    const daily = (trendRes.daily || []).filter(
      (d) => d.records >= 10 && d.avg_pred_pb != null && d.avg_actual != null
    )
    trend.value = daily
    if (daily.length) {
      const vals = daily.map((d) => d.avg_actual)
      const sum = (a) => a.reduce((s, v) => s + v, 0)
      const avg = sum(vals) / vals.length
      const mae = daily.reduce((s, d) => s + Math.abs(d.avg_pred_pb - d.avg_actual), 0) / daily.length
      const peak = Math.max(...vals)
      const trough = Math.min(...vals)
      const std = Math.sqrt(vals.reduce((s, v) => s + (v - avg) ** 2, 0) / vals.length)
      /* 环比：近 7 日均值 vs 之前 7 日均值 */
      const last7 = vals.slice(-7)
      const prev7 = vals.slice(-14, -7)
      const m7 = sum(last7) / last7.length
      const mPrev = prev7.length ? sum(prev7) / prev7.length : null
      const delta = mPrev == null ? null : m7 - mPrev
      trendStat.value = {
        avg: avg.toFixed(2),
        mae: mae.toFixed(2),
        days: daily.length,
        peak: peak.toFixed(2),
        trough: trough.toFixed(2),
        std: std.toFixed(2),
        delta: delta == null
          ? '—'
          : Math.abs(delta) < 0.01
            ? '0.00'
            : `${delta >= 0 ? '+' : ''}${delta.toFixed(2)}`,
        deltaUp: delta == null || delta >= 0,
      }
      kpis.value[2].sub = `区间 ${trough.toFixed(1)} ~ ${peak.toFixed(1)} · σ ${std.toFixed(2)}`
    }
    renderTrendChart()

    /* KPI 入场动画 */
    animateKPI(0, statsRes.total_records || 0)
    animateKPI(1, statsRes.total_groups || 0)
    animateKPI(2, statsRes.grade_distribution?.mean ?? 0, true)
    animateKPI(3, alarmStats.value.new || 0)

    /* KPI 副行：全部来自上面已取到的真实序列 */
    kpis.value[0].sub = daily.length
      ? `近 7 天 +${daily.slice(-7).reduce((s, d) => s + (d.records || 0), 0).toLocaleString()} · 覆盖 ${daily.length} 天`
      : '—'
    kpis.value[1].sub = statsRes.total_groups
      ? `平均每组 ${((statsRes.total_records || 0) / statsRes.total_groups).toFixed(2)} 条`
      : '—'
    kpis.value[3].sub = `已确认 ${alarmStats.value.acknowledged || 0} · 已闭环 ${alarmStats.value.closed || 0}`

    /* 流程节点：metric / ledger 全部取接口真实字段，不造数 */
    const pick = (n) => scenes.value.find((s) => s.name === n)
    const setNode = (id, patch) => Object.assign(processNodes.value.find((n) => n.id === id), patch)
    const fl = pick('flotation')
    const gr = pick('grinding')
    const sa = pick('safety')

    if (gr?.status === 'active') {
      setNode('grinding', {
        status: 'active',
        metric: `${gr.records || 0} 组样本`,
        ledger: `模型 ${gr.active_model || '—'}`,
      })
    }
    if (fl?.status === 'active') {
      setNode('flotation', {
        status: 'active',
        metric: trendStat.value.avg === '—' ? '—' : `Pb ${trendStat.value.avg} %`,
        ledger: `模型 ${fl.active_model || '—'}`,
      })
    }
    if (sa?.status === 'active') {
      const ppe = safetyRes?.ppe_compliance_avg
      setNode('safety', {
        status: 'active',
        metric: ppe != null ? `PPE ${(ppe * 100).toFixed(1)} %` : `${sa.records || 0} 条`,
        ledger: `风险 ${safetyRes?.intrusion_30d ?? '—'} · 台账 ${sa.records || 0}`,
      })
    }
    if (waterRes?.avg_reuse_score != null) {
      setNode('water', {
        status: 'active',
        metric: `回用 ${waterRes.avg_reuse_score} 分`,
        ledger: `${waterRes.station_count} 点位 · ${waterRes.abnormal_count} 异常`,
      })
    }
    /* 尾矿处理：读数全部取自 /tailings/overview 的 metrics，不造数 */
    if (tailingsRes?.metrics) {
      const tm = tailingsRes.metrics
      const uc = tm.underflow_conc
      const bed = tm.bed_height
      const flc = tm.flocculant_dosage
      setNode('tailings', {
        status: 'active',
        metric: uc ? `底流 ${uc.value}${uc.unit || ''}` : '—',
        ledger:
          bed != null && flc != null
            ? `泥床 ${bed.value}${bed.unit || ''} · 凝剂 ${flc.value}${flc.unit || ''}`
            : `${tailingsRes.equipment?.name || '浓密机'}`,
      })
    }
  } catch (e) {
    console.error('LiveWall 数据加载失败:', e)
    backendOk.value = false
  }
}

/* ================= 轻量刷新（只更新数字） ================= */
async function lightRefresh() {
  isRefreshing.value = true
  try {
    const [statsRes, alarmsRes] = await Promise.all([
      api.get('/scenes/flotation/stats'),
      api.get('/alarms?limit=60&collapse=1'),
    ])
    stats.value = statsRes
    alarms.value = alarmsRes.alarms || []
    alarmStats.value = alarmsRes.stats || {}

    kpis.value[0].displayValue = (statsRes.total_records || 0).toLocaleString()
    kpis.value[1].displayValue = (statsRes.total_groups || 0).toLocaleString()
    kpis.value[2].displayValue = (statsRes.grade_distribution?.mean ?? 0).toFixed(2)
    kpis.value[3].displayValue = String(alarmStats.value.new || 0)
    kpis.value[3].sub = `已确认 ${alarmStats.value.acknowledged || 0} · 已闭环 ${alarmStats.value.closed || 0}`
  } catch (e) {
    console.warn('LiveWall 轻量刷新失败:', e)
  } finally {
    setTimeout(() => { isRefreshing.value = false }, 800)
  }
}

/* ================= 品位趋势图（预测 vs XRF 实测） ================= */
function renderTrendChart() {
  if (!trendChartRef.value || !trend.value.length) return
  if (!trendChart) trendChart = echarts.init(trendChartRef.value)

  const dates = trend.value.map((d) => d.date.slice(5))
  const pred = trend.value.map((d) => d.avg_pred_pb)
  const actual = trend.value.map((d) => d.avg_actual)

  trendChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: '#e5e6eb',
      textStyle: { color: '#1d2129', fontSize: 13 },
      valueFormatter: (v) => `${v} %`,
    },
    grid: { top: 16, right: 14, bottom: 28, left: 48 },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#e5e6eb' } },
      axisTick: { show: false },
      axisLabel: { color: '#86909c', fontSize: 12, interval: Math.ceil(dates.length / 8) },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: '#f2f3f5' } },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#86909c', fontSize: 12 },
    },
    series: [
      {
        name: 'XRF 实测',
        type: 'line',
        data: actual,
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#c9cdd4', width: 1.6, type: 'dashed' },
      },
      {
        name: '模型预测',
        type: 'line',
        data: pred,
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        showSymbol: false,
        lineStyle: { color: '#165dff', width: 2.2 },
        itemStyle: { color: '#165dff' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(22,93,255,0.10)' },
            { offset: 1, color: 'rgba(22,93,255,0)' },
          ]),
        },
      },
    ],
  })
  trendChart.resize()
}

/* ================= 导航 ================= */
function goWorkbench(id) {
  if (id === 'water') { router.push('/water'); return }
  if (id === 'tailings') { router.push('/tailings'); return }
  if (id === 'equipment') { router.push('/equipment'); return }
  router.push(`/workbench/${id}`)
}
function goPage(path) { router.push(path) }

/* ================= 生命周期 ================= */
function handleResize() {
  trendChart?.resize()
  fitAlarmRows()
}
let alarmRO = null

onMounted(() => {
  tick()
  clockTimer = setInterval(tick, 1000)
  loadData()
  refreshTimer = setInterval(lightRefresh, REFRESH_INTERVAL)
  window.addEventListener('resize', handleResize)
  // 列表容器尺寸一变（窗口缩放 / 侧栏折叠）就重算可显示条数
  if (typeof ResizeObserver !== 'undefined' && alarmListEl.value) {
    alarmRO = new ResizeObserver(fitAlarmRows)
    alarmRO.observe(alarmListEl.value)
  }
  fitAlarmRows()
})

onUnmounted(() => {
  clearInterval(clockTimer)
  clearInterval(refreshTimer)
  window.removeEventListener('resize', handleResize)
  alarmRO?.disconnect()
  alarmRO = null
  trendChart?.dispose()
  trendChart = null
})
</script>

<template>
  <div class="livewall">
    <!-- ============ 指挥条（无卡片，靠分隔线分层） ============ -->
    <header class="lw-topbar">
      <div class="brand">
        <BrandMark class="brand-mark" />
        <div class="brand-text">
          <h1>淬眸 SuiEye</h1>
          <span class="brand-sub">选矿全流程智能视觉分析平台</span>
        </div>
      </div>
      <div class="topbar-right">
        <!-- 返回厂区：极简模式（无边框面包屑样式），不抢指挥条的视觉 -->
        <BackToFactory compact />
        <span class="sys-status" :class="{ err: backendOk === false }">
          <i class="sys-dot"></i>{{ backendOk === false ? '后端未连接' : '系统运行正常' }}
        </span>
        <span class="topbar-divider"></span>
        <div class="clock">
          <div class="clock-time">{{ currentTime }}</div>
          <div class="clock-date">{{ currentDate }}</div>
        </div>
      </div>
    </header>

    <!-- ============ 关键指标（无框大数字） ============ -->
    <section class="lw-metrics">
      <div v-for="(kpi, i) in kpis" :key="i" class="metric">
        <div class="metric-label">{{ kpi.label }}</div>
        <div class="metric-value" :class="{ danger: kpi.danger }">
          {{ kpi.displayValue }}<span class="metric-unit">{{ kpi.unit }}</span>
        </div>
        <div class="metric-sub">{{ kpi.sub || '—' }}</div>
      </div>
    </section>

    <!-- ============ 主区：2.2 : 1 双栏 ============ -->
    <main class="lw-main">
      <div class="col-left">
        <!-- 第一行：工艺流程 ∥ 浮选泡沫窗（并排）
             泡沫**不占额外垂直空间** —— 它是窗，不是横在中间的一条带子。
             早先把它做成"与工艺流程上下堆叠的 116px 扁条"，代价是把下面的
             品位趋势（主视觉）压掉 128px（407→279），趋势图被压得很难受。
             并排后趋势恢复全高，泡沫也从"被裁扁的带"变成比例正常的画面窗。 -->
        <div class="row-top">
          <!-- 工艺流程 -->
          <section class="panel process-panel">
            <div class="panel-header">
              <h3>选矿工艺流程</h3>
              <span class="panel-hint">点击节点进入工作台</span>
            </div>
            <div class="flow-track">
              <template v-for="(node, idx) in processNodes" :key="node.id">
                <div
                  class="flow-node"
                  :class="[node.status, { primary: node.primary }]"
                  @click="goWorkbench(node.id)"
                >
                  <div class="node-head">
                    <span class="node-icon"><el-icon><component :is="node.icon" /></el-icon></span>
                    <span class="node-name">{{ node.name }}</span>
                  </div>
                  <div class="node-metric">{{ node.metric }}</div>
                  <div class="node-ledger">{{ node.ledger }}</div>
                </div>
                <span v-if="idx < processNodes.length - 1" class="flow-arrow">›</span>
              </template>
            </div>
          </section>

          <!-- 浮选泡沫（现场实拍实时画面）—— 文案要短：窗宽只有 ~272px -->
          <section class="panel froth-band">
            <FrothLive
              variant="band"
              label="1#浮选槽 · 泡沫层"
              note="现场实拍"
            />
          </section>
        </div>

        <!-- 品位趋势（主视觉） -->
        <section class="panel trend-panel">
          <div class="panel-header">
            <h3>品位趋势 · 近 30 天</h3>
            <div class="legend">
              <span class="legend-item"><i class="legend-line pred"></i>模型预测</span>
              <span class="legend-item"><i class="legend-line actual"></i>XRF 实测</span>
            </div>
          </div>
          <div class="trend-body">
            <div class="trend-nums">
              <div class="trend-main-label">近 30 天平均品位 · XRF 实测</div>
              <div class="trend-main-value">{{ trendStat.avg }}<span class="trend-main-unit"> %</span></div>
              <div class="trend-main-sub">
                环比 {{ trendStat.delta }} · 峰值 {{ trendStat.peak }} · 谷值 {{ trendStat.trough }}
              </div>
              <div class="trend-sep"></div>
              <div class="trend-pair">
                <div class="trend-pair-item">
                  <span class="trend-sub-label">预测平均偏差</span>
                  <span class="trend-sub-value">± {{ trendStat.mae }}</span>
                </div>
                <div class="trend-pair-item">
                  <span class="trend-sub-label">有效样本</span>
                  <span class="trend-sub-value">{{ trendStat.days }} 天</span>
                </div>
              </div>
            </div>
            <div ref="trendChartRef" class="trend-chart"></div>
          </div>
        </section>
      </div>

      <div class="col-right">
        <!-- 需关注 -->
        <section class="panel alarm-panel">
          <div class="panel-header">
            <h3>需关注</h3>
            <span class="alarm-badge">{{ alarmStats.total || 0 }} 条 → {{ alarmStats.events || 0 }} 事件</span>
          </div>
          <div v-if="alarmStats.new" class="sev-row">
            <div class="sev-track">
              <i class="sev-danger" :style="{ width: sevPct.danger + '%' }"></i>
              <i class="sev-warning" :style="{ width: sevPct.warning + '%' }"></i>
            </div>
            <span class="sev-legend">严重 {{ alarmStats.todo_danger || 0 }} · 预警 {{ alarmStats.todo_warning || 0 }}</span>
          </div>
          <div class="alarm-list" :class="{ rich: alarmRich }" ref="alarmListEl">
            <div v-if="!displayAlarms.length" class="alarm-empty">暂无告警，系统运行平稳</div>
            <div
              v-for="alarm in displayAlarms"
              :key="alarm.id"
              class="alarm-item"
              :class="alarm.level"
            >
              <span class="alarm-bar"></span>
              <div class="alarm-body">
                <div class="alarm-msg" :title="alarm.message || alarm.rule_name">
                  {{ alarm.message || alarm.rule_name }}
                </div>
                <span class="alarm-meta">{{ metaText(alarm) }}</span>
              </div>
            </div>
          </div>
          <div class="panel-foot">
            <span>更新于 {{ currentTime.slice(0, 5) }}</span>
            <span class="panel-link" @click="goPage('/alarms')">查看全部 →</span>
          </div>
        </section>

        <!-- 运行摘要（水质 + 安全） -->
        <section class="panel summary-panel">
          <div class="panel-header">
            <h3>运行摘要</h3>
            <span class="panel-hint">实时</span>
          </div>
          <div class="summary-grid">
            <div class="summary-item">
              <span class="summary-label">回水可回用评分</span>
              <span class="summary-value" :class="{ warn: (waterOverview?.avg_reuse_score ?? 100) < 70 }">
                {{ waterOverview?.avg_reuse_score ?? '—' }}<i>分</i>
              </span>
            </div>
            <div class="summary-item">
              <span class="summary-label">回水总流量</span>
              <span class="summary-value">
                {{ waterOverview?.total_flow_m3h?.toLocaleString() ?? '—' }}<i>m³/h</i>
              </span>
            </div>
            <div class="summary-item">
              <span class="summary-label">PPE 合规率</span>
              <span class="summary-value">
                {{ safetyStats?.ppe_compliance_avg != null ? (safetyStats.ppe_compliance_avg * 100).toFixed(1) : '—' }}<i>%</i>
              </span>
            </div>
            <div class="summary-item">
              <span class="summary-label">未闭环隐患</span>
              <span class="summary-value">
                {{ safetyStats?.hazard_open ?? '—' }}<i>/ {{ safetyStats?.hazard_total ?? '—' }}</i>
              </span>
            </div>
          </div>
        </section>

        <!-- 场景引擎 -->
        <section class="panel scene-panel">
          <div class="panel-header">
            <h3>场景引擎</h3>
            <span class="panel-hint">{{ scenes.filter((s) => s.status === 'active').length }} / {{ scenes.length }} 已接入</span>
          </div>
          <div class="scene-list">
            <div
              v-for="s in scenes"
              :key="s.name"
              class="scene-item"
              :class="s.status"
              @click="goWorkbench(s.name)"
            >
              <span class="scene-dot"></span>
              <span class="scene-name">{{ s.display_name }}</span>
              <span class="scene-model">{{ s.status === 'active' ? (s.active_model || '') : '' }}</span>
              <span class="scene-count">{{ s.status === 'active' ? (s.records == null ? '实时' : s.records.toLocaleString()) : '待接入' }}</span>
            </div>
          </div>
        </section>
      </div>
    </main>

    <!-- ============ 设备健康条带（悬停展开运行指标） ============ -->
    <section class="equip-strip">
      <div class="strip-header">
        <h3>设备健康</h3>
        <span class="panel-hint">
          {{ equipSummary.total }} 台在线 · {{ equipSummary.need }} 台需检修 · 平均 {{ equipSummary.avg }} 分 · 悬停查看运行指标
        </span>
      </div>
      <div class="equip-row">
        <div
          v-for="eq in equipmentList"
          :key="eq.id"
          class="equip-cell"
          :class="[scoreClass(eq.health), { hovered: hoveredEquip === eq.id }]"
          @mouseenter="hoveredEquip = eq.id"
          @mouseleave="hoveredEquip = null"
          @click="goPage('/equipment')"
        >
          <div class="cell-top">
            <span class="cell-name">{{ eq.name }}</span>
            <span v-if="eq.status === 'stopped'" class="cell-off">停机</span>
            <span v-if="eq.metrics?.temperature != null" class="cell-temp">{{ eq.metrics.temperature.toFixed(0) }}°</span>
          </div>
          <div class="cell-score" :class="scoreClass(eq.health)">{{ eq.health }}</div>
          <div class="cell-bar">
            <i :class="scoreClass(eq.health)" :style="{ width: eq.health + '%' }"></i>
          </div>

          <div v-if="hoveredEquip === eq.id" class="cell-pop">
            <div class="pop-title">{{ eq.name }}</div>
            <div class="pop-grid">
              <div class="pop-item"><span>温度</span><strong>{{ eq.metrics?.temperature?.toFixed(1) ?? '—' }} °C</strong></div>
              <div class="pop-item"><span>振动</span><strong>{{ eq.metrics?.vibration?.toFixed(1) ?? '—' }} mm/s</strong></div>
              <div class="pop-item"><span>功率</span><strong>{{ eq.metrics?.power?.toFixed(0) ?? '—' }} kW</strong></div>
              <div class="pop-item"><span>转速</span><strong>{{ eq.metrics?.rpm?.toFixed(0) ?? '—' }} rpm</strong></div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ 底栏 ============ -->
    <footer class="lw-footer">
      <span>SuiEye Platform v0.1 · 铅锌矿浮选过程智能视觉分析</span>
      <span class="footer-divider">|</span>
      <span>数据来源：真实生产台账 · 覆盖 {{ trendStat.days || '—' }} 个有效日</span>
      <span class="footer-divider">|</span>
      <span>算法：U-Net + XGBoost</span>
      <span class="footer-divider">|</span>
      <span class="refresh-indicator" :class="{ refreshing: isRefreshing }">
        <span class="refresh-dot"></span>{{ isRefreshing ? '数据同步中...' : '已同步' }}
      </span>
    </footer>
  </div>
</template>

<style scoped>
/* ============ 页面骨架：固定视口高度，整页不滚动 ============ */
.livewall {
  /* 父级 .app-main 已有内边距，这里用 100% 而非 100vh，否则会溢出 */
  height: 100%;
  overflow: hidden;
  background: var(--bg-3);
  color: var(--text-1);
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  font-family: var(--font-family);
}

/* ============ 指挥条 ============ */
.lw-topbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 72px;
  padding: 0 30px;
  background: var(--bg-1);
  border-bottom: 1px solid var(--border-1);
  box-sizing: border-box;
}
.brand {
  display: flex;
  align-items: center;
  gap: 14px;
}
.brand-mark {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  color: var(--color-primary);
}
.brand-text {
  display: flex;
  align-items: baseline;
  gap: 14px;
}
.brand-text h1 {
  margin: 0;
  font-size: 21px;
  font-weight: var(--weight-semibold);
  letter-spacing: 0.2px;
  line-height: 1.25;
}
.brand-sub {
  font-size: 12.5px;
  color: var(--text-3);
  border-left: 1px solid var(--border-1);
  padding-left: 14px;
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: 20px;
}
.sys-status {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  color: var(--text-2);
}
.sys-status.err {
  color: var(--color-danger);
}
.sys-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-success);
  animation: pulse-dot 2s ease-in-out infinite;
}
.sys-status.err .sys-dot {
  background: var(--color-danger);
}
.topbar-divider {
  width: 1px;
  height: 22px;
  background: var(--border-1);
}
.clock {
  text-align: right;
}
.clock-time {
  font-size: 21px;
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
  letter-spacing: 0.5px;
}
.clock-date {
  font-size: 12px;
  color: var(--text-3);
}
@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.45; transform: scale(0.85); }
}

/* ============ 关键指标（无框） ============ */
.lw-metrics {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  height: 106px;
  padding: 0 28px;
  background: var(--bg-1);
  border-bottom: 1px solid var(--border-1);
  box-sizing: border-box;
}
.metric {
  flex: 1;
  min-width: 0;
  position: relative;
  padding-right: 26px;
}
.metric:not(:last-child)::after {
  content: '';
  position: absolute;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 1px;
  height: 54px;
  background: var(--border-1);
}
.metric-label {
  font-size: 12.5px;
  color: var(--text-3);
}
.metric-value {
  margin-top: 5px;
  font-size: 26px;
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  line-height: 1;
  letter-spacing: -0.5px;
}
.metric-value.danger {
  color: var(--color-danger);
}
.metric-unit {
  font-size: 13px;
  font-weight: var(--weight-normal);
  color: var(--text-3);
  margin-left: 4px;
  letter-spacing: 0;
}
.metric-sub {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ============ 主区 ============ */
.lw-main {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 2.15fr) minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
  gap: 12px;
  padding: 14px 20px;
  box-sizing: border-box;
}
.col-left,
.col-right {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  min-height: 0;
}

/* ============ 面板 ============ */
.panel {
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-radius: 12px;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.panel-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  white-space: nowrap;
}
.panel-hint {
  margin-left: auto;
  font-size: 12.5px;
  color: var(--text-3);
  white-space: nowrap;
}
.panel-link {
  font-size: 13px;
  color: var(--color-primary);
  cursor: pointer;
  transition: color var(--transition);
}
.panel-link:hover {
  color: var(--color-primary-hover);
}

/* ============ 工艺流程 ============ */
/* 第一行：工艺流程 ∥ 浮选泡沫窗。
   用 flex row 并排 → 泡沫窗 `align-self: stretch` 跟随流程面板高度，
   **零额外垂直占用**，下面的品位趋势（主视觉）得以保持全高。 */
.row-top {
  flex: 0 0 auto;
  display: flex;
  gap: 12px;
  align-items: stretch;
  min-width: 0;
}
.process-panel {
  flex: 1 1 auto;
  min-width: 0;
}

/* ============ 浮选泡沫窗 ============
   一个画面窗（不是横贯全宽的扁带）。**宽度按左栏的 22% 自适应**：
   1920 屏 → ~273px；2560 屏 → ~363px（固定 272px 在 1440p 下会显得又小又孤）。
   高度不设 —— 由同行「工艺流程」决定（flex row 的 stretch），零额外垂直占用。
   无标题栏 —— LIVE 角标 + 左下点位已经说明了身份；padding 归零让画面铺满。
   ⚠️ 窗宽只够放一行文案 → label/note 必须短（见模板）。 */
.froth-band {
  flex: 0 0 22%;
  min-width: 240px;
  max-width: 380px;
  padding: 0;
  overflow: hidden;
  border-radius: 12px;
  background: #0f1419;
}
.flow-track {
  display: flex;
  align-items: stretch;
  gap: 8px;
}
.flow-node {
  flex: 1 1 0;
  min-width: 0;
  padding: 11px 14px;
  background: var(--color-primary-bg);
  border: 1px solid #d6e4ff;
  border-radius: 10px;
  cursor: pointer;
  transition: background var(--transition), border-color var(--transition);
}
.flow-node:hover {
  background: #dceaff;
  border-color: #94bfff;
}
.flow-node.primary {
  background: var(--color-primary);
  border-color: var(--color-primary);
}
.flow-node.primary:hover {
  background: var(--color-primary-hover);
  border-color: var(--color-primary-hover);
}
.flow-node.planned {
  background: transparent;
  border: 1px dashed #c9cdd4;
  cursor: default;
}
.flow-node.planned:hover {
  background: transparent;
  border-color: #c9cdd4;
}
.node-head {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.node-icon {
  font-size: 15px;
  color: var(--text-2);
  display: inline-flex;
  flex-shrink: 0;
}
.flow-node.primary .node-icon {
  color: rgba(255, 255, 255, 0.85);
}
.flow-node.planned .node-icon {
  color: var(--text-4);
}
.node-name {
  font-size: 14px;
  font-weight: var(--weight-medium);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.flow-node.primary .node-name {
  color: #fff;
}
.flow-node.planned .node-name {
  color: var(--text-3);
}
.node-metric {
  margin-top: 8px;
  font-size: 16px;
  font-weight: var(--weight-semibold);
  color: var(--color-primary);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.flow-node.primary .node-metric {
  color: #fff;
}
.flow-node.planned .node-metric {
  color: var(--text-3);
}
.node-ledger {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.flow-node.primary .node-ledger {
  color: rgba(255, 255, 255, 0.8);
}
.flow-arrow {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  font-size: 15px;
  color: var(--text-4);
}

/* ══════ 工艺流程节点：按视口分三档 ══════
   为什么需要分档：5 个节点 + 4 个箭头挤在流程区里，节点宽度随视口线性缩小。
   实测（内距 14px + 图标 15 + 间距 7 时）：
     1920 → 节点 145px，名称/指标全放得下
     1584 → 节点 99px，留给"安全巡检"只剩 47px（需 52px）→ 被截成"安全…"
     1366 → 节点 71px，怎么调都放不下
   所以 1920（演示尺寸）保持大气排版，中等屏收紧，极窄屏再做取舍。 */

/* 中等屏（≤1700）：把内距/图标/字号各收一档，名称可用 61px → 4 字完整 */
@media (max-width: 1700px) {
  .flow-node { padding: 11px 10px; }
  .node-head { gap: 5px; }
  .node-icon { font-size: 13px; }
  .node-name { font-size: 13px; }
  /* 指标 14px：该档节点内宽 77px，「底流 67.71%」15px 需 80px、14px 约 75px */
  .node-metric { font-size: 14px; letter-spacing: -0.4px; }
  .node-ledger { font-size: 11px; }
}

/* 极窄屏（≤1400）：名称（导航依据）优先保证完整，指标数字允许省略号收尾。
   ⚠️ 试过把节点改成 3 列两行 —— 名称数字确实全露出来了，但左列高度从 ~150px
      涨到 254px，直接挤掉下面的「品位趋势」主图（768 高度下主区只有 409px）。
      **主图 > 节点全名**，所以保持单行。 */
@media (max-width: 1400px) {
  .flow-node { padding: 10px 7px; }
  .node-metric { font-size: 13px; letter-spacing: -0.6px; }
}

/* ============ 品位趋势（主视觉） ============ */
.trend-panel {
  flex: 1;
  min-height: 0;
  border-top: 2.5px solid var(--color-primary);
}
.legend {
  margin-left: auto;
  display: flex;
  gap: 18px;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12.5px;
  color: var(--text-2);
  white-space: nowrap;
}
.legend-line {
  width: 18px;
  height: 3px;
  border-radius: 2px;
}
.legend-line.pred { background: var(--color-primary); }
.legend-line.actual { background: var(--text-4); }

.trend-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 22px;
  align-items: stretch;
  margin-top: 12px;
}
.trend-nums {
  flex: 0 0 200px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
}
.trend-main-label {
  font-size: 12.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.trend-main-value {
  margin-top: 6px;
  font-size: 42px;
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
  letter-spacing: -1.5px;
}
.trend-main-unit {
  font-size: 16px;
  font-weight: var(--weight-normal);
  color: var(--text-3);
  letter-spacing: 0;
}
.trend-main-sub {
  margin-top: 9px;
  font-size: 12.5px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.trend-sep {
  height: 1px;
  background: var(--border-1);
  margin: 15px 0;
}
.trend-pair {
  display: flex;
  gap: 24px;
}
.trend-pair-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.trend-sub-label {
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
}
.trend-sub-value {
  font-size: 21px;
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.trend-pair-item:first-child .trend-sub-value {
  color: var(--color-primary);
}
.trend-chart {
  flex: 1;
  min-width: 0;
  min-height: 0;
}

/* ============ 需关注 ============ */
.alarm-panel {
  flex: 1;
  min-height: 0;
}
.alarm-panel .panel-header {
  margin-bottom: 10px;
}
.alarm-badge {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-primary);
  background: var(--color-primary-bg);
  padding: 3px 10px;
  border-radius: 7px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.sev-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.sev-track {
  flex: 1;
  min-width: 0;
  height: 6px;
  border-radius: 3px;
  overflow: hidden;
  display: flex;
  background: var(--bg-3);
}
.sev-track i {
  display: block;
  height: 100%;
}
.sev-track .sev-danger { background: var(--color-danger); }
.sev-track .sev-warning { background: var(--color-warning); }
.sev-legend {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.alarm-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.alarm-list::-webkit-scrollbar { width: 4px; }
.alarm-list::-webkit-scrollbar-thumb { background: #d0d3d8; border-radius: 4px; }
.alarm-empty {
  font-size: 13px;
  color: var(--text-3);
  padding: 20px 0;
  text-align: center;
}
.alarm-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 6px 10px;
  background: #fafbfc;
  border-radius: 8px;
  /* 卡片被拉高时由行自己长大来填满，而不是在下面留一片空白 */
  flex: 1 1 auto;
  min-height: 30px;
  max-height: 64px;
}

/* 两行式（大屏空间富余时）：上行告警消息，下行「场景 · 读数 · 时间」。
   把多出来的高度用来承载信息，而不是把单行文字撑高留白 */
.alarm-list.rich .alarm-body {
  flex-direction: column;
  align-items: stretch;
  gap: 3px;
}
.alarm-list.rich .alarm-msg {
  flex: 0 0 auto;
}
.alarm-list.rich .alarm-meta {
  align-self: flex-start;
  font-variant-numeric: tabular-nums;
}
.alarm-bar {
  width: 3px;
  border-radius: 2px;
  flex-shrink: 0;
  align-self: stretch;
}
.alarm-item.danger .alarm-bar { background: var(--color-danger); }
.alarm-item.warning .alarm-bar { background: var(--color-warning); }
.alarm-body {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}
.alarm-msg {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  line-height: 1.4;
  color: var(--text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.alarm-meta {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
}
.panel-foot {
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px solid var(--border-1);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 12.5px;
  color: var(--text-3);
}

/* ============ 运行摘要 ============ */
.summary-panel {
  flex: 0 0 auto;
  padding: 12px 18px;
}
.summary-panel .panel-header {
  margin-bottom: 6px;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
.summary-item {
  min-width: 0;
}
.summary-label {
  display: block;
  font-size: 11.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.summary-value {
  display: block;
  margin-top: 2px;
  font-size: 15px;
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
  line-height: 1.15;
}
.summary-value.warn {
  color: var(--color-warning);
}
.summary-value i {
  font-style: normal;
  font-size: 12px;
  font-weight: var(--weight-normal);
  color: var(--text-3);
  margin-left: 3px;
}

/* ============ 场景引擎 ============ */
.scene-panel {
  flex: 0 0 auto;
}
.scene-panel .panel-header {
  margin-bottom: 8px;
}
.scene-list {
  display: flex;
  flex-direction: column;
}
.scene-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 3px 0;
  cursor: pointer;
}
.scene-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-4);
  flex-shrink: 0;
}
.scene-item.active .scene-dot {
  background: var(--color-primary);
}
.scene-name {
  flex: 1;
  min-width: 0;
  font-size: 13.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: color var(--transition);
}
.scene-model {
  flex-shrink: 0;
  max-width: 104px;
  font-size: 11.5px;
  color: var(--text-4);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.scene-item:hover .scene-name {
  color: var(--color-primary);
}
.scene-item.planned .scene-name {
  color: var(--text-3);
}
.scene-item.planned {
  cursor: default;
}
.scene-count {
  font-size: 12.5px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* ============ 设备健康条带 ============ */
.equip-strip {
  flex: 0 0 auto;
  background: var(--bg-1);
  border-top: 1px solid var(--border-1);
  padding: 13px 20px 15px;
}
.strip-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 11px;
}
.strip-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: var(--weight-semibold);
}
.equip-row {
  display: flex;
  gap: 9px;
}
.equip-cell {
  position: relative;
  flex: 1;
  min-width: 0;
  padding: 10px 13px;
  background: var(--bg-2);
  border: 1px solid var(--border-1);
  border-radius: 10px;
  cursor: pointer;
  transition: background var(--transition), border-color var(--transition);
}
.equip-cell:hover,
.equip-cell.hovered {
  background: var(--bg-1);
  border-color: var(--color-primary);
}
.equip-cell.bad {
  background: #fff1f0;
  border-color: var(--color-danger);
}
.equip-cell.bad:hover,
.equip-cell.bad.hovered {
  border-color: var(--color-danger);
}
.cell-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}
.cell-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cell-temp {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}
/* 人工停机标记：设备停转后健康分仍会展示，必须有个"这台已经停了"的一眼标识 */
.cell-off {
  flex-shrink: 0;
  font-size: 11px;
  line-height: 1;
  padding: 2px 5px;
  border-radius: 4px;
  background: #f2f3f5;
  color: #86909c;
  border: 1px solid #e5e6eb;
}
.equip-cell.bad .cell-name,
.equip-cell.bad .cell-temp {
  color: #a32d2d;
}
.cell-score {
  margin-top: 6px;
  font-size: 22px;
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  line-height: 1;
}
.cell-score.warn { color: var(--color-warning); }
.cell-score.bad { color: var(--color-danger); }
.cell-bar {
  height: 4px;
  margin-top: 7px;
  border-radius: 2px;
  background: var(--bg-3);
  overflow: hidden;
}
.equip-cell.bad .cell-bar {
  background: #ffd6d1;
}
.cell-bar i {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--color-primary);
  transition: width 0.5s ease;
}
.cell-bar i.warn { background: var(--color-warning); }
.cell-bar i.bad { background: var(--color-danger); }

/* 悬停浮层：向上弹出，避开页面底边 */
.cell-pop {
  position: absolute;
  left: 0;
  bottom: calc(100% + 10px);
  width: 222px;
  padding: 14px 15px;
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-radius: 14px;
  box-shadow: var(--shadow-md);
  z-index: 30;
  cursor: default;
}
.equip-cell:nth-last-child(-n+2) .cell-pop {
  left: auto;
  right: 0;
}
.pop-title {
  font-size: 13px;
  font-weight: var(--weight-medium);
  margin-bottom: 10px;
}
.pop-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 14px;
}
.pop-item span {
  display: block;
  font-size: 12px;
  color: var(--text-3);
}
.pop-item strong {
  display: block;
  margin-top: 3px;
  font-size: 13.5px;
  font-weight: var(--weight-medium);
  font-variant-numeric: tabular-nums;
}

/* ============ 底栏 ============ */
.lw-footer {
  flex: 0 0 auto;
  height: 42px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  font-size: 12.5px;
  color: var(--text-3);
  background: var(--bg-1);
  border-top: 1px solid var(--border-1);
  box-sizing: border-box;
}
.footer-divider {
  color: #dfe2e8;
}
.refresh-indicator {
  display: flex;
  align-items: center;
  gap: 5px;
  transition: color var(--transition);
}
.refresh-indicator.refreshing {
  color: var(--color-primary);
}
.refresh-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success);
}
.refresh-indicator.refreshing .refresh-dot {
  background: var(--color-primary);
  animation: pulse-dot 1s ease-in-out infinite;
}

/* ============ 响应式 ============ */
/* 矮屏（1440×900 笔记本等）：内容高于视口时改为整页滚动，避免底栏被裁 */
@media (max-height: 940px) {
  .livewall {
    height: auto;
    min-height: 100%;
    overflow: visible;
  }
  /* 矮屏：主区不锁高，各卡按内容自然撑开，整页滚动（不再往卡内部压） */
  .lw-main {
    flex: 0 0 auto;
    height: auto;
    min-height: 0;
    padding-bottom: 16px;
  }
  .trend-panel {
    flex: 0 0 auto;
    height: 380px;
  }
  .trend-chart {
    min-height: 250px;
  }
  .alarm-panel {
    flex: 0 0 auto;
  }
  .alarm-list {
    flex: 0 0 auto;
    overflow-y: visible;
  }
}
@media (max-width: 1600px) {
  /* 窄屏右列放不下 4 格摘要，改 2×2 保证标签完整 */
  .summary-grid {
    grid-template-columns: 1fr 1fr;
    gap: 10px 16px;
  }
}
@media (max-width: 1280px) {
  .lw-main {
    grid-template-columns: minmax(0, 1.8fr) minmax(0, 1fr);
  }
}
@media (max-width: 1024px) {
  .livewall {
    height: auto;
    overflow: auto;
  }
  .lw-main {
    grid-template-columns: 1fr;
  }
  .equip-row {
    flex-wrap: wrap;
  }
  .equip-cell {
    flex: 1 1 calc(33% - 10px);
  }
}
</style>
