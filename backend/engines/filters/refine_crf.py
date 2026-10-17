"""CRF 边界精修 Filter —— 在分割输出上跑 DenseCRF 提升边界精度。

两种输入模式（params.source）：
- "probability"（推荐）：读上游 segment_unet 存的 ctx.overlays["unet_raw_prob"]
  概率图做 unary（unary_from_softmax），CRF 发挥最充分。
- "mask"：读 ctx.fg_mask 硬标签做 unary（unary_from_labels），
  可精修任意分割方法（Felzenszwalb / Watershed / ...）的输出。

flotation.yaml 用法（接在 segment 之后）::

    - name: segment
      params:
        method: unet
    - name: refine_crf
      params:
        source: probability
        iterations: 5

实现依赖 backend/algorithms/densecrf_np.py（自研 numpy 版 DenseCRF，
pydensecrf 在 Windows+py3.13 无二进制，见该文件头部说明）。
"""

import time

import numpy as np
import cv2

from backend.engines.base import Engine, EngineContext
from backend.algorithms.densecrf_np import (
    DenseCRF2D,
    unary_from_softmax,
    unary_from_labels,
)
from backend.utils.image_utils import draw_boundary_overlay, colorize_labels
from backend.engines.filters.segment_unet import (
    _probability_to_labels,
    _filter_small_regions,
)


class CRFRefineEngine(Engine):
    """CRF 精修 Filter。

    Pipeline integration:
      - Input: ctx.labels / ctx.fg_mask / ctx.overlays["unet_raw_prob"]
      - Output: ctx.labels, ctx.fg_mask（精修后），ctx.metrics, ctx.overlays
    """

    name = "refine_crf"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        t0 = time.time()

        # ---- 参数 ----
        # bilat_compat：3~5 保守（干净概率图/已高精度时负漂移小），
        #               10 激进（噪声大/边界糊时吸附边缘更强）
        source = params.get("source", "probability")
        iterations = int(params.get("iterations", 5))
        threshold = float(params.get("threshold", 0.5))
        min_region_area = int(params.get("min_region_area", 100))
        gauss_sxy = float(params.get("gauss_sxy", 3))
        gauss_compat = float(params.get("gauss_compat", 3))
        bilat_sxy = float(params.get("bilat_sxy", 80))
        bilat_srgb = float(params.get("bilat_srgb", 13))
        bilat_compat = float(params.get("bilat_compat", 5))
        gt_prob = float(params.get("gt_prob", 0.8))  # mask 模式下的标签置信度

        # ---- 引导图（与 segment_unet 相同的输入链）----
        # 注意：numpy 数组不能用 or（已知坑 2）
        img = ctx.cropped_image if ctx.cropped_image is not None else (
            ctx.preprocessed if ctx.preprocessed is not None else ctx.raw_image
        )
        if img is None:
            ctx.metrics["crf_applied"] = False
            ctx.metrics["crf_skip_reason"] = "no_input_image"
            return ctx

        h, w = img.shape[:2]
        prev_mask = ctx.fg_mask

        # ---- 构建 unary ----
        prob_u8 = ctx.overlays.get("unet_raw_prob")
        if source == "probability" and prob_u8 is not None:
            p = (np.asarray(prob_u8, dtype=np.float32) / 255.0).reshape(h, w)
            probs = np.stack([1.0 - p, p], axis=0)  # (2, H, W)
            U = unary_from_softmax(probs, clip=1e-5)
            used_source = "probability"
        elif prev_mask is not None:
            # 回退/显式 mask 模式：硬标签 → unary
            labels_in = (np.asarray(prev_mask) > 0).astype(np.int32)
            U = unary_from_labels(labels_in, nlabels=2, gt_prob=gt_prob)
            used_source = "mask"
        else:
            ctx.metrics["crf_applied"] = False
            ctx.metrics["crf_skip_reason"] = "no_prob_no_mask"
            return ctx

        # ---- DenseCRF 推理 ----
        crf = DenseCRF2D(w, h, 2)
        crf.setUnaryEnergy(U)
        crf.addPairwiseGaussian(sxy=gauss_sxy, compat=gauss_compat)
        crf.addPairwiseBilateral(
            sxy=bilat_sxy, srgb=bilat_srgb, rgbim=img, compat=bilat_compat
        )
        Q = crf.inference(iterations)
        refined_prob = Q[1].reshape(h, w)

        # ---- 概率 → 标签（复用 U-Net 的连通域逻辑）----
        labels, fg_mask, n_regions = _probability_to_labels(refined_prob, threshold)
        if min_region_area > 0 and n_regions > 0:
            labels, fg_mask, n_regions = _filter_small_regions(
                labels, fg_mask, min_region_area
            )

        # ---- 变化统计 ----
        changed_ratio = 0.0
        if prev_mask is not None:
            prev_binary = (np.asarray(prev_mask) > 0)
            new_binary = (fg_mask > 0)
            changed_ratio = float((prev_binary != new_binary).mean())

        # ---- 写回 context ----
        ctx.labels = labels
        ctx.fg_mask = fg_mask
        ctx.metrics["n_foams"] = n_regions
        ctx.metrics["foreground_ratio"] = round(float((fg_mask > 0).mean()), 4)
        ctx.metrics["segment_method"] = f"{ctx.metrics.get('segment_method', 'seg')}+crf"
        ctx.metrics["crf_applied"] = True
        ctx.metrics["crf_source"] = used_source
        ctx.metrics["crf_iterations"] = iterations
        ctx.metrics["crf_time_ms"] = round((time.time() - t0) * 1000, 1)
        ctx.metrics["crf_changed_pixel_ratio"] = round(changed_ratio, 4)

        # ---- 可视化 ----
        if prev_mask is not None and prev_mask.shape[:2] == (h, w):
            # 保留精修前的边界叠加图供对比
            if "boundary_overlay" in ctx.overlays:
                ctx.overlays["crf_before_boundary"] = ctx.overlays["boundary_overlay"]
            # 变化图：绿 = 新增前景，红 = 移除前景
            diff_vis = img.copy()
            prev_binary = np.asarray(prev_mask) > 0
            new_binary = fg_mask > 0
            added = (~prev_binary) & new_binary
            removed = prev_binary & (~new_binary)
            diff_vis[added] = (0, 200, 0)
            diff_vis[removed] = (0, 0, 255)
            ctx.overlays["crf_diff"] = diff_vis

        ctx.overlays["boundary_overlay"] = draw_boundary_overlay(img, labels, fg_mask)
        ctx.overlays["colored_labels"] = colorize_labels(labels)
        prob_uint8 = np.clip(refined_prob * 255, 0, 255).astype(np.uint8)
        ctx.overlays["crf_refined_prob"] = prob_uint8
        ctx.overlays["probability_map"] = cv2.applyColorMap(
            prob_uint8, cv2.COLORMAP_JET
        )

        return ctx
