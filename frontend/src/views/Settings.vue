<script setup>
/**
 * 系统设置 —— 这一页每一块都接真实数据源，没有"演示模式"。
 *
 *  · 系统信息    /api/settings/overview → system（真实库大小 / 数据目录 / 表数 / 运行时长）
 *  · 场景与数据  /api/scenes（6 个场景包的记录数与在跑模型）
 *  · 模型版本    /api/settings/overview → models（model_versions 台账）
 *  · 告警阈值    /api/settings/overview → scenes[].alarm_rules（场景包 YAML 的真实规则）
 *               改完 PUT …/alarm-rules → 写回 YAML + 刷新规则缓存，**下一次分析立即用新阈值**
 *
 * ⚠️ 历史版本这里是"演示模式"：阈值是编的（pb 0.8/2.5，而真实是 YAML 里的 <4.0/<2.5）、
 *    保存只弹 toast、四个开关（自动刷新/告警声音/紧凑视图…）没有任何消费者。
 *    那次改造把假的全部删掉/换成真的 —— 宁缺勿假。
 */
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const loading = ref(true)
const sys = ref(null)
const sceneCfgs = ref([])   // 有 YAML 配置的场景（带 alarm_rules / pipeline / sources）
const sceneRows = ref([])   // /api/scenes 全量场景（带 records / groups / active_model）
const models = ref([])
const draft = ref({})       // 阈值编辑态 { scene: { ruleName: value } }
const saving = ref('')      // 正在保存的场景名

const sceneNameMap = computed(() => {
  const m = {}
  for (const s of sceneRows.value) m[s.name] = s.display_name
  return m
})

