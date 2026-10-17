"""
安全巡检 — YOLO 目标检测 Filter
=================================
支持两种模式：
  1. YOLO 模式（ultralytics YOLOv8）— 需要安装 ultralytics + 模型文件
  2. 规则模式（fallback）— 纯 OpenCV，基于边缘/轮廓/颜色规则检测

检测目标类别（COCO 预训练 + 扩展规则类）：
  - person: 人员（YOLO 原生）
  - helmet: 安全帽（YOLO 原生或颜色近似）
  - belt_misalignment: 皮带跑偏（边缘检测规则）
  - foreign_object: 异物（异常检测规则）
  - intrusion: 危险区域入侵（区域规则）

参考开源项目：
  - Amank-root/YoloV8-detection: 皮带金属棒检测 + 停机监控
  - GES-YOLO: 轻量化皮带跑偏检测（MDPI Machines 2025）
  - SCCG-YOLO: 矿用传送带大块煤检测（PLOS ONE 2026）
"""

from __future__ import annotations

import time
import logging
from typing import Optional

import cv2
import numpy as np

from backend.engines.base import Engine, EngineContext

logger = logging.getLogger(__name__)

# ============================================================
# 尝试导入 ultralytics（可选依赖）
# ============================================================
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO = None
    YOLO_AVAILABLE = False
    logger.warning("ultralytics 未安装，detect_objects 将使用规则模式 (fallback)")

# ============================================================
# COCO 类别名称映射（YOLOv8 预训练模型）
# ============================================================
COCO_CLASSES = {
    0: 'person', 1: 'bicycle', 2: 'car', 3: 'motorcycle', 4: 'airplane',
    5: 'bus', 6: 'train', 7: 'truck', 8: 'boat', 9: 'traffic_light',
    # ... 完整80类，这里只列常用的
}

# 安全巡检关心的目标类别（COCO 索引）
SAFETY_COCO_CLASSES = {0: 'person'}  # 目前只关心 person

# 检测框颜色 BGR
DETECTION_COLORS = {
    'person': (0, 255, 0),           # 绿色
    'helmet': (255, 0, 0),            # 蓝色（安全帽）
    'belt_misalignment': (0, 165, 255),  # 橙色（跑偏）
    'foreign_object': (0, 0, 255),    # 红色（异物）
    'intrusion': (128, 0, 128),       # 紫色（入侵）
    'unknown': (200, 200, 200),       # 灰色
}

# 风险等级颜色
RISK_LEVEL_COLORS = {
    'low': (0, 255, 0),        # 绿色
    'medium': (0, 165, 255),   # 橙色
    'high': (0, 100, 255),     # 蓝红（偏红）
    'critical': (0, 0, 255),   # 红色
}


