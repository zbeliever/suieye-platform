<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElDialog, ElMessage } from 'element-plus'
// ⚠️ 模板里用 <component :is="Xxx" /> **动态渲染**的图标必须全部在这里导入 ——
//    漏一个就是该处渲染空白（console 只给 "Property `Xxx` was accessed during render but is not defined"）。
//    Coin 只在「磨矿」场景的摘要卡里出现，flotation 场景跑巡检抓不到它，别以为没问题。
import {
  ArrowUp, ArrowDown, WarningFilled, TrendCharts, CircleCheck, Coin, InfoFilled,
} from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import api from '../api'
import PageShell from '../components/PageShell.vue'
import StatCard from '../components/StatCard.vue'

const route = useRoute()
const router = useRouter()

// ---- 数据 ----
const loading = ref(false)
const records = ref([])
const total = ref(0)
const scenes = ref([])

// ---- 数据新鲜度提示（演示动线：先分析 → 再看时光机）----
// ★ 不造假数据：最近一条记录不是今天时，引导用户先去跑一次「分析当前画面」——
//   跑完回到这里，第一张卡就是刚才那次分析（真实链路，不是补数据）。
const staleTip = computed(() => {
  const first = records.value[0]
  if (!first?.created_at) return ''
  const d = new Date(String(first.created_at).replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return ''
  if (d.toDateString() === new Date().toDateString()) return ''   // 今天已有数据 → 不打扰
  const days = Math.floor((Date.now() - d.getTime()) / 86400000)
  const label = `${d.getMonth() + 1}/${d.getDate()}`
  return `最近一次分析在 ${label}（${days} 天前）。想在这里看到当前数据，先到工作台跑一次「分析当前画面」即可。`
})
const goAnalyze = () => router.push(`/workbench/${query.value.scene || 'flotation'}`)

// 筛选
const query = ref({
  scene: 'flotation',
  date: '',
  limit: 24,
  offset: 0,
})

// 统计摘要（从记录中计算，场景自适应）
const summary = computed(() => {
  if (!records.value.length) return null

  if (isGrinding.value) {
    // 磨矿场景：基于 P80 粒度
    const preds = records.value
      .map(r => { const v = r.prediction?.p80; return v != null ? Number(v) : null })
      .filter(v => v != null)
    if (!preds.length) return null
    preds.sort((a, b) => a - b)
    const n = preds.length
    const condCounts = { '过磨': 0, '正常': 0, '欠磨': 0 }
    for (const r of records.value) {
      const c = r.prediction?.condition || '未知'
      if (c in condCounts) condCounts[c]++
    }
    return {
      count: n,
      avg: (preds.reduce((s, v) => s + v, 0) / n).toFixed(0),
      min: preds[0].toFixed(0),
      max: preds[n - 1].toFixed(0),
      median: preds[Math.floor(n / 2)].toFixed(0),
      unit: 'μm',
      lowCount: condCounts['过磨'],     // 过磨（偏细）
      normalCount: condCounts['正常'],
      highCount: condCounts['欠磨'],     // 欠磨（偏粗）
    }
  }

  // 浮选场景：基于 Pb 品位
  const preds = records.value
    .map(r => {
      const v = r.prediction?.pb_grade_xrf ?? r.prediction?.pb_grade
      return v != null ? Number(v) : null
    })
    .filter(v => v != null)
  if (!preds.length) return null
  preds.sort((a, b) => a - b)
  const n = preds.length
  return {
    count: n,
    avg: (preds.reduce((s, v) => s + v, 0) / n).toFixed(2),
    min: preds[0].toFixed(2),
    max: preds[n - 1].toFixed(2),
    median: preds[Math.floor(n / 2)].toFixed(2),
    unit: '%',
    lowCount: preds.filter(v => v < 4.0).length,
    normalCount: preds.filter(v => v >= 4.0 && v <= 7.0).length,
    highCount: preds.filter(v => v > 7.0).length,
  }
})

// 详情弹窗
const dialogVisible = ref(false)
const currentRecord = ref(null)
const compareRecord = ref(null)  // 对比记录

// 迷你趋势图
const trendChartRef = ref(null)
let trendChart = null

// ---- 加载场景列表 ----
async function loadScenes() {
  try {
    const res = await api.get('/scenes')
    // 工况时光机建立在统一分析台账（analysis_records）之上，
    // water / tailings / equipment 的数据不在台账里，不能作为历史场景查询
    scenes.value = (res.scenes || []).filter(s => s.status === 'active' && s.has_analysis_records)
  } catch (e) {
    console.error(e)
  }
}

// ---- 加载记录 ----
async function loadRecords() {
  loading.value = true
  try {
    const params = {
      scene_name: query.value.scene,
      limit: query.value.limit,
      offset: query.value.offset,
    }
    if (query.value.date) {
      params.group_id = query.value.date.replace(/-/g, '')
    }

    const res = await api.get(`/scenes/${query.value.scene}/records`, { params })
    records.value = res.records || []
    total.value = res.total || 0

    await nextTick()
    renderTrendChart()
  } catch (e) {
    console.error('加载记录失败:', e)
  } finally {
    loading.value = false
  }
}

function handleSceneChange() {
  query.value.offset = 0
  loadRecords()
}

function handleDateChange() {
  query.value.offset = 0
  loadRecords()
}

// ---- 查看详情 ----
async function showDetail(record) {
  currentRecord.value = record
  compareRecord.value = null
  dialogVisible.value = true
}

// ---- 找一条相似记录做对比（场景自适应）----
function findSimilar(record) {
  if (!records.value || records.value.length < 2) return null
  if (isGrinding.value) {
    const targetP80 = Number(record.prediction?.p80) || 0
    let closest = null
    let minDiff = Infinity
    for (const r of records.value) {
      if (r.id === record.id) continue
      const rP80 = Number(r.prediction?.p80) || 0
      const diff = Math.abs(rP80 - targetP80)
      if (diff < minDiff) { minDiff = diff; closest = r }
    }
    return closest ? { record: closest, diff: minDiff.toFixed(0) } : null
  }
  const targetPb = Number(record.prediction?.pb_grade_xrf ?? record.prediction?.pb_grade) || 0
  let closest = null
  let minDiff = Infinity
  for (const r of records.value) {
    if (r.id === record.id) continue
    const rPb = Number(r.prediction?.pb_grade_xrf ?? r.prediction?.pb_grade) || 0
    const diff = Math.abs(rPb - targetPb)
    if (diff < minDiff) { minDiff = diff; closest = r }
  }
  return closest ? { record: closest, diff: minDiff.toFixed(2) } : null
}

function startCompare(record) {
  compareRecord.value = findSimilar(record)
  if (!compareRecord.value) {
    ElMessage.info('没有找到足够的历史记录进行对比')
  }
}

// ---- 分页 ----
const currentPage = ref(1)
const pageSize = ref(24)

watch(currentPage, (val) => {
  query.value.offset = (val - 1) * query.value.limit
  loadRecords()
})

// ---- 格式化 ----
function formatTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatGroup(gid) {
  if (!gid || gid.length < 10) return gid
  return gid.slice(0, 4) + '-' + gid.slice(4, 6) + '-' + gid.slice(6, 8) + ' ' + gid.slice(8, 10) + ':' + gid.slice(10, 12)
}

function getPredPb(record) {
  try {
    const pb = record.prediction?.pb_grade_xrf ?? record.prediction?.pb_grade
    return (record.prediction && pb != null)
      ? Number(pb).toFixed(2) + '%'
      : '—'
  } catch { return '—' }
}

function getPredZn(record) {
  try {
    const zn = record.prediction?.zn_grade_xrf ?? record.prediction?.zn_grade
    return (record.prediction && zn != null)
      ? Number(zn).toFixed(2) + '%'
      : '—'
  } catch { return '—' }
}

function getConfidence(record) {
  try {
    const c = record.prediction?.confidence
    return c != null ? (Number(c) * 100).toFixed(1) + '%' : '—'
  } catch { return '—' }
}

// ---- 场景判断 ----
const isGrinding = computed(() => query.value.scene === 'grinding')

// 磨矿场景专用 getter
function getP80(record) {
  try {
    const v = record.prediction?.p80
    return v != null ? Number(v).toFixed(0) + 'μm' : '—'
  } catch { return '—' }
}

function getP80Raw(record) {
  try {
    return record.prediction?.p80 ?? null
  } catch { return null }
}

function getCondition(record) {
  try {
    return record.prediction?.condition || '未知'
  } catch { return '未知' }
}

function conditionType(cond) {
  if (cond === '正常') return 'normal'
  if (cond === '过磨') return 'low'   // 过磨 = 偏细 = 警告色
  if (cond === '欠磨') return 'high'  // 欠磨 = 偏粗 = 危险色
  return 'normal'
}

function getActual(record) {
  return record.actual_value != null ? Number(record.actual_value).toFixed(2) + '%' : '—'
}

function getDeviation(record) {
  const pred = Number(record.prediction?.pb_grade_xrf ?? record.prediction?.pb_grade)
  const act = record.actual_value
  if (pred == null || act == null) return null
  return (pred - act).toFixed(2)
}

function gradeLevel(val) {
  const v = Number(val)
  if (v < 4.0) return { label: '偏低', cls: 'low' }
  if (v > 7.0) return { label: '偏高', cls: 'high' }
  return { label: '正常', cls: 'normal' }
}

// ---- 缩略图 URL ----
function thumbUrl(record) {
  if (record.image_path) return '/api/image/' + encodeURIComponent(record.image_path)
  return ''
}

// ---- 趋势迷你图（场景自适应）----
function renderTrendChart() {
  if (!trendChartRef.value) return
  if (!trendChart) trendChart = echarts.init(trendChartRef.value)

  const dates = []
  const values = []

  if (isGrinding.value) {
    // 磨矿：P80 粒度趋势
    const sliced = [...records.value]
      .filter(r => r.prediction?.p80 != null)
      .slice(-20)
    for (const r of sliced) {
      dates.push(formatTime(r.created_at))
      values.push(Number(r.prediction.p80))
    }
    const option = {
      backgroundColor: 'transparent',
      grid: { top: 10, right: 10, bottom: 22, left: 40 },
      xAxis: {
        type: 'category', data: dates,
        axisLabel: { fontSize: 9, color: 'var(--text-3)', rotate: 30 },
        axisLine: { lineStyle: { color: 'var(--border-1)' } }, axisTick: { show: false },
      },
      yAxis: {
        type: 'value', name: 'P80(μm)',
        nameTextStyle: { fontSize: 10, color: 'var(--text-3)' },
        splitLine: { lineStyle: { color: 'var(--bg-3)' } },
        axisLabel: { fontSize: 9, color: 'var(--text-3)' }, axisLine: { show: false },
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'var(--bg-1)', borderColor: 'var(--border-1)',
        textStyle: { color: 'var(--text-1)', fontSize: 11 },
        extraCssText: 'box-shadow: var(--shadow-md);',
      },
      series: [{
        type: 'line', data: values, smooth: true,
        symbol: 'circle', symbolSize: 5,
        lineStyle: { color: '#722ed1', width: 2 },
        itemStyle: { color: '#722ed1' },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: 'rgba(114,46,209,0.15)' }, { offset: 1, color: 'rgba(114,46,209,0)' }] } },
      }],
    }
    trendChart.setOption(option, true)
    return
  }

  // 浮选：Pb 品位趋势（原有逻辑）
  const sliced = [...records.value]
    .filter(r => {
      const v = r.prediction?.pb_grade_xrf ?? r.prediction?.pb_grade
      return v != null
    })
    .slice(-20)

  for (const r of sliced) {
    dates.push(formatTime(r.created_at))
    values.push(Number(r.prediction?.pb_grade_xrf ?? r.prediction?.pb_grade))
  }

  const option = {
    backgroundColor: 'transparent',
    grid: { top: 10, right: 10, bottom: 22, left: 36 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { fontSize: 9, color: 'var(--text-3)', rotate: 30 },
      axisLine: { lineStyle: { color: 'var(--border-1)' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      name: 'Pb%',
      nameTextStyle: { fontSize: 10, color: 'var(--text-3)' },
      splitLine: { lineStyle: { color: 'var(--bg-3)' } },
      axisLabel: { fontSize: 9, color: 'var(--text-3)' },
      axisLine: { show: false },
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'var(--bg-1)',
      borderColor: 'var(--border-1)',
      textStyle: { color: 'var(--text-1)', fontSize: 11 },
      extraCssText: 'box-shadow: var(--shadow-md);',
    },
    series: [{
      type: 'line',
      data: values,
      smooth: true,
      symbol: 'circle',
      symbolSize: 5,
      lineStyle: { color: 'var(--color-primary)', width: 2 },
      itemStyle: { color: 'var(--color-primary)' },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(22,93,255,0.15)' },
          { offset: 1, color: 'rgba(22,93,255,0.02)' },
        ]),
      },
    }],
  }
  trendChart.setOption(option, true)
}

