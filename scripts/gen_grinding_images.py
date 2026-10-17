"""
磨矿颗粒图像生成器 v2 — 照片级真实感显微镜矿物截面图

三种工况模式：
  normal    : 混合粒度分布（P80 ~ 120μm）
  overground: 偏细过磨（P80 ~ 65μm，大量微粉）
  underground: 偏粗欠磨（P80 ~ 200μm，大颗粒为主）

v2 改进（解决 v1 "太假" 问题）：
  - 颗粒形状：Perlin 噪声变形的椭圆，非规则多边形
  - 真实矿物光学特性：方铅矿(铅灰白)、闪锌矿(棕黄-黑)、黄铁矿(浅黄铜)、石英(透明灰)
  - 3D 光照模型：漫反射 + 镜面高光 + 自身阴影 + 环境光遮蔽
  - 晶体纹理：解理面、内部裂纹、夹杂纹理
  - 真实背景：细粉基质 + 微观纹理 + 渐变照明
  - 颗粒间交互：接触边界、部分遮挡、间隙填充

用法：
    python scripts/gen_grinding_images.py [--count 25] [--output data/uploads/grinding]
"""

import argparse
import math
import os
import random
import sys
from pathlib import Path

import cv2
import numpy as np

# ── 固定随机种子 ───────────────────────────────────────────────
BASE_SEED = 2026

# ── 图像参数 ────────────────────────────────────────────────────
IMG_W, IMG_H = 640, 480
SCALE_BAR_UM = 500


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)


# ═══════════════════════════════════════════════════════════════════
#  Perlin 噪声实现（用于自然形状变形）
# ═══════════════════════════════════════════════════════════════════

def _fade(t):
    """平滑插值函数 6t^5 - 15t^4 + 10t^3"""
    return t * t * t * (t * (t * 6 - 15) + 10)


def _lerp(a, b, t):
    return a + t * (b - a)


def _grad(h, x, y):
    """梯度向量点积"""
    vectors = np.array([[1, 1], [-1, 1], [1, -1], [-1, -1],
                        [1, 0], [-1, 0], [0, 1], [0, -1]], dtype=np.float64)
    g = vectors[h % 8]
    return g[0] * x + g[1] * y


class PerlinNoise2D:
    """2D Perlin 噪声生成器"""

    def __init__(self, seed=0):
        rng = np.random.RandomState(seed)
        self.perm = list(range(256))
        rng.shuffle(self.perm)
        self.perm += self.perm

    def __call__(self, x, y):
        """计算 (x, y) 处的噪声值，范围约 [-1, 1]"""
        xi = int(math.floor(x)) & 255
        yi = int(math.floor(y)) & 255
        xf = x - math.floor(x)
        yf = y - math.floor(y)
        u = _fade(xf)
        v = _fade(yf)

        aa = self.perm[self.perm[xi] + yi]
        ab = self.perm[self.perm[xi] + yi + 1]
        ba = self.perm[self.perm[xi + 1] + yi]
        bb = self.perm[self.perm[xi + 1] + yi + 1]

        x1 = _lerp(_grad(aa, xf, yf), _grad(ba, xf - 1, yf), u)
        x2 = _lerp(_grad(ab, xf, yf - 1), _grad(bb, xf - 1, yf - 1), u)
        return _lerp(x1, x2, v)

    def octave(self, x, y, octaves=4, persistence=0.5):
        """多倍频噪声（分形噪声）"""
        total = 0
        frequency = 1
        amplitude = 1
        max_value = 0
        for _ in range(octaves):
            total += self.__call__(x * frequency, y * frequency) * amplitude
            max_value += amplitude
            amplitude *= persistence
            frequency *= 2
        return total / max_value


# ═══════════════════════════════════════════════════════════════════
#  真实矿物光学属性
# ═══════════════════════════════════════════════════════════════════

