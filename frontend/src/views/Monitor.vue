<script setup>
/**
 * 实时监控 —— 凡口铅锌矿浮选现场 · 单点在线监测（2026-10-16 单点重构）
 *
 * 布局：左  = 粗选给矿现场画面（大幅，画面内 HUD 即实时品位）
 *      右上 = 给矿品位趋势（Feed II · Pb，随节拍跳档推进 + 文字研判）
 *      右下 = 工艺链实时快照（9 个测点当前值 + 时段走势）
 *
 * ★★ 时间语义（三级）：
 *   ① 画面时刻 `curSec` = 录像播放进度（逐帧，1:1）→ **仅** HUD 时钟用；
 *   ② 数据时刻 `dataSec` = 数据时钟按 DATA_SPEED 倍**快进**，在录像数据窗口内循环
 *      （化验 17min 一测、视频 1:1 播放则 17min 才动一格 → 曲线长期静止看不出在动，
 *       故数据时钟快进 30x，让曲线持续左滚、肉眼可见）；
 *   ③ 节拍时刻 `beatSec` = 固定节拍器每 BEAT_MS 把 `beatSec` 对齐到 `dataSec`。
 *   右侧「所有」分析（趋势游标 / 工艺链 9 点 / KPI / 研判文字）
 *   均由 `beatSec` 派生 → 视频一动，右侧**按节拍一档一档**同步推进。
 *
 * ⚠️ 素材口径：画面取自凡口 2019-08-22 现场录像池（23 段循环播放），化验为该日
 *    Courier 在线分析仪真值。页面如实标注来源，不冒充他厂。
 */
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts'
import { VideoPlay, VideoPause } from '@element-plus/icons-vue'
import api from '../api'

const DATA_DATE = '2019-08-22'
const C_LINE = '#165dff'
function pad(n) { return String(n).padStart(2, '0') }

/* ---------------- 现场画面（唯一点位） ---------------- */
// 录像池：23 段真实文件名（源间隔非严格 5min，必须硬编码）
// 段起始秒（当日秒）= 小时*3600+分*60 —— 用于把「视频进度」映射为「当日时刻」
const SEG_FILES = [
  '105629', '110130', '110630', '111130', '111630', '112130', '112630',
  '113130', '113630', '114130', '114630', '115130', '115630',
  '120130', '120630', '121130', '121630', '122130', '122630',
  '123130', '123630', '124130', '124630',
]
const segStartSec = (hms) =>
  Number(hms.slice(0, 2)) * 3600 + Number(hms.slice(2, 4)) * 60 + Number(hms.slice(4, 6))

const allSegs = SEG_FILES.map((hms) => ({
  file: `/media/froth2019/${hms}.mp4`,
  label: `${hms.slice(0, 2)}:${hms.slice(2, 4)}`,
  startSec: segStartSec(hms),
}))

/** 监控点位：粗选给矿（Feed II · Pb） */
const CAM = { name: '粗选给矿', point: 'Feed II', element: 'Pb', label: '给矿品位 Pb', unit: '%' }

const videoEl = ref(null)
const playing = ref(true)
const failed = ref(false)
const segIndex = ref(0)
/** ★ 视频当前播放进度（秒）——由 timeupdate 驱动，是「当前时刻」的唯一来源 */
const playSec = ref(0)
const videoSrc = computed(() => allSegs[segIndex.value % allSegs.length].file)
const segLabel = computed(() => allSegs[segIndex.value % allSegs.length].label)

/** ★★ 当前时刻（当日秒）= 本段起始秒 + 视频内进度 → 视频与所有分析共用此值 */
const curSec = computed(() => allSegs[segIndex.value % allSegs.length].startSec + playSec.value)

/* ================= 数据时钟「快进」=================
 * ★★ 老板要求：右侧曲线"看得出在动" —— 但化验 17min 一测、视频 1:1 播放，
 *    走到下一个化验点要 17 分钟画面 → 曲线长期静止，看不出在动。
 *    解法：**数据时钟按 DATA_SPEED 倍快进**（画面走 1 秒 = 数据走 DATA_SPEED 秒），
 *    并在录像数据窗口内**循环**，让曲线持续向左滚动、肉眼可见。
 *    画面时刻（HUD 时钟）仍走真实 1:1 → 画面时间轴不受影响。
 */
const DATA_SPEED = 30                 // 数据快进倍速（1s 画面 = 30s 数据）
const DATA_T0 = 10 * 3600 + 49 * 60   // 数据窗口起点 = 首个化验点 10:49
const DATA_T1 = 12 * 3600 + 51 * 60   // 数据窗口终点 = 录像末 + 余量 12:51
const DATA_SPAN = DATA_T1 - DATA_T0

/** ★★ 数据时刻（当日秒）—— 右侧「所有」数据的唯一来源（节拍在它之上再对齐） */
const dataSec = computed(() => {
  const base = allSegs[0].startSec // 录像首段起点（数据窗口锚点）
  const adv = (curSec.value - base) * DATA_SPEED
  return DATA_T0 + ((adv % DATA_SPAN) + DATA_SPAN) % DATA_SPAN
})

function playVideo() {
  const v = videoEl.value
  if (!v) return
  const p = v.play()
  if (p && p.catch) p.catch(() => {})
}

function onTimeUpdate() {
  const v = videoEl.value
  if (v) playSec.value = v.currentTime
}

function onVideoEnded() {
  segIndex.value = (segIndex.value + 1) % allSegs.length
  playSec.value = 0
  nextTick(() => {
    playVideo()
    // ★ 换段 = 画面对应新时刻 → 立刻把节拍对齐到新段起点（不等下一个节拍周期）
    beatSec.value = Math.floor(dataSec.value)
  })
}

function togglePlay() {
  playing.value = !playing.value
  const v = videoEl.value
  if (!v) return
  if (playing.value) playVideo()
  else v.pause()
}

/* ---------------- 时刻 ---------------- */

/** ★ 画面时刻（当日秒）——视频逐帧推进，仅用于「视频」自身（HUD 时钟走字） */
const daySec = computed(() => Math.floor(curSec.value))
/** ★★ 节拍时刻（当日秒）——右侧「趋势/快照/KPI/研判」等**全部数据的唯一来源**
 *  由固定节拍器每 BEAT_MS 跳一次 → 右侧"一档一档推进"，与逐帧的画面区分开 */
