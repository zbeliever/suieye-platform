"""ROI 提取 Filter：支持多种裁剪策略。

方法：
  - foam: 浮选泡沫区域提取（去黑背景）
  - center_crop: 中心裁剪
  - rect: 指定矩形区域
  - full_frame: 使用整幅图像（安全巡检等场景）
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from backend.algorithms.markers import extract_foam_roi
from backend.engines.base import Engine, EngineContext

logger = logging.getLogger(__name__)


class RoiExtractEngine(Engine):
    name = "roi_extract"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        img = ctx.raw_image
        if img is None:
            logger.warning("[roi_extract] 无原始图像")
            return ctx

        method = params.get("method", "foam")

        if method == "foam":
            cropped, bbox = extract_foam_roi(img)
            ctx.cropped_image = cropped
            ctx.bbox = [int(v) for v in bbox]

        elif method == "center_crop":
            w = params.get("width", 640)
            h = params.get("height", 480)
            ih, iw = img.shape[:2]
            x1 = max(0, (iw - w) // 2)
            y1 = max(0, (ih - h) // 2)
            x2 = min(iw, x1 + w)
            y2 = min(ih, y1 + h)
            ctx.cropped_image = img[y1:y2, x1:x2].copy()
            ctx.bbox = [x1, y1, x2, y2]

        elif method == "rect":
            rect = params.get("rect", [0, 0, img.shape[1], img.shape[0]])
            x1, y1, x2, y2 = [int(v) for v in rect]
            # 边界保护
            ih, iw = img.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(iw, x2), min(ih, y2)
            if x2 > x1 and y2 > y1:
                ctx.cropped_image = img[y1:y2, x1:x2].copy()
                ctx.bbox = [x1, y1, x2, y2]
            else:
                logger.warning(f"[roi_extract] rect 参数无效: {rect}, 使用原图")
                ctx.cropped_image = img.copy()
                ctx.bbox = [0, 0, iw, ih]

        elif method == "full_frame":
            # 安全巡检：使用整幅图像，不做裁剪
            ctx.cropped_image = img.copy()
            ctx.bbox = [0, 0, img.shape[1], img.shape[0]]

        else:
            logger.warning(f"[roi_extract] 未知方法: {method}，默认使用 foam")
            cropped, bbox = extract_foam_roi(img)
            ctx.cropped_image = cropped
            ctx.bbox = [int(v) for v in bbox]

        logger.debug(
            f"[roi_extract] method={method}, "
            f"output_size={ctx.cropped_image.shape[:2] if ctx.cropped_image is not None else None}"
        )
        return ctx