# 每种矿物的光学特征（用于 3D 光照渲染）
MINERALS = {
    "galena": {
        "name": "方铅矿 PbS",
        # 基础颜色范围 (BGR) — 铅灰白色
        "base_color_range": ((170, 165, 155), (210, 205, 195)),
        # 反射率（影响亮度）
        "reflectivity": 0.85,
        # 表面粗糙度（低=光滑/金属光泽，高=暗淡）
        "roughness": 0.15,
        # 是否有解理纹理
        "has_cleavage": True,
        # 解理方向（立方体解理 → 90度交叉纹）
        "cleavage_angle": 90,
        # 内部是否常见包裹体
        "has_inclusions": True,
        # 典型包裹体颜色偏移
        "inclusion_color": (-20, -25, -30),  # BGR 暗斑
    },
    "sphalerite": {
        "name": "闪锌矿 ZnS",
        # 树脂-金刚光泽，棕色到黄色到黑色（含铁量变化）
        "base_color_range": ((90, 75, 50), (160, 140, 100)),
        "reflectivity": 0.55,
        "roughness": 0.35,
        "has_cleavage": True,
        "cleavage_angle": 60,  # 六方/菱形解理
        "has_inclusions": True,
        "inclusion_color": (-30, -35, -20),
    },
    "pyrite": {
        "name": "黄铁矿 FeS2",
        # 浅黄铜色，强金属光泽
        "base_color_range": ((180, 175, 130), (220, 215, 170)),
        "reflectivity": 0.70,
        "roughness": 0.20,
        "has_cleavage": False,
        "has_inclusions": False,
        "inclusion_color": (0, 0, 0),
    },
    "quartz": {
        "name": "石英 SiO2",
        # 玻璃光泽，透明到灰白色
        "base_color_range": ((140, 135, 128), (180, 175, 168)),
        "reflectivity": 0.40,
        "roughness": 0.10,
        "has_cleavage": False,
        "has_inclusions": False,
        "inclusion_color": (0, 0, 0),
    },
    "calcite": {
        "name": "方解石 CaCO3",
        # 白色-灰白色
        "base_color_range": ((175, 172, 168), (210, 207, 203)),
        "reflectivity": 0.45,
        "roughness": 0.25,
        "has_cleavage": True,
        "cleavage_angle": 75,  # 菱形解理
        "has_inclusions": False,
        "inclusion_color": (0, 0, 0),
    },
}


def pick_mineral():
    """按铅锌矿典型品位比例选择矿物"""
    r = random.random()
    if r < 0.38:
        return "galena"       # 方铅矿（主要铅矿物）
    elif r < 0.68:
        return "sphalerite"   # 闪锌矿（主要锌矿物）
    elif r < 0.85:
        return "pyrite"       # 黄铁矿（常见伴生）
    elif r < 0.95:
        return "quartz"       # 石英（脉石主要成分）
    else:
        return "calcite"      # 方解石（常见脉石）


# ═══════════════════════════════════════════════════════════════════
#  形状生成：基于噪声变形的自然颗粒
# ═══════════════════════════════════════════════════════════════════

def generate_particle_shape(cx, cy, rx, ry, noise_scale=0.08, noise_amp=0.30,
                            rotation=0, perlin=None):
    """生成一个噪声变形的椭圆颗粒形状掩码

    Args:
        cx, cy: 中心坐标
        rx, ry: 椭圆半轴长（像素）
        noise_scale: 噪声频率（越小越平滑）
        noise_amp: 噪声振幅（相对于半径的比例）
        rotation: 旋转角度（弧度）
        perlin: PerlinNoise2D 实例

    Returns:
        (H, W) bool 掩码, 距离场 (H, W) float32
    """
    if perlin is None:
        perlin = PerlinNoise2D(seed=random.randint(0, 99999))

    y_coords, x_coords = np.ogrid[:IMG_H, :IMG_W]

    # 相对于中心的坐标
    dx = x_coords - cx
    dy = y_coords - cy

    # 旋转
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    dx_rot = dx * cos_r + dy * sin_r
    dy_rot = -dx * sin_r + dy * cos_r

    # 归一化到椭圆坐标系
    nx = dx_rot / max(rx, 1)
    ny = dy_rot / max(ry, 1)

    # 在椭圆边界附近施加 Perlin 噪声变形
    dist_from_center = np.sqrt(nx * nx + ny * ny)

    # 只在边界区域应用噪声（内部保持接近 1，外部接近 >1）
    boundary_zone = np.clip((dist_from_center - 0.3) / 0.7, 0, 1)

    # 计算每个点的噪声偏移
    noise_val = np.zeros_like(dist_from_center, dtype=np.float64)
    # 采样关键点（全图采样太慢，用向量化近似）
    step = 2  # 每 2 像素采一个噪点
    for i in range(0, IMG_H, step):
        for j in range(0, IMG_W, step):
            if dist_from_center[i, j] < 1.5:  # 只在颗粒附近计算
                n = perlin.octave(j * noise_scale, i * noise_scale, octaves=3)
                noise_val[i:i+step, j:j+step] = n

    # 噪声影响的边界
    noisy_radius = 1.0 + noise_val * noise_amp * boundary_zone

    # 最终掩码
    mask = dist_from_center <= noisy_radius

    # 距离场（用于光照计算，内部为正，外部为负）
    distance_field = noisy_radius - dist_from_center

    return mask, distance_field.astype(np.float32)


