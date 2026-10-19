<template>
  <PageShell
    v-loading="loading"
    :title="`${eq.name || '尾矿浓密机'} · 尾矿处理`"
    :sub="`${eq.spec || ''} · 进料：${eq.feed_from || '—'}`"
    kpi="none"
  >
    <template #meta>
      <span class="cond-badge" :class="condition.level">{{ condition.label || '—' }}</span>
      <span class="eq-cond-desc">{{ condition.desc }}</span>
    </template>
    <template #actions>
      <span class="eq-fresh">
        数据截止 <b>{{ tsText }}</b>
        <span class="fresh-age">（{{ freshness.age_hours ?? '—' }} h 前）</span>
      </span>
    </template>

    <!-- KPI 带：6 个核心工艺量（组件统一为 StatCard；格数是业务量决定的，见文件头注释） -->
    <div class="kpi-grid">
        <StatCard
          label="底流浓度"
          :value="valOf('underflow_conc')"
          :unit="unitOf('underflow_conc')"
          :foot="rangeText('underflow_conc')"
          :tone="toneOf('underflow_conc')"
        />
        <StatCard
          label="泥床高度"
          :value="valOf('bed_height')"
          :unit="unitOf('bed_height')"
          :foot="`目标 5.0 · 上限 ${hiText('bed_height')}`"
          :tone="toneOf('bed_height')"
        />
        <StatCard
          label="耙架扭矩"
          :value="valOf('rake_torque')"
          :unit="unitOf('rake_torque')"
          :foot="`报警 ${hiText('rake_torque')}`"
          :tone="toneOf('rake_torque')"
        />
        <StatCard
          label="絮凝剂单耗"
          :value="valOf('flocculant_dosage')"
          :unit="unitOf('flocculant_dosage')"
          :foot="`超投报警 ${hiText('flocculant_dosage')}`"
          :tone="toneOf('flocculant_dosage')"
        />
        <StatCard
          label="物料平衡残差"
          :value="balance.residual_pct ?? '—'"
          unit="%"
          :foot="balance.ok ? '自洽（< 2%）' : '偏离，需核查计量'"
          :tone="balance.ok ? 'default' : 'danger'"
        />
        <StatCard
          label="溢流浊度 · 水质 W3"
          :value="fmt(overflow.turbidity, 1)"
          unit="NTU"
          :foot="`SS ${fmt(overflow.ss, 0)} mg/L · ${levelCn(overflow.ss_level)}`"
          :tone="overflowTone"
        />
    </div>

    <!-- ====== 趋势 + 实时参数总表 ====== -->
    <section class="dual-row wide-left">
      <div class="panel fill">
        <div class="panel-head">
          <h3>浓密机参数趋势</h3>
          <div class="panel-tools">
            <el-select v-model="metric" size="small" style="width: 128px" @change="loadHistory">
              <el-option v-for="m in metricOptions" :key="m.key" :label="m.label" :value="m.key" />
            </el-select>
            <span class="vs">对比</span>
            <el-select v-model="compare" size="small" style="width: 128px" @change="loadHistory">
              <el-option label="不对比" value="" />
              <el-option
                v-for="m in metricOptions.filter((x) => x.key !== metric)"
                :key="m.key"
                :label="m.label"
                :value="m.key"
              />
            </el-select>
            <el-radio-group v-model="hours" size="small" @change="loadHistory">
              <el-radio-button :value="6">6h</el-radio-button>
              <el-radio-button :value="24">24h</el-radio-button>
              <el-radio-button :value="72">3d</el-radio-button>
              <el-radio-button :value="168">7d</el-radio-button>
            </el-radio-group>
          </div>
        </div>
        <div v-if="!history.series?.length" class="chart-empty">该时间范围内暂无数据</div>
        <div v-else ref="trendRef" class="chart chart-md"></div>
        <div v-if="history.series?.length" class="chart-foot">
          对比序列使用右侧坐标轴，便于观察泥床与底流浓度的耦合关系；预警 / 危险虚线仅在阈值落入当前量程时绘制（阈值数值见右侧实时参数总表）。
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>实时参数总表</h3>
          <span class="panel-hint">{{ tsText }}</span>
        </div>
        <div class="param-table">
          <template v-for="g in metricGroups" :key="g.name">
            <div class="pt-group">{{ g.name }}</div>
            <div v-for="m in g.items" :key="m.key" class="pt-row">
              <span class="pt-label">{{ m.label }}</span>
              <span class="pt-val" :class="m.level === 'normal' ? 'ok' : m.level">
                {{ m.value === null || m.value === undefined ? '—' : fmt(m.value, decOf(m.unit)) }}
              </span>
              <span class="pt-unit">{{ m.value === null || m.value === undefined ? '' : m.unit }}</span>
              <span class="pt-thd">{{ thdText(m) }}</span>
            </div>
          </template>
        </div>
        <div class="panel-note">
          给料 P80 未接入（可选粒度指标）；其余为浓密机现场仪表实测值。
        </div>
      </div>
    </section>

    <!-- ====== 软测量 + 前瞻推演 ====== -->
    <section class="dual-row">
      <div class="panel">
        <div class="panel-head">
          <h3>底流浓度软测量</h3>
          <span class="panel-hint">{{ soft.model?.algorithm || '—' }} · 仪表变量反演</span>
        </div>

        <div v-if="soft.available" class="soft-body">
          <div class="soft-top">
            <div class="soft-now">
              <div class="sn-val" :class="softLevel">{{ fmt(soft.current.predicted, 2) }}<span>%</span></div>
              <div class="sn-label">模型预测底流浓度</div>
            </div>
            <div class="soft-cmp">
              <div class="sc-row">
                <span class="sc-k">实测值</span>
                <span class="sc-v">{{ fmt(soft.current.actual, 2) }} %</span>
              </div>
              <div class="sc-row">
                <span class="sc-k">95% 置信区间</span>
                <span class="sc-v">{{ fmt(soft.current.lower, 2) }} ~ {{ fmt(soft.current.upper, 2) }}</span>
              </div>
              <div class="sc-row">
                <span class="sc-k">实时偏差</span>
                <span class="sc-v" :class="Math.abs(soft.current.deviation) > 1 ? 'warning' : 'ok'">
                  {{ soft.current.deviation >= 0 ? '+' : '' }}{{ fmt(soft.current.deviation, 2) }} %
                </span>
              </div>
            </div>
          </div>

          <div class="model-metrics">
            <div class="mm-cell">
              <div class="mm-v">{{ fmt(soft.model.mae, 3) }}<span>%</span></div>
              <div class="mm-k">MAE 平均绝对误差</div>
            </div>
            <div class="mm-cell">
              <div class="mm-v">{{ fmt(soft.model.rmse, 3) }}</div>
              <div class="mm-k">RMSE 均方根误差</div>
            </div>
            <div class="mm-cell">
              <div class="mm-v accent">{{ fmt(soft.model.r2, 3) }}</div>
              <div class="mm-k">R² 决定系数</div>
            </div>
            <div class="mm-cell">
              <div class="mm-v">{{ soft.model.n_total ?? '—' }}</div>
              <div class="mm-k">样本（训练 {{ soft.model.n_train }} / 测试 {{ soft.model.n_test }}）</div>
            </div>
          </div>

          <div class="imp-block">
            <div class="imp-title">特征重要性（输入仪表变量 → 底流浓度）</div>
            <div class="imp-list">
              <div v-for="f in soft.model.importance" :key="f.key" class="imp-row">
                <span class="imp-label">{{ f.label }}</span>
                <div class="imp-bar"><i :style="{ width: Math.max(2, f.value * 100) + '%' }"></i></div>
                <span class="imp-val">{{ (f.value * 100).toFixed(1) }}%</span>
              </div>
            </div>
          </div>

          <div ref="trackRef" class="chart chart-track"></div>
          <div class="chart-foot">{{ soft.value_note }}</div>
        </div>
        <div v-else class="chart-empty">软测量模型暂不可用</div>
      </div>

      <div class="panel fill">
        <div class="panel-head">
          <h3>前瞻推演 · 不干预 vs 采纳推荐</h3>
          <div class="panel-tools">
            <el-radio-group v-model="fcVar" size="small" @change="renderForecast">
              <el-radio-button value="underflow_conc">底流浓度</el-radio-button>
              <el-radio-button value="bed_height">泥床高度</el-radio-button>
            </el-radio-group>
            <el-radio-group v-model="horizon" size="small" @change="loadForecast">
              <el-radio-button :value="6">6h</el-radio-button>
              <el-radio-button :value="12">12h</el-radio-button>
              <el-radio-button :value="24">24h</el-radio-button>
            </el-radio-group>
          </div>
        </div>

        <div v-if="forecast.available" class="fc-body">
          <div ref="fcRef" class="chart chart-md"></div>
          <div class="fc-summary">{{ forecast.summary?.text }}</div>
          <div class="sp-block">
            <div class="sp-title">推演所用 setpoint</div>
            <div class="sp-grid">
              <div class="sp-cell">
                <span class="sp-k">底流排放比</span>
                <span class="sp-v">
                  {{ forecast.setpoint?.discharge_ratio?.current }}
                  <el-icon class="sp-arrow"><Right /></el-icon>
                  <b>{{ forecast.setpoint?.discharge_ratio?.recommended }}</b>
                </span>
              </div>
              <div class="sp-cell">
                <span class="sp-k">絮凝剂单耗</span>
                <span class="sp-v">
                  {{ forecast.setpoint?.flocculant_dosage?.current }}
                  <el-icon class="sp-arrow"><Right /></el-icon>
                  <b>{{ forecast.setpoint?.flocculant_dosage?.recommended }}</b>
                  <span class="sp-unit">g/t</span>
                </span>
              </div>
            </div>
          </div>
          <div class="chart-foot">{{ forecast.method_note }}</div>
        </div>
        <div v-else class="chart-empty">推演模型暂不可用</div>
      </div>
    </section>

    <!-- ====== 操作建议 / 去向决策 / 节药 ====== -->
    <section class="tri-row">
      <div class="panel">
        <div class="panel-head">
          <h3>操作建议</h3>
          <div class="panel-tools">
            <span class="cond-chip" :class="condition.level">{{ condition.label || '—' }}</span>
            <span class="panel-hint">
              近 2h：泥床 {{ signed(advice.trend?.bed_delta, 2) }} m · 底流浓度
              {{ signed(advice.trend?.conc_delta, 2) }} pt
            </span>
          </div>
        </div>

        <table class="advice-table">
          <thead>
            <tr>
              <th>调整对象</th>
              <th class="num">当前</th>
              <th class="num">稳态最优</th>
              <th class="num">即时建议</th>
              <th>动作</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>底流排放比</td>
              <td class="num">{{ forecast.setpoint?.discharge_ratio?.current ?? '—' }}</td>
              <td class="num">{{ forecast.setpoint?.discharge_ratio?.recommended ?? '—' }}</td>
              <td class="num">—</td>
              <td class="act hold">维持</td>
            </tr>
            <tr>
              <td>絮凝剂单耗</td>
              <td class="num">{{ fmt(forecast.setpoint?.flocculant_dosage?.current, 1) }} g/t</td>
              <td class="num">{{ fmt(forecast.setpoint?.flocculant_dosage?.recommended, 1) }} g/t</td>
              <td class="num">{{ adviceDosage ?? '—' }}</td>
              <td class="act" :class="adviceActClass">
                {{ adviceActText }}
              </td>
            </tr>
          </tbody>
        </table>

        <div class="advice-body">
          <div v-for="(it, i) in advice.items || []" :key="i" class="advice-item" :class="it.type">
            <div class="ai-head">
              <span class="ai-label">{{ it.label }}</span>
              <span class="ai-change">
                {{ fmt(it.current, 1) }}
                <el-icon class="ai-arrow"><Right /></el-icon>
                <b>{{ fmt(it.recommended, 1) }}</b>
                <span class="ai-unit">{{ it.unit }}</span>
                <span class="ai-delta" :class="it.delta_pct > 0 ? 'up' : it.delta_pct < 0 ? 'down' : 'flat'">
                  {{ signed(it.delta_pct, 1) }}%
                </span>
              </span>
            </div>
            <div class="ai-reason">{{ it.reason }}</div>
            <div class="ai-expected">预期：{{ it.expected }}</div>
          </div>
        </div>

        <div class="basis-note">
          <b>口径说明：</b>「稳态最优」为机理模型在候选 setpoint 中搜索出的长期目标值（目标：泥床回归 5 m、底流浓度最大化，而非排放量最大）；
          「即时建议」在此之上叠加近 2 小时趋势判据，属临时修正；「30 天回溯均值」见右侧节药卡，为全期统计值。三者时间尺度不同，取值可不同。
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>底流去向决策</h3>
          <span class="panel-hint">充填 ≥ {{ dec.thresholds?.backfill_min }}% · 入库 ≥ {{ dec.thresholds?.storage_min }}%</span>
        </div>
        <div v-if="dec.available" class="dec-body">
          <div class="dec-head">
            <span class="dec-label" :class="dec.level">{{ dec.label }}</span>
            <span class="dec-conc">{{ fmt(dec.current_conc, 2) }}<span>%</span></span>
          </div>
          <div class="dec-track">
            <div class="dt-band danger" :style="{ width: bandW.danger + '%' }"></div>
            <div class="dt-band warning" :style="{ width: bandW.warning + '%' }"></div>
            <div class="dt-band success" :style="{ width: bandW.success + '%' }"></div>
            <div class="dt-marker" :style="{ left: markerPos + '%' }">
              <i></i>
              <span>{{ fmt(dec.current_conc, 1) }}%</span>
            </div>
          </div>
          <div class="dec-scale">
            <span>56</span>
            <span class="ds-tick">{{ dec.thresholds?.storage_min }} 入库</span>
            <span class="ds-tick">{{ dec.thresholds?.backfill_min }} 充填</span>
            <span>80</span>
          </div>
          <div class="dec-detail">{{ dec.detail }}</div>
          <table class="dec-table">
            <thead>
              <tr><th>去向</th><th class="num">浓度要求</th><th>用途</th><th>当前</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in decRules" :key="r.key">
                <td>{{ r.name }}</td>
                <td class="num">{{ r.rule }}</td>
                <td class="dt-use">{{ r.use }}</td>
                <td>
                  <span class="dt-flag" :class="r.state">{{ r.stateText }}</span>
                </td>
              </tr>
            </tbody>
          </table>
          <div class="panel-note">{{ dec.note }}</div>
        </div>
        <div v-else class="chart-empty">暂不可用</div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>絮凝剂节药量化</h3>
          <span class="panel-hint">近 {{ savings.days ?? 30 }} 天回溯</span>
        </div>
        <div v-if="savings.available" class="sav-body">
          <div class="sav-hero">
            <div class="sh-val">{{ savings.saving_pct }}<span>%</span></div>
            <div class="sh-meta">
              <div class="sh-k">单耗可降幅度</div>
              <div class="sh-sub">
                {{ savings.actual_dosage_avg }} → {{ savings.recommended_dosage_avg }} g/t（30 天均值）
              </div>
            </div>
          </div>
          <div class="sav-grid">
            <div class="sg-cell">
              <div class="sg-v">{{ fmt(savings.saving_kg, 1) }}<span>kg</span></div>
              <div class="sg-k">周期节药量</div>
            </div>
            <div class="sg-cell">
              <div class="sg-v accent">¥{{ fmt(savings.saving_cny, 0) }}</div>
              <div class="sg-k">周期节费</div>
            </div>
            <div class="sg-cell">
              <div class="sg-v accent">¥{{ fmt(savings.annualized_cny, 0) }}</div>
              <div class="sg-k">年化节费</div>
            </div>
              <div class="sg-cell">
                <div class="sg-v">{{ (savings.solids_treated_t / 10000).toFixed(2) }}<span>万吨</span></div>
                <div class="sg-k">周期处理固体量</div>
              </div>
              <div class="sg-cell">
                <div class="sg-v">¥{{ savings.price_cny_per_kg }}<span>/kg</span></div>
                <div class="sg-k">药剂单价（演示假设值）</div>
              </div>
              <div class="sg-cell">
                <div class="sg-v">{{ savings.samples }}<span>条</span></div>
                <div class="sg-k">参与统计读数</div>
              </div>
            </div>
          <div class="panel-note">{{ savings.method_note }}</div>
          <div class="panel-note caveat">{{ savings.price_note }}</div>
        </div>
        <div v-else class="chart-empty">暂不可用</div>
      </div>
    </section>

    <!-- ====== 工况事件 ====== -->
    <section class="panel">
      <div class="panel-head">
        <h3>工况事件与跨模块证据</h3>
        <span class="panel-hint">共 {{ events.length }} 起</span>
      </div>
      <div class="event-list">
        <div v-for="e in events" :key="e.id" class="event-item" :class="e.level">
          <div class="ev-head">
            <span class="ev-level" :class="e.level">{{ levelCn(e.level) }}</span>
            <span class="ev-title">{{ e.title }}</span>
            <span class="ev-time">{{ fmtTs(e.created_at) }}</span>
          </div>
          <div class="ev-metric">
            {{ e.metric_label }}：{{ e.baseline_value }} <el-icon class="ev-arrow"><Right /></el-icon>
            <b>{{ e.peak_value }}</b> {{ e.unit }}
          </div>
          <div class="ev-detail">{{ e.detail }}</div>
          <div v-if="e.evidence" class="ev-evidence">
            <span class="ev-chip">观察窗口 {{ e.evidence.window_hours }} h</span>
            <span class="ev-chip">峰值泥床 {{ e.evidence.peak_bed_height }} m</span>
            <span class="ev-chip">最低底流浓度 {{ e.evidence.min_underflow_conc }}%</span>
            <span class="ev-chip">最大扭矩 {{ e.evidence.max_rake_torque }}%</span>
            <span v-if="e.evidence.linked_water_event" class="ev-chip link">
              <el-icon><Connection /></el-icon>
              联动 {{ e.evidence.linked_water_event.station_id }} · 浊度事件，峰值 SS
              {{ e.evidence.linked_water_event.peak_ss }} mg/L @
              {{ fmtTs(e.evidence.linked_water_event.created_at) }}
            </span>
          </div>
        </div>
        <div v-if="events.length === 0" class="chart-empty">暂无工况事件</div>
      </div>
    </section>
  </PageShell>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import api from '../api'
