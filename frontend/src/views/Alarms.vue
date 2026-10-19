<script setup>
/**
 * 告警中心 v2 — P0（证据抽屉 + 处置闭环 + 帧级聚合）
 *
 * 三件事：
 *   1. 帧级聚合：同一帧图像触发的多条规则合并为一个「事件」
 *   2. 证据链：原图 / 分割叠加 + 规则命中 + 特征与历史基线对比
 *   3. 处置闭环：待处理 → 已确认 → 处理中 → 已闭环 / 误报，写审计流水
 */
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

// ---- 字典 ----
const POSITIONS = ['浮选班长', '磨矿班长', '值班调度', '安全员', '工艺工程师', '设备点检员']

const STATUS_META = {
  new:            { label: '待处理', color: 'var(--color-danger)',  tag: 'danger' },
  acknowledged:   { label: '已确认', color: 'var(--color-warning)', tag: 'warning' },
  in_progress:    { label: '处理中', color: 'var(--color-primary)', tag: 'primary' },
  closed:         { label: '已闭环', color: 'var(--color-success)', tag: 'success' },
  false_positive: { label: '误报',   color: 'var(--text-3)',        tag: 'info' },
}

const SCENES = [
  { value: 'flotation', label: '浮选品位' },
  { value: 'grinding', label: '粒度分析' },
  { value: 'safety', label: '安全巡检' },
]

// 当前状态 → 可执行的处置动作
const ACTIONS_BY_STATUS = {
  new: [
    { key: 'acknowledge', label: '确认收到' },
    { key: 'start', label: '开始处理' },
    { key: 'close', label: '直接闭环' },
    { key: 'false_positive', label: '标记误报' },
  ],
  acknowledged: [
    { key: 'start', label: '开始处理' },
    { key: 'close', label: '闭环' },
    { key: 'false_positive', label: '标记误报' },
  ],
  in_progress: [
    { key: 'close', label: '闭环' },
    { key: 'false_positive', label: '标记误报' },
  ],
  closed: [{ key: 'reopen', label: '重新打开' }],
  false_positive: [{ key: 'reopen', label: '重新打开' }],
}

// ---- 状态 ----
const loading = ref(false)
const detailLoading = ref(false)
const submitting = ref(false)

const groups = ref([])
const total = ref(0)
const stats = ref({
  total: 0, new: 0, acknowledged: 0, in_progress: 0, closed: 0, false_positive: 0,
  warning: 0, danger: 0, mtta_minutes: null,
  redundancy: { groups: 0, redundant: 0, ratio: 0 },
})

const filters = ref({ scene_id: '', level: '', status: '', keyword: '', limit: 15, offset: 0 })

const selectedKey = ref(null)
const selectedAlarmId = ref(null)
const detail = ref(null)
const imgMode = ref('image')          // image | overlay
const imgError = ref(false)
const rootCause = ref(null)           // 根因联动分析结果
const rcError = ref(false)
const expandedRc = ref('')            // 展开的候选原因 code

// 处置表单
const handleForm = ref({ action: '', operator: '', note: '', action_taken: '' })

// ---- 批量处置（勾选事件 → 一次调用 batch-handle 写多条）----
// 后端 `POST /api/alarms/batch-handle` 早已就绪（≤200 条/次、逐条返回成败），
// 此前前端零调用 —— 446 条告警只能一条条点，这条补上"整组/多选处置"。
const checkedKeys = ref([])            // 选中的 group_key（仅当前页；翻页/筛选会清空）
const showBatchDialog = ref(false)
const batchSubmitting = ref(false)
const batchForm = ref({ action: '', operator: '', note: '' })
const exporting = ref(false)
// 记住上次使用的处理人（单条/批量共用一个记忆）——连续处置多个事件时免去重复选择
const lastOperator = ref('')

// ---- 自动刷新（30s，与 LiveWall 同约定）----
// 静默刷新：不亮 loading 遮罩、不动右侧详情（避免打断正在填写的处置表单）。
// ★ 处置中不打扰：有勾选待处置 / 批量弹窗打开时自动暂停 ——
//   自动刷新会走 loadGroups → 清空勾选，把用户选到一半的批量操作"吞掉"。
const AUTO_REFRESH_MS = 30000
const lastUpdate = ref('')
let autoTimer = null
const autoPaused = computed(() => checkedKeys.value.length > 0 || showBatchDialog.value)

// ---- 计算 ----
const page = computed(() => Math.floor(filters.value.offset / filters.value.limit) + 1)
const curStatus = computed(() => detail.value?.alarm?.status || 'new')
const availableActions = computed(() => ACTIONS_BY_STATUS[curStatus.value] || [])
const noteRequired = computed(() => ['start', 'close', 'false_positive'].includes(handleForm.value.action))

// ---- 批量选择派生值 ----
const checkedGroups = computed(() => groups.value.filter((g) => checkedKeys.value.includes(g.group_key)))
// 每条事件的 rules[] 里带各自告警 id —— 这就是 batch-handle 的入参来源
const checkedAlarmIds = computed(() =>
  checkedGroups.value.flatMap((g) => (g.rules || []).map((r) => r.id))
)
// 选中集合里各状态可执行动作的**并集**（后端会逐条校验，不允许的单独回失败原因）
const batchActionOptions = computed(() => {
  const m = new Map()
  checkedGroups.value.forEach((g) => {
    (g.rules || []).forEach((r) => {
      (ACTIONS_BY_STATUS[r.status] || []).forEach((a) => m.set(a.key, a))
    })
  })
  return [...m.values()]
})
const batchNoteRequired = computed(() => ['start', 'close', 'false_positive'].includes(batchForm.value.action))

const stage = computed(() => {
  const s = stats.value
  return { pct: (v) => (s.total ? Math.round((v / s.total) * 100) : 0) }
})

// 响应时长：用中位数（均值会被跨天的极端值拉偏）
const mttaText = computed(() => {
  const m = stats.value.mtta_median_minutes ?? stats.value.mtta_minutes
  if (!m) return '—'
  return m >= 60 ? `${(m / 60).toFixed(1)} 小时` : `${Math.round(m)} 分钟`
})

const mttaSub = computed(() => {
  const s = stats.value
  if (!s.mtta_samples) return '暂无处置样本'
  const fmt = (m) => (m == null ? '—' : m >= 60 ? `${(m / 60).toFixed(1)}h` : `${Math.round(m)}min`)
  return `均值 ${fmt(s.mtta_minutes)} · P90 ${fmt(s.mtta_p90_minutes)} · n=${s.mtta_samples}`
})