const tickSec = computed(() => beatSec.value)
const camClock = computed(() => {
  const t = daySec.value
  return `${pad(Math.floor(t / 3600))}:${pad(Math.floor((t % 3600) / 60))}:${pad(t % 60)}`
})
/** 右侧数据时刻字符串（HH:MM:SS）——标注数据取自哪一格 */
const tickClock = computed(() => {
  const t = tickSec.value
  return `${pad(Math.floor(t / 3600))}:${pad(Math.floor((t % 3600) / 60))}:${pad(t % 60)}`
})
const camAt = computed(() => tickClock.value)

/* ---------------- 化验数据 ---------------- */
const dayData = ref(null)
const bands = ref({}) // ★ 各测点历史分位区间（参照标准）→ { "Feed II|Pb": {p10,p50,p90,...} }
const chartEl = ref(null)
let chart = null

function seriesOf(point, element) {
  return (dayData.value?.series || []).find((x) => x.point === point && x.element === element) || null
}
function secsOf(s) {
  return s.values.map((p) => { const [h, m, sec] = p.ts.split(':').map(Number); return h * 3600 + m * 60 + sec })
}
/** 某时刻的插值读数（「今日已过秒数」口径） */
function valueAtT(point, element, t) {
  const s = seriesOf(point, element)
  if (!s || !s.values.length) return null
  const secs = secsOf(s)
  if (t <= secs[0]) return { v: s.values[0].v, at: s.values[0].ts }
  const last = s.values.length - 1
  if (t >= secs[last]) return { v: s.values[last].v, at: s.values[last].ts }
  const hi = secs.findIndex((x) => x >= t)
  const lo = hi - 1
  const r = (t - secs[lo]) / (secs[hi] - secs[lo] || 1)
  return { v: s.values[lo].v + (s.values[hi].v - s.values[lo].v) * r, at: s.values[hi].ts }
}
const valueAt = (point, element) => valueAtT(point, element, tickSec.value)

/* ---------------- 顶部 KPI ----------------
 * ★ 去重原则：给矿品位在「视频 HUD」、精矿/尾矿品位在「工艺链」，
 *   顶栏只放**全流程级**、别处没有的指标：回收率 + 富集比。
 */
const kpis = computed(() => [
  { label: '铅回收率', val: recov('Pb'), unit: '%' },
  { label: '锌回收率', val: recov('Zn'), unit: '%' },
  { label: '铅富集比', val: enrich('Pb'), unit: '×' },
  { label: '锌富集比', val: enrich('Zn'), unit: '×' },
])

/** 富集比 = 精矿品位 / 给矿品位（选别效果的直接度量） */
function enrich(el) {
  const f = valueAt('Feed II', el)?.v
  const k = el === 'Pb' ? valueAt('Kpb II', 'Pb')?.v : valueAt('Kzn II', 'Zn')?.v
  if (f == null || k == null || f <= 0) return '—'
  return (k / f).toFixed(1)
}

/** 回收率（简式）：(给矿 − 扫选尾矿) / 给矿 —— 与页面既有口径保持一致 */
function recov(el) {
  const f = valueAt('Feed II', el)?.v
  const x = el === 'Pb' ? valueAt('Xpb II', 'Pb')?.v : valueAt('Xzn II', 'Zn')?.v
  if (f == null || x == null || f <= 0) return '—'
  const r = ((f - x) / f) * 100
  return (r > 0 && r <= 100 ? r.toFixed(1) : '—')
}

/* ---------------- 工艺链快照 ---------------- */
const CHAIN = [
  { group: '给矿', rows: [{ key: 'Feed II|Pb', name: '给矿（原矿）' }] },
  {
    group: '铅回路',
    rows: [
      { key: 'Kpb II|Pb', name: '铅精矿' },
      { key: 'Pb KCX II|Pb', name: '铅粗选尾矿' },
      { key: 'Xpb II|Pb', name: '铅扫选尾矿' },
      { key: 'Pb CP II|Pb', name: '硫精矿*' },
    ],
  },
  {
    group: '锌回路',
    rows: [
      { key: 'Kzn II|Zn', name: '锌精矿' },
      { key: 'Zn KCX II|Zn', name: '锌粗选尾矿' },
      { key: 'Xzn II|Zn', name: '最终尾矿' },
      { key: 'H pb-zn II|Zn', name: '铅锌混合流*' },
    ],
  },
]

/** 工艺链小线：返回 { line, area, dot, band } ——
 *  ★ 与主曲线同口径 = **滚动窗口**（近 WIN_MIN 分钟）
 *  ★★ 坐标系 = **该测点历史 P10~P90 正常区间**（不是各线自己归一）→
 *     ① 9 条小线**横向可比**（同一个"相对标准"口径）② 曲线出背景带 = 一目了然的异常
 *  ★ 普通折线（点对点直连）+ 淡蓝面积 + 末端呼吸落点 → 实时仪表感（老板 2026-10-16） */
