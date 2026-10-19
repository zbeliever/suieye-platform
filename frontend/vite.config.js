import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5188,
    // ★ 端口被占时直接报错，不要静默挪到 5189/5190（否则用户浏览器地址栏还是 5188 → 看到旧页面/白页）
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8300',
        changeOrigin: true,
      },
      // ★★ 2026-10-19 补：以前只代理了 /api，**没代理 /ws** ——
      //   前端 new WebSocket('ws://127.0.0.1:5188/ws/alarms') 直接连到 vite，
      //   vite 没有这个路由 → 连接失败 → 自动重连 → 再失败，无限循环。
      //   结果是整条实时通道从来没工作过：告警页一直靠 30 秒轮询，
      //   水质页的 WS 更新也一直是空转，而代码看起来"都写好了"。
      //   `ws: true` 必填，否则 vite 只转发普通 HTTP 请求，不升级 WebSocket。
      '/ws': {
        target: 'ws://127.0.0.1:8300',
        ws: true,
        changeOrigin: true,
      },
    },
  },
})
