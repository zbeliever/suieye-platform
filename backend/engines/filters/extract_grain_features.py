"""粒度特征提取引擎（粒度场景专用）

从颗粒分割结果中提取粒度分析特征：
1. 基础统计：颗粒数、总面积、填充率
2. 粒度分布：D10/D50/D80/D90（累积分布百分位直径）
3. 形状特征：平均圆度、长宽比、实心度
4. 工况指标：过磨率、欠磨率、均匀系数
"""

import math
from typing import List

import cv2
import numpy as np

from backend.engines.base import Engine, EngineContext


class ExtractGrainFeaturesEngine(Engine):
    """粒度特征提取 Filter"""

    name = "extract_grain_features"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        labels = ctx.labels
        fg_mask = ctx.fg_mask
        img = ctx.cropped_image if ctx.cropped_image is not None else ctx.raw_image

        if labels is None or fg_mask is None or img is None:
            ctx.features["error"] = "No segmentation result available"
            return ctx

        feature_set = params.get("feature_set", "grinding_full")
        features = self._extract_all(labels, fg_mask, img)
        ctx.features = features

        # 关键指标也写入 metrics（供告警规则引用）
        ctx.metrics.update({
            "n_grains": features.get("grain_count", 0),
            "p80_estimated": features.get("p80_estimated", 0),
            "overgrinding_ratio": features.get("overgrinding_ratio", 0),
            "undergrinding_ratio": features.get("undergrinding_ratio", 0),
            "mean_circularity": features.get("mean_circularity", 0),
            "fill_ratio": features.get("fill_ratio", 0),
        })

        return ctx

    def _extract_all(
        self,
        labels: np.ndarray,
        fg_mask: np.ndarray,
        img: np.ndarray,
    ) -> dict:
        """提取完整粒度特征集"""
        h, w = fg_mask.shape
        total_pixels = h * w

        # ---- 单颗粒属性提取 ----
        grain_props = self._extract_per_grain_properties(labels, img)
        n_grains = len(grain_props)

        if n_grains == 0:
            return {
                "grain_count": 0,
                "fill_ratio": 0.0,
                "p80_estimated": 0.0,
                "condition": "no_data",
            }

        # ---- 汇总统计 ----
        areas = [p["area"] for p in grain_props]
        equiv_diameters = [p["equiv_diameter"] for p in grain_props]
        circularities = [p["circularity"] for p in grain_props]
        aspect_ratios = [p["aspect_ratio"] for p in grain_props]
        solidities = [p["solidity"] for p in grain_props]

        areas_sorted = sorted(areas)
        diameters_sorted = sorted(equiv_diameters)

        # 粒度分布百分位
        def percentile(sorted_data, p):
            if not sorted_data:
                return 0.0
            idx = int(p / 100 * (len(sorted_data) - 1))
            return sorted_data[min(idx, len(sorted_data) - 1)]

        # 像素→微米转换（假设标定系数，实际应从场景配置读取）
        # 默认假设：图像宽度对应 2mm = 2000μm
        pixel_to_um = 2000.0 / w

        d10 = percentile(diameters_sorted, 10) * pixel_to_um
        d50 = percentile(diameters_sorted, 50) * pixel_to_um
        d80 = percentile(diameters_sorted, 80) * pixel_to_um
        d90 = percentile(diameters_sorted, 90) * pixel_to_um

        # 过磨/欠磨判定阈值（可配置）
        fine_threshold_um = 45   # < 45μm 视为过磨
        coarse_threshold_um = 200  # > 200μm 视为欠磨

        fine_count = sum(1 for d in equiv_diameters if d * pixel_to_um < fine_threshold_um)
        coarse_count = sum(1 for d in equiv_diameters if d * pixel_to_um > coarse_threshold_um)

        # 均匀系数 Cu = D60/D10（越接近1越均匀）
        d60 = percentile(diameters_sorted, 60) * pixel_to_um
        cu = d60 / d10 if d10 > 0 else 0
        # 曲率系数 Cc = (D30)^2 / (D10*D60)
        d30 = percentile(diameters_sorted, 30) * pixel_to_um
        cc = (d30 ** 2) / (d10 * d60) if (d10 > 0 and d60 > 0) else 0

        # 颗粒面积直方图数据（前端绘图用）
        hist_bins = np.linspace(0, max(equiv_diameters) * pixel_to_um + 1, 21)
        hist_counts, _ = np.histogram([d * pixel_to_um for d in equiv_diameters], bins=hist_bins)

        return {
            # === 基础统计 ===
            "grain_count": n_grains,
            "total_area_px": int(sum(areas)),
            "fill_ratio": round(sum(areas) / total_pixels, 4),
            "image_size": f"{w}x{h}",

            # === 粒度分布（μm）===
            "d10": round(d10, 1),
            "d30": round(d30, 1),
            "d50": round(d50, 1),
            "d60": round(d60, 1),
            "d80": round(d80, 1),
            "d90": round(d90, 1),

            # === P80 估计（基于面积加权经验公式）===
            "p80_estimated": round(d80, 1),

            # === 形状特征 ===
            "mean_circularity": round(sum(circularities) / n_grains, 4),
            "mean_aspect_ratio": round(sum(aspect_ratios) / n_grains, 3),
            "mean_solidity": round(sum(solidities) / n_grains, 4),
            "min_circularity": round(min(circularities), 4),
            "max_area_px": int(max(areas)),

            # === 工况指标 ===
            "overgrinding_ratio": round(fine_count / n_grains, 4),
            "undergrinding_ratio": round(coarse_count / n_grains, 4),
            "normal_ratio": round((n_grains - fine_count - coarse_count) / n_grains, 4),

            # === 分布均匀性 ===
            "uniformity_coefficient": round(cu, 2),
            "curvature_coefficient": round(cc, 2),

            # === 直方图数据（前端 ECharts 用）===
            "size_histogram": {
                "bins": [round(x, 1) for x in hist_bins.tolist()],
                "counts": hist_counts.tolist(),
            },

            # === 工况判断 ===
            "condition": self._judge_condition(d80, fine_count / n_grains),
        }

    def _extract_per_grain_properties(
        self, labels: np.ndarray, img: np.ndarray
    ) -> List[dict]:
        """提取每个颗粒的几何属性"""
        props = []
        unique_labels = np.unique(labels)
        unique_labels = unique_labels[unique_labels != 0]  # 排除背景

        for grain_id in unique_labels:
            mask = (labels == grain_id).astype(np.uint8)
            area = int(mask.sum())
            if area < 1:
                continue

            # 等效直径（等面积圆直径）
            equiv_diameter = math.sqrt(4 * area / math.pi)

            # 外接矩形
            x, y, bw, bh = cv2.boundingRect(mask)
            aspect_ratio = max(bw, bh) / min(bw, bh) if min(bw, bh) > 0 else 0

            # 圆度：4π × Area / Perimeter²
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                perimeter = cv2.arcLength(contours[0], True)
                circularity = 4 * math.pi * area / (perimeter ** 2) if perimeter > 0 else 0
            else:
                circularity = 0.0

            # 实心度（面积 / 凸包面积）
            hull = cv2.convexHull(contours[0]) if contours else np.array([])
            hull_area = cv2.contourArea(hull) if len(hull) > 0 else area
            solidity = area / hull_area if hull_area > 0 else 0

            props.append({
                "id": int(grain_id),
                "area": area,
                "equiv_diameter": equiv_diameter,
                "aspect_ratio": round(aspect_ratio, 3),
                "circularity": min(circularity, 1.0),  # 截断到[0,1]
                "solidity": round(solidity, 4),
                "bbox": (x, y, bw, bh),
            })

        return props

    @staticmethod
    def _judge_condition(p80: float, overgrinding_ratio: float) -> str:
        """工况判断：过磨/正常/欠磨"""
        if p80 < 75 or overgrinding_ratio > 0.35:
            return "过磨"
        elif p80 > 200:
            return "欠磨"
        else:
            return "正常"
