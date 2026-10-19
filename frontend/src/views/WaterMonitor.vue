<template>
  <PageShell
    v-loading="loading"
    title="水质监测"
    sub="回水可回用性 · 5 点位在线监测"
  >
    <!-- 四格 KPI：原为 5 格自定义统计带；
         回水总流量并入"监测点位"副行（数字不丢），其余四格统一 StatCard。 -->
    <template #kpi>
      <StatCard
        label="监测点位"
        :value="overview.station_count ?? '—'"
        unit="个"
        :foot="`回水总流量 ${overview.total_flow_m3h ?? '—'} m³/h`"
      />
      <StatCard
        label="水质异常点位"
        :value="overview.abnormal_count ?? '—'"
        unit="个"
        :tone="(overview.abnormal_count || 0) > 0 ? 'warning' : 'default'"
        :foot="(overview.abnormal_count || 0) > 0 ? '存在异常，需处置' : '全部正常'"
      />
      <StatCard
        label="平均回水可回用评分"
        :value="overview.avg_reuse_score ?? '—'"
        unit="分"
        tone="primary"
      />
      <StatCard
        label="近 7 天异常事件"
        :value="overview.events_7d ?? '—'"
        unit="起"
      />
    </template>

    <!-- ====== 5 点位状态卡 ====== -->
    <section class="station-grid">
      <div
        v-for="s in stations"
        :key="s.id"
        class="station-card"
        :class="[s.status, { active: s.id === selectedId }]"
        @click="selectStation(s.id)"
      >
        <div class="sc-head">
          <span class="status-dot" :class="s.status"></span>
          <span class="sc-name">{{ s.name }}</span>
        </div>
        <div class="sc-zone">{{ s.zone }}</div>
        <div class="sc-metrics">
          <div v-for="m in CARD_METRICS" :key="m" class="sc-metric">
            <span class="sm-label">{{ metaOf(s, m).label }}</span>
            <span class="sm-value" :class="metaOf(s, m).level">
              {{ fmt(metaOf(s, m).value, m) }}
            </span>
            <span class="sm-unit">{{ metaOf(s, m).unit }}</span>
          </div>
        </div>
        <div class="sc-foot">
          <div class="sc-score-row">
            <span class="sc-score-label">回用评分</span>
            <span class="sc-score" :class="scoreLevel(s.reuse?.score)">{{ s.reuse?.score ?? '—' }}</span>
          </div>
          <div class="score-bar"><i :style="{ width: (s.reuse?.score || 0) + '%' }" :class="scoreLevel(s.reuse?.score)"></i></div>
        </div>
      </div>
    </section>

    <!-- ====== 趋势 + 回用评分 ====== -->
    <section class="dual-row">
      <div class="panel">
        <div class="panel-head">
          <h3>{{ currentStation?.name || '—' }} · 参数趋势</h3>
          <div class="panel-tools">
            <el-select v-model="metric" size="small" style="width: 132px" @change="loadHistory">
              <el-option v-for="m in METRIC_OPTIONS" :key="m.key" :label="m.label" :value="m.key" />
            </el-select>
            <el-radio-group v-model="hours" size="small" @change="loadHistory">
              <el-radio-button :value="1">1h</el-radio-button>
              <el-radio-button :value="6">6h</el-radio-button>
              <el-radio-button :value="24">24h</el-radio-button>
              <el-radio-button :value="168">7d</el-radio-button>
            </el-radio-group>
          </div>
        </div>
        <div v-if="history.points === 0" class="chart-empty">该时间范围内暂无数据，可切换到 7d 查看历史</div>
        <div v-else ref="trendRef" class="chart chart-md"></div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>回水可回用评分</h3>
          <span class="panel-hint">{{ reuse?.station_name || '' }}</span>
        </div>
        <div v-if="reuse" class="reuse-body">
          <div class="reuse-top">
            <div class="reuse-score" :class="reuse.level">{{ reuse.score }}</div>
            <div class="reuse-meta">
              <div class="reuse-level">{{ reuse.level_cn }}</div>
              <div class="reuse-sub">建议回用比例 <b>{{ reuse.suggested_reuse_ratio }}%</b></div>
            </div>
          </div>

          <div class="factor-list">
            <div v-for="f in reuse.factors" :key="f.key" class="factor-row">
              <span class="fr-label">{{ f.label }}</span>
              <div class="fr-bar">
                <i :style="{ width: f.score + '%' }" :class="scoreLevel(f.score)"></i>
              </div>
              <span class="fr-score">{{ f.score }}</span>
              <span class="fr-weight">权重 {{ (f.weight * 100).toFixed(0) }}%</span>
            </div>
          </div>

          <div class="reuse-advice">{{ reuse.advice }}</div>

          <div class="trend-7d">
            <div class="t7-title">近 7 天评分</div>
            <div class="t7-bars">
              <div v-for="t in reuse.trend_7d" :key="t.date" class="t7-item">
                <div class="t7-bar-wrap">
                  <i :style="{ height: Math.max(4, t.score) + '%' }" :class="scoreLevel(t.score)"></i>
                </div>
                <span class="t7-date">{{ t.date.slice(5) }}</span>
                <span class="t7-val">{{ t.score }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ====== 水质→品位联动 + 异常事件 ====== -->
    <section class="dual-row">
      <div class="panel">
        <div class="panel-head">
          <h3>水质 → 品位联动分析</h3>
          <span class="panel-hint">回水指标 × 浮选铅品位 · 滞后互相关</span>
        </div>
        <div v-if="corr && corr.available" class="corr-body">
          <table class="corr-table">
            <thead>
              <tr><th>回水指标</th><th>相关系数 r</th><th>最优滞后</th><th>样本</th><th>强度</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in corr.results" :key="r.water_metric">
                <td>{{ r.label }}</td>
                <td class="num" :class="r.best?.r < 0 ? 'neg' : 'pos'">{{ r.best?.r?.toFixed(3) ?? '—' }}</td>
                <td class="num">{{ r.best?.lag_days ?? '—' }} 天</td>
                <td class="num">{{ r.best?.n ?? '—' }}</td>
                <td>{{ strengthLabel(r.best?.r) }}</td>
              </tr>
            </tbody>
          </table>
          <div ref="scatterRef" class="chart chart-sm"></div>
          <div class="corr-conclusion">{{ corr.conclusion }}</div>
        </div>
        <div v-else class="chart-empty">{{ corr?.reason || '加载中…' }}</div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>异常事件</h3>
          <span class="panel-hint">共 {{ events.length }} 起</span>
        </div>
        <div class="event-list">
          <div v-for="e in events" :key="e.id" class="event-item" :class="e.level">
            <div class="ev-head">
              <span class="ev-level" :class="e.level">{{ e.level_cn }}</span>
              <span class="ev-title">{{ e.title }}</span>
              <span class="ev-time">{{ (e.created_at || '').slice(5, 16).replace('T', ' ') }}</span>
            </div>
            <div class="ev-metric">
              {{ e.metric_label }}：{{ e.baseline_value }} → <b>{{ e.peak_value }}</b> {{ e.unit }}
            </div>
            <div class="ev-detail">{{ e.detail }}</div>
          </div>
          <div v-if="events.length === 0" class="chart-empty">暂无异常事件</div>
        </div>
      </div>
    </section>

    <!-- ====== 合规统计 ====== -->
    <section class="panel">
      <div class="panel-head">
        <h3>排放合规统计</h3>
        <div class="panel-tools">
          <span class="panel-hint">{{ compliance?.standard }} · 超标行优先</span>
          <el-button size="small" @click="exportReport('csv')">导出 CSV</el-button>
          <el-button size="small" type="primary" @click="exportReport('xlsx')">导出 Excel</el-button>
        </div>
      </div>
      <div v-if="compliance" class="compliance-summary">
        <span>统计记录 <b>{{ compliance.summary.record_count }}</b> 条</span>
        <span>超标记录 <b class="danger">{{ compliance.summary.exceed_records }}</b> 条</span>
        <span>累计超标时长 <b class="danger">{{ compliance.summary.exceed_hours }}</b> 小时</span>
        <span v-for="p in compliance.summary.params" :key="p.param_label" class="cparam">
          {{ p.param_label }} 超标 {{ p.exceed_count }} 次 / {{ p.exceed_hours }}h
        </span>
      </div>
      <el-table :data="complianceTable" size="small" max-height="360" stripe>
        <el-table-column prop="date" label="日期" width="110" />
        <el-table-column prop="station_name" label="点位" min-width="150" />
        <el-table-column prop="param_label" label="参数" width="110" />
        <el-table-column label="日均值" width="90">
          <template #default="{ row }">{{ row.avg_value }}</template>
        </el-table-column>
        <el-table-column label="最大值" width="90">
          <template #default="{ row }">{{ row.max_value }}</template>
        </el-table-column>
        <el-table-column label="限值" width="90">
          <template #default="{ row }">{{ row.limit_value }}</template>
        </el-table-column>
        <el-table-column label="超标次数" width="100">
          <template #default="{ row }">
            <span :class="{ danger: row.exceed_count > 0 }">{{ row.exceed_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="超标时长" width="100">
          <template #default="{ row }">
            <span :class="{ danger: row.exceed_hours > 0 }">{{ row.exceed_hours }} h</span>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- ====== 药剂联动建议 ====== -->
    <section class="panel" v-if="dosing">
      <div class="panel-head">
        <h3>药剂联动建议</h3>
        <span class="panel-hint">由回水残留药剂反推 → 新药剂投加调整</span>
      </div>
      <div class="dosing-body">
        <div class="dosing-metrics">
          <div class="dm-cell">
            <div class="dm-val">{{ dosing.residual_reagent }}<span>mg/L</span></div>
            <div class="dm-label">残留药剂估算</div>
          </div>
          <div class="dm-cell">
            <div class="dm-val">{{ dosing.estimated_collector_mgl }}<span>mg/L</span></div>
            <div class="dm-label">残留捕收剂</div>
          </div>
          <div class="dm-cell">
            <div class="dm-val">{{ dosing.estimated_frother_mgl }}<span>mg/L</span></div>
            <div class="dm-label">残留起泡剂</div>
          </div>
          <div class="dm-cell">
            <div class="dm-val accent">-{{ dosing.suggested_saving_pct }}<span>%</span></div>
            <div class="dm-label">建议新药剂下调</div>
          </div>
        </div>
        <div class="dosing-note">{{ dosing.note }}</div>
        <div class="dosing-foot">
          <span v-if="dosing.dosing_reference">
            对照最近配药记录：{{ dosing.dosing_reference.ore_type }} · 目标铅品位 {{ dosing.dosing_reference.target_pb_grade }}% · pH {{ dosing.dosing_reference.current_ph }}
          </span>
          <span class="caveat">{{ dosing.caveat }}</span>
        </div>
      </div>
    </section>
  </PageShell>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import api from '../api'
import { useWebSocket } from '../api/ws'
import PageShell from '../components/PageShell.vue'
import StatCard from '../components/StatCard.vue'

const CARD_METRICS = ['ss', 'turbidity', 'cod', 'cu']

const METRIC_OPTIONS = [
  { key: 'ss', label: '悬浮物 SS' },
  { key: 'cod', label: 'COD' },
  { key: 'turbidity', label: '浊度' },
  { key: 'ph', label: 'pH' },
  { key: 'pb', label: '总铅' },
  { key: 'zn', label: '总锌' },
  { key: 'cu', label: '总铜' },
  { key: 'flow', label: '流量' },
]

const loading = ref(true)
const overview = ref({})
const stations = ref([])
const selectedId = ref('W5')
const metric = ref('ss')
// 默认时间范围：取 7d 而不是 6h。
// ⚠️ 演示库里水质数据截止 10-13，6h/24h 窗口内恒为空 → 左上大图会显示
//    "该时间范围内暂无数据"，占屏 1/3 且极像"系统坏了"。
//    水质本身是慢变量，默认看 7 天趋势也更符合业务直觉。
const hours = ref(168)
const history = ref({ series: [], thresholds: {}, points: 0, metrics: [] })
const reuse = ref(null)
const corr = ref(null)
const events = ref([])
const compliance = ref(null)
const complianceTable = ref([])
const dosing = ref(null)

const trendRef = ref(null)
const scatterRef = ref(null)
let trendChart = null
let scatterChart = null

const currentStation = computed(() => stations.value.find((s) => s.id === selectedId.value) || null)

function metaOf(station, m) {
  return station?.latest?.params?.[m] || { label: m, value: null, unit: '', level: 'normal' }
}

function fmt(v, m) {
  if (v === null || v === undefined) return '—'
  if (m === 'ph') return v.toFixed(2)
  if (m === 'flow') return v.toFixed(0)
  if (m === 'cu' || m === 'pb' || m === 'zn') return v.toFixed(2)
  return v.toFixed(1)
}

function scoreLevel(score) {
  if (score === null || score === undefined) return 'na'
  if (score >= 85) return 'good'
  if (score >= 70) return 'fair'
  if (score >= 50) return 'poor'
  return 'bad'
}

function strengthLabel(r) {
  if (r === null || r === undefined) return '—'
  const a = Math.abs(r)
  const s = a >= 0.7 ? '强' : a >= 0.4 ? '中等' : '弱'
  return `${s}${r < 0 ? '负' : '正'}相关`
}

async function loadOverview() {
  try {
    overview.value = await api.get('/water/overview')
  } catch (e) {
    console.error('加载水质概览失败:', e)
  }
}

async function loadStations() {
  try {
    const res = await api.get('/water/stations')
    stations.value = res.stations || []
  } catch (e) {
    console.error('加载点位失败:', e)
  }
}

async function loadHistory() {
  try {
    const res = await api.get(`/water/stations/${selectedId.value}/history`, {
      params: { hours: hours.value, metric: metric.value },
    })
    history.value = res
    await nextTick()
    renderTrend()
  } catch (e) {
    console.error('加载趋势失败:', e)
  }
}

async function loadReuse() {
  try {
    reuse.value = await api.get('/water/reuse-index', { params: { station_id: selectedId.value } })
  } catch (e) {
    console.error('加载回用评分失败:', e)
  }
}

async function loadCorrelation() {
  try {
    corr.value = await api.get('/water/correlation', { params: { days: 30 } })
    await nextTick()
    renderScatter()
  } catch (e) {
    console.error('加载联动分析失败:', e)
  }
}

async function loadEvents() {
  try {
    const res = await api.get('/water/events', { params: { hours: 720, limit: 20 } })
    events.value = res.items || []
  } catch (e) {
    console.error('加载事件失败:', e)
  }
}

async function loadCompliance() {
  try {
    const res = await api.get('/water/compliance', { params: { days: 7 } })
    compliance.value = res
    // 后端已按"超标优先"排序，取前 60 行防止 DOM 过重
    complianceTable.value = (res.items || []).slice(0, 60)
  } catch (e) {
    console.error('加载合规数据失败:', e)
  }
}

async function loadDosing() {
  try {
    dosing.value = await api.get('/water/dosing-advice')
  } catch (e) {
    console.error('加载药剂建议失败:', e)
  }
}

function selectStation(id) {
  if (selectedId.value === id) return
  selectedId.value = id
  loadHistory()
  loadReuse()
}

// ---- 图表 ----
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

function renderTrend() {
  if (!trendRef.value || !history.value.series?.length) return
  if (!trendChart) trendChart = echarts.init(trendRef.value)
  const s = history.value.series
  const th = history.value.thresholds?.[metric.value] || {}
  const times = s.map((p) => (p.ts || '').slice(5, 16).replace('T', ' '))
  const values = s.map((p) => p[metric.value])

  const markLines = []
  if (th.warn != null) {
    markLines.push({ yAxis: th.warn, label: { formatter: `预警 ${th.warn}`, position: 'insideEndTop', fontSize: 11 }, lineStyle: { color: cssVar('--color-warning', 'var(--color-warning)'), type: 'dashed', width: 1 } })
  }
  if (th.danger != null) {
    markLines.push({ yAxis: th.danger, label: { formatter: `危险 ${th.danger}`, position: 'insideEndTop', fontSize: 11 }, lineStyle: { color: cssVar('--color-danger', 'var(--color-danger)'), type: 'dashed', width: 1 } })
  }

  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { top: 26, right: 46, bottom: 34, left: 54 },
    xAxis: {
      type: 'category', data: times, boundaryGap: false,
      axisLabel: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)'), hideOverlap: true },
      axisLine: { lineStyle: { color: cssVar('--border-1', 'var(--border-1)') } },
    },
    yAxis: {
      type: 'value', scale: true,
      name: th.unit || '',
      nameTextStyle: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') },
      axisLabel: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') },
      splitLine: { lineStyle: { color: cssVar('--border-2', 'var(--bg-3)') } },
    },
    series: [{
      name: th.label || metric.value,
      type: 'line', smooth: true, showSymbol: false, data: values,
      lineStyle: { width: 2, color: cssVar('--chart-1', 'var(--color-primary)') },
      itemStyle: { color: cssVar('--chart-1', 'var(--color-primary)') },
      areaStyle: { color: cssVar('--chart-1-bg', '#e8f3ff'), opacity: 0.6 },
      markLine: markLines.length ? { silent: true, symbol: 'none', data: markLines } : undefined,
    }],
  }, true)
}

