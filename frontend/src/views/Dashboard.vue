<script setup>
import { cssVar } from '../utils/chartColor'

import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import api from '../api'
import PageShell from '../components/PageShell.vue'
import StatCard from '../components/StatCard.vue'

const router = useRouter()

// ---- 数据 ----
const loading = ref(true)
const stats = ref(null)
const trend = ref(null)
const scenes = ref([])
const equipData = ref(null)
const safetyStats = ref(null)
const todayReport = ref(null)
const yesterdayReport = ref(null)
const production7d = ref([])
const shifts = ref([])
const waterOverview = ref(null)
const waterStations = ref([])

// ---- 自动刷新（30s，与 LiveWall 同约定）----
// 静默刷新：只更新数据与图表（echarts setOption 原位更新，不闪），不亮任何遮罩
const REFRESH_INTERVAL = 30000
const lastUpdate = ref('')
let refreshTimer = null

// 今日六维 KPI（去 emoji 改 SVG 图标名 + 统一主色，状态色按需）
const kpis = ref([
  { key: 'analysis', title: '今日分析', value: '—', sub: '', icon: 'DataAnalysis', route: '/workbench/flotation' },
  { key: 'equip', title: '设备在线率', value: '—', sub: '', icon: 'Cpu', route: '/equipment' },
  { key: 'safety', title: '今日安全巡检', value: '—', sub: '', icon: 'Aim', route: '/safety' },
  { key: 'alarm', title: '今日告警待处理', value: '—', sub: '', icon: 'Bell', route: '/alarms' },
  { key: 'grade', title: '平均铅品位', value: '—', sub: '', icon: 'TrendCharts', unit: '%', route: '/workbench/flotation' },
  { key: 'shift', title: '班产达标', value: '—', sub: '', icon: 'Histogram', route: '/reports' },
])

// 图表 DOM 引用
const trendChartRef = ref(null)
const distChartRef = ref(null)
const safetyPieRef = ref(null)
const productionRef = ref(null)
const shiftRef = ref(null)
let trendChart = null, distChart = null, safetyPie = null, productionChart = null, shiftChart = null

// ---- 工具：对比箭头（用令牌色，不再硬编码） ----
function diffText(today, yesterday) {
  if (yesterday == null || yesterday === 0) return ''
  const d = today - yesterday
  if (d === 0) return '持平'
  const pct = ((d / yesterday) * 100).toFixed(1)
  return d > 0 ? `↑ ${pct}%` : `↓ ${Math.abs(pct)}%`
}
function diffColor(today, yesterday, inverse = false) {
  if (yesterday == null) return cssVar('--text-3')
  const d = today - yesterday
  if (d === 0) return cssVar('--text-3')
  const good = d > 0
  const bad = d < 0
  if (inverse) return bad ? cssVar('--color-danger') : cssVar('--color-success')
  return good ? cssVar('--color-success') : cssVar('--color-danger')
}

// 场景状态中文名（status 由后端 scene_packages 注册表透传）
function sceneStatusCn(s) {
  return { active: '运行中', paused: '已暂停', planned: '规划中', archived: '已下线' }[s] || s
}

// ---- 加载数据（逻辑不变） ----
async function loadData(opts = {}) {
  const silent = !!opts.silent      // 自动刷新走静默：不闪 loading、不改按钮态
  if (!silent) loading.value = true
  try {
    const today = new Date().toISOString().slice(0, 10)
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10)

    const [scenesRes, statsRes, trendRes, equipRes, safetyRes, todayRep, yestRep, waterOv, waterSt] = await Promise.all([
      api.get('/scenes'),
      api.get('/scenes/flotation/stats'),
      api.get('/scenes/flotation/trend?days=90'),
      api.get('/equipment'),
      api.get('/safety/stats'),
      api.get(`/reports/daily?date=${today}`),
      api.get(`/reports/daily?date=${yesterday}`),
      // 水质模块单独兜底：即使该模块异常也不拖垮整个仪表盘
      api.get('/water/overview').catch(() => null),
      api.get('/water/stations').catch(() => null),
    ])

    waterOverview.value = waterOv
    waterStations.value = waterSt?.stations || []

    scenes.value = scenesRes.scenes || []
    stats.value = statsRes
    trend.value = trendRes
    equipData.value = equipRes
    safetyStats.value = safetyRes
    todayReport.value = todayRep
    yesterdayReport.value = yestRep

    const prodPromises = []
    for (let i = 6; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10)
      prodPromises.push(api.get(`/reports/daily?date=${d}`))
    }
    const prodResults = await Promise.all(prodPromises)
    production7d.value = prodResults.map(r => ({
      date: r.date,
      pb: r.flotation?.pb_grade?.avg,
      zn: r.flotation?.zn_grade?.avg,
      records: r.flotation?.records || 0,
      alarms: r.alarms?.total || 0,
    }))

    const shiftKeys = ['morning', 'evening', 'night']
    const shiftResults = await Promise.all(
      shiftKeys.map(s => api.get(`/reports/shift?date=${today}&shift=${s}`))
    )
    shifts.value = shiftResults

    fillKPIs()
    await nextTick()
    renderCharts()
    lastUpdate.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (e) {
    console.error('仪表盘数据加载失败:', e)
  } finally {
    if (!silent) loading.value = false
  }
}