import PageShell from '../components/PageShell.vue'
import StatCard from '../components/StatCard.vue'

const loading = ref(true)

const overview = ref({})
const history = ref({ series: [], points: 0 })
const soft = ref({ available: false })
const forecast = ref({ available: false })
const advice = ref({ items: [], trend: {} })
const savings = ref({})
const dec = ref({})
const events = ref([])

const metric = ref('underflow_conc')
const compare = ref('bed_height')
const hours = ref(24)
const fcVar = ref('underflow_conc')
const horizon = ref(6)

const trendRef = ref(null)
const trackRef = ref(null)
const fcRef = ref(null)
let trendChart = null
let trackChart = null
let fcChart = null

const eq = computed(() => overview.value.equipment || {})
const condition = computed(() => overview.value.condition || {})
const freshness = computed(() => overview.value.data_freshness || {})
const balance = computed(() => overview.value.balance || {})
const overflow = computed(() => overview.value.overflow || {})
const metrics = computed(() => overview.value.metrics || {})
const tsText = computed(() => fmtTs(overview.value.ts))

const metricGroups = computed(() => {
  const order = ['给料', '药剂', '设备', '出料']
  const buckets = {}
  for (const [key, m] of Object.entries(metrics.value)) {
    const g = m.group || '其他'
    if (!buckets[g]) buckets[g] = []
    buckets[g].push({ key, ...m })
  }
  return order
    .filter((g) => buckets[g]?.length)
    .map((g) => ({ name: g, items: buckets[g] }))
})

