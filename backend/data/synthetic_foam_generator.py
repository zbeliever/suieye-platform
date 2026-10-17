"""合成泡沫数据生成器 —— 用于 U-Net 预训练/快速验证。

在没有真实标注数据时，用程序生成的泡沫图像 + 完美 mask 训练 U-Net，
让模型先学会"什么是泡沫的视觉特征"，再用少量真实数据微调。

生成策略：
1. 背景：Perlin 噪声 / 高斯模糊模拟矿浆纹理
2. 气泡：Voronoi 图 + 圆形变形，模拟真实泡沫堆积
3. 边界：暗缝隙（气泡间），亮度渐变
4. 增强：光照变化、颜色偏移、噪声注入
"""

import os
import numpy as np
import cv2
from scipy.ndimage import gaussian_filter, binary_dilation
from typing import Tuple


def _perlin_noise_2d(shape: Tuple[int, int], scale: float = 100.0, octaves: int = 4) -> np.ndarray:
    """简化版 Perlin 噪声（用多尺度高斯模糊叠加近似）。"""
    noise = np.zeros(shape, dtype=np.float32)
    for i in range(octaves):
        freq = 2 ** i
        amp = 1.0 / freq
        rand = np.random.randn(
            (shape[0] - 1) // freq + 2,
            (shape[1] - 1) // freq + 2,
        ).astype(np.float32)
        # 上采样到目标尺寸
        h, w = shape
        small_h, small_w = rand.shape
        upsampled = cv2.resize(rand, (w, h), interpolation=cv2.INTER_CUBIC)
        noise += upsampled * amp * scale

    # 归一化到 [0, 1]
    noise = (noise - noise.min()) / (noise.max() - noise.min() + 1e-8)
    return noise