// ---- 数据加载 ----
async function loadGroups(keepSelection = false, opts = {}) {
  const silent = !!opts.silent          // 静默（自动刷新）：不亮遮罩
  if (!silent) {
    loading.value = true
    // 列表一变（翻页/筛选/刷新），跨页勾选就失去意义 —— 清空，避免"看到的"和"要处置的"不一致
    // （静默刷新不走到这里：它在 autoPaused 为真时根本不会被触发）
    checkedKeys.value = []
  }
  try {
    const params = Object.fromEntries(
      Object.entries(filters.value).filter(([, v]) => v !== '' && v !== null && v !== undefined)
    )
    const res = await api.get('/alarms/groups', { params })
    groups.value = res.groups || []
    total.value = res.total || 0
    stats.value = res.stats || stats.value
    lastUpdate.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })

    if (!keepSelection) {
      // 默认选第一条，右侧不留空
      const first = groups.value[0]
      if (first) selectGroup(first)
      else { selectedKey.value = null; detail.value = null }
    }
  } catch (e) {
    console.error('加载告警分组失败:', e)
  } finally {
    if (!silent) loading.value = false
  }
}

async function selectGroup(g) {
  selectedKey.value = g.group_key
  imgError.value = false
  imgMode.value = 'image'
  await loadDetail(g.primary.id)
}

async function selectAlarm(id) {
  imgError.value = false
  await loadDetail(id)
}

async function loadDetail(id) {
  if (!id) return
  detailLoading.value = true
  selectedAlarmId.value = id
  try {
    detail.value = await api.get(`/alarms/${id}`)
    handleForm.value = {
      action: '',
      operator: handleForm.value.operator || lastOperator.value || '',
      note: '',
      action_taken: '',
    }
    if (curStatus.value === 'closed' || curStatus.value === 'false_positive') {
      handleForm.value.action = 'reopen'
    }
    loadRootCause(id)   // 根因分析并行加载，不阻塞详情渲染
  } catch (e) {
    console.error('加载告警详情失败:', e)
    detail.value = null
  } finally {
    detailLoading.value = false
  }
}

// ---- 根因联动分析（P1）----
async function loadRootCause(id) {
  rootCause.value = null
  rcError.value = false
  expandedRc.value = ''
  try {
    rootCause.value = await api.get(`/alarms/${id}/root-cause`)
  } catch (e) {
    console.error('根因分析失败:', e)
    rcError.value = true
  }
}

function toggleRc(code) {
  expandedRc.value = expandedRc.value === code ? '' : code
}

const CAT_LABELS = { grinding: '磨矿', flotation: '浮选', water: '回水' }
const CONF_CLS = { '高': 'high', '中': 'mid', '低': 'low' }

function lagText(min) {
  if (min === null || min === undefined) return ''
  return min < 90 ? `${Math.round(min)} 分钟前` : `${(min / 60).toFixed(1)} 小时前`
}

function srcMeta(s) {
  if (!s.available) return { cls: 'off', text: '无数据' }
  if (s.in_window) return { cls: 'on', text: lagText(s.lag_minutes) }
  return { cls: 'far', text: lagText(s.lag_minutes) }
}

function handleSearch() { filters.value.offset = 0; loadGroups() }
function resetFilters() {
  filters.value = { scene_id: '', level: '', status: '', keyword: '', limit: 15, offset: 0 }
  loadGroups()
}
function onPageChange(p) { filters.value.offset = (p - 1) * filters.value.limit; loadGroups(true) }
function onSizeChange(s) { filters.value.limit = s; filters.value.offset = 0; loadGroups(true) }

// ---- 处置提交 ----
async function submitAction() {
  const f = handleForm.value
  if (!f.action) { ElMessage.warning('请先选择处置动作'); return }
  if (!f.operator) { ElMessage.warning('请选择处理人'); return }
  if (noteRequired.value && !f.note.trim()) {
    ElMessage.warning('开始处理 / 闭环 / 误报 必须填写处理意见')
    return
  }
  submitting.value = true
  try {
    await api.post(`/alarms/${selectedAlarmId.value}/handle`, {
      action: f.action,
      operator: f.operator,
      note: f.note?.trim() || null,
      action_taken: f.action_taken?.trim() || null,
    })
    ElMessage.success('处置已记录')
    lastOperator.value = f.operator
    const keepId = selectedAlarmId.value
    await loadGroups(true)
    await loadDetail(keepId)
  } catch (e) {
    // 拦截器已提示常见错误，这里兜底业务校验信息
    const msg = e?.response?.data?.detail
    if (msg) ElMessage.error(msg)
  } finally {
    submitting.value = false
  }
}

// ---- 批量处置 ----
function toggleCheck(key, on) {
  if (on) {
    if (!checkedKeys.value.includes(key)) checkedKeys.value.push(key)
  } else {
    checkedKeys.value = checkedKeys.value.filter((k) => k !== key)
  }
}
function clearChecked() { checkedKeys.value = [] }
function selectAllOnPage() { checkedKeys.value = groups.value.map((g) => g.group_key) }

function openBatchDialog() {
  if (!checkedKeys.value.length) return
  batchForm.value = { action: '', operator: lastOperator.value || handleForm.value.operator || '', note: '' }
  showBatchDialog.value = true
}

async function submitBatch() {
  const f = batchForm.value
  if (!f.action) { ElMessage.warning('请先选择处置动作'); return }
  if (!f.operator) { ElMessage.warning('请选择处理人'); return }
  if (batchNoteRequired.value && !f.note.trim()) {
    ElMessage.warning('开始处理 / 闭环 / 误报 必须填写处理意见')
    return
  }
  batchSubmitting.value = true
  try {
    const res = await api.post('/alarms/batch-handle', {
      alarm_ids: checkedAlarmIds.value,
      action: f.action,
      operator: f.operator,
      note: f.note?.trim() || null,
    })
    const okc = res.ok_count ?? (res.ok || []).length
    const failed = res.failed || []
    if (failed.length) {
      // 部分失败不阻断（后端设计如此）—— 把第一条失败原因带出来，别让用户猜
      ElMessage.warning(`已处置 ${okc} 条 · ${failed.length} 条未通过（如「${failed[0].reason}」）`)
    } else {
      ElMessage.success(`已批量处置 ${okc} 条告警`)
    }
    lastOperator.value = f.operator
    showBatchDialog.value = false
    checkedKeys.value = []
    const keepId = selectedAlarmId.value
    await loadGroups(true)
    if (keepId) await loadDetail(keepId)
  } catch (e) {
    const msg = e?.response?.data?.detail
    if (msg) ElMessage.error(msg)
  } finally {
    batchSubmitting.value = false
  }
}

// ---- 导出当前筛选的告警（CSV，前端生成）----
function csvCell(v) {
  const s = v == null ? '' : String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}
