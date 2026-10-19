<script setup>
import { cssVar } from '../utils/chartColor'

/**
 * 告警中心 v2 — P0（证据抽屉 + 处置闭环 + 帧级聚合）
 *
 * 三件事：
 *   1. 帧级聚合：同一帧图像触发的多条规则合并为一个「事件」
 *   2. 证据链：原图 / 分割叠加 + 规则命中 + 特征与历史基线对比
 *   3. 处置闭环：待处理 → 已确认 → 处理中 → 已闭环 / 误报，写审计流水
 */
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useWebSocket } from '../api/ws'
import PageShell from '../components/PageShell.vue'
import StatCard from '../components/StatCard.vue'

const router = useRouter()

/** 去设置页接飞书机器人（带 focus 参数，对方会滚到面板并聚焦地址框）。
 *  ★ 为什么放在告警页：告警"能不能推到群里"是这一页的诉求，
 *    入口藏在「设置 → 页面最底部」等于没有 —— 用户不会去找。 */
function goFeishu() {
  router.push({ path: '/settings', query: { focus: 'feishu' } })
}

// ---- 字典 ----
const POSITIONS = ['浮选班长', '磨矿班长', '值班调度', '安全员', '工艺工程师', '设备点检员']

const STATUS_META = {
  new:            { label: '待处理', color: cssVar('--color-danger'),  tag: 'danger' },
  acknowledged:   { label: '已确认', color: cssVar('--color-warning'), tag: 'warning' },
  in_progress:    { label: '处理中', color: cssVar('--color-primary'), tag: 'primary' },
  closed:         { label: '已闭环', color: cssVar('--color-success'), tag: 'success' },
  false_positive: { label: '误报',   color: cssVar('--text-3'),        tag: 'info' },
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
const featExpanded = ref(false)       // 泡沫特征：默认只给 4 项（支撑证据，不跟根因争主次）

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

// ---- 实时推送（2026-10-19）----
// ★ 改这个页面的原因：以前只有 30 秒轮询，分析刚产生的告警要等半分钟才出现；
//   而屏幕上弹出来的却是后端随机编的假告警。现在真告警一产生就推过来，
//   轮询降级成"WS 断开时的兜底"，不再承担主要更新职责。
const pendingNew = ref(0)        // 处置中被压住的新告警条数（不打断用户操作）

// ⚠️ useWebSocket 必须在 setup 同步阶段调用（内部注册 onUnmounted）
const { status: alarmsWs } = useWebSocket('alarms', (msg) => {
  if (msg.event !== 'alarm.created' && msg.event !== 'alarm.updated') return

  // 处置中（有勾选 / 批量弹窗开着）绝不刷新：会清空勾选，把用户选到一半的操作吞掉
  if (autoPaused.value) {
    if (msg.event === 'alarm.created') pendingNew.value += 1
    return
  }
  pendingNew.value = 0
  loadGroups(true, { silent: true }).then(() => {
    // 别人处置了「我正看着的这条」→ 右侧详情也得跟着变，
    // 否则会出现"列表显示已闭环、右边还挂着待处理按钮"的分裂画面
    if (msg.event === 'alarm.updated' && detail.value?.alarm?.id === msg.data?.id) {
      const cur = groups.value.find((g) => g.group_key === selectedKey.value)
      if (cur) selectGroup(cur)
    }
  })
})

/** 用户主动"查看新告警"：放弃当前处置上下文，重新拉一次列表 */
function flushPending() {
  pendingNew.value = 0
  loadGroups(false)
}

// ---- 计算 ----
const page = computed(() => Math.floor(filters.value.offset / filters.value.limit) + 1)
const curStatus = computed(() => detail.value?.alarm?.status || 'new')
const availableActions = computed(() => ACTIONS_BY_STATUS[curStatus.value] || [])
const noteRequired = computed(() => ['start', 'close', 'false_positive'].includes(handleForm.value.action))
const evidenceSrc = computed(() => {
  const ev = detail.value?.evidence
  if (!ev) return ''
  return imgUrl(imgMode.value === 'overlay' ? ev.overlay : ev.image)
})
// 泡沫特征只占 4 行：它是根因的旁证，不该比根因还长
const shownFeatures = computed(() => {
  const items = detail.value?.evidence?.features?.items || []
  return featExpanded.value ? items.slice(0, 8) : items.slice(0, 4)
})
// 「超窗降权」已经在来源格里写了一次，缺口里不再重复同一句
const missingNotes = computed(() =>
  (rootCause.value?.missing || []).filter((m) => !m.includes('超出建议窗口'))
)

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
    // ★ 头号候选默认展开：机理 + 建议措施是这个模块的全部价值，
    //   藏在一次点击后面，等于"特色功能"在默认视野里不存在。
    expandedRc.value = rootCause.value?.candidates?.[0]?.code || ''
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
  if (!s.available) {
    return { cls: 'off', text: '无数据', tag: '未接入', tip: '告警前窗口内没有该工段的记录，无法比对' }
  }
  if (s.in_window) return { cls: 'on', text: lagText(s.lag_minutes), tag: '窗口内', tip: '' }
  const hours = (s.lag_minutes / 60).toFixed(1)
  return {
    cls: 'far',
    text: lagText(s.lag_minutes),
    tag: '超窗·降权',
    tip: `最近样本距告警 ${hours} 小时，超出建议窗口，已按时间衰减降权`,
  }
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
  const color = severity === 'high' ? cssVar('--color-danger')
    : severity === 'medium' ? cssVar('--color-warning') : cssVar('--text-4')
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
  <PageShell
    title="告警"
    :sub="`${stats.total} 条告警聚合为 ${stats.events || 0} 个事件 · 严重 ${stats.danger} / 预警 ${stats.warning}`"
  >
    <template #actions>
      <!-- 实时状态：WS 通了就别再让人盯着"30 秒轮询"那句话 -->
      <span class="live-hint" :class="alarmsWs">
        <i class="live-dot" />
        <template v-if="alarmsWs === 'connected'">实时推送</template>
        <template v-else>重连中 · 轮询兜底</template>
      </span>
      <span class="auto-hint" :class="{ paused: autoPaused }">
        <template v-if="autoPaused">
          刷新已暂停（处置中）
          <button v-if="pendingNew" class="pending-btn" @click="flushPending">
            {{ pendingNew }} 条新告警
          </button>
        </template>
        <template v-else-if="lastUpdate">最后更新 {{ lastUpdate }}</template>
      </span>
      <el-button size="small" @click="goFeishu">
        <el-icon><Promotion /></el-icon>&nbsp;接入飞书
      </el-button>
      <el-button size="small" :loading="loading" @click="loadGroups(true)">
        <el-icon><Refresh /></el-icon>&nbsp;刷新
      </el-button>
    </template>

    <!-- 四格 KPI（规范：固定 4 格）。
         ★ 原来是 6 格"数据带"（五段状态 + 响应时长），且用 1px 缝隙拼成整块；
           现在统一成 4 张 StatCard：已确认并入"处理中"副行、误报并入"已闭环"副行，
           数字一个没丢，只是不再各占一格。 -->
    <template #kpi>
      <StatCard
        label="待处理"
        :value="stats.new"
        :foot="`${stage.pct(stats.new)}% 未处置`"
        tone="danger"
      />
      <StatCard
        label="处理中"
        :value="stats.in_progress"
        :foot="`已确认 ${stats.acknowledged} 条`"
        tone="primary"
      />
      <StatCard
        label="已闭环"
        :value="stats.closed"
        :foot="`闭环率 ${stage.pct(stats.closed)}% · 误报 ${stats.false_positive}`"
        tone="success"
      />
      <StatCard
        label="响应时长中位数"
        :value="mttaText"
        :foot="mttaSub"
      />
    </template>

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
                <!-- ★ 2026-10-19：外部系统推来的告警必须标出是谁报的。
                     接入 DCS / MES / 视频盒子之后，如果界面上所有告警长得一样，
                     出了问题就说不清"这条到底是 SuiEye 算的还是别人推的"。 -->
                <span
                  v-if="g.primary.source === 'external'"
                  class="ev-origin"
                  :title="`外部系统推送：${g.primary.origin}`"
                >{{ g.primary.origin }}</span>
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
              <span
                v-if="detail.alarm.source === 'external'"
                class="ev-origin"
                :title="`外部系统推送：${detail.alarm.origin}`"
              >{{ detail.alarm.origin }}</span>
            </div>
            <span class="dh-status" :style="{ color: statusMeta(detail.alarm.status).color }">
              {{ detail.alarm.status_label }}
            </span>
          </div>

          <div class="panel-body">
            <!-- ① 触发快照：证据图 / 实测值 / 时间 / 记录压成一条（原「规则命中」四行整块并入） -->
            <div class="snap">
              <div class="snap-thumb" v-if="detail.evidence.image">
                <el-image
                  v-if="!imgError && evidenceSrc"
                  :src="evidenceSrc"
                  :preview-src-list="[evidenceSrc]"
                  preview-teleported
                  fit="cover"
                  class="snap-img"
                  @error="imgError = true"
                />
                <div v-else class="img-missing">
                  <el-icon><Picture /></el-icon>
                  <span>图像不可用</span>
                </div>
                <div class="img-tabs" v-if="detail.evidence.overlay">
                  <button :class="{ on: imgMode === 'image' }" @click="imgMode = 'image'">原始</button>
                  <button :class="{ on: imgMode === 'overlay' }" @click="imgMode = 'overlay'">
                    {{ detail.evidence.overlay_kind === 'det' ? '标注' : '叠加' }}
                  </button>
                </div>
              </div>
              <div class="snap-body">
                <div class="snap-top">
                  <span class="snap-msg" :title="detail.alarm.message">{{ detail.alarm.message }}</span>
                </div>
                <div class="snap-vals" v-if="detail.rule_hit.value !== null && detail.rule_hit.value !== undefined">
                  <b :class="{ danger: detail.alarm.level === 'danger' }">{{ detail.rule_hit.value }}</b>
                  <span class="sv-op">{{ detail.rule_hit.condition_label }}阈值 {{ detail.rule_hit.threshold }}</span>
                  <span class="sv-dev" v-if="detail.rule_hit.deviation !== null && detail.rule_hit.deviation !== undefined">
                    偏离 {{ detail.rule_hit.deviation > 0 ? '+' : '' }}{{ detail.rule_hit.deviation }}
                  </span>
                </div>
                <div class="snap-meta">
                  <span>{{ fmtTime(detail.alarm.created_at) }}</span>
                  <template v-if="detail.record">
                    <span class="dot">·</span>
                    <span class="snap-mono">#{{ detail.record.id }}</span>
                    <span class="dot">·</span>
                    <span>{{ detail.record.model_version }}</span>
                  </template>
                </div>
              </div>
            </div>

            <!-- ② 根因联动分析（SuiEye 特色）：把一条品位告警放回「磨矿 → 浮选 → 回水」工艺链 -->
            <section class="rc-hero" v-if="rootCause && rootCause.supported">
              <div class="rc-hd">
                <span class="rc-name">根因联动分析</span>
                <span class="rc-sub">磨矿 · 配药 · 回水</span>
                <span class="rc-count" v-if="rootCause.candidates.length">
                  {{ rootCause.candidates.length }} 项候选{{ rootCause.candidates.length > 1 ? ' · 按可能性排序' : '' }}
                </span>
                <span class="rc-count" v-else>未发现显著异常</span>
              </div>

              <!-- 三个工段的最新样本与时间距离：并列证据，不画箭头假装因果先后 -->
              <div class="rc-srcs">
                <div
                  class="rc-src" v-for="s in rootCause.sources" :key="s.key"
                  :class="srcMeta(s).cls" :title="srcMeta(s).tip"
                >
                  <i class="rc-dot"></i>
                  <span class="rc-src-name">{{ s.label }}</span>
                  <span class="rc-src-state">{{ srcMeta(s).tag }}</span>
                  <span class="rc-src-lag">{{ srcMeta(s).text }}</span>
                </div>
              </div>

              <!-- 候选：第 1 名直接给机理与建议（这是本模块的价值，不该藏在一次点击后面） -->
              <div
                class="rc-item" v-for="c in rootCause.candidates" :key="c.code"
                :class="{ open: expandedRc === c.code, top: c.rank === 1 }"
                @click="toggleRc(c.code)"
              >
                <div class="rc-line1">
                  <span class="rc-rank">{{ c.rank }}</span>
                  <span class="rc-title">{{ c.title }}</span>
                  <span class="rc-cat" :class="c.category">{{ CAT_LABELS[c.category] || c.category }}</span>
                  <span class="rc-bar"><i :style="{ width: Math.round(c.confidence * 100) + '%' }"></i></span>
                  <span class="rc-bar-num">{{ c.confidence.toFixed(2) }}</span>
                  <span class="rc-conf" :class="'lv-' + (CONF_CLS[c.confidence_label] || 'low')"
                        :title="`置信度 ${c.confidence_label}`"><i>置信</i>{{ c.confidence_label }}</span>
                  <el-icon class="rc-caret" :class="{ open: expandedRc === c.code }"><ArrowRight /></el-icon>
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
                        <template v-if="ei"> · </template>{{ e.sample_at ? fmtTime(e.sample_at) : '—' }}（距 {{ Math.round(e.lag_minutes || 0) }} 分钟）
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

              <!-- 数据缺口：只写「没有数据」；「超窗降权」已在上面来源格里，不重复 -->
              <div class="rc-note" v-if="missingNotes.length">
                <div class="rc-missing-item" v-for="(m, mi) in missingNotes" :key="mi">
                  <i class="rc-warn">!</i><span>{{ m }}</span>
                </div>
              </div>

              <div class="rc-caveat">统计关联，非因果结论 · 依据告警前同期数据，请结合现场核查</div>
            </section>

            <!-- 非浮选场景：说明为什么没有这块，而不是静默消失 -->
            <div class="rc-unsupported" v-else-if="rootCause && !rootCause.supported">
              跨工段根因归因当前只覆盖浮选品位告警
            </div>

            <!-- 同帧其他规则：一行 chips，不再各占一行 -->
            <div class="sib-block" v-if="detail.siblings.length">
              <div class="block-title">
                同帧其他告警 <span class="block-note">{{ detail.siblings.length }} 条</span>
              </div>
              <div class="sib-row">
                <button
                  class="sib" v-for="s in detail.siblings" :key="s.id"
                  @click="selectAlarm(s.id)" :title="`${s.rule_name} · ${s.status_label}`"
                >
                  <span class="ev-level" :class="s.level">{{ s.level_label }}</span>
                  <span class="sib-rule">{{ s.rule_name }}</span>
                  <span class="sib-status" :style="{ color: statusMeta(s.status).color }">{{ s.status_label }}</span>
                </button>
              </div>
            </div>

            <!-- 浮选：泡沫表观偏离（默认 4 行 —— 旁证，不与根因争主次） -->
            <div class="feat-block" v-if="detail.evidence.kind === 'foam' && detail.evidence.features.items.length">
              <div class="block-title" title="以基线均值为中心，偏移量 = 偏离的标准差倍数（|z| ≥ 1.5 显著）">
                泡沫表观偏离
                <span class="block-note">
                  {{ shownFeatures.length }} / {{ detail.evidence.features.items.length }} 项
                  · 显著 {{ detail.evidence.features.significant }}
                  <template v-if="detail.evidence.features.baseline_n">
                    · 基线 n={{ detail.evidence.features.baseline_n }}
                  </template>
                </span>
                <button
                  class="link-btn" v-if="detail.evidence.features.items.length > 4"
                  @click="featExpanded = !featExpanded"
                >{{ featExpanded ? '收起' : '全部' }}</button>
              </div>
              <div class="feat" v-for="f in shownFeatures" :key="f.key">
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
              <div class="feat-summary" v-if="detail.evidence.features.excluded_bad">
                基线已排除 {{ detail.evidence.features.excluded_bad }} 条口径异常的历史记录
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
  </PageShell>
</template>

<style scoped>
/* 页头/留白/返回入口由 PageShell 提供（2026-10-19 一致性整治） */
/* ---- 实时状态 ---- */
/* ★ 2026-10-19：从"自动刷新 30s"改成"实时推送" —— 数据来源从轮询计时器
   换成了 WS 连接状态，用户该看到的是"通没通"，不是"还有几秒刷" */
.live-hint {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--text-xs);
  color: var(--text-3);
}
.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-4);
  flex-shrink: 0;
}
.live-hint.connected { color: var(--text-2); }
.live-hint.connected .live-dot { background: var(--color-success); }

