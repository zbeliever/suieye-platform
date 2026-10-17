<template>
  <div class="dosing">
    <!-- 顶栏 -->
    <header class="ds-header">
      <div class="header-left">
        <el-icon :size="28" color="#165dff"><MagicStick /></el-icon>
        <div>
          <h1>智能配药助手</h1>
          <p class="sub">基于选矿专家规则的药剂配方推荐系统</p>
        </div>
      </div>
      <div class="header-right">
        <el-tag :type="result ? 'success' : 'info'" size="small" effect="light">
          {{ result ? '推荐就绪' : '等待输入' }}
        </el-tag>
      </div>
    </header>

    <div class="ds-body">
      <!-- 左侧：输入面板 -->
      <section class="panel input-panel">
        <h2 class="panel-title">
          <el-icon><EditPen /></el-icon>
          工况输入
        </h2>

        <el-form label-position="top" label-width="auto" size="default">
          <el-form-item label="矿石类型" required>
            <el-select v-model="form.ore_type" placeholder="请选择矿石类型" style="width:100%">
              <el-option
                v-for="(ot, key) in config.ore_types"
                :key="key"
                :label="ot.name + ' — ' + ot.desc"
                :value="key"
              />
            </el-select>
            <div class="form-hint" v-if="config.ore_types[form.ore_type]">
              策略：{{ config.ore_types[form.ore_type].strategy }} | 最优 pH：{{ config.ore_types[form.ore_type].optimal_ph }}
            </div>
          </el-form-item>

          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="目标铅品位 (%)" required>
                <el-input-number
                  v-model="form.target_pb_grade"
                  :min="0.5" :max="20" :step="0.1"
                  controls-position="right"
                  style="width:100%"
                />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="目标锌品位 (%)">
                <el-input-number
                  v-model="form.target_zn_grade"
                  :min="0" :max="15" :step="0.1"
                  controls-position="right"
                  placeholder="可选"
                  style="width:100%"
                />
              </el-form-item>
            </el-col>
          </el-row>

          <el-form-item label="当前矿浆 pH" required>
            <div class="ph-slider-wrap">
              <el-slider
                v-model="form.current_ph"
                :min="6" :max="13" :step="0.1"
                show-stops
                :format-tooltip="(v) => v + ' pH'"
              />
              <div class="ph-info">
                <span class="ph-value" :class="{ 'ph-warn': Math.abs(form.current_ph - optimalPh) > 1 }">
                  {{ form.current_ph.toFixed(1) }}
                </span>
                <span class="ph-optimal">最优: {{ optimalPh }}</span>
              </div>
            </div>
          </el-form-item>

          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="矿浆浓度 (%)">
                <el-input-number
                  v-model="form.pulp_density"
                  :min="15" :max="55" :step="1"
                  controls-position="right"
                  placeholder="25-40 为宜"
                  style="width:100%"
                />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="泡沫状态">
                <el-select v-model="form.froth_state" placeholder="请选择泡沫状态" style="width:100%">
                  <el-option
                    v-for="fs in config.froth_states"
                    :key="fs.value"
                    :label="fs.label + ' — ' + fs.desc"
                    :value="fs.value"
                  />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>

          <el-button
            type="primary"
            size="large"
            @click="getRecommendation"
            :loading="loading"
            style="width:100%; margin-top:8px"
          >
            生成配药方案
          </el-button>
        </el-form>
      </section>

      <!-- 右侧：结果面板 -->
      <section class="panel result-panel" v-if="result">
        <h2 class="panel-title">
          <el-icon><Cpu /></el-icon>
          配方推荐
          <el-tag
            :type="result.confidence === 'high' ? 'success' : result.confidence === 'medium' ? 'warning' : 'danger'"
            size="small"
            effect="light"
          >
            {{ result.confidence === 'high' ? '高置信度' : result.confidence === 'medium' ? '中置信度' : '需验证' }}
          </el-tag>
        </h2>

        <!-- 输入摘要 -->
        <el-card shadow="never" class="summary-card">
          <el-descriptions :column="3" size="small" border>
            <el-descriptions-item
              v-for="(val, key) in result.input_summary"
              :key="key"
              :label="formatLabel(key)"
            >{{ formatValue(key, val) }}</el-descriptions-item>
          </el-descriptions>
        </el-card>

        <!-- 警告 -->
        <el-alert
          v-if="result.warnings && result.warnings.length"
          v-for="(w, i) in result.warnings"
          :key="i"
          :title="w"
          type="warning"
          show-icon
          :closable="false"
          style="margin-bottom:10px"
        />

        <!-- 药剂列表 -->
        <h3 class="section-title">推荐药剂方案</h3>
        <div class="reagent-list">
          <el-card
            v-for="(r, i) in result.reagents"
            :key="i"
            shadow="hover"
            class="reagent-card"
            :class="{ 'stage-zinc': r.stage === 'zinc_circuit' }"
          >
            <div class="reagent-header">
              <el-tag size="small" :type="getTagType(r.type)">{{ r.type }}</el-tag>
              <span class="reagent-name">{{ r.name }}</span>
              <span class="reagent-adj" :class="{ adj_up: r.adjustment === '\u2191', adj_down: r.adjustment === '\u2193' }">
                {{ r.adjustment === '=' ? '\u2014' : r.adjustment }}
              </span>
            </div>
            <div class="reagent-dosage">
              <span class="dosage-num">{{ r.recommended_dosage }}</span>
              <span class="dosage-unit">{{ r.unit }}</span>
              <span class="dosage-range">范围: {{ r.range[0] }}-{{ r.range[1] }} {{ r.unit }}</span>
            </div>
            <p class="reagent-reason"><strong>调整依据：</strong>{{ r.reason }}</p>
            <p class="reagent-desc">{{ r.desc }}</p>
          </el-card>
        </div>

        <!-- 工艺参数 -->
        <h3 class="section-title">工艺参数建议</h3>
        <el-row :gutter="10">
          <el-col :span="8" v-for="(val, key) in result.process_params" :key="key">
            <div class="param-item">
              <span class="param-label">{{ formatLabel(key) }}</span>
              <span class="param-val">{{ val }}<span v-if="key.includes('rate')"> m\xb3/min</span><span v-if="key.includes('speed')"> rpm</span><span v-if="key.includes('level')"> %</span><span v-if="key.includes('time')"> min</span></span>
            </div>
          </el-col>
        </el-row>

        <!-- 预期效果 -->
        <h3 class="section-title">预期效果</h3>
        <el-row :gutter="10">
          <el-col :span="8" v-for="(val, key) in result.expected_outcome" :key="key">
            <div class="outcome-item">
              <span class="outcome-label">{{ formatLabel(key) }}</span>
              <span class="outcome-val">{{ val }}<span v-if="key.includes('recovery')">%</span><span v-if="key.includes('grade')">%</span><span v-if="key.includes('ratio')"> \u500d</span></span>
            </div>
          </el-col>
        </el-row>

        <!-- 推理过程 -->
        <el-collapse v-if="result.reasoning && result.reasoning.length" style="margin-top:14px">
          <el-collapse-item>
            <template #title>
              推理依据 ({{ result.reasoning.length }} 条)
            </template>
            <ol class="reasoning-list">
              <li v-for="(r, i) in result.reasoning" :key="i">{{ r }}</li>
            </ol>
          </el-collapse-item>
        </el-collapse>

        <!-- 操作按钮 -->
        <div class="action-bar">
          <el-button @click="showFeedback = true">提交反馈</el-button>
          <el-button type="primary" @click="applyDosing">应用此方案</el-button>
        </div>
      </section>

      <!-- 空状态 -->
      <section class="panel result-panel empty-state" v-else>
        <el-empty description="填写左侧工况参数，点击生成配药方案">
          <template #image>
            <el-icon :size="64" color="#c9cdd4"><MagicStick /></el-icon>
          </template>
          <template #description>
            <p>系统将基于选矿专家规则，输出完整的药剂配方推荐</p>
          </template>
        </el-empty>
      </section>
    </div>

    <!-- 历史记录（底部） -->
    <section class="panel history-panel" v-if="history.records.length">
      <h2 class="panel-title">
        历史记录
        <el-badge :value="history.total" type="info" />
      </h2>
      <el-table :data="history.records" stripe size="small" max-height="280" style="width:100%">
        <el-table-column prop="created_at" label="时间" width="110">
          <template #default="{ row }">{{ row.created_at.slice(5, 16) }}</template>
        </el-table-column>
        <el-table-column label="矿石类型" width="120">
          <template #default="{ row }">{{ config.ore_types[row.ore_type]?.name || row.ore_type }}</template>
        </el-table-column>
        <el-table-column prop="target_pb_grade" label="目标 Pb%" width="80" />
        <el-table-column prop="current_ph" label="pH" width="60" />
        <el-table-column label="泡沫" width="80">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="{'normal':'success','fragile':'info','sticky':'warning','overloaded':'danger'}[row.froth_state] || 'info'"
              effect="plain"
            >{{ frothLabel(row.froth_state) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="reagent_count" label="剂数" width="60" />
        <el-table-column label="置信度" width="70">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="(row.confidence || 'high') === 'high' ? 'success' : (row.confidence || 'high') === 'medium' ? 'warning' : 'danger'"
              effect="light"
              round
            />
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.applied ? 'success' : 'info'" size="small" effect="plain">
              {{ row.applied ? '已应用' : '未应用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="60">
          <template #default="{ row }">
            <el-link type="primary" @click="loadRecord(row.id)">查看</el-link>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 反馈弹窗 -->
    <el-dialog v-model="showFeedback" title="方案反馈" width="400px" center>
      <p style="color:#86909c; text-align:center; margin-bottom:16px">此配药方案的实际效果如何？</p>
      <div style="text-align:center; font-size:32px; margin-bottom:20px">
        <span
          v-for="s in 5" :key="s"
          style="cursor:pointer; color:#e5e6eb; transition:color 0.2s"
          :style="{ color: feedbackScore >= s ? '#ff7d00' : '#e5e6eb' }"
          @click="feedbackScore = s"
        >&#9733;</span>
      </div>
      <template #footer>
        <el-button @click="showFeedback = false">取消</el-button>
        <el-button type="primary" @click="submitFeedback">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { MagicStick, EditPen, Cpu } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const route = useRoute()
const loading = ref(false)
const result = ref(null)
const showFeedback = ref(false)
const feedbackScore = ref(4)

// 默认配置（不依赖API，确保表单立即可用）
const config = ref({
  ore_types: {
    lead_zinc_mixed: { name: '铅锌混合矿', desc: '铅锌共生矿石', strategy: '优先浮铅抑锌', optimal_ph: 10.5 },
    high_lead: { name: '高铅矿石', desc: '铅品位较高', strategy: '强化铅回收', optimal_ph: 9.5 },
    high_zinc: { name: '高锌矿石', desc: '锌品位较高', strategy: '锌活化浮选', optimal_ph: 11.5 },
    oxidized: { name: '氧化矿', desc: '氧化程度较高', strategy: '硫化后浮选', optimal_ph: 12.0 },
  },
  froth_states: [
    { value: 'normal', label: '正常', desc: '泡沫稳定' },
    { value: 'fragile', label: '偏脆', desc: '泡沫易破' },
    { value: 'sticky', label: '发粘', desc: '泡沫粘稠' },
    { value: 'overloaded', label: '过载', desc: '泡沫过多' },
  ],
})
const history = ref({ total: 0, records: [] })

const form = ref({
  ore_type: 'lead_zinc_mixed',
  target_pb_grade: 5.0,
  target_zn_grade: 3.0,
  current_ph: 10.5,
  pulp_density: 32,
  froth_state: 'normal',
})

const optimalPh = computed(() => {
  return config.value.ore_types[form.value.ore_type]?.optimal_ph || 10.5
})

onMounted(async () => {
  try { config.value = await api.get('/dosing/config') } catch {}
  try { history.value = await api.get('/dosing/history?limit=10') } catch {}

  // 从 Workbench 跳转时自动填充参数
  const q = route.query
  let autoFilled = false
  if (q.target_pb_grade) { form.value.target_pb_grade = Number(q.target_pb_grade); autoFilled = true }
  if (q.target_zn_grade) { form.value.target_zn_grade = Number(q.target_zn_grade); autoFilled = true }
  if (q.froth_state && config.value.froth_states.some(f => f.value === q.froth_state)) {
    form.value.froth_state = q.froth_state
    autoFilled = true
  }
  // 自动触发推荐
  if (autoFilled) {
    getRecommendation()
  }
})

async function getRecommendation() {
  loading.value = true
  try {
    const params = new URLSearchParams({
      ore_type: form.value.ore_type,
      target_pb_grade: form.value.target_pb_grade,
      target_zn_grade: form.value.target_zn_grade,
      current_ph: form.value.current_ph,
      pulp_density: form.value.pulp_density,
      froth_state: form.value.froth_state,
    })
    const url = `/api/dosing/recommend?${params.toString()}`
    console.log('发送配药请求:', url)
    const res = await fetch(url, { method: 'POST' })
    console.log('响应状态:', res.status)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    result.value = await res.json()
    console.log('收到结果:', result.value)
    if (result.value && result.value.reagents && result.value.reagents.length > 0) {
      ElMessage.success(`配方生成成功！推荐 ${result.value.reagents.length} 种药剂`)
    } else {
      ElMessage.warning('生成完成但数据异常')
    }
    try { history.value = await api.get('/dosing/history?limit=10') } catch {}
  } catch (e) {
    console.error('推荐失败:', e)
    ElMessage.error('配药请求失败: ' + (e.message || JSON.stringify(e) || '未知错误'))
  } finally {
    loading.value = false
  }
}

async function applyDosing() {
  if (!result.value?.record_id) {
    ElMessage.warning('没有可应用的方案')
    return
  }
  try {
    const res = await fetch(`/api/dosing/${result.value.record_id}/feedback?score=5&applied=true`, { method: 'PUT' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    ElMessage.success('方案已标记为已应用')
    result.value = null
    try { history.value = await api.get('/dosing/history?limit=10') } catch {}
  } catch (e) {
    console.error('操作失败:', e)
    ElMessage.error('应用失败: ' + e.message)
  }
}

async function submitFeedback() {
  if (!result.value?.record_id) {
    ElMessage.warning('没有可评价的方案')
    return
  }
  try {
    const res = await fetch(`/api/dosing/${result.value.record_id}/feedback?score=${feedbackScore.value}&applied=true`, { method: 'PUT' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    ElMessage.success('感谢您的反馈！')
    showFeedback.value = false
  } catch (e) {
    console.error('提交失败:', e)
    ElMessage.error('提交失败: ' + e.message)
  }
}

async function loadRecord(id) {
  try {
    result.value = await api.get(`/dosing/${id}`)
  } catch (e) {
    console.error('加载失败:', e)
  }
}

function formatLabel(key) {
  const map = {
    ore_type: '矿石类型', ore_name: '矿石名称', target_pb_grade: '目标铅品位',
    target_zn_grade: '目标锌品位', current_ph: '当前 pH', pulp_density: '矿浆浓度',
    froth_state: '泡沫状态', strategy: '浮选策略',
    aeration_rate: '充气量', agitation_speed: '搅拌速度', pulp_level: '液位高度',
    flotation_time_rougher: '粗选时间', flotation_time_scavenger: '扫选时间',
    estimated_pb_recovery: '预计铅回收率', estimated_zn_recovery: '预计锌回收率',
    estimated_pb_concentrate_grade: '预计精矿铅品位', estimated_concentrate_ratio: '富集比',
    confidence_note: '置信度',
  }
  return map[key] || key
}

function formatValue(key, val) {
  if (key === 'current_ph') return val + ' pH'
  if (key.includes('grade')) return val + ' %'
  if (typeof val === 'object') return JSON.stringify(val)
  return val
}

function frothLabel(state) {
  const map = { normal: '正常', fragile: '偏脆', sticky: '发粘', overloaded: '过载' }
  return map[state] || state
}

function getTagType(typeStr) {
  const t = typeStr.replace(/[\(\)\/]/g, '').split('\uff08')[0]
  // ⚠️ 不能返回空串：Element Plus 的 el-tag type 只接受
  //    primary/success/info/warning/danger —— 传 '' 会触发 prop 校验失败并退化成默认样式
  //    （console 满屏 "Invalid prop: validation failed for prop type"）。
  //    这里想表达的是"中性、不着色" → 用 'info'。
  if (t === '\u6355\u6536\u5242' || t === '\u6291\u5236\u5242') return 'info'
  if (t === '\u6d3b\u5316\u5242') return 'warning'
  if (t === '\u8d77\u6ce1\u5242') return 'success'
  return 'info'
}
</script>

<style scoped>
.dosing {
  min-height: 100vh;
  background: #f0f2f5;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 12px 20px 10px;
}

/* Header */
.ds-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 24px;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 14px;
}
.ds-header h1 { margin: 0; font-size: 18px; font-weight: 600; }
.sub { margin: 2px 0 0; font-size: 12px; color: #86909c; }

/* Body layout */
.ds-body {
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: 16px;
}
@media (max-width: 1024px) {
  .ds-body { grid-template-columns: 1fr; }
}

/* Panel */
.panel {
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 20px 24px;
}
.panel-title {
  margin: 0 0 20px;
  font-size: 15px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Form hints */
.form-hint {
  margin-top: 6px;
  font-size: 11px;
  color: #86909c;
  line-height: 1.5;
}

/* pH slider */
.ph-slider-wrap { padding: 4px 0; }
.ph-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}
.ph-value {
  font-size: 22px;
  font-weight: 700;
  color: #165dff;
  font-variant-numeric: tabular-nums;
}
.ph-warn { color: #ff7d00 !important; }
.ph-optimal { font-size: 12px; color: #86909c; }

/* Summary card */
.summary-card { margin-bottom: 16px; }

/* Section titles */
.section-title {
  font-size: 14px;
  font-weight: 600;
  margin: 20px 0 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #f2f3f5;
  color: #1d2129;
}

/* Reagent cards */
.reagent-list { display: flex; flex-direction: column; gap: 10px; }
.reagent-card { transition: all 0.2s; }
.reagent-card.stage-zinc { border-left: 3px solid #0fc6c2; }
.reagent-header { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.reagent-name { font-size: 14px; font-weight: 600; }
.reagent-adj { margin-left: auto; font-size: 18px; font-weight: 700; }
.adj_up { color: #00b42a; }
.adj_down { color: #f53f3f; }
.reagent-dosage { display: flex; align-items: baseline; gap: 6px; margin-bottom: 8px; }
.dosage-num { font-size: 26px; font-weight: 700; color: #165dff; }
.dosage-unit { font-size: 13px; color: #86909c; }
.dosage-range { margin-left: auto; font-size: 11px; color: #c9cdd4; }
.reagent-reason { font-size: 13px; color: #4e5969; margin: 0 0 4px; }
.reagent-desc { font-size: 12px; color: #86909c; margin: 0; }

/* Param / outcome items */
.param-item, .outcome-item {
  background: #f7f8fa;
  border-radius: 6px;
  padding: 12px;
  text-align: center;
  margin-bottom: 8px;
}
.param-label, .outcome-label {
  font-size: 11px;
  color: #86909c;
  display: block;
  margin-bottom: 4px;
}
.param-val, .outcome-val {
  font-size: 16px;
  font-weight: 600;
  color: #1d2129;
}

/* Reasoning */
.reasoning-list { padding-left: 20px; margin: 0; font-size: 13px; color: #4e5969; }
.reasoning-list li { margin-bottom: 6px; line-height: 1.6; }

/* Action bar */
.action-bar { display: flex; gap: 10px; margin-top: 20px; padding-top: 16px; border-top: 1px solid #f2f3f5; }
.action-bar .el-button { flex: 1; }

/* Empty state */
.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 420px;
}

/* History */
.history-panel { max-height: 340px; overflow: hidden; }
</style>
