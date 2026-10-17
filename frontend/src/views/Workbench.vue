<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { UploadFilled, VideoPlay } from '@element-plus/icons-vue'
import FrothLive from '../components/FrothLive.vue'
import api from '../api'

const route = useRoute()
const router = useRouter()
const sceneId = route.params.sceneId || 'flotation'
const sceneInfo = ref(null)

// ---- 场景配置（多场景适配）----
const SCENE_CONFIGS = {
  flotation: {
    name: '浮选品位预测',
    uploadText: '拖拽泡沫图像到此处',
    analyzeHint: '请先选择一张泡沫图像',
    successMsg: (d) => `分析完成：预测铅品位 ${d.pb_grade_xrf}% / 锌品位 ${d.zn_grade_xrf}%`,
    predLabel: '预测铅品位 Pb',
    predUnit: '%',
    predValue: (r) => r.prediction?.pb_grade_xrf ?? '—',
    sideMetrics: [
      { key: 'n_foams', label: '分割泡沫数', value: (r) => r.metrics?.n_foams ?? 0 },
      { key: 'trace_len', label: 'Filter 级数', value: (r) => r.trace?.length ?? 0 },
      { key: 'pipeline_ms', label: '管线耗时', value: (r) => ((r.metrics?.pipeline_ms ?? 0) / 1000).toFixed(1) + 's' },
    ],
    featureTitle: (n) => `泡沫视觉特征 · ${n} 维`,
    recordPred: (row) => row.prediction?.pb_grade_xrf ? `${row.prediction.pb_grade_xrf}%` : '—',
    featureGroups: [
      {
        label: '泡沫形态',
        keys: ['bubble_avg_size', 'bubble_size_std', 'bubble_shape_roundness', 'bubble_shape_dispersion', 'n_valid_bubbles', 'bubble_size_skew', 'bubble_size_kurtosis', 'n_foams'],
        units: { bubble_avg_size: 'px²', bubble_size_std: 'px²', n_valid_bubbles: '个', n_foams: '个' },
      },
      {
        label: '纹理特征',
        keys: ['texture_contrast', 'texture_correlation', 'texture_entropy', 'texture_uniformity', 'texture_energy', 'texture_homogeneity', 'texture_variance'],
        units: {},
      },
      {
        label: '颜色特征',
        keys: ['r_mean', 'g_mean', 'b_mean', 'gray_mean', 'relative_red', 'relative_green', 'relative_blue'],
        units: { r_mean: '/255', g_mean: '/255', b_mean: '/255', gray_mean: '/255' },
      },
    ],
    featureNames: {
      bubble_avg_size: '平均气泡面积', bubble_size_std: '气泡面积标准差',
      bubble_shape_roundness: '气泡圆度', bubble_shape_dispersion: '圆离散度',
      n_valid_bubbles: '有效气泡数', bubble_size_skew: '面积偏度', bubble_size_kurtosis: '面积峰度',
      n_foams: '分割泡沫数',
      texture_contrast: '对比度', texture_correlation: '相关性', texture_entropy: '熵',
      texture_uniformity: '均匀性', texture_energy: '能量', texture_homogeneity: '同质性', texture_variance: '方差',
      r_mean: 'R 均值', g_mean: 'G 均值', b_mean: 'B 均值', gray_mean: '灰度均值',
      relative_red: '红色占比', relative_green: '绿色占比', relative_blue: '蓝色占比',
    },
  },
  grinding: {
    name: '矿石粒度分析',
    uploadText: '拖拽矿石/矿浆图像到此处',
    analyzeHint: '请先选择一张矿石图像',
    successMsg: (d) => `分析完成：P80=${d.p80}μm · 工况=${d.condition || '未知'}`,
    predLabel: '预测 P80 粒度',
    predUnit: 'μm',
    predValue: (r) => r.prediction?.p80 ?? '—',
    sideMetrics: [
      { key: 'n_grains', label: '检测颗粒数', value: (r) => r.metrics?.n_grains ?? 0 },
      { key: 'trace_len', label: 'Filter 级数', value: (r) => r.trace?.length ?? 0 },
      { key: 'pipeline_ms', label: '管线耗时', value: (r) => ((r.metrics?.pipeline_ms ?? 0) / 1000).toFixed(1) + 's' },
    ],
    featureTitle: (n) => `粒度分布特征 · ${n} 维`,
    recordPred: (row) => row.prediction?.p80 ? `${row.prediction.p80}μm` : '—',
    featureGroups: [
      {
        label: '粒度分布',
        keys: ['d10', 'd30', 'd50', 'd60', 'd80', 'd90', 'p80_estimated'],
        units: { d10: 'μm', d30: 'μm', d50: 'μm', d60: 'μm', d80: 'μm', d90: 'μm', p80_estimated: 'μm' },
      },
      {
        label: '形状特征',
        keys: ['grain_count', 'mean_circularity', 'mean_aspect_ratio', 'mean_solidity', 'fill_ratio'],
        units: { grain_count: '个', fill_ratio: '' },
      },
      {
        label: '工况指标',
        keys: ['overgrinding_ratio', 'undergrinding_ratio', 'normal_ratio', 'uniformity_coefficient', 'curvature_coefficient'],
        units: { overgrinding_ratio: '', undergrinding_ratio: '', normal_ratio: '', uniformity_coefficient: '', curvature_coefficient: '' },
      },
    ],
    featureNames: {
      d10: 'D10', d30: 'D30', d50: 'D50(中位径)', d60: 'D60', d80: 'D80', d90: 'D90', p80_estimated: 'P80估计',
      grain_count: '颗粒总数', mean_circularity: '平均圆度', mean_aspect_ratio: '平均长宽比', mean_solidity: '平均实心度', fill_ratio: '填充率',
      overgrinding_ratio: '过磨率', undergrinding_ratio: '欠磨率', normal_ratio: '正常率',
      uniformity_coefficient: '均匀系数 Cu', curvature_coefficient: '曲率系数 Cc',
    },
  },
  safety: {
    name: '安全巡检',
    uploadText: '拖拽皮带/区域监控图像到此处',
    analyzeHint: '请先选择一张安全巡检图像',
    successMsg: (d) => `巡检完成：风险等级=${d.risk_level} · 评分=${d.risk_score}/100`,
    predLabel: '风险等级',
    predUnit: '',
    predValue: (r) => r.prediction?.risk_level ?? '—',
    sideMetrics: [
      { key: 'person_count', label: '检测人员', value: (r) => r.features?.person_count ?? 0 },
      { key: 'belt_offset', label: '皮带偏移', value: (r) => (r.features?.belt_offset_px ?? 0) + 'px' },
      { key: 'foreign_count', label: '异物数量', value: (r) => r.features?.foreign_object_count ?? 0 },
      { key: 'detection_count', label: '检测目标', value: (r) => r.features?.detection_count ?? 0 },
      { key: 'pipeline_ms', label: '管线耗时', value: (r) => ((r.features?.pipeline_ms ?? 0) / 1000).toFixed(1) + 's' },
    ],
    featureTitle: (n) => `安全特征 · ${n} 维`,
    recordPred: (row) => ({ low: '低风险', medium: '中风险', high: '高风险', critical: '严重' }[row.prediction?.risk_level] || row.prediction?.risk_level || '—'),
    recordTagType: (row) => ({ low: 'success', medium: 'warning', high: 'danger', critical: 'danger' }[row.prediction?.risk_level] || 'info'),
    truthLabel: '风险分',
    recordTruth: (row) => (row.prediction?.risk_score ?? '').toString() === '' ? '—' : Number(row.prediction.risk_score).toFixed(1),
    groupLabel: '点位',
    recordGroup: (row) => (row.group_id || '').split('_')[0] || '—',
    recordImage: (row) => row.image_path,
    featureGroups: [
      {
        label: '人员安全',
        keys: ['person_count', 'persons_in_danger_zone', 'persons_safe_zone', 'ppe_compliance_rate', 'avg_person_confidence'],
        units: { person_count: '人', persons_in_danger_zone: '人', persons_safe_zone: '人', ppe_compliance_rate: '', avg_person_confidence: '' },
      },
      {
        label: '皮带状态',
        keys: ['belt_offset_px', 'belt_offset_percent', 'belt_direction', 'belt_severity', 'belt_misalignment_detected'],
        units: { belt_offset_px: 'px', belt_offset_percent: '%' },
      },
      {
        label: '异物检测',
        keys: ['foreign_object_count', 'foreign_total_area', 'foreign_area_ratio', 'avg_foreign_confidence'],
        units: { foreign_object_count: '个', foreign_total_area: 'px²', foreign_area_ratio: '' },
      },
      {
        label: '综合指标',
        keys: ['total_detections', 'max_confidence', 'avg_confidence', 'raw_risk_score'],
        units: { total_detections: '个', raw_risk_score: '分' },
      },
    ],
    featureNames: {
      person_count: '人员总数', persons_in_danger_zone: '危险区人员', persons_safe_zone: '安全区人员',
      ppe_compliance_rate: 'PPE合规率', avg_person_confidence: '平均置信度',
      belt_offset_px: '皮带偏移(px)', belt_offset_percent: '偏移百分比', belt_direction: '偏移方向',
      belt_severity: '严重程度', belt_misalignment_detected: '跑偏检测',
      foreign_object_count: '异物数量', foreign_total_area: '异物总面积', foreign_area_ratio: '异物面积比',
      avg_foreign_confidence: '异物平均置信度',
      total_detections: '总检测数', max_confidence: '最高置信度', avg_confidence: '平均置信度', raw_risk_score: '原始风险分',
    },
  },
}

