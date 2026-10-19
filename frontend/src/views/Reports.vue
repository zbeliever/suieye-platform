<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import api from '../api'

// ---- 数据 ----
const loading = ref(true)
const report = ref(null)
const dateRange = ref(30)

// 图表引用
const trendChartRef = ref(null)
const distChartRef = ref(null)
const alarmChartRef = ref(null)
const histogramRef = ref(null)
let trendChart = null
let distChart = null
let alarmChart = null
let histogramChart = null

// KPI 动画状态
const kpiReady = ref(false)

// ---- 加载 ----
async function loadData() {
  loading.value = true
  kpiReady.value = false
  try {
    const res = await api.get('/reports/summary', { params: { days: dateRange.value } })
    report.value = res
    await nextTick()
    renderCharts()
    // 延迟触发 KPI 动画（等图表渲染完）
    setTimeout(() => { kpiReady.value = true }, 300)
  } catch (e) {
    console.error('加载报表失败:', e)
  } finally {
    loading.value = false
  }
}

function handleRangeChange() {
  loadData()
}

// 导出报表 —— 导「当前所选时间范围」（近 7/30/90 天）
// ★ 之前是把"今天"固定写进 URL：数据不保证每天都有，点导出会拿到一个只有表头的空文件。
//   改成 days 模式后，后端把终点锚定到「最新有数据的日期」，导出内容与页面图表范围一致。
function exportReport(fmt) {
  const url = `/api/reports/export?format=${fmt}&days=${dateRange.value}&scope=all`
  window.open(url, '_blank')
}

// ---- 图表渲染 ----
function renderCharts() {
  if (!report.value) return
  const d = report.value

  renderTrendChart(d)
  renderDistChart(d)
  renderAlarmChart(d)
  renderHistogram(d)
}

function renderTrendChart(d) {
  if (!trendChartRef.value) return
  if (!trendChart) trendChart = echarts.init(trendChartRef.value)
  const dates = d.daily.map(item => item.date.slice(5))
  const preds = d.daily.map(item => item.avg_pred)
  const actuals = d.daily.map(item => item.avg_actual)

  // 计算目标线和警戒线
  const allVals = [...preds, ...actuals].filter(v => v != null && v > 0)
  const targetLine = d.overview?.overall_avg_actual || (allVals.length ? allVals.reduce((a,b)=>a+b)/allVals.length : 5.5)
  const upperWarn = targetLine * 1.3
  const lowerWarn = targetLine * 0.7

  trendChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: '#e5e6eb',
      textStyle: { color: '#1d2129', fontSize: 12 },
      extraCssText: 'box-shadow: var(--shadow-md);',
    },
    legend: {
      data: ['预测品位', '真实品位', '目标线', '警戒区间'],
      bottom: 0,
      textStyle: { fontSize: 11, color: '#86909c' },
      itemWidth: 16,
      itemHeight: 3,
    },
    grid: { top: 20, right: 20, bottom: 40, left: 58 },
    xAxis: {
      type: 'category', data: dates,
      axisLabel: { rotate: 30, fontSize: 11, color: '#86909c' },
      axisLine: { lineStyle: { color: '#e5e6eb' } },
    },
    yAxis: {
      type: 'value', name: '铅品位 (%)', min: 0,
      nameTextStyle: { color: '#86909c', fontSize: 11 },
      splitLine: { lineStyle: { color: '#f2f3f5', type: 'dashed' } },
      axisLabel: { color: '#86909c', fontSize: 11 },
      axisLine: { lineStyle: { color: '#e5e6eb' } },
    },
    animation: true,
    animationDuration: 1200,
    animationEasing: 'cubicOut',
    series: [
      {
        name: '预测品位', type: 'line', data: preds, smooth: true,
        lineStyle: { color: '#165dff', width: 2.5 },
        itemStyle: { color: '#165dff' },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(22,93,255,0.2)' },
          { offset: 1, color: 'rgba(22,93,255,0.02)' },
        ]) },
        symbol: 'circle', symbolSize: 5,
      },
      {
        name: '真实品位', type: 'line', data: actuals, smooth: true,
        lineStyle: { color: '#00b42a', width: 2, type: 'dashed' },
        itemStyle: { color: '#00b42a' },
        symbol: 'diamond', symbolSize: 5,
      },
      {
        name: '目标线', type: 'line',
        data: Array(dates.length).fill(targetLine),
        lineStyle: { color: '#ff7d00', width: 1.5, type: 'dotted' },
        symbol: 'none',
        lineStep: 'middle',
      },
      {
        name: '警戒区间', type: 'line',
        data: Array(dates.length).fill(upperWarn),
        lineStyle: { color: '#f53f3f', width: 1, type: 'dashed', opacity: 0.5 },
        symbol: 'none',
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(245,63,63,0.06)' },
          { offset: 1, color: 'rgba(245,63,63,0.02)' },
        ]) },
        silent: true,
      },
    ],
    // 警戒下界用 markArea
    markArea: {
      silent: true,
      data: [[
        { yAxis: lowerWarn, itemStyle: { color: 'rgba(245,63,63,0.03)' } },
        { yAxis: 0 },
      ]],
    },
  }, true)
}