onMounted(() => {
  loadScenes()
  loadRecords()
  window.addEventListener('resize', () => trendChart?.resize())
})
</script>

<template>
  <PageShell
    title="时光机"
    :sub="isGrinding
      ? '回溯粒度分析记录，对比相似工况，洞察磨矿产品细度变化规律'
      : '回溯历史分析记录，对比相似工况，洞察品位波动规律'"
  >
    <!-- 四格摘要：原来 5 张卡（平均/最高/最低/异常/正常），
         按"固定 4 格"并进 4 格 —— 正常条数不丢，落到第 4 格的副行。 -->
    <template #kpi v-if="summary">
      <template v-if="isGrinding">
        <StatCard label="平均 P80" :value="summary.avg" unit="μm" tone="primary" />
        <StatCard label="最粗（P80max）" :value="summary.max" unit="μm" />
        <StatCard label="最细（P80min）" :value="summary.min" unit="μm" />
        <StatCard
          label="过磨条数"
          :value="summary.lowCount"
          tone="warning"
          :foot="`正常 ${summary.normalCount} 条`"
        />
      </template>
      <template v-else>
        <StatCard label="平均铅品位" :value="summary.avg" unit="%" tone="primary" />
        <StatCard label="最高值" :value="summary.max" unit="%" />
        <StatCard label="最低值" :value="summary.min" unit="%" />
        <StatCard
          label="偏低条数（<4%）"
          :value="summary.lowCount"
          tone="danger"
          :foot="`正常区间 ${summary.normalCount} 条`"
        />
      </template>
    </template>

    <!-- ====== 筛选栏 ====== -->
    <div class="filter-bar">
      <el-form :inline="true" class="filter-form">
        <el-form-item label="场景">
          <el-select v-model="query.scene" @change="handleSceneChange" style="width:160px">
            <el-option v-for="s in scenes" :key="s.name" :label="s.display_name" :value="s.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="时间组">
          <el-date-picker
            v-model="query.date"
            type="date"
            placeholder="选择日期筛选"
            value-format="YYYY-MM-DD"
            @change="handleDateChange"
            style="width:160px"
          />
        </el-form-item>
        <el-form-item>
          <span class="count-badge">
            <strong>{{ total }}</strong> 条记录
          </span>
        </el-form-item>
      </el-form>
    </div>

    <!-- 数据新鲜度提示（演示动线：先分析 → 再看时光机）
         ★ 不造数据：引导用户自己跑一次分析，回来后这里就有今天的记录 -->
    <div v-if="staleTip" class="stale-tip">
      <el-icon class="stale-ico"><InfoFilled /></el-icon>
      <span class="stale-text">{{ staleTip }}</span>
      <el-button type="primary" size="small" link @click="goAnalyze">去分析一次 →</el-button>
    </div>

    <!-- ====== 主体：左侧网格 + 右侧趋势图 ====== -->
    <div class="tm-main">
      <!-- 左侧：图像卡片网格 -->
      <div v-loading="loading" class="tm-grid-section">
        <div v-if="!records.length && !loading" class="empty-state">
          <div class="empty-icon"></div>
          <p>该条件下暂无分析记录</p>
          <p class="empty-hint">尝试切换场景或调整日期范围</p>
        </div>
        <div v-else class="image-grid">
          <div
            v-for="r in records"
            :key="r.id"
            class="grid-card"
            @click="showDetail(r)"
          >
            <div class="card-thumb-wrap">
              <el-image :src="thumbUrl(r)" fit="cover" class="card-thumb">
                <template #error>
                  <div class="thumb-placeholder">
                    <span></span>
                    <span>{{ isGrinding ? '粒度图像' : '泡沫图像' }}</span>
                  </div>
                </template>
              </el-image>
              <!-- 磨矿：P80 角标 / 浮选：品位角标 -->
              <div v-if="isGrinding" class="card-grade-badge" :class="conditionType(getCondition(r))">
                {{ getP80(r) }}
              </div>
              <div v-else class="card-grade-badge" :class="gradeLevel(getPredPb(r).replace('%','')).cls">
                {{ getPredPb(r) }}
              </div>
              <!-- 偏差标签（仅浮选） -->
              <div v-if="!isGrinding && getDeviation(r) != null" class="card-dev-tag" :class="Math.abs(Number(getDeviation(r))) < 0.5 ? 'good' : 'warn'">
                偏差 {{ getDeviation(r) }}
              </div>
              <!-- 工况标签（仅磨矿） -->
              <div v-if="isGrinding" class="card-cond-tag" :class="'cond-' + getCondition(r)">
                {{ getCondition(r) }}
              </div>
            </div>
            <div class="card-body">
              <div class="card-time">{{ formatTime(r.created_at) }}</div>
              <div class="card-meta">
                <!-- 磨矿：工况 + 置信度 -->
                <template v-if="isGrinding">
                  <span class="meta-zn">工况 {{ getCondition(r) }}</span>
                  <span class="meta-conf">置信度 {{ getConfidence(r) }}</span>
                </template>
                <!-- 浮选：Zn + 置信度（原有） -->
                <template v-else>
                  <span class="meta-zn">Zn {{ getPredZn(r) }}</span>
                  <span class="meta-conf">置信度 {{ getConfidence(r) }}</span>
                </template>
              </div>
              <div v-if="r.actual_value != null" class="card-actual">
                真实值: <strong>{{ getActual(r) }}</strong>
              </div>
            </div>
          </div>
        </div>

        <!-- 分页 -->
        <div class="pagination-wrap" v-if="total > query.limit">
          <el-pagination
            v-model:current-page="currentPage"
            :page-size="pageSize"
            :total="total"
            layout="prev, pager, next"
            size="small"
          />
        </div>
      </div>

      <!-- 右侧：迷你趋势图 -->
      <div class="tm-trend-section" v-if="records.length > 3">
        <div class="trend-card">
          <h4 class="trend-title">{{ isGrinding ? '粒度趋势（P80）' : '品位趋势（最近 N 条）' }}</h4>
          <div ref="trendChartRef" class="trend-chart"></div>
        </div>
        <!-- 快速统计 -->
        <div class="quick-stats" v-if="summary">
          <h4 class="trend-title">分布概览</h4>
          <div class="dist-bar">
            <div class="dist-segment low" :style="{ width: (summary.lowCount / summary.count * 100) + '%' }">
              偏低 {{ summary.lowCount }}
            </div>
            <div class="dist-segment normal" :style="{ width: (summary.normalCount / summary.count * 100) + '%' }">
              正常 {{ summary.normalCount }}
            </div>
            <div class="dist-segment high" :style="{ width: (summary.highCount / summary.count * 100) + '%' }">
              偏高 {{ summary.highCount }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ====== 详情弹窗（增强版） ====== -->
    <ElDialog v-model="dialogVisible" title="工况详情" width="800px" destroy-on-close>
      <div v-if="currentRecord" class="detail-content">

        <!-- 主记录信息 -->
        <div class="detail-hero">
          <div class="hero-image">
            <el-image :src="thumbUrl(currentRecord)" fit="contain" class="hero-img">
              <template #error><div class="thumb-placeholder hero-ph">无图</div></template>
            </el-image>
          </div>
          <div class="hero-info">
            <h3>分析结果</h3>
            <div class="info-grid">
              <!-- 磨矿场景 -->
              <template v-if="isGrinding">
                <div class="info-item">
                  <span class="info-label">P80 粒度</span>
                  <span class="info-value primary">{{ getP80(currentRecord) }}</span>
                </div>
                <div class="info-item">
                  <span class="info-label">工况判定</span>
                  <span class="info-value" :class="'cond-text-' + getCondition(currentRecord)">{{ getCondition(currentRecord) }}</span>
                </div>
              </template>
              <!-- 浮选场景（原有） -->
              <template v-else>
                <div class="info-item">
                  <span class="info-label">预测 Pb</span>
                  <span class="info-value primary">{{ getPredPb(currentRecord) }}</span>
                </div>
                <div class="info-item">
                  <span class="info-label">预测 Zn</span>
                  <span class="info-value">{{ getPredZn(currentRecord) }}</span>
                </div>
              </template>
              <div class="info-item">
                <span class="info-label">置信度</span>
                <span class="info-value">{{ getConfidence(currentRecord) }}</span>
              </div>
              <!-- 真实值（仅浮选有） -->
              <div v-if="!isGrinding" class="info-item">
                <span class="info-label">真实值</span>
                <span class="info-value" :class="getDeviation(currentRecord) != null && Math.abs(Number(getDeviation(currentRecord))) < 0.5 ? 'success' : ''">
                  {{ getActual(currentRecord) }}
                </span>
              </div>
              <div class="info-item">
                <span class="info-label">模型版本</span>
                <span class="info-value mono">{{ currentRecord.model_version || '—' }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">分析时间</span>
                <span class="info-value mono">{{ formatTime(currentRecord.created_at) }}</span>
              </div>
            </div>
            <el-button type="primary" size="small" @click="startCompare(currentRecord)" style="margin-top:12px;">
              找相似工况对比
            </el-button>
          </div>
        </div>

        <!-- 相似工况对比区 -->
        <div v-if="compareRecord" class="compare-section">
          <h4 class="compare-title">最相似历史记录（偏差 {{ isGrinding ? compareRecord.diff + 'μm' : compareRecord.diff + '%' }}）</h4>
          <div class="compare-grid">
            <div class="compare-col">
              <div class="compare-label">当前记录</div>
              <el-image :src="thumbUrl(currentRecord)" fit="cover" class="compare-img">
                <template #error><div class="thumb-placeholder"></div></template>
              </el-image>
              <div class="compare-data">
                <!-- 磨矿对比数据 -->
                <template v-if="isGrinding">
                  <div>P80: <strong>{{ getP80(currentRecord) }}</strong></div>
                  <div>工况: {{ getCondition(currentRecord) }}</div>
                </template>
                <!-- 浮选对比数据（原有） -->
                <template v-else>
                  <div>Pb: <strong>{{ getPredPb(currentRecord) }}</strong></div>
                  <div>Zn: {{ getPredZn(currentRecord) }}</div>
                </template>
                <div>{{ formatTime(currentRecord.created_at) }}</div>
              </div>
            </div>
            <div class="compare-vs">VS</div>
            <div class="compare-col">
              <div class="compare-label">历史相似</div>
              <el-image :src="thumbUrl(compareRecord.record)" fit="cover" class="compare-img">
                <template #error><div class="thumb-placeholder"></div></template>
              </el-image>
              <div class="compare-data">
                <template v-if="isGrinding">
                  <div>P80: <strong>{{ getP80(compareRecord.record) }}</strong></div>
                  <div>工况: {{ getCondition(compareRecord.record) }}</div>
                </template>
                <template v-else>
                  <div>Pb: <strong>{{ getPredPb(compareRecord.record) }}</strong></div>
                  <div>Zn: {{ getPredZn(compareRecord.record) }}</div>
                </template>
                <div>{{ formatTime(compareRecord.record.created_at) }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- 预测详情 JSON -->
        <el-divider content-position="left">原始预测数据</el-divider>
        <pre class="json-block">{{ JSON.stringify(currentRecord.prediction, null, 2) }}</pre>
      </div>
    </ElDialog>
  </PageShell>
</template>

<style scoped>
/* 页头/留白/返回入口由 PageShell 提供（2026-10-19 一致性整治） */

/* ---- 筛选栏 ---- */
.filter-bar { background: var(--bg-1); border-radius: 8px; padding: 14px 20px; margin-bottom: 18px; box-shadow: var(--shadow-sm); }
.filter-form { margin-bottom: -6px; }
.count-badge { color: var(--text-3); font-size: 13px; margin-left: 12px; }
.count-badge strong { color: var(--color-primary); font-size: 16px; }

/* ---- 数据新鲜度提示条 ---- */
.stale-tip {
  display: flex; align-items: center; gap: 8px;
  background: #e8f3ff; border: 1px solid #bedaff; border-radius: 8px;
  padding: 10px 14px; margin: -6px 0 18px;
  font-size: 13px; color: var(--text-1);
}
.stale-ico { color: var(--color-primary); font-size: 15px; flex-shrink: 0; }
.stale-text { flex: 1; min-width: 0; }

/* ---- 摘要卡片 ---- */

/* ---- 主体布局 ---- */
.tm-main { display: grid; grid-template-columns: 1fr 300px; gap: 18px; align-items: start; }

/* ---- 图像网格 ---- */
.tm-grid-section { min-height: 300px; }
.image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 14px; }

.grid-card { cursor: pointer; border-radius: 8px; overflow: hidden; background: var(--bg-1); box-shadow: var(--shadow-md); transition: transform 0.2s, box-shadow 0.2s; }
.grid-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-md); }