async function exportAlarms() {
  exporting.value = true
  try {
    const base = {}
    for (const k of ['scene_id', 'level', 'status', 'keyword']) {
      if (filters.value[k]) base[k] = filters.value[k]
    }
    // 分页拉全量（后端 limit 上限 200；CAP 防手滑，正常数据量 3 次请求内）
    const CAP = 2000
    const rows = []
    for (let off = 0; off < CAP; off += 200) {
      const res = await api.get('/alarms', { params: { ...base, limit: 200, offset: off } })
      const items = res.alarms || []
      rows.push(...items)
      if (items.length < 200 || rows.length >= (res.total || 0)) break
    }
    if (!rows.length) { ElMessage.warning('当前筛选下没有可导出的告警'); return }
    const header = ['告警ID', '事件组', '场景', '级别', '规则', '当前状态', '告警内容', '处理人', '处理时间', '处理意见', '触发时间']
    const body = rows.map((a) => [
      a.id, a.group_key || '', a.scene_id || '', a.level_label || a.level || '', a.rule_name || '',
      a.status_label || a.status || '', a.message || '', a.handled_by || '',
      a.handled_at ? a.handled_at.replace('T', ' ').slice(0, 19) : '',
      a.handled_note || '',
      (a.created_at || '').replace('T', ' ').slice(0, 19),
    ])
    const csv = [header, ...body].map((r) => r.map(csvCell).join(',')).join('\r\n')
    const blob = new Blob([`\ufeff${csv}`], { type: 'text/csv;charset=utf-8' })   // BOM：Excel 正确识别中文
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const t = new Date()
    const pad = (n) => String(n).padStart(2, '0')
    a.href = url
    a.download = `suieye_alarms_${t.getFullYear()}${pad(t.getMonth() + 1)}${pad(t.getDate())}_${pad(t.getHours())}${pad(t.getMinutes())}.csv`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success(`已导出 ${rows.length} 条告警`)
  } catch (e) {
    console.error('导出告警失败:', e)
    ElMessage.error('导出失败，请重试')
  } finally {
    exporting.value = false
  }
}

// ---- 工具 ----
function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function imgUrl(p) {
  if (!p) return ''
  return `/api/image/${p.replace(/^\/+/, '')}`
}

function statusMeta(s) { return STATUS_META[s] || STATUS_META.new }

// 特征对比条：以基线 0 为中心，偏移量 = z（截断到 ±3）
function devBarWidth(z) {
  return Math.min(Math.abs(z) / 3, 1) * 50
}
function devBarStyle(z, severity) {
  const color = severity === 'high' ? 'var(--color-danger)'
    : severity === 'medium' ? 'var(--color-warning)' : 'var(--text-4)'
  const w = devBarWidth(z)
  return z >= 0
    ? { left: '50%', width: `${w}%`, background: color }
    : { right: '50%', width: `${w}%`, background: color }
}

onMounted(() => {
  loadGroups()
  // 自动刷新：keepSelection=true 保住右侧详情；静默不亮遮罩
  autoTimer = setInterval(() => {
    if (autoPaused.value) return
    loadGroups(true, { silent: true })
  }, AUTO_REFRESH_MS)
})
onBeforeUnmount(() => { if (autoTimer) clearInterval(autoTimer) })
</script>

