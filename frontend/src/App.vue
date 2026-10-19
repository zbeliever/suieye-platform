<script setup>
import { onMounted } from 'vue'
import { ensureAlarmNotifications } from './api/ws'

/**
 * 应用外壳 —— 2026-10-19 老板拍板：**去掉左侧全局导航栏**。
 *
 * 为什么去掉：
 *   原来全站常驻一条 220px 竖栏（14 个功能入口），而首页「智能工厂」的 3D 厂区里
 *   已经有一套同构的入口（建筑 → 功能、工艺段 → 功能页、设备 → 详情）。
 *   两套入口并存 → 观众在演示时看到"同一个功能在左边出现两次"，不知道该点哪个。
 *   老板的定性：**3D 页就是首页和主页，它本身要做成其他功能页的入口。**
 *
 * 于是架构变成两条动线：
 *   ① 进入：智能工厂（/）→ 厂区内点建筑 / 点工艺段 → 功能页
 *   ② 返回：功能页页头统一「← 智能工厂」→ 回枢纽（组件 `components/BackToFactory.vue`）
 *
 * ★ 导航数据仍保留在 `utils/navMap.js`：它现在只服务于
 *   "标题同源"（titleOf）、"面包屑/返回指向"，不再渲染成侧栏。
 *   ❌ 不要因为看不到侧栏就把 navMap 删掉 —— 名称唯一真源还在里面。
 */
onMounted(() => {
  // 全局 WS 告警通知（与侧栏无关，是事件订阅，必须留在外壳）
  ensureAlarmNotifications()
})
</script>

<template>
  <el-container class="app-shell">
    <el-main class="app-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.app-shell {
  height: 100vh;
}

/* 无侧栏后主区直接铺满；内边距交给各页自己控制（3D 页要贴边） */
.app-main {
  padding: 0;
  height: 100%;
  overflow: hidden;
}
</style>
