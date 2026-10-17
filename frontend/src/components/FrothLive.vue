<script setup>
/**
 * 浮选泡沫实时画面面板
 *
 * 素材：现场实拍泡沫 → 转码为 H.264 MP4（浏览器原生硬解）。
 * 素材特征（实测）：源 AVI 是 MJPG 编码、80fps 容器，但内部画面实际只有 ~12fps
 * 且**帧间互不相关**（不是连续拍摄的运镜，是一组泡沫照片）。
 * 因此若按原速硬切播放，会变成"幻灯片闪烁"，一眼假。
 * 这里的做法是**连续交叉溶解**：永远处于两帧的过渡中，画面一直在缓慢演变、
 * 从不静止。好处不只是好看 —— 溶解让"循环点"在物理上不存在（首帧与末帧是同一内容）。
 *
 * ⚠️ 为什么必须 muted + autoplay + playsinline：
 *    少一个都会被浏览器拦掉自动播放；playsinline 是 iOS 的额外要求。
 * ⚠️ 不要为每个使用处各建一个 <video>：多个实例 = 多份解码。
 *    多路用同一个 src 时，浏览器仍会各自解码；如需多路，改用 currentTime 偏移错开。
 */
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  label: { type: String, default: '浮选泡沫' },
  src: { type: String, default: '/media/froth-loop.mp4' },
  poster: { type: String, default: '/media/froth-poster.jpg' },
  // 画面宽高比（素材为 692×518 ≈ 4:3）。面板要窄一点就传 '4 / 3' 或 '16 / 10'
  ratio: { type: String, default: '4 / 3' },
  // 左下角标注：这是现场实拍素材，不是合成画面（老板对"假"零容忍，标注清楚）
  note: { type: String, default: '现场实拍 · 692×518' },
  /**
   * 形态：
   *   panel —— 默认。按 ratio 撑开高度（工作台左列那种竖版面板）。
   *   band  —— 横幅。高度交给外部（挂载点给多少就多高），方形圆角与边框交给外部。
   *            用在直播墙上：做成一条扁的超宽画面带。
   *
   * ⚠️ 为什么横幅不用 ratio 而用固定高：
   *    直播墙那条带宽 ≈1275px，若按 4:3 会撑到 956px 高（整页都放不下）。
   *    泡沫纹理**没有构图主体**，用 object-fit:cover 横向裁一条完全成立，
   *    所以带子只需要一个"窗高"。
   */
  variant: { type: String, default: 'panel' },
})

const isBand = computed(() => props.variant === 'band')
const rootStyle = computed(() =>
  isBand.value ? {} : { aspectRatio: props.ratio },
)

const videoEl = ref(null)
const failed = ref(false)
const clock = ref('')
let timer = null

/**
 * 抓取当前画面为一帧图像文件 —— 供"分析当前画面"直接接进既有分析链路
 * （后端 `POST /analyze/{scene} ` 收的就是 multipart 的 `file`，所以这里交回 File）。
 *
 * ⚠️ 必须在视频已解出画面时调用（readyState >= 2）：否则 drawImage 画出来是空白。
 * ⚠️ 源是同源的 `/media/*.mp4` → canvas 不会被跨域污染，可直接 toBlob；
 *    若以后换成跨域视频源，必须给 <video> 加 crossorigin 且服务端带 CORS 头。
 * 分辨率取视频固有尺寸（692×518 = 素材原始分辨率），不做任何缩放或滤镜 ——
 * 交出去的必须是原帧（CSS 上的 brightness 只是显示层，不会进画布）。
 */
function captureFrame(name = 'froth-live-frame.jpg', quality = 0.92) {
  const v = videoEl.value
  if (!v || v.readyState < 2 || !v.videoWidth) return Promise.resolve(null)
  const c = document.createElement('canvas')
  c.width = v.videoWidth
  c.height = v.videoHeight
  c.getContext('2d').drawImage(v, 0, 0, c.width, c.height)
  return new Promise((resolve) => {
    c.toBlob(
      (blob) => resolve(blob ? new File([blob], name, { type: 'image/jpeg' }) : null),
      'image/jpeg',
      quality,
    )
  })
}

