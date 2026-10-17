"""CRF 边界精修测试脚本。

验证：
1. DenseCRF2D（numpy 版）基础正确性（概率守恒、二类互补）
2. U-Net 输出 + CRF 精修 vs U-Net 直接输出的 IoU / Dice / 边界 IoU
3. 噪声扰动下的边界恢复能力（模拟真实场景的模糊边界）
4. mask 模式（unary_from_labels，可精修 Felzenszwalb 等经典输出）
5. Filter 链集成（segment_unet → refine_crf，经 filters/__init__ 注册）

用法：
  python scripts/test_crf_refinement.py --weights backend/models/weights/unet_foam_best.pth
  python scripts/test_crf_refinement.py --compare  # 含噪声扰动对比
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
    """生成一张测试用泡沫图像（与 test_unet_segmentation.py 相同）。"""
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


def iou_dice(pred_binary, gt_binary):
    inter = (pred_binary * gt_binary).sum()
    union = pred_binary.sum() + gt_binary.sum() - inter
    iou = inter / (union + 1e-8)
    dice = 2 * inter / (pred_binary.sum() + gt_binary.sum() + 1e-8)
    return float(iou), float(dice)


def boundary_iou(pred_mask, gt_mask, band=2):
    """边界 IoU：GT 边界带与预测边界带的交并比（HANDOFF 提到的'边界精度'）。"""
    def edge_band(m):
        m = (np.asarray(m) > 0).astype(np.uint8)
        k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        e = cv2.morphologyEx(m, cv2.MORPH_GRADIENT, k3)
        kband = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1, 2 * band + 1))
        return cv2.dilate(e, kband) > 0

    pe, ge = edge_band(pred_mask), edge_band(gt_mask)
    inter = (pe & ge).sum()
    union = (pe | ge).sum()
    return float(inter / (union + 1e-8))


def make_montage(img, gt_mask, before_mask, after_mask, labels=("U-Net", "U-Net + CRF")):
    """四联图：原图 / GT / 精修前 / 精修后。"""
    h, w = img.shape[:2]
    panel_h = h + 30

    def titled(vis, title):
        canvas = np.full((panel_h, w, 3), 20, np.uint8)
        canvas[:h] = vis
        cv2.putText(canvas, title, (6, h + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        return canvas

    def overlay_mask(base, mask, color):
        out = base.copy()
        m = np.asarray(mask) > 0
        layer = np.zeros_like(out)
        layer[m] = color
        return cv2.addWeighted(out, 0.5, layer, 0.5, 0)

    gt_vis = overlay_mask(img, gt_mask, (255, 255, 0))
    before_vis = overlay_mask(img, before_mask, (0, 255, 0))
    after_vis = overlay_mask(img, after_mask, (0, 255, 0))

    panels = [
        titled(img, "Input"),
        titled(gt_vis, "Ground Truth"),
        titled(before_vis, labels[0]),
        titled(after_vis, labels[1]),
    ]
    return np.hstack(panels)


def test_crf_basics():
    """基础正确性：概率和为 1、二类互补、退化参数下不崩溃。"""
    print("\n" + "=" * 60)
    print("[Test 0] DenseCRF2D basic sanity")
    print("=" * 60)
    from backend.algorithms.densecrf_np import (
        DenseCRF2D, unary_from_softmax, unary_from_labels,
    )

    rng = np.random.default_rng(0)
    h, w = 64, 64
    p = rng.random((h, w)).astype(np.float32)
    U = unary_from_softmax(np.stack([1 - p, p]), clip=1e-5)

    crf = DenseCRF2D(w, h, 2)
    crf.setUnaryEnergy(U)
    crf.addPairwiseGaussian(sxy=3, compat=3)
    crf.addPairwiseBilateral(
        sxy=10, srgb=13,
        rgbim=rng.integers(0, 255, (h, w, 3), dtype=np.uint8), compat=10,
    )
    t0 = time.time()
    Q = crf.inference(5)
    dt = (time.time() - t0) * 1000

    Q2d = Q.reshape(2, h, w)
    s = Q2d.sum(axis=0)
    print(f"   64x64 双边+高斯 5 iter: {dt:.1f}ms")
    print(f"   概率和 min/max: [{s.min():.6f}, {s.max():.6f}] (应≈1)")
    assert np.allclose(s, 1.0, atol=1e-4), "概率不守恒"
    assert Q2d.min() >= 0 and Q2d.max() <= 1

    # unary_from_labels: 确定标签的像素在 gt_prob 高时预测稳定
    labels = np.zeros((h, w), np.int32)
    labels[16:48, 16:48] = 1
    U2 = unary_from_labels(labels, 2, gt_prob=0.9)
    assert U2.shape == (2, h * w)
    assert U2[0][0] < U2[1][0] and U2[1][np.flatnonzero(labels.ravel() == 1)[0]] < U2[0][np.flatnonzero(labels.ravel() == 1)[0]]
    print("   unary_from_labels 形状/语义 OK")
    print("   Basic sanity PASSED")
    return True


def run_unet(weights_path, img):
    from backend.engines.filters.segment_unet import _get_unet_model, _unet_predict
    model = _get_unet_model(weights_path, "cpu")
    return _unet_predict(model, img, "cpu")


def test_crf_refinement(weights_path=None, save_dir=None, seed=42, noise_std=0.0):
    """U-Net vs U-Net+CRF 对比主测试。noise_std>0 时对概率图加噪声模拟模糊边界。"""
    from backend.algorithms.densecrf_np import DenseCRF2D, unary_from_softmax

    tag = f"seed={seed}" + (f" noise={noise_std}" if noise_std else "")
    print(f"\n--- Run: {tag} ---")

    img, gt_mask = generate_test_image(seed=seed)
    gt_binary = gt_mask > 127

    # U-Net 概率
    prob = run_unet(weights_path, img)
    if noise_std > 0:
        rng = np.random.default_rng(seed + 1000)
        prob = np.clip(prob + rng.normal(0, noise_std, prob.shape).astype(np.float32), 0, 1)

    # ---- baseline：直接阈值 ----
    pred_before = (prob > 0.5)
    iou0, dice0 = iou_dice(pred_before.astype(np.float32), gt_binary.astype(np.float32))
    biou0 = boundary_iou(pred_before, gt_binary)

    # ---- CRF 精修 ----
    t0 = time.time()
    probs = np.stack([1 - prob, prob], axis=0)
    U = unary_from_softmax(probs, clip=1e-5)
    crf = DenseCRF2D(img.shape[1], img.shape[0], 2)
    crf.setUnaryEnergy(U)
    crf.addPairwiseGaussian(sxy=3, compat=3)
    crf.addPairwiseBilateral(sxy=80, srgb=13, rgbim=img, compat=5)
    Q = crf.inference(5)
    crf_ms = (time.time() - t0) * 1000
    refined_prob = Q[1].reshape(img.shape[:2])
    pred_after = (refined_prob > 0.5)
    iou1, dice1 = iou_dice(pred_after.astype(np.float32), gt_binary.astype(np.float32))
    biou1 = boundary_iou(pred_after, gt_binary)

    print(f"   IoU : {iou0:.4f} → {iou1:.4f}  ({(iou1 - iou0) * 100:+.2f} pp)")
    print(f"   Dice: {dice0:.4f} → {dice1:.4f}  ({(dice1 - dice0) * 100:+.2f} pp)")
    print(f"   边界IoU: {biou0:.4f} → {biou1:.4f}  ({(biou1 - biou0) * 100:+.2f} pp)")
    print(f"   CRF 耗时: {crf_ms:.1f}ms (5 iter)")

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        prefix = f"seed{seed}" + (f"_noise{noise_std}" if noise_std else "")
        cv2.imwrite(os.path.join(save_dir, f"{prefix}_input.png"), img)
        cv2.imwrite(os.path.join(save_dir, f"{prefix}_gt.png"), gt_mask)
        cv2.imwrite(
            os.path.join(save_dir, f"{prefix}_prob.png"),
            cv2.applyColorMap((prob * 255).astype(np.uint8), cv2.COLORMAP_JET),
        )
        cv2.imwrite(
            os.path.join(save_dir, f"{prefix}_refined_prob.png"),
            cv2.applyColorMap((refined_prob * 255).astype(np.uint8), cv2.COLORMAP_JET),
        )
        montage = make_montage(img, gt_mask, pred_before, pred_after)
        cv2.imwrite(os.path.join(save_dir, f"{prefix}_montage.png"), montage)

    return {
        "iou": (iou0, iou1), "dice": (dice0, dice1),
        "biou": (biou0, biou1), "crf_ms": crf_ms,
    }


def test_mask_source(weights_path=None, save_dir=None):
    """mask 模式：unary_from_labels 精修硬标签（适用任意分割方法）。

    基线用 Otsu（经典阈值分割）。注：合成生成器偶尔产出暗泡沫图像
    （如 seed=42），Otsu 亮侧与 GT 相交近零，属基线失效而非 CRF 问题，
    故本测试固定选用亮泡沫 seed（7/99）。
    """
    from backend.algorithms.densecrf_np import DenseCRF2D, unary_from_labels

    print("\n" + "=" * 60)
    print("[Test 3] mask-source refinement (Otsu → CRF)")
    print("=" * 60)

    stats = []
    for seed in (7, 99):
        img, gt_mask = generate_test_image(seed=seed)
        gt_binary = gt_mask > 127

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        fg = otsu > 0  # 亮侧 = 泡沫

        iou0, _ = iou_dice(fg.astype(np.float32), gt_binary.astype(np.float32))
        biou0 = boundary_iou(fg, gt_mask)

        t0 = time.time()
        U = unary_from_labels(fg.astype(np.int32), 2, gt_prob=0.8)
        crf = DenseCRF2D(img.shape[1], img.shape[0], 2)
        crf.setUnaryEnergy(U)
        crf.addPairwiseGaussian(sxy=3, compat=3)
        crf.addPairwiseBilateral(sxy=80, srgb=13, rgbim=img, compat=5)
        refined = crf.inference(5)[1].reshape(img.shape[:2]) > 0.5
        crf_ms = (time.time() - t0) * 1000

        iou1, _ = iou_dice(refined.astype(np.float32), gt_binary.astype(np.float32))
        biou1 = boundary_iou(refined, gt_mask)
        print(f"   seed={seed} Otsu IoU: {iou0:.4f} → {iou1:.4f} ({(iou1 - iou0) * 100:+.2f} pp), "
              f"边界IoU: {biou0:.4f} → {biou1:.4f} ({(biou1 - biou0) * 100:+.2f} pp), {crf_ms:.0f}ms")
        stats.append((iou0, iou1, biou0, biou1))

        if save_dir and seed == 7:
            montage = make_montage(
                img, gt_mask, fg, refined, labels=("Otsu", "Otsu + CRF (mask mode)")
            )
            cv2.imwrite(os.path.join(save_dir, "mask_source_montage.png"), montage)

    m = np.mean(stats, axis=0)
    print(f"   均值: IoU {m[0]:.4f} → {m[1]:.4f} ({(m[1] - m[0]) * 100:+.2f} pp), "
          f"边界IoU {m[2]:.4f} → {m[3]:.4f} ({(m[3] - m[2]) * 100:+.2f} pp)")
    return {"iou": (m[0], m[1]), "biou": (m[2], m[3])}


def test_filter_chain(weights_path=None, save_dir=None):
    """Filter 链集成：segment_unet → refine_crf，走完整注册流程。"""
    print("\n" + "=" * 60)
    print("[Test 4] Filter chain integration (segment_unet → refine_crf)")
    print("=" * 60)

    # import 触发集中注册（filters/__init__.py）——同时验证无循环导入
    import backend.engines.filters  # noqa: F401
    from backend.engines.registry import get_engine, list_engines
    from backend.engines.base import EngineContext

    all_engines = list_engines()
    refine = get_engine("refine_crf")
    print(f"   Registry: {len(all_engines)} filters = {all_engines}")

    img, gt_mask = generate_test_image(seed=42)
    gt_binary = gt_mask > 127

    unet = get_engine("segment_unet")
    refine = get_engine("refine_crf")

    ctx = EngineContext(raw_image=img, cropped_image=img, scene="flotation")
    ctx = unet.run(ctx, {"weights_path": weights_path})
    iou0, _ = iou_dice((ctx.fg_mask > 0).astype(np.float32), gt_binary.astype(np.float32))
    biou0 = boundary_iou(ctx.fg_mask, gt_mask)
    print(f"   segment_unet: {ctx.metrics['n_foams']} regions, IoU={iou0:.4f}")

    ctx = refine.run(ctx, {"source": "probability", "iterations": 5})
    iou1, _ = iou_dice((ctx.fg_mask > 0).astype(np.float32), gt_binary.astype(np.float32))
    biou1 = boundary_iou(ctx.fg_mask, gt_mask)
    print(f"   refine_crf:   {ctx.metrics['n_foams']} regions, IoU={iou1:.4f}, "
          f"changed={ctx.metrics['crf_changed_pixel_ratio']:.4f}, "
          f"time={ctx.metrics['crf_time_ms']}ms")
    print(f"   IoU {iou0:.4f} → {iou1:.4f}, 边界IoU {biou0:.4f} → {biou1:.4f}")
    print(f"   overlays: {sorted(k for k in ctx.overlays if k.startswith('crf'))}")

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        before = ctx.overlays.get("crf_before_boundary", img)
        after = ctx.overlays["boundary_overlay"]
        h = img.shape[0]
        comp = np.hstack([before, after])
        cv2.putText(comp, "before CRF", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(comp, "after CRF", (h + 6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1, cv2.LINE_AA)
        cv2.imwrite(os.path.join(save_dir, "filter_chain_before_after.png"), comp)
        cv2.imwrite(os.path.join(save_dir, "filter_chain_diff.png"), ctx.overlays["crf_diff"])

    return {"iou": (iou0, iou1), "biou": (biou0, biou1)}


def main():
    parser = argparse.ArgumentParser(description="Test CRF boundary refinement")
    parser.add_argument("--weights", type=str,
                        default="backend/models/weights/unet_foam_best.pth")
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--compare", action="store_true",
                        help="add noise-perturbation runs")
    args = parser.parse_args()

    save_dir = args.output_dir or os.path.join(
        PROJECT_ROOT, "..", "outputs", "verify_crf"
    )
    save_dir = os.path.abspath(save_dir)
    os.makedirs(save_dir, exist_ok=True)

    print("=" * 60)
    print("CRF Boundary Refinement Test")
    print(f"Output dir: {save_dir}")
    print("=" * 60)

    ok = test_crf_basics()
    if not ok:
        sys.exit(1)

    # 多 seed 干净数据
    print("\n" + "=" * 60)
    print("[Test 1] Clean synthetic: U-Net vs U-Net + CRF")
    print("=" * 60)
    results = [test_crf_refinement(args.weights, save_dir, seed=s) for s in (42, 99, 2024)]

    if args.compare:
        print("\n" + "=" * 60)
        print("[Test 2] Noisy probability (simulated blurry boundaries)")
        print("=" * 60)
        for std in (0.1, 0.2, 0.3):
            test_crf_refinement(args.weights, save_dir, seed=42, noise_std=std)

    test_mask_source(args.weights, save_dir)
    test_filter_chain(args.weights, save_dir)

    # ---- 汇总 ----
    print("\n" + "=" * 60)
    print("SUMMARY (clean synthetic, %d seeds)" % len(results))
    print("=" * 60)
    print(f"{'Metric':<12} {'U-Net':>10} {'+CRF':>10} {'Δ':>10}")
    print("-" * 46)
    for key, name in (("iou", "IoU"), ("dice", "Dice"), ("biou", "边界IoU")):
        before = np.mean([r[key][0] for r in results])
        after = np.mean([r[key][1] for r in results])
        print(f"{name:<12} {before:>10.4f} {after:>10.4f} {(after - before) * 100:>+9.2f}pp")
    print(f"{'CRF耗时ms':<12} {'':>10} {np.mean([r['crf_ms'] for r in results]):>10.1f}")
    print(f"\n可视化输出已保存到: {save_dir}/")
    print("CRF refinement test DONE")


if __name__ == "__main__":
    main()
