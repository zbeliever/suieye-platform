<template>
  <!-- 「淬眸」品牌标记：矿石晶体轮廓 + 瞳孔
       - 外：六边形（尖朝上下），取矿物结晶意象，呼应「淬」
       - 内：横向扁椭圆 + 实心瞳心，取「眸」意
       单色描边，随 currentColor 继承父级颜色，24px 下依然清晰 -->
  <svg
    viewBox="0 0 32 32"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    role="img"
    aria-label="SuiEye 淬眸"
  >
    <path
      d="M16 3.5 L26.83 9.75 L26.83 22.25 L16 28.5 L5.17 22.25 L5.17 9.75 Z"
      stroke="currentColor"
      stroke-width="2"
      stroke-linejoin="round"
    />
    <ellipse cx="16" cy="16" rx="7" ry="5.6" stroke="currentColor" stroke-width="2" />
    <circle cx="16" cy="16" r="2.2" fill="currentColor" />
  </svg>
</template>