const metricOptions = computed(() =>
  Object.entries(metrics.value)
    .filter(([, m]) => m.value !== null && m.value !== undefined)
    .map(([key, m]) => ({ key, label: m.label, unit: m.unit })),
)

// ---- 工具 ----
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}
function fmt(v, d = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Number(v).toFixed(d)
}
function signed(v, d = 2) {
  if (v === null || v === undefined) return '—'
  return (v >= 0 ? '+' : '') + Number(v).toFixed(d)
}
function fmtTs(iso) {
  if (!iso) return '—'
  const s = String(iso)
  // 非当日需带月日，避免只显示时刻造成误读
  const d = new Date(s)
  const now = new Date()
  const sameDay = !Number.isNaN(d.getTime()) && d.toDateString() === now.toDateString()
  const body = s.slice(11, 16)
  return sameDay ? body : `${s.slice(5, 10)} ${body}`
}
function decOf(unit) {
  if (!unit) return 2
  if (unit === 'A') return 1
  if (unit === 'g/t') return 1
  return 2
}
function levelCn(l) {
  return { danger: '危险', warning: '预警', info: '提示', normal: '正常' }[l] || '正常'
}
// 后端 normal → CSS 里的 ok（绿色），保持默认态有颜色
function lvClass(l) {
  if (!l) return ''
  return l === 'normal' ? 'ok' : l
}