.card-thumb-wrap { position: relative; height: 140px; overflow: hidden; background: var(--bg-2); }
.card-thumb { width: 100%; height: 100%; }
.thumb-placeholder { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: var(--text-4); font-size: 28px; gap: 4px; font-size: 13px; }
.thumb-placeholder span:first-child { font-size: 28px; }

.card-grade-badge { position: absolute; top: 8px; left: 8px; padding: 3px 10px; border-radius: 6px; font-size: 12px; font-weight: 700; color: var(--bg-1); backdrop-filter: blur(4px); }
.card-grade-badge.low { background: rgba(245,63,63,0.85); }
.card-grade-badge.normal { background: rgba(0,180,42,0.85); }
.card-grade-badge.high { background: rgba(22,93,255,0.85); }

.card-dev-tag { position: absolute; bottom: 8px; right: 8px; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; color: var(--bg-1); }
.card-dev-tag.good { background: rgba(0,180,42,0.8); }
.card-dev-tag.warn { background: rgba(255,125,0,0.8); }

/* 磨矿工况标签（卡片角标下方） */
.card-cond-tag { position: absolute; bottom: 8px; left: 8px; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; color: var(--bg-1); }
.card-cond-tag.cond-正常 { background: rgba(0,180,42,0.85); }
.card-cond-tag.cond-过磨 { background: rgba(255,125,0,0.85); }
.card-cond-tag.cond-欠磨 { background: rgba(245,63,63,0.85); }