function renderScatter() {
  if (!scatterRef.value || !corr.value?.scatter?.length) return
  if (!scatterChart) scatterChart = echarts.init(scatterRef.value)
  const pts = corr.value.scatter.map((p) => [p.x, p.y])
  const bm = corr.value.results?.find((r) => r.water_metric === corr.value.best_metric)

  scatterChart.setOption({
    tooltip: {
      trigger: 'item',
      formatter: (p) => `${bm?.label || ''} ${p.value[0]}<br/>铅品位 ${p.value[1].toFixed(2)}%`,
    },
    grid: { top: 16, right: 20, bottom: 34, left: 54 },
    xAxis: {
      type: 'value', scale: true, name: `${bm?.label || ''}${bm?.unit ? ' (' + bm.unit + ')' : ''}`,
      nameTextStyle: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') },
      axisLabel: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') },
      splitLine: { lineStyle: { color: cssVar('--border-2', 'var(--bg-3)') } },
    },
    yAxis: {
      type: 'value', scale: true, name: '铅品位 %',
      nameTextStyle: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') },
      axisLabel: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') },
      splitLine: { lineStyle: { color: cssVar('--border-2', 'var(--bg-3)') } },
    },
    series: [{
      type: 'scatter', data: pts, symbolSize: 9,
      itemStyle: { color: cssVar('--chart-1', 'var(--color-primary)'), opacity: 0.75 },
    }],
  }, true)
}

