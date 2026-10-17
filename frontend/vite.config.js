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
    },
  },
})