function renderDistChart(d) {
  if (!distChartRef.value) return
  if (!distChart) distChart = echarts.init(distChartRef.value)
  const dates = d.daily.map(item => item.date.slice(5))
  const records = d.daily.map(item => item.records)

  distChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: '#e5e6eb',
      textStyle: { color: '#1d2129', fontSize: 12 },
    },
    grid: { top: 20, right: 20, bottom: 35, left: 50 },
    xAxis: {
      type: 'category', data: dates,
      axisLabel: { rotate: 30, fontSize: 11, color: '#86909c' },
      axisLine: { lineStyle: { color: '#e5e6eb' } },
    },
    yAxis: {
      type: 'value', name: '记录数',
      nameTextStyle: { color: '#86909c', fontSize: 11 },
      splitLine: { lineStyle: { color: '#f2f3f5', type: 'dashed' } },
      axisLabel: { color: '#86909c', fontSize: 11 },
      axisLine: { lineStyle: { color: '#e5e6eb' } },
    },
    animation: true,
    animationDuration: 1000,
    animationEasing: 'cubicOut',
    animationDelay: (idx) => idx * 50,
    series: [{
      type: 'bar', data: records,
      itemStyle: {
        borderRadius: [4, 4, 0, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#3c7dff' },
          { offset: 1, color: '#165dff' },
        ]),
      },
      barMaxWidth: 24,
    }],
  }, true)
}

function renderAlarmChart(d) {
  if (!alarmChartRef.value) return
  if (!alarmChart) alarmChart = echarts.init(alarmChartRef.value)
  const ad = d.alarm_trend || []
  const dates = ad.map(item => item.date.slice(5))
  const counts = ad.map(item => item.count)

  alarmChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: '#e5e6eb',
      textStyle: { color: '#1d2129', fontSize: 12 },
    },
    grid: { top: 20, right: 20, bottom: 35, left: 50 },
    xAxis: {
      type: 'category', data: dates,
      axisLabel: { rotate: 30, fontSize: 11, color: '#86909c' },
      axisLine: { lineStyle: { color: '#e5e6eb' } },
    },
    yAxis: {
      type: 'value', name: '告警数',
      nameTextStyle: { color: '#86909c', fontSize: 11 },
      splitLine: { lineStyle: { color: '#f2f3f5', type: 'dashed' } },
      axisLabel: { color: '#86909c', fontSize: 11 },
      axisLine: { lineStyle: { color: '#e5e6eb' } },
    },
    animation: true,
    animationDuration: 1000,
    series: [{
      type: 'line', data: counts, smooth: true,
      lineStyle: { color: '#f53f3f', width: 2 },
      itemStyle: { color: '#f53f3f' },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: 'rgba(245,63,63,0.2)' },
        { offset: 1, color: 'rgba(245,63,63,0.02)' },
      ]) },
      symbol: 'circle', symbolSize: 6,
    }],
  }, true)
}

