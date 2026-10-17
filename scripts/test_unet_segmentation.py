"""U-Net 泡沫分割测试脚本。

验证：
1. 模型加载（随机权重 / 预训练权重）
2. 推理流程（图像 → 概率图 → 标签 → 可视化）
3. 与 Felzenszwalb 的视觉对比
4. 推理速度 benchmark

用法：
  # 基础测试（随机权重，合成图像）
  python scripts/test_unet_segmentation.py

  # 用预训练权重测试
  python scripts/test_unet_segmentation.py --weights backend/models/weights/unet_foam_best.pth

  # 对比 Felzenszwalb
  python scripts/test_unet_segmentation.py --compare
"""

import os
import sys
import time
import argparse

import numpy as np
import cv2

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, ".."))


def generate_test_image(img_size=(256, 256), seed=42):
    """生成一张测试用泡沫图像。"""
    from backend.data.synthetic_foam_generator import generate_synthetic_foam_image
    img, mask = generate_synthetic_foam_image(
        img_size=img_size,
        n_bubbles=(15, 30),
        bubble_size_range=(10, 45),
        gap_darkness=0.4,
        blur_level=1.5,
        seed=seed,
    )
    return img, mask


def test_unet_inference(weights_path=None, device="cpu", save_dir=None):
    """Test U-Net inference end-to-end."""
    print("=" * 60)
    print("U-Net Segmentation Test")
    print("=" * 60)

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    # Generate test image
    print("\n[1] Generating test image...")
    img, gt_mask = generate_test_image(seed=42)
    print(f"   Image shape: {img.shape}, GT mask range: [{gt_mask.min()}, {gt_mask.max()}]")

    # Save ground truth
    if save_dir:
        cv2.imwrite(os.path.join(save_dir, "01_input.png"), img)
        cv2.imwrite(os.path.join(save_dir, "02_gt_mask.png"), gt_mask)

    # Load model and run inference
    print("\n[2] Loading U-Net model...")
    t0 = time.time()
    from backend.engines.filters.segment_unet import _get_unet_model, _unet_predict, _probability_to_labels
    model = _get_unet_model(weights_path, device)
    load_time = time.time() - t0
    print(f"   Model loaded in {load_time*1000:.1f}ms")

    # Inference (warmup + timed)
    print("\n[3] Running inference (warmup)...")
    _ = _unet_predict(model, img, device)  # warmup

    print("[4] Running inference (timed)...")
    times = []
    for i in range(5):
        t0 = time.time()
        prob_map = _unet_predict(model, img, device)
        t = time.time() - t0
        times.append(t)
    avg_time = np.mean(times) * 1000
    print(f"   Inference time: {avg_time:.1f}ms avg ({len(times)} runs)")
    print(f"   Probability map: shape={prob_map.shape}, range=[{prob_map.min():.3f}, {prob_map.max():.3f}]")

    # Convert to labels
    print("\n[5] Converting probability to labels...")
    labels, fg_mask, n_regions = _probability_to_labels(prob_map, threshold=0.5)
    print(f"   Regions detected: {n_regions}")
    print(f"   Foreground ratio: {(fg_mask > 0).mean():.4f}")
    print(f"   Labels range: [{labels.min()}, {labels.max()}]")

    # Save outputs
    if save_dir:
        prob_vis = (prob_map * 255).astype(np.uint8)
        prob_color = cv2.applyColorMap(prob_vis, cv2.COLORMAP_JET)
        cv2.imwrite(os.path.join(save_dir, "03_unet_probability.png"), prob_color)

        overlay = img.copy()
        green_overlay = np.zeros_like(img)
        green_overlay[fg_mask > 0] = [0, 255, 0]
        overlay = cv2.addWeighted(overlay, 0.5, green_overlay, 0.5, 0)
        cv2.imwrite(os.path.join(save_dir, "04_unet_overlay.png"), overlay)

        # Colored labels
        from backend.utils.image_utils import colorize_labels
        colored = colorize_labels(labels)
        cv2.imwrite(os.path.join(save_dir, "05_unet_labels.png"), colored)

        print(f"\n   Outputs saved to {save_dir}/")

    # Compute metrics against GT (if using trained model with meaningful output)
    if n_regions > 0:
        pred_binary = (prob_map > 0.5).astype(np.float32)
        gt_binary = (gt_mask > 127).astype(np.float32)
        intersection = (pred_binary * gt_binary).sum()
        union = pred_binary.sum() + gt_binary.sum() - intersection
        iou = intersection / (union + 1e-8)
        dice = 2 * intersection / (pred_binary.sum() + gt_binary.sum() + 1e-8)
        print(f"\n[6] Metrics vs GT:")
        print(f"   IoU:  {iou:.4f}")
        print(f"   Dice: {dice:.4f}")

    print("\n" + "=" * 60)
    print("U-Net test PASSED")
    print("=" * 60)
    return {
        "load_time_ms": load_time * 1000,
        "inference_ms": avg_time,
        "n_regions": n_regions,
        "fg_ratio": float((fg_mask > 0).mean()),
    }


