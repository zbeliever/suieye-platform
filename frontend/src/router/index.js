import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    // ★ 2026-10-19：首页从「设备监测中心」升格为**全站门面**（数字孪生导览屏）。
    //   它不再只是一张设备图，而是：品牌页头 + 全厂 KPI + 3D 厂区 + 功能入口。
    //   组件名仍是 Equipment3D.vue（内部 3D 逻辑全部复用），但页面定位已经变了。
    path: '/',
    name: 'home3d',
    component: () => import('../views/Equipment3D.vue'),
    meta: { title: '淬眸 SuiEye · 铅锌浮选数字孪生' },
  },
  {
    path: '/livewall',
    name: 'livewall',
    component: () => import('../views/LiveWall.vue'),
    meta: { title: '直播墙' },
  },
  {
    path: '/workbench/:sceneId',
    name: 'workbench',
    component: () => import('../views/Workbench.vue'),
    meta: { title: '工位工作台' },
  },
  {
    path: '/timemachine',
    name: 'timemachine',
    component: () => import('../views/TimeMachine.vue'),
    meta: { title: '工况时光机' },
  },
  {
    path: '/monitor',
    name: 'monitor',
    component: () => import('../views/Monitor.vue'),
    meta: { title: '实时监控' },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('../views/Dashboard.vue'),
    meta: { title: '仪表盘' },
  },
  {
    path: '/reports',
    name: 'reports',
    component: () => import('../views/Reports.vue'),
    meta: { title: '报表中心' },
  },
  {
    path: '/alarms',
    name: 'alarms',
    component: () => import('../views/Alarms.vue'),
    meta: { title: '告警中心' },
  },
  {
    path: '/safety',
    name: 'safety',
    component: () => import('../views/SafetyCenter.vue'),
    meta: { title: '安全巡检中心' },
  },
  {
    path: '/water',
    name: 'water',
    component: () => import('../views/WaterMonitor.vue'),
    meta: { title: '脱水工段水质监测' },
  },
  {
    path: '/tailings',
    name: 'tailings',
    component: () => import('../views/TailingsMonitor.vue'),
    meta: { title: '尾矿处理监控' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('../views/Settings.vue'),
    meta: { title: '系统设置' },
  },
  {
    // 旧链接兼容：/equipment 曾是 3D 页独立地址，现在它就是首页
    path: '/equipment',
    redirect: '/',
  },
  {
    path: '/dosing',
    name: 'dosing',
    component: () => import('../views/DosingAssistant.vue'),
    meta: { title: '智能配药助手' },
  },
  // 兜底：未匹配路径（含已下线的 /models）一律回首页，避免白屏
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · 淬眸 SuiEye` : '淬眸 SuiEye'
})

export default router