function fillKPIs() {
  const t = todayReport.value || {}
  const y = yesterdayReport.value || {}
  const eq = equipData.value?.stats || {}
  const ss = safetyStats.value || {}

  const todayAnalysis = (t.flotation?.records || 0) + (t.safety?.records || 0)
  const yestAnalysis = (y.flotation?.records || 0) + (y.safety?.records || 0)
  kpis.value[0].value = todayAnalysis.toLocaleString()
  kpis.value[0].sub = diffText(todayAnalysis, yestAnalysis)
  kpis.value[0].subColor = diffColor(todayAnalysis, yestAnalysis)

  const online = (eq.running || 0) + (eq.warning || 0)
  const total = eq.total || 1
  const rate = ((online / total) * 100).toFixed(0)
  kpis.value[1].value = `${rate}%`
  kpis.value[1].sub = `${online}/${total}台 · 危险${eq.danger || 0}`
  kpis.value[1].subColor = cssVar('--text-3')

  const todaySafety = ss.today_count || 0
  const totalSafety = ss.total_count || 0
  kpis.value[2].value = todaySafety.toLocaleString()
  kpis.value[2].sub = `累计${totalSafety} · 隐患${ss.hazard_open || 0}`
  kpis.value[2].subColor = cssVar('--text-3')

  const unack = t.alarms?.unacknowledged || 0
  const totalAlarm = t.alarms?.total || 0
  kpis.value[3].value = unack > 0 ? `${unack} 条` : '无'
  kpis.value[3].sub = `今日共 ${totalAlarm} 条`
  kpis.value[3].subColor = unack > 0 ? cssVar('--color-danger') : cssVar('--color-success')

  const pbAvg = t.flotation?.pb_grade?.avg
  const yestPbAvg = y.flotation?.pb_grade?.avg
  kpis.value[4].value = pbAvg != null ? pbAvg.toFixed(2) : '—'
  kpis.value[4].sub = yestPbAvg != null ? diffText(pbAvg, yestPbAvg) : '昨日无数据'
  kpis.value[4].subColor = yestPbAvg != null ? diffColor(pbAvg, yestPbAvg) : cssVar('--text-3')

  const currentShift = shifts.value.find(s => {
    const now = new Date().getHours()
    if (now >= 8 && now < 16) return s.shift === 'morning'
    if (now >= 16 && now < 24) return s.shift === 'evening'
    return s.shift === 'night'
  })
  const shiftRecords = currentShift?.flotation?.records || 0
  const target = 30
  const pct = Math.min(100, (shiftRecords / target) * 100).toFixed(0)
  kpis.value[5].value = `${pct}%`
  kpis.value[5].sub = `${(currentShift?.shift_label || '当前班').split(' ')[0]} ${shiftRecords}/${target}`
  kpis.value[5].subColor = pct >= 100 ? cssVar('--color-success') : (pct >= 50 ? cssVar('--color-warning') : cssVar('--color-danger'))
}

// ---- 水质：评分等级（与 WaterMonitor.vue 保持一致，避免两页口径不一） ----
function scoreLevel(score) {
  if (score === null || score === undefined) return 'na'
  if (score >= 85) return 'good'
  if (score >= 70) return 'fair'
  if (score >= 50) return 'poor'
  return 'bad'
}
function statusCn(s) {
  return { normal: '正常', warning: '预警', danger: '危险' }[s] || '正常'
}