function sparkPoints(key) {
  if (!dayData.value) return null
  const s = dayData.value.series.find((x) => `${x.point}|${x.element}` === key)
  if (!s || s.values.length < 2) return null
  const cur = tickSec.value
  const X0 = cur - WIN_MIN * 60
  const X1 = cur
  const XSPAN = X1 - X0
  // 窗口内实测点（含窗口左缘前"借"一个锚点，线从左边缘连续进入）
  const raw = s.values.map((p) => {
    const [h, m] = p.ts.split(':').map(Number)
    return { sec: h * 3600 + m * 60, v: p.v }
  }).sort((a, b) => a.sec - b.sec)
  const anchor = raw.filter((r) => r.sec < X0).slice(-1)
  const win = [...anchor, ...raw.filter((r) => r.sec >= X0 && r.sec <= X1)]
  if (win.length < 2) return null

  // ★★ y 轴坐标系：历史 P10→y=19（底）、P90→y=5（顶）；上下各留 15% 余量画越限
  const b = bands.value[key]
  let lo, hi
  if (b) {
    const core = Math.max(b.p90 - b.p10, 1e-6)
    lo = b.p10 - core * 0.35
    hi = b.p90 + core * 0.35
  } else {
    // 无历史区间（理论上不会）→ 退回窗口自适应
    const vs = win.map((w) => w.v)
    const mn = Math.min(...vs), mx = Math.max(...vs)
    const pad = Math.max((mx - mn) * 0.5, Math.abs(mn) * 0.03, 1e-6)
    lo = mn - pad; hi = mx + pad
  }
  const yOf = (v) => {
    const y = 19 - ((v - lo) / (hi - lo)) * 14
    return Math.max(0.5, Math.min(23.5, y))
  }
  const xOf = (sec) => Math.max(0, Math.min(100, ((sec - X0) / XSPAN) * 100))
  // 落点 = 当前节拍时刻（= 最右端），值为窗口内插值
  let dotV = win[win.length - 1].v
  const before = win.filter((w) => w.sec <= cur)
  const after = win.filter((w) => w.sec > cur)
  if (before.length && after.length) {
    const a = before[before.length - 1], bb = after[0]
    const t = (cur - a.sec) / ((bb.sec - a.sec) || 1)
    dotV = a.v + (bb.v - a.v) * t
  } else if (before.length) { dotV = before[before.length - 1].v }
  const pts = win.map((w) => `${xOf(w.sec).toFixed(1)},${yOf(w.v).toFixed(1)}`)
  const lastY = yOf(dotV)
  pts.push(`100.0,${lastY.toFixed(1)}`) // 末端补「此刻」点 → 接到最右落点
  const dot = [xOf(cur), lastY]
  const area = `0,21 ${pts.join(' ')} 100,21`
  // ★ 正常区间背景带（P10~P90）
  let band = null
  if (b) {
    const yTop = yOf(b.p90)
    const yBot = yOf(b.p10)
    band = { y: yTop, h: Math.max(yBot - yTop, 2) }
  }
  return { line: pts.join(' '), area, dot, band }
}

/** ★★ 偏离度判定（参照标准 = 该测点**全月真实化验**的分位区间，非编造目标值）
 *  返回 { tag, level, tip }；level: ok | high | low | over | under
 *  - 落在 P10~P90  → ok「正常」
 *  - P90~P99 / P01~P10 → high/low「偏高/偏低」
 *  - 越 P99 / P01 → over/under「超上限/超下限」
 */
function deviationOf(key, v) {
  const b = bands.value[key]
  if (b == null || v == null) return null
  if (v > b.p99) return { tag: '超上限', level: 'over', tip: `历史 P99 = ${b.p99}%` }
  if (v < b.p01) return { tag: '超下限', level: 'under', tip: `历史 P01 = ${b.p01}%` }
  if (v > b.p90) return { tag: '偏高', level: 'high', tip: `正常区间 ${b.p10}~${b.p90}%` }
  if (v < b.p10) return { tag: '偏低', level: 'low', tip: `正常区间 ${b.p10}~${b.p90}%` }
  return { tag: '正常', level: 'ok', tip: `正常区间 ${b.p10}~${b.p90}%（P50=${b.p50}%）` }
}

const chainRows = computed(() => CHAIN.map((g, gi) => ({
  ...g,
  rows: g.rows.map((r, i) => {
    const [p, e] = r.key.split('|')
    const v = valueAt(p, e)
    // gid：SVG 渐变 id 必须无空格/管道符 → 用索引生成唯一 id
    return {
      ...r,
      gid: `${gi}-${i}`,
      cur: v ? v.v : null,
      spark: sparkPoints(r.key),
      dev: deviationOf(r.key, v ? v.v : null),
    }
  }),
})))

/* ================= 固定节拍推进（右侧趋势 + 快照） =================
 * ★ 老板定：视频仍是时间真源，但右侧不逐帧刷新，而是**按固定节拍跳档**
 *   （每 BEAT_MS 把 beatSec 对齐到当前画面时刻）→ 观感"一档一档往前走"。
 *   HUD / 曲线游标 / 工艺链 9 点 / KPI / 文字研判 全部由 beatSec 派生。
 */
const BEAT_MS = 800             // 节拍间隔（右侧刷新频率）—— 配合数据 30x 快进，曲线滚动肉眼可见
const beatSec = ref(0)          // 节拍时刻（当日秒）—— 右侧所有数据的唯一来源
let beatTimer = null

function beatTick() {
  if (beatTimer) clearTimeout(beatTimer)
  beatTimer = setTimeout(() => {
    if (playing.value) beatSec.value = Math.floor(dataSec.value)
    beatTick()
  }, BEAT_MS)
}

/** ★★ 滚动窗口跨度（分钟）—— x 轴 = [游标-跨度, 游标]，光标恒在**最右端**
 *  老板要"光标钉在最右、曲线往左滚"→ 真实实时监控的观感。
 *  ★ 取 30min（非 60min）：化验 16.6min 一测，30min 窗口内 2 个测点 + 左缘锚点
 *    → 每 16.6min 数据（≈33s 画面，30x 快进）就滑出一整段，滚动感明显强于 60min。 */
const WIN_MIN = 30

/* ---------------- 给矿品位趋势（hero） ---------------- */
const heroNow = computed(() => valueAt(CAM.point, CAM.element))
const heroValText = computed(() => (heroNow.value ? heroNow.value.v.toFixed(2) : '—'))

/** 当前滚动窗口（近 WIN_MIN 分钟）统计 —— 随视频推进而滑动
 *  ★ 与曲线 x 轴同口径：窗口下界 = 节拍时刻 - WIN_MIN。 */
const heroStats = computed(() => {
  const s = seriesOf(CAM.point, CAM.element)
  if (!s || !s.values.length) return null
  const secs = secsOf(s)
  const lo = tickSec.value - WIN_MIN * 60
  const vals = []
  s.values.forEach((p, i) => { if (secs[i] >= lo && secs[i] <= tickSec.value) vals.push(p.v) })
  if (!vals.length) return null
  let mn = Infinity, mx = -Infinity, sum = 0
  for (const v of vals) { if (v < mn) mn = v; if (v > mx) mx = v; sum += v }
  return { n: vals.length, min: mn, max: mx, avg: sum / vals.length }
})

/** 近 30 分钟变化量 */
const trendDelta30 = computed(() => {
  const n = heroNow.value
  if (!n) return null
  const p = valueAtT(CAM.point, CAM.element, tickSec.value - 1800)
  if (!p) return null
  return n.v - p.v
})