def generate_particle_shape_fast(cx, cy, rx, ry, noise_seed=0,
                                 noise_amp=0.28, rotation=0):
    """快速版本：用极坐标 + 噪声变形生成颗粒轮廓

    比 full 版本快 10 倍以上，效果相近
    """
    # 极坐标采样点
    n_points = 128
    angles = np.linspace(0, 2 * np.pi, n_points, endpoint=False)

    # 基础椭圆半径
    base_r = (rx * ry) / np.sqrt((ry * np.cos(angles))**2 + (rx * np.sin(angles))**2)

    # Perlin 噪声变形
    perlin = PerlinNoise2D(seed=noise_seed)
    noise_vals = np.array([perlin.octave(
        cx * 0.02 + math.cos(a) * 3,
        cy * 0.02 + math.sin(a) * 3,
        octaves=3
    ) for a in angles])

    # 应用噪声
    deformed_r = base_r * (1 + noise_vals * noise_amp)

    # 旋转
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    pts_x = cx + deformed_r * np.cos(angles) * cos_r - deformed_r * np.sin(angles) * sin_r
    pts_y = cy + deformed_r * np.cos(angles) * sin_r + deformed_r * np.sin(angles) * cos_r

    pts = np.column_stack([pts_x, pts_y]).astype(np.int32)

    # 创建掩码
    mask = np.zeros((IMG_H, IMG_W), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 1)

    # 创建距离场近似（用于光照）
    # 对 mask 本身做距离变换：内部像素到最近边缘的距离（内部为正，中心最大）
    distance_field = np.zeros((IMG_H, IMG_W), dtype=np.float32)
    cv2.distanceTransform(mask, cv2.DIST_L2, 5, distance_field)
    # 内部为正，外部为负
    distance_field = np.where(mask > 0, distance_field, -distance_field * 0.3)

    return mask > 0, distance_field, pts


# ═══════════════════════════════════════════════════════════════════
#  3D 光照渲染
# ═══════════════════════════════════════════════════════════════════