function goRoute(route) {
  if (route) router.push(route)
}

// ---- ECharts 渲染（配色收敛到 3 色：主色 + 中性灰 + 状态色） ----
function renderCharts() {
  const C1 = getComputedStyle(document.documentElement).getPropertyValue('--chart-1').trim() || cssVar('--color-primary')
  const C2 = getComputedStyle(document.documentElement).getPropertyValue('--chart-2').trim() || cssVar('--text-3')
  const C3 = getComputedStyle(document.documentElement).getPropertyValue('--chart-3').trim() || cssVar('--color-success')
  const CDANGER = getComputedStyle(document.documentElement).getPropertyValue('--chart-danger').trim() || cssVar('--color-danger')
  const CWARN = getComputedStyle(document.documentElement).getPropertyValue('--chart-warning').trim() || cssVar('--color-warning')
  const C1LIGHT = getComputedStyle(document.documentElement).getPropertyValue('--chart-1-light').trim() || '#94b8ff'
  const TEXT3 = getComputedStyle(document.documentElement).getPropertyValue('--text-3').trim() || cssVar('--text-3')
  const BORDER = getComputedStyle(document.documentElement).getPropertyValue('--border-1').trim() || cssVar('--border-1')

  // 1. 铅品位趋势（主色 + 中性灰对比）
  if (trendChartRef.value) {
    if (!trendChart) trendChart = echarts.init(trendChartRef.value)
    const d = trend.value?.daily || []
    trendChart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['预测铅品位', '化验真值'], bottom: 0, textStyle: { color: TEXT3 } },
      grid: { top: 20, right: 40, bottom: 48, left: 50 },
      xAxis: { type: 'category', data: d.map(r => r.date.slice(5)), axisLabel: { rotate: 30, fontSize: 10, color: TEXT3 }, axisLine: { lineStyle: { color: BORDER } } },
      yAxis: { type: 'value', name: 'Pb (%)', scale: true, axisLabel: { color: TEXT3 }, splitLine: { lineStyle: { color: BORDER } } },
      series: [
        { name: '预测铅品位', type: 'line', smooth: true, data: d.map(r => r.avg_pred_pb),
          lineStyle: { width: 2 }, itemStyle: { color: C1 },
          areaStyle: { color: C1, opacity: 0.08 } },
        { name: '化验真值', type: 'line', smooth: true, data: d.map(r => r.avg_actual),
          lineStyle: { width: 1.5, type: 'dashed' }, itemStyle: { color: C2 } },
      ],
    }, true)
  }

  // 2. 品位分布（单一主色深浅，不再 6 色彩虹）
  if (distChartRef.value) {
    if (!distChart) distChart = echarts.init(distChartRef.value)
    const gd = stats.value?.grade_distribution || {}
    const data = [gd.min, gd.p25, gd.median, gd.p75, gd.max, gd.mean]
    const colors = [C2, C1LIGHT, C1LIGHT, C1LIGHT, C1, C1]  // 极值用主色，其余用浅主色
    distChart.setOption({
      tooltip: { trigger: 'axis', formatter: '{b}: {c}%' },
      grid: { top: 20, right: 20, bottom: 30, left: 50 },
      xAxis: { type: 'category', data: ['最小值', 'P25', '中位数', 'P75', '最大值', '均值'], axisLabel: { color: TEXT3 }, axisLine: { lineStyle: { color: BORDER } } },
      yAxis: { type: 'value', name: 'Pb (%)', axisLabel: { color: TEXT3 }, splitLine: { lineStyle: { color: BORDER } } },
      series: [{
        type: 'bar',
        data: data.map((v, i) => ({ value: v, itemStyle: { color: colors[i] } })),
        label: { show: true, position: 'top', formatter: '{c}', fontSize: 11, color: TEXT3 },
        barWidth: '50%',
      }],
    }, true)
  }

  // 3. 安全风险饼图（4 状态色，但降饱和）
  if (safetyPieRef.value) {
    if (!safetyPie) safetyPie = echarts.init(safetyPieRef.value)
    const rd = safetyStats.value?.risk_distribution_30d || {}
    const palette = { low: C3, medium: CWARN, high: CDANGER, critical: '#722ed1' }
    const data = Object.entries(rd).map(([k, v]) => ({
      name: { low: '低风险', medium: '中风险', high: '高风险', critical: '危急' }[k] || k,
      value: v,
      itemStyle: { color: palette[k] || C2 }
    }))
    safetyPie.setOption({
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { bottom: 0, type: 'scroll', textStyle: { color: TEXT3 } },
      series: [{
        type: 'pie', radius: ['42%', '68%'], center: ['50%', '45%'],
        avoidLabelOverlap: true,
        label: { show: true, formatter: '{d}%', fontSize: 11, color: TEXT3 },
        data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: BORDER } }],
      }],
    }, true)
  }

  // 4. 近 7 天产量（主色 + 中性灰 + 危险色，3 色不彩虹）
  if (productionRef.value) {
    if (!productionChart) productionChart = echarts.init(productionRef.value)
    const p = production7d.value
    productionChart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['铅品位', '锌品位', '告警数'], bottom: 0, textStyle: { color: TEXT3 } },
      grid: { top: 20, right: 40, bottom: 46, left: 45 },
      xAxis: { type: 'category', data: p.map(r => r.date.slice(5)), axisLabel: { fontSize: 10, color: TEXT3 }, axisLine: { lineStyle: { color: BORDER } } },
      yAxis: [
        { type: 'value', name: '品位 %', position: 'left', axisLabel: { color: TEXT3 }, splitLine: { lineStyle: { color: BORDER } } },
        { type: 'value', name: '告警数', position: 'right', axisLabel: { color: TEXT3 }, splitLine: { show: false } },
      ],
      series: [
        { name: '铅品位', type: 'bar', data: p.map(r => r.pb?.toFixed(2) ?? null), itemStyle: { color: C1 }, barWidth: '28%' },
        { name: '锌品位', type: 'bar', data: p.map(r => r.zn?.toFixed(2) ?? null), itemStyle: { color: C1LIGHT }, barWidth: '28%' },
        { name: '告警数', type: 'line', yAxisIndex: 1, data: p.map(r => r.alarms), itemStyle: { color: CDANGER }, lineStyle: { width: 2 } },
      ],
    }, true)
  }

  // 5. 三班次对比（主色 + 中性灰 + 主色折线，3 色不彩虹）
  if (shiftRef.value) {
    if (!shiftChart) shiftChart = echarts.init(shiftRef.value)
    const s = shifts.value
    shiftChart.setOption({
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const idx = params[0]?.dataIndex ?? 0
          const row = s[idx] || {}
          const period = row.period ? `${row.period.start?.slice(11, 16)}~${row.period.end?.slice(11, 16)}` : ''
          return `${row.shift_label || ''} ${period}<br/>` +
            params.map(p => `${p.marker}${p.seriesName}: ${p.value ?? '—'}`).join('<br/>')
        },
      },
      legend: { bottom: 0, textStyle: { color: TEXT3 } },
      grid: { top: 20, right: 20, bottom: 46, left: 45 },
      xAxis: {
        type: 'category',
        data: s.map(x => (x.shift_label || '').split(' ')[0]),
        axisLabel: { fontSize: 12, interval: 0, color: TEXT3 },
        axisLine: { lineStyle: { color: BORDER } },
      },
      yAxis: [
        { type: 'value', name: '记录数', axisLabel: { color: TEXT3 }, splitLine: { lineStyle: { color: BORDER } } },
        { type: 'value', name: '铅品位 %', position: 'right', axisLabel: { color: TEXT3 }, splitLine: { show: false } },
      ],
      series: [
        { name: '分析记录', type: 'bar', data: s.map(x => x.flotation?.records || 0), itemStyle: { color: C1 }, barWidth: '32%' },
        { name: '安全巡检', type: 'bar', data: s.map(x => x.safety?.records || 0), itemStyle: { color: C1LIGHT }, barWidth: '32%' },
        { name: '铅品位', type: 'line', yAxisIndex: 1, data: s.map(x => x.flotation?.pb_avg?.toFixed(2) ?? null), itemStyle: { color: C2 }, lineStyle: { width: 2 } },
      ],
    }, true)
  }
}

