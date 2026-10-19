<template>
  <!-- ════════════════════════════════════════════════════════════════
       全站唯一的品牌区（2026-10-19 统一）

       ★ 为什么要有这个组件：
         统一之前有两套完全不同的品牌区 ——
           · 门面（/）：.fh-mark 渐变斜纹方块 28px + 17px/700 标题
                        + 「SuiEye」单独放大到 19px/800 + 11px 副标题堆叠
           · 直播墙（/livewall）：BrandMark 六边形瞳孔 34px + 21px/600 标题
                        + 12.5px 副标题横排带竖线
         结果就是老板看到的「系统图标不一致，字体风格也不一致」：
         同一句品牌名出现 800 与 600 两种字重、方块与六边形两种标记。

       ★ 规矩：
         图标、字号、字重、字距、颜色、间距**只在这里定义一次**。
         版式（横排 / 堆叠）由视口宽度决定，不由页面决定 ——
         任何页面都不许再自己写一套品牌区。
       ════════════════════════════════════════════════════════════════ -->
  <div class="brand-block">
    <BrandMark class="bb-mark" />
    <div class="bb-text">
      <h1 class="bb-title">SuiEye 淬眸</h1>
      <span class="bb-sub">铅锌浮选全流程智能视觉分析平台</span>
    </div>
  </div>
</template>

<script setup>
import BrandMark from './BrandMark.vue'
</script>

<style scoped>
.brand-block {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

/* 图标：与标题同高的一档（32px）。用 currentColor 继承主色，
   不写死 hex，也不做渐变 —— 渐变在小尺寸下会糊成一团色块。 */
.bb-mark {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  color: var(--color-primary);
}

.bb-text {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}

/* 品牌名：18px/600 —— 落在 --text-xl 阶梯上。
   ★ 不大于 KPI 数值（21px/700）：一屏只有一个主角，品牌是标识不是数据。
   ★ 不拆「SuiEye」单独加粗：那是"字体风格不一致"的直接来源。 */
.bb-title {
  margin: 0;
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
  letter-spacing: 0.2px;
  color: var(--text-1);
  white-space: nowrap;
}

/* 副标题：12px（--text-xs）落在阶梯上，不再用 11 / 12.5 这种编外字号 */
.bb-sub {
  font-size: var(--text-xs);
  line-height: var(--leading-tight);
  color: var(--text-3);
  white-space: nowrap;
  border-left: 1px solid var(--border-1);
  padding-left: 12px;
}

/* 窄屏（≤1279）：门面页头要同时容纳 4 格 KPI + CTA，
   横排品牌会把 KPI 压到溢出 → 副标题落到下一行并去掉竖线。
   ★ 这是版式自适应，不是风格分叉：字号/字重/图标一律不变。 */
@media (max-width: 1279px) {
  .bb-text {
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
  }
  .bb-sub {
    border-left: none;
    padding-left: 0;
  }
}
</style>