def render_particle_3d(img, mask, dist_field, mineral_key, cx, cy,
                       light_dir=None, shadow_offset=(3, 3)):
    """对单个颗粒进行 3D 光照渲染

    使用简化的 Phong-Blinn 模型：
      - 漫反射（Lambertian）：基于法线（从距离场估算）与光源方向的夹角
      - 镜面反射：在面向光源的高曲率区域产生高光
      - 环境光遮蔽(AO)：颗粒凹陷处和接触边界变暗
      - 投影阴影：颗粒向右下方投射阴影
    """
    mineral = MINERALS[mineral_key]

    # 基础颜色（在该矿物范围内随机选取）
    lo = np.array(mineral["base_color_range"][0], dtype=np.float64)
    hi = np.array(mineral["base_color_range"][1], dtype=np.float64)
    base_color = lo + np.random.random(3) * (hi - lo)

    # 光源方向（左上方 45 度）
    if light_dir is None:
        light_angle = random.uniform(-0.6, -0.3)  # 弧度，左上方
        light_dir = np.array([math.cos(light_angle), math.sin(light_angle)])

    light_dir = light_dir / (np.linalg.norm(light_dir) + 1e-8)

    # 从距离场估算法线
    # 距离场在颗粒内部：中心最大、边缘为 0，梯度指向中心
    # 表面法线 = (-dh/dx, -dh/dy, 1)，整体归一化
    # （小梯度 → 法线接近竖直 (0,0,1)；大梯度 → 法线倾斜）
    gy, gx = np.gradient(dist_field, edge_order=2)

    nx = -gx
    ny = -gy
    nz = np.ones_like(nx)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx = nx / norm
    ny = ny / norm
    nz = nz / norm

    # 光源在 3D 空间的方向（假设 z=0.7 的俯视角）
    light_3d = np.array([light_dir[0], light_dir[1], 0.7])
    light_3d = light_3d / np.linalg.norm(light_3d)

    # 漫反射
    n_pixels = len(light_3d)
    normal_3d = np.stack([nx, ny, nz], axis=-1)
    diffuse = np.sum(normal_3d * light_3d, axis=-1)
    diffuse = np.clip(diffuse, 0, 1)

    # 镜面高光（Blinn-Phong）
    view_dir = np.array([0, 0, 1])
    half_dir = light_3d + view_dir
    half_dir = half_dir / (np.linalg.norm(half_dir) + 1e-8)
    spec = np.sum(normal_3d * half_dir, axis=-1)
    spec = np.clip(spec, 0, 1)
    spec_power = 1.0 / (mineral["roughness"] + 0.1)
    spec = np.power(spec, spec_power) * mineral["reflectivity"]

    # 环境光遮蔽（边缘和凹陷处变暗）
    ao = np.clip(dist_field / 15.0, 0.1, 1.0)
    # 边缘额外暗化
    edge_darken = np.clip(dist_field / 5.0, 0.3, 1.0)

    # 组合光照
    ambient = 0.32
    lighting = ambient + diffuse * 0.62 * ao + spec * 0.22 * edge_darken

    # 应用矿物颜色
    particle_rgb = np.zeros((IMG_H, IMG_W, 3), dtype=np.float64)
    for c in range(3):
        particle_rgb[:, :, c] = base_color[c] * lighting

    # ── 添加晶体纹理 ──

    # 解理纹理（某些矿物有）
    if mineral.get("has_cleavage"):
        cleavage_angle = mineral.get("cleavage_angle", 90)
        angle_rad = math.radians(cleavage_angle + random.uniform(-10, 10))
        yy, xx = np.mgrid[0:IMG_H, 0:IMG_W]
        # 沿解理方向的投影坐标，产生平行条纹
        proj = (xx - cx) * math.cos(angle_rad) + (yy - cy) * math.sin(angle_rad)
        line_spacing = random.uniform(8, 20)
        cleavage_pattern = np.abs(np.sin(proj / line_spacing * np.pi))
        cleavage = np.where(mask, cleavage_pattern * 0.06, 0)
        # 只在颗粒内部且不在边缘显示
        cleavage *= edge_darken
        for c in range(3):
            particle_rgb[:, :, c] *= (1 - cleavage)

    # 内部裂纹/包裹体
    if mineral.get("has_inclusions") and random.random() > 0.4:
        # 用距离场最大值估算颗粒内切半径
        grain_radius = max(float(dist_field.max()), 4.0)
        n_cracks = random.randint(0, 3)
        for _ in range(n_cracks):
            # 随机裂纹起点和方向
            crack_cx = cx + random.randint(int(-grain_radius * 0.5), int(grain_radius * 0.5))
            crack_cy = cy + random.randint(int(-grain_radius * 0.5), int(grain_radius * 0.5))
            crack_len = random.uniform(grain_radius * 0.3, grain_radius * 0.8)
            crack_angle = random.uniform(0, np.pi)
            crack_width = int(random.uniform(1, 3))

            crack_mask = np.zeros((IMG_H, IMG_W), dtype=np.float64)
            for d in np.linspace(0, crack_len, int(max(crack_len, 2))):
                px = int(crack_cx + d * math.cos(crack_angle))
                py = int(crack_cy + d * math.sin(crack_angle))
                if 0 <= px < IMG_W and 0 <= py < IMG_H:
                    cv2.circle(crack_mask, (px, py), crack_width, 0.3, -1)

            inc_color = mineral.get("inclusion_color", (-15, -15, -15))
            for c in range(3):
                particle_rgb[:, :, c] += inc_color[c] * crack_mask * mask

    # 细微颜色不均匀性（晶体生长带）
    color_variation = np.zeros((IMG_H, IMG_W, 3), dtype=np.float64)
    var_seed = random.randint(0, 99999)
    var_perlin = PerlinNoise2D(seed=var_seed)
    step = 4
    for i in range(0, IMG_H, step):
        for j in range(0, IMG_W, step):
            if mask[i, j]:
                v = var_perlin.octave(j * 0.03, i * 0.03, octaves=2) * 12
                color_variation[i:i+step, j:j+step, :] = v
    particle_rgb += color_variation * mask[:, :, np.newaxis]

    # ── 合成到主图像 ──
    particle_rgb = np.clip(particle_rgb, 0, 255).astype(np.uint8)

    # 先绘制投影阴影
    shadow_mask = np.roll(mask, shadow_offset[1], axis=0)
    shadow_mask = np.roll(shadow_mask, shadow_offset[0], axis=1)
    shadow_overlap = shadow_mask.astype(np.float16) * mask.astype(np.float16)
    shadow_alpha = 0.25 * (1 - shadow_overlap)  # 不重叠的地方才有阴影
    img_shadowed = img.astype(np.float16)
    for c in range(3):
        img_shadowed[:, :, c] = np.where(
            (shadow_mask > 0) & (mask == 0),
            img_shadowed[:, :, c] * (1 - shadow_alpha * 0.4),
            img_shadowed[:, :, c]
        )

    # 再绘制颗粒本身（带抗锯齿边缘）
    # 边缘混合（使用距离场做 anti-aliasing）
    edge_width = 2.5
    edge_blend = np.clip(dist_field / edge_width, 0, 1)  # 内部=1, 外部过渡到0
    edge_blend = edge_blend * mask.astype(np.float64)

    for c in range(3):
        img_float = img_shadowed[:, :, c].astype(np.float64)
        particle_ch = particle_rgb[:, :, c].astype(np.float64)
        blended = img_float * (1 - edge_blend) + particle_ch * edge_blend
        img[:, :, c] = np.clip(blended, 0, 255).astype(np.uint8)


