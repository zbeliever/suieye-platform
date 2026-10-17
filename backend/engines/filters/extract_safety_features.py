"""
安全巡检 — 安全特征提取 Filter
=================================
从检测结果中提取安全专用特征和指标：
  - 人员相关：人数、危险区人数、PPE合规率
  - 皮带相关：偏移量、偏移方向、偏移严重程度
  - 异物相关：异物数量、总面积占比
  - 综合指标：风险评分、工况判断

输入: ctx.detections (来自 detect_objects Filter)
输出: ctx.features, ctx.metrics
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from backend.engines.base import Engine, EngineContext

logger = logging.getLogger(__name__)


class ExtractSafetyFeaturesEngine(Engine):
    """安全特征提取 Filter"""

    name = "extract_safety_features"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        detections = getattr(ctx, 'detections', None)
        img = ctx.cropped_image if ctx.cropped_image is not None else ctx.raw_image

        if detections is None:
            logger.warning("[extract_safety_features] 无检测结果，跳过")
            return ctx

        if img is None:
            logger.warning("[extract_safety_features] 无可用图像")
            return ctx

        h, w = img.shape[:2]
        danger_zones = params.get("danger_zones", [])

        # ---- 初始化特征字典 ----
        features = {}
        metrics = {}

        # ==================== 1. 人员特征 ====================
        person_dets = [d for d in detections if d["class"] == "person"]
        person_count = len(person_dets)

        # 人员在危险区域的数量
        persons_in_danger = self._count_in_zones(person_dets, danger_zones, w, h)

        # PPE 合规率（简化版：假设检测到 helmet 的比例）
        # 实际项目中需要单独检测头盔/反光衣
        helmet_dets = [d for d in detections if d["class"] == "helmet"]
        ppe_compliance_rate = (
            len(helmet_dets) / person_count if person_count > 0 else 1.0
        )

        # 人员平均置信度
        avg_person_conf = (
            np.mean([d["confidence"] for d in person_dets]) if len(person_dets) > 0 else 0.0
        )

        features["person_count"] = person_count
        features["persons_in_danger_zone"] = persons_in_danger
        features["persons_safe_zone"] = person_count - persons_in_danger
        features["ppe_compliance_rate"] = round(ppe_compliance_rate, 3)
        features["avg_person_confidence"] = round(avg_person_conf, 3)

        metrics["person_count"] = person_count
        metrics["persons_in_danger_zone"] = persons_in_danger
        metrics["ppe_compliance_rate"] = round(ppe_compliance_rate, 3)

        # ==================== 2. 皮带跑偏特征 ====================
        belt_dets = [d for d in detections if d["class"] == "belt_misalignment"]

        if belt_dets:
            # 取最严重的跑偏（偏移量最大的）
            worst_belt = max(belt_dets, key=lambda d: abs(d.get("attributes", {}).get("offset_px", 0)))
            attr = worst_belt.get("attributes", {})

            belt_offset = attr.get("offset_px", 0)
            belt_offset_pct = attr.get("offset_pct", 0)
            belt_direction = attr.get("direction", "未知")
            belt_severity = attr.get("severity", "未知")

            features["belt_offset_px"] = round(belt_offset, 1)
            features["belt_offset_percent"] = round(belt_offset_pct, 1)
            features["belt_direction"] = belt_direction
            features["belt_severity"] = belt_severity
            features["belt_misalignment_detected"] = True

            metrics["belt_offset"] = round(abs(belt_offset), 1)
            metrics["belt_severity"] = belt_severity
        else:
            features["belt_offset_px"] = 0.0
            features["belt_offset_percent"] = 0.0
            features["belt_direction"] = "正常"
            features["belt_severity"] = "无"
            features["belt_misalignment_detected"] = False

            metrics["belt_offset"] = 0.0
            metrics["belt_severity"] = "无"

        # ==================== 3. 异物特征 ====================
        foreign_dets = [d for d in detections if d["class"] == "foreign_object"]
        foreign_count = len(foreign_dets)
        foreign_total_area = sum(d["area"] for d in foreign_dets) if foreign_dets else 0
        image_area = h * w
        foreign_area_ratio = foreign_total_area / image_area if image_area > 0 else 0

        # 异物平均置信度
        avg_foreign_conf = (
            np.mean([d["confidence"] for d in foreign_dets]) if foreign_count > 0 else 0.0
        )

        features["foreign_object_count"] = foreign_count
        features["foreign_total_area"] = foreign_total_area
        features["foreign_area_ratio"] = round(foreign_area_ratio, 4)
        features["avg_foreign_confidence"] = round(avg_foreign_conf, 3)

        metrics["foreign_object_count"] = foreign_count
        metrics["foreign_area_ratio"] = round(foreign_area_ratio, 4)

        # ==================== 4. 检测统计 ====================
        total_detections = len(detections)

        # 各类别计数
        class_counts = {}
        for det in detections:
            cls = det["class"]
            class_counts[cls] = class_counts.get(cls, 0) + 1

        # 最高置信度
        max_confidence = max([d["confidence"] for d in detections], default=0.0)
        avg_confidence = np.mean([d["confidence"] for d in detections]) if detections else 0.0

        # 检测方法分布
        method_counts = {}
        for det in detections:
            method = det.get("method", "unknown")
            method_counts[method] = method_counts.get(method, 0) + 1

        features["total_detections"] = total_detections
        features["class_distribution"] = class_counts
        features["max_confidence"] = round(max_confidence, 3)
        features["avg_confidence"] = round(avg_confidence, 3)
        features["method_distribution"] = method_counts

        metrics["detection_count"] = total_detections
        metrics["max_confidence"] = round(max_confidence, 3)

        # ==================== 5. 风险预评分（供 assess_risk 使用）====================
        risk_weights = params.get("weights", {
            "person_in_danger_zone": 4.0,
            "belt_misalignment": 3.0,
            "foreign_object": 2.5,
            "ppe_missing": 2.0,
            "person_safe_zone": 1.0,
        })

        risk_score = 0.0

        # 人员在危险区
        if persons_in_danger > 0:
            risk_score += persons_in_danger * risk_weights.get("person_in_danger_zone", 4.0)

        # 安全区人员
        safe_persons = person_count - persons_in_danger
        if safe_persons > 0:
            risk_score += safe_persons * risk_weights.get("person_safe_zone", 1.0)

        # 皮带跑偏
        if belt_dets:
            offset_factor = min(abs(metrics.get("belt_offset", 0)) / 50, 2.0)  # 归一化到 0-2
            risk_score += offset_factor * risk_weights.get("belt_misalignment", 3.0)

        # 异物
        if foreign_count > 0:
            risk_score += foreign_count * risk_weights.get("foreign_object", 2.5)

        # PPE 不合规
        if person_count > 0 and ppe_compliance_rate < 0.8:
            ppe_penalty = (0.8 - ppe_compliance_rate) * 10 * risk_weights.get("ppe_missing", 2.0)
            risk_score += ppe_penalty

        features["raw_risk_score"] = round(risk_score, 2)

        # 存储结果
        ctx.features = {**getattr(ctx, 'features', {}), **features}
        ctx.metrics = {**ctx.metrics, **metrics}

        logger.info(
            f"[extract_safety_features] 人员={person_count}(危险区{persons_in_danger}), "
            f"皮带偏移={metrics.get('belt_offset', 0)}px, "
            f"异物={foreign_count}, "
            f"风险分={risk_score:.1f}"
        )

        return ctx

    def _count_in_zones(
        self,
        detections: list[dict],
        danger_zones: list[dict],
        img_w: int,
        img_h: int,
    ) -> int:
        """
        计算检测目标中有多少个在危险区域内
        判断标准：检测框中心点落在危险区域矩形内
        """
        if not danger_zones or not detections:
            return 0

        count = 0
        for det in detections:
            cx, cy = det["center"]

            for zone in danger_zones:
                rect = zone["rect"]
                x1 = rect[0] * img_w
                y1 = rect[1] * img_h
                x2 = rect[2] * img_w
                y2 = rect[3] * img_h

                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    count += 1
                    break  # 一个目标只计一次

        return count