const valOf = (k) => fmt(metrics.value[k]?.value, decOf(metrics.value[k]?.unit))
const unitOf = (k) => metrics.value[k]?.unit || ''
const lvlOf = (k) => {
  const m = metrics.value[k]
  if (!m) return ''
  return m.level === 'normal' ? 'ok' : m.level
}

// ★ StatCard 的 tone 只表达状态：正常一律中性（default），越限才着色。
//   整治前这里是给"正常"上绿色 —— 满屏绿数字会让真正的异常失去对比。
const toneOf = (k) => {
  const l = lvlOf(k)
  return l === 'warning' ? 'warning' : l === 'danger' ? 'danger' : 'default'
}
const overflowTone = computed(() => {
  const l = overflow.value?.turbidity_level
  return l === 'warning' ? 'warning' : l === 'danger' ? 'danger' : 'default'
})
const hiText = (k) => {
  const m = metrics.value[k]
  if (!m) return '—'
  return m.warn_hi !== null && m.warn_hi !== undefined ? m.warn_hi : '—'
}
function rangeText(k) {
  const m = metrics.value[k]
  if (!m) return ''
  const parts = []
  if (m.warn_lo !== null && m.warn_lo !== undefined) parts.push(`下限 ${m.warn_lo}`)
  if (m.warn_hi !== null && m.warn_hi !== undefined) parts.push(`上限 ${m.warn_hi}`)
  return parts.length ? parts.join(' · ') : '无阈值'
}
function thdText(m) {
  if (m.warn_lo === null && m.warn_hi === null && m.danger_lo === null && m.danger_hi === null) return ''
  const parts = []
  if (m.danger_lo !== null && m.danger_lo !== undefined) parts.push(`危低 ${m.danger_lo}`)
  if (m.warn_lo !== null && m.warn_lo !== undefined) parts.push(`警低 ${m.warn_lo}`)
  if (m.warn_hi !== null && m.warn_hi !== undefined) parts.push(`警高 ${m.warn_hi}`)
  if (m.danger_hi !== null && m.danger_hi !== undefined) parts.push(`危高 ${m.danger_hi}`)
  return parts.join(' / ')
}

// ---- 软测量等级 ----
const softLevel = computed(() => {
  const c = soft.value.current
  if (!c) return ''
  const m = metrics.value.underflow_conc
  if (!m) return 'ok'
  return c.predicted < 60 || c.predicted > 78 ? 'warning' : 'ok'
})

// ---- 建议卡 ----
const adviceItem = computed(() => (advice.value.items || []).find((i) => i.type === 'flocculant_dosage'))
const adviceDosage = computed(() => {
  const it = adviceItem.value
  return it ? `${fmt(it.recommended, 1)} g/t` : '—'
})
const adviceActText = computed(() => {
  const it = adviceItem.value
  if (!it || it.type === 'keep') return '维持'
  return it.delta_pct > 0 ? `上调 ${it.delta_pct}%` : `下调 ${Math.abs(it.delta_pct)}%`
})
const adviceActClass = computed(() => {
  const it = adviceItem.value
  if (!it || it.type === 'keep' || !it.delta_pct) return 'hold'
  return it.delta_pct > 0 ? 'up' : 'down'
})

// ---- 去向决策刻度 ----
const DEC_MIN = 56
const DEC_MAX = 80
const bandW = computed(() => {
  const t = dec.value.thresholds || {}
  const storage = t.storage_min ?? 62
  const backfill = t.backfill_min ?? 72
  const span = DEC_MAX - DEC_MIN
  return {
    danger: ((storage - DEC_MIN) / span) * 100,
    warning: ((backfill - storage) / span) * 100,
    success: (100 - ((backfill - DEC_MIN) / span) * 100) > 0 ? 100 - ((backfill - DEC_MIN) / span) * 100 : 0,
  }
})
const markerPos = computed(() => {
  const c = dec.value.current_conc
  if (c === null || c === undefined) return 0
  const p = ((c - DEC_MIN) / (DEC_MAX - DEC_MIN)) * 100
  return Math.max(0, Math.min(100, p))
})
// 去向判定口径：阈值全部取自接口，不另设常量
const decRules = computed(() => {
  const t = dec.value.thresholds || {}
  const backfill = t.backfill_min
  const storage = t.storage_min
  const c = dec.value.current_conc
  const label = (ok) => (ok ? '满足' : '不满足')
  const state = (ok) => (ok ? 'yes' : 'no')
  if (backfill === undefined || storage === undefined || c === null || c === undefined) return []
  return [
    {
      key: 'backfill', name: '膏体充填', rule: `≥ ${backfill}%`,
      use: '井下采空区充填', state: state(c >= backfill), stateText: label(c >= backfill),
    },
    {
      key: 'storage', name: '入库堆存', rule: `≥ ${storage}%`,
      use: '尾矿库湿排堆存', state: state(c >= storage && c < backfill), stateText: label(c >= storage),
    },
    {
      key: 'dispose', name: '需处置', rule: `< ${storage}%`,
      use: '再浓缩 / 外委处置', state: state(c < storage), stateText: label(c < storage),
    },
  ]
})