def generate_synthetic_foam_image(
    img_size: Tuple[int, int] = (256, 256),
    n_bubbles: int = (15, 40),
    bubble_size_range: Tuple[int, int] = (8, 50),
    bg_color_range: Tuple[int, int] = (60, 120),
    bubble_color_range: Tuple[int, int] = (160, 230),
    gap_darkness: float = 0.4,
    blur_level: float = 1.5,
    seed: int | None = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """生成一张合成泡沫图像及其完美分割 mask。

    Args:
        img_size: (H, W) 输出图像尺寸
        n_bubbles: 气泡数量范围 (min, max)
        bubble_size_range: 气泡半径范围 (min, max)
        bg_color_range: 背景灰度范围
        bubble_color_range: 气泡灰度范围
        gap_darkness: 缝隙暗度系数 (0=无暗缝, 1=纯黑缝)
        blur_level: 高斯模糊 sigma
        seed: 随机种子（可复现）

    Returns:
        (image_bgr, mask_uint8) — image 是 BGR 三通道, mask 是单通道 0/255
    """
    if seed is not None:
        np.random.seed(seed)

    h, w = img_size
    n = np.random.randint(*n_bubbles)

    # === 1. 生成背景纹理 ===
    bg_noise = _perlin_noise_2d((h, w), scale=80, octaves=3)
    bg_base = np.random.randint(*bg_color_range)
    background = (bg_noise * 30 + bg_base).clip(0, 255).astype(np.uint8)

    # === 2. 放置气泡 ===
    mask = np.zeros((h, w), dtype=np.uint8)
    image = np.full((h, w, 3), int(bg_base), dtype=np.uint8)

    # 用颜色变化增加真实感
    color_shift = np.random.randint(-20, 20, 3).astype(np.int16)

    bubbles_placed = 0
    max_attempts = n * 50
    attempts = 0

    while bubbles_placed < n and attempts < max_attempts:
        attempts += 1
        cx = np.random.randint(20, w - 20)
        cy = np.random.randint(20, h - 20)
        r = np.random.randint(*bubble_size_range)

        # 检查是否与已有气泡重叠太多
        test_mask = np.zeros_like(mask)
        cv2.circle(test_mask, (cx, cy), r, 255, -1)

        overlap = (test_mask > 0) & (mask > 0)
        if overlap.sum() > r * r * 0.3:  # 允许 30% 重叠（模拟粘连）
            continue

        # 绘制气泡
        brightness = np.random.randint(*bubble_color_range)
        color = np.clip(
            np.array([brightness, brightness, brightness], dtype=np.int16) + color_shift,
            0, 255
        ).astype(np.uint8)

        # 气泡主体（略有不规则）
        irregularity = np.random.uniform(0.85, 1.0)
        pts = []
        for angle in np.linspace(0, 2 * np.pi, 36):
            rr = r * irregularity * (1 + 0.1 * np.sin(angle * 6))
            px = int(cx + rr * np.cos(angle))
            py = int(cy + rr * np.sin(angle))
            pts.append([px, py])
        pts = np.array(pts, dtype=np.int32)
        cv2.fillPoly(image, [pts], color.tolist())
        cv2.fillPoly(mask, [pts], 255)

        # 气泡内部高光（偏右上角）
        hl_r = int(r * 0.3)
        hl_cx = cx + int(r * 0.25)
        hl_cy = cy - int(r * 0.25)
        hl_color = np.clip(color.astype(np.int16) + 40, 0, 255).astype(np.uint8)
        cv2.circle(image, (hl_cx, hl_cy), hl_r, hl_color.tolist(), -1)
        cv2.circle(mask, (hl_cx, hl_cy), hl_r, 255, -1)

        bubbles_placed += 1

    # === 3. 生成暗缝隙（气泡边界）===
    if gap_darkness > 0 and bubbles_placed > 0:
        # 膨胀 mask 找边界区域
        kernel_size = max(3, int(np.mean(bubble_size_range) * 0.15))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        dilated = cv2.dilate(mask, kernel, iterations=1)
        boundary = dilated - mask  # 只在原始 mask 外面一圈

        # 在边界处变暗
        dark_amount = int(80 * gap_darkness)
        image[boundary > 0] = np.clip(
            image[boundary > 0].astype(np.int16) - dark_amount, 0, 255
        ).astype(np.uint8)

        # 气泡之间的接触线也稍微变暗（模拟表面张力）
        eroded = cv2.erode(mask, kernel, iterations=1)
        inner_boundary = mask - eroded
        image[inner_boundary > 0] = np.clip(
            image[inner_boundary > 0].astype(np.int16) - int(dark_amount * 0.5), 0, 255
        ).astype(np.uint8)

    # === 4. 叠加背景纹理 ===
    texture_blend = 0.3
    for c in range(3):
        image[:, :, c] = (
            image[:, :, c].astype(np.float32) * (1 - texture_blend)
            + background.astype(np.float32) * texture_blend
        ).clip(0, 255).astype(np.uint8)

    # === 5. 整体模糊 + 噪声 ===
    if blur_level > 0:
        image = cv2.GaussianBlur(image, (0, 0), blur_level)

    # 添加轻微噪声
    noise = np.random.randn(*image.shape) * 5
    image = np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    return image, mask


def generate_foam_dataset(
    output_dir: str,
    n_train: int = 200,
    n_val: int = 50,
    n_test: int = 50,
    img_size: Tuple[int, int] = (256, 256),
    seed: int = 42,
) -> dict:
    """生成完整的合成泡沫数据集。

    目录结构：
    output_dir/
      train/
        images/   xxx.png
        masks/    xxx.png
      val/
        images/
        masks/
      test/
        images/
        masks/

    Returns:
        统计信息 dict
    """
    rng = np.random.RandomState(seed)

    os.makedirs(os.path.join(output_dir, "train", "images"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "train", "masks"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "val", "images"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "val", "masks"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "test", "images"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "test", "masks"), exist_ok=True)

    def _generate_split(n, split_name):
        fg_ratios = []
        for i in range(n):
            s = rng.randint(0, 2**31)
            img, mask = generate_synthetic_foam_image(
                img_size=img_size,
                n_bubbles=(10, 45),
                bubble_size_range=(6, 55),
                gap_darkness=rng.uniform(0.2, 0.6),
                blur_level=rng.uniform(0.5, 2.5),
                seed=s,
            )
            img_path = os.path.join(output_dir, split_name, "images", f"{i:04d}.png")
            msk_path = os.path.join(output_dir, split_name, "masks", f"{i:04d}.png")
            cv2.imwrite(img_path, img)
            cv2.imwrite(msk_path, mask)
            fg_ratios.append(mask.mean() / 255.0)

        return {
            "count": n,
            "fg_ratio_mean": float(np.mean(fg_ratios)),
            "fg_ratio_std": float(np.std(fg_ratios)),
        }

    train_stats = _generate_split(n_train, "train")
    val_stats = _generate_split(n_val, "val")
    test_stats = _generate_split(n_test, "test")

    stats = {
        "output_dir": output_dir,
        "img_size": img_size,
        "total": n_train + n_val + n_test,
        "train": train_stats,
        "val": val_stats,
        "test": test_stats,
    }

    # Save stats
    import json
    with open(os.path.join(output_dir, "dataset_stats.json"), "w") as f:
        json.dump(stats, f, indent=2)

    print(f"Dataset generated: {output_dir}")
    print(f"  Train: {n_train} images (fg ratio: {train_stats['fg_ratio_mean']:.3f} ± {train_stats['fg_ratio_std']:.3f})")
    print(f"  Val:   {n_val} images (fg ratio: {val_stats['fg_ratio_mean']:.3f} ± {val_stats['fg_ratio_std']:.3f})")
    print(f"  Test:  {n_test} images (fg ratio: {test_stats['fg_ratio_mean']:.3f} ± {test_stats['fg_ratio_std']:.3f})")

    return stats


if __name__ == "__main__":
    # Quick demo: generate a few samples and show them
    print("=== Synthetic Foam Data Generator Demo ===\n")

    output_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "data", "synthetic_foam"
    )
    output_dir = os.path.abspath(output_dir)

    # Generate small dataset for testing
    stats = generate_foam_dataset(
        output_dir=output_dir,
        n_train=30,
        n_val=10,
        n_test=10,
        img_size=(256, 256),
        seed=42,
    )

    print("\nDemo: showing first training sample...")
    img = cv2.imread(os.path.join(output_dir, "train", "images", "0000.png"))
    mask = cv2.imread(os.path.join(output_dir, "train", "masks", "0000.png"), cv2.IMREAD_GRAYSCALE)

    overlay = img.copy()
    overlay[mask > 0] = cv2.addWeighted(
        img[mask > 0], 0.5, np.array([0, 255, 0], dtype=np.uint8), 0.5, 0
    )
    demo_path = os.path.join(output_dir, "demo_overlay.png")
    cv2.imwrite(demo_path, overlay)
    print(f"Demo saved to: {demo_path}")