function renderHistogram(d) {
  if (!histogramRef.value) return
  if (!histogramChart) histogramChart = echarts.init(histogramRef.value)

  // 从每日数据构建品位分布直方图
  const allGrades = []
  d.daily.forEach(day => {
    if (day.avg_pred != null) allGrades.push(day.avg_pred)
    if (day.avg_actual != null) allGrades.push(day.avg_actual)
  })

  if (allGrades.length === 0) return

  // 分桶：0-2, 2-4, 4-6, 6-8, 8-10, 10+
  const buckets = [
    { label: '< 2%', range: [0, 2], count: 0 },
    { label: '2-4%', range: [2, 4], count: 0 },
    { label: '4-6%', range: [4, 6], count: 0 },
    { label: '6-8%', range: [6, 8], count: 0 },
    { label: '8-10%', range: [8, 10], count: 0 },
    { label: '> 10%', range: [10, 100], count: 0 },
  ]
  allGrades.forEach(g => {
    for (const b of buckets) {
      if (g >= b.range[0] && g < b.range[1]) { b.count++; break }
    }
  })

  histogramChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: '{b}: {c} 条记录 ({d}%)',
      backgroundColor: '#fff',
      borderColor: '#e5e6eb',
      textStyle: { color: '#1d2129', fontSize: 12 },
    },
    grid: { top: 20, right: 20, bottom: 30, left: 55 },
    xAxis: {
      type: 'category',
      data: buckets.map(b => b.label),
      axisLabel: { fontSize: 11, color: '#86909c' },
      axisLine: { lineStyle: { color: '#e5e6eb' } },
    },
    yAxis: {
      type: 'value', name: '频次',
      nameTextStyle: { color: '#86909c', fontSize: 11 },
      splitLine: { lineStyle: { color: '#f2f3f5', type: 'dashed' } },
      axisLabel: { color: '#86909c', fontSize: 11 },
      axisLine: { lineStyle: { color: '#e5e6eb' } },
    },
    animation: true,
    animationDuration: 1200,
    animationEasing: 'elasticOut',
    series: [{
      type: 'bar', data: buckets.map(b => b.count),
      itemStyle: {
        borderRadius: [5, 5, 0, 0],
        color: function(params) {
          const colors = ['#86909c', '#165dff', '#00b42a', '#ff7d00', '#f53f3f', '#722ed1']
          return colors[params.dataIndex] || '#165dff'
        },
      },
      barMaxWidth: 40,
      label: {
        show: true, position: 'top', fontSize: 11,
        color: '#86909c', fontWeight: 600,
      },
    }],
  }, true)
}

// ---- 窗口 resize ----
function handleResize() {
  trendChart?.resize()
  distChart?.resize()
  alarmChart?.resize()
  histogramChart?.resize()
}

// ---- 格式化 ----
function pct(val) {
  if (val === null || val === undefined) return '—'
  return Number(val).toFixed(2) + '%'
}

// ---- KPI 数字动画 ----
const kpis = computed(() => {
  if (!report.value?.overview) return []
  const o = report.value.overview
  return [
    { icon: 'Document', label: '分析记录总数', value: o.total_records ?? 0, unit: '条', color: '#165dff', format: 'int' },
    { icon: 'Calendar', label: '日均分析量', value: o.avg_records_per_day ?? 0, unit: '条/天', color: '#00b42a', format: 'int' },
    { icon: 'TrendCharts', label: '平均预测品位', value: o.overall_avg_pred ?? 0, unit: '%', color: '#ff7d00', format: 'float' },
    { icon: 'Select', label: '平均真实品位', value: o.overall_avg_actual ?? 0, unit: '%', color: '#722ed1', format: 'float' },
  ]
})

onMounted(() => {
  loadData()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  trendChart?.dispose()
  distChart?.dispose()
  alarmChart?.dispose()
  histogramChart?.dispose()
})
</script>

