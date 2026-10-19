/**
 * 全站导航唯一真源（Single Source of Truth）
 *
 * ★★ 为什么必须有这个文件：
 *   2026-10-19 之前，同一批功能被三处各写一遍 ——
 *     ① `App.vue` 的全局侧栏（仪表盘 / 告警 / 报表 / 设置 / 时光机 / 分析工作台）
 *     ② `Equipment3D.vue` 的「厂区导览」（生产总览 / 告警中心 / 报表统计 / 系统设置 / 工况时光机 / 浮选工作台）
 *     ③ 各页面自己的 `<h2>`（告警中心 / 报表中心 / 工况时光机 …）
 *   结果是**同一个页面有三种叫法**，用户每跳一次页标题就换一次名字，
 *   这是"这些页面不像一个系统"的最大来源。
 *
 *   现在：名称、路径、图标、分组**只在这里定义一次**，
 *   侧栏、页面标题、面包屑全部引用它（`titleOf(path)`）。
 *   改名字只需改这一处。
 *
 * 命名约定（老板 2026-10-19 拍板：**简短名优先**）：
 *   · 2~4 个字，侧栏 220px 放得下
 *   · 不带"中心/统计/系统/工况"这类冗余后缀
 *   · 与页面 `<h2>` 完全一致，不留落差
 */

export const NAV_ITEMS = {
  // ── 总览 ───────────────────────────────────────
  factory:   { path: '/',                     name: '智能工厂',  icon: 'OfficeBuilding',    group: 'overview' },
  livewall:  { path: '/livewall',             name: '直播墙',    icon: 'VideoCamera',       group: 'overview' },
  monitor:   { path: '/monitor',              name: '实时监控',  icon: 'VideoCameraFilled', group: 'overview' },

  // ── 生产 ───────────────────────────────────────
  dashboard: { path: '/dashboard',            name: '仪表盘',    icon: 'DataLine',          group: 'prod' },
  alarms:    { path: '/alarms',               name: '告警',      icon: 'Bell',              group: 'prod' },
  reports:   { path: '/reports',              name: '报表',      icon: 'Document',          group: 'prod' },

  // ── 质量与资产 ─────────────────────────────────
  workbench: { path: '/workbench/flotation',  name: '分析工作台', icon: 'MagicStick',       group: 'quality' },
  safety:    { path: '/safety',               name: '安全巡检',  icon: 'Aim',               group: 'quality' },
  water:     { path: '/water',                name: '水质监测',  icon: 'Drizzling',         group: 'quality' },
  tailings:  { path: '/tailings',             name: '尾矿处理',  icon: 'Coordinate',        group: 'quality' },
  dosing:    { path: '/dosing',               name: '配药助手',  icon: 'FirstAidKit',       group: 'quality' },
  timemachine:{ path: '/timemachine',         name: '时光机',    icon: 'Search',            group: 'quality' },

  // ── 系统 ───────────────────────────────────────
  settings:  { path: '/settings',             name: '设置',      icon: 'Setting',           group: 'system' },
}

/**
 * 侧栏分组顺序。
 * ★ 分组依据是**使用场景**，不是组织架构：
 *   "运营 vs 资产"这条边界没人说得清（告警算运营还是资产？），
 *   改成"总览 / 生产 / 质量与资产 / 系统"后，每一项都能一眼归位。
 */
export const NAV_GROUPS = [
  { key: 'overview', title: '总览',       expandable: true },
  { key: 'prod',     title: '生产' },
  { key: 'quality',  title: '质量与资产' },
  { key: 'system',   title: '系统' },
]

/** 分组 → 条目（按 NAV_ITEMS 的声明顺序） */
export function itemsOfGroup(groupKey) {
  return Object.values(NAV_ITEMS).filter((it) => it.group === groupKey)
}

/**
 * 路径 → 显示名（面包屑 / 页面标题 / tooltip 都走这里）
 * @param {string} path 当前路由 path
 * @returns {string} 未登记的路径返回空串（调用方自行兜底）
 */
export function titleOf(path) {
  if (!path) return ''
  // 工作台是一族路由（/workbench/:sceneId），统一归到 flotation 那条
  if (path.startsWith('/workbench')) return NAV_ITEMS.workbench.name
  // /equipment 已重定向到首页
  if (path === '/equipment') return NAV_ITEMS.factory.name
  const hit = Object.values(NAV_ITEMS).find((it) => it.path === path)
  return hit ? hit.name : ''
}

/**
 * 工作台族路由的**入口名** —— 给"同时列多个工作台入口"的地方用（如厂区导览的化验室）。
 *
 * ★ 为什么不直接用 titleOf：`/workbench/safety` 和 `/workbench/flotation` 都返回
 *   「分析工作台」，同一张卡里并排出现两条同名入口，观众会以为是重复项
 *   （2026-10-19 实测：化验室里「分析工作台」印了两遍）。
 *   这里按场景补一个限定词：浮选 → 浮选分析，安全 → 安全分析。
 *   ⚠️ 这只是**入口消歧**，不是新造一个全站页面名 —— 页面标题仍旧叫「分析工作台」。
 */
const WORKBENCH_SCENE_LABEL = {
  flotation: '浮选分析',
  grinding: '磨矿分析',
  safety: '安全分析',
}

export function workbenchEntryName(path) {
  const m = /^\/workbench\/([\w-]+)/.exec(path || '')
  if (!m) return titleOf(path)
  return WORKBENCH_SCENE_LABEL[m[1]] || NAV_ITEMS.workbench.name
}

/**
 * 工艺段子项（挂在「智能工厂」下）。
 *
 * ★ 为什么不复用 Equipment3D.vue 里的 PROCESS_SEGMENTS：
 *   那份定义里还带着相机机位、设备 id 列表等 3D 专属字段，侧栏并不需要；
 *   而侧栏需要的是"全部 4 段 + 简短名 + 该进哪个功能页"。
 *   两边共享的是 `segKey`，Equipment3D 启动时按 segKey 接收侧栏的聚焦请求。
 *
 * 这里的 route 是"该段的主功能页"——侧栏子项**点击只做聚焦，不跳转**，
 * route 仅用于 tooltip：「浮选段 · 实时监控」。
 */
export const NAV_SEGMENTS = [
  { segKey: 'grinding',   name: '磨矿段',     route: '/workbench/grinding', color: '#165dff' },
  { segKey: 'flotation',  name: '浮选段',     route: '/monitor',            color: '#00b42a' },
  { segKey: 'concentrate',name: '精矿输送',   route: '/safety',             color: '#ff7d00' },
  { segKey: 'tailings',   name: '尾矿与回水', route: '/tailings',           color: '#722ed1' },
]

/** 通过 query 把"要聚焦哪一段"传给 3D 页（App.vue 点侧栏子项时用） */
export const SEG_QUERY_KEY = 'seg'
