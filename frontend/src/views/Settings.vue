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
 *
 * ★ 2026-10-19 一致性整治 P0 试点页：
 *    原来 0 处使用设计令牌（27 处硬编码色），页头与另外 7 个页面各不相同。
 *    现在统一走 PageShell + 4 格 StatCard + Panel，颜色全部来自 tokens.css。
 *    KPI 四格都来自已有真实字段，没有为了"凑四格"新造指标。
 */
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import PageShell from '../components/PageShell.vue'
import Panel from '../components/Panel.vue'
import StatCard from '../components/StatCard.vue'

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

// ── 顶部四格 KPI：全部取自已加载的真实数据，不新造指标 ──
const activeScenes = computed(() => sceneRows.value.filter((s) => s.status === 'active').length)
const activeModels = computed(() => models.value.filter((m) => m.is_active).length)

/** 运行时长压缩成"KPI 格放得下"的写法（28px 字号下超过 8 字会挤压） */
const uptimeShort = computed(() => {
  const sec = sys.value?.uptime_sec
  if (sec == null) return '—'
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d) return `${d} 天 ${h} 时`
  if (h) return `${h} 时 ${m} 分`
  return `${m} 分`
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

// ══════════════════════════════════════════════════
// 告警对外接入（2026-10-19）
// ★ 这一块回答的是"你的告警能不能接别的软件"：
//   入站给接口和钥匙，出站给订阅列表和投递状态。
//   所有数字都来自后端真实接口 —— 不编"已连接 N 个系统"这种看着好看的空话。
// ══════════════════════════════════════════════════
const integration = ref({})
const hooks = ref([])
const newHookUrl = ref('')
const newHookDesc = ref('')
const hookSaving = ref(false)
const hookTesting = ref(0)

const wsOnline = computed(() => integration.value.ws_online?.alarms ?? 0)

async function loadIntegration() {
  try {
    const [info, list] = await Promise.all([
      api.get('/webhooks/integration'),
      api.get('/webhooks'),
    ])
    integration.value = info || {}
    hooks.value = list?.subscriptions || []
  } catch (e) {
    console.error('加载对外接入信息失败:', e)
  }
}

/** 投递状态 → 圆角标签（只表达事实：正常 / 失败 / 还没推过 / 已停用） */
function hookState(w) {
  if (!w.enabled) return { cls: 'off', text: '已停用' }
  if (!w.last_attempt_at) return { cls: 'idle', text: '等待首推' }
  const ok = w.last_status >= 200 && w.last_status < 300
  return ok
    ? { cls: 'ok', text: `正常 ${w.last_status}` }
    : { cls: 'bad', text: w.last_status ? `失败 ${w.last_status}` : '连接失败' }
}

async function addHook() {
  const url = newHookUrl.value.trim()
  if (!/^https?:\/\//.test(url)) {
    ElMessage.warning('回调地址必须以 http:// 或 https:// 开头')
    return
  }
  hookSaving.value = true
  try {
    const r = await api.post('/webhooks', {
      url,
      events: ['alarm.created'],
      description: newHookDesc.value.trim(),
    })
    newHookUrl.value = ''
    newHookDesc.value = ''
    await loadIntegration()
    // 密钥只在创建时返回一次，之后任何接口都拿不到 —— 必须当场提醒保存
    await ElMessageBox.alert(
      `<div style="word-break:break-all;font-family:var(--font-mono);font-size:12px;line-height:1.6">${r.secret}</div>
       <div style="margin-top:8px;font-size:12px;color:#86909c">接收方用它验签：HMAC-SHA256(secret, 原始请求体) 与请求头 X-SuiEye-Signature 比对。</div>`,
      '请保存签名密钥（只显示这一次）',
      { dangerouslyUseHTMLString: true, confirmButtonText: '已保存' },
    )
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '添加订阅失败')
  } finally {
    hookSaving.value = false
  }
}