<template>
  <div class="page">
    <!-- ===== 顶部：标题 + 聚合说明 ===== -->
    <div class="page-head">
      <div class="head-left">
        <h1>告警</h1>
        <span class="head-sub">
          {{ stats.total }} 条告警聚合为 <b>{{ stats.events || 0 }}</b> 个事件
          <template v-if="stats.redundancy?.redundant">
            · 消除 <b>{{ stats.redundancy.redundant }}</b> 条同帧冗余
          </template>
          · 严重 <b>{{ stats.danger }}</b> / 预警 <b>{{ stats.warning }}</b>
        </span>
      </div>
      <div class="head-actions">
        <span class="auto-hint" :class="{ paused: autoPaused }">
          <template v-if="autoPaused">自动刷新已暂停（处置中）</template>
          <template v-else>自动刷新 30s<template v-if="lastUpdate"> · 最后更新 {{ lastUpdate }}</template></template>
        </span>
        <el-button size="small" :loading="loading" @click="loadGroups(true)">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
      </div>
    </div>

    <!-- ===== KPI 数据带 ===== -->
    <div class="kpi-band">
      <div class="kpi" v-for="k in [
        { key: 'new', label: '待处理', value: stats.new, sub: `${stage.pct(stats.new)}% 未处置`, color: 'var(--color-danger)' },
        { key: 'acknowledged', label: '已确认', value: stats.acknowledged, sub: `${stage.pct(stats.acknowledged)}% 已受理`, color: 'var(--color-warning)' },
        { key: 'in_progress', label: '处理中', value: stats.in_progress, sub: '现场处置中', color: 'var(--color-primary)' },
        { key: 'closed', label: '已闭环', value: stats.closed, sub: `${stage.pct(stats.closed)}% 闭环率`, color: 'var(--color-success)' },
        { key: 'false_positive', label: '误报', value: stats.false_positive, sub: '规则调优依据', color: 'var(--text-3)' },
      ]" :key="k.key">
        <div class="kpi-value" :style="{ color: k.color }">{{ k.value }}</div>
        <div class="kpi-label">{{ k.label }}</div>
        <div class="kpi-sub">{{ k.sub }}</div>
      </div>
      <div class="kpi kpi-wide">
        <div class="kpi-value">{{ mttaText }}</div>
        <div class="kpi-label">响应时长中位数</div>
        <div class="kpi-sub">{{ mttaSub }}</div>
      </div>
    </div>

    <!-- ===== 筛选 ===== -->
    <div class="toolbar">
      <el-select v-model="filters.scene_id" placeholder="全部场景" clearable size="small" style="width:126px" @change="handleSearch">
        <el-option v-for="s in SCENES" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
      <el-select v-model="filters.level" placeholder="全部级别" clearable size="small" style="width:112px" @change="handleSearch">
        <el-option label="严重" value="danger" />
        <el-option label="预警" value="warning" />
      </el-select>
      <el-select v-model="filters.status" placeholder="全部状态" clearable size="small" style="width:118px" @change="handleSearch">
        <el-option v-for="(v, k) in STATUS_META" :key="k" :label="v.label" :value="k" />
      </el-select>
      <el-input v-model="filters.keyword" placeholder="搜索规则或内容" clearable size="small" style="width:190px"
                @keyup.enter="handleSearch" @clear="handleSearch">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-button size="small" type="primary" @click="handleSearch">查询</el-button>
      <el-button size="small" text @click="resetFilters">重置</el-button>
      <el-button size="small" :loading="exporting" @click="exportAlarms">
        <el-icon><Download /></el-icon>&nbsp;导出
      </el-button>
      <span class="toolbar-count">共 {{ total }} 个事件</span>
    </div>

    <!-- ===== 主区：事件列表 + 证据面板 ===== -->
    <div class="main">
      <!-- 左：事件列表 -->
      <div class="panel event-panel">
        <div class="panel-head">
          <span class="panel-title">告警事件</span>
          <span class="panel-hint">按帧聚合 · 点击查看证据</span>
        </div>
        <!-- 批量处置条：勾选 ≥1 个事件时出现（一处闭环整组，后端 batch-handle 逐条写审计） -->
        <div class="batch-bar" v-if="checkedKeys.length">
          <span class="bb-info">
            已选 <b>{{ checkedKeys.length }}</b> 个事件 · <b>{{ checkedAlarmIds.length }}</b> 条告警
          </span>
          <span class="bb-actions">
            <button class="bb-btn" @click="selectAllOnPage">全选本页</button>
            <button class="bb-btn" @click="clearChecked">清除</button>
            <button class="bb-btn primary" @click="openBatchDialog">批量处置</button>
          </span>
        </div>
        <div class="panel-body" v-loading="loading">
          <div v-if="!groups.length && !loading" class="empty">暂无符合条件的告警</div>
          <div
            v-for="g in groups" :key="g.group_key"
            class="event"
            :class="{ active: selectedKey === g.group_key, danger: g.primary.level === 'danger' }"
            @click="selectGroup(g)"
          >
            <!-- 勾选框：点它不该触发"查看证据" → 外层包一层 @click.stop -->
            <span class="ev-check" @click.stop>
              <el-checkbox
                size="small"
                :model-value="checkedKeys.includes(g.group_key)"
                @change="(v) => toggleCheck(g.group_key, v)"
              />
            </span>
            <div class="ev-thumb">
              <img v-if="g.thumb" :src="imgUrl(g.thumb)" alt="" loading="lazy" />
              <el-icon v-else><Picture /></el-icon>
            </div>
            <div class="ev-main">
              <div class="ev-line1">
                <span class="ev-level" :class="g.primary.level">{{ g.primary.level === 'danger' ? '严重' : '预警' }}</span>
                <span class="ev-rule">{{ g.primary.rule_name }}</span>
                <span v-if="g.count > 1" class="ev-count">同帧 {{ g.count }} 条</span>
                <span class="ev-status" :style="{ color: statusMeta(g.status).color }">{{ statusMeta(g.status).label }}</span>
              </div>
              <div class="ev-msg">{{ g.primary.message }}</div>
              <div class="ev-line3">
                <span>{{ SCENES.find(s => s.value === g.scene_id)?.label || g.scene_id }}</span>
                <span class="dot">·</span>
                <span>{{ fmtTime(g.latest_at) }}</span>
                <template v-if="g.unhandled">
                  <span class="dot">·</span>
                  <span class="ev-todo">{{ g.unhandled }} 条待办</span>
                </template>
                <template v-if="g.primary.handled_by">
                  <span class="dot">·</span>
                  <span>{{ g.primary.handled_by }}</span>
                </template>
              </div>
            </div>
          </div>
        </div>
        <div class="panel-foot">
          <el-pagination
            size="small" background
            :current-page="page" :page-size="filters.limit" :total="total"
            :page-sizes="[15, 30, 50]"
            layout="prev, pager, next, sizes"
            @current-change="onPageChange" @size-change="onSizeChange"
          />
        </div>
      </div>

      <!-- 右：证据面板 -->
      <div class="panel detail-panel" v-loading="detailLoading">
        <template v-if="detail">
          <!-- 头部 -->
          <div class="panel-head detail-head">
            <div class="dh-left">
              <span class="ev-level" :class="detail.alarm.level">{{ detail.alarm.level_label }}</span>
              <span class="dh-rule">{{ detail.alarm.rule_name }}</span>
            </div>
            <span class="dh-status" :style="{ color: statusMeta(detail.alarm.status).color }">
              {{ detail.alarm.status_label }}
            </span>
          </div>

          <div class="panel-body">
            <!-- 证据图 -->
            <div class="evidence-img">
              <div class="img-tabs">
                <button :class="{ on: imgMode === 'image' }" @click="imgMode = 'image'">原始抓拍</button>
                <button :class="{ on: imgMode === 'overlay' }" @click="imgMode = 'overlay'">
                  {{ detail.evidence.overlay_kind === 'det' ? '检测标注' : '分割叠加' }}
                </button>
              </div>
              <div class="img-box">
                <img
                  v-if="!imgError && (imgMode === 'image' ? detail.evidence.image : detail.evidence.overlay)"
                  :src="imgUrl(imgMode === 'image' ? detail.evidence.image : detail.evidence.overlay)"
                  @error="imgError = true" alt="告警证据图"
                />
                <div v-else class="img-missing">
                  <el-icon><Picture /></el-icon>
                  <span>图像不可用</span>
                </div>
              </div>
            </div>

            <!-- 规则命中 -->
            <div class="block">
              <div class="block-title">规则命中</div>
              <div class="rule-hit">
                <div class="rh-row">
                  <span class="rh-label">告警内容</span>
                  <span class="rh-value strong">{{ detail.alarm.message }}</span>
                </div>
                <div class="rh-row" v-if="detail.rule_hit.value !== null && detail.rule_hit.value !== undefined">
                  <span class="rh-label">{{ detail.rule_hit.field_label || '实测值' }}</span>
                  <span class="rh-value">
                    <b :class="{ danger: detail.alarm.level === 'danger' }">{{ detail.rule_hit.value }}</b>
                    <span class="rh-op">{{ detail.rule_hit.condition_label }} 阈值 {{ detail.rule_hit.threshold }}</span>
                    <span class="rh-dev" v-if="detail.rule_hit.deviation !== null">
                      （偏离 {{ detail.rule_hit.deviation > 0 ? '+' : '' }}{{ detail.rule_hit.deviation }}）
                    </span>
                  </span>
                </div>
                <div class="rh-row">
                  <span class="rh-label">触发时间</span>
                  <span class="rh-value">{{ fmtTime(detail.alarm.created_at) }}</span>
                </div>
                <div class="rh-row" v-if="detail.record">
                  <span class="rh-label">分析记录</span>
                  <span class="rh-value">
                    #{{ detail.record.id }} · 模型 {{ detail.record.model_version }}
                  </span>
                </div>
              </div>
            </div>

            <!-- 根因联动分析（P1）：把品位告警放回「磨矿 → 浮选 → 回水」链上 -->
            <div class="block rc-block" v-if="rootCause && rootCause.supported">
              <div class="block-title">
                根因联动分析
                <span class="block-note" v-if="rootCause.candidates.length">
                  {{ rootCause.candidates.length }} 项候选 · 按可能性排序
                </span>
                <span class="block-note" v-else>未发现显著异常</span>
              </div>

              <!-- 溯源链路：三个工段最近一条样本的时间距离 -->
              <div class="rc-sources">
                <span class="rc-src" v-for="(s, i) in rootCause.sources" :key="s.key">
                  <i class="rc-join" v-if="i">→</i>
                  <i class="rc-dot" :class="srcMeta(s).cls"></i>
                  <span class="rc-src-name">{{ s.label }}</span>
                  <em class="rc-src-lag">{{ srcMeta(s).text }}</em>
                </span>
              </div>

              <!-- 候选原因 -->
              <div class="rc-item" v-for="c in rootCause.candidates" :key="c.code"
                   :class="{ open: expandedRc === c.code }" @click="toggleRc(c.code)">
                <div class="rc-line1">
                  <span class="rc-rank">{{ c.rank }}</span>
                  <span class="rc-title">{{ c.title }}</span>
                  <span class="rc-cat" :class="c.category">{{ CAT_LABELS[c.category] || c.category }}</span>
                  <span class="rc-conf" :class="'lv-' + (CONF_CLS[c.confidence_label] || 'low')">{{ c.confidence_label }}</span>
                </div>
                <div class="rc-bar">
                  <i :style="{ width: Math.round(c.confidence * 100) + '%' }"></i>
                  <span class="rc-bar-num">{{ c.confidence.toFixed(2) }}</span>
                </div>
                <div class="rc-ev">
                  <span class="rc-ev-item" v-for="(e, ei) in c.evidence" :key="ei">
                    <i class="rc-st" v-if="e.station">{{ e.station }}</i>
                    {{ e.label }} <b>{{ e.value }}{{ e.unit || '' }}</b>
                    <em class="rc-dev" v-if="e.deviation_pct !== undefined && e.deviation_pct !== null">
                      偏离 {{ e.deviation_pct > 0 ? '+' : '' }}{{ e.deviation_pct }}%
                    </em>
                    <em class="rc-dev" v-else-if="e.z !== undefined && e.z !== null">z={{ e.z }}</em>
                    <span class="rc-base" v-else-if="e.reference !== undefined && e.reference !== null">
                      基线 {{ e.reference }}
                    </span>
                  </span>
                </div>

                <!-- 展开：机理 / 建议 / 证据时间 / 评分构成 -->
                <div class="rc-detail" v-if="expandedRc === c.code" @click.stop>
                  <div class="rc-kv">
                    <span class="rc-k">工艺机理</span>
                    <span class="rc-v">{{ c.mechanism }}</span>
                  </div>
                  <div class="rc-kv">
                    <span class="rc-k">建议措施</span>
                    <span class="rc-v act">{{ c.suggested_action }}</span>
                  </div>
                  <div class="rc-kv">
                    <span class="rc-k">采样时间</span>
                    <span class="rc-v">
                      <template v-for="(e, ei) in c.evidence" :key="ei">
                        <template v-if="ei"> · </template>{{ e.sample_at ? fmtTime(e.sample_at) : '—' }}（距 {{ Math.round(e.lag_minutes) }} 分钟）
                      </template>
                    </span>
                  </div>
                  <div class="rc-formula">
                    评分构成：先验 {{ c.prior }} × 偏离强度 {{ c.strength }} × 时间衰减 {{ c.time_decay }}
                  </div>
                </div>
              </div>

              <!-- 无结论 -->
              <div class="rc-empty" v-if="!rootCause.candidates.length">{{ rootCause.conclusion }}</div>

              <!-- 数据缺口：如实说明为什么没归因出来 -->
              <div class="rc-missing" v-if="rootCause.missing.length">
                <div class="rc-missing-item" v-for="(m, mi) in rootCause.missing" :key="mi">
                  <i class="rc-warn">!</i><span>{{ m }}</span>
                </div>
              </div>

              <div class="rc-caveat">本分析基于统计关联，不作为因果结论</div>
            </div>

            <!-- 同帧其他规则 -->
            <div class="block" v-if="detail.siblings.length">
              <div class="block-title">同一帧的其他告警 <span class="block-note">{{ detail.siblings.length }} 条</span></div>
              <div class="sib" v-for="s in detail.siblings" :key="s.id" @click="selectAlarm(s.id)">
                <span class="ev-level" :class="s.level">{{ s.level_label }}</span>
                <span class="sib-rule">{{ s.rule_name }}</span>
                <span class="sib-status" :style="{ color: statusMeta(s.status).color }">{{ s.status_label }}</span>
                <el-icon class="sib-go"><ArrowRight /></el-icon>
              </div>
            </div>

            <!-- 浮选：特征与历史基线对比 -->
            <div class="block" v-if="detail.evidence.kind === 'foam' && detail.evidence.features.items.length">
              <div class="block-title">
                泡沫特征与历史基线对比
                <span class="block-note">
                  TOP {{ Math.min(8, detail.evidence.features.items.length) }} 偏离
                  <template v-if="detail.evidence.features.baseline_n">
                    · 基线 n={{ detail.evidence.features.baseline_n }}
                  </template>
                </span>
              </div>
              <div class="feat-note">
                以基线均值为中心，偏移量 = 偏离的标准差倍数（|z| ≥ 1.5 显著）
              </div>
              <div class="feat" v-for="f in detail.evidence.features.items.slice(0, 8)" :key="f.key">
                <span class="feat-name">{{ f.label }}</span>
                <span class="feat-val">
                  {{ f.value }}
                  <em v-if="f.mean !== undefined">/ 基线 {{ f.mean.toFixed(2) }}±{{ f.std.toFixed(2) }}</em>
                </span>
                <span class="feat-bar">
                  <i class="bar-axis"></i>
                  <i class="bar-fill" :style="devBarStyle(f.z, f.severity)"></i>
                </span>
                <span class="feat-z" :class="f.severity">
                  {{ f.z > 0 ? '+' : '' }}{{ f.z }}
                  <em v-if="f.percentile !== undefined">· {{ f.percentile }}%</em>
                </span>
              </div>
              <div class="feat-summary">
                显著偏离 <b>{{ detail.evidence.features.significant }}</b> / {{ detail.evidence.features.items.length }} 项
                <template v-if="detail.evidence.features.excluded_bad">
                  <span class="dot">·</span>
                  基线已排除 {{ detail.evidence.features.excluded_bad }} 条口径异常的历史记录
                </template>
              </div>
            </div>

            <!-- 安全：风险快照 -->
            <div class="block" v-if="detail.evidence.risk">
              <div class="block-title">风险识别</div>
              <div class="risk-grid">
                <div class="risk-item">
                  <span class="risk-label">风险等级</span>
                  <span class="risk-value danger">{{ detail.evidence.risk.risk_level || '—' }}</span>
                </div>
                <div class="risk-item">
                  <span class="risk-label">风险评分</span>
                  <span class="risk-value">{{ detail.evidence.risk.risk_score ?? '—' }}</span>
                </div>
                <div class="risk-item">
                  <span class="risk-label">置信度</span>
                  <span class="risk-value">
                    {{ detail.evidence.risk.confidence != null ? (detail.evidence.risk.confidence * 100).toFixed(0) + '%' : '—' }}
                  </span>
                </div>
                <div class="risk-item">
                  <span class="risk-label">相机位</span>
                  <span class="risk-value">{{ detail.evidence.risk.camera_id || '—' }}</span>
                </div>
              </div>
              <div class="risk-action" v-if="detail.evidence.risk.action">{{ detail.evidence.risk.action }}</div>
            </div>

            <!-- 处置闭环 -->
            <div class="block handle-block">
              <div class="block-title">
                处置闭环
                <span class="block-note" v-if="detail.alarm.handled_by">
                  {{ detail.alarm.handled_by }} · {{ fmtTime(detail.alarm.handled_at) }}
                </span>
              </div>

              <!-- 已填写的处置内容 -->
              <div class="handled-info" v-if="detail.alarm.handled_note || detail.alarm.action_taken">
                <div class="hi-row" v-if="detail.alarm.handled_note">
                  <span class="hi-label">处理意见</span><span class="hi-text">{{ detail.alarm.handled_note }}</span>
                </div>
                <div class="hi-row" v-if="detail.alarm.action_taken">
                  <span class="hi-label">处置措施</span><span class="hi-text">{{ detail.alarm.action_taken }}</span>
                </div>
              </div>

              <!-- 审计流水 -->
              <div class="trail" v-if="detail.actions.length">
                <div class="trail-item" v-for="x in detail.actions" :key="x.id">
                  <span class="trail-dot"></span>
                  <span class="trail-time">{{ fmtTime(x.created_at) }}</span>
                  <span class="trail-what">{{ x.operator }} → {{ statusMeta(x.to_status).label }}</span>
                  <span class="trail-note" v-if="x.note">{{ x.note }}</span>
                </div>
              </div>

              <!-- 处置表单 -->
              <div class="handle-form">
                <div class="hf-actions">
                  <button
                    v-for="a in availableActions" :key="a.key"
                    class="hf-btn" :class="{ on: handleForm.action === a.key, danger: a.key === 'false_positive' }"
                    @click="handleForm.action = a.key"
                  >{{ a.label }}</button>
                </div>
                <div class="hf-row">
                  <el-select v-model="handleForm.operator" class="operator-select" placeholder="处理人"
                             size="small" filterable allow-create default-first-option style="width:150px">
                    <el-option v-for="p in POSITIONS" :key="p" :label="p" :value="p" />
                  </el-select>
                  <el-input v-model="handleForm.action_taken" placeholder="处置措施（选填）" size="small" />
                </div>
                <el-input
                  v-model="handleForm.note" type="textarea" :rows="2"
                  :placeholder="noteRequired ? '处理意见（必填）' : '处理意见（选填）'"
                />
                <div class="hf-submit">
                  <span class="hf-tip" v-if="!handleForm.action">请选择处置动作</span>
                  <span class="hf-tip warn" v-else-if="noteRequired && !handleForm.note.trim()">该动作必须填写处理意见</span>
                  <span class="hf-tip" v-else>将记录处置人与时间，写入审计流水</span>
                  <el-button type="primary" size="small" :loading="submitting" @click="submitAction">
                    提交处置
                  </el-button>
                </div>
              </div>
            </div>
          </div>
        </template>
        <div v-else class="empty">选择左侧事件查看证据详情</div>
      </div>
    </div>

    <!-- ===== 批量处置弹窗 ===== -->
    <el-dialog
      v-model="showBatchDialog"
      :title="`批量处置 — ${checkedKeys.length} 个事件 · ${checkedAlarmIds.length} 条告警`"
      width="480px"
    >
      <div class="batch-dlg">
        <div class="bd-label">处置动作</div>
        <div class="hf-actions">
          <button
            v-for="a in batchActionOptions" :key="a.key"
            class="hf-btn" :class="{ on: batchForm.action === a.key, danger: a.key === 'false_positive' }"
            @click="batchForm.action = a.key"
          >{{ a.label }}</button>
        </div>
        <div class="bd-label">处理人</div>
        <div class="batch-row">
          <el-select v-model="batchForm.operator" placeholder="处理人" size="small"
                     filterable allow-create default-first-option style="width:150px">
            <el-option v-for="p in POSITIONS" :key="p" :label="p" :value="p" />
          </el-select>
          <span class="batch-hint">逐条写入审计流水，不满足条件的会单独列出原因</span>
        </div>
        <div class="bd-label">处理意见</div>
        <el-input
          v-model="batchForm.note" type="textarea" :rows="2"
          :placeholder="batchNoteRequired ? '处理意见（必填）' : '处理意见（选填）'"
        />
      </div>
      <template #footer>
        <el-button @click="showBatchDialog = false">取消</el-button>
        <el-button type="primary" :loading="batchSubmitting" @click="submitBatch">
          提交处置
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page { height: 100%; display: flex; flex-direction: column; gap: var(--space-3); }