def compare_with_felzenszwalb(device="cpu", save_dir=None):
    """Visual comparison: U-Net vs Felzenszwalb."""
    print("\n" + "=" * 60)
    print("Comparison: U-Net vs Felzenszwalb")
    print("=" * 60)

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    # Generate test image
    img, gt_mask = generate_test_image(seed=99)
    if save_dir:
        cv2.imwrite(os.path.join(save_dir, "cmp_input.png"), img)

    # --- Felzenszwalb ---
    print("\n[Felzenszwalb] Running segmentation...")
    t0 = time.time()
    from skimage.segmentation import felzenszwalb as sk_fz
    fz_labels = sk_fz(img, scale=100, sigma=0.8, min_size=1000)
    fz_time = (time.time() - t0) * 1000
    fz_n = len(np.unique(fz_labels)) - (1 if 0 in fz_labels else 0)
    print(f"   Time: {fz_time:.1f}ms, Regions: {fz_n}")

    if save_dir:
        from backend.utils.image_utils import colorize_labels, draw_boundary_overlay
        fz_colored = colorize_labels(fz_labels.astype(np.int32))
        fz_overlay = draw_boundary_overlay(img, fz_labels.astype(np.int32))
        cv2.imwrite(os.path.join(save_dir, "cmp_felzenszwalb_labels.png"), fz_colored)
        cv2.imwrite(os.path.join(save_dir, "cmp_felzenszwalb_overlay.png"), fz_overlay)

    # --- U-Net ---
    print("\n[U-Net] Running segmentation...")
    t0 = time.time()
    from backend.engines.filters.segment_unet import _get_unet_model, _unet_predict, _probability_to_labels
    model = _get_unet_model(None, device)
    prob = _unet_predict(model, img, device)
    unet_labels, unet_mask, unet_n = _probability_to_labels(prob, threshold=0.5)
    unet_time = (time.time() - t0) * 1000
    print(f"   Time: {unet_time:.1f}ms, Regions: {unet_n}")

    if save_dir:
        from backend.utils.image_utils import colorize_labels, draw_boundary_overlay
        unet_colored = colorize_labels(unet_labels)
        unet_overlay = draw_boundary_overlay(img, unet_labels, unet_mask)
        unet_prob_color = cv2.applyColorMap((prob * 255).astype(np.uint8), cv2.COLORMAP_JET)
        cv2.imwrite(os.path.join(save_dir, "cmp_unet_labels.png"), unet_colored)
        cv2.imwrite(os.path.join(save_dir, "cmp_unet_overlay.png"), unet_overlay)
        cv2.imwrite(os.path.join(save_dir, "cmp_unet_probability.png"), unet_prob_color)

    # Summary table
    print("\n" + "-" * 50)
    print(f"{'Method':<20} {'Time(ms)':<12} {'Regions':<10}")
    print("-" * 50)
    print(f"{'Felzenszwalb':<20} {fz_time:<12.1f} {fz_n:<10}")
    print(f"{'U-Net (random)':<20} {unet_time:<12.1f} {unet_n:<10}")
    print("-" * 50)

    if save_dir:
        print(f"\nComparison images saved to {save_dir}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test U-Net foam segmentation")
    parser.add_argument("--weights", type=str, default=None, help="Path to model weights")
    parser.add_argument("--device", type=str, default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--compare", action="store_true", help="Run comparison with Felzenszwalb")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to save outputs")
    args = parser.parse_args()

    output_dir = args.output_dir or os.path.join(PROJECT_ROOT, "..", "outputs", "unet_test")
    output_dir = os.path.abspath(output_dir)

    result = test_unet_inference(
        weights_path=args.weights,
        device=args.device,
        save_dir=output_dir,
    )

    if args.compare:
        compare_with_felzenszwalb(device=args.device, save_dir=os.path.join(output_dir, "comparison"))