function resizeCharts() {
  trendChart?.resize()
  scatterChart?.resize()
}

// ---- 导出 ----
function exportReport(format) {
  const url = `/api/water/compliance/export?format=${format}&days=7`
  const a = document.createElement('a')
  a.href = url
  a.download = `water_compliance.${format}`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  ElMessage.success(`合规报表已开始下载（${format.toUpperCase()}）`)
}

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([
      loadOverview(), loadStations(), loadHistory(), loadReuse(),
      loadCorrelation(), loadEvents(), loadCompliance(), loadDosing(),
    ])
  } finally {
    loading.value = false
  }
}

// ---- 实时推送：WS 更新点位卡状态 ----
// ⚠️ useWebSocket 内部会注册 onUnmounted → **必须在 setup 的同步阶段调用**。
//    原先它写在 onMounted 的 `await loadAll()` 之后 → 注册时没有活跃组件实例，
//    Vue 警告且 handler 永不清理（已卸载的页面仍被推送回调，泄漏）。
useWebSocket('water', (msg) => {
  if (msg.type !== 'snapshot') return
  for (const s of msg.stations || []) {
    const local = stations.value.find((x) => x.id === s.id)
    if (local) {
      local.status = s.status
      if (local.reuse) local.reuse.score = s.reuse_score
    }
  }
})