/** 文字研判（随视频推进实时刷新） */
const trendComment = computed(() => {
  const st = heroStats.value
  const d = trendDelta30.value
  const n = heroNow.value
  if (!st || !n) return '在线分析仪数据接入中…'
  if (d == null) {
    // 窗口尚短（录像刚开播）→ 给出当前读数与窗口内位置，而非"接入中"
    return `当前给矿品位 ${n.v.toFixed(2)}%，处于近 ${WIN_MIN} 分钟${zoneOf(n, st)} · 随画面推进向左滚动`
  }
  const pos = (n.v - st.min) / ((st.max - st.min) || 1)
  const zone = pos < 0.34 ? '低位区间' : pos < 0.67 ? '中位区间' : '高位区间'
  const a = Math.abs(d)
  if (a < 0.03) return `较一档前基本持稳 · 当前处于近 ${WIN_MIN} 分钟${zone}`
  const verb = a < 0.12 ? (d > 0 ? '缓升' : '缓降') : (d > 0 ? '上行' : '下行')
  return `较上一测点${verb} ${a.toFixed(2)} 个百分点 · 当前处于近 ${WIN_MIN} 分钟${zone}`
})
function zoneOf(n, st) {
  const pos = (n.v - st.min) / ((st.max - st.min) || 1)
  return pos < 0.34 ? '低位区间' : pos < 0.67 ? '中位区间' : '高位区间'
}

const statChips = computed(() => {
  const st = heroStats.value
  if (!st) return []
  return [
    { k: '窗口均值', v: `${st.avg.toFixed(2)}%` },
    { k: '波动区间', v: `${st.min.toFixed(2)} ~ ${st.max.toFixed(2)}%` },
    { k: '窗口测点', v: `${st.n} 点` },
  ]
})

/** 当前窗口内最新一次化验的时刻（真实采样，非插值） */
const latestAssay = computed(() => {
  const s = seriesOf(CAM.point, CAM.element)
  if (!s || !s.values.length) return '—'
  const secs = secsOf(s)
  const lo = tickSec.value - WIN_MIN * 60
  let last = null
  s.values.forEach((p, i) => { if (secs[i] >= lo && secs[i] <= tickSec.value) last = p })
  return last ? last.ts.slice(11, 16) : '—'
})

/* ---------------- 趋势曲线 ---------------- */
const dayMs = (h, m, s = 0) => new Date(2019, 7, 22, h, m, s).getTime()

/** ★★ 曲线游标 x = 节拍时刻（= 窗口右端 = "现在"） */
const xEnd = computed(() => dayMs(0, 0) + tickSec.value * 1000)
const xStart = computed(() => xEnd.value - WIN_MIN * 60 * 1000)

/** ★★ ①B：趋势图叠加多测点 —— 给矿→铅精矿→铅扫选尾矿，构成「品位链条」
 *  ★ 起伏来自**真实工艺差异**（精矿 59% vs 尾矿 0.45%），不是放大 y 轴 → 老板要"变化大"又不假
 *  量级差异用双 y 轴：左轴 = 给矿/尾矿（低品位），右轴 = 精矿（高品位） */
const TREND_SERIES = [
  { key: 'Feed II|Pb', name: '给矿', axis: 0, color: '#165dff', width: 2.5, main: true },
  { key: 'Xpb II|Pb', name: '铅扫选尾矿', axis: 0, color: '#00b42a', width: 1.8, main: false },
  { key: 'Kpb II|Pb', name: '铅精矿', axis: 1, color: '#ff7d00', width: 1.8, main: false },
]

/** 左轴范围（给矿+尾矿，低品位段）：含窗口内这两条线的所有点，量化到 0.5 */
const leftAxis = computed(() => {
  const keys = TREND_SERIES.filter((t) => t.axis === 0).map((t) => t.key)
  const vals = []
  const lo0 = tickSec.value - WIN_MIN * 60
  for (const k of keys) {
    const [p, e] = k.split('|')
    const s = seriesOf(p, e)
    if (!s) continue
    const secs = secsOf(s)
    s.values.forEach((pt, i) => { if (secs[i] >= lo0 && secs[i] <= tickSec.value) vals.push(pt.v) })
  }
  if (!vals.length) return { min: 0, max: 5 }
  const mn = Math.min(...vals), mx = Math.max(...vals)
  const pad = Math.max((mx - mn) * 0.15, 0.15)
  const step = 0.5
  return {
    min: Math.max(0, Math.floor((mn - pad) / step) * step),
    max: Math.ceil((mx + pad) / step) * step,
  }
})

/** 右轴范围（铅精矿，高品位段）：量化到 1 */
const rightAxis = computed(() => {
  const s = seriesOf('Kpb II', 'Pb')
  if (!s || !s.values.length) return { min: 50, max: 65 }
  const lo0 = tickSec.value - WIN_MIN * 60
  const secs = secsOf(s)
  const vals = []
  s.values.forEach((p, i) => { if (secs[i] >= lo0 && secs[i] <= tickSec.value) vals.push(p.v) })
  if (!vals.length) return { min: 50, max: 65 }
  const mn = Math.min(...vals), mx = Math.max(...vals)
  const pad = Math.max((mx - mn) * 0.35, 1)
  return { min: Math.floor(mn - pad), max: Math.ceil(mx + pad) }
})

/** 主曲线（给矿）的 y 轴范围 —— 保留原「诚实刻度」逻辑（暂未使用，左轴已覆盖） */


