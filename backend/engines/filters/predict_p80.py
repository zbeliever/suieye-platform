"""P80 粒度预测引擎（粒度场景专用）

当前实现：基于粒度特征的规则模型（无需训练数据）
未来可替换为：XGBoost / Random Forest / 神经网络回归模型

输出：
- p80: 预测的 P80 值（μm）
- condition: 工况判断（过磨/正常/欠磨）
- confidence: 预测置信度
"""

from backend.engines.base import Engine, EngineContext


class PredictP80Engine(Engine):
    """P80 粒度预测 Filter"""

    name = "predict_p80"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        model_key = params.get("model_key", "p80-v1")
        features = ctx.features

        if not features or "grain_count" not in features:
            ctx.prediction = {
                "model": model_key,
                "p80": 0.0,
                "condition": "error",
                "confidence": 0.0,
                "method": "rule_based",
                "error": "No grain features available",
            }
            return ctx

        # 规则模型预测
        result = self._predict_rule_based(features)
        result["model"] = model_key
        ctx.prediction = result
        return ctx

    def _predict_rule_based(self, features: dict) -> dict:
        """基于粒度特征的规则模型预测 P80

        核心逻辑：
        - P80 主要由 d80 特征决定（已从特征提取中计算）
        - 加入圆度和均匀系数微调
        - 置信度基于颗粒数和数据质量
        """
        # 基础 P80 = 提取的 d80 值
        p80_base = features.get("p80_estimated", 150.0)

        # 微调因子
        mean_circ = features.get("mean_circularity", 0.7)

        # 圆度越高（颗粒越接近球形），P80 估计越可靠，微调越小
        if mean_circ > 0.85:
            adjustment = 1.0   # 高圆度，几乎不调
        elif mean_circ > 0.7:
            adjustment = 1.05  # 中等圆度，略微上调（非球形颗粒投影偏小）
        else:
            adjustment = 1.10  # 低圆度，明显上调

        p80_predicted = round(p80_base * adjustment, 1)

        # 置信度评估
        confidence = self._compute_confidence(features)

        # 工况判断
        condition = features.get("condition", "正常")
        overgrind_ratio = features.get("overgrinding_ratio", 0)

        # 综合工况消息
        if condition == "过磨":
            message = f"产品粒度过细(P80={p80_predicted}μm)，过磨颗粒占比{overgrind_ratio*100:.1f}%"
        elif condition == "欠磨":
            message = f"产品粒度过粗(P80={p80_predicted}μm)，建议增加研磨时间"
        else:
            message = f"粒度分布正常(P80={p80_predicted}μm)，磨矿效果良好"

        return {
            "p80": p80_predicted,
            "condition": condition,
            "condition_label": {"过磨": 0, "正常": 1, "欠磨": 2}.get(condition, 1),
            "confidence": round(confidence, 3),
            "method": "rule_based",
            "message": message,

            # 辅助信息（前端展示用）
            "d10": features.get("d10", 0),
            "d50": features.get("d50", 0),
            "d80": features.get("d80", 0),
            "d90": features.get("d90", 0),
            "grain_count": features.get("grain_count", 0),
            "overgrinding_ratio": overgrind_ratio,
            "uniformity_coefficient": features.get("uniformity_coefficient", 0),
        }

    @staticmethod
    def _compute_confidence(features: dict) -> float:
        """计算预测置信度

        因素：
        - 颗粒数越多越可信
        - 圆度越高越可信（形状规则）
        - 填充率适中（太低=样本少，太高=过度密集分割不准）
        """
        n = features.get("grain_count", 0)
        circ = features.get("mean_circularity", 0)
        fill = features.get("fill_ratio", 0)

        # 颗粒数得分 (0~0.4)
        if n >= 50:
            score_n = 0.4
        elif n >= 20:
            score_n = 0.2 + 0.2 * (n - 20) / 30
        elif n >= 5:
            score_n = 0.1 + 0.1 * (n - 5) / 15
        else:
            score_n = 0.05 * max(n, 0) / 5

        # 圆度得分 (0~0.3)
        score_circ = min(circ, 1.0) * 0.3

        # 填充率得分 (0~0.3)，最优区间 0.15~0.5
        if 0.15 <= fill <= 0.5:
            score_fill = 0.3
        elif fill < 0.15:
            score_fill = 0.3 * fill / 0.15
        else:
            score_fill = 0.3 * max(0, 1.0 - fill) / 0.5

        return min(score_n + score_circ + score_fill, 1.0)