let refreshTimer = null
onMounted(async () => {
  await loadAll()
  window.addEventListener('resize', resizeCharts)
  // 兜底轮询（WS 不可用时也能刷新）
  refreshTimer = setInterval(() => {
    loadOverview()
    loadStations()
  }, 30000)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeCharts)
  if (refreshTimer) clearInterval(refreshTimer)
  trendChart?.dispose()
  scatterChart?.dispose()
  trendChart = null
  scatterChart = null
})
</script>

<style scoped>
.water-page {
  padding: var(--space-4) var(--space-5) var(--space-8);
  background: var(--bg-2);
  min-height: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
/* 页头：只占一行，与 Dashboard 的 h1 同字号，不额外抢 KPI 带的注意力 */
.water-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.water-head h1 {
  margin: 0;
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  line-height: 1.2;
}
.water-sub {
  font-size: var(--text-xs);
  color: var(--text-3);
  padding-left: 12px;
  border-left: 1px solid var(--border-1);
}

/* ====== KPI 统计带 ====== */
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-radius: var(--radius);
}
.kpi-cell {
  padding: 14px 18px;
  border-right: 1px solid var(--border-1);
}
.kpi-cell:last-child { border-right: none; }
.kpi-num {
  font-size: var(--text-2xl);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.kpi-num.warn { color: var(--color-warning); }
.kpi-unit {
  font-size: var(--text-xs);
  font-weight: var(--weight-normal);
  color: var(--text-3);
  margin-left: 3px;
}
.kpi-label {
  margin-top: 4px;
  font-size: var(--text-xs);
  color: var(--text-3);
}

/* ====== 点位卡 ====== */
.station-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: var(--space-3);
}
.station-card {
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-radius: var(--radius);
  padding: 12px 14px;
  cursor: pointer;
  transition: border-color var(--transition), background var(--transition);
  min-width: 0;
}
.station-card:hover { border-color: var(--color-primary); }
.station-card.active {
  border-color: var(--color-primary);
  background: var(--color-primary-bg);
}
.station-card.danger { border-left: 3px solid var(--color-danger); }
.station-card.warning { border-left: 3px solid var(--color-warning); }
.station-card.normal { border-left: 3px solid var(--color-success); }