const cfg = computed(() => SCENE_CONFIGS[sceneId] || SCENE_CONFIGS.flotation)

// ---- 上传状态 ----
const file = ref(null)
const localPreview = ref('')
const dragging = ref(false)

// ---- 分析状态 ----
const loading = ref(false)
const result = ref(null)
const activeImageTab = ref('boundary_overlay')
const recentRecords = ref([])

// ---- 实时画面（浮选场景）：抓当前帧直接进分析链路 ----
const frothRef = ref(null)
const frameBusy = ref(false)

/**
 * 「分析当前画面」—— 把实时画面的**当前帧**喂给后端，走与上传图片完全相同的链路。
 *
 * 之前实时画面只是个"播放器"：右栏永远提示"上传图像并点击开始分析"，
 * 画面在动、分析区纹丝不动 —— 看着就像"不能分析视频"。
 * 这里补上最后一环：canvas 抓帧 → File → `POST /analyze/{scene}`。
 *
 * ★ 抓到的帧会**回填到上传区**（预览 + file）：
 *   ① 用户能确认"被分析的就是这一帧"；② 可以再用「开始分析」重跑；
 *   ③ 与上传流程共用同一份状态，不必为实时画面单开一条链路。
 */
async function analyzeFrame() {
  if (frameBusy.value || loading.value) return
  frameBusy.value = true
  try {
    const frame = await frothRef.value?.captureFrame()
    if (!frame) {
      ElMessage.warning('画面还没准备好，请稍等一两秒再试')
      return
    }
    file.value = frame
    localPreview.value = URL.createObjectURL(frame)
    await analyze()
  } finally {
    frameBusy.value = false
  }
}