async function toggleHook(w, v) {
  try {
    await api.put(`/webhooks/${w.id}`, { enabled: v })
    await loadIntegration()
  } catch (e) {
    ElMessage.error('切换状态失败')
    await loadIntegration()
  }
}

async function testHook(w) {
  hookTesting.value = w.id
  try {
    const r = await api.post(`/webhooks/${w.id}/test`)
    if (r.ok) ElMessage.success(`投递成功 · HTTP ${r.status} · ${r.elapsed_ms}ms`)
    else ElMessage.error(`投递失败 · ${r.status || '无响应'}${r.error ? ' · ' + r.error : ''}`)
    await loadIntegration()
  } catch (e) {
    ElMessage.error('测试请求失败')
  } finally {
    hookTesting.value = 0
  }
}

async function removeHook(w) {
  try {
    await ElMessageBox.confirm(
      `删除订阅 ${w.url}？删除后对方将不再收到任何告警推送。`,
      '确认删除',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await api.delete(`/webhooks/${w.id}`)
    await loadIntegration()
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error('删除失败')
  }
}

function copyCurl() {
  const text = integration.value.ingest_curl || ''
  if (!text) return
  navigator.clipboard?.writeText(text).then(
    () => ElMessage.success('已复制，可直接粘到终端执行'),
    () => ElMessage.warning('复制失败，请手动选中'),
  )
}

onMounted(() => {
  loadAll()
  loadIntegration()
})
</script>

<template>
  <PageShell
    title="设置"
    sub="配置均来自真实数据源：场景包 YAML + 模型台账，改动即生效"
  >
    <template #kpi>
      <StatCard
        label="场景包"
        :value="sceneRows.length"
        unit="个"
        :foot="`${activeScenes} 个运行中`"
      />
      <StatCard
        label="模型版本"
        :value="models.length"
        unit="个"
        :foot="`${activeModels} 个在线`"
      />
      <StatCard
        label="数据库"
        :value="sys?.db_size_mb ?? '—'"
        unit="MB"
        :foot="sys ? `${sys.table_count} 张表` : '正在读取'"
      />
      <StatCard
        label="连续运行"
        :value="uptimeShort"
        :foot="sys?.boot_at || '—'"
      />
    </template>

    <el-row :gutter="16">
      <!-- ══════ 左列 ══════ -->
      <el-col :xs="24" :md="12">
        <!-- 系统信息 -->
        <Panel title="系统信息" class="section-panel">
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
        </Panel>

        <!-- 场景与数据 -->
        <Panel
          title="场景与数据"
          :sub="`${sceneRows.length} 个场景包`"
          class="section-panel"
        >
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
        </Panel>

        <!-- 模型版本 -->
        <Panel
          title="模型版本"
          :sub="`${models.length} 个已注册`"
          class="section-panel"
        >
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
        </Panel>
      </el-col>

      <!-- ══════ 右列：告警阈值（真配置） ══════ -->
      <el-col :xs="24" :md="12">
        <Panel title="告警阈值" sub="来源：场景包 YAML · 保存后立即生效">
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
            阈值直接写回场景包 YAML（保留文件内注释）并即时刷新规则缓存 ——
            <strong>下一次分析就用新值</strong>。
            想亲眼验证：把「铅品位 低于」调到实测值以下，再分析一帧，该条告警立刻消失。
          </div>
        </Panel>
      </el-col>
    </el-row>

      <!-- ══════ 通栏：告警对外接入（2026-10-19） ══════
           老板："告警系统要有实时性，也可以接入其他软件。"
           实时性在 ws.py + Alarms.vue 里做完了，这块是"接入"的可见部分：
           入站告诉别人怎么推进来，出站管理我们往哪推。 -->
      <el-row :gutter="16">
        <el-col :span="24">
          <Panel title="告警对外接入" sub="入站收别家的告警 · 出站推给其他软件（数字全部来自真实接口）">
            <!-- ── 入站 ── -->
            <div class="int-block">
              <div class="int-head">
                <span class="int-title">入站 · 外部系统 → SuiEye</span>
                <el-tag size="small" type="success" effect="plain">
                  POST {{ integration.ingest_url || '/api/alarms/ingest' }}
                </el-tag>
                <el-tag v-if="integration.ingest_key_set" size="small" effect="plain">
                  Key {{ integration.ingest_key_masked }}
                </el-tag>
                <el-tag v-else size="small" type="danger" effect="plain">未配置 Key</el-tag>
              </div>
              <pre class="int-pre">{{ integration.ingest_curl || '正在读取…' }}</pre>
              <div class="int-actions">
                <el-button size="small" @click="copyCurl">复制 curl 示例</el-button>
                <span class="int-note">
                  DCS / MES / 视频分析盒子带 <code>X-API-Key</code> 推过来，写库后立刻广播到告警页与门面
                </span>
              </div>
            </div>

            <!-- ── 出站 ── -->
            <div class="int-block">
              <div class="int-head">
                <span class="int-title">出站 · SuiEye → 其他软件</span>
                <el-tag size="small" effect="plain">WebSocket {{ integration.ws_url || '/ws/alarms' }}</el-tag>
                <el-tag size="small" :type="wsOnline ? 'success' : 'info'" effect="plain">
                  在线 {{ wsOnline }} 个连接
                </el-tag>
                <el-tag v-if="integration.ws_token_required" size="small" type="warning" effect="plain">
                  需 token
                </el-tag>
              </div>

              <div v-if="hooks.length" class="hook-list">
                <div v-for="w in hooks" :key="w.id" class="hook-row">
                  <span class="hook-state" :class="hookState(w).cls">{{ hookState(w).text }}</span>
                  <div class="hook-main">
                    <span class="hook-url mono">{{ w.url }}</span>
                    <span class="hook-meta">
                      {{ w.events.join(' / ') }}
                      <template v-if="w.description"> · {{ w.description }}</template>
                      <template v-if="w.ok_count || w.fail_count">
                        · 成功 {{ w.ok_count }} / 失败 {{ w.fail_count }}
                      </template>
                    </span>
                  </div>
                  <el-button link size="small" :loading="hookTesting === w.id" @click="testHook(w)">测试</el-button>
                  <el-switch :model-value="w.enabled" size="small" @change="(v) => toggleHook(w, v)" />
                  <el-button link size="small" type="danger" @click="removeHook(w)">删除</el-button>
                </div>
              </div>
              <div v-else class="hook-empty">
                还没有订阅方 —— 告警目前只能在本系统内看到。
              </div>

              <div class="hook-add">
                <el-input v-model="newHookUrl" size="small" class="hook-url-input"
                  placeholder="https://your-mes.example.com/suieye/alarm" />
                <el-input v-model="newHookDesc" size="small" class="hook-desc-input" placeholder="备注（选填）" />
                <el-button size="small" type="primary" :loading="hookSaving" @click="addHook">
                  添加订阅
                </el-button>
              </div>
              <div class="int-note" style="margin-top: 8px;">
                只支持 <code>http(s)://</code> 回调。钉钉/企业微信请填机器人 Webhook 地址；
                接收方按 <code>X-SuiEye-Signature</code> 验签后可确认事件确实来自 SuiEye。
              </div>
            </div>
          </Panel>
        </el-col>
      </el-row>
  </PageShell>
</template>

<style scoped>
/* 页面骨架/留白由 PageShell 统一提供，本页不再自己写 padding */

.section-panel { margin-bottom: 16px; }
.el-col:last-child .section-panel { margin-bottom: 0; }

.link { color: var(--color-primary); text-decoration: none; }
.mono { font-family: var(--font-mono); }
.dim { font-size: var(--text-xs); color: var(--text-3); margin-top: 2px; word-break: break-all; }
.dim-inline { color: var(--text-3); }
.hint { margin-top: 12px; font-size: var(--text-xs); color: var(--text-3); line-height: var(--leading-relaxed); }

/* 场景与数据 */
.scene-list { display: flex; flex-direction: column; gap: 8px; }
.scene-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 9px 12px; background: var(--bg-2); border-radius: var(--radius);
}
.scene-info { display: flex; align-items: center; gap: 8px; }
.scene-name { font-weight: var(--weight-semibold); color: var(--text-1); font-size: var(--text-base); }
.scene-stats { display: flex; gap: 14px; font-size: var(--text-xs); color: var(--text-2); }
.scene-stats b { color: var(--color-primary); }