.sc-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.status-dot {
  width: 7px; height: 7px; border-radius: 50%;
  flex-shrink: 0;
  background: var(--color-success);
}
.status-dot.warning { background: var(--color-warning); }
.status-dot.danger { background: var(--color-danger); }
.status-dot.offline { background: var(--text-4); }
.sc-name {
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  color: var(--text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sc-zone {
  font-size: var(--text-xs);
  color: var(--text-3);
  margin: 2px 0 10px;
}
.sc-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-2);
}
.sc-metric {
  display: flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
  font-size: var(--text-xs);
}
.sm-label { color: var(--text-3); white-space: nowrap; }
.sm-value {
  font-variant-numeric: tabular-nums;
  font-weight: var(--weight-medium);
  color: var(--text-1);
}
.sm-value.warning { color: var(--color-warning); }
.sm-value.danger { color: var(--color-danger); }
.sm-unit { color: var(--text-4); font-size: 11px; }

.sc-foot { padding-top: 10px; }
.sc-score-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 5px;
}
.sc-score-label { font-size: var(--text-xs); color: var(--text-3); }
.sc-score {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}
.score-bar {
  height: 4px;
  border-radius: 2px;
  background: var(--bg-3);
  overflow: hidden;
}
.score-bar i {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: var(--text-3);
  transition: width var(--transition);
}
.score-bar i.good, .score-bar i.fair { background: var(--color-success); }
.score-bar i.poor { background: var(--color-warning); }
.score-bar i.bad, .score-bar i.na { background: var(--color-danger); }
.sc-score.good, .sc-score.fair { color: var(--color-success); }
.sc-score.poor { color: var(--color-warning); }
.sc-score.bad { color: var(--color-danger); }

