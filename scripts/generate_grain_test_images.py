"""生成模拟矿石颗粒测试图像

用于粒度场景端到端测试（在没有真实磨矿图像时）
生成包含不同大小圆形/椭圆形颗粒的合成图像
"""

import os
import random
import numpy as np
import cv2

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "样例图像", "grinding")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def generate_grain_image(
    width: int = 640,
    height: int = 480,
    n_grains: int = 35,
    min_radius: int = 8,
    max_radius: int = 45,
    bg_color: tuple = (60, 55, 50),  # 深灰褐背景（模拟矿浆）
    grain_color_range: tuple = ((140, 130, 110), (200, 190, 170)),  # 浅灰-米色颗粒
    noise_level: float = 15.0,
    seed: int = 42,
) -> np.ndarray:
    """生成模拟矿石颗粒图像

    Args:
        width, height: 图像尺寸
        n_grains: 颗粒数量
        min_radius, max_radius: 颗粒半径范围
        bg_color: 背景色 (BGR)
        grain_color_range: 颜色范围 (BGR)
        noise_level: 高斯噪声标准差
        seed: 随机种子（可复现）
    """
    rng = np.random.RandomState(seed)

    # 背景
    img = np.full((height, width, 3), bg_color, dtype=np.uint8)

    # 生成颗粒（从大到小画，小的在上层）
    radii = rng.randint(min_radius, max_radius + 1, size=n_grains * 2)
    radii = sorted(radii, reverse=True)[:n_grains]

    for r in radii:
        # 随机位置（避免边缘）
        cx = rng.randint(r + 10, width - r - 10)
        cy = rng.randint(r + 10, height - r - 10)

        # 随机颜色（在范围内）
        c_min = np.array(grain_color_range[0], dtype=np.uint8)
        c_max = np.array(grain_color_range[1], dtype=np.uint8)
        color = tuple(rng.randint(c_min[i], c_max[i] + 1) for i in range(3))

        # 随机形状：圆形或轻微椭圆
        ellipse_ratio = rng.uniform(0.7, 1.3)
        axes = (int(r * ellipse_ratio), int(r / ellipse_ratio))
        angle = rng.randint(0, 180)

        cv2.ellipse(img, (cx, cy), axes, angle, 0, 360, color, -1)

        # 添加内部纹理（模拟矿石表面）
        texture_strength = rng.randint(5, 20)
        roi_y1 = max(0, cy - r)
        roi_y2 = min(height, cy + r)
        roi_x1 = max(0, cx - r)
        roi_x2 = min(width, cx + r)
        texture = rng.randint(-texture_strength, texture_strength + 1,
                               (roi_y2 - roi_y1, roi_x2 - roi_x1, 3)).astype(np.int16)
        img[roi_y1:roi_y2, roi_x1:roi_x2] = np.clip(
            img[roi_y1:roi_y2, roi_x1:roi_x2].astype(np.int16) + texture, 0, 255
        ).astype(np.uint8)

    # 添加高斯噪声
    if noise_level > 0:
        noise = rng.normal(0, noise_level, (height, width, 3)).astype(np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    # 轻微模糊（模拟相机成像）
    img = cv2.GaussianBlur(img, (3, 3), 0.5)

    return img


def main():
    """生成多张不同参数的测试图像"""
    configs = [
        {"name": "normal_grinding", "n": 35, "min_r": 10, "max_r": 40, "seed": 42,
         "desc": "正常磨矿（P80~120μm）"},
        {"name": "overground", "n": 60, "min_r": 5, "max_r": 25, "seed": 77,
         "desc": "过磨状态（细颗粒多，P80~55μm）"},
        {"name": "underground", "n": 15, "min_r": 20, "max_r": 55, "seed": 123,
         "desc": "欠磨状态（粗颗粒多，P80~220μm）"},
        {"name": "dense_mixture", "n": 50, "min_r": 6, "max_r": 45, "seed": 2024,
         "desc": "密集混合粒度分布"},
    ]

    for cfg in configs:
        img = generate_grain_image(
            n_grains=cfg["n"],
            min_radius=cfg["min_r"],
            max_radius=cfg["max_r"],
            seed=cfg["seed"],
        )
        path = os.path.join(OUTPUT_DIR, f'{cfg["name"]}.jpg')
        cv2.imencode('.jpg', img)[1].tofile(path)
        print(f"✅ {cfg['name']}: {cfg['desc']} → {path}")

    print(f"\n📁 测试图像已保存到: {os.path.abspath(OUTPUT_DIR)}")


if __name__ == "__main__":
    main()