/* 详情弹窗中工况文字颜色 */
.cond-text-正常 { color: var(--color-success) !important; }
.cond-text-过磨 { color: var(--color-warning) !important; }
.cond-text-欠磨 { color: var(--color-danger) !important; }

.card-body { padding: 10px 12px; }
.card-time { font-size: 12px; font-weight: 600; color: var(--text-1); }
.card-meta { display: flex; gap: 12px; margin-top: 4px; }
.meta-zn, .meta-conf { font-size: 11px; color: var(--text-3); }
.card-actual { font-size: 11px; color: var(--color-success); margin-top: 4px; }

/* ---- 空状态 ---- */
.empty-state { text-align: center; padding: 60px 20px; color: var(--text-3); }
.empty-icon { font-size: 48px; margin-bottom: 12px; }
.empty-state p { margin: 4px 0; font-size: 14px; }
.empty-hint { font-size: 12px; color: var(--text-4); }

/* ---- 分页 ---- */
.pagination-wrap { display: flex; justify-content: center; margin-top: 20px; }

/* ---- 右侧趋势区 ---- */
.tm-trend-section { position: sticky; top: 20px; }
.trend-card { background: var(--bg-1); border-radius: 8px; padding: 16px; box-shadow: var(--shadow-sm); margin-bottom: 12px; }
.trend-title { font-size: 14px; font-weight: 600; color: var(--text-1); margin: 0 0 12px; }
.trend-chart { height: 200px; }