/* ====== 面板 ====== */
.dual-row {
  display: grid;
  grid-template-columns: 1.15fr 1fr;
  gap: var(--space-3);
}
.panel {
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-radius: var(--panel-radius);
  padding: var(--panel-pad-y) var(--panel-pad-x);
  min-width: 0;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.panel-head h3 {
  margin: 0;
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  color: var(--text-1);
  white-space: nowrap;
}
.panel-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.panel-hint {
  font-size: var(--text-xs);
  color: var(--text-3);
}
.chart { width: 100%; }
.chart-md { height: 300px; }
.chart-sm { height: 200px; margin-top: 12px; }
.chart-empty {
  text-align: center;
  padding: 40px 0;
  color: var(--text-3);
  font-size: var(--text-sm);
}

/* ====== 回用评分卡 ====== */
.reuse-top {
  display: flex;
  align-items: center;
  gap: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border-2);
}
.reuse-score {
  font-size: 40px;
  font-weight: var(--weight-semibold);
  line-height: 1;
  font-variant-numeric: tabular-nums;
  color: var(--text-1);
}
.reuse-score.good, .reuse-score.fair { color: var(--color-success); }
.reuse-score.poor { color: var(--color-warning); }
.reuse-score.bad, .reuse-score.critical { color: var(--color-danger); }
.reuse-level {
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  color: var(--text-1);
}
.reuse-sub {
  font-size: var(--text-xs);
  color: var(--text-3);
  margin-top: 4px;
}
.reuse-sub b { color: var(--color-primary); font-weight: var(--weight-semibold); }