function buildOption() {
  // ★★ 滚动窗口：x 轴 = [游标-WIN_MIN, 游标]，游标恒在**最右端**
  const X0 = xStart.value, X1 = xEnd.value
  const XCUR = X1 // 光标 = 右端（"现在"）
  const st = heroStats.value
  const lax = leftAxis.value
  const rax = rightAxis.value

  // 光标只在右端，故不需要 x markLine（右端即"现在"）；主图保留均值虚线
  const markLines = []
  if (st) {
    markLines.push({
      yAxis: Number(st.avg.toFixed(3)),
      lineStyle: { color: '#c9cdd4', width: 1, type: 'dashed' },
      label: { show: true, formatter: `均值 ${st.avg.toFixed(2)}`, color: '#86909c', fontSize: 10, position: 'insideStartTop' },
    })
  }

  const baseSeries = {
    type: 'line', smooth: false, showSymbol: false, emphasis: { disabled: true },
  }

  /** 构造一条滚动窗口曲线（含左缘锚点 + 末端补点） */
  function lineDataOf(point, element) {
    const s = seriesOf(point, element)
    const allRaw = (s?.values || [])
      .map((p) => { const [h, m, sec] = p.ts.split(':').map(Number); return [dayMs(h, m, sec), p.v] })
      .filter((d) => d[0] <= X1)
      .sort((a, b) => a[0] - b[0])
    const anchor = allRaw.filter((d) => d[0] < X0).slice(-1)
    const inWin = allRaw.filter((d) => d[0] >= X0)
    const allPts = [...anchor, ...inWin]
    // 末端补「此刻」插值点 → 接到右端光标
    const nowVal = valueAt(point, element)?.v
    const linePts = (nowVal != null && allPts.length)
      ? [...allPts, [XCUR, Number(nowVal.toFixed(3))]]
      : allPts
    return { linePts, realPts: inWin, nowVal }
  }

  // 主曲线（给矿）—— 面积 + 实线 + 均值线 + 落点
  const hero = lineDataOf(CAM.point, CAM.element)

  // 非主测点（尾矿 / 精矿）—— 细线 + 末端小点
  const extraSeries = TREND_SERIES.filter((t) => !t.main).map((t) => {
    const [p, e] = t.key.split('|')
    const d = lineDataOf(p, e)
    return {
      ...baseSeries,
      name: t.name,
      yAxisIndex: t.axis,
      z: 3,
      legendHoverLink: false,
      lineStyle: { width: t.width, color: t.color, opacity: 0.85 },
      itemStyle: { color: t.color },
      data: d.linePts,
      markPoint: d.nowVal != null ? {
        silent: true, symbol: 'circle', symbolSize: 7,
        itemStyle: { color: t.color, borderColor: '#fff', borderWidth: 1.5 },
        label: { show: false },
        data: [{ coord: [XCUR, Number(d.nowVal.toFixed(3))] }],
      } : undefined,
    }
  })

  return {
    animation: false,
    grid: { left: 44, right: 44, top: 22, bottom: 20 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: '#c9cdd4' } },
      valueFormatter: (v) => (v == null ? '—' : Number(v).toFixed(2) + '%'),
    },
    legend: { show: false },
    xAxis: {
      // ★ 10min 一档刻度（30min 窗口 = 3~4 个标签）；time 轴须用 min/maxInterval 强约束
      type: 'time', min: X0, max: X1,
      minInterval: 10 * 60 * 1000, maxInterval: 10 * 60 * 1000,
      axisLine: { lineStyle: { color: '#c9cdd4' } },
      axisLabel: { color: '#86909c', fontSize: 11, hideOverlap: true, formatter: (v) => { const d = new Date(v); return `${pad(d.getHours())}:${pad(d.getMinutes())}` } },
      splitLine: { show: true, lineStyle: { color: '#f7f8fa' } },
    },
    yAxis: [
      {
        // 左轴：给矿 + 尾矿（低品位段）
        type: 'value',
        min: lax.min, max: lax.max,
        splitNumber: 3,
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: '#86909c', fontSize: 11, formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#f2f3f5' } },
      },
      {
        // 右轴：铅精矿（高品位段）
        type: 'value',
        min: rax.min, max: rax.max,
        splitNumber: 3,
        position: 'right',
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: '#ff7d00', fontSize: 11, formatter: '{value}%' },
        splitLine: { show: false },
      },
    ],
    series: [
      // 面积：填满整个滚动窗口（"至今"已发生）
      {
        ...baseSeries,
        name: '已发生区间', silent: true, z: 2, yAxisIndex: 0, legendHoverLink: false,
        lineStyle: { width: 0, opacity: 0 },
        itemStyle: { color: 'rgba(22,93,255,.14)' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(22,93,255,.22)' },
            { offset: 1, color: 'rgba(22,93,255,0)' },
          ]),
        },
        data: hero.linePts,
      },
      // 主测点（给矿）：实线 + 均值虚线 + 右端落点
      {
        ...baseSeries,
        name: `给矿品位 ${CAM.element}`, z: 4, yAxisIndex: 0, legendHoverLink: false,
        lineStyle: { width: 2.5, color: C_LINE },
        itemStyle: { color: C_LINE },
        data: hero.linePts,
        markLine: { silent: true, symbol: 'none', animation: false, data: markLines },
        markPoint: hero.nowVal != null ? {
          silent: true, symbol: 'circle', symbolSize: 9,
          itemStyle: { color: C_LINE, borderColor: '#fff', borderWidth: 2 },
          label: { show: false },
          data: [{ coord: [XCUR, Number(hero.nowVal.toFixed(3))] }],
        } : undefined,
      },
      // ★ 叠加：铅扫选尾矿（左轴）/ 铅精矿（右轴）
      ...extraSeries,
      // ★ 实测点标记（仅主测点）：真实化验点各打一个小圆点
      {
        ...baseSeries,
        name: '实测点', silent: true, z: 5, yAxisIndex: 0, legendHoverLink: false,
        lineStyle: { width: 0, opacity: 0 },
        symbol: 'circle', symbolSize: 5, showSymbol: true,
        itemStyle: { color: '#fff', borderColor: C_LINE, borderWidth: 2 },
        tooltip: { show: false },
        data: hero.realPts,
      },
    ],
  }
}

function renderChart() { if (chart) chart.setOption(buildOption(), { notMerge: true }) }

function onResize() { chart && chart.resize() }

/** ★★ 节拍一跳 → 重绘曲线（右侧"跳档推进"的关键；不再是逐帧） */
watch(tickSec, () => renderChart())

/** ★★ 数据到达/刷新也必须重绘 —— 不能只靠节拍（视频未播放时节拍不变，曲线会一直空白） */
watch(dayData, () => nextTick(() => { ensureChart(); renderChart() }))

/** 确保 echarts 实例挂在有高度的容器上（0 高度初始化会渲染成空白） */
function ensureChart() {
  const el = chartEl.value
  if (!el) return
  if (!chart) {
    chart = echarts.init(el)
    return
  }
  if (!el.clientWidth || !el.clientHeight) return
  chart.resize()
}