.quick-stats { background: var(--bg-1); border-radius: 8px; padding: 16px; box-shadow: var(--shadow-sm); }
.dist-bar { display: flex; border-radius: 6px; overflow: hidden; height: 36px; font-size: 12px; }
.dist-segment { display: flex; align-items: center; justify-content: center; color: var(--bg-1); font-weight: 500; transition: width 0.5s ease; }
.dist-segment.low { background: var(--color-danger); }
.dist-segment.normal { background: var(--color-success); }
.dist-segment.high { background: var(--color-primary); }

/* ---- 详情弹窗 ---- */
.detail-content { max-height: 65vh; overflow-y: auto; }

.detail-hero { display: flex; gap: 20px; margin-bottom: 20px; }
.hero-image { width: 280px; flex-shrink: 0; border-radius: 8px; overflow: hidden; background: var(--bg-2); }
.hero-img { width: 100%; height: 200px; }
.hero-ph { height: 200px; display: flex; align-items: center; justify-content: center; font-size: 16px; }

.hero-info { flex: 1; }
.hero-info h3 { margin: 0 0 12px; font-size: 16px; color: var(--text-1); }
.info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px; }
.info-item { display: flex; flex-direction: column; }
.info-label { font-size: 11px; color: var(--text-3); }
.info-value { font-size: 15px; font-weight: 600; color: var(--text-1); }
.info-value.primary { color: var(--color-primary); font-size: 17px; }
.info-value.success { color: var(--color-success); }
.info-value.mono { font-family: 'Menlo', 'Consolas', monospace; font-size: 13px; font-weight: 400; }

/* ---- 对比区 ---- */
.compare-section { background: var(--bg-2); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
.compare-title { font-size: 14px; font-weight: 600; color: var(--text-1); margin: 0 0 14px; }
.compare-grid { display: flex; align-items: center; gap: 16px; }
.compare-col { flex: 1; }
.compare-label { font-size: 12px; font-weight: 600; color: var(--text-3); margin-bottom: 8px; text-align: center; }
.compare-img { width: 100%; height: 150px; border-radius: 6px; overflow: hidden; background: var(--bg-3); }
.compare-data { margin-top: 8px; font-size: 12px; color: var(--text-2); line-height: 1.8; }
.compare-vs { font-size: 14px; font-weight: 700; color: var(--text-4); flex-shrink: 0; }

.json-block { background: var(--bg-2); padding: 12px; border-radius: 6px; font-size: 12px; line-height: 1.6; overflow-x: auto; white-space: pre-wrap; word-break: break-all; color: var(--text-2); }

/* ---- 响应式 ---- */
@media (max-width: 900px) {
  .tm-main { grid-template-columns: 1fr; }
  .detail-hero { flex-direction: column; }
  .hero-image { width: 100%; }
}
</style>