function handleResize() {
  trendChart?.resize(); distChart?.resize(); safetyPie?.resize(); productionChart?.resize(); shiftChart?.resize()
}

onMounted(() => {
  loadData()
  refreshTimer = setInterval(() => loadData({ silent: true }), REFRESH_INTERVAL)
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  window.removeEventListener('resize', handleResize)
  trendChart?.dispose(); distChart?.dispose(); safetyPie?.dispose(); productionChart?.dispose(); shiftChart?.dispose()
})
</script>

<template>
  <PageShell
    title="仪表盘"
    :sub="`铅锌矿浮选过程智能视觉分析 · ${new Date().toLocaleDateString('zh-CN')}`"
  >
    <template #actions>
      <span class="auto-hint">
        自动刷新 30s<template v-if="lastUpdate"> · 最后更新 {{ lastUpdate }}</template>
      </span>
      <el-button size="small" :loading="loading" @click="loadData()">
        <el-icon><Refresh /></el-icon>&nbsp;刷新
      </el-button>
    </template>

    <!-- 今日四格 KPI（规范：固定 4 格）。
         ★ 原为六格（分析/设备/安全/告警/品位/班产），按"固定 4 格"取舍：
           保留 今日分析 · 设备在线率 · 今日告警 · 平均品位；
           今日安全巡检并入告警格副行（数字不丢），班产达标属报表口径，不在门面重复。 -->
    <template #kpi>
      <StatCard
        class="kpi-link"
        label="今日分析"
        :value="kpis[0].value"
        :foot="kpis[0].sub"
        tone="primary"
        @click="goRoute(kpis[0].route)"
      />
      <StatCard
        class="kpi-link"
        label="设备在线率"
        :value="kpis[1].value"
        :foot="kpis[1].sub"
        @click="goRoute(kpis[1].route)"
      />
      <StatCard
        class="kpi-link"
        label="今日告警待处理"
        :value="kpis[3].value"
        :foot="`巡检 ${kpis[2].value} · ${kpis[3].sub}`"
        tone="warning"
        @click="goRoute(kpis[3].route)"
      />
      <StatCard
        class="kpi-link"
        label="平均铅品位"
        :value="kpis[4].value"
        :unit="kpis[4].unit"
        :foot="kpis[4].sub"
        @click="goRoute(kpis[4].route)"
      />
    </template>

    <!-- 场景状态 -->
    <section class="block">
      <h2>场景状态</h2>
      <div class="scene-cards">
        <div v-for="s in scenes" :key="s.name" class="scene-card" :class="{ active: s.status === 'active', planned: s.status !== 'active' }">
          <div class="scene-name">{{ s.display_name }}</div>
          <div class="scene-meta">
            <span>记录 <b>{{ s.records == null ? '—' : s.records.toLocaleString() }}</b></span>
            <span>模型 <b>{{ s.active_model || '—' }}</b></span>
          </div>
          <el-tag :type="s.status === 'active' ? 'success' : 'info'" size="small" effect="plain">
            {{ sceneStatusCn(s.status) }}
          </el-tag>
        </div>
      </div>
    </section>

    <!-- 设备状态概览 + 安全风险分布（双栏） -->
    <div class="dual-row">
      <div class="card card-clickable" @click="goRoute('/equipment')">
        <div class="card-head">
          <h3>设备状态概览</h3>
          <span class="card-link">查看详情</span>
        </div>
        <div v-if="equipData" class="equip-grid">
          <div v-for="e in equipData.equipment" :key="e.id" class="equip-item" :class="`st-${e.status}`">
            <span class="equip-dot"></span>
            <div class="equip-info">
              <div class="equip-name">{{ e.name }}</div>
              <div class="equip-health">H {{ e.health?.toFixed(0) }}</div>
            </div>
          </div>
        </div>
        <div v-if="equipData" class="equip-summary">
          <span>在线 <b class="ok">{{ ((equipData.stats.running + equipData.stats.warning) / equipData.stats.total * 100).toFixed(0) }}%</b></span>
          <span>危险 <b class="bad">{{ equipData.stats.danger }}</b></span>
          <span>平均健康 <b>{{ equipData.stats.avg_health?.toFixed(0) }}</b></span>
        </div>
      </div>

      <div class="card card-clickable" @click="goRoute('/safety')">
        <div class="card-head">
          <h3>安全风险分布 · 30 天</h3>
          <span class="card-link">查看详情</span>
        </div>
        <div ref="safetyPieRef" class="chart-box-sm"></div>
        <div v-if="safetyStats" class="equip-summary">
          <span>今日巡检 <b>{{ safetyStats.today_count }}</b></span>
          <span>隐患待处理 <b class="bad">{{ safetyStats.hazard_open }}</b></span>
          <span>PPE合规 <b>{{ safetyStats.ppe_compliance_avg != null ? (safetyStats.ppe_compliance_avg * 100).toFixed(1) + '%' : '—' }}</b></span>
        </div>
      </div>
    </div>

    <!-- 脱水工段水质概览 -->
    <section class="block" v-if="waterOverview">
      <h2>脱水工段水质</h2>
      <div class="card card-clickable" @click="goRoute('/water')">
        <div class="card-head">
          <h3>回水可回用评分</h3>
          <span class="card-link">查看详情</span>
        </div>
        <div class="water-grid">
          <div class="water-score">
            <div class="ws-num" :class="scoreLevel(waterOverview.avg_reuse_score)">
              {{ waterOverview.avg_reuse_score?.toFixed(1) }}<small>分</small>
            </div>
            <div class="ws-label">
              5 点位平均 ·
              <template v-if="waterOverview.abnormal_count > 0">{{ waterOverview.abnormal_count }} 个点位待改善</template>
              <template v-else>全部达标</template>
            </div>
          </div>
          <div class="water-stations">
            <div v-for="s in waterStations" :key="s.id" class="wst-row">
              <span class="wst-dot" :class="'wst-' + s.status" :title="statusCn(s.status)"></span>
              <span class="wst-name">{{ s.short || s.name }}</span>
              <span class="wst-bar"><i :style="{ width: (s.reuse?.score || 0) + '%' }" :class="scoreLevel(s.reuse?.score)"></i></span>
              <span class="wst-score" :class="scoreLevel(s.reuse?.score)">{{ s.reuse?.score?.toFixed(1) ?? '—' }}</span>
            </div>
          </div>
          <div class="water-metrics">
            <div class="wm-item">
              <span class="wm-label">异常点位</span>
              <b class="wm-val" :class="{ bad: (waterOverview.abnormal_count || 0) > 0 }">{{ waterOverview.abnormal_count }}</b>
            </div>
            <div class="wm-item">
              <span class="wm-label">近 7 天事件</span>
              <b class="wm-val">{{ waterOverview.events_7d }}</b>
            </div>
            <div class="wm-item">
              <span class="wm-label">回水总流量</span>
              <b class="wm-val">{{ waterOverview.total_flow_m3h?.toFixed(0) }}<small>m³/h</small></b>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 产量趋势 + 班次对比 -->
    <div class="dual-row">
      <div class="card">
        <div class="card-head"><h3>近 7 天产量趋势</h3></div>
        <div ref="productionRef" class="chart-box"></div>
      </div>
      <div class="card">
        <div class="card-head"><h3>今日班次对比</h3></div>
        <div ref="shiftRef" class="chart-box"></div>
      </div>
    </div>

    <!-- 品位趋势 + 分布 -->
    <div class="charts-row">
      <div class="card">
        <div class="card-head"><h3>铅品位趋势 · 近 90 天</h3></div>
        <div ref="trendChartRef" class="chart-box"></div>
      </div>
      <div class="card">
        <div class="card-head"><h3>品位分布</h3></div>
        <div ref="distChartRef" class="chart-box"></div>
      </div>
    </div>

    <!-- 最近告警 -->
    <section class="block" v-if="trend?.recent_alarms?.length">
      <h2>最近告警</h2>
      <el-table :data="trend.recent_alarms" size="small" stripe max-height="300">
        <el-table-column prop="created_at" label="时间" width="170">
          <template #default="{ row }">{{ row.created_at?.slice(0, 19).replace('T', ' ') }}</template>
        </el-table-column>
        <el-table-column prop="level" label="级别" width="80">
          <template #default="{ row }">
            <el-tag :type="row.level === 'danger' ? 'danger' : 'warning'" size="small" effect="plain">{{ row.level }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="rule_name" label="规则" width="140" />
        <el-table-column prop="message" label="内容" />
        <el-table-column prop="is_acknowledged" label="状态" width="90">
          <template #default="{ row }">
            <span :class="{ 'text-4': !row.is_acknowledged }">{{ row.is_acknowledged ? '已确认' : '待处理' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 数据概览 -->
    <section class="block" v-if="stats">
      <h2>数据概览</h2>
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="场景">浮选 (flotation)</el-descriptions-item>
        <el-descriptions-item label="总记录数">{{ stats.total_records?.toLocaleString() }}</el-descriptions-item>
        <el-descriptions-item label="分析组数">{{ stats.total_groups?.toLocaleString() }}</el-descriptions-item>
        <el-descriptions-item label="数据起始">{{ stats.date_range?.from?.slice(0, 10) }}</el-descriptions-item>
        <el-descriptions-item label="数据截止">{{ stats.date_range?.to?.slice(0, 10) }}</el-descriptions-item>
        <el-descriptions-item label="有真值记录">{{ stats.grade_distribution?.count?.toLocaleString() }} ({{ ((stats.grade_distribution?.count / stats.total_records) * 100).toFixed(1) }}%)</el-descriptions-item>
        <el-descriptions-item label="品位最小值">{{ stats.grade_distribution?.min?.toFixed(2) }}%</el-descriptions-item>
        <el-descriptions-item label="品位均值">{{ stats.grade_distribution?.mean?.toFixed(2) }}%</el-descriptions-item>
        <el-descriptions-item label="品位最大值">{{ stats.grade_distribution?.max?.toFixed(2) }}%</el-descriptions-item>
      </el-descriptions>
    </section>
  </PageShell>
</template>

<style scoped>
/* 页头/留白/返回入口由 PageShell 提供（2026-10-19 一致性整治） */
.kpi-link { cursor: pointer; }
.kpi-link:hover { border-color: var(--color-primary); }
.auto-hint { font-size: var(--text-xs); color: var(--text-3); }

.block {
  margin-bottom: var(--space-5);
}
.block h2 {
  margin: 0 0 var(--space-3);
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}

/* 场景卡片 —— 左边框状态色，不再花哨。
   场景包固定 6 个、容器宽固定 1320px，故显式 3×2 均衡排布：
   原 auto-fill/240px 会排成 5 列 → 末行只剩 1 张孤卡。 */
.scene-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
}
.scene-card {
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-left: 3px solid var(--border-1);
  border-radius: var(--radius);
  padding: var(--space-4);
  transition: border-color var(--transition);
  /* 两行式：① 名称 + 状态标签  ② 记录 / 模型 两列对齐，铺满卡宽 */
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    "name tag"
    "meta meta";
  align-items: center;
  gap: var(--space-2) var(--space-3);
}
.scene-card.active {
  border-left-color: var(--color-success);
}
.scene-card.planned {
  opacity: 0.7;
}
.scene-name {
  grid-area: name;
  font-weight: var(--weight-semibold);
  font-size: var(--text-base);
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.scene-card > .el-tag {
  grid-area: tag;
  justify-self: end;
}
.scene-meta {
  grid-area: meta;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2);
  margin: 0;
  font-size: var(--text-xs);
  color: var(--text-2);
}
.scene-meta b {
  color: var(--text-1);
  font-weight: var(--weight-semibold);
}

/* 卡片基础 —— 白底 + 1px 边框 + 轻圆角，不用重阴影 */
.card {
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-radius: var(--radius);
  padding: var(--space-4);
}
.dual-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}
.charts-row {
  display: grid;
  grid-template-columns: 1.6fr 1fr;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}
.card-clickable {
  cursor: pointer;
  transition: border-color var(--transition);
}
.card-clickable:hover {
  border-color: var(--color-primary);
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-3);
}
.card-head h3 {
  margin: 0;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
}
.card-link {
  font-size: var(--text-xs);
  color: var(--color-primary);
  font-weight: var(--weight-normal);
}
.chart-box {
  height: 280px;
}
.chart-box-sm {
  height: 200px;
}