let ro = null

/** ★ 标签页重新可见 / 窗口聚焦时 → 尺寸可能已变，重绘一次（长时间挂后台的页面常因此空白） */
function onVisible() {
  if (document.visibilityState === 'visible') nextTick(() => { ensureChart(); renderChart() })
}

onMounted(async () => {
  try { dayData.value = await api.get(`/courier/day?date=${DATA_DATE}`) }
  catch (e) { dayData.value = { count: 0, series: [] } }
  try { bands.value = await api.get('/courier/bands') }
  catch (e) { bands.value = {} }
  await nextTick()
  if (chartEl.value) {
    chart = echarts.init(chartEl.value)
    renderChart()
    window.addEventListener('resize', onResize)
    document.addEventListener('visibilitychange', onVisible)
    // ★★ 容器尺寸变化（含布局稳定 / 高度从 0 变正）→ 自适应重绘，避免 0 高度初始化后永远空白
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => { chart && chart.resize() })
      ro.observe(chartEl.value)
    }
  }
  playVideo()
  // ★ 节拍：首帧即对齐一次，随后每 BEAT_MS 跳一档
  beatSec.value = Math.floor(dataSec.value)
  beatTick()
})

onBeforeUnmount(() => {
  if (beatTimer) clearTimeout(beatTimer)
  if (ro) { ro.disconnect(); ro = null }
  window.removeEventListener('resize', onResize)
  document.removeEventListener('visibilitychange', onVisible)
  if (chart) { chart.dispose(); chart = null }
})
</script>

<template>
  <div class="monitor">
    <!-- 顶栏 -->
    <div class="mn-top">
      <div class="mn-top-left">
        <div class="mn-title">
          <h2>实时监控</h2>
          <span class="mn-live"><i />LIVE</span>
        </div>
        <div class="mn-kpis">
          <div v-for="k in kpis" :key="k.label" class="mk">
            <span class="mk-label">{{ k.label }}</span>
            <span class="mk-val">{{ k.val }}<i>{{ k.unit }}</i></span>
          </div>
        </div>
      </div>
      <div class="mn-clock">
        <span class="mn-time">{{ camClock }}</span>
        <span class="mn-date">画面时刻 · 2019-08-22 实录</span>
      </div>
    </div>

    <!-- 主体：左「现场画面」 · 右「品位趋势 + 实时快照」 -->
    <div class="mn-body">
      <!-- 左：现场画面（唯一点位） -->
      <div class="mn-stage">
        <div class="stage-video">
          <video
            v-show="!failed"
            ref="videoEl"
            class="cam-video"
            :src="videoSrc"
            muted
            playsinline
            preload="auto"
            @timeupdate="onTimeUpdate"
            @ended="onVideoEnded"
            @error="failed = true"
          />
          <div v-if="failed" class="stage-fallback">画面暂不可用</div>
          <!-- 画面内 HUD：该点位实时品位（画面即数据） -->
          <div class="cam-hud">
            <div class="hud-top">
              <span class="hud-name">{{ CAM.name }}</span>
              <span class="hud-live"><i />LIVE</span>
            </div>
            <div class="hud-bottom">
              <div class="hud-val-wrap">
                <span class="hud-lbl">{{ CAM.label }}</span>
                <span class="hud-val">{{ heroValText }}<i>{{ CAM.unit }}</i></span>
              </div>
              <span class="hud-point">{{ CAM.point }} · 在线分析仪</span>
            </div>
          </div>
        </div>
        <div class="stage-bar">
          <el-button size="small" circle :icon="playing ? VideoPause : VideoPlay" @click="togglePlay" />
          <span class="stage-seg">现场录像 {{ segIndex + 1 }}/{{ allSegs.length }} 段 · {{ segLabel }}</span>
          <span class="stage-tick">数据节拍 {{ tickClock }} · 每 {{ (BEAT_MS / 1000).toFixed(1) }}s 推进</span>
          <span class="stage-src">凡口浮选车间实录 · 2019-08-22</span>
        </div>
      </div>

      <!-- 右：给矿品位趋势 + 工艺链实时快照 -->
      <div class="mn-side">
        <!-- 给矿品位趋势 -->
        <div class="side-card side-chart">
          <div class="side-head">
            <span>给矿品位趋势</span>
            <span class="side-sub">{{ CAM.point }} · {{ CAM.element }} · 滚动 {{ WIN_MIN }}min</span>
            <span class="side-legend">
              <i class="lg-ink" style="background:#165dff" />给矿
              <i class="lg-ink" style="background:#00b42a" />铅尾矿
              <i class="lg-ink" style="background:#ff7d00" />铅精矿
            </span>
          </div>
          <div class="tstats">
            <div v-for="c in statChips" :key="c.k" class="tstat">
              <span class="tstat-k">{{ c.k }}</span>
              <b class="tstat-v">{{ c.v }}</b>
            </div>
          </div>
          <div ref="chartEl" class="chart" />
          <div class="trend-note">{{ trendComment }}</div>
        </div>

        <!-- 工艺链实时快照 -->
        <div class="side-card side-chain">
          <div class="side-head">
            <span>工艺链实时快照</span>
            <span class="side-sub">9 个测点 · 当前值</span>
            <span class="side-tick">{{ tickClock }}</span>
          </div>
          <div class="chain">
            <div v-for="g in chainRows" :key="g.group" class="chain-group">
              <div class="chain-group-title">{{ g.group }}</div>
              <div v-for="r in g.rows" :key="r.key" class="chain-row" :class="{ stale: r.cur == null }">
                <span class="cr-name">
                  <i v-if="r.dev" class="cr-dot-st" :class="'lv-' + r.dev.level" />
                  {{ r.name }}
                </span>
                <span class="cr-line" :title="r.dev ? `参照标准：${r.dev.tip}` : ''">
                  <svg v-if="r.spark" class="cr-spark" viewBox="0 0 100 24" preserveAspectRatio="none">
                    <defs>
                      <linearGradient :id="'cg-' + r.gid" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0" stop-color="#165dff" stop-opacity=".22" />
                        <stop offset="1" stop-color="#165dff" stop-opacity="0" />
                      </linearGradient>
                    </defs>
                    <rect v-if="r.spark.band" :y="r.spark.band.y" :height="r.spark.band.h" x="0" width="100" class="cr-band" />
                    <polygon :points="r.spark.area" :fill="'url(#cg-' + r.gid + ')'" stroke="none" />
                    <polyline :points="r.spark.line" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
                  </svg>
                  <span v-if="r.spark" class="cr-dot" :style="{ left: r.spark.dot[0] + '%', top: (r.spark.dot[1] / 24 * 100) + '%' }" />
                  <span v-else class="cr-spark-empty">—</span>
                </span>
                <b class="cr-val" :class="r.dev ? 'lv-' + r.dev.level : ''">{{ r.cur != null ? r.cur.toFixed(2) : '—' }}<i>%</i></b>
                <span v-if="r.dev" class="cr-dev" :class="'lv-' + r.dev.level" :title="r.dev.tip">{{ r.dev.tag }}</span>
                <span v-else class="cr-dev lv-na">—</span>
              </div>
            </div>
          </div>
          <div class="side-foot">近 {{ WIN_MIN }} 分钟滚动窗口 · 圆点 = 当前节拍落点（最右 = 现在）· 数据源：在线分析仪（2019-08-22 实录）· * 语义待确认</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.monitor { display: flex; flex-direction: column; gap: 10px; height: calc(100vh - 40px); min-height: 0; }

