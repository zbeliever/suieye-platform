<script setup>
/**
 * Panel —— 全站统一的内容卡。
 *
 * ★ 整治前各页直接写 el-card / section.panel / div.card，
 *   圆角 6/8、边框有无、阴影有无、内边距 16/20/24 全不一致。
 *   现在统一为：白底 + 1px 边框 + 圆角 8 + 无阴影 + 内边距 14/18。
 *
 * ★ 为什么不用 el-card：
 *   el-card 自带 header/body 两段结构与自己的边框阴影，覆盖起来要写一堆
 *   :deep(.el-card__header)；而它并没有提供我们需要的"副标题 + 右侧动作区"。
 *   自建 30 行组件比长期跟 Element Plus 的默认样式打架更划算。
 *
 * 用法：
 *   <Panel title="系统信息">
 *     <template #actions><el-button link>刷新</el-button></template>
 *     ...内容...
 *   </Panel>
 */
defineProps({
  title: { type: String, default: '' },
  sub: { type: String, default: '' },
  /** 内容区去内边距（表格/图铺满用） */
  flush: { type: Boolean, default: false },
})
</script>

<template>
  <section class="panel">
    <header v-if="title || $slots.actions" class="panel-head">
      <h3 class="panel-title">{{ title }}</h3>
      <span v-if="sub" class="panel-sub">{{ sub }}</span>
      <div class="panel-actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="panel-body" :class="{ 'is-flush': flush }">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--bg-1);
  border: 1px solid var(--border-1);
  border-radius: var(--panel-radius);
  /* 不用阴影：门面/表格页叠阴影会让浅色界面变脏 */
  box-shadow: none;
}

.panel-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: var(--panel-pad-y) var(--panel-pad-x) 0;
  flex: none;
}
.panel-title {
  margin: 0;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
  line-height: 1.4;
}
.panel-sub {
  font-size: var(--text-xs);
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.panel-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.panel-body {
  padding: var(--panel-pad-y) var(--panel-pad-x);
  min-height: 0;
}
.panel-body.is-flush {
  padding: 0;
}
</style>