.auto-hint { font-size: var(--text-xs); color: var(--text-3); }
.auto-hint.paused { color: var(--color-warning); }
/* 处置中被压住的新告警：给一个可点的入口，别让用户以为"没有新告警" */
.pending-btn {
  margin-left: 6px;
  padding: 1px 8px;
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-sm);
  background: var(--color-primary-bg);
  color: var(--color-primary);
  font-size: var(--text-xs);
  cursor: pointer;
  transition: background var(--transition), color var(--transition);
}
.pending-btn:hover { background: var(--color-primary); color: var(--text-inverse); }

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
.ev-level.danger { color: var(--bg-1); background: var(--color-danger); }
.ev-level.warning { color: var(--bg-1); background: var(--color-warning); }
.ev-rule { font-size: var(--text-sm); font-weight: var(--weight-medium); color: var(--text-1); }
.ev-count {
  font-size: 11px; color: var(--color-primary); background: var(--color-primary-bg);
  padding: 0 5px; border-radius: var(--radius-sm); line-height: 16px;
}
/* 外部来源徽标：中性灰底，刻意和"同帧 N 条"的主色徽标区分开 ——
   一个是"谁报的"，一个是"几条"，不该长得一样 */
.ev-origin {
  flex-shrink: 0;
  max-width: 96px;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--bg-3);
  color: var(--text-2);
  font-size: 11px;
  line-height: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