.factor-list { margin: 14px 0; display: flex; flex-direction: column; gap: 9px; }
.factor-row {
  display: grid;
  grid-template-columns: 68px 1fr 34px 58px;
  align-items: center;
  gap: 8px;
  font-size: var(--text-xs);
}
.fr-label { color: var(--text-2); white-space: nowrap; }
.fr-bar {
  height: 6px;
  background: var(--bg-3);
  border-radius: 3px;
  overflow: hidden;
}
.fr-bar i {
  display: block; height: 100%; border-radius: 3px;
  background: var(--text-3);
}
.fr-bar i.good, .fr-bar i.fair { background: var(--color-success); }
.fr-bar i.poor { background: var(--color-warning); }
.fr-bar i.bad, .fr-bar i.critical { background: var(--color-danger); }
.fr-score {
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--text-1);
  font-weight: var(--weight-medium);
}
.fr-weight { color: var(--text-4); white-space: nowrap; }

.reuse-advice {
  font-size: var(--text-sm);
  color: var(--text-2);
  background: var(--bg-2);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  line-height: var(--leading-normal);
}
.trend-7d { margin-top: 14px; }
.t7-title { font-size: var(--text-xs); color: var(--text-3); margin-bottom: 8px; }
.t7-bars { display: flex; gap: 6px; align-items: flex-end; }
.t7-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.t7-bar-wrap {
  width: 100%;
  height: 48px;
  display: flex;
  align-items: flex-end;
}
.t7-bar-wrap i {
  display: block;
  width: 100%;
  border-radius: 2px 2px 0 0;
  background: var(--text-3);
  transition: height var(--transition);
}
.t7-bar-wrap i.good, .t7-bar-wrap i.fair { background: var(--color-success); }
.t7-bar-wrap i.poor { background: var(--color-warning); }
.t7-bar-wrap i.bad { background: var(--color-danger); }
.t7-date { font-size: 10px; color: var(--text-4); }
.t7-val { font-size: 11px; color: var(--text-2); font-variant-numeric: tabular-nums; }

