<script setup>
import { onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { ensureAlarmNotifications } from './api/ws'

const route = useRoute()

// 导航分组（2026-10-17 重排：3D 作首页后，"设备监测"变"智能工厂总览"置顶）
const navGroups = [
  {
    title: '运营',
    items: [
      { path: '/', label: '智能工厂', icon: 'OfficeBuilding' },
      { path: '/livewall', label: '直播墙', icon: 'VideoCamera' },
      { path: '/dashboard', label: '仪表盘', icon: 'DataLine' },
      { path: '/timemachine', label: '时光机', icon: 'Search' },
      { path: '/monitor', label: '实时监控', icon: 'VideoCameraFilled' },
      { path: '/workbench/flotation', label: '分析工作台', icon: 'MagicStick' },
    ],
  },
  {
    title: '资产',
    items: [
      { path: '/safety', label: '安全巡检', icon: 'Aim' },
      { path: '/water', label: '水质监测', icon: 'Drizzling' },
      { path: '/tailings', label: '尾矿处理', icon: 'Coordinate' },
      { path: '/alarms', label: '告警', icon: 'Bell' },
      { path: '/reports', label: '报表', icon: 'Document' },
      { path: '/dosing', label: '配药助手', icon: 'FirstAidKit' },
    ],
  },
  {
    title: '系统',
    items: [{ path: '/settings', label: '设置', icon: 'Setting' }],
  },
]

const currentPath = computed(() => {
  // 工作台高亮匹配前缀
  if (route.path.startsWith('/workbench')) return '/workbench/flotation'
  // /equipment 已重定向到首页，高亮也跟着走
  if (route.path === '/equipment') return '/'
  return route.path
})

// 启动全局 WS 告警通知
onMounted(() => {
  ensureAlarmNotifications()
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
        <div v-for="group in navGroups" :key="group.title" class="nav-group">
          <div class="nav-title">{{ group.title }}</div>
          <router-link
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            class="nav-item"
            :class="{ active: currentPath === item.path }"
          >
            <el-icon class="nav-icon"><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </router-link>
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

/* 主区：灰底，内容白卡浮于其上 */
.app-main {
  background: var(--bg-2);
  padding: var(--space-5);
  overflow-y: auto;
}
</style>