// ---- 加载 ----
async function loadOverview() {
  try {
    overview.value = await api.get('/tailings/overview')
  } catch (e) {
    console.error('加载尾矿概览失败:', e)
  }
}

async function loadHistory() {
  try {
    const keys = [metric.value, compare.value].filter(Boolean)
    const res = await api.get('/tailings/history', {
      params: { hours: hours.value, metrics: keys.join(',') },
    })
    history.value = res
    await nextTick()
    renderTrend()
  } catch (e) {
    console.error('加载尾矿趋势失败:', e)
  }
}

async function loadSoft() {
  try {
    soft.value = await api.get('/tailings/underflow-soft-sensor')
    await nextTick()
    renderTrack()
  } catch (e) {
    console.error('加载软测量失败:', e)
  }
}

async function loadForecast() {
  try {
    forecast.value = await api.get('/tailings/forecast', { params: { horizon: horizon.value } })
    await nextTick()
    renderForecast()
  } catch (e) {
    console.error('加载前瞻推演失败:', e)
  }
}

async function loadAdvice() {
  try {
    advice.value = await api.get('/tailings/setpoint-advice')
  } catch (e) {
    console.error('加载操作建议失败:', e)
  }
}

async function loadSavings() {
  try {
    savings.value = await api.get('/tailings/flocculant-savings', { params: { days: 30 } })
  } catch (e) {
    console.error('加载节药量化失败:', e)
  }
}

async function loadDecision() {
  try {
    dec.value = await api.get('/tailings/discharge-decision')
  } catch (e) {
    console.error('加载去向决策失败:', e)
  }
}

async function loadEvents() {
  try {
    const res = await api.get('/tailings/events', { params: { limit: 20 } })
    events.value = res.events || []
  } catch (e) {
    console.error('加载工况事件失败:', e)
  }
}

// ---- 图表 ----
function renderTrend() {
  const series = history.value.series || []
  if (!trendRef.value || !series.length) return
  if (!trendChart) trendChart = echarts.init(trendRef.value)

  const times = (series[0].points || []).map((p) => fmtTs(p.ts))
  const yAxis = []
  const sers = []
  const colors = [cssVar('--chart-1', 'var(--color-primary)'), cssVar('--chart-2', 'var(--text-3)')]

  series.forEach((s, i) => {
    yAxis.push({
      type: 'value',
      scale: true,
      position: i === 0 ? 'left' : 'right',
      // 轴名改挂到图例（避免与右上角图例重叠）
      axisLabel: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') },
      splitLine: i === 0 ? { lineStyle: { color: cssVar('--border-2', 'var(--bg-3)') } } : { show: false },
    })

    const markLines = []
    if (i === 0) {
      if (s.warn_hi !== null && s.warn_hi !== undefined)
        markLines.push({ yAxis: s.warn_hi, label: { formatter: `预警 ${s.warn_hi}`, position: 'insideEndTop', fontSize: 10 }, lineStyle: { color: cssVar('--color-warning', 'var(--color-warning)'), type: 'dashed', width: 1 } })
      if (s.warn_lo !== null && s.warn_lo !== undefined)
        markLines.push({ yAxis: s.warn_lo, label: { formatter: `预警 ${s.warn_lo}`, position: 'insideEndBottom', fontSize: 10 }, lineStyle: { color: cssVar('--color-warning', 'var(--color-warning)'), type: 'dashed', width: 1 } })
      if (s.danger_hi !== null && s.danger_hi !== undefined)
        markLines.push({ yAxis: s.danger_hi, label: { formatter: `危险 ${s.danger_hi}`, position: 'insideEndTop', fontSize: 10 }, lineStyle: { color: cssVar('--color-danger', 'var(--color-danger)'), type: 'dashed', width: 1 } })
      if (s.danger_lo !== null && s.danger_lo !== undefined)
        markLines.push({ yAxis: s.danger_lo, label: { formatter: `危险 ${s.danger_lo}`, position: 'insideEndBottom', fontSize: 10 }, lineStyle: { color: cssVar('--color-danger', 'var(--color-danger)'), type: 'dashed', width: 1 } })
    }

    sers.push({
      name: `${s.label}${s.unit ? '（' + s.unit + '）' : ''}`,
      type: 'line',
      smooth: true,
      showSymbol: false,
      yAxisIndex: i,
      data: (s.points || []).map((p) => p.value),
      lineStyle: { width: 2, color: colors[i] },
      itemStyle: { color: colors[i] },
      areaStyle: i === 0 ? { color: cssVar('--chart-1-bg', '#e8f3ff'), opacity: 0.5 } : undefined,
      markLine: markLines.length ? { silent: true, symbol: 'none', data: markLines } : undefined,
    })
  })

  trendChart.setOption(
    {
      tooltip: { trigger: 'axis' },
      legend: { show: series.length > 1, top: 0, right: 8, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') } },
      grid: { top: series.length > 1 ? 30 : 26, right: series.length > 1 ? 56 : 46, bottom: 30, left: 54 },
      xAxis: {
        type: 'category',
        data: times,
        boundaryGap: false,
        axisLabel: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)'), hideOverlap: true },
        axisLine: { lineStyle: { color: cssVar('--border-1', 'var(--border-1)') } },
      },
      yAxis,
      series: sers,
    },
    true,
  )
}

