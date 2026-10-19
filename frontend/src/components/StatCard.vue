<script setup>
/**
 * StatCard —— 全站统一的指标格（KPI）。
 *
 * ★ 整治前的四种形态：仪表盘 6 张小卡带图标 / 告警 5 个裸数字靠竖线 /
 *   报表 4 张卡配四色图标 / 尾矿同组内 40px 与 20px 混排。
 *   现在只有这一种：label(12) + value(28/600) + foot(12)，高 88px。
 *
 * ★ variant：
 *   · card（默认）：白底+1px 边框+圆角 8，管理型页面用（PageShell kpi="block"）
 *   · inline：无底无框，横排塞进页头（PageShell kpi="inline"），
 *             仅供"主体是画面、垂直空间紧张"的分析台型页面 —— 样式仍是同一套令牌。
 *
 * ★ tone 只影响数值颜色，且只允许表达状态（success/warning/danger），
 *   不允许当装饰色 —— 这是 tokens.css 里定下的纪律。
 */
defineProps({
  label: { type: String, default: '' },
  value: { type: [String, Number], default: '—' },
  unit: { type: String, default: '' },
  foot: { type: String, default: '' },
  /** default | primary | success | warning | danger */
  tone: { type: String, default: 'default' },
  /** card | inline */
  variant: { type: String, default: 'card' },
})
</script>

<template>
  <div class="stat-card" :class="[`is-${tone}`, `is-${variant}`]">
    <span class="sc-label">{{ label }}</span>
    <span class="sc-value">
      {{ value }}<em v-if="unit">{{ unit }}</em>
    </span>
    <span v-if="foot || $slots.foot" class="sc-foot">
      <slot name="foot">{{ foot }}</slot>
    </span>
  </div>
</template>

<style scoped>
.stat-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 1px;
  height: var(--kpi-h);
  padding: 10px 16px;
  box-sizing: border-box;
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-radius: var(--panel-radius);
}

.sc-label {
  font-size: var(--kpi-label);
  color: var(--text-3);
  line-height: 1.4;
}
.sc-value {
  font-size: var(--kpi-val);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.sc-value em {
  font-style: normal;
  font-size: var(--text-sm);
  font-weight: var(--weight-normal);
  color: var(--text-3);
  margin-left: 3px;
}
.sc-foot {
  font-size: var(--text-xs);
  color: var(--text-3);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 状态色：只染色，不加底色/边框，避免"处处是彩色卡片" ── */
.stat-card.is-primary .sc-value { color: var(--color-primary); }
.stat-card.is-success .sc-value { color: var(--color-success); }
.stat-card.is-warning .sc-value { color: var(--color-warning); }
.stat-card.is-danger  .sc-value { color: var(--color-danger); }

/* ── inline：塞进页头，不占垂直空间 ── */
.stat-card.is-inline {
  height: auto;
  padding: 0;
  background: transparent;
  border: none;
  border-radius: 0;
  gap: 1px;
}
.stat-card.is-inline .sc-value {
  font-size: 19px;
  line-height: 1.15;
}
.stat-card.is-inline .sc-label {
  font-size: 11px;
}
</style>
