import { ref, onUnmounted, getCurrentInstance } from 'vue'
import { ElNotification } from 'element-plus'

// WebSocket 全局管理 — 跨页面复用
const channels = {}  // { alarms: { ws, listeners: Set, lastMsg } }

function getWsUrl(channel) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/ws/${channel}`
}

/**
 * 订阅 WebSocket 频道
 * @param {string} channel - alarms / safety / equipment
 * @param {(msg: any) => void} handler - 消息处理函数
 * @returns {() => void} unsubscribe
 */
export function useWebSocket(channel, handler) {
  if (!channels[channel]) {
    channels[channel] = { ws: null, listeners: new Set(), status: ref('disconnected'), reconnectTimer: null }
  }
  const ch = channels[channel]

  function connect() {
    if (ch.ws && ch.ws.readyState <= 1) return  // 已连接或连接中
    try {
      ch.ws = new WebSocket(getWsUrl(channel))
    } catch (e) {
      console.warn('[ws] connect failed', e)
      scheduleReconnect()
      return
    }
    ch.ws.onopen = () => {
      ch.status.value = 'connected'
      console.log(`[ws] ${channel} connected`)
    }
    ch.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        ch.listeners.forEach(fn => fn(msg))
      } catch (e) {
        console.warn('[ws] parse error', e)
      }
    }
    ch.ws.onclose = () => {
      ch.status.value = 'disconnected'
      console.log(`[ws] ${channel} closed, reconnect in 3s`)
      scheduleReconnect()
    }
    ch.ws.onerror = (e) => {
      console.warn(`[ws] ${channel} error`, e)
    }
  }

  function scheduleReconnect() {
    if (ch.reconnectTimer) clearTimeout(ch.reconnectTimer)
    ch.reconnectTimer = setTimeout(() => connect(), 3000)
  }

  // 注册 handler
  ch.listeners.add(handler)
  // 尝试连接
  connect()

  // 卸载时移除 handler
  // ⚠️ onUnmounted 必须在**组件 setup 的同步阶段**调用。若调用方把它写在
  //    `onMounted(async () => { await ...; useWebSocket(...) })` 里，await 之后
  //    已经没有活跃组件实例 → Vue 报 "onUnmounted is called when there is no active
  //    component instance"，而且 handler **永远不会被移除**（已卸载组件继续收推送 = 泄漏）。
  //    这里用 getCurrentInstance 兜底：不在 setup 内就不注册，并明确提示调用方改位置。
  const inst = getCurrentInstance()
  if (inst) {
    onUnmounted(() => {
      ch.listeners.delete(handler)
    })
  } else {
    console.warn(
      `[ws] useWebSocket('${channel}') 不在组件 setup 内调用 —— handler 不会随组件卸载清理。` +
      '请把它移到 setup 顶层（不要在 await 之后调用）。'
    )
  }

  return {
    status: ch.status,
    unsubscribe: () => ch.listeners.delete(handler),
    send: (data) => {
      if (ch.ws && ch.ws.readyState === 1) ch.ws.send(JSON.stringify(data))
    },
  }
}

/**
 * 告警推送 → 全局通知（适合在 App.vue 中调用一次）
 *
 * ★ 2026-10-19 事件格式统一为 `{ event, ts, data }`：
 *   旧版读 `msg.type === 'alarm'` 且字段拍平在顶层，那是**假告警**的格式。
 *   现在只认真事件，且能区分"谁报的"（source/origin）。
 * @param {Object} msg - { event: 'alarm.created', ts, data: { level, rule_name, message, origin, source, demo } }
 */
export function setupAlarmNotifications() {
  const { status } = useWebSocket('alarms', (msg) => {
    if (msg.event !== 'alarm.created') return
    const d = msg.data || {}

    // 已经在告警页就不弹：列表自己会实时刷新，再弹一次纯属噪音
    if (location.pathname.startsWith('/alarms')) return

    // 外部系统推来的告警标出来源 —— 不然分不清是 SuiEye 算的还是别的系统报的
    const tag = d.source === 'external' ? `[${d.origin || '外部系统'}] ` : ''
    const isDemo = !!d.demo

    ElNotification({
      title: isDemo ? '演示告警（非真实）' : (d.level === 'danger' ? '严重告警' : '预警'),
      message: `${tag}${d.rule_name || ''}${d.message ? '：' + d.message : ''}`,
      type: d.level === 'danger' ? 'error' : 'warning',
      duration: isDemo ? 3000 : 6000,
      position: 'top-right',
    })
  })
  return { status }
}

/**
 * 全局 WS 实时推送通知 — 在 App.vue 中调用一次，覆盖告警通知中心
 */
let _alarmSetup = false
export function ensureAlarmNotifications() {
  if (_alarmSetup) return
  _alarmSetup = true
  setupAlarmNotifications()
}