# ═══════════════════════════════════════════════════════════════════
#  背景生成
# ═══════════════════════════════════════════════════════════════════

def generate_background(condition="normal", seed=0):
    """生成真实的显微镜背景

    特征：
      - 深灰色树脂/环氧基底
      - 细微粉末填充（尤其是过磨状态）
      - 微观纹理和不均匀照明
      - 轻微的 vignette（暗角）效果
    """
    rng = np.random.RandomState(seed)
    perlin = PerlinNoise2D(seed=seed + 1000)

    # 基底颜色（深灰，模拟环氧树脂）
    base_gray = rng.randint(42, 52)
    img = np.full((IMG_H, IMG_W, 3), base_gray, dtype=np.uint8)

    # 大尺度亮度变化（模拟照明不均匀）
    for i in range(0, IMG_H, 3):
        for j in range(0, IMG_W, 3):
            v = perlin.octave(j * 0.008, i * 0.008, octaves=2) * 12
            img[i:i+3, j:j+3] = np.clip(img[i:i+3, j:j+3].astype(np.int16) + v, 0, 255).astype(np.uint8)

    # 细粉基质（微小颗粒点缀）
    if condition == "overground":
        # 过磨：大量细粉
        n_dust = rng.randint(800, 1500)
        dust_sizes = rng.exponential(1.5, n_dust).clip(0.5, 4)
    elif condition == "underground":
        # 欠磨：较少细粉
        n_dust = rng.randint(200, 400)
        dust_sizes = rng.exponential(1.2, n_dust).clip(0.5, 3)
    else:
        # 正常：中等细粉
        n_dust = rng.randint(400, 700)
        dust_sizes = rng.exponential(1.4, n_dust).clip(0.5, 3.5)

    for _ in range(n_dust):
        dx = rng.randint(0, IMG_W)
        dy = rng.randint(0, IMG_H)
        dr = dust_sizes[_] if _ < len(dust_sizes) else rng.uniform(0.5, 3)
        # 细粉颜色：略亮或略暗于背景
        brightness = rng.choice([-8, -5, -2, 2, 5, 8])
        dust_val = int(np.clip(base_gray + brightness, 0, 255))
        dust_color = (dust_val, dust_val, dust_val)
        cv2.circle(img, (dx, dy), int(dr), dust_color, -1)

    # 更大的背景颗粒（脉石碎片）
    n_bg_grains = rng.randint(15, 35) if condition != "overground" else rng.randint(8, 20)
    for _ in range(n_bg_grains):
        bgx = rng.randint(0, IMG_W)
        bgy = rng.randint(0, IMG_H)
        bgr = rng.uniform(5, 18) if condition != "underground" else rng.uniform(3, 12)
        # 暗灰色脉石
        bg_brightness = rng.randint(base_gray - 12, base_gray + 3)
        bg_val = int(np.clip(bg_brightness, 20, 80))
        bg_color = (bg_val, bg_val, bg_val)
        # 不规则形状
        n_verts = rng.randint(5, 8)
        verts = []
        for vi in range(n_verts):
            a = 2 * np.pi * vi / n_verts + rng.uniform(-0.3, 0.3)
            rr = bgr * rng.uniform(0.6, 1.3)
            verts.append([int(bgx + rr * math.cos(a)), int(bgy + rr * math.sin(a))])
        cv2.fillPoly(img, [np.array(verts, np.int32)], bg_color)

    # Vignette 暗角效果（模拟显微镜视野）
    cx_v, cy_v = IMG_W / 2, IMG_H / 2
    max_dist = math.sqrt(cx_v ** 2 + cy_v ** 2)
    y_idx, x_idx = np.ogrid[:IMG_H, :IMG_W]
    dist_from_center = np.sqrt((x_idx - cx_v) ** 2 + (y_idx - cy_v) ** 2) / max_dist
    vignette = 1 - np.clip(dist_from_center - 0.5, 0, 0.5) * 0.35
    for c in range(3):
        img[:, :, c] = (img[:, :, c].astype(np.float32) * vignette).astype(np.uint8)

    return img