<template>
  <div class="reports-page">
    <!-- 顶栏 -->
    <header class="rp-header">
      <div class="header-left">
        <h2>报表</h2>
        <span class="sub">浮选过程数据分析与趋势追踪</span>
      </div>
      <div class="header-right">
        <el-radio-group v-model="dateRange" size="small" @change="handleRangeChange">
          <el-radio-button :value="7">近 7 天</el-radio-button>
          <el-radio-button :value="30">近 30 天</el-radio-button>
          <el-radio-button :value="90">近 90 天</el-radio-button>
        </el-radio-group>
        <el-button size="small" @click="exportReport('csv')">导出 CSV</el-button>
        <el-button size="small" type="primary" @click="exportReport('xlsx')">导出 Excel</el-button>
      </div>
    </header>

    <div v-loading="loading" element-loading-text="数据加载中..." class="rp-body">
      <template v-if="report">
        <!-- 核心指标卡 -->
        <section class="kpi-row">
          <div v-for="(kpi, i) in kpis" :key="i" class="kpi-card" :style="{ '--accent': kpi.color }">
            <div class="kpi-icon"><el-icon><component :is="kpi.icon" /></el-icon></div>
            <div class="kpi-content">
              <div class="kpi-value" :class="{ ready: kpiReady }">
                <span class="kpi-num">
                  {{ kpi.format === 'int'
                    ? (kpiReady ? Number(kpi.value).toLocaleString() : '—')
                    : (kpiReady ? Number(kpi.value).toFixed(2) : '—')
                  }}
                </span>
                <span class="kpi-unit">{{ kpi.unit }}</span>
              </div>
              <div class="kpi-label">{{ kpi.label }}</div>
            </div>
            <div class="kpi-glow"></div>
          </div>
        </section>

        <!-- 预测范围提示 -->
        <div class="range-banner" v-if="report.overview?.prediction_range">
          <span class="range-label">预测范围</span>
          <strong>{{ pct(report.overview.prediction_range.min) }} ~ {{ pct(report.overview.prediction_range.max) }}</strong>
          <span class="range-divider">|</span>
          <span>统计周期：{{ report.period.from }} ~ {{ report.period.to }}</span>
          <span class="range-divider">|</span>
          <span class="range-days">共 {{ dateRange }} 天</span>
        </div>

        <!-- 主趋势图 -->
        <section class="panel main-chart-panel">
          <div class="panel-header">
            <h3>品位趋势（预测 vs 真实）</h3>
            <span class="panel-hint">含目标线与警戒区间</span>
          </div>
          <div ref="trendChartRef" class="chart-body large"></div>
        </section>

        <!-- 下排双图 -->
        <div class="chart-row">
          <section class="panel chart-panel">
            <div class="panel-header">
                <h3>每日分析量</h3>
            </div>
            <div ref="distChartRef" class="chart-body"></div>
          </section>
          <section class="panel chart-panel">
            <div class="panel-header">
                <h3>告警趋势</h3>
            </div>
            <div ref="alarmChartRef" class="chart-body"></div>
          </section>
        </div>

        <!-- 品位分布直方图 + 明细表 -->
        <div class="bottom-row">
          <section class="panel hist-panel">
            <div class="panel-header">
                <h3>品位分布</h3>
              <span class="panel-hint">数据集中度分析</span>
            </div>
            <div ref="histogramRef" class="chart-body"></div>
          </section>
          <section class="panel table-panel">
            <div class="panel-header">
                <h3>每日明细</h3>
              <span class="count-badge">{{ report.daily.length }} 条</span>
            </div>
            <el-table :data="report.daily" stripe size="small" max-height="340" style="width:100%">
              <el-table-column prop="date" label="日期" width="105" fixed />
              <el-table-column prop="records" label="记录数" width="72" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.records > 20 ? 'success' : row.records > 5 ? 'warning' : 'info'" effect="plain">
                    {{ row.records }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="平均预测" width="95" align="right">
                <template #default="{ row }"><span class="pred-val">{{ pct(row.avg_pred) }}</span></template>
              </el-table-column>
              <el-table-column label="平均真实" width="95" align="right">
                <template #default="{ row }"><span class="actual-val">{{ pct(row.avg_actual) }}</span></template>
              </el-table-column>
              <el-table-column label="偏差" width="75" align="right">
                <template #default="{ row }">
                  <span v-if="row.avg_pred != null && row.avg_actual != null"
                    :class="Math.abs(row.avg_pred - row.avg_actual) < 0.5 ? 'dev-good' : 'dev-warn'">
                    {{ (row.avg_pred - row.avg_actual).toFixed(2) }}
                  </span>
                  <span v-else>—</span>
                </template>
              </el-table-column>
              <el-table-column label="范围" min-width="130">
                <template #default="{ row }">{{ pct(row.min_pred) }} ~ {{ pct(row.max_pred) }}</template>
              </el-table-column>
            </el-table>
          </section>
        </div>
      </template>

      <div v-else-if="!loading" class="empty-wrap">
        <el-empty description="暂无报表数据" />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ========== 页面容器 ========== */
.reports-page {
  max-width: 1280px;
  margin: 0 auto;
  padding: 16px 20px 24px;
  min-height: 100vh;
  background: var(--bg-page, #f2f3f5);
  box-sizing: border-box;
}

/* ========== 顶栏 ========== */
.rp-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 18px;
}
.header-left h2 {
  margin: 0; font-size: 20px; font-weight: 700; color: var(--text-primary, #1d2129);
}
.sub { font-size: 12px; color: var(--text-muted, #86909c); margin-top: 2px; }

/* ========== KPI 行 ========== */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 16px;
}
.kpi-card {
  position: relative;
  background: var(--bg-card, #fff);
  border: 1px solid var(--border-color, #e5e6eb);
  border-radius: 8px;
  padding: 16px 20px;
  display: flex;
  align-items: center;
  gap: 14px;
  overflow: hidden;
  transition: transform 0.25s, box-shadow 0.25s;
  box-shadow: var(--shadow-sm);
  border-left: 3px solid var(--accent);
}
.kpi-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}
.kpi-icon { font-size: 28px; flex-shrink: 0; }
.kpi-content { flex: 1; }
.kpi-value {
  font-size: 26px; font-weight: 700; color: var(--accent);
  font-variant-numeric: tabular-nums;
  transition: opacity 0.4s;
  opacity: 0;
}
.kpi-value.ready { opacity: 1; }
.kpi-unit { font-size: 13px; color: var(--text-secondary, #4e5969); margin-left: 4px; font-weight: 400; }
.kpi-label { font-size: 12px; color: var(--text-muted, #86909c); margin-top: 3px; }
.kpi-glow {
  position: absolute; top: -15px; right: -15px;
  width: 70px; height: 70px; border-radius: 50%;
  background: radial-gradient(circle, var(--accent), transparent 70%);
  opacity: 0.08; pointer-events: none;
}

/* ========== 范围横幅 ========== */
.range-banner {
  display: flex; align-items: center; gap: 8px;
  background: var(--bg-card, #fff);
  border: 1px solid var(--border-color, #e5e6eb);
  border-radius: 8px; padding: 10px 18px;
  margin-bottom: 16px; font-size: 13px; color: var(--text-secondary, #4e5969);
}
.range-label { color: var(--text-muted, #86909c); }
.range-divider { color: var(--border-color, #e5e6eb); }
.range-days {
  margin-left: auto;
  padding: 2px 10px; border-radius: 8px;
  background: #e8f3ff; color: #165dff; font-weight: 600; font-size: 12px;
}

/* ========== 面板通用 ========== */
.panel {
  background: var(--bg-card, #fff);
  border: 1px solid var(--border-color, #e5e6eb);
  border-radius: 8px;
  padding: 16px 20px;
  box-shadow: var(--shadow-sm);
}
.panel-header {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 14px;
}
.panel-icon { font-size: 15px; }
.panel-header h3 {
  margin: 0; font-size: 14px; font-weight: 600;
  color: var(--text-primary, #1d2129); flex: 1;
}
.panel-hint { font-size: 11px; color: var(--text-muted, #86909c); }

/* ========== 图表 ========== */
.main-chart-panel { margin-bottom: 14px; }
.chart-body { width: 100%; }
.chart-body.large { height: 340px; }
.chart-body { height: 260px; }

.chart-row {
  display: grid; grid-template-columns: 1fr 1fr; gap: 14px;
  margin-bottom: 14px;
}
.chart-panel { overflow: hidden; }

.bottom-row {
  display: grid; grid-template-columns: 1fr 1.2fr; gap: 14px;
}

/* ========== 表格 ========== */
.count-badge {
  font-size: 11px; padding: 2px 8px; border-radius: 8px;
  background: #f2f3f5; color: var(--text-muted, #86909c); font-weight: 600;
}
.table-panel { overflow: hidden; }
.pred-val { color: #165dff; font-weight: 600; font-family: Consolas, monospace; }
.actual-val { color: #00b42a; font-weight: 600; font-family: Consolas, monospace; }
.dev-good { color: #00b42a; font-weight: 600; font-family: Consolas, monospace; }
.dev-warn { color: #ff7d00; font-weight: 600; font-family: Consolas, monospace; }

/* ========== 空状态 ========== */
.empty-wrap { text-align: center; padding: 80px 0; }

/* ========== 响应式 ========== */
@media (max-width: 1100px) {
  .kpi-row { grid-template-columns: repeat(2, 1fr); }
  .chart-row { grid-template-columns: 1fr; }
  .bottom-row { grid-template-columns: 1fr; }
}
@media (max-width: 700px) {
  .kpi-row { grid-template-columns: 1fr; }
  .rp-header { flex-direction: column; align-items: flex-start; gap: 10px; }
}
</style>