defineExpose({ captureFrame, videoEl })

function tick() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  clock.value = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

onMounted(() => {
  tick()
  timer = setInterval(tick, 1000)
  const v = videoEl.value
  if (!v) return
  // 有些浏览器即便写了 autoplay 也会因策略被挂起，这里补一次显式 play()
  const p = v.play?.()
  if (p && p.catch) p.catch(() => { /* 交给 poster 兜底 */ })
  v.addEventListener('error', () => { failed.value = true })
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  const v = videoEl.value
  if (v) { try { v.pause() } catch (e) { /* noop */ } }
})
</script>

<template>
  <div class="froth-live" :class="{ 'is-band': isBand }" :style="rootStyle">
    <video
      v-show="!failed"
      ref="videoEl"
      class="fl-video"
      :src="src"
      :poster="poster"
      muted
      autoplay
      playsinline
      loop
      preload="auto"
    />
    <img v-if="failed" class="fl-video" :src="poster" :alt="label" />

    <!-- 左上：LIVE 角标（呼吸红点 = 画面在走） -->
    <div class="fl-live">
      <i class="fl-dot" />
      <span>LIVE</span>
    </div>

    <!-- 右上：本机时钟（真实时间，不是素材时间戳） -->
    <div class="fl-clock">{{ clock }}</div>

    <!-- 底部：点位 + 来源标注 -->
    <div class="fl-foot">
      <span class="fl-label">{{ label }}</span>
      <span class="fl-note">{{ note }}</span>
    </div>
  </div>
</template>

<style scoped>
.froth-live {
  position: relative;
  width: 100%;
  overflow: hidden;
  border-radius: 8px;
  background: #0f1419;
  border: 1px solid #e5e6eb;
}
.fl-video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.fl-live {
  position: absolute;
  top: 8px;
  left: 8px;
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.fl-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #f53f3f;
  animation: fl-breath 1.6s ease-in-out infinite;
}
@keyframes fl-breath {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.35; transform: scale(0.82); }
}
.fl-clock {
  position: absolute;
  top: 8px;
  right: 8px;
  padding: 3px 8px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
}
.fl-foot {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 18px 8px 6px;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.62));
  color: #fff;
  font-size: 11px;
}
.fl-label { font-weight: 600; }
.fl-note { color: rgba(255, 255, 255, 0.72); }

/* ---- 横幅形态：撑满挂载点，圆角/边框交给外部（挂载点自己做 overflow:hidden） ---- */
.froth-live.is-band {
  height: 100%;
  border: none;
  border-radius: inherit;   /* 跟随挂载点的圆角，避免四角漏底色 */
}
/* 带子上下一共就一百来像素，角标与底部标签各收薄一点，把画面留给泡沫 */
.froth-live.is-band .fl-live,
.froth-live.is-band .fl-clock {
  top: 7px;
}
.froth-live.is-band .fl-foot {
  padding: 22px 12px 7px;
}
/* 素材是矿物化泡沫（气泡表面覆着硫化物矿粒），本色偏暗；
   扁带/小窗在浅色大屏上不抬一点会读成"一条没画面的黑带"。
   只提亮度/对比度 —— 画面仍是实拍原帧，不做任何合成或改色。 */
.froth-live.is-band .fl-video {
  /* ⚠️ 必须让 video 脱离文档流（absolute），不能参与父级高度计算：
     band 模式的高度是"父级给多少就是多少"（height:100%），若父级高度是 auto，
     这个 100% 就是循环依赖 → 回落成 video 的固有宽高比（692×518）。
     实测 272px 宽的窗会被撑到 204px 自然高，**再反过来把同行面板一起拉高**
     （工艺流程 160 → 204，连带把下面的品位趋势少给了 44px）。
     absolute 之后自然高度=0，窗口高度完全由外部决定。 */
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  filter: brightness(1.12) contrast(1.05);
}
</style>