const imageTabs = computed(() => {
  if (!result.value?.image) return []
  if (sceneId === 'safety') {
    return [
      { key: 'detection_overlay', label: '检测叠加' },
      { key: 'danger_zone_map', label: '危险区域' },
      { key: 'risk_assessment', label: '风险报告' },
    ].filter((t) => result.value.image[t.key])
  }
  if (sceneId === 'grinding') {
    return [
      { key: 'boundary_overlay', label: '颗粒叠加' },
      { key: 'cropped', label: 'ROI 裁剪' },
      { key: 'probability_map', label: '距离热图' },
      { key: 'colored_labels', label: '彩色标签' },
    ].filter((t) => result.value.image[t.key])
  }
  // flotation default
  return [
    { key: 'boundary_overlay', label: '分割叠加' },
    { key: 'cropped', label: 'ROI 裁剪' },
    { key: 'probability_map', label: '概率热图' },
    { key: 'colored_labels', label: '彩色标签' },
  ].filter((t) => result.value.image[t.key])
})

const groupedFeatures = computed(() => {
  if (!result.value?.features) return []
  const feats = result.value.features
  const groups = cfg.value.featureGroups
  const names = cfg.value.featureNames
  return groups.map((g) => ({
    label: g.label,
    items: g.keys
      .filter((k) => feats[k] !== undefined)
      .map((k) => ({ key: k, name: names[k] || k, value: fmtNum(feats[k]), unit: g.units[k] || '' })),
  }))
})