/* ---- 顶部 ---- */
.page-head { display: flex; align-items: center; justify-content: space-between; flex: 0 0 auto; }
.head-actions { display: flex; align-items: center; gap: 10px; }
.auto-hint { font-size: var(--text-xs); color: var(--text-3); }
.auto-hint.paused { color: var(--color-warning); }
.head-left { display: flex; align-items: baseline; gap: var(--space-3); }
h1 { font-size: var(--text-xl); font-weight: var(--weight-semibold); color: var(--text-1); margin: 0; }
.head-sub { font-size: var(--text-sm); color: var(--text-3); }
.head-sub b { color: var(--color-primary); font-weight: var(--weight-medium); }

/* ---- KPI 带 ---- */
.kpi-band {
  flex: 0 0 auto;
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr)) minmax(0, 1.3fr);
  gap: 1px;
  background: var(--border-1);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.kpi { background: var(--bg-1); padding: 10px 14px; }
.kpi-value { font-size: 22px; font-weight: var(--weight-semibold); line-height: 1.15; color: var(--text-1); }
.kpi-label { font-size: var(--text-sm); color: var(--text-2); margin-top: 2px; }
.kpi-sub { font-size: var(--text-xs); color: var(--text-3); margin-top: 1px; }

/* ---- 筛选 ---- */
.toolbar { flex: 0 0 auto; display: flex; align-items: center; gap: var(--space-2); }
.toolbar-count { margin-left: auto; font-size: var(--text-sm); color: var(--text-3); }