/* 模型版本 */
.model-list { display: flex; flex-direction: column; gap: 10px; }
.model-item { padding: 10px 12px; background: var(--bg-2); border-radius: var(--radius); }
.model-head { display: flex; align-items: center; gap: 8px; }
.model-ver { font-weight: var(--weight-semibold); font-size: var(--text-sm); color: var(--text-1); }
.model-scene { font-size: var(--text-xs); color: var(--text-2); }
.model-health { margin-left: auto; font-size: var(--text-xs); }
.model-health.ok { color: var(--color-success); }
.model-health.bad { color: var(--color-danger); }
.model-meta { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 12px; font-size: var(--text-xs); color: var(--text-3); }
.model-meta .hl { color: var(--color-primary); font-weight: var(--weight-semibold); }

/* 告警阈值 */
.rule-group { padding-bottom: 14px; margin-bottom: 14px; border-bottom: 1px solid var(--border-2); }
.rule-group:last-of-type { border-bottom: none; margin-bottom: 4px; padding-bottom: 4px; }
.rule-group-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.rule-scene { font-weight: var(--weight-semibold); color: var(--text-1); font-size: var(--text-base); }
.rule-count { font-size: var(--text-xs); color: var(--text-3); }
.rule-group-head .el-button { margin-left: auto; }