# ═══════════════════════════════════════════════════════════════════
#  主生成函数
# ═══════════════════════════════════════════════════════════════════

def generate_grinding_image(condition="normal", index=0):
    """生成一张照片级真实的磨矿颗粒显微镜图像

    Args:
        condition: 'normal' | 'overground' | 'underground'
        index: 图片编号（种子变化）

    Returns:
        numpy.ndarray (H, W, 3) BGR image
    """
    seed = BASE_SEED + hash(f"{condition}_{index}") % (2 ** 31)
    set_seed(seed)
    rng = np.random.RandomState(seed + 50000)

    # ── 1. 生成背景 ──
    img = generate_background(condition=condition, seed=seed)

    # ── 2. 根据工况决定颗粒大小分布 ──
    if condition == "normal":
        # 对数正态混合，P80 ~ 120μm (假设 1px ≈ 2.5μm)
        main_sizes = rng.lognormal(mean=3.2, sigma=0.75, size=rng.randint(35, 60)).clip(5, 80)
    elif condition == "overground":
        # 过磨：以中小颗粒为主
        main_sizes = rng.lognormal(mean=2.5, sigma=0.65, size=rng.randint(80, 140)).clip(3, 45)
        # 少量稍大的
        extra = rng.lognormal(mean=3.3, sigma=0.4, size=rng.randint(5, 12)).clip(25, 60)
        main_sizes = np.concatenate([main_sizes, extra])
    else:  # underground
        # 欠磨：大颗粒为主
        main_sizes = rng.lognormal(mean=3.9, sigma=0.55, size=rng.randint(18, 32)).clip(30, 95)
        # 少量填充
        filler = rng.lognormal(mean=2.0, sigma=0.55, size=rng.randint(8, 20)).clip(3, 18)
        main_sizes = np.concatenate([main_sizes, filler])

    rng.shuffle(main_sizes)

    # ── 3. 按从大到小绘制（实现遮挡关系）──
    # 大颗粒先画（在下层），小颗粒后画（在上层）
    sort_idx = np.argsort(-main_sizes)
    sorted_sizes = main_sizes[sort_idx]

    # 用于记录已占用区域（避免过度重叠）
    occupied = np.zeros((IMG_H, IMG_W), dtype=np.uint8)

    particle_count = 0
    for size_idx, size in enumerate(sorted_sizes):
        radius = size / 2
        if radius < 2:
            continue

        # 尝试找到一个不与太多已有颗粒重叠的位置
        best_pos = None
        best_overlap = float('inf')
        attempts = 0
        max_attempts = 25 if radius < 20 else 15

        while attempts < max_attempts:
            x = rng.randint(int(radius) + 8, IMG_W - int(radius) - 8)
            y = rng.randint(int(radius) + 8, IMG_H - int(radius) - 8)

            # 检查与已占用区域的重叠
            test_mask = np.zeros((IMG_H, IMG_W), dtype=np.uint8)
            cv2.circle(test_mask, (x, y), int(radius * 1.1), 1, -1)
            overlap = np.sum(test_mask & occupied) / max(1, np.sum(test_mask))

            if overlap < best_overlap:
                best_overlap = overlap
                best_pos = (x, y)
                if overlap < 0.15:
                    break
            attempts += 1

        if best_pos is None:
            continue

        x, y = best_pos

        # 椭圆长短轴（略微不等，更自然）
        rx = radius * rng.uniform(0.85, 1.15)
        ry = radius * rng.uniform(0.85, 1.15)
        rotation = rng.uniform(0, np.pi)

        # 选择矿物类型
        mineral_key = pick_mineral()

        # 生成颗粒形状
        noise_seed = rng.randint(0, 999999)
        noise_amp = rng.uniform(0.18, 0.35)
        mask, dist_field, contour_pts = generate_particle_shape_fast(
            x, y, rx, ry,
            noise_seed=noise_seed,
            noise_amp=noise_amp,
            rotation=rotation
        )

        if np.sum(mask) < 10:  # 太小了
            continue

        # 3D 光照渲染
        render_particle_3d(
            img, mask, dist_field, mineral_key, x, y,
            light_dir=None,
            shadow_offset=(rng.randint(2, 5), rng.randint(2, 5))
        )

        # 标记为已占用（用于后续颗粒避让）
        occupied = np.maximum(occupied, mask.astype(np.uint8))

        particle_count += 1

    # ── 4. 后处理 ──

    # 轻微高斯模糊（模拟光学显微镜有限分辨率）
    img = cv2.GaussianBlur(img, (3, 3), sigmaX=0.5, sigmaY=0.5)

    # 添加传感器噪声（模拟 CCD/CMOS 噪点）
    noise = rng.normal(0, 3.5, img.shape).astype(np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    # 轻微对比度增强（模拟图像处理）
    alpha = 1.06
    beta = 4
    img = cv2.convertScaleAbs(img, alpha=alpha, beta=beta)

    # 轻微锐化（增强颗粒边缘）
    kernel_sharpen = np.array([[-0.1, -0.1, -0.1],
                               [-0.1,  1.8, -0.1],
                               [-0.1, -0.1, -0.1]])
    img = cv2.filter2D(img, -1, kernel_sharpen)
    img = np.clip(img, 0, 255).astype(np.uint8)

    # ── 5. 标尺条 ──
    scale_bar_px = 120  # 120px ≈ 500μm
    sb_x = IMG_W - scale_bar_px - 20
    sb_y = IMG_H - 22
    # 白色标尺线
    cv2.line(img, (sb_x, sb_y), (sb_x + scale_bar_px, sb_y), (215, 212, 208), 2)
    # 标尺文字
    cv2.putText(img, f"{SCALE_BAR_UM} um", (sb_x, sb_y - 7),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (215, 212, 208), 1, cv2.LINE_AA)

    # 左上角颗粒计数标签
    cv2.putText(img, f"Grains: {particle_count}", (8, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 176, 172), 1, cv2.LINE_AA)

    return img


# ═══════════════════════════════════════════════════════════════════
#  CLI 入口
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="生成照片级真实磨矿颗粒显微镜图像 v2")
    parser.add_argument("--count", type=int, default=25, help="生成的图片数量")
    parser.add_argument("--output", type=str, default=None,
                        help="输出目录（默认 data/uploads/grinding）")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    output_dir = Path(args.output) if args.output else project_root / "data" / "uploads" / "grinding"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[v2 照片级真实版] 输出目录: {output_dir}")

    # 工况分配（固定种子，保证文件名与条件稳定，避免 DB 关联错位）
    random.seed(BASE_SEED)
    conditions = (
        ["normal"] * max(1, int(args.count * 0.40)) +
        ["overground"] * max(1, int(args.count * 0.35)) +
        ["underground"] * max(1, int(args.count * 0.25))
    )
    random.shuffle(conditions)
    while len(conditions) < args.count:
        conditions.append(random.choice(["normal", "overground", "underground"]))
    conditions = conditions[:args.count]

    generated = []
    for i, cond in enumerate(conditions):
        print(f"  [{i+1}/{args.count}] 生成 {cond} ...", end=" ", flush=True)
        img = generate_grinding_image(condition=cond, index=i)
        filename = f"grinding_{i:03d}_{cond}.jpg"
        filepath = output_dir / filename
        _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 94])
        with open(str(filepath), 'wb') as f:
            f.write(buf.tobytes())
        generated.append((filename, cond))
        print(f"OK ({filepath.stat().st_size // 1024}KB)")

    print(f"\n完成！生成 {len(generated)} 张图片到 {output_dir}")

    # 文件列表
    list_file = output_dir / "_file_list.txt"
    with open(list_file, "w") as f:
        for fname, cond in generated:
            f.write(f"{fname}|{cond}\n")
    print(f"文件列表: {list_file}")


if __name__ == "__main__":
    main()