function renderTrack() {
  const track = soft.value.track || []
  if (!trackRef.value || !track.length) return
  if (!trackChart) trackChart = echarts.init(trackRef.value)
  const times = track.map((p) => fmtTs(p.ts))
  trackChart.setOption(
    {
      tooltip: { trigger: 'axis' },
      legend: { top: 0, right: 8, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') } },
      grid: { top: 30, right: 20, bottom: 28, left: 48 },
      xAxis: {
        type: 'category',
        data: times,
        boundaryGap: false,
        axisLabel: { fontSize: 10, color: cssVar('--text-3', 'var(--text-3)'), hideOverlap: true },
        axisLine: { lineStyle: { color: cssVar('--border-1', 'var(--border-1)') } },
      },
      yAxis: {
        type: 'value',
        scale: true,
        name: '%',
        nameTextStyle: { fontSize: 10, color: cssVar('--text-3', 'var(--text-3)') },
        axisLabel: { fontSize: 10, color: cssVar('--text-3', 'var(--text-3)') },
        splitLine: { lineStyle: { color: cssVar('--border-2', 'var(--bg-3)') } },
      },
      series: [
        {
          name: '实测',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: track.map((p) => p.actual),
          lineStyle: { width: 2, color: cssVar('--chart-1', 'var(--color-primary)') },
          itemStyle: { color: cssVar('--chart-1', 'var(--color-primary)') },
        },
        {
          name: '模型预测',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: track.map((p) => p.predicted),
          lineStyle: { width: 1.5, color: cssVar('--color-warning', 'var(--color-warning)'), type: 'dashed' },
          itemStyle: { color: cssVar('--color-warning', 'var(--color-warning)') },
        },
      ],
    },
    true,
  )
}

function renderForecast() {
  const fc = forecast.value
  if (!fcRef.value || !fc.available || !fc.baseline?.length) return
  if (!fcChart) fcChart = echarts.init(fcRef.value)
  const key = fcVar.value
  const unit = key === 'bed_height' ? 'm' : '%'
  const times = fc.baseline.map((p) => `${p.t_h}h`)

  fcChart.setOption(
    {
      tooltip: { trigger: 'axis' },
      legend: { top: 0, right: 8, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') } },
      grid: { top: 30, right: 46, bottom: 30, left: 54 },
      xAxis: {
        type: 'category',
        data: times,
        boundaryGap: false,
        axisLabel: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)'), hideOverlap: true },
        axisLine: { lineStyle: { color: cssVar('--border-1', 'var(--border-1)') } },
      },
      yAxis: {
        type: 'value',
        scale: true,
        name: unit,
        nameTextStyle: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') },
        axisLabel: { fontSize: 11, color: cssVar('--text-3', 'var(--text-3)') },
        splitLine: { lineStyle: { color: cssVar('--border-2', 'var(--bg-3)') } },
      },
      series: [
        {
          name: '不干预',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: fc.baseline.map((p) => p[key]),
          lineStyle: { width: 2, color: cssVar('--color-warning', 'var(--color-warning)'), type: 'dashed' },
          itemStyle: { color: cssVar('--color-warning', 'var(--color-warning)') },
        },
        {
          name: '采纳推荐',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: fc.recommended.map((p) => p[key]),
          lineStyle: { width: 2, color: cssVar('--chart-1', 'var(--color-primary)') },
          itemStyle: { color: cssVar('--chart-1', 'var(--color-primary)') },
          areaStyle: { color: cssVar('--chart-1-bg', '#e8f3ff'), opacity: 0.4 },
        },
      ],
    },
    true,
  )
}

function resizeCharts() {
  trendChart?.resize()
  trackChart?.resize()
  fcChart?.resize()
}

async function loadAll() {
  loading.value = true
  try {
    await loadOverview()
    await Promise.all([loadHistory(), loadSoft(), loadForecast(), loadAdvice(), loadSavings(), loadDecision(), loadEvents()])
  } finally {
    loading.value = false
  }
  // fill 面板高度依赖同行兄弟节点，等布局落定后再让 echarts 适配一次
  await nextTick()
  setTimeout(resizeCharts, 160)
}

let refreshTimer = null
onMounted(async () => {
  window.addEventListener('resize', resizeCharts)
  await loadAll()
  refreshTimer = setInterval(() => {
    loadOverview()
    loadDecision()
  }, 60000)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeCharts)
  if (refreshTimer) clearInterval(refreshTimer)
  trendChart?.dispose()
  trackChart?.dispose()
  fcChart?.dispose()
  trendChart = null
  trackChart = null
  fcChart = null
})
</script>

<style scoped>
.tailings-page {
  padding: var(--space-4) var(--space-5) var(--space-8);
  background: var(--bg-2);
  min-height: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* ====== 设备头 ====== */
.equip-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-left: 3px solid var(--color-primary);
  border-radius: var(--radius);
  padding: 12px 16px;
}
.eq-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.eq-name {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
}
/* 功能名（= navMap 短名）：弱化处理，与设备名形成"主体 · 归属"的层次 */
.eq-page-tag {
  font-size: 12px;
  color: var(--text-3);
  padding-left: 10px;
  border-left: 1px solid var(--border-1);
  line-height: 1.1;
}
.cond-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--color-success-light);
  color: var(--color-success);
}
.cond-badge.warning { background: var(--color-warning-light); color: var(--color-warning); }
.cond-badge.danger { background: var(--color-danger-light); color: var(--color-danger); }
.eq-meta {
  margin-top: 4px;
  font-size: var(--text-xs);
  color: var(--text-3);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.eq-sep { color: var(--text-4); }
.eq-side {
  text-align: right;
  flex-shrink: 0;
}
.eq-cond-desc {
  font-size: var(--text-xs);
  color: var(--text-2);
  max-width: 520px;
}
.eq-fresh {
  margin-top: 4px;
  font-size: var(--text-xs);
  color: var(--text-3);
}
.eq-fresh b { color: var(--text-1); font-variant-numeric: tabular-nums; }
.fresh-age { color: var(--text-4); }

/* ====== KPI ====== */
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-radius: var(--radius);
}
.kpi-cell {
  padding: 12px 16px;
  border-right: 1px solid var(--border-1);
  min-width: 0;
}
.kpi-cell:last-child { border-right: none; }
.kpi-num {
  font-size: var(--text-2xl);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.kpi-num.ok { color: var(--color-success); }
.kpi-num.warning { color: var(--color-warning); }
.kpi-num.danger { color: var(--color-danger); }
.kpi-unit {
  font-size: var(--text-xs);
  font-weight: var(--weight-normal);
  color: var(--text-3);
  margin-left: 3px;
}
.kpi-label { margin-top: 3px; font-size: var(--text-xs); color: var(--text-2); }
.kpi-sub { margin-top: 2px; font-size: 11px; color: var(--text-3); }
.kpi-sub .ok { color: var(--color-success); }
.kpi-sub .warning { color: var(--color-warning); }
.kpi-sub .danger { color: var(--color-danger); }

/* KPI 网格：6 格（StatCard 本体统一，格数由业务量决定） */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
}