// 记录表图片 URL（相对路径 → 后端图片服务）
const recImgUrl = (path) => (path ? '/api/image/' + encodeURIComponent(path) : '')

function fmtNum(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (Math.abs(n) >= 10000) return n.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
  if (Math.abs(n) < 0.01 && n !== 0) return n.toExponential(2)
  return String(Math.round(n * 1000) / 1000)
}

// ---- 文件选择（点击 / 拖拽）----
function onFileChange(f) {
  if (!f.raw || !f.raw.type.startsWith('image/')) {
    ElMessage.warning('请上传图像文件（jpg/png/bmp）')
    return
  }
  file.value = f.raw
  localPreview.value = URL.createObjectURL(f.raw)
  result.value = null
}

function onDrop(e) {
  e.preventDefault()
  dragging.value = false
  const f = e.dataTransfer.files?.[0]
  if (f && f.type.startsWith('image/')) {
    file.value = f
    localPreview.value = URL.createObjectURL(f.raw)
    result.value = null
  } else {
    ElMessage.warning('请拖入图像文件')
  }
}

// ---- 分析 ----
async function analyze() {
  if (!file.value) {
    ElMessage.warning(cfg.value.analyzeHint)
    return
  }
  loading.value = true
  result.value = null
  try {
    const fd = new FormData()
    fd.append('file', file.value)
    const data = await api.post(`/analyze/${sceneId}`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    result.value = data
    activeImageTab.value = 'boundary_overlay'
    ElMessage.success(cfg.value.successMsg(data.prediction))
    loadRecent()
  } catch (err) {
    const msg = err?.response?.data?.detail || '分析失败，请检查后端服务'
    ElMessage.error(String(msg))
  } finally {
    loading.value = false
  }
}

// ---- 跳转配药助手（仅浮选场景）----
function goToDosing() {
  if (!result.value?.prediction) return
  const p = result.value.prediction
  const params = new URLSearchParams()
  if (p.pb_grade_xrf != null) params.set('target_pb_grade', String(p.pb_grade_xrf))
  if (p.zn_grade_xrf != null) params.set('target_zn_grade', String(p.zn_grade_xrf))
  // 根据品位自动推断泡沫状态
  let froth = 'normal'
  if (p.pb_grade_xrf < 4.5 || p.zn_grade_xrf < 2.0) froth = 'fragile'
  else if (p.pb_grade_xrf > 7.0 || p.zn_grade_xrf > 4.5) froth = 'mineralized'
  params.set('froth_state', froth)
  router.push(`/dosing?${params.toString()}`)
}

async function loadRecent() {
  try {
    const data = await api.get(`/scenes/${sceneId}/records`, { params: { limit: 8 } })
    recentRecords.value = data.records || []
  } catch {
    recentRecords.value = []
  }
}

onMounted(async () => {
  try {
    const data = await api.get('/scenes')
    sceneInfo.value = (data.scenes || []).find((s) => s.name === sceneId)
  } catch {
    sceneInfo.value = null
  }
  loadRecent()
})
</script>

<template>
  <div class="page">
    <el-page-header @back="$router.push('/')">
      <template #content>
        <span class="page-title">{{ sceneInfo?.display_name || cfg.name }} · 分析工作台</span>
      </template>
      <template #extra>
        <el-tag v-if="sceneInfo?.active_model" type="success" effect="dark" size="small">
          模型 {{ sceneInfo.active_model }}
        </el-tag>
        <el-tag v-else type="info" size="small">无在线模型</el-tag>
        <!-- 工况标签（粒度场景） -->
        <el-tag
          v-if="result?.prediction?.condition && sceneId === 'grinding'"
          :type="result.prediction.condition === '正常' ? 'success' : result.prediction.condition === '过磨' ? 'warning' : 'danger'"
          size="small"
          style="margin-left: 6px"
        >
          {{ result.prediction.condition }}
        </el-tag>
      </template>
    </el-page-header>

    <div class="workbench">
      <!-- 左列：实时画面 + 上传 -->
      <div class="upload-col">
        <!-- 浮选场景专属：泡沫层实时画面（现场实拍素材，非合成） -->
        <template v-if="sceneId === 'flotation'">
          <div class="col-title">浮选泡沫 · 实时画面</div>
          <FrothLive
            ref="frothRef"
            label="1#浮选槽 · 泡沫层"
            src="/media/froth-loop.mp4"
            poster="/media/froth-poster.jpg"
            ratio="4 / 3"
            class="froth-block"
          />
          <!-- 抓当前帧直接分析：实时画面不再只是"播放器" -->
          <el-button
            class="frame-btn"
            type="primary"
            :loading="frameBusy || loading"
            @click="analyzeFrame"
          >
            <el-icon><VideoPlay /></el-icon>
            <span>分析当前画面</span>
          </el-button>
          <div class="col-sep">或上传单帧图像走全链路分析</div>
        </template>

        <div
          class="drop-zone"
          :class="{ dragging, compact: sceneId === 'flotation' }"
          @dragover.prevent="dragging = true"
          @dragleave="dragging = false"
          @drop="onDrop"
        >
          <img v-if="localPreview" :src="localPreview" class="preview" alt="待分析图像" />
          <template v-else>
            <el-icon :size="42" color="#86909c"><UploadFilled /></el-icon>
            <div class="drop-text">{{ cfg.uploadText }}</div>
            <div class="drop-sub">或点击下方按钮选择文件</div>
          </template>
        </div>
        <div class="upload-actions">
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept="image/*"
            :on-change="onFileChange"
          >
            <el-button>选择图像</el-button>
          </el-upload>
          <el-button
            type="primary"
            :loading="loading"
            :disabled="!file"
            @click="analyze"
          >
            {{ loading ? '分析中…' : '开始分析' }}
          </el-button>
        </div>
        <div v-if="file" class="file-meta">{{ file.name }} · {{ (file.size / 1024).toFixed(0) }} KB</div>

        <!-- 管线轨迹 -->
        <div v-if="result" class="trace-card">
          <div class="card-title">管线执行轨迹 <span class="total">{{ result.metrics.pipeline_ms }} ms</span></div>
          <div v-for="t in result.trace" :key="t.filter" class="trace-row">
            <span class="trace-name">{{ t.filter }}</span>
            <div class="trace-bar-wrap">
              <div class="trace-bar" :style="{ width: Math.max(2, (t.ms / result.metrics.pipeline_ms) * 100) + '%' }" />
            </div>
            <span class="trace-ms">{{ t.ms }} ms</span>
          </div>
        </div>
      </div>

      <!-- 右列：结果 -->
      <div class="result-col">
        <el-empty
          v-if="!result && !loading"
          :description="sceneId === 'flotation'
            ? '点左侧「分析当前画面」抓取实时帧，或上传图像，查看全链路结果'
            : '上传图像并点击「开始分析」，查看全链路结果'"
        />

        <div v-else-if="loading" class="loading-box" v-loading="true" element-loading-text="Filter 管线运行中…">
          <el-skeleton :rows="8" animated />
        </div>

        <template v-else>
          <!-- 告警 -->
          <el-alert
            v-for="a in result.alarms"
            :key="a.rule_name"
            :title="a.message"
            :type="a.level === 'danger' ? 'error' : 'warning'"
            show-icon
            :closable="false"
            style="margin-bottom: 12px"
          />

          <!-- 预测卡（场景自适应）-->
          <div class="pred-card">
            <div class="pred-main">
              <div class="pred-label">{{ cfg.predLabel }}</div>
              <div class="pred-value">{{ cfg.predValue(result) }}<span class="unit">{{ cfg.predUnit }}</span></div>
              <div class="pred-model">
                模型 {{ result.prediction.model }} · 记录 #{{ result.record_id }}
                <span v-if="result.prediction.condition" class="condition-badge" :class="'cond-' + result.prediction.condition">
                  {{ result.prediction.condition }}
                </span>
              </div>
              <div v-if="result.prediction.confidence !== undefined" class="pred-confidence">
                置信度：{{ (result.prediction.confidence * 100).toFixed(1) }}%
              </div>
              <!-- 配药联动（仅浮选场景） -->
              <div v-if="sceneId === 'flotation' && result.prediction?.pb_grade_xrf" class="dosing-link">
                <el-button type="primary" size="small" @click="goToDosing">
                  <el-icon style="margin-right: 4px"><svg viewBox="0 0 1024 1024" width="14" height="14"><path d="M512 64C264.6 64 64 264.6 64 512s200.6 448 448 448 448-200.6 448-448S759.4 64 512 64zm0 820c-205.4 0-372-166.6-372-372s166.6-372 372-372 372 166.6 372 372-166.6 372-372 372zm-32-588h64v256h-64zm0 320h64v64h-64z" fill="currentColor"/></svg></el-icon>
                  生成配药方案
                </el-button>
                <span class="dosing-hint">基于当前品位自动推荐药剂</span>
              </div>
            </div>
            <div class="pred-side">
              <div v-for="m in cfg.sideMetrics" :key="m.key" class="side-item">
                <div class="side-num">{{ m.value(result) }}</div>
                <div class="side-label">{{ m.label }}</div>
              </div>
            </div>
          </div>

          <!-- 图像结果 -->
          <el-tabs v-model="activeImageTab" class="image-tabs">
            <el-tab-pane v-for="t in imageTabs" :key="t.key" :label="t.label" :name="t.key" />
          </el-tabs>
          <div class="image-box">
            <el-image
              v-if="result.image[activeImageTab]"
              :src="result.image[activeImageTab]"
              fit="contain"
              :preview-src-list="imageTabs.map((t) => result.image[t.key])"
              :initial-index="imageTabs.findIndex((t) => t.key === activeImageTab)"
              hide-on-click-modal
            />
          </div>

          <!-- 特征表（场景自适应）-->
          <div class="features-card">
            <div class="card-title">{{ cfg.featureTitle(Object.keys(result.features || {}).length) }}</div>
            <div class="feature-groups">
              <div v-for="g in groupedFeatures" :key="g.label" class="feature-group">
                <div class="group-label">{{ g.label }}</div>
                <div class="feature-grid">
                  <div v-for="it in g.items" :key="it.key" class="feature-item">
                    <span class="f-name">{{ it.name }}</span>
                    <span class="f-value">{{ it.value }}<small v-if="it.unit"> {{ it.unit }}</small></span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- 最近记录 -->
    <div class="recent-card">
      <div class="card-title">最近分析记录</div>
      <el-table :data="recentRecords" size="small" stripe>
        <el-table-column prop="id" label="ID" width="64" />
        <el-table-column v-if="cfg.recordImage" label="图像" width="84">
          <template #default="{ row }">
            <el-image
              v-if="row.image_path"
              :src="recImgUrl(row.image_path)"
              :preview-src-list="[recImgUrl(row.image_path)]"
              preview-teleported
              fit="cover"
              class="rec-thumb"
            />
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column :label="cfg.groupLabel || '时间组'" :width="cfg.groupLabel ? 80 : 130">
          <template #default="{ row }">{{ cfg.recordGroup ? cfg.recordGroup(row) : (row.group_id || '—') }}</template>
        </el-table-column>
        <el-table-column label="预测结果" width="104">
          <template #default="{ row }">
            <el-tag v-if="cfg.recordTagType" :type="cfg.recordTagType(row)" size="small">{{ cfg.recordPred(row) }}</el-tag>
            <span v-else class="grade">{{ cfg.recordPred(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="cfg.truthLabel || '真值'" width="80">
          <template #default="{ row }">{{ cfg.recordTruth ? cfg.recordTruth(row) : (row.actual_value ?? '—') }}</template>
        </el-table-column>
        <el-table-column prop="model_version" label="模型" min-width="130" show-overflow-tooltip />
        <el-table-column prop="source" label="来源" width="80">
          <template #default="{ row }">
            <el-tag :type="row.source === 'upload' ? 'success' : 'info'" size="small" effect="plain">
              {{ row.source }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" min-width="150">
          <template #default="{ row }">{{ (row.created_at || '').replace('T', ' ').slice(0, 19) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.page {
  max-width: 1200px;
  margin: 0 auto;
  padding-bottom: 40px;
}
.page-title {
  font-size: 16px;
  font-weight: 500;
}
.workbench {
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: 16px;
  margin-top: 16px;
}

/* 上传列 */
/* ── 左列：实时泡沫画面 ── */
.col-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #1d2129;
}
.col-title::before {
  content: '';
  width: 3px;
  height: 12px;
  border-radius: 2px;
  background: #165dff;
}
.froth-block { margin-bottom: 10px; }
/* 实时画面下的主按钮：抓当前帧 → 走全链路分析（这一颗把"播放器"接进了分析链路） */
.frame-btn {
  width: 100%;
  margin-bottom: 12px;
}
.col-sep {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 2px 0 10px;
  font-size: 11px;
  color: #86909c;
}
.col-sep::before,
.col-sep::after {
  content: '';
  flex: 1;
  height: 1px;
  background: #e5e6eb;
}
/* 浮选场景左列多了实时画面 → 上传区收矮一点，整页不至于太长 */
.drop-zone.compact { height: 216px; }

.drop-zone {
  border: 1.5px dashed #c9cdd4;
  border-radius: 6px;
  height: 300px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: #ffffff;
  transition: all 0.2s;
  overflow: hidden;
}
.drop-zone.dragging {
  border-color: #165dff;
  background: #ecf5ff;
}
.preview {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
.drop-text {
  margin-top: 10px;
  color: #4e5969;
  font-size: 14px;
}
.drop-sub {
  margin-top: 4px;
  color: #86909c;
  font-size: 12px;
}
.upload-actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}
.file-meta {
  margin-top: 8px;
  font-size: 12px;
  color: #86909c;
  word-break: break-all;
}

/* 轨迹卡 */
.trace-card {
  margin-top: 16px;
  background: #fff;
  border-radius: 6px;
  padding: 14px;
  border: 1px solid #f2f3f5;
}
.card-title {
  font-size: 13px;
  font-weight: 600;
  color: #1d2129;
  margin-bottom: 10px;
  display: flex;
  justify-content: space-between;
}
.card-title .total {
  color: #165dff;
  font-weight: 500;
}
.trace-row {
  display: grid;
  grid-template-columns: 110px 1fr 70px;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
}
.trace-name {
  color: #4e5969;
  font-family: Consolas, monospace;
}
.trace-bar-wrap {
  background: #f0f2f5;
  border-radius: 4px;
  height: 8px;
  overflow: hidden;
}
.trace-bar {
  height: 100%;
  background: linear-gradient(90deg, #165dff, #67c23a);
  border-radius: 4px;
}
.trace-ms {
  color: #86909c;
  text-align: right;
  font-family: Consolas, monospace;
}

/* 结果列 */
.result-col {
  min-height: 400px;
}
.loading-box {
  background: #fff;
  border-radius: 6px;
  padding: 24px;
  min-height: 400px;
  border: 1px solid #f2f3f5;
}

/* 预测卡 */
.pred-card {
  display: flex;
  background: #fff;
  border: 1px solid #f2f3f5;
  border-radius: 6px;
  padding: 18px;
  margin-bottom: 14px;
  box-shadow: var(--shadow-sm);
}
.pred-main {
  flex: 1;
}
.pred-label {
  font-size: 13px;
  color: #86909c;
}
.pred-value {
  font-size: 44px;
  font-weight: 700;
  color: #1d2129;
  line-height: 1.2;
  font-family: 'DIN Alternate', Consolas, monospace;
}
.pred-value .unit {
  font-size: 18px;
  color: #86909c;
  margin-left: 4px;
}
.pred-model {
  margin-top: 4px;
  font-size: 12px;
  color: #86909c;
}
.pred-confidence {
  margin-top: 2px;
  font-size: 12px;
  color: #67c23a;
}
.condition-badge {
  display: inline-block;
  margin-left: 8px;
  padding: 1px 8px;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 600;
}
.condition-badge.cond-正常 { background: #e8fdf0; color: #67c23a; }
.condition-badge.cond-过磨 { background: #fdf6ec; color: #e6a23c; }
.condition-badge.cond-欠磨 { background: #fef0f0; color: #f56c6c; }

/* 配药联动 */
.dosing-link {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed #e5e6eb;
  display: flex;
  align-items: center;
  gap: 10px;
}
.dosing-hint {
  font-size: 12px;
  color: #86909c;
}

.pred-side {
  display: flex;
  gap: 18px;
  align-items: center;
}
.side-item {
  text-align: center;
}
.side-num {
  font-size: 20px;
  font-weight: 600;
  color: #165dff;
}
.side-label {
  font-size: 11px;
  color: #86909c;
  margin-top: 2px;
}

/* 图像 */
.image-tabs {
  background: #fff;
  border: 1px solid #f2f3f5;
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  padding: 0 12px;
}
.image-box {
  background: #fff;
  border: 1px solid #f2f3f5;
  border-radius: 0 0 8px 8px;
  height: 380px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 14px;
  overflow: hidden;
}
.image-box .el-image {
  width: 100%;
  height: 100%;
}

/* 特征 */
.features-card {
  background: #fff;
  border: 1px solid #f2f3f5;
  border-radius: 6px;
  padding: 14px;
}
.feature-groups {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}
.group-label {
  font-size: 12px;
  color: #165dff;
  font-weight: 600;
  margin-bottom: 6px;
}
.feature-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 12px;
}
.feature-item {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 3px 6px;
  border-radius: 4px;
  background: #ffffff;
}
.f-name {
  color: #4e5969;
}
.f-value {
  color: #1d2129;
  font-family: Consolas, monospace;
  font-weight: 600;
}
.f-value small {
  color: #c9cdd4;
  font-weight: 400;
}

/* 最近记录 */
.recent-card {
  margin-top: 16px;
  background: #fff;
  border: 1px solid #f2f3f5;
  border-radius: 6px;
  padding: 14px;
}
.grade {
  color: #e6a23c;
  font-weight: 600;
}
.rec-thumb {
  width: 64px;
  height: 44px;
  border-radius: 4px;
  display: block;
  cursor: zoom-in;
  background: #f7f8fa;
}
.dim { color: #c9cdd4; }
</style>
