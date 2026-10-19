<script setup>
/**
 * PageShell —— 全站统一的页面骨架（2026-10-19 一致性整治 P0）。
 *
 * ★ 为什么要有它：
 *   整治前的 12 个功能页有 8 种页头写法（page-header / page-head / rp-header /
 *   ds-header / mn-top …），标题字号 18/20/22 混用，动作区有的在右上、有的没有，
 *   页面外边距 16/20/24/32 随意取。观众每跳一页就换一种"系统感"。
 *
 * ★ 用法（管理型页面）：
 *     <PageShell title="设置" sub="配置均来自真实数据源">
 *       <template #kpi> 4 个 <StatCard/> </template>
 *       <template #actions> 右侧动作按钮 </template>
 *       ...内容...
 *     </PageShell>
 *
 * ★ KPI 两种排布（不是两套样式，是两种版式原型的差别）：
 *   · kpi="block"（默认，管理型）：页头下方独立一行 4 格卡片，88px 高
 *   · kpi="inline"（分析台型）：塞进页头右侧横排，不占垂直空间
 *     —— 实时监控这类页面主体是画面，多 88px 就等于少 88px 的画面
 *
 * ★ 返回入口由本组件统一渲染（BackToFactory），页面不要再自己写一遍。
 */
import BackToFactory from './BackToFactory.vue'

defineProps({
  title: { type: String, required: true },
  sub: { type: String, default: '' },
  /** block = 页头下方 4 格卡片；inline = 页头右侧横排；none = 无 KPI */
  kpi: { type: String, default: 'block' },
  /** 内容区贴边（大屏页用，去掉页面留白） */
  flush: { type: Boolean, default: false },
})
</script>

<template>
  <div class="page-shell" :class="{ 'is-flush': flush }">
    <header class="page-head">
      <BackToFactory class="page-back" />

      <div class="ph-main">
        <h1 class="ph-title">{{ title }}</h1>
        <span v-if="sub" class="ph-sub">{{ sub }}</span>
        <slot name="meta" />
      </div>

      <div v-if="kpi === 'inline'" class="ph-kpi">
        <slot name="kpi" />
      </div>

      <div class="ph-actions">
        <slot name="actions" />
      </div>
    </header>

    <div v-if="kpi === 'block'" class="page-kpi">
      <slot name="kpi" />
    </div>

    <div class="page-body">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.page-shell {
  display: flex;
  flex-direction: column;
  gap: var(--header-gap);
  height: 100%;
  min-height: 0;
  padding: var(--page-pad-y) var(--page-pad-x);
  box-sizing: border-box;
}
.page-shell.is-flush {
  padding: 0;
}

/* ── 页头：56px 固定高度，底部一条分隔线 ── */
.page-head {
  display: flex;
  align-items: center;
  gap: 12px;
  height: var(--header-h);
  flex: none;
  border-bottom: 1px solid var(--border-1);
}
/* BackToFactory 自带 margin-bottom:12px（给"独立一行"的页面用），
   进了页头这一行必须清零，否则整行会被顶高。 */
.page-head :deep(.page-back) {
  margin-bottom: 0;
}

.ph-main {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}
.ph-title {
  margin: 0;
  font-size: var(--text-page-title);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  line-height: 1.2;
  white-space: nowrap;
}
.ph-sub {
  font-size: var(--text-sm);
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ph-kpi {
  display: flex;
  align-items: center;
  gap: 26px;
  margin-left: 16px;
  min-width: 0;
}
.ph-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
}

/* ── KPI 行：四格等宽，不拉伸变形 ── */
.page-kpi {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  flex: none;
}

/* ── 内容区 ── */
.page-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

@media (max-width: 1200px) {
  .page-kpi {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