async function loadAll() {
  loading.value = true
  try {
    const [ov, sc] = await Promise.all([
      api.get('/settings/overview'),
      api.get('/scenes'),
    ])
    sys.value = ov.system
    sceneCfgs.value = ov.scenes || []
    models.value = ov.models || []
    sceneRows.value = sc.scenes || []
    // 编辑态 = 深拷贝当前值（不直接绑原对象，才能算"有无改动"）
    const d = {}
    for (const s of sceneCfgs.value) {
      d[s.name] = {}
      for (const r of (s.alarm_rules || [])) d[s.name][r.name] = r.threshold
    }
    draft.value = d
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// ---- 阈值编辑 ----
const FRACTIONAL = /grade|ratio|rate|compliance/
const stepFor = (r) => (FRACTIONAL.test(r.field || '') ? 0.05 : 1)
const precisionFor = (r) => (stepFor(r) < 1 ? 2 : 0)
// 展示用格式化：按字段精度补零（4 → "4.00"，75 → "75"），
// 否则确认框里会出现「（原 4）」这种与被改值（1.50）风格不一致的写法
const fmtTh = (r, v) => Number(v).toFixed(precisionFor(r))

function dirtyRules(s) {
  const d = draft.value[s.name] || {}
  return (s.alarm_rules || []).filter((r) => Number(d[r.name]) !== Number(r.threshold))
}

function resetRules(s) {
  const d = { ...(draft.value[s.name] || {}) }
  for (const r of (s.alarm_rules || [])) d[r.name] = r.threshold
  draft.value[s.name] = d
  ElMessage.info('已还原为文件中的当前值')
}

async function saveRules(s) {
  const changed = dirtyRules(s)
  if (!changed.length) {
    ElMessage.info(`${s.display_name}：没有改动`)
    return
  }
  const rules = changed.map((r) => ({ name: r.name, threshold: Number(draft.value[s.name][r.name]) }))
  const preview = changed
    .map((r) => `· ${r.field_label} ${r.op_label} ${fmtTh(r, draft.value[s.name][r.name])}（原 ${fmtTh(r, r.threshold)}）`)
    .join('\n')
  try {
    await ElMessageBox.confirm(
      `将 ${s.display_name} 的告警阈值改为：\n${preview}\n\n保存后立即生效 —— 下一次分析就用新阈值。`,
      '确认修改告警阈值',
      { confirmButtonText: '保存并生效', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  saving.value = s.name
  try {
    const res = await api.put(`/settings/scenes/${s.name}/alarm-rules`, { rules })
    const list = (res.changed || [])
      .map((c) => {
        const r = (s.alarm_rules || []).find((x) => x.name === c.name) || {}
        return `${r.field_label || c.name} ${fmtTh(r, c.before)} → ${fmtTh(r, c.after)}`
      })
      .join('、')
    ElMessage.success(`已保存并生效：${list || '无实际变化'}`)
    await loadAll()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    saving.value = ''
  }
}

// ---- 展示辅助 ----
function fmtUptime(sec) {
  if (sec == null) return '—'
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d) return `${d} 天 ${h} 小时`
  if (h) return `${h} 小时 ${m} 分`
  return `${m} 分 ${sec % 60} 秒`
}

function modelMetric(m) {
  const met = m.metrics || {}
  const parts = []
  if (met.test_mae_temporal != null) parts.push(`MAE ${met.test_mae_temporal}`)
  if (met.mae != null) parts.push(`MAE ${met.mae}`)
  if (met.r2 != null) parts.push(`R² ${met.r2}`)
  if (met.improvement_vs_manual) parts.push(`较人工 ${met.improvement_vs_manual}`)
  if (met.features != null) parts.push(`${met.features} 维特征`)
  return parts.join(' · ')
}

onMounted(loadAll)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h2>设置</h2>
      <span class="page-sub">配置均来自真实数据源：场景包 YAML + 模型台账，改动即生效</span>
    </div>

    <el-row :gutter="20">
      <!-- ══════ 左列 ══════ -->
      <el-col :xs="24" :md="12">
        <!-- 系统信息 -->
        <el-card shadow="never" class="section-card">
          <template #header><span class="card-title">系统信息</span></template>
          <el-descriptions v-if="sys" :column="1" border size="small">
            <el-descriptions-item label="平台名称">{{ sys.app }}</el-descriptions-item>
            <el-descriptions-item label="版本号">{{ sys.version }}</el-descriptions-item>
            <el-descriptions-item label="后端状态">
              <el-tag type="success" size="small" effect="dark">运行中</el-tag>
              <span class="dim">已运行 {{ fmtUptime(sys.uptime_sec) }} · {{ sys.boot_at }} 启动</span>
            </el-descriptions-item>
            <el-descriptions-item label="API 文档">
              <a href="/docs" target="_blank" class="link">Swagger UI →</a>
            </el-descriptions-item>
            <el-descriptions-item label="数据库">
              SQLite · <b>{{ sys.db_size_mb }} MB</b> · {{ sys.table_count }} 张表
              <div class="dim mono">{{ sys.db_path }}</div>
            </el-descriptions-item>
            <el-descriptions-item label="数据目录">
              <b>{{ sys.data_dir_mb }} MB</b>
              <div class="dim mono">{{ sys.data_dir }}</div>
            </el-descriptions-item>
          </el-descriptions>
          <el-skeleton v-else :rows="6" animated />
        </el-card>

        <!-- 场景与数据 -->
        <el-card shadow="never" class="section-card">
          <template #header>
            <span class="card-title">场景与数据</span>
            <span class="card-sub">{{ sceneRows.length }} 个场景包</span>
          </template>
          <div class="scene-list">
            <div v-for="s in sceneRows" :key="s.name" class="scene-item">
              <div class="scene-info">
                <el-tag :type="s.status === 'active' ? 'success' : 'info'" size="small" effect="dark">
                  {{ s.status === 'active' ? '运行中' : '未激活' }}
                </el-tag>
                <span class="scene-name">{{ s.display_name }}</span>
              </div>
              <div class="scene-stats">
                <span><b>{{ s.records == null ? '—' : s.records.toLocaleString() }}</b> 记录</span>
                <span><b>{{ s.groups == null ? '—' : s.groups.toLocaleString() }}</b> 组</span>
                <span class="mono dim-inline">{{ s.active_model || '—' }}</span>
              </div>
            </div>
          </div>
          <div class="hint">
            「记录」= 该场景自有数据表的沉淀条数；设备健康监测为实时计算、不沉淀历史，故显示「—」。
          </div>
        </el-card>

        <!-- 模型版本 -->
        <el-card shadow="never" class="section-card">
          <template #header>
            <span class="card-title">模型版本</span>
            <span class="card-sub">{{ models.length }} 个已注册</span>
          </template>
          <div class="model-list">
            <div v-for="m in models" :key="m.id" class="model-item">
              <div class="model-head">
                <el-tag size="small" :type="m.is_active ? 'success' : 'info'" effect="dark">
                  {{ m.is_active ? '在线' : '未激活' }}
                </el-tag>
                <span class="model-ver mono">{{ m.version }}</span>
                <span class="model-scene">{{ sceneNameMap[m.scene_id] || m.scene_id }}</span>
                <span class="model-health" :class="m.health_status === 'healthy' ? 'ok' : 'bad'">
                  {{ m.health_status === 'healthy' ? '健康' : m.health_status || '—' }}
                </span>
              </div>
              <div class="model-meta">
                <span>{{ m.algorithm }}</span>
                <span v-if="modelMetric(m)" class="hl">{{ modelMetric(m) }}</span>
                <span>训练样本 {{ m.training_data_count == null ? '—' : m.training_data_count }}</span>
                <span>累计推理 {{ m.total_inferences }} 次</span>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>

      <!-- ══════ 右列：告警阈值（真配置） ══════ -->
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="section-card">
          <template #header>
            <span class="card-title">告警阈值</span>
            <span class="card-sub">来源：场景包 YAML · 保存后立即生效</span>
          </template>

          <div v-for="s in sceneCfgs" :key="s.name" class="rule-group">
            <div class="rule-group-head">
              <span class="rule-scene">{{ sceneNameMap[s.name] || s.display_name }}</span>
              <span class="rule-count">{{ (s.alarm_rules || []).length }} 条规则</span>
              <el-button link size="small" @click="resetRules(s)">还原</el-button>
            </div>

            <div v-for="r in (s.alarm_rules || [])" :key="r.name" class="rule-row">
              <span class="rule-level" :class="r.level"></span>
              <div class="rule-info">
                <span class="rule-field">{{ r.field_label }} {{ r.op_label }}</span>
                <span class="rule-name mono">{{ r.name }}</span>
              </div>
              <el-input-number
                v-model="draft[s.name][r.name]"
                size="small"
                controls-position="right"
                :step="stepFor(r)"
                :precision="precisionFor(r)"
                :min="0"
                class="rule-input"
              />
              <el-tag size="small" :type="r.level === 'danger' ? 'danger' : 'warning'" effect="plain">
                {{ r.level_label }}
              </el-tag>
            </div>

            <div class="rule-actions">
              <el-button
                type="primary"
                size="small"
                :loading="saving === s.name"
                :disabled="!dirtyRules(s).length"
                @click="saveRules(s)"
              >
                保存{{ dirtyRules(s).length ? `（${dirtyRules(s).length} 处改动）` : '' }}
              </el-button>
              <span v-if="dirtyRules(s).length" class="dirty-hint">
                待保存：{{ dirtyRules(s).map((r) => `${r.field_label} → ${fmtTh(r, draft[s.name][r.name])}`).join('、') }}
              </span>
            </div>
          </div>

          <div class="hint">
            阈值直接写回场景包 YAML（保留文件内注释）并即时刷新规则缓存 —— **下一次分析就用新值**。
            想亲眼验证：把「铅品位 低于」调到实测值以下，再分析一帧，该条告警立刻消失。
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.page { max-width: 1240px; margin: 0 auto; padding: 20px; box-sizing: border-box; }
.page-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 18px; }
h2 { color: #1d2129; font-size: 22px; margin: 0; }
.page-sub { font-size: 12px; color: #86909c; }

.section-card { margin-bottom: 18px; }
.card-title { font-size: 15px; font-weight: 600; color: #1d2129; }
.card-sub { margin-left: 10px; font-size: 12px; color: #86909c; }

.link { color: #165dff; text-decoration: none; }
.mono { font-family: Consolas, Menlo, monospace; }
.dim { font-size: 12px; color: #86909c; margin-top: 2px; word-break: break-all; }
.dim-inline { color: #86909c; }
.hint { margin-top: 12px; font-size: 12px; color: #86909c; line-height: 1.7; }

/* 场景与数据 */
.scene-list { display: flex; flex-direction: column; gap: 8px; }
.scene-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 9px 12px; background: #f7f8fa; border-radius: 6px;
}
.scene-info { display: flex; align-items: center; gap: 8px; }
.scene-name { font-weight: 600; color: #1d2129; font-size: 14px; }
.scene-stats { display: flex; gap: 14px; font-size: 12px; color: #4e5969; }
.scene-stats b { color: #165dff; }

/* 模型版本 */
.model-list { display: flex; flex-direction: column; gap: 10px; }
.model-item { padding: 10px 12px; background: #f7f8fa; border-radius: 6px; }
.model-head { display: flex; align-items: center; gap: 8px; }
.model-ver { font-weight: 600; font-size: 13px; color: #1d2129; }
.model-scene { font-size: 12px; color: #4e5969; }
.model-health { margin-left: auto; font-size: 12px; }
.model-health.ok { color: #00b42a; }
.model-health.bad { color: #f53f3f; }
.model-meta { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; color: #86909c; }
.model-meta .hl { color: #165dff; font-weight: 600; }

/* 告警阈值 */
.rule-group { padding-bottom: 14px; margin-bottom: 14px; border-bottom: 1px solid #f0f0f0; }
.rule-group:last-of-type { border-bottom: none; margin-bottom: 4px; padding-bottom: 4px; }
.rule-group-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.rule-scene { font-weight: 600; color: #1d2129; font-size: 14px; }
.rule-count { font-size: 12px; color: #86909c; }
.rule-group-head .el-button { margin-left: auto; }

.rule-row { display: flex; align-items: center; gap: 10px; padding: 5px 0; }
.rule-level { width: 3px; height: 26px; border-radius: 2px; flex: none; }
.rule-level.warning { background: #ff7d00; }
.rule-level.danger { background: #f53f3f; }
.rule-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.rule-field { font-size: 13px; color: #1d2129; }
.rule-name { font-size: 11px; color: #c9cdd4; }
.rule-input { width: 118px; flex: none; }

.rule-actions { margin-top: 8px; display: flex; align-items: center; gap: 10px; }
.dirty-hint { font-size: 12px; color: #ff7d00; }
</style>