class DetectObjectsEngine(Engine):
    """YOLO 目标检测 / 规则检测 Filter"""

    name = "detect_objects"

    # 单例模型缓存
    _model_cache: dict = {}
    _model_path: Optional[str] = None

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        method = params.get("method", "auto")  # auto / yolo / rule
        img = ctx.cropped_image if ctx.cropped_image is not None else (
            ctx.preprocessed if ctx.preprocessed is not None else ctx.raw_image
        )
        if img is None:
            logger.warning("[detect_objects] 无可用图像")
            return ctx

        h, w = img.shape[:2]

        # 自动选择方法
        if method == "auto":
            method = "yolo" if YOLO_AVAILABLE else "rule"

        start_time = time.time()

        if method == "yolo" and YOLO_AVAILABLE:
            detections = self._detect_yolo(img, params)
        else:
            detections = self._detect_rule_based(img, params, h, w)

        elapsed_ms = (time.time() - start_time) * 1000

        # 存储检测结果到 context
        ctx.detections = detections
        ctx.metrics["inference_time_ms"] = round(elapsed_ms, 1)
        ctx.metrics["detection_method"] = method
        ctx.metrics["detection_count"] = len(detections)

        # 生成检测叠加图
        overlay = self._draw_detections(img.copy(), detections, params)
        ctx.overlays["detection_overlay"] = overlay

        # 生成危险区域热力图
        danger_map = self._draw_danger_zones(img.copy(), params)
        ctx.overlays["danger_zone_map"] = danger_map

        logger.info(
            f"[detect_objects] 方法={method}, "
            f"检测到 {len(detections)} 个目标, "
            f"耗时 {elapsed_ms:.1f}ms"
        )

        return ctx

    # ==================== YOLO 检测 ====================

    def _detect_yolo(self, img: np.ndarray, params: dict) -> list[dict]:
        """使用 YOLOv8 进行目标检测"""
        model_name = params.get("model_name", "yolov8n.pt")
        conf_threshold = params.get("conf_threshold", 0.25)
        iou_threshold = params.get("iou_threshold", 0.45)
        target_classes = params.get("target_classes", ["person"])

        model = self._get_model(model_name)

        if model is None:
            logger.warning(f"[detect_objects] 无法加载模型 {model_name}，回退到规则模式")
            return self._detect_rule_based(img, params, img.shape[0], img.shape[1])

        # 执行推理
        results = model(
            img,
            conf=conf_threshold,
            iou=iou_threshold,
            verbose=False,
        )

        detections = []
        for r in results:
            boxes = r.boxes
            if boxes is None or len(boxes) == 0:
                continue

            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            clss = boxes.cls.cpu().numpy().astype(int)

            for i in range(len(xyxy)):
                x1, y1, x2, y2 = map(float, xyxy[i])
                cls_id = int(clss[i])
                confidence = float(confs[i])

                # 获取类别名
                cls_name = COCO_CLASSES.get(cls_id, f"class_{cls_id}")

                # 只保留目标类别
                if target_classes and cls_name not in target_classes:
                    # 特殊处理：person 的子类
                    if cls_name == "person" and "person" not in target_classes:
                        continue

                detections.append({
                    "class": cls_name,
                    "confidence": round(confidence, 3),
                    "bbox": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                    "center": [round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1)],
                    "area": round((x2 - x1) * (y2 - y1), 1),
                    "method": "yolo",
                })

        # 补充规则检测（YOLO 不擅长的：皮带跑偏 + 异物局部异常）
        rule_dets = self._detect_belt_misalignment(img, params)
        detections.extend(rule_dets)
        h2, w2 = img.shape[:2]
        if "foreign_object" in target_classes:
            detections.extend(self._detect_foreign_objects(img, params, h2, w2))

        return detections

    def _get_model(self, model_name: str):
        """获取/缓存 YOLO 模型单例"""
        if model_name in self._model_cache:
            return self._model_cache[model_name]

        try:
            # 尝试从本地路径加载，如果不存在则让 ultralytics 自动下载预训练模型
            model = YOLO(model_name)
            self._model_cache[model_name] = model
            logger.info(f"[detect_objects] 成功加载模型: {model_name}")
            return model
        except Exception as e:
            logger.error(f"[detect_objects] 加载模型失败: {model_name}, 错误: {e}")
            return None

    # ==================== 规则检测（Fallback）====================

    def _detect_rule_based(self, img: np.ndarray, params: dict, h: int, w: int) -> list[dict]:
        """
        纯 OpenCV 规则检测（无需 YOLO/GPU）
        检测策略：
          1. 皮带跑偏 — 边缘检测 + 霍夫直线
          2. 人员近似 — HOG + SVM 或肤色/轮廓
          3. 异物 — 背景差分 / 异常亮度区域
        """
        detections = []
        target_classes = params.get("target_classes", ["person", "belt_misalignment", "foreign_object"])

        # 1. 皮带边缘/跑偏检测
        if "belt_misalignment" in target_classes:
            belt_dets = self._detect_belt_misalignment(img, params)
            detections.extend(belt_dets)

        # 2. 人员检测（基于轮廓+比例的简化版）
        if "person" in target_classes:
            person_dets = self._detect_person_approx(img, params, h, w)
            detections.extend(person_dets)

        # 3. 异物检测（基于亮度和面积异常）
        if "foreign_object" in target_classes:
            foreign_dets = self._detect_foreign_objects(img, params, h, w)
            detections.extend(foreign_dets)

        return detections

    def _detect_belt_misalignment(self, img: np.ndarray, params: dict) -> list[dict]:
        """
        皮带跑偏检测 — 基于 Canny 边缘 + 霍夫直线检测
        参考：GES-YOLO (MDPI Machines 2025) 的皮带边缘检测思路
        """
        detections = []
        edge_params = params.get("edge_detection", {})
        canny_low = edge_params.get("canny_low", 50)
        canny_high = edge_params.get("canny_high", 150)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

        # 高斯模糊降噪
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)

        # Canny 边缘检测
        edges = cv2.Canny(blurred, canny_low, canny_high)

        # 霍夫直线检测（检测皮带边缘线）
        lines = cv2.HoughLinesP(
            edges,
            rho=edge_params.get("hough_rho", 1),
            theta=edge_params.get("hough_theta", np.pi / 180),
            threshold=edge_params.get("hough_threshold", 100),
            minLineLength=edge_params.get("min_line_length", 100),
            maxLineGap=edge_params.get("max_line_gap", 20),
        )

        if lines is not None and len(lines) > 0:
            h, w = gray.shape

            # 确保lines是 (N, 4) 格式
            lines_arr = np.array(lines).reshape(-1, 4)

            # 分离左边缘线和右边缘线（按x1坐标分类）
            left_x_list = []
            right_x_list = []
            for i in range(len(lines_arr)):
                x1 = int(lines_arr[i][0])
                x2 = int(lines_arr[i][2])
                mid_x = (x1 + x2) / 2
                if mid_x < w * 0.4:
                    left_x_list.append(x1)
                elif mid_x > w * 0.6:
                    right_x_list.append(x1)

            # 计算左右边缘的平均 X 坐标
            left_edge_x = np.mean(left_x_list) if left_x_list else w * 0.25
            right_edge_x = np.mean(right_x_list) if right_x_list else w * 0.75

            # 计算皮带中心偏移量（相对于图像中心）
            belt_center = (left_edge_x + right_edge_x) / 2
            image_center_x = w / 2
            offset_px = belt_center - image_center_x
            offset_pct = abs(offset_px) / (w / 2) * 100  # 偏移百分比

            misalignment_threshold = params.get("misalignment_threshold", 30)

            if abs(offset_px) > misalignment_threshold:
                severity = "严重" if offset_pct > 15 else ("中等" if offset_pct > 8 else "轻微")
                confidence = min(0.5 + offset_pct / 20, 0.95)

                # 标注跑偏区域
                margin = 50
                det_bbox = [
                    int(max(0, min(left_edge_x, right_edge_x) - margin)),
                    0,
                    int(min(w, max(left_edge_x, right_edge_x) + margin)),
                    h,
                ]

                detections.append({
                    "class": "belt_misalignment",
                    "confidence": round(confidence, 3),
                    "bbox": det_bbox,
                    "center": [int(belt_center), h // 2],
                    "area": (det_bbox[2] - det_bbox[0]) * h,
                    "method": "rule",
                    "attributes": {
                        "offset_px": round(offset_px, 1),
                        "offset_pct": round(offset_pct, 1),
                        "severity": severity,
                        "direction": "左偏" if offset_px < 0 else "右偏",
                        "left_edge_x": round(left_edge_x, 1),
                        "right_edge_x": round(right_edge_x, 1),
                    },
                })

                # 存储偏移量供后续 Filter 使用
                logger.info(
                    f"[detect_objects] 检测到皮带跑偏: {severity}, "
                    f"偏移={offset_px:.1f}px ({offset_pct:.1f}%), "
                    f"方向={'左' if offset_px < 0 else '右'}"
                )

        return detections

    def _detect_person_approx(self, img: np.ndarray, params: dict, h: int, w: int) -> list[dict]:
        """
        人员近似检测（规则模式 fallback）
        使用 HOG 特征 + 轮廓比例粗筛
        注意：这是简化版，精度远不如 YOLO
        """
        detections = []

        try:
            # 尝试使用 HOG+SVM（OpenCV 内置）
            hog = cv2.HOGDescriptor()
            hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

            # 检测（缩小图像加速）
            scale = 0.5 if max(h, w) > 640 else 1.0
            if scale < 1.0:
                small_img = cv2.resize(img, (int(w * scale), int(h * scale)))
            else:
                small_img = img

            # winStride 和 padding 影响检测速度和精度
            rects, weights = hog.detectMultiScale(
                small_img,
                winStride=(8, 8),
                padding=(8, 8),
                scale=1.05,
            )

            # 还原坐标并过滤重叠
            for i, (x, y, bw, bh) in enumerate(rects):
                # 还原到原始尺寸
                x, y, bw, bh = [int(v / scale) for v in (x, y, bw, bh)]
                confidence = min(0.4 + float(weights[i][0]) * 0.1, 0.9)

                # 过滤太小的检测
                if bw < 30 or bh < 50:
                    continue

                detections.append({
                    "class": "person",
                    "confidence": round(max(confidence, 0.3), 3),
                    "bbox": [x, y, x + bw, y + bh],
                    "center": [x + bw // 2, y + bh // 2],
                    "area": bw * bh,
                    "method": "hog_svm",
                })

        except Exception as e:
            logger.debug(f"[detect_objects] HOG 人员检测失败: {e}，尝试轮廓法")

            # Fallback: 基于轮廓比例的粗略检测
            detections.extend(self._detect_person_by_contour(img, h, w))

        return detections

    def _detect_person_by_contour(self, img: np.ndarray, h: int, w: int) -> list[dict]:
        """基于轮廓比例的人员粗检（最后手段）"""
        detections = []
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

        # 自适应阈值
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 21, -5
        )

        # 形态学清理
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

        # 连通域分析
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(cleaned)

        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            x, y, bw, bh = (
                stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP],
                stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT],
            )

            # 人体大致比例：宽高比 1:2 到 1:4，面积大于一定阈值
            aspect_ratio = bw / bh if bh > 0 else 0
            min_person_area = (h * w) * 0.02  # 至少占图像 2%

            if (min_person_area < area < (h * w) * 0.4
                    and 0.2 < aspect_ratio < 0.7
                    and bh > h * 0.15):
                detections.append({
                    "class": "person",
                    "confidence": 0.35,  # 低置信度（轮廓法不靠谱）
                    "bbox": [x, y, x + bw, y + bh],
                    "center": [int(centroids[i][0]), int(centroids[i][1])],
                    "area": area,
                    "method": "contour",
                })

        return detections

    def _detect_foreign_objects(self, img: np.ndarray, params: dict, h: int, w: int) -> list[dict]:
        """
        异物检测 — 基于局部亮度/颜色异常
        思路：在均匀背景（如皮带表面）上，异物通常表现为亮度或颜色突变
        """
        detections = []

        if len(img.shape) == 3:
            # 转换到 LAB 色彩空间（L 通道对光照更鲁棒）
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            l_channel = lab[:, :, 0]

            # 计算局部均值和标准差
            local_mean = cv2.blur(l_channel, (51, 51))
            local_std = cv2.blur((l_channel.astype(np.float32) - local_mean.astype(np.float32)) ** 2, (51, 51))
            local_std = np.sqrt(local_std)

            # 异常区域：标准差异常高（纹理/颜色不一致）
            mean_std = np.mean(local_std)
            threshold = mean_std * 2.5

            # 找到高异常区域
            anomaly_mask = (local_std > threshold).astype(np.uint8) * 255

            # 形态学清理
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
            anomaly_mask = cv2.morphologyEx(anomaly_mask, cv2.MORPH_OPEN, kernel)
            anomaly_mask = cv2.morphologyEx(anomaly_mask, cv2.MORPH_CLOSE, kernel)

            # 连通域
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(anomaly_mask)

            for i in range(1, num_labels):
                area = stats[i, cv2.CC_STAT_AREA]
                x, y, bw, bh = (
                    stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP],
                    stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT],
                )

                # 异物面积阈值：不能太小（噪点），也不能太大（整个区域变化）
                min_foreign_area = (h * w) * 0.005
                max_foreign_area = (h * w) * 0.15

                if min_foreign_area < area < max_foreign_area:
                    # 计算异常分数
                    anomaly_score = min((np.max(local_std[y:y+bh, x:x+bw]) / mean_std - 1) * 0.5, 0.8) + 0.2

                    detections.append({
                        "class": "foreign_object",
                        "confidence": round(anomaly_score, 3),
                        "bbox": [x, y, x + bw, y + bh],
                        "center": [int(centroids[i][0]), int(centroids[i][1])],
                        "area": area,
                        "method": "anomaly",
                    })

        return detections

    # ==================== 可视化 ====================

    def _draw_detections(self, img: np.ndarray, detections: list[dict], params: dict) -> np.ndarray:
        """绘制检测框叠加图"""
        overlay = img.copy()

        # 绘制危险区域半透明罩
        danger_zones = params.get("danger_zones", [])
        for zone in danger_zones:
            rect = zone["rect"]
            x1 = int(rect[0] * overlay.shape[1])
            y1 = int(rect[1] * overlay.shape[0])
            x2 = int(rect[2] * overlay.shape[1])
            y2 = int(rect[3] * overlay.shape[0])

            risk = zone.get("risk_level", "medium")
            color = RISK_LEVEL_COLORS.get(risk, (128, 128, 128))

            # 半透明填充
            overlay_rect = overlay[y1:y2, x1:x2].copy()
            overlay_rect = cv2.addWeighted(overlay_rect, 0.6, np.full_like(overlay_rect, color[::-1], dtype=np.uint8), 0.4, 0)
            overlay[y1:y2, x1:x2] = overlay_rect

            # 区域边框
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                overlay, f"{zone['name']} ({risk})",
                (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2,
            )

        # 绘制检测框
        for det in detections:
            cls = det["class"]
            conf = det["confidence"]
            bbox = det["bbox"]
            x1, y1, x2, y2 = map(int, bbox)

            color = DETECTION_COLORS.get(cls, DETECTION_COLORS["unknown"])

            # 检测框
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)

            # 标签背景
            label = f"{cls} {conf:.0%}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            cv2.rectangle(overlay, (x1, y1 - th - 10), (x1 + tw + 4, y1), color, -1)
            cv2.putText(overlay, label, (x1 + 2, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

            # 中心点标记（OpenCV 5.x 不接受 numpy 数值，必须转 Python int）
            cx, cy = int(det["center"][0]), int(det["center"][1])
            cv2.circle(overlay, (cx, cy), 4, (0, 255, 0), -1)

            # 如果是皮带跑偏，画偏移指示线
            if cls == "belt_misalignment" and "attributes" in det:
                attr = det["attributes"]
                direction = attr.get("direction", "")
                offset_px = attr.get("offset_px", 0)

                # 画箭头指示偏移方向
                arrow_color = (0, 0, 255) if abs(offset_px) > 50 else (0, 165, 255)
                arrow_end_x = cx + (100 if offset_px > 0 else -100)
                cv2.arrowedLine(
                    overlay, (cx, cy), (arrow_end_x, cy),
                    arrow_color, 3, tipLength=0.3,
                )
                cv2.putText(
                    overlay, f"{direction} {abs(offset_px):.0f}px",
                    (arrow_end_x + (10 if offset_px > 0 else -80), cy - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, arrow_color, 2,
                )

        # 左上角状态信息
        method = params.get("method", "auto")
        status_text = f"Detections: {len(detections)} | Method: {'YOLO' if method != 'rule' else 'Rule'}"
        cv2.rectangle(overlay, (5, 5), (350, 30), (0, 0, 0), -1)
        cv2.putText(overlay, status_text, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)

        return overlay

    def _draw_danger_zones(self, img: np.ndarray, params: dict) -> np.ndarray:
        """绘制危险区域热力图"""
        heat_map = np.zeros((img.shape[0], img.shape[1], 3), dtype=np.uint8)

        danger_zones = params.get("danger_zones", [])
        for zone in danger_zones:
            rect = zone["rect"]
            x1 = int(rect[0] * heat_map.shape[1])
            y1 = int(rect[1] * heat_map.shape[0])
            x2 = int(rect[2] * heat_map.shape[1])
            y2 = int(rect[3] * heat_map.shape[0])

            risk = zone.get("risk_level", "medium")

            # 根据风险等级设置颜色强度
            if risk == "critical":
                color = (0, 0, 180)   # 深红
            elif risk == "high":
                color = (0, 0, 120)   # 红
            elif risk == "medium":
                color = (0, 100, 100)  # 橙
            else:
                color = (0, 150, 0)   # 绿

            heat_map[y1:y2, x1:x2] = color

        # 叠加原图（半透明）
        alpha = 0.4
        blended = cv2.addWeighted(img, 1 - alpha, heat_map, alpha, 0)

        # 绘制区域标签
        for zone in danger_zones:
            rect = zone["rect"]
            x1 = int(rect[0] * blended.shape[1])
            y1 = int(rect[1] * blended.shape[0])
            name = zone.get("name", "Zone")
            risk = zone.get("risk_level", "medium")

            cv2.putText(
                blended, f"{name} [{risk}]",
                (x1 + 5, y1 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (255, 255, 255), 2,
            )

        return blended

