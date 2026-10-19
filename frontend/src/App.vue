<script setup>
import { onMounted, computed, ref, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ensureAlarmNotifications } from './api/ws'
import api from './api'
import { NAV_ITEMS, NAV_GROUPS, NAV_SEGMENTS, SEG_QUERY_KEY, titleOf } from './utils/navMap'

const route = useRoute()
const router = useRouter()

/**
 * 侧栏分组 —— 名称/路径/图标全部来自 `utils/navMap.js`（唯一真源）。
 * ★ 2026-10-19 结构调整（老板拍板方案 B）：
 *   · 分组由「运营 / 资产 / 系统」3 组改为「总览 / 生产 / 质量与资产 / 系统」4 组
 *     —— 按**使用场景**分，不按组织架构分（"告警算运营还是资产"这个问题没有答案）
 *   · 「智能工厂」下面挂 4 个工艺段子项，显示各段健康度：它是"厂区现在什么样"的第一眼
 */
const groups = computed(() =>
  NAV_GROUPS.map((g) => ({
    ...g,
    items: Object.values(NAV_ITEMS).filter((it) => it.group === g.key),
  }))
)

// ── 工艺段健康度（侧栏子项用）──────────────────────
/**
 * ★ 为什么在 App.vue 里拉设备数据：
 *   侧栏是全站常驻的，4 个工艺段健康度必须**离开 3D 页也看得到**。
 *   如果只让 Equipment3D 算，那切到告警页侧栏就变空——比不显示更奇怪。
 * 数据量很小（10 台设备），20s 轮询一次，与 3D 页的 5s 轮询互不干扰。
 */
const segHealth = ref({})   // { grinding: {avg, bad, warn, count}, ... }
let segTimer = null

async function loadSegHealth() {
  try {
    const data = await api.get('/equipment')
    const list = data?.equipment || []
    const out = {}
    for (const seg of NAV_SEGMENTS) {
      // ★ 段与设备的归属关系只在 Equipment3D.PROCESS_SEGMENTS 里维护，
      //   这里按"段的 route 页"无法反推，所以改用设备 id 前缀约定不合适 ——
      //   直接把归属表放进 navMap 的调用方（见下方 SEG_EQUIP_IDS）
      const eids = SEG_EQUIP_IDS[seg.segKey] || []
      const eqs = list.filter((e) => eids.includes(e.id))
      if (!eqs.length) { out[seg.segKey] = null; continue }
      out[seg.segKey] = {
        avg: Math.round(eqs.reduce((s, e) => s + (e.health || 0), 0) / eqs.length),
        bad: eqs.filter((e) => e.status === 'danger').length,
        warn: eqs.filter((e) => e.status === 'warning').length,
        count: eqs.length,
      }
    }
    segHealth.value = out
  } catch {
    /* 后端未启动时静默：侧栏不显示百分比，不影响导航 */
  }
}

/** 段 → 设备 id（与 Equipment3D 的 PROCESS_SEGMENTS.equipIds 保持一致） */
const SEG_EQUIP_IDS = {
  grinding:   ['ball_mill'],
  flotation:  ['flotation_1', 'flotation_2', 'agitator', 'pump_1'],
  concentrate:['conveyor'],
  tailings:   ['thickener', 'tailings_pool', 'water_return', 'water_dosing'],
}

/** 各段摘要文案：故障 > 预警 > 全部正常；无数据时留空（不填 0） */
function segStatusText(segKey) {
  const h = segHealth.value[segKey]
  if (!h) return ''
  if (h.bad) return `${h.bad} 故障`
  if (h.warn) return `${h.warn} 预警`
  return '正常'
}
function segStatusClass(segKey) {
  const h = segHealth.value[segKey]
  if (!h) return ''
  if (h.bad) return 'is-bad'
  if (h.warn) return 'is-warn'
  return 'is-ok'
}

/** 工艺段子项的点击：回到 3D 页并带上要聚焦的段（不跳功能页） */
function focusSegOnMap(segKey) {
  const target = { path: '/', query: { [SEG_QUERY_KEY]: segKey } }
  if (route.path === '/') {
    // 已在首页：直接改 query，3D 页 watch 到变化会飞相机
    router.replace(target)
  } else {
    router.push(target)
  }
}

const currentPath = computed(() => {
  // 工作台高亮匹配前缀
  if (route.path.startsWith('/workbench')) return '/workbench/flotation'
  // /equipment 已重定向到首页，高亮也跟着走
  if (route.path === '/equipment') return '/'
  return route.path
})

/** 页面标题（面包屑用，与 NAV 名称同源） */
const pageTitle = computed(() => titleOf(route.path))

// 启动全局 WS 告警通知
onMounted(() => {
  ensureAlarmNotifications()
  loadSegHealth()
  segTimer = setInterval(loadSegHealth, 20000)
})
onUnmounted(() => {
  if (segTimer) clearInterval(segTimer)
})
</script>