/* ---- 触发快照（图 + 实测 + 时间压成一条）---- */
.snap {
  display: flex; gap: var(--space-3);
  padding: 10px; margin-bottom: var(--space-3);
  background: var(--bg-3); border-radius: var(--radius);
}
.snap-thumb {
  position: relative;
  flex: 0 0 112px; width: 112px; height: 84px;
  border-radius: var(--radius-sm); overflow: hidden; background: var(--bg-video);
}
.snap-img { width: 100%; height: 100%; display: block; cursor: zoom-in; }
.snap-img :deep(img) { object-fit: cover; }
.snap-body { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 5px; justify-content: center; }
.snap-top { display: flex; align-items: flex-start; gap: 8px; }
.snap-msg {
  flex: 1 1 auto; min-width: 0; font-size: var(--text-sm); font-weight: var(--weight-medium);
  color: var(--text-1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.snap-vals { display: flex; align-items: baseline; gap: 8px; font-size: var(--text-xs); color: var(--text-3); }
.snap-vals b {
  font-size: 20px; line-height: 1; font-weight: var(--weight-semibold);
  color: var(--text-1); font-variant-numeric: tabular-nums;
}
.snap-vals b.danger { color: var(--color-danger); }
.sv-op { color: var(--text-3); }
.sv-dev { color: var(--color-danger); }
.snap-meta { display: flex; align-items: center; gap: 4px; font-size: var(--text-xs); color: var(--text-3); }
.snap-mono { font-variant-numeric: tabular-nums; }

/* 原始/叠加切换压在缩略图底部：控件贴着它控制的东西，不横跨整条 */
.img-tabs {
  position: absolute; left: 4px; right: 4px; bottom: 4px;
  display: flex; gap: 3px; justify-content: center;
}
.img-tabs button {
  flex: 0 1 auto; font-size: 10px; padding: 0 6px; line-height: 15px;
  border-radius: 3px; border: 1px solid transparent;
  background: rgba(255, 255, 255, 0.92); color: var(--text-2);
  cursor: pointer; transition: all var(--transition); font-family: inherit;
}
.img-tabs button:hover { background: #fff; }
.img-tabs button.on { background: var(--color-primary); color: var(--text-inverse); }
.img-missing {
  width: 100%; height: 100%; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 4px;
  color: var(--text-3); font-size: 11px;
}
.img-missing .el-icon { font-size: 20px; color: var(--text-4); }

.block { margin-bottom: var(--space-4); }
.block:last-child { margin-bottom: 0; }
.block-title {
  font-size: var(--text-sm); font-weight: var(--weight-semibold); color: var(--text-1);
  margin-bottom: var(--space-2); display: flex; align-items: baseline; gap: 8px;
}
.block-note { font-size: var(--text-xs); color: var(--text-3); font-weight: var(--weight-normal); }
/* 行内文字按钮（全部 / 收起），不抢主按钮的视觉 */
.link-btn {
  margin-left: auto; padding: 0; border: 0; background: none;
  color: var(--color-primary); font-size: var(--text-xs);
  font-family: inherit; cursor: pointer;
}
.link-btn:hover { color: var(--color-primary-hover); }

/* 同帧兄弟：一行 chips */
.sib-block { margin-bottom: var(--space-4); }
.sib-row { display: flex; flex-wrap: wrap; gap: 6px; }
.sib {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 8px; border-radius: var(--radius); background: var(--bg-3);
  border: 1px solid var(--border-1);
  cursor: pointer; font-size: var(--text-xs); font-family: inherit;
  transition: border-color var(--transition);
}
.sib:hover { border-color: var(--color-primary); }
.sib-rule { color: var(--text-1); }
.sib-status { font-size: var(--text-xs); }

/* 泡沫表观：4 行旁证，条形压窄、去掉说明段 */
.feat-block { border-top: 1px solid var(--border-2); padding-top: 10px; margin-bottom: var(--space-4); }
.feat { display: flex; align-items: center; gap: 8px; font-size: var(--text-xs); height: 20px; }
.feat-name { flex: 0 0 80px; color: var(--text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.feat-val { flex: 0 0 146px; color: var(--text-1); font-variant-numeric: tabular-nums; }
.feat-val em { color: var(--text-3); font-style: normal; }
.feat-bar { position: relative; flex: 1 1 auto; height: 6px; background: var(--bg-3); border-radius: 3px; overflow: hidden; }
.bar-axis { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: var(--text-4); }
.bar-fill { position: absolute; top: 1px; bottom: 1px; border-radius: 2px; }
.feat-z { flex: 0 0 66px; text-align: right; color: var(--text-2); font-variant-numeric: tabular-nums; }
.feat-z em { color: var(--text-3); font-style: normal; }
.feat-z.high { color: var(--color-danger); font-weight: var(--weight-medium); }
.feat-z.medium { color: var(--color-warning); }
.feat-summary { font-size: 11px; color: var(--text-4); margin-top: 5px; }

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
.hf-btn.on { background: var(--color-primary); border-color: var(--color-primary); color: var(--bg-1); }
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
.bb-btn.primary { background: var(--color-primary); border-color: var(--color-primary); color: var(--bg-1); }
.bb-btn.primary:hover { color: var(--bg-1); opacity: 0.9; }
/* 批量处置弹窗 */
.batch-dlg .bd-label { font-size: var(--text-xs); color: var(--text-3); margin: 10px 0 6px; }
.batch-dlg .bd-label:first-child { margin-top: 0; }
.batch-row { display: flex; align-items: center; gap: 10px; }
.batch-hint { font-size: var(--text-xs); color: var(--text-3); }
/* ============================================================
   根因联动分析 —— 本页唯一的主角
   ★ 视觉上只给它三样特权：主色左边线、浅蓝底、白卡抬升；
     其余区块回到中性灰的"旁证"语汇。层级靠结构，不靠加颜色。
   ============================================================ */
.rc-hero {
  margin-bottom: var(--space-4);
  padding: 12px 14px;
  border: 1px solid #dbe7ff;
  border-left: 3px solid var(--color-primary);
  border-radius: var(--radius-lg);
  background: #f8fbff;
}
.rc-hd { display: flex; align-items: baseline; gap: 8px; margin-bottom: 10px; }
.rc-name {
  font-size: 15px; font-weight: var(--weight-semibold);
  color: var(--text-1); letter-spacing: .2px;
}
.rc-sub {
  flex: 0 0 auto; font-size: 11px; line-height: 16px; padding: 0 6px;
  color: var(--color-primary); background: var(--bg-1);
  border: 1px solid #c7d8ff; border-radius: var(--radius-sm);
}
.rc-count { margin-left: auto; font-size: var(--text-xs); color: var(--text-3); }

/* 三源并列：各自最近样本 + 时间距离。
   ★ 不画「磨矿→配药→回水」箭头：三个工段是并列证据，不是因果先后，
     画成链会让观众以为是推理顺序 —— 这是数据诚实问题，不是样式问题。
   ★ 做成一整条带子（格间 1px 分隔）而不是三张浮卡：三张卡在 880px 宽里
     内容只占四成，看着像没填满；一条带子读起来才是"一组证据"。 */
.rc-srcs {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-bottom: 10px; overflow: hidden;
  background: var(--bg-1); border: 1px solid var(--border-1); border-radius: var(--radius);
}
.rc-src {
  display: flex; flex-wrap: wrap; align-items: center; gap: 2px 5px;
  min-width: 0; padding: 5px 9px;
  border-left: 1px solid var(--border-2);
  font-size: 11px; color: var(--text-2);
}
.rc-src:first-child { border-left: 0; }
.rc-dot { flex: 0 0 auto; width: 6px; height: 6px; border-radius: 50%; background: var(--text-4); }
.rc-dot.on { background: var(--color-success); }
.rc-dot.far { background: var(--color-warning); }
.rc-src-name { white-space: nowrap; }
.rc-src-state { margin-left: auto; font-size: 10px; color: var(--text-3); white-space: nowrap; }
.rc-src.on .rc-src-state { color: var(--color-success); }
.rc-src.far .rc-src-state { color: var(--color-warning-deep); }
.rc-src-lag {
  flex: 0 0 100%; padding-left: 11px;
  font-size: var(--text-xs); color: var(--text-1); font-variant-numeric: tabular-nums;
}
.rc-src.far .rc-src-lag { color: var(--text-2); }

/* 候选：#1 是白卡（结论），其余是列表行（备选） */
.rc-item { border-radius: var(--radius); cursor: pointer; transition: background var(--transition), border-color var(--transition); }
.rc-item.top {
  background: var(--bg-1); border: 1px solid var(--border-1);
  box-shadow: var(--shadow-sm); padding: 9px 10px; margin-bottom: 6px;
}
.rc-item.top.open { border-color: #c7d8ff; }
.rc-item:not(.top) { padding: 7px 10px 5px; border-bottom: 1px solid var(--border-2); }
.rc-item:not(.top):hover { background: #f3f7ff; }
.rc-item:not(.top).open { background: var(--bg-1); }

.rc-line1 { display: flex; align-items: center; gap: 6px; }
.rc-rank {
  flex: 0 0 auto; width: 16px; height: 16px; border-radius: 50%;
  background: var(--color-primary); color: var(--text-inverse); font-size: 10px;
  display: inline-flex; align-items: center; justify-content: center;
}
.rc-item:not(.top) .rc-rank {
  background: var(--bg-1); color: var(--text-3); border: 1px solid var(--border-1);
}
.rc-title {
  flex: 1 1 auto; min-width: 0;
  font-size: var(--text-sm); font-weight: var(--weight-medium); color: var(--text-1);
}
.rc-item.top .rc-title { font-size: var(--text-base); font-weight: var(--weight-semibold); }
.rc-cat {
  flex: 0 0 auto; font-size: 10px; padding: 1px 6px; border-radius: var(--radius-sm);
  background: var(--bg-1); color: var(--text-3); border: 1px solid var(--border-1);
}
.rc-cat.grinding { color: #7d5fff; border-color: #d9cfff; background: #f5f2ff; }
.rc-cat.flotation { color: var(--color-primary); border-color: #c7d8ff; background: var(--color-primary-bg); }
.rc-cat.water { color: #0e9bd6; border-color: #c2e8f7; background: #eaf8fe; }
.rc-conf { flex: 0 0 auto; font-size: 11px; font-weight: var(--weight-medium); }
.rc-conf i { font-style: normal; font-size: 10px; font-weight: var(--weight-normal); color: var(--text-4); margin-right: 2px; }
.rc-conf.lv-high { color: var(--color-danger); }
.rc-conf.lv-mid { color: var(--color-warning-deep); }
.rc-conf.lv-low { color: var(--text-3); }

.rc-bar {
  flex: 0 0 48px; height: 4px; background: var(--bg-3);
  border-radius: 2px; overflow: hidden;
}
.rc-bar i { display: block; height: 100%; border-radius: 2px; background: var(--color-primary); transition: width .3s; }
.rc-bar-num {
  flex: 0 0 26px; text-align: right;
  font-size: 11px; color: var(--text-2); font-variant-numeric: tabular-nums;
}
.rc-caret { flex: 0 0 auto; font-size: 12px; color: var(--text-4); transition: transform var(--transition); }
.rc-caret.open { transform: rotate(90deg); color: var(--color-primary); }

.rc-ev {
  display: flex; flex-wrap: wrap; gap: 2px 12px;
  margin-top: 5px; padding-left: 22px;
  font-size: var(--text-xs); color: var(--text-2);
}
.rc-ev-item b { color: var(--text-1); font-weight: var(--weight-medium); }
.rc-ev-item .rc-dev { color: var(--color-danger); font-style: normal; margin-left: 3px; }
.rc-ev-item .rc-base { color: var(--text-4); margin-left: 3px; }
.rc-st { color: #0e9bd6; font-style: normal; font-weight: var(--weight-medium); margin-right: 3px; }

.rc-detail {
  margin: 8px 0 0 22px; padding-top: 8px;
  border-top: 1px dashed var(--border-1);
  display: flex; flex-direction: column; gap: 5px;
}
.rc-kv { display: flex; gap: 8px; font-size: var(--text-xs); }
.rc-k { flex: 0 0 52px; color: var(--text-3); }
.rc-v { flex: 1 1 auto; color: var(--text-2); line-height: 1.65; }
.rc-v.act { color: var(--color-primary); }
.rc-formula { font-size: 10px; color: var(--text-4); font-variant-numeric: tabular-nums; }

.rc-empty {
  font-size: var(--text-xs); color: var(--text-3); line-height: 1.6;
  background: var(--bg-1); border: 1px solid var(--border-1);
  border-radius: var(--radius); padding: 8px 10px;
}
.rc-note { margin-top: 8px; display: flex; flex-direction: column; gap: 3px; }
.rc-missing-item { display: flex; align-items: flex-start; gap: 5px; font-size: 11px; color: var(--text-3); line-height: 1.5; }
.rc-warn {
  flex: 0 0 auto; width: 13px; height: 13px; border-radius: 50%; margin-top: 1px;
  background: var(--color-warning); color: var(--bg-1); font-size: 9px; font-style: normal;
  display: inline-flex; align-items: center; justify-content: center; font-weight: bold;
}
.rc-caveat { margin-top: 8px; font-size: 11px; color: var(--text-3); }
.rc-unsupported {
  margin-bottom: var(--space-4);
  padding: 6px 10px;
  background: var(--bg-3); border-radius: var(--radius);
  font-size: var(--text-xs); color: var(--text-3);
}
</style>
