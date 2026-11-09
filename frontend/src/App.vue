<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
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

/**
 * 2026-10-25：主区变成滚动容器后新增 —— 换页必须把滚动位置归零。
 *
 * 为什么必须有：
 *   滚动条现在挂在 .app-main 上（不是 window），浏览器不会像正常翻页那样自动回到顶部。
 *   不处理的话，在「设置」滑到底再点进「报表」，报表会直接以"已经滚到一半"的样子打开，
 *   观众第一眼看到的是一段没有页头的中间内容 —— 比原来不能滚更迷惑。
 */
const mainEl = ref(null)
const route = useRoute()
watch(
  () => route.fullPath,
  () => {
    const el = mainEl.value?.$el || mainEl.value
    if (el && typeof el.scrollTop === 'number') el.scrollTop = 0
  }
)
</script>

<template>
  <el-container class="app-shell">
    <el-main ref="mainEl" class="app-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.app-shell {
  height: 100vh;
}

/* 无侧栏后主区直接铺满；内边距交给各页自己控制（3D 页要贴边）
   ★ 2026-10-25：overflow hidden → auto。原来的 hidden 把"内容比视口高"的页面
     直接裁掉且不可滚（设置 1445 / 报表 1322 / 水位 1618 @768 高），
     老板看到的就是"各页不能下滑，展示不全"。
     现在主区是唯一的滚动容器：内容装得下 → 不出现滚动条（大屏页仍是满屏）；
     装不下 → 整页纵向滚动。横向继续 hidden，避免出现左右抖动的双滚动条。 */
.app-main {
  padding: 0;
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
}
</style>