/* ---- 主区 ---- */
.main {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1.02fr) minmax(0, 1fr);
  gap: var(--space-3);
}
.panel {
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.panel-head {
  flex: 0 0 auto;
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px var(--space-4);
  border-bottom: 1px solid var(--border-2);
}
.panel-title { font-size: 15px; font-weight: var(--weight-semibold); color: var(--text-1); }
.panel-hint { font-size: var(--text-xs); color: var(--text-3); }
.panel-body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: var(--space-2) var(--space-3); }
.panel-foot {
  flex: 0 0 auto; display: flex; justify-content: flex-end;
  padding: 8px var(--space-3); border-top: 1px solid var(--border-2);
}
.empty { color: var(--text-3); font-size: var(--text-sm); text-align: center; padding: 40px 0; }

/* ---- 事件卡 ---- */
.event {
  display: flex; gap: 10px;
  padding: 9px 10px;
  border-radius: var(--radius);
  border-left: 3px solid transparent;
  background: var(--bg-3);
  cursor: pointer;
  margin-bottom: 6px;
  transition: background var(--transition);
}
.event:hover { background: #eaecef; }
.event.active { background: var(--color-primary-bg); border-left-color: var(--color-primary); }
.event.danger { border-left-color: var(--color-danger); }
.event.danger.active { border-left-color: var(--color-danger); }
.ev-thumb {
  flex: 0 0 auto; width: 42px; height: 42px; border-radius: var(--radius-sm);
  overflow: hidden; background: var(--bg-1);
  display: flex; align-items: center; justify-content: center;
  color: var(--text-4); border: 1px solid var(--border-1);
}
.ev-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.ev-main { flex: 1 1 auto; min-width: 0; }
.ev-line1 { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ev-level {
  font-size: 11px; padding: 0 5px; border-radius: var(--radius-sm); line-height: 16px;
  font-weight: var(--weight-medium); flex: 0 0 auto;
}
.ev-level.danger { color: #fff; background: var(--color-danger); }
.ev-level.warning { color: #fff; background: var(--color-warning); }
.ev-rule { font-size: var(--text-sm); font-weight: var(--weight-medium); color: var(--text-1); }
.ev-count {
  font-size: 11px; color: var(--color-primary); background: var(--color-primary-bg);
  padding: 0 5px; border-radius: var(--radius-sm); line-height: 16px;
}
.ev-status { margin-left: auto; font-size: var(--text-xs); font-weight: var(--weight-medium); }
.ev-msg {
  font-size: var(--text-sm); color: var(--text-2); margin-top: 2px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ev-line3 { display: flex; align-items: center; gap: 4px; font-size: var(--text-xs); color: var(--text-3); margin-top: 3px; }
.dot { color: var(--text-4); }
.ev-todo { color: var(--color-danger); }

/* ---- 详情 ---- */
.detail-head { gap: var(--space-2); }
.dh-left { display: flex; align-items: center; gap: 6px; }
.dh-rule { font-size: 15px; font-weight: var(--weight-semibold); color: var(--text-1); }
.dh-status { font-size: var(--text-sm); font-weight: var(--weight-medium); }

.evidence-img { margin-bottom: var(--space-3); }
.img-tabs { display: flex; gap: 6px; margin-bottom: 6px; }
.img-tabs button {
  font-size: var(--text-xs); padding: 3px 10px; border-radius: var(--radius-sm);
  border: 1px solid var(--border-1); background: var(--bg-1); color: var(--text-2);
  cursor: pointer; transition: all var(--transition); font-family: inherit;
}
.img-tabs button:hover { border-color: var(--text-4); }
.img-tabs button.on { background: var(--color-primary-bg); border-color: var(--color-primary); color: var(--color-primary); }
.img-box {
  height: 190px; background: var(--bg-3); border-radius: var(--radius);
  display: flex; align-items: center; justify-content: center; overflow: hidden;
}
.img-box img { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
.img-missing { display: flex; flex-direction: column; align-items: center; gap: 4px; color: var(--text-3); font-size: var(--text-xs); }
.img-missing .el-icon { font-size: 22px; color: var(--text-4); }

.block { margin-bottom: var(--space-4); }
.block:last-child { margin-bottom: 0; }
.block-title {
  font-size: var(--text-sm); font-weight: var(--weight-semibold); color: var(--text-1);
  margin-bottom: var(--space-2); display: flex; align-items: baseline; gap: 8px;
}
.block-note { font-size: var(--text-xs); color: var(--text-3); font-weight: var(--weight-normal); }

/* 规则命中 */
.rule-hit { background: var(--bg-3); border-radius: var(--radius); padding: 10px 12px; }
.rh-row { display: flex; gap: 10px; font-size: var(--text-sm); padding: 2px 0; }
.rh-label { flex: 0 0 66px; color: var(--text-3); }
.rh-value { color: var(--text-1); flex: 1 1 auto; }
.rh-value.strong { font-weight: var(--weight-medium); }
.rh-value b { font-size: 15px; }
.rh-value b.danger { color: var(--color-danger); }
.rh-op { color: var(--text-3); margin-left: 6px; }
.rh-dev { color: var(--color-danger); }

/* 同帧兄弟 */
.sib {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; border-radius: var(--radius); background: var(--bg-3);
  margin-bottom: 4px; cursor: pointer; font-size: var(--text-sm);
  transition: background var(--transition);
}
.sib:hover { background: #eaecef; }
.sib-rule { color: var(--text-1); flex: 1 1 auto; }
.sib-status { font-size: var(--text-xs); }
.sib-go { color: var(--text-4); font-size: 12px; }

/* 特征对比 */
.feat-note { font-size: var(--text-xs); color: var(--text-3); margin-bottom: 6px; }
.feat { display: flex; align-items: center; gap: 8px; font-size: var(--text-xs); height: 22px; }
.feat-name { flex: 0 0 84px; color: var(--text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.feat-val { flex: 0 0 150px; color: var(--text-1); font-variant-numeric: tabular-nums; }
.feat-val em { color: var(--text-3); font-style: normal; }
.feat-bar { position: relative; flex: 1 1 auto; height: 8px; background: var(--bg-3); border-radius: 4px; overflow: hidden; }
.bar-axis { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: var(--text-4); }
.bar-fill { position: absolute; top: 1px; bottom: 1px; border-radius: 3px; }
.feat-z { flex: 0 0 74px; text-align: right; color: var(--text-2); font-variant-numeric: tabular-nums; }
.feat-z em { color: var(--text-3); font-style: normal; }
.feat-z.high { color: var(--color-danger); font-weight: var(--weight-medium); }
.feat-z.medium { color: var(--color-warning); }
.feat-summary { font-size: var(--text-xs); color: var(--text-3); margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--border-2); }

/* 风险快照 */
.risk-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; background: var(--border-1); border-radius: var(--radius); overflow: hidden; }
.risk-item { background: var(--bg-3); padding: 8px 10px; }
.risk-label { display: block; font-size: var(--text-xs); color: var(--text-3); }
.risk-value { font-size: var(--text-lg); font-weight: var(--weight-semibold); color: var(--text-1); }
.risk-value.danger { color: var(--color-danger); }
.risk-action {
  margin-top: 8px; font-size: var(--text-sm); color: var(--text-2);
  background: var(--color-danger-light, #ffece8); border-radius: var(--radius); padding: 8px 10px;
}

/* 处置区 */
.handle-block { border-top: 1px solid var(--border-2); padding-top: var(--space-3); }
.handled-info { background: var(--bg-3); border-radius: var(--radius); padding: 8px 10px; margin-bottom: 8px; }
.hi-row { display: flex; gap: 8px; font-size: var(--text-sm); padding: 2px 0; }
.hi-label { flex: 0 0 56px; color: var(--text-3); }
.hi-text { color: var(--text-1); flex: 1 1 auto; }
.trail { margin-bottom: 10px; }
.trail-item { display: flex; align-items: baseline; gap: 6px; font-size: var(--text-xs); color: var(--text-3); padding: 2px 0; }
.trail-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--color-primary); flex: 0 0 auto; }
.trail-time { flex: 0 0 auto; font-variant-numeric: tabular-nums; }
.trail-what { color: var(--text-2); }
.trail-note { color: var(--text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.handle-form { display: flex; flex-direction: column; gap: 8px; }
.hf-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.hf-btn {
  font-size: var(--text-sm); padding: 5px 12px; border-radius: var(--radius);
  border: 1px solid var(--border-1); background: var(--bg-1); color: var(--text-2);
  cursor: pointer; font-family: inherit; transition: all var(--transition);
}
.hf-btn:hover { border-color: var(--color-primary); color: var(--color-primary); }
.hf-btn.on { background: var(--color-primary); border-color: var(--color-primary); color: #fff; }
.hf-btn.danger.on { background: var(--color-danger); border-color: var(--color-danger); }
.hf-row { display: flex; gap: 8px; }
.hf-submit { display: flex; align-items: center; justify-content: space-between; }
.hf-tip { font-size: var(--text-xs); color: var(--text-3); }
.hf-tip.warn { color: var(--color-warning); }

/* ---- 批量选择与批量处置 ---- */
.ev-check { flex: 0 0 auto; display: flex; align-items: center; margin-left: -4px; }
.batch-bar {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 6px 10px; border-bottom: 1px solid var(--border-2);
  background: var(--color-primary-bg); font-size: var(--text-sm);
}
.bb-info { color: var(--text-2); }
.bb-info b { color: var(--color-primary); }
.bb-actions { display: flex; gap: 6px; }
.bb-btn {
  font-size: var(--text-xs); padding: 3px 10px; border-radius: var(--radius-sm);
  border: 1px solid var(--border-1); background: var(--bg-1); color: var(--text-2);
  cursor: pointer; font-family: inherit; transition: all var(--transition);
}
.bb-btn:hover { border-color: var(--color-primary); color: var(--color-primary); }
.bb-btn.primary { background: var(--color-primary); border-color: var(--color-primary); color: #fff; }
.bb-btn.primary:hover { color: #fff; opacity: 0.9; }
/* 批量处置弹窗 */
.batch-dlg .bd-label { font-size: var(--text-xs); color: var(--text-3); margin: 10px 0 6px; }
.batch-dlg .bd-label:first-child { margin-top: 0; }
.batch-row { display: flex; align-items: center; gap: 10px; }
.batch-hint { font-size: var(--text-xs); color: var(--text-3); }
/* ---- 根因联动分析 ---- */
.rc-block { border-top: 1px solid var(--border-2); padding-top: 12px; }
.rc-sources {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  background: var(--bg-3); border-radius: var(--radius); padding: 6px 10px;
  margin-bottom: 8px; font-size: var(--text-xs);
}
.rc-src { display: inline-flex; align-items: center; gap: 4px; }
.rc-join { color: var(--text-4); font-style: normal; margin: 0 2px; }
.rc-dot { width: 6px; height: 6px; border-radius: 50%; flex: 0 0 auto; background: var(--text-4); }
.rc-dot.on { background: #00b42a; }
.rc-dot.far { background: var(--color-warning); }
.rc-dot.off { background: var(--text-4); }
.rc-src-name { color: var(--text-2); }
.rc-src-lag { color: var(--text-3); font-style: normal; }

.rc-item {
  border: 1px solid var(--border-1); border-radius: var(--radius);
  padding: 8px 10px; margin-bottom: 6px; cursor: pointer;
  transition: border-color .18s, background .18s;
}
.rc-item:hover { border-color: var(--text-4); }
.rc-item.open { border-color: var(--color-primary); background: var(--color-primary-bg); }
.rc-line1 { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.rc-rank {
  flex: 0 0 auto; width: 16px; height: 16px; border-radius: 50%;
  background: var(--text-1); color: #fff; font-size: 10px;
  display: inline-flex; align-items: center; justify-content: center;
}
.rc-title { flex: 1 1 auto; font-size: var(--text-sm); font-weight: var(--weight-medium); color: var(--text-1); }
.rc-cat {
  flex: 0 0 auto; font-size: 10px; padding: 1px 6px; border-radius: var(--radius-sm);
  background: var(--bg-1); color: var(--text-3); border: 1px solid var(--border-1);
}
.rc-cat.grinding { color: #7d5fff; border-color: #d9cfff; background: #f5f2ff; }
.rc-cat.flotation { color: var(--color-primary); border-color: #c7d8ff; background: var(--color-primary-bg); }
.rc-cat.water { color: #0e9bd6; border-color: #c2e8f7; background: #eaf8fe; }
.rc-conf { flex: 0 0 auto; font-size: 11px; font-weight: var(--weight-medium); }
.rc-conf.lv-high { color: var(--color-danger); }
.rc-conf.lv-mid { color: var(--color-warning); }
.rc-conf.lv-low { color: var(--text-3); }

.rc-bar { position: relative; height: 6px; background: var(--bg-3); border-radius: 3px; margin-bottom: 8px; }
.rc-bar i { display: block; height: 100%; border-radius: 3px; background: var(--color-primary); transition: width .3s; }
.rc-bar-num { position: absolute; right: 0; top: -13px; font-size: 10px; color: var(--text-3); font-variant-numeric: tabular-nums; }

.rc-ev { display: flex; flex-wrap: wrap; gap: 3px 12px; font-size: var(--text-xs); color: var(--text-2); }
.rc-ev-item b { color: var(--text-1); font-weight: var(--weight-medium); }
.rc-ev-item .rc-dev { color: var(--color-danger); font-style: normal; margin-left: 3px; }
.rc-ev-item .rc-base { color: var(--text-4); margin-left: 3px; }
.rc-st { color: #0e9bd6; font-style: normal; font-weight: var(--weight-medium); margin-right: 3px; }

.rc-detail { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border-1); display: flex; flex-direction: column; gap: 5px; }
.rc-kv { display: flex; gap: 8px; font-size: var(--text-xs); }
.rc-k { flex: 0 0 52px; color: var(--text-3); }
.rc-v { flex: 1 1 auto; color: var(--text-2); line-height: 1.65; }
.rc-v.act { color: var(--color-primary); }
.rc-formula { font-size: 10px; color: var(--text-4); font-variant-numeric: tabular-nums; }

.rc-empty { font-size: var(--text-xs); color: var(--text-3); background: var(--bg-3); border-radius: var(--radius); padding: 8px 10px; line-height: 1.6; }
.rc-missing { margin-top: 8px; display: flex; flex-direction: column; gap: 3px; }
.rc-missing-item { display: flex; align-items: flex-start; gap: 5px; font-size: var(--text-xs); color: var(--text-3); line-height: 1.5; }
.rc-warn {
  flex: 0 0 auto; width: 13px; height: 13px; border-radius: 50%; margin-top: 1px;
  background: var(--color-warning); color: #fff; font-size: 9px; font-style: normal;
  display: inline-flex; align-items: center; justify-content: center; font-weight: bold;
}
.rc-caveat { margin-top: 8px; padding-top: 6px; border-top: 1px solid var(--border-2); font-size: 10px; color: var(--text-4); }
</style>