/* ====== 联动分析 ====== */
.corr-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-xs);
}
.corr-table th {
  text-align: left;
  font-weight: var(--weight-medium);
  color: var(--text-3);
  padding: 6px 8px;
  border-bottom: 1px solid var(--border-1);
}
.corr-table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--border-2);
  color: var(--text-2);
}
.corr-table td.num { font-variant-numeric: tabular-nums; }
.corr-table td.neg { color: var(--color-danger); }
.corr-table td.pos { color: var(--color-success); }
.corr-conclusion {
  margin-top: 10px;
  font-size: var(--text-xs);
  color: var(--text-2);
  line-height: var(--leading-relaxed);
  background: var(--bg-2);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
}

/* ====== 事件列表 ====== */
.event-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 420px;
  overflow-y: auto;
}
.event-item {
  border: 1px solid var(--border-1);
  border-left: 3px solid var(--text-3);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
}
.event-item.danger { border-left-color: var(--color-danger); }
.event-item.warning { border-left-color: var(--color-warning); }
.ev-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
}
.ev-level {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--bg-3);
  color: var(--text-2);
  flex-shrink: 0;
}
.ev-level.danger { background: var(--color-danger-light); color: var(--color-danger); }
.ev-level.warning { background: var(--color-warning-light); color: var(--color-warning); }
.ev-title {
  flex: 1;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--text-1);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ev-time { font-size: var(--text-xs); color: var(--text-3); flex-shrink: 0; }
.ev-metric {
  font-size: var(--text-xs);
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
  margin-bottom: 4px;
}
.ev-metric b { color: var(--color-danger); }
.ev-detail {
  font-size: var(--text-xs);
  color: var(--text-3);
  line-height: var(--leading-normal);
}

/* ====== 合规 ====== */
.compliance-summary {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  font-size: var(--text-xs);
  color: var(--text-2);
  padding: 10px 12px;
  background: var(--bg-2);
  border-radius: var(--radius-sm);
  margin-bottom: 12px;
}
.compliance-summary b { color: var(--text-1); font-variant-numeric: tabular-nums; }
.compliance-summary b.danger, .danger { color: var(--color-danger); }
.cparam {
  color: var(--text-3);
  padding-left: 12px;
  border-left: 1px solid var(--border-1);
}

/* ====== 药剂建议 ====== */
.dosing-metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-3);
  margin-bottom: 12px;
}
.dm-cell {
  background: var(--bg-2);
  border-radius: var(--radius-sm);
  padding: 12px 14px;
}
.dm-val {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
}
.dm-val span {
  font-size: var(--text-xs);
  font-weight: var(--weight-normal);
  color: var(--text-3);
  margin-left: 3px;
}
.dm-val.accent { color: var(--color-primary); }
.dm-label { font-size: var(--text-xs); color: var(--text-3); margin-top: 4px; }
.dosing-note {
  font-size: var(--text-sm);
  color: var(--text-2);
  background: var(--bg-2);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  line-height: var(--leading-normal);
}
.dosing-foot {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--text-xs);
  color: var(--text-3);
}
.caveat { color: var(--text-4); }

@media (max-width: 1400px) {
  .station-grid { grid-template-columns: repeat(3, 1fr); }
  .dual-row { grid-template-columns: 1fr; }
}
@media (max-width: 900px) {
  .kpi-strip { grid-template-columns: repeat(2, 1fr); }
  .station-grid { grid-template-columns: 1fr; }
  .dosing-metrics { grid-template-columns: repeat(2, 1fr); }
}
</style>
