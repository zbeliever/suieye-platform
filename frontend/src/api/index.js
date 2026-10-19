import axios from 'axios'
import { ElMessage, ElNotification } from 'element-plus'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// 请求计数器（用于 loading 管理）
let pendingRequests = 0

// 网络错误提示节流：同一文案 5 秒内只弹一次（并发请求不会连弹多条）
const NET_ERR_THROTTLE_MS = 5000
let lastNetErrAt = 0
let lastNetErrMsg = ''

api.interceptors.request.use(
  (config) => {
    pendingRequests++
    return config
  },
  (err) => {
    return Promise.reject(err)
  },
)

api.interceptors.response.use(
  (res) => {
    pendingRequests--
    // 保持原有解包行为（res.data）
    return res.data
  },
  (err) => {
    pendingRequests--

    // 网络错误 / 超时 / 后端未启动
    //
    // ★★ 必须节流去重（2026-10-19 门面化后）：
    //   门面页一进来就并发打 /scenes/flotation/stats、/scenes/flotation/records、
    //   /water/overview、/equipment 等四五个接口。后端没起时，
    //   上面每条都走一次 `!err.response` → **连弹 4~5 条一模一样的红条**，
    //   把整块屏幕糊住，观感是"页面崩了"。而真实信息只有一句：后端没起来。
    //   做法：同一个提示文案 5 秒内只弹一次；期间后续的同类错误只 reject，不弹。
    if (!err.response) {
      const msg = err.code === 'ECONNABORTED' ? '请求超时，请稍后重试' : '网络连接失败，请检查后端服务是否启动'
      const now = Date.now()
      if (now - lastNetErrAt > NET_ERR_THROTTLE_MS || lastNetErrMsg !== msg) {
        lastNetErrAt = now
        lastNetErrMsg = msg
        ElMessage({ type: 'error', message: msg, duration: 5000 })
      }
      return Promise.reject(err)
    }

    const { status, data } = err.response

    // 根据状态码分类处理
    switch (status) {
      case 400:
        ElMessage({ type: 'warning', message: data?.detail || '请求参数有误', duration: 3000 })
        break
      case 401:
        ElMessage({ type: 'warning', message: '登录已过期，请重新登录', duration: 3000 })
        break
      case 403:
        ElMessage({ type: 'error', message: '没有权限执行此操作', duration: 3000 })
        break
      case 404:
        // 静默处理 404，让调用方决定是否提示（避免干扰正常流程）
        console.warn('[API] 404:', err.config?.url, data?.detail || '')
        break
      case 422:
        // FastAPI 参数校验失败
        const details = data?.detail
        if (Array.isArray(details)) {
          const msgs = details.map((d) => d?.msg || JSON.stringify(d)).join('; ')
          ElMessage({ type: 'warning', message: `参数校验失败: ${msgs}`, duration: 4000 })
        } else {
          ElMessage({ type: 'warning', message: details || '请求格式不正确', duration: 3000 })
        }
        break
      case 500:
      case 502:
      case 503:
      case 504:
        ElNotification({
          type: 'error',
          title: `服务器错误 (${status})`,
          message: data?.detail || '服务器内部错误，请联系管理员',
          duration: 5000,
        })
        break
      default:
        ElMessage({ type: 'error', message: `请求失败 (${status})`, duration: 3000 })
    }

    return Promise.reject(err)
  },
)

export default api
export { pendingRequests }