<template>
  <el-container class="app-shell">
    <el-aside width="220px" class="app-aside">
      <!-- 品牌区（克制） -->
      <div class="brand">
        <div class="brand-mark">淬眸</div>
        <div class="brand-sub">SuiEye · 铅锌浮选视觉平台</div>
      </div>

      <!-- 导航（分组） -->
      <nav class="nav">
        <div v-for="group in groups" :key="group.key" class="nav-group">
          <div class="nav-title">{{ group.title }}</div>
          <template v-for="item in group.items" :key="item.path">
            <router-link
              :to="item.path"
              class="nav-item"
              :class="{ active: currentPath === item.path }"
            >
              <el-icon class="nav-icon"><component :is="item.icon" /></el-icon>
              <span>{{ item.name }}</span>
            </router-link>
          </template>

          <!-- 工艺段子项：只在「总览」组、只在"智能工厂"当前页时出现
               ★ 子项是**只读状态行 + 聚焦入口**，不是第二套菜单：
                 点击 = 让 3D 相机飞到该段，不跳功能页（功能照旧从设备/建筑进）。
               ⚠️ 必须挂在 v-for 的 <template> **外部**：挂进去会被复制 3 遍
                  （每个总览条目一份），实测侧栏出现「磨矿段 ×3」。
               ★ 侧栏只有 220px，每行只放"圆点 + 段名 + %"；
                 "1 预警 / 1 故障"这类细节交给 3D 里的浮层导览（那边是全宽）。 -->
          <div
            v-if="group.expandable && currentPath === '/'"
            class="nav-segs"
          >
            <button
              v-for="seg in NAV_SEGMENTS"
              :key="seg.segKey"
              class="nav-seg"
              :class="[segStatusClass(seg.segKey), { 'is-focus': route.query[SEG_QUERY_KEY] === seg.segKey }]"
              :title="`${seg.name} · 点击在厂区中定位`"
              @click="focusSegOnMap(seg.segKey)"
            >
              <i class="seg-dot" :style="{ background: seg.color }"></i>
              <span class="seg-name">{{ seg.name }}</span>
              <span class="seg-pct" v-if="segHealth[seg.segKey]">{{ segHealth[seg.segKey].avg }}<em>%</em></span>
            </button>
          </div>
        </div>
      </nav>
    </el-aside>

    <el-main class="app-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.app-shell {
  height: 100vh;
}

/* 侧栏：白底 + 1px 分隔线，不再花哨灰底 */
.app-aside {
  background: var(--bg-1);
  border-right: 1px solid var(--border-1);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

/* 品牌区：克制，不用大字号 + 渐变 */
.brand {
  padding: var(--space-5) var(--space-4);
  border-bottom: 1px solid var(--border-1);
}
.brand-mark {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  letter-spacing: 0.5px;
}
.brand-sub {
  font-size: var(--text-xs);
  color: var(--text-3);
  margin-top: 2px;
}

/* 导航：分组 + 紧凑 */
.nav {
  flex: 1;
  padding: var(--space-3) var(--space-2);
}
.nav-group {
  margin-bottom: var(--space-4);
}
.nav-group:last-child {
  margin-bottom: 0;
}
.nav-title {
  font-size: 11px;
  color: var(--text-4);
  font-weight: var(--weight-medium);
  text-transform: uppercase;
  letter-spacing: 0.8px;
  padding: 0 var(--space-2) var(--space-2);
}

/* 导航项：默认中性灰，hover 中性灰底，active 主色 */
.nav-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 7px var(--space-3);
  border-radius: var(--radius);
  color: var(--text-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-normal);
  margin-bottom: 1px;
  transition: background var(--transition), color var(--transition);
}
.nav-item:hover {
  background: var(--bg-hover);
  color: var(--text-1);
  text-decoration: none;
}
.nav-item.active {
  background: var(--color-primary-bg);
  color: var(--color-primary);
  font-weight: var(--weight-medium);
}
.nav-icon {
  font-size: var(--text-lg);
  flex-shrink: 0;
}

/* ── 工艺段子项 ──
   ★ 视觉上必须比 .nav-item 低一档（缩进 + 更小的字号 + 无图标条），
     否则它会被读成"和智能工厂并列的第 2~5 个菜单项"。 */
.nav-segs {
  padding: 2px 0 4px 30px;
}
.nav-seg {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 4px 6px;
  border: 0;
  border-radius: var(--radius);
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-size: var(--text-xs);
  color: var(--text-2);
  transition: background var(--transition), color var(--transition);
}
.nav-seg:hover {
  background: var(--bg-hover);
  color: var(--text-1);
}
.nav-seg.is-focus {
  background: var(--color-primary-bg);
  color: var(--color-primary);
}
.seg-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  flex-shrink: 0;
}
.seg-name {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.seg-pct {
  font-variant-numeric: tabular-nums;
  color: var(--text-3);
  flex-shrink: 0;
}
.seg-pct em {
  font-style: normal;
  font-size: 9px;
  margin-left: 1px;
}
.nav-seg.is-bad .seg-pct { color: var(--color-danger); }
.nav-seg.is-warn .seg-pct { color: var(--color-warning); }

/* 主区：灰底，内容白卡浮于其上 */
.app-main {
  background: var(--bg-2);
  padding: var(--space-5);
  overflow-y: auto;
}
</style>
