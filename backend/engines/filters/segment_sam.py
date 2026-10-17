"""SAM (Segment Anything Model) 分割引擎。

零样本/少样本泡沫分割，质量远超经典分水岭。
首次调用时自动从 Meta 下载权重（vit_b ~375MB / vit_h ~2.5GB）。

用法：在 flotation.yaml 中将 segment 的 method 改为 "sam" 即可切换。
"""

import os
import numpy as np
import cv2
from typing import Optional

from backend.engines.base import Engine, EngineContext
from backend.utils.image_utils import (
    build_probability_map,
    colorize_labels,
    draw_boundary_overlay,
)

# Lazy import — segment-anything is heavy, only load when SAM is actually used
_sam_model = None
_sam_model_type = None


def _get_sam_model(model_type: str = "vit_b"):
    """Load or return cached SAM model. Downloads weights on first call."""
    global _sam_model, _sam_model_type
    if _sam_model is not None and _sam_model_type == model_type:
        return _sam_model

    from segment_anything import sam_model_registry, SamPredictor

    # Model URLs from Meta
    url_map = {
        "vit_h": "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth",
        "vit_l": "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_l_0b3195.pth",
        "vit_b": "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth",
    }

    if model_type not in url_map:
        raise ValueError(f"Unsupported SAM model: {model_type}. Choose from {list(url_map)}")

    print(f"[SAM] Loading {model_type} (first run downloads ~{'375MB' if model_type=='vit_b' else '2.5GB' if model_type=='vit_h' else '1.2GB'})...")
    sam = sam_model_registry[model_type]()  # auto-downloads weights on first call
    predictor = SamPredictor(sam)
    _sam_model = predictor
    _sam_model_type = model_type
    print(f"[SAM] {model_type} loaded OK")
    return predictor


class SAMSegmentEngine(Engine):
    name = "segment_sam"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        model_type = params.get("model_type", "vit_b")
        points_per_side = params.get("points_per_side", 32)
        pred_iou_thresh = params.get("pred_iou_thresh", 0.86)
        stability_score_thresh = params.get("stability_score_thresh", 0.92)
        min_mask_region_area = params.get("min_mask_region_area", 1000)

        predictor = _get_sam_model(model_type)

        # SAM expects RGB, not BGR
        rgb = cv2.cvtColor(ctx.preprocessed, cv2.COLOR_BGR2RGB)

        # Automatic mask generation — grid-based, no prompts needed
        from segment_anything import SamAutomaticMaskGenerator

        mask_generator = SamAutomaticMaskGenerator(
            predictor.model,
            points_per_side=points_per_side,
            pred_iou_thresh=pred_iou_thresh,
            stability_score_thresh=stability_score_thresh,
            min_mask_region_area=min_mask_region_area,
        )

        results = mask_generator.generate(rgb)

        if not results:
            # Fallback to watershed if SAM finds nothing
            from backend.engines.filters.segment import SegmentEngine
            fallback = SegmentEngine()
            return fallback.run(ctx, {"method": "watershed"})

        # Build label map from SAM masks (sorted by area, descending)
        h, w = ctx.preprocessed.shape[:2]
        labels = np.zeros((h, w), dtype=np.int32)
        fg_mask = np.zeros((h, w), dtype=np.uint8)

        # Sort by area descending so larger bubbles get lower (more stable) labels
        results.sort(key=lambda r: r["area"], reverse=True)

        for idx, r in enumerate(results, start=1):
            m = r["segment"]
            labels[m] = idx
            fg_mask[m] = 255

        # Write back to context
        ctx.labels = labels
        ctx.fg_mask = fg_mask
        n_regions = len(results)
        ctx.metrics["n_foams"] = n_regions
        ctx.metrics["foreground_ratio"] = round(
            float((fg_mask > 0).mean()), 4
        )
        ctx.metrics["sam_model"] = model_type
        ctx.metrics["sam_masks"] = len(results)

        # Visualizations
        ctx.overlays["boundary_overlay"] = draw_boundary_overlay(
            ctx.cropped_image, labels, fg_mask
        )
        ctx.overlays["probability_map"] = build_probability_map(labels, fg_mask)
        ctx.overlays["colored_labels"] = colorize_labels(labels)

        return ctx