.rule-row { display: flex; align-items: center; gap: 10px; padding: 5px 0; }
.rule-level { width: 3px; height: 26px; border-radius: 2px; flex: none; }
.rule-level.warning { background: var(--color-warning); }
.rule-level.danger { background: var(--color-danger); }
.rule-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.rule-field { font-size: var(--text-sm); color: var(--text-1); }
.rule-name { font-size: 11px; color: var(--text-4); }
.rule-input { width: 118px; flex: none; }

.rule-actions { margin-top: 8px; display: flex; align-items: center; gap: 10px; }
.dirty-hint { font-size: var(--text-xs); color: var(--color-warning); }
/* ---- 告警对外接入（2026-10-19） ---- */
.int-block { padding: 2px 0 16px; }
.int-block + .int-block {
  border-top: 1px solid var(--border-1);
  padding-top: 16px;
  padding-bottom: 0;
}
.int-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.int-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-1);
}
.int-pre {
  margin: 0;
  padding: 10px 12px;
  background: var(--bg-2);
  border: 1px solid var(--border-1);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.7;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-all;
}
.int-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.int-note { font-size: var(--text-xs); color: var(--text-3); line-height: 1.6; }
.int-note code {
  padding: 0 4px;
  border-radius: 3px;
  background: var(--bg-3);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-2);
}

.hook-list { display: flex; flex-direction: column; gap: 8px; }
.hook-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border: 1px solid var(--border-1);
  border-radius: var(--radius);
  background: var(--bg-1);
}
.hook-state {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  font-weight: 500;
}
.hook-state.ok { color: var(--color-success); background: var(--color-success-light); }
.hook-state.bad { color: var(--color-danger); background: var(--color-danger-light); }
.hook-state.idle,
.hook-state.off { color: var(--text-3); background: var(--bg-3); }

.hook-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.hook-url {
  font-size: var(--text-xs);
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hook-meta {
  font-size: 11px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hook-empty {
  padding: 16px;
  text-align: center;
  font-size: var(--text-xs);
  color: var(--text-3);
  border: 1px dashed var(--border-1);
  border-radius: var(--radius);
}
.hook-add { display: flex; gap: 8px; margin-top: 10px; }
.hook-url-input { flex: 1; }
.hook-desc-input { width: 180px; }
</style>