/* ====== 布局 ====== */
.dual-row {
  display: grid;
  grid-template-columns: 1.05fr 1fr;
  gap: var(--space-3);
}
.dual-row.wide-left { grid-template-columns: 1.5fr 1fr; }
/* 三栏：操作建议 / 去向决策 / 节药，高度天然接近，避免留白 */
.tri-row {
  display: grid;
  grid-template-columns: 1.5fr 1fr 1fr;
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
.panel-hint { font-size: var(--text-xs); color: var(--text-3); }
.panel-note {
  margin-top: 8px;
  font-size: 11px;
  color: var(--text-3);
  line-height: var(--leading-normal);
}
.panel-note.caveat { color: var(--text-4); }
.vs { font-size: var(--text-xs); color: var(--text-3); }
.chart { width: 100%; }
.chart-md { height: 288px; }
/* fill 面板：图表吃掉同行富余高度，避免大屏留白 */
.panel.fill { display: flex; flex-direction: column; }
.panel.fill .chart-md { flex: 1 1 auto; height: auto; min-height: 288px; }
.panel.fill .fc-body {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}
.fc-body .chart-md { flex: 1 1 auto; height: auto; min-height: 288px; }
.chart-track { height: 180px; margin-top: 12px; }
.chart-foot {
  margin-top: 8px;
  font-size: 11px;
  color: var(--text-3);
  line-height: var(--leading-normal);
}
.chart-empty {
  text-align: center;
  padding: 40px 0;
  color: var(--text-3);
  font-size: var(--text-sm);
}
.cond-chip {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--color-success-light);
  color: var(--color-success);
}
.cond-chip.warning { background: var(--color-warning-light); color: var(--color-warning); }
.cond-chip.danger { background: var(--color-danger-light); color: var(--color-danger); }

/* ====== 实时参数总表 ====== */
.param-table { display: flex; flex-direction: column; }
.pt-group {
  font-size: 11px;
  color: var(--text-3);
  font-weight: var(--weight-medium);
  padding: 6px 0 4px;
  border-bottom: 1px solid var(--border-1);
  margin-bottom: 2px;
}
.pt-group:not(:first-child) { margin-top: 8px; }
.pt-row {
  display: grid;
  grid-template-columns: 1fr 62px 52px 1.35fr;
  align-items: baseline;
  gap: 6px;
  padding: 5px 0;
  border-bottom: 1px solid var(--border-2);
  font-size: var(--text-xs);
}
.pt-row:last-child { border-bottom: none; }
.pt-label { color: var(--text-2); }
.pt-val {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-weight: var(--weight-medium);
  color: var(--text-1);
}
.pt-val.ok { color: var(--color-success); }
.pt-val.warning { color: var(--color-warning); }
.pt-val.danger { color: var(--color-danger); }
.pt-unit { color: var(--text-4); font-size: 11px; }
.pt-thd { color: var(--text-4); font-size: 11px; text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* ====== 软测量 ====== */
.soft-top {
  display: flex;
  align-items: center;
  gap: 24px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-2);
}
.sn-val {
  font-size: 34px;
  font-weight: var(--weight-semibold);
  line-height: 1;
  font-variant-numeric: tabular-nums;
  color: var(--color-success);
}
.sn-val.warning { color: var(--color-warning); }
.sn-val.danger { color: var(--color-danger); }
.sn-val span { font-size: var(--text-sm); font-weight: var(--weight-normal); color: var(--text-3); margin-left: 3px; }
.sn-label { margin-top: 5px; font-size: var(--text-xs); color: var(--text-3); }
.soft-cmp { flex: 1; display: flex; flex-direction: column; gap: 5px; }
.sc-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: var(--text-xs);
}
.sc-k { color: var(--text-3); }
.sc-v { color: var(--text-1); font-variant-numeric: tabular-nums; font-weight: var(--weight-medium); }
.sc-v.ok { color: var(--color-success); }
.sc-v.warning { color: var(--color-warning); }

.model-metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-2);
  margin: 12px 0;
}
.mm-cell {
  background: var(--bg-2);
  border-radius: var(--radius-sm);
  padding: 9px 11px;
  min-width: 0;
}
.mm-v {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.mm-v.accent { color: var(--color-primary); }
.mm-v span { font-size: var(--text-xs); font-weight: var(--weight-normal); color: var(--text-3); margin-left: 2px; }
.mm-k { margin-top: 3px; font-size: 11px; color: var(--text-3); }

.imp-block { margin-bottom: 4px; }
.imp-title { font-size: 11px; color: var(--text-3); margin-bottom: 7px; }
.imp-list { display: flex; flex-direction: column; gap: 6px; }
.imp-row {
  display: grid;
  grid-template-columns: 76px 1fr 46px;
  align-items: center;
  gap: 8px;
  font-size: var(--text-xs);
}
.imp-label { color: var(--text-2); white-space: nowrap; }
.imp-bar {
  height: 6px;
  background: var(--bg-3);
  border-radius: 3px;
  overflow: hidden;
}
.imp-bar i { display: block; height: 100%; border-radius: 3px; background: var(--color-primary); }
.imp-val { text-align: right; color: var(--text-1); font-variant-numeric: tabular-nums; }

/* ====== 前瞻推演 ====== */
.fc-summary {
  margin-top: 10px;
  font-size: var(--text-sm);
  color: var(--text-2);
  background: var(--bg-2);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  line-height: var(--leading-normal);
}
.sp-block { margin-top: 10px; }
.sp-title { font-size: 11px; color: var(--text-3); margin-bottom: 7px; }
.sp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); }
.sp-cell {
  background: var(--bg-2);
  border-radius: var(--radius-sm);
  padding: 8px 11px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sp-k { font-size: 11px; color: var(--text-3); }
.sp-v {
  font-size: var(--text-sm);
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
  display: flex;
  align-items: center;
  gap: 5px;
}
.sp-v b { color: var(--color-primary); font-weight: var(--weight-semibold); }
.sp-arrow { color: var(--text-4); font-size: 12px; }
.sp-unit { font-size: 11px; color: var(--text-3); }

/* ====== 操作建议 ====== */
.advice-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-xs);
  margin-bottom: 12px;
}
.advice-table th {
  text-align: left;
  font-weight: var(--weight-medium);
  color: var(--text-3);
  padding: 6px 8px;
  border-bottom: 1px solid var(--border-1);
  white-space: nowrap;
}
.advice-table th.num, .advice-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
.advice-table td {
  padding: 8px;
  border-bottom: 1px solid var(--border-2);
  color: var(--text-2);
}
.advice-table td:first-child { color: var(--text-1); font-weight: var(--weight-medium); }
.act {
  display: inline-block;
  font-size: 11px;
  padding: 1px 7px;
  border-radius: var(--radius-sm);
  background: var(--bg-3);
  color: var(--text-2);
}
.act.up { background: var(--color-primary-bg); color: var(--color-primary); }
.act.down { background: var(--color-success-light); color: var(--color-success); }
.act.hold { background: var(--bg-3); color: var(--text-2); }