/* 设备概览 */
.equip-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}
.equip-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--bg-3);
}
.equip-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.st-running .equip-dot { background: var(--color-success); }
.st-warning .equip-dot { background: var(--color-warning); }
.st-danger .equip-dot { background: var(--color-danger); box-shadow: 0 0 6px var(--color-danger); }
.st-stopped .equip-dot { background: var(--text-4); }
.equip-info {
  flex: 1;
  min-width: 0;
}
.equip-name {
  font-size: var(--text-xs);
  color: var(--text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.equip-health {
  font-size: 11px;
  color: var(--text-3);
}
.equip-summary {
  display: flex;
  gap: var(--space-4);
  font-size: var(--text-xs);
  color: var(--text-2);
  padding-top: var(--space-2);
  border-top: 1px solid var(--border-2);
}
.equip-summary b {
  color: var(--text-1);
  font-weight: var(--weight-semibold);
}
.equip-summary .ok {
  color: var(--color-success);
}
.equip-summary .bad {
  color: var(--color-danger);
}

/* 脱水工段水质卡 —— 三栏：总分 / 点位条 / 关键指标 */
.water-grid {
  display: grid;
  grid-template-columns: 170px 1fr 190px;
  gap: var(--space-5);
  align-items: center;
}
.water-score {
  border-right: 1px solid var(--border-2);
  padding-right: var(--space-4);
}
.ws-num {
  font-size: 38px;
  font-weight: var(--weight-semibold);
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
.ws-num small {
  font-size: var(--text-sm);
  font-weight: var(--weight-normal);
  color: var(--text-3);
  margin-left: 3px;
}
.ws-num.good { color: var(--color-success); }
.ws-num.fair { color: var(--color-primary); }
.ws-num.poor { color: var(--color-warning); }
.ws-num.bad { color: var(--color-danger); }
.ws-label {
  font-size: var(--text-xs);
  color: var(--text-3);
  margin-top: var(--space-2);
  line-height: 1.5;
}
.water-stations {
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
}
.wst-row {
  display: grid;
  grid-template-columns: 8px 96px 1fr 44px;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.wst-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.wst-normal { background: var(--color-success); }
.wst-warning { background: var(--color-warning); }
.wst-danger { background: var(--color-danger); box-shadow: 0 0 6px var(--color-danger); }
.wst-name {
  font-size: var(--text-xs);
  color: var(--text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.wst-bar {
  height: 6px;
  background: var(--bg-3);
  border-radius: 3px;
  overflow: hidden;
}
.wst-bar i {
  display: block;
  height: 100%;
  border-radius: 3px;
  transition: width var(--transition);
}
.wst-bar i.good { background: var(--color-success); }
.wst-bar i.fair { background: var(--color-primary); }
.wst-bar i.poor { background: var(--color-warning); }
.wst-bar i.bad { background: var(--color-danger); }
.wst-score {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.wst-score.good { color: var(--color-success); }
.wst-score.fair { color: var(--color-primary); }
.wst-score.poor { color: var(--color-warning); }
.wst-score.bad { color: var(--color-danger); }
.water-metrics {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  border-left: 1px solid var(--border-2);
  padding-left: var(--space-4);
}
.wm-item {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.wm-label {
  font-size: var(--text-xs);
  color: var(--text-3);
}
.wm-val {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
}
.wm-val.bad { color: var(--color-danger); }
.wm-val small {
  font-size: 10px;
  font-weight: var(--weight-normal);
  color: var(--text-3);
  margin-left: 3px;
}

/* 响应式 */
@media (max-width: 1100px) {
  .kpi-row { grid-template-columns: repeat(3, 1fr); }
  .scene-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dual-row { grid-template-columns: 1fr; }
  .water-grid { grid-template-columns: 1fr; gap: var(--space-4); }
  .water-score {
    border-right: none;
    padding-right: 0;
    border-bottom: 1px solid var(--border-2);
    padding-bottom: var(--space-3);
  }
  .water-metrics {
    border-left: none;
    padding-left: 0;
    border-top: 1px solid var(--border-2);
    padding-top: var(--space-3);
    flex-direction: row;
    gap: var(--space-5);
  }
  .wm-item { flex-direction: column; align-items: flex-start; gap: 2px; }
}
@media (max-width: 700px) {
  .kpi-row { grid-template-columns: repeat(2, 1fr); }
  .scene-cards { grid-template-columns: 1fr; }
  .scene-meta { grid-template-columns: 1fr; }
  .charts-row { grid-template-columns: 1fr; }
}
</style>
