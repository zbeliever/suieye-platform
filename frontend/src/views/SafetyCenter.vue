<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import BackToFactory from '../components/BackToFactory.vue'

const router = useRouter()

// ---- 数据 ----
const loading = ref(false)
const cameras = ref([])
const timeline = ref([])
const timelineTotal = ref(0)
const stats = ref({
  today_count: 0, intrusion_30d: 0, ppe_compliance_avg: null,
  hazard_open: 0, hazard_total: 0, alarm_30d: 0, risk_distribution_30d: {},
})
const tickets = ref([])
const ticketStatusCount = ref({})
const ticketTab = ref('pending')

// ---- 加载 ----
async function loadAll() {
  loading.value = true
  try {
    const [cam, tl, st, tk] = await Promise.all([
      api.get('/safety/cameras'),
      api.get('/safety/timeline', { params: { limit: 24 } }),
      api.get('/safety/stats'),
      api.get('/safety/hazard-tickets', { params: { limit: 100 } }),
    ])
    cameras.value = cam.cameras || []
    timeline.value = tl.items || []
    timelineTotal.value = tl.total || 0
    stats.value = { ...stats.value, ...st }
    tickets.value = tk.items || []
    ticketStatusCount.value = tk.status_count || {}
  } catch (e) {
    console.error('加载巡检数据失败:', e)
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

async function loadTickets() {
  try {
    const res = await api.get('/safety/hazard-tickets', { params: { limit: 100 } })
    tickets.value = res.items || []
    ticketStatusCount.value = res.status_count || {}
  } catch (e) {
    console.error(e)
  }
}

// ---- 工单流转 ----
async function transitionTicket(ticket, action) {
  const actionLabel = { rectify: '开始整改', verify: '复查销项', reopen: '重开' }[action]
  try {
    let body = { action }
    if (action === 'rectify') {
      const { value } = await ElMessageBox.prompt('请输入整改措施', `${actionLabel} · ${ticket.title}`, {
        confirmButtonText: '提交',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: '例：已通知现场巡检员调整托辊并复检',
      })
      body.action_taken = value
      body.assigned_to = '当班巡检员'
    } else if (action === 'verify') {
      await ElMessageBox.confirm(`确认该隐患已复查合格、准予销项？`, `${actionLabel} · ${ticket.title}`, {
        confirmButtonText: '确认销项',
        cancelButtonText: '取消',
        type: 'success',
      })
    } else {
      await ElMessageBox.confirm(`确认重开该工单？`, `${actionLabel} · ${ticket.title}`, {
        confirmButtonText: '重开',
        cancelButtonText: '取消',
        type: 'warning',
      })
    }
    await api.post(`/safety/hazard-tickets/${ticket.id}/transition`, body)
    ElMessage.success(`${actionLabel}成功`)
    loadTickets()
    loadAll()
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error(e?.response?.data?.detail || '操作失败')
    }
  }
}

// ---- 跳临时抽检 ----
function goSpotCheck() {
  router.push('/workbench/safety')
}

// ---- 跳实时监控（现场画面 + 实时仪表） ----
function goReplay() {
  router.push('/monitor')
}

// ---- 格式化 ----
const camStatusDot = (status) => ({
  normal: '#00b42a', warning: '#ff7d00', danger: '#f53f3f',
}[status] || '#86909c')

const camStatusLabel = (status) => ({
  normal: '正常运行', warning: '关注', danger: '告警',
}[status] || '正常')

const riskTagType = (level) => ({
  low: 'success', medium: 'warning', high: 'danger', critical: 'danger',
}[level] || 'info')

const riskLabel = (level) => ({
  low: '低风险', medium: '中风险', high: '高风险', critical: '严重',
}[level] || level)

const imgUrl = (path) => path ? '/api/image/' + encodeURIComponent(path) : ''
const fmtTime = (iso) => {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
const fmtPct = (v) => v == null ? '—' : (v * 100).toFixed(0) + '%'
const fmtPct2 = (v) => v == null ? '—' : (v * 100).toFixed(1) + '%'

// 过滤后的工单
const filteredTickets = computed(() => {
  if (ticketTab.value === 'all') return tickets.value
  return tickets.value.filter(t => t.status === ticketTab.value)
})

onMounted(loadAll)
</script>

<template>
  <div class="page" v-loading="loading">
    <BackToFactory class="page-back" />
    <!-- 页头 -->
    <div class="header">
      <div>
        <h2>安全巡检</h2>
        <p class="subtitle">皮带全线监控 · 隐患闭环管理 · {{ stats.total_count || 0 }} 条历史巡检记录</p>
      </div>
      <el-button type="primary" @click="goSpotCheck">
        <el-icon style="margin-right:4px"><Camera /></el-icon>临时抽检
      </el-button>
    </div>

    <!-- KPI 条 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-number">{{ stats.today_count }}</div>
          <div class="stat-label">今日巡检次数</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card warn">
          <div class="stat-number">{{ stats.hazard_open }}</div>
          <div class="stat-label">待处理隐患</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card danger-card">
          <div class="stat-number">{{ stats.intrusion_30d }}</div>
          <div class="stat-label">30天危险区入侵</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card ok">
          <div class="stat-number">{{ fmtPct(stats.ppe_compliance_avg) }}</div>
          <div class="stat-label">PPE 平均合规率（30天）</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 主区两栏 -->
    <el-row :gutter="16">
      <!-- 左：点位监控 -->
      <el-col :span="16">
        <el-card shadow="never" class="section-card">
          <template #header>
            <div class="section-title">
              <span>巡检点位监控</span>
              <el-tag size="small" type="info">{{ cameras.length }} 个点位</el-tag>
            </div>
          </template>
          <div class="camera-grid">
            <div v-for="cam in cameras" :key="cam.id" class="camera-card" :class="cam.status">
              <div class="cam-head">
                <div class="cam-name">
                  <span class="status-dot" :style="{ background: camStatusDot(cam.status) }"></span>
                  {{ cam.name }}
                </div>
                <el-tag size="small" :type="cam.status === 'danger' ? 'danger' : (cam.status === 'warning' ? 'warning' : 'success')">
                  {{ camStatusLabel(cam.status) }}
                </el-tag>
              </div>
              <div class="cam-zone">{{ cam.zone }} · {{ cam.focus }}</div>
              <div class="cam-body">
                <div class="cam-thumb" v-if="cam.latest?.detection_overlay">
                  <img :src="imgUrl(cam.latest.detection_overlay)" alt="检测叠加" />
                </div>
                <div class="cam-thumb cam-thumb-empty" v-else>
                  <el-icon size="32" color="#c9cdd4"><Picture /></el-icon>
                </div>
                <div class="cam-metrics">
                  <div class="metric">
                    <span class="m-label">风险</span>
                    <el-tag size="small" :type="riskTagType(cam.latest?.risk_level)">
                      {{ riskLabel(cam.latest?.risk_level) }}
                    </el-tag>
                  </div>
                  <div class="metric">
                    <span class="m-label">人员</span>
                    <span class="m-value">{{ cam.latest?.person_count ?? 0 }} 人<span v-if="cam.latest?.persons_in_danger_zone" class="m-danger">·危险区{{ cam.latest.persons_in_danger_zone }}</span></span>
                  </div>
                  <div class="metric">
                    <span class="m-label">皮带偏移</span>
                    <span class="m-value">{{ cam.latest?.belt_offset_px ?? 0 }} px</span>
                  </div>
                  <div class="metric">
                    <span class="m-label">异物</span>
                    <span class="m-value">{{ cam.latest?.foreign_object_count ?? 0 }} 个</span>
                  </div>
                </div>
              </div>
              <div class="cam-foot">
                <span class="cam-time">{{ fmtTime(cam.latest?.created_at) }}</span>
                <el-button size="small" text @click="goReplay">实时画面</el-button>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>

      <!-- 右：隐患闭环看板 -->
      <el-col :span="8">
        <el-card shadow="never" class="section-card">
          <template #header>
            <div class="section-title">
              <span>隐患整改闭环</span>
              <el-tag size="small" type="warning">{{ stats.hazard_open }} 待处理</el-tag>
            </div>
          </template>
          <el-tabs v-model="ticketTab" class="ticket-tabs">
            <el-tab-pane :label="`待整改 ${ticketStatusCount.pending || 0}`" name="pending" />
            <el-tab-pane :label="`整改中 ${ticketStatusCount.rectifying || 0}`" name="rectifying" />
            <el-tab-pane :label="`已销项 ${ticketStatusCount.closed || 0}`" name="closed" />
            <el-tab-pane label="全部" name="all" />
          </el-tabs>
          <div class="ticket-list">
            <div v-if="!filteredTickets.length" class="empty-tip">该状态下暂无工单</div>
            <div v-for="t in filteredTickets" :key="t.id" class="ticket-card" :class="t.level">
              <div class="t-head">
                <el-tag size="small" :type="t.level === 'danger' ? 'danger' : 'warning'">
                  {{ t.level === 'danger' ? '严重' : '警告' }}
                </el-tag>
                <span class="t-cam">{{ t.camera_id }}</span>
                <span class="t-time">{{ fmtTime(t.created_at) }}</span>
              </div>
              <div class="t-title" :title="t.title">{{ t.title }}</div>
              <div class="t-detail" v-if="t.action_taken">整改：{{ t.action_taken }}</div>
              <div class="t-assignee" v-if="t.assigned_to">责任人：{{ t.assigned_to }}</div>
              <div class="t-actions">
                <el-button v-if="t.status === 'pending'" size="small" type="warning" @click="transitionTicket(t, 'rectify')">开始整改</el-button>
                <el-button v-if="t.status === 'rectifying'" size="small" type="success" @click="transitionTicket(t, 'verify')">复查销项</el-button>
                <el-button v-if="t.status === 'closed'" size="small" plain @click="transitionTicket(t, 'reopen')">重开</el-button>
                <el-tag v-if="t.status === 'closed'" size="small" type="success">已销项</el-tag>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 巡检记录流 -->
    <el-card shadow="never" class="section-card timeline-card">
      <template #header>
        <div class="section-title">
          <span>巡检记录流</span>
          <el-tag size="small" type="info">共 {{ timelineTotal }} 条 · 显示最近 {{ timeline.length }} 条</el-tag>
        </div>
      </template>
      <div class="timeline">
        <div v-for="r in timeline" :key="r.id" class="tl-item" :class="r.risk_level">
          <div class="tl-thumb" v-if="r.detection_overlay">
            <img :src="imgUrl(r.detection_overlay)" alt="检测" />
          </div>
          <div class="tl-content">
            <div class="tl-head">
              <el-tag size="small" :type="riskTagType(r.risk_level)">{{ riskLabel(r.risk_level) }}</el-tag>
              <span class="tl-cam">{{ r.camera_id }}</span>
              <span class="tl-time">{{ fmtTime(r.created_at) }}</span>
            </div>
            <div class="tl-metrics">
              <span>人员 {{ r.person_count }}</span>
              <span v-if="r.persons_in_danger_zone" class="danger-text">危险区 {{ r.persons_in_danger_zone }}</span>
              <span>偏移 {{ r.belt_offset_px ?? 0 }}px</span>
              <span>异物 {{ r.foreign_object_count }}</span>
              <span v-if="r.ppe_compliance_rate != null">PPE {{ fmtPct2(r.ppe_compliance_rate) }}</span>
              <span class="m-pipeline">{{ ((r.pipeline_ms || 0) / 1000).toFixed(1) }}s</span>
            </div>
            <div class="tl-alarms" v-if="r.alarms?.length">
              <el-tag v-for="(a, i) in r.alarms.slice(0,3)" :key="i" size="small" :type="a.level === 'danger' ? 'danger' : 'warning'" class="alarm-tag">
                {{ a.rule_name }}
              </el-tag>
            </div>
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.page { max-width: 1400px; margin: 0 auto; padding: 20px; box-sizing: border-box; }
.header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }
h2 { color: #1d2129; margin: 0 0 4px; font-size: 22px; }
.subtitle { color: #86909c; font-size: 13px; margin: 0; }

.stat-row { margin-bottom: 16px; }
.stat-card { text-align: center; padding: 12px 0; }
.stat-card .stat-number { font-size: 30px; font-weight: 700; color: #165dff; }
.stat-card .stat-label { font-size: 13px; color: #86909c; margin-top: 4px; }
.stat-card.warn .stat-number { color: #ff7d00; }
.stat-card.danger-card .stat-number { color: #f53f3f; }
.stat-card.ok .stat-number { color: #00b42a; }

.section-card { margin-bottom: 16px; }
.section-title { display: flex; justify-content: space-between; align-items: center; font-weight: 600; color: #1d2129; }

/* 点位网格 */
.camera-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.camera-card { border: 1px solid #f2f3f5; border-radius: 6px; padding: 12px; background: #fff; }
.camera-card.danger { border-color: #f53f3f; box-shadow: 0 0 0 1px rgba(245,108,108,0.2); }
.camera-card.warning { border-color: #ff7d00; }
.cam-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.cam-name { font-weight: 600; color: #1d2129; display: flex; align-items: center; gap: 6px; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 6px currentColor; }
.cam-zone { font-size: 12px; color: #86909c; margin-bottom: 8px; }
.cam-body { display: flex; gap: 10px; }
.cam-thumb { flex: 0 0 160px; height: 100px; overflow: hidden; border-radius: 4px; background: #f7f8fa; }
.cam-thumb img { width: 100%; height: 100%; object-fit: cover; }
.cam-thumb-empty { display: flex; align-items: center; justify-content: center; }
.cam-metrics { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.metric { display: flex; justify-content: space-between; font-size: 13px; }
.m-label { color: #86909c; }
.m-value { color: #1d2129; font-weight: 500; }
.m-danger { color: #f53f3f; margin-left: 4px; }
.cam-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; font-size: 12px; color: #86909c; }

/* 隐患工单 */
.ticket-tabs { margin-bottom: 8px; }
.ticket-list { max-height: 460px; overflow-y: auto; }
.ticket-card { border-left: 3px solid #ff7d00; background: #fafafa; padding: 8px 10px; margin-bottom: 8px; border-radius: 4px; }
.ticket-card.danger { border-left-color: #f53f3f; background: #fef0f0; }
.t-head { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; font-size: 12px; color: #86909c; }
.t-time { margin-left: auto; }
.t-title { font-size: 13px; color: #1d2129; line-height: 1.4; margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.t-detail { font-size: 12px; color: #4e5969; margin-bottom: 4px; }
.t-assignee { font-size: 12px; color: #86909c; }
.t-actions { margin-top: 6px; display: flex; gap: 6px; align-items: center; }
.empty-tip { text-align: center; color: #c9cdd4; padding: 30px 0; font-size: 13px; }

/* 巡检记录流 */
.timeline-card { margin-top: 0; }
.timeline { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.tl-item { border: 1px solid #f2f3f5; border-radius: 6px; padding: 8px; background: #fff; transition: box-shadow .2s; }
.tl-item:hover { box-shadow: var(--shadow-md); }
.tl-item.critical { border-color: #f53f3f; }
.tl-item.high { border-color: #f78989; }
.tl-item.medium { border-color: #ff7d00; }
.tl-thumb { width: 100%; height: 90px; overflow: hidden; border-radius: 4px; margin-bottom: 6px; background: #f7f8fa; }
.tl-thumb img { width: 100%; height: 100%; object-fit: cover; }
.tl-content { font-size: 12px; }
.tl-head { display: flex; align-items: center; gap: 4px; margin-bottom: 4px; flex-wrap: wrap; }
.tl-cam { color: #4e5969; font-weight: 500; }
.tl-time { margin-left: auto; color: #86909c; font-size: 11px; }
.tl-metrics { display: flex; flex-wrap: wrap; gap: 6px; color: #4e5969; margin-bottom: 4px; }
.danger-text { color: #f53f3f; font-weight: 500; }
.m-pipeline { color: #c9cdd4; }
.tl-alarms { display: flex; flex-wrap: wrap; gap: 4px; }
.alarm-tag { font-size: 10px; }
</style>