.advice-body { display: flex; flex-direction: column; gap: 8px; }
.advice-item {
  border: 1px solid var(--border-1);
  border-left: 3px solid var(--color-primary);
  border-radius: var(--radius-sm);
  padding: 9px 12px;
}
.advice-item.keep { border-left-color: var(--color-success); }
.ai-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 5px;
}
.ai-label { font-size: var(--text-sm); font-weight: var(--weight-medium); color: var(--text-1); }
.ai-change {
  font-size: var(--text-xs);
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}
.ai-change b { color: var(--color-primary); font-weight: var(--weight-semibold); }
.ai-arrow { color: var(--text-4); font-size: 12px; }
.ai-unit { color: var(--text-3); }
.ai-delta { font-size: 11px; }
.ai-delta.up { color: var(--color-primary); }
.ai-delta.down { color: var(--color-success); }
.ai-delta.flat { color: var(--text-3); }
.ai-reason { font-size: var(--text-xs); color: var(--text-2); line-height: var(--leading-normal); }
.ai-expected { margin-top: 3px; font-size: 11px; color: var(--text-3); line-height: var(--leading-normal); }

.basis-note {
  margin-top: 12px;
  font-size: 11px;
  color: var(--text-3);
  line-height: var(--leading-relaxed);
  background: var(--bg-2);
  border-radius: var(--radius-sm);
  padding: 9px 12px;
}
.basis-note b { color: var(--text-2); }

/* ====== 去向决策 ====== */
.dec-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 12px;
}
.dec-label {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
}
.dec-label.success { color: var(--color-success); }
.dec-label.warning { color: var(--color-warning); }
.dec-label.danger { color: var(--color-danger); }
.dec-conc {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
}
.dec-conc span { font-size: var(--text-xs); font-weight: var(--weight-normal); color: var(--text-3); margin-left: 2px; }
.dec-track {
  position: relative;
  display: flex;
  height: 10px;
  border-radius: 5px;
  overflow: visible;
  margin-bottom: 22px;
}
.dt-band { height: 100%; }
.dt-band:first-child { border-radius: 5px 0 0 5px; }
.dt-band:last-child { border-radius: 0 5px 5px 0; }
.dt-band.danger { background: rgba(245, 63, 63, 0.3); }
.dt-band.warning { background: rgba(255, 125, 0, 0.3); }
.dt-band.success { background: rgba(0, 180, 42, 0.3); }
.dt-marker {
  position: absolute;
  top: -4px;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  pointer-events: none;
}
.dt-marker i {
  width: 2px;
  height: 18px;
  background: var(--text-1);
  border-radius: 1px;
}
.dt-marker span {
  margin-top: 2px;
  font-size: 10px;
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.dec-scale {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--text-4);
  margin-bottom: 10px;
}
.ds-tick { color: var(--text-3); }
.dec-detail {
  font-size: var(--text-xs);
  color: var(--text-2);
  line-height: var(--leading-relaxed);
  background: var(--bg-2);
  border-radius: var(--radius-sm);
  padding: 9px 12px;
}
.dec-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-xs);
  margin-top: 12px;
}
.dec-table th {
  text-align: left;
  font-weight: var(--weight-medium);
  color: var(--text-3);
  padding: 5px 6px;
  border-bottom: 1px solid var(--border-1);
  white-space: nowrap;
}
.dec-table th.num, .dec-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
.dec-table td {
  padding: 6px;
  border-bottom: 1px solid var(--border-2);
  color: var(--text-2);
  white-space: nowrap;
}
.dec-table td:first-child { color: var(--text-1); font-weight: var(--weight-medium); }
.dt-use { color: var(--text-3) !important; font-weight: var(--weight-normal) !important; }
.dt-flag {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
}
.dt-flag.yes { background: var(--color-success-light); color: var(--color-success); }
.dt-flag.no { background: var(--bg-3); color: var(--text-3); }

/* ====== 节药 ====== */
.sav-hero {
  display: flex;
  align-items: center;
  gap: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-2);
}
.sh-val {
  font-size: 32px;
  font-weight: var(--weight-semibold);
  color: var(--color-primary);
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.sh-val span { font-size: var(--text-sm); font-weight: var(--weight-normal); color: var(--text-3); margin-left: 2px; }
.sh-k { font-size: var(--text-sm); color: var(--text-1); }
.sh-sub { margin-top: 3px; font-size: var(--text-xs); color: var(--text-3); font-variant-numeric: tabular-nums; }
.sav-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-2);
  margin-top: 12px;
}
.sg-cell {
  background: var(--bg-2);
  border-radius: var(--radius-sm);
  padding: 9px 11px;
}
.sg-v {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.sg-v.accent { color: var(--color-primary); }
.sg-v span { font-size: 11px; font-weight: var(--weight-normal); color: var(--text-3); margin-left: 2px; }
.sg-k { margin-top: 3px; font-size: 11px; color: var(--text-3); }

/* ====== 事件 ====== */
.event-list { display: flex; flex-direction: column; gap: 10px; }
.event-item {
  border: 1px solid var(--border-1);
  border-left: 3px solid var(--text-3);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
}
.event-item.danger { border-left-color: var(--color-danger); }
.event-item.warning { border-left-color: var(--color-warning); }
.event-item.info { border-left-color: var(--color-primary); }
.ev-head { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
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
.ev-level.info { background: var(--color-primary-bg); color: var(--color-primary); }
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
.ev-time { font-size: var(--text-xs); color: var(--text-3); flex-shrink: 0; font-variant-numeric: tabular-nums; }
.ev-metric {
  font-size: var(--text-xs);
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
  margin-bottom: 5px;
  display: flex;
  align-items: center;
  gap: 5px;
}
.ev-metric b { color: var(--color-danger); }
.ev-arrow { color: var(--text-4); font-size: 12px; }
.ev-detail { font-size: var(--text-xs); color: var(--text-3); line-height: var(--leading-normal); }
.ev-evidence {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.ev-chip {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-2);
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}
.ev-chip.link {
  background: var(--color-primary-bg);
  color: var(--color-primary);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

/* ====== 响应式 ====== */
@media (max-width: 1500px) {
  .kpi-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .kpi-cell:nth-child(3n) { border-right: none; }
  .dual-row.wide-left { grid-template-columns: 1.2fr 1fr; }
  .tri-row { grid-template-columns: 1.3fr 1fr 1fr; }
}
@media (max-width: 1400px) {
  .dual-row, .dual-row.wide-left, .tri-row { grid-template-columns: 1fr; }
}
@media (max-width: 900px) {
  .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .model-metrics { grid-template-columns: repeat(2, 1fr); }
  .sp-grid { grid-template-columns: 1fr; }
  .sav-grid { grid-template-columns: 1fr 1fr; }
  .equip-strip { flex-direction: column; align-items: flex-start; }
  .eq-side { text-align: left; }
}
</style>
