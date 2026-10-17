"""
安全巡检 — 风险评级 Filter
=============================
基于检测结果和特征进行综合风险评级：
  - low (低风险): 风险分 < 2
  - medium (中等风险): 2 <= 风险分 < 5
  - high (高风险): 5 <= 风险分 < 10
  - critical (紧急): 风险分 >= 10

同时生成：
  - 风险等级标签
  - 建议措施
  - 告警标志（供 alarm Filter 使用）
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import cv2

from backend.engines.base import Engine, EngineContext

logger = logging.getLogger(__name__)

# 风险等级定义
RISK_LEVELS = {
    "low": {"score_max": 2, "color": (0, 255, 0), "label": "低风险", "icon": "✅"},
    "medium": {"score_max": 5, "color": (0, 165, 255), "label": "中等风险", "icon": "⚠️"},
    "high": {"score_max": 10, "color": (0, 100, 255), "label": "高风险", "icon": "🔴"},
    "critical": {"score_max": float("inf"), "color": (0, 0, 255), "label": "紧急", "icon": "🚨"},
}

# 各风险等级的建议措施
RISK_ACTIONS = {
    "low": "保持监控，按计划巡检。",
    "medium": "建议关注，加强巡检频率，准备干预措施。",
    "high": "需要立即关注！建议现场确认，必要时停机检查。",
    "critical": "紧急！立即采取安全措施，通知现场人员撤离，启动应急预案。",
}

# 强制升级规则（某些条件直接提升到最高级）
FORCE_CRITICAL_CONDITIONS = [
    ("persons_in_danger_zone > 0", "检测到人员进入危险区域"),
    ("belt_offset > 80", "皮带严重跑偏"),
]

FORCE_HIGH_CONDITIONS = [
    ("belt_offset > 50", "皮带明显跑偏"),
    ("foreign_object_count >= 3", "检测到多个异物"),
]


class AssessRiskEngine(Engine):
    """风险评级 Filter"""

    name = "assess_risk"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        features = getattr(ctx, 'features', None)
        img = ctx.cropped_image if ctx.cropped_image is not None else ctx.raw_image

        if features is None:
            logger.warning("[assess_risk] 无特征数据，跳过")
            return ctx

        # ---- 获取原始风险分 ----
        raw_score = features.get("raw_risk_score", 0.0)

        # ---- 获取阈值配置 ----
        thresholds = params.get("thresholds", {
            "low": 2.0,
            "medium": 5.0,
            "high": 10.0,
            "critical": 20.0,
        })

        # ---- 判断风险等级 ----
        risk_level = self._classify_risk(raw_score, thresholds, features)

        # ---- 检查强制升级条件 ----
        risk_level, force_reason = self._check_force_upgrade(risk_level, features)
        if force_reason:
            logger.warning(f"[assess_risk] 风险强制升级: {force_reason}")

        # ---- 归一化风险评分 (0-100) ----
        normalized_score = self._normalize_score(raw_score, risk_level)

        # ---- 生成建议措施 ----
        action = RISK_ACTIONS.get(risk_level, RISK_ACTIONS["medium"])

        # ---- 存储结果 ----
        prediction = {
            "risk_level": risk_level,
            "risk_score": round(normalized_score, 1),
            "raw_score": round(raw_score, 1),
            "action": action,
            "force_reason": force_reason,
            "confidence": self._calc_confidence(features, risk_level),
        }

        ctx.prediction = prediction
        ctx.metrics["risk_score"] = round(normalized_score, 1)
        ctx.metrics["risk_level"] = risk_level

        # ---- 生成风险评级可视化 ----
        if img is not None:
            risk_viz = self._draw_risk_panel(img.copy(), prediction, features)
            ctx.overlays["risk_assessment"] = risk_viz

        logger.info(
            f"[assess_risk] 风险等级={risk_level}, "
            f"评分={normalized_score:.1f}/100, "
            f"原始分={raw_score:.1f}"
        )

        return ctx

    def _classify_risk(
        self,
        score: float,
        thresholds: dict,
        features: dict,
    ) -> str:
        """根据分数分类风险等级"""
        t_low = thresholds.get("low", 2.0)
        t_medium = thresholds.get("medium", 5.0)
        t_high = thresholds.get("high", 10.0)

        if score < t_low:
            return "low"
        elif score < t_medium:
            return "medium"
        elif score < t_high:
            return "high"
        else:
            return "critical"

    def _check_force_upgrade(self, current_level: str, features: dict) -> tuple:
        """
        检查是否需要强制升级风险等级
        返回: (最终等级, 升级原因/None)
        """
        force_reason = None

        # 检查 critical 强制条件
        for condition, reason in FORCE_CRITICAL_CONDITIONS:
            try:
                # 简单的条件评估（支持基本的比较表达式）
                if self._eval_condition(condition, features):
                    if current_level != "critical":
                        force_reason = f"强制升级→紧急: {reason}"
                    return "critical", force_reason
            except Exception:
                pass

        # 检查 high 强制条件
        for condition, reason in FORCE_HIGH_CONDITIONS:
            try:
                if self._eval_condition(condition, features):
                    if current_level not in ("high", "critical"):
                        force_reason = f"强制升级→高风险: {reason}"
                    return "high", force_reason
            except Exception:
                pass

        return current_level, force_reason

    def _eval_condition(self, condition: str, features: dict) -> bool:
        """
        简单的条件表达式求值
        支持格式: "key > value", "key >= value", "key == value"
        """
        for op in [">=", "<=", "!=", "==", ">", "<"]:
            if op in condition:
                key, val_str = condition.split(op, 1)
                key = key.strip()
                val = float(val_str.strip())

                feat_val = features.get(key, 0)
                if isinstance(feat_val, (int, float)):
                    if op == ">":
                        return feat_val > val
                    elif op == ">=":
                        return feat_val >= val
                    elif op == "<":
                        return feat_val < val
                    elif op == "<=":
                        return feat_val <= val
                    elif op == "==":
                        return feat_val == val
                    elif op == "!=":
                        return feat_val != val
                break

        return False

    def _normalize_score(self, raw_score: float, risk_level: str) -> float:
        """将原始风险分归一化到 0-100"""
        # 每个等级的分数范围映射
        range_map = {
            "low": (0, 2, 0, 25),
            "medium": (2, 5, 25, 50),
            "high": (5, 10, 50, 75),
            "critical": (10, 100, 75, 100),
        }

        r_min_in, r_max_in, r_min_out, r_max_out = range_map.get(
            risk_level, (0, 100, 0, 100)
        )

        if r_max_in == r_min_in:
            return r_min_out

        ratio = (raw_score - r_min_in) / (r_max_in - r_min_in)
        ratio = max(0, min(1, ratio))  # clamp to [0, 1]

        return r_min_out + ratio * (r_max_out - r_min_out)

    def _calc_confidence(self, features: dict, risk_level: str) -> float:
        """
        计算评级置信度
        基于检测结果的置信度和覆盖度
        """
        base_confidence = 0.5

        # 如果有检测目标，提高置信度
        total_dets = features.get("total_detections", 0)
        if total_dets > 0:
            base_confidence += min(total_dets * 0.05, 0.2)

        # 平均检测置信度
        avg_conf = features.get("avg_confidence", 0)
        base_confidence += avg_conf * 0.3

        # 低风险且无检测结果 → 高置信度（确实安全）
        if risk_level == "low" and total_dets == 0:
            base_confidence = 0.85

        return round(min(base_confidence, 0.99), 3)

    def _draw_risk_panel(
        self,
        img: np.ndarray,
        prediction: dict,
        features: dict,
    ) -> np.ndarray:
        """绘制风险评级面板"""
        viz = img.copy()
        h, w = img.shape[:2]

        # 右侧半透明面板
        panel_w = 280
        panel_h = h
        panel_x = w - panel_w

        # 面板背景
        overlay = viz[:, panel_x:w].copy()
        bg_color = (30, 30, 35)
        overlay[:] = bg_color
        viz[:, panel_x:w] = cv2.addWeighted(viz[:, panel_x:w], 0.4, overlay, 0.6, 0)

        # 风险等级颜色
        level = prediction["risk_level"]
        level_color = RISK_LEVELS[level]["color"]
        level_label = RISK_LEVELS[level]["label"]
        level_icon = RISK_LEVELS[level]["icon"]

        y_offset = 30
        line_height = 28

        # 标题
        cv2.putText(
            viz, "=== 安全巡检报告 ===",
            (panel_x + 15, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 2,
        )
        y_offset += line_height + 15

        # 风险等级（大字）
        cv2.putText(
            viz, f"{level_icon} {level_label}",
            (panel_x + 15, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.85, level_color, 2,
        )
        y_offset += line_height + 5

        # 风险评分条
        score = prediction["risk_score"]
        bar_width = int((score / 100) * (panel_w - 40))
        cv2.rectangle(viz, (panel_x + 20, y_offset), (panel_x + panel_w - 20, y_offset + 18), (60, 60, 60), -1)
        if bar_width > 0:
            cv2.rectangle(viz, (panel_x + 20, y_offset), (panel_x + 20 + bar_width, y_offset + 18), level_color, -1)
        cv2.putText(
            viz, f"{score:.0f}/100",
            (panel_x + panel_w - 70, y_offset + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 2,
        )
        y_offset += line_height + 15

        # 分隔线
        cv2.line(viz, (panel_x + 10, y_offset), (w - 10, y_offset), (80, 80, 80), 1)
        y_offset += 12

        # 详细指标
        detail_items = [
            ("人员总数", features.get("person_count", 0), ""),
            ("危险区人员", features.get("persons_in_danger_zone", 0), "人"),
            ("皮带偏移", f'{features.get("belt_offset_px", 0)}px', features.get("belt_direction", "")),
            ("异物数量", features.get("foreign_object_count", 0), "个"),
            ("PPE合规率", f'{features.get("ppe_compliance_rate", 1.0)*100:.0f}', "%"),
            ("检测目标", features.get("total_detections", 0), ""),
        ]

        for label, value, unit in detail_items:
            text = f"{label}: {value}{unit}"
            cv2.putText(
                viz, text,
                (panel_x + 15, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 2,
            )
            y_offset += line_height

        # 分隔线
        y_offset += 8
        cv2.line(viz, (panel_x + 10, y_offset), (w - 10, y_offset), (80, 80, 80), 1)
        y_offset += 12

        # 建议措施（自动换行）
        action = prediction["action"]
        action_lines = self._wrap_text(action, max_chars=18)

        cv2.putText(
            viz, "建议措施:",
            (panel_x + 15, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 100), 2,
        )
        y_offset += line_height

        for line in action_lines:
            cv2.putText(
                viz, line,
                (panel_x + 15, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (160, 160, 160), 2,
            )
            y_offset += int(line_height * 0.85)

        # 强制升级原因
        force_reason = prediction.get("force_reason")
        if force_reason:
            y_offset += 10
            cv2.putText(
                viz, f"⚠ {force_reason}",
                (panel_x + 15, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 2,
            )

        return viz

    @staticmethod
    def _wrap_text(text: str, max_chars: int) -> list[str]:
        """简单文本换行"""
        lines = []
        while len(text) > max_chars:
            # 找到最近的逗号或空格处断开
            split_pos = text[:max_chars].rfind("，")
            if split_pos <= 0:
                split_pos = text[:max_chars].rfind(" ")
            if split_pos <= 0:
                split_pos = max_chars
            lines.append(text[:split_pos])
            text = text[split_pos:].lstrip("， ")
        if text:
            lines.append(text)
        return lines

