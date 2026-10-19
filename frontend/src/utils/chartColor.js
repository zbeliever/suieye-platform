/**
 * ECharts 颜色解析 —— canvas 不认识 CSS 变量
 *
 * ★★ 2026-10-19 老板报「光标附在曲线上时曲线不显示」，实测根因在这里：
 *
 *   ECharts 用 canvas 渲染，而 canvas 的 strokeStyle/fillStyle 只接受真色值。
 *   把 'var(--color-primary)' 直接传进去是**非法颜色**，浏览器不报错、静默忽略：
 *
 *     · 平时：strokeStyle 保留上一次的值 → 整条线被画成纯黑 (0,0,0)，
 *             而设计色是 #165dff。截图像素统计：蓝色 0 个，深色 8229 个。
 *     · hover 重绘：回退也失效，曲线大片消失
 *             （实测深色像素 4527 → 2419，少 46.6%）
 *
 *   图例是 DOM（CSS 变量有效）所以显示蓝色，图里的线是黑的 —— 这就是
 *   「图例说蓝、曲线是黑」的来源。
 *
 * ★ 用法：option 里**继续写** var(--x)（保持颜色单一真源 = tokens.css），
 *   但在交给 ECharts 之前用 cssVar() 解析一次。或者直接调用 setChartOption()。
 *
 *   lineStyle: { color: cssVar('--color-primary') }      // 单值
 *   setChartOption(chart, option)                        // 整棵 option 树
 */

const cache = new Map()

/** 读一个设计令牌的真色值（带缓存；主题切换后调 resetCssVarCache()） */
export function cssVar(name, fallback = '#000000') {
  const key = name.startsWith('--') ? name : `--${name}`
  if (cache.has(key)) return cache.get(key)
  let v = ''
  try {
    v = getComputedStyle(document.documentElement).getPropertyValue(key).trim()
  } catch {
    v = ''
  }
  const out = v || fallback
  // 只在拿到真值时缓存，避免首帧早于样式加载时把 fallback 固化
  if (v) cache.set(key, out)
  return out
}

export function resetCssVarCache() {
  cache.clear()
}

const VAR_RE = /^var\(\s*(--[a-z0-9-]+)\s*(?:,\s*(.+))?\)$/i

/** 深遍历，把字符串 'var(--x)' 解析成真色值。返回新对象，不修改入参。 */
export function resolveCssVars(value) {
  if (typeof value === 'string') {
    const m = VAR_RE.exec(value.trim())
    if (!m) return value
    return cssVar(m[1], (m[2] || '').trim() || undefined)
  }
  if (Array.isArray(value)) return value.map(resolveCssVars)
  if (value && typeof value === 'object') {
    // ECharts 的类实例（LinearGradient / RadialGradient / 日期对象等）原样透传，
    // 重新构造会丢方法
    const proto = Object.getPrototypeOf(value)
    if (proto !== Object.prototype && proto !== null) return value
    const out = {}
    for (const k in value) out[k] = resolveCssVars(value[k])
    return out
  }
  return value
}

/** 推荐入口：先解析再 setOption */
export function setChartOption(chart, option, opts) {
  if (!chart) return
  chart.setOption(resolveCssVars(option), opts)
}