/* 顶栏 */
.mn-top { display: flex; align-items: center; justify-content: space-between; gap: 20px; flex-shrink: 0; }
.mn-top-left { display: flex; align-items: center; gap: 26px; min-width: 0; }
.mn-title { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.mn-title h2 { margin: 0; font-size: 17px; color: #1d2129; white-space: nowrap; }
.mn-live {
  display: inline-flex; align-items: center; gap: 5px; padding: 2px 8px; border-radius: 4px;
  background: #ffece8; color: #f53f3f; font-size: 11px; font-weight: 700; letter-spacing: .5px;
}
.mn-live i { width: 6px; height: 6px; border-radius: 50%; background: #f53f3f; animation: mn-breath 1.6s ease-in-out infinite; }
@keyframes mn-breath { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: .35; transform: scale(.82); } }

.mn-kpis { display: flex; gap: 26px; align-items: center; min-width: 0; }
.mk { display: flex; flex-direction: column; gap: 1px; }
.mk-label { font-size: 11px; color: #86909c; }
.mk-val { font-size: 19px; font-weight: 600; color: #1d2129; font-variant-numeric: tabular-nums; line-height: 1.15; }
.mk-val i { font-style: normal; font-size: 10px; font-weight: 400; color: #86909c; margin-left: 3px; }

.mn-clock { display: flex; align-items: baseline; gap: 10px; flex-shrink: 0; }
.mn-time { font-size: 22px; font-weight: 600; color: #1d2129; font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, Consolas, monospace; }
.mn-date { font-size: 12px; color: #86909c; }

/* 主体：两栏 —— 现场画面 / 品位趋势 + 实时快照 */
.mn-body { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1.75fr) minmax(0, 1fr); gap: 10px; }

/* 左：主画面 */
.mn-stage { min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 8px; }
.stage-video { position: relative; flex: 1; min-height: 0; border-radius: 8px; overflow: hidden; background: #0f1419; }
.cam-video { display: block; width: 100%; height: 100%; object-fit: cover; }
.stage-fallback { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #86909c; font-size: 13px; }

.cam-hud { position: absolute; inset: 0; pointer-events: none; display: flex; flex-direction: column; justify-content: space-between; padding: 12px 14px; }
.hud-top { display: flex; align-items: center; justify-content: space-between; }
.hud-name {
  font-size: 13px; font-weight: 600; color: #fff;
  padding: 4px 10px; border-radius: 4px; background: rgba(0,0,0,.62);
  backdrop-filter: blur(2px);
}
.hud-live {
  display: flex; align-items: center; gap: 4px; font-size: 10px; font-weight: 700; color: #fff;
  letter-spacing: .5px; padding: 4px 8px; border-radius: 3px; background: rgba(0,0,0,.62);
  backdrop-filter: blur(2px);
}
.hud-live i { width: 5px; height: 5px; border-radius: 50%; background: #f53f3f; animation: mn-breath 1.6s ease-in-out infinite; }
.hud-bottom {
  display: flex; align-items: flex-end; justify-content: space-between;
  padding: 30px 14px 6px; margin: 0 -14px -12px;
  background: linear-gradient(transparent, rgba(0,0,0,.45) 30%, rgba(0,0,0,.85));
}
.hud-val-wrap { display: flex; flex-direction: column; gap: 3px; }
.hud-lbl { font-size: 12px; color: rgba(255,255,255,.82); text-shadow: 0 1px 3px rgba(0,0,0,.8); }
.hud-val {
  font-size: 34px; font-weight: 700; color: #fff; font-variant-numeric: tabular-nums;
  text-shadow: 0 2px 6px rgba(0,0,0,.85), 0 0 2px rgba(0,0,0,.9);
  line-height: 1;
}
.hud-val i { font-style: normal; font-size: 14px; font-weight: 400; color: rgba(255,255,255,.85); margin-left: 4px; }
.hud-point {
  font-size: 10px; color: rgba(255,255,255,.85); font-family: ui-monospace, monospace;
  text-shadow: 0 1px 3px rgba(0,0,0,.8);
}

/* 画面控制条 */
.stage-bar { flex-shrink: 0; display: flex; align-items: center; gap: 10px; min-height: 24px; padding: 0 2px; }
.stage-seg { font-size: 11px; color: #86909c; }
.stage-tick { font-size: 11px; color: #165dff; font-variant-numeric: tabular-nums; }
.stage-src { margin-left: auto; font-size: 11px; color: #c9cdd4; }

/* 右栏：曲线自适应撑满，工艺链按内容高度（保证 9 行永不裁切） */
.mn-side { min-height: 0; display: grid; grid-template-rows: minmax(0, 1fr) minmax(0, auto); gap: 10px; }
.side-card { background: #fff; border: 1px solid #e5e6eb; border-radius: 8px; padding: 12px; min-height: 0; display: flex; flex-direction: column; gap: 8px; }
.side-head { display: flex; align-items: baseline; gap: 8px; flex-shrink: 0; }
.side-head > span:first-child { font-size: 14px; font-weight: 600; color: #1d2129; white-space: nowrap; }
.side-sub { font-size: 11px; color: #86909c; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 窄屏（≤1500）优先保住标题不折行，隐藏副标题细节 */
@media (max-width: 1500px) { .side-sub { display: none; } }
.side-legend { margin-left: auto; display: flex; align-items: center; gap: 5px; font-size: 10px; color: #86909c; }
.side-legend .lg-ink { display: inline-block; width: 9px; height: 3px; border-radius: 2px; margin-right: 2px; vertical-align: middle; }

/* 趋势卡 */
.tstats { display: flex; gap: 18px; flex-shrink: 0; }
.tstat { display: flex; align-items: baseline; gap: 4px; }
.tstat-k { font-size: 10px; color: #86909c; }
.tstat-v { font-size: 13px; font-weight: 600; color: #1d2129; font-variant-numeric: tabular-nums; }
.chart { flex: 1; min-height: 150px; }
.trend-note {
  flex-shrink: 0; display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: #4e5969; background: #f7f8fa; border-radius: 4px; padding: 5px 9px;
}
.trend-note::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: #165dff; flex-shrink: 0; }

/* 工艺链 —— ★ 放大版（老板要求"大一点、有感觉"） */
.chain { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 8px; overflow: hidden; }
.chain-group { display: flex; flex-direction: column; gap: 4px; }
.chain-group-title { font-size: 12px; color: #86909c; font-weight: 600; padding-bottom: 3px; border-bottom: 1px dashed #e5e6eb; }
.chain-row { display: grid; grid-template-columns: 88px minmax(0, 1fr) 66px 40px; align-items: center; gap: 8px; padding: 3px 0; }
.chain-row.stale .cr-name, .chain-row.stale .cr-val { color: #c9cdd4; }
.cr-name { font-size: 13px; color: #4e5969; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: flex; align-items: center; gap: 5px; }
/* ★ 名称前状态点：相对历史 P10~P90 的偏离度 */
.cr-dot-st { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; background: #00b42a; }
.cr-dot-st.lv-ok { background: #00b42a; }
.cr-dot-st.lv-high, .cr-dot-st.lv-low { background: #ff7d00; }
.cr-dot-st.lv-over, .cr-dot-st.lv-under { background: #f53f3f; }
/* ★ 小线容器（折线 + 面积 + 正常区间背景带 + 末端"现在"落点）—— 高度 26px */
.cr-line { position: relative; display: block; width: 100%; height: 26px; min-width: 0; }
.cr-spark { display: block; width: 100%; height: 26px; color: #2f7bff; min-width: 0; }
/* ★ 正常区间背景带（历史 P10~P90）：淡绿 = 合格区，线在带内 = 正常 */
.cr-band { fill: #00b42a; fill-opacity: .10; }
/* 末端实心落点 = "现在"；带白色描边 + 光晕，并轻微呼吸（实时感） */
.cr-dot {
  position: absolute; width: 9px; height: 9px; border-radius: 50%;
  background: #165dff; border: 1.5px solid #fff; box-sizing: border-box;
  transform: translate(-50%, -50%);
  box-shadow: 0 0 0 1.5px rgba(22,93,255,.45), 0 0 6px 1px rgba(22,93,255,.35);
  pointer-events: none;
  animation: cr-pulse 2s ease-in-out infinite;
}
@keyframes cr-pulse {
  0%, 100% { box-shadow: 0 0 0 1.5px rgba(22,93,255,.45), 0 0 6px 1px rgba(22,93,255,.30); }
  50% { box-shadow: 0 0 0 2.5px rgba(22,93,255,.22), 0 0 11px 3px rgba(22,93,255,.42); }
}
.cr-spark-empty { display: flex; align-items: center; height: 26px; font-size: 11px; color: #c9cdd4; }
.cr-val {
  font-size: 17px; font-weight: 600; color: #1d2129; font-variant-numeric: tabular-nums;
  text-align: right; white-space: nowrap;
}
.cr-val i { font-style: normal; font-size: 10px; font-weight: 400; color: #86909c; margin-left: 1px; }
/* ★ 数值按偏离度着色 */
.cr-val.lv-high, .cr-val.lv-low { color: #d25f00; }
.cr-val.lv-over, .cr-val.lv-under { color: #cb2634; }
/* ★ 偏离度标签（数值右侧） */
.cr-dev {
  font-size: 10px; line-height: 1; text-align: center; padding: 3px 0; border-radius: 3px;
  white-space: nowrap; cursor: default;
}
.cr-dev.lv-ok { color: #00b42a; background: #e8ffea; }
.cr-dev.lv-high, .cr-dev.lv-low { color: #d25f00; background: #fff3e8; }
.cr-dev.lv-over, .cr-dev.lv-under { color: #cb2634; background: #ffece8; }
.cr-dev.lv-na { color: #c9cdd4; background: transparent; }

/* 节拍标识（右侧数据刷新时刻） */
.side-tick {
  margin-left: auto; font-size: 10px; font-weight: 600; color: #165dff;
  font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, Consolas, monospace;
  padding: 1px 6px; border-radius: 3px; background: #f0f5ff;
}

.side-foot { font-size: 10px; color: #c9cdd4; flex-shrink: 0; }

@media (max-height: 880px) {
  .monitor { height: auto; }
  .mn-body { min-height: 640px; }
  /* ★ 矮屏：工艺链回落到紧凑尺寸，避免 9 行溢出（放大只在 ≥900 高度生效） */
  .chain { gap: 5px; }
  .chain-group { gap: 2px; }
  .chain-group-title { font-size: 11px; padding-bottom: 2px; }
  .chain-row { grid-template-columns: 78px minmax(0, 1fr) 56px 34px; gap: 6px; padding: 0; }
  .cr-name { font-size: 12px; }
  .cr-line, .cr-spark, .cr-spark-empty { height: 20px; }
  .cr-val { font-size: 14px; }
  .cr-dev { font-size: 9px; padding: 2px 0; }
  .cr-dot { width: 7px; height: 7px; }
}
</style>
