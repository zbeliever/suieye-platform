<script setup>
/**
 * 功能页统一返回入口 —— 回到「智能工厂」（3D 厂区首页）。
 *
 * 为什么需要它：
 *   2026-10-19 去掉全局左侧导航栏后，10 个功能页失去了回首页的入口。
 *   演示时"点进告警页之后怎么回去"是必被问到的问题，所以每页页头都要有一步到位的返回。
 *
 * 为什么抽成组件：
 *   12 个页面的页头结构各不相同（page-header / page-head / rp-header / ds-header …），
 *   如果每页各写一遍 el-page-header，字号/间距/回退路径会漂移成 12 个样子。
 *   统一成一个组件才能保证"演示动线"在任何一页看起来都是同一个动作。
 */
import { useRouter } from 'vue-router'

const props = defineProps({
  /** 返回目标文案（默认就是首页） */
  label: { type: String, default: '智能工厂' },
  /** 极简模式：只显示箭头+文字，不带左侧分隔（贴在大屏页头里用） */
  compact: { type: Boolean, default: false },
})

const router = useRouter()

function goHome() {
  // 已在首页时什么都不做，避免 push 出重复历史条目
  if (router.currentRoute.value.path === '/') return
  router.push('/')
}
</script>

<template>
  <button
    class="back-factory"
    :class="{ 'is-compact': compact }"
    type="button"
    :title="`返回${label}（3D 厂区）`"
    @click="goHome"
  >
    <svg class="bf-arrow" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path
        d="M9.5 3.5 5 8l4.5 4.5"
        fill="none"
        stroke="currentColor"
        stroke-width="1.7"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
    <span class="bf-text">{{ label }}</span>
  </button>
</template>

<style scoped>
.back-factory {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 10px 0 7px;
  border: 1px solid var(--border-1);
  border-radius: var(--radius-sm);
  background: var(--bg-1);
  color: var(--text-2);
  font-size: var(--text-sm);
  font-family: inherit;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
  white-space: nowrap;
  flex-shrink: 0;
  /* ★ 必须 align-self:flex-start：好几个页面的根节点是 `display:flex; flex-direction:column`，
     inline-flex 的按钮作为 flex 子项会在交叉轴上被拉满整行（实测变成 1440px 宽的"隐形大按钮"，
     点页面顶部任意位置都会跳回首页）。这一行让它保持自身宽度。 */
  align-self: flex-start;
  /* ★ 默认带上"页头顶部一行"的间距：12 个页面都要这一行，
     每页各写一遍 margin 迟早会漂移成 12 种间距。页面需要时用 class 覆盖即可。 */
  margin-bottom: 12px;
}
/* hover 只做加深/位移，不改尺寸（老板的交互偏好） */
.back-factory:hover {
  color: var(--color-primary);
  border-color: var(--color-primary);
  background: var(--color-primary-bg);
}
.back-factory:hover .bf-arrow {
  transform: translateX(-1px);
}
.bf-arrow {
  transition: transform 0.15s;
  flex-shrink: 0;
}
.bf-text {
  line-height: 1;
}
/* 极简模式：无边框，像页头的面包屑，不与主按钮抢视觉 */
.back-factory.is-compact {
  padding: 0 6px 0 2px;
  border-color: transparent;
  background: transparent;
  margin-bottom: 0;   /* 贴在大屏指挥条里，不要额外占行高 */
}
.back-factory.is-compact:hover {
  border-color: transparent;
  background: var(--color-primary-bg);
}
</style>
