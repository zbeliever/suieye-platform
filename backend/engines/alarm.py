"""告警规则评估（场景包 YAML 的 alarm_rules 段驱动）。

规则形如：
    - name: pb_too_low
      field: predicted.pb_grade     # 点路径：predicted.* / features.* / metrics.*
      condition: "<"                # < <= > >= == !=
      threshold: 1.0
      level: warning                # warning / danger
      cooldown_sec: 600             # 冷却（流式场景用，单次分析仅透传）

通用设计：任何场景包只要写规则就能触发告警，无浮选专属逻辑。
"""

import operator

_OPS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}

# 字段路径 → 中文显示名映射
_FIELD_LABELS = {
    "predicted.pb_grade_xrf": "铅品位",
    "predicted.pb_grade": "铅品位",
    "predicted.zn_grade_xrf": "锌品位",
    "predicted.zn_grade": "锌品位",
    "predicted.p80": "P80粒度",
    "metrics.overgrinding_ratio": "过磨率",
    "features.overgrinding_ratio": "过磨率",
    # 安全巡检（safety.yaml 的规则字段）—— 漏了这几个会让设置页/告警文案显示英文点路径
    "features.persons_in_danger_zone": "危险区人员",
    "features.belt_offset_px": "皮带跑偏量",
    "features.belt_misalignment_detected": "皮带跑偏",
    "features.foreign_object_count": "异物数量",
    "features.ppe_compliance_rate": "PPE 合规率",
}

# 比较符中文显示
_OP_LABELS = {"<": "低于", "<=": "不超过", ">": "高于", ">=": "不低于", "==": "等于", "!=": "不等于"}


def _resolve_field(ctx, dotted: str):
    """按点路径在 ctx 产物中取值：predicted.pb_grade → ctx.prediction['pb_grade']

    兼容：若主字段名取不到，尝试常见别名（如 pb_grade ↔ pb_grade_xrf）。
    """
    parts = dotted.split(".")
    if len(parts) != 2:
        return None
    section, key = parts
    bag = {
        "predicted": ctx.prediction,
        "features": ctx.features,
        "metrics": ctx.metrics,
    }.get(section)
    if bag is None:
        return None
    val = bag.get(key)
    # 字段名兼容：常见别名映射
    if val is None:
        ALIASES = {
            "pb_grade": "pb_grade_xrf",
            "pb_grade_xrf": "pb_grade",
            "zn_grade": "zn_grade_xrf",
            "zn_grade_xrf": "zn_grade",
        }
        alias = ALIASES.get(key)
        if alias:
            val = bag.get(alias)
    return float(val) if isinstance(val, (int, float)) else None


def evaluate_alarm_rules(rules: list, ctx) -> list:
    """评估所有规则，返回触发列表 [{name, level, message, value, threshold}]"""
    triggered = []
    for rule in rules or []:
        op = _OPS.get(rule.get("condition", "<"))
        if op is None:
            continue
        value = _resolve_field(ctx, rule.get("field", ""))
        if value is None:
            continue
        threshold = float(rule.get("threshold", 0))
        if op(value, threshold):
            # 优先使用 YAML 中定义的中文消息
            msg = rule.get("message", "")
            if not msg:
                # fallback：自动生成中文描述
                field_label = _FIELD_LABELS.get(rule.get("field", ""), rule.get("field", ""))
                op_label = _OP_LABELS.get(rule.get("condition", "<"), rule.get("condition", ""))
                msg = f"{field_label} {round(value, 4)} {op_label} 阈值 {threshold}"
            triggered.append({
                "rule_name": rule.get("name", "unnamed"),
                "level": rule.get("level", "warning"),
                "field": rule.get("field"),
                "value": round(value, 4),
                "threshold": threshold,
                "condition": rule.get("condition"),
                "cooldown_sec": rule.get("cooldown_sec", 0),
                "message": msg,
            })
    return triggered


# ── 供 UI 复用的公开映射（设置页要展示"字段中文名 + 比较符中文"，必须与告警文案同源）──
# 私有变量判据不变，这里只是给外部（如 backend/api/routers/settings.py）一个稳定入口，
# 避免设置页自己再写一份中文映射 —— 两套映射迟早会漂移。
FIELD_LABELS = _FIELD_LABELS
OP_LABELS = _OP_LABELS
