import json
import os

import numpy as np

from backend.algorithms.dataset_stats import FEATURE_BOUNDS, FEATURE_KEYS

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODEL_FILE = os.path.join(BASE_DIR, "data", "model_config.json")
XGB_MODEL_FILE = os.path.join(BASE_DIR, "data", "model_pb_xgb_v1.json")

# XGBoost 模型使用的特征子集（extract_features 输出与 catalog features_dataset 的交集）
XGB_FEATURES = [
    "bubble_avg_size",
    "bubble_size_std",
    "texture_contrast",
    "texture_correlation",
    "texture_entropy",
    "texture_energy",
    "r_mean",
]

# ---- XGBoost 真模型（3004 张真实图像训练）----
_xgb_model = None
_xgb_loaded = False


def _load_xgb_model():
    """懒加载 XGBoost 模型，只尝试一次。"""
    global _xgb_model, _xgb_loaded
    if _xgb_loaded:
        return _xgb_model
    _xgb_loaded = True
    if not os.path.exists(XGB_MODEL_FILE):
        return None
    try:
        import xgboost as xgb
        m = xgb.XGBRegressor()
        m.load_model(XGB_MODEL_FILE)
        _xgb_model = m
        return m
    except Exception:
        return None


def _baseline_stats() -> dict:
    """真实特征基线（μ/σ），用于把原始特征换算成真正的 z-score

    2026-10-13 修复：此处原先传的是 `{"k": {"mean": 0, "std": 1} for k in FEATURE_KEYS}`
    ——字典键被写死成字面量 "k"，永远匹配不到 `zscore_features` 里的真实特征名，
    于是每个特征都回落到 mean=0/std=1，z 退化成**原始特征值**
    （r_mean=81、n_foams=81 这种量纲），与字段名"偏离标准差倍数"完全不符。

    基线来自 analysis_records 里同口径的历史真实特征分布（见 feature_baseline）。
    拿不到基线时回落到中性值，仅影响展示、不影响品位预测。
    """
    neutral = {"k": {"mean": 0.0, "std": 1.0}}
    try:
        from backend.algorithms.feature_baseline import get_stats_dict
        return get_stats_dict() or neutral
    except Exception:
        return neutral


def _predict_xgb(features):
    """用 XGBoost 模型预测品位。返回 (grade, z) 或 None。"""
    model = _load_xgb_model()
    if model is None:
        return None
    try:
        x = np.array([[float(features.get(k, 0.0)) for k in XGB_FEATURES]], dtype=np.float32)
        pred = float(model.predict(x)[0])
        z = zscore_features(features, _baseline_stats())
        return round(float(np.clip(pred, 0.3, 4.0)), 2), z
    except Exception:
        return None


# 品位与泡沫视觉特征的关联权重（选矿泡沫：小泡、多泡、纹理丰富 → 品位偏高）
GRADE_WEIGHTS = {
    "bubble_avg_size": -0.22,
    "bubble_size_std": -0.10,
    "bubble_shape_roundness": 0.14,
    "bubble_shape_dispersion": 0.08,
    "texture_contrast": 0.18,
    "texture_correlation": -0.06,
    "texture_entropy": 0.16,
    "texture_uniformity": -0.05,
    "texture_energy": 0.10,
    "r_mean": 0.08,
    "n_foams": 0.12,
}


def _norm01(val, key):
    lo, hi = FEATURE_BOUNDS.get(key, (0.0, 1.0))
    return float(np.clip((val - lo) / (hi - lo + 1e-6), 0.0, 1.0))


def zscore_features(features, stats):
    z = {}
    for key in FEATURE_KEYS:
        val = float(features.get(key, 0.0))
        stat = stats.get(key, {"mean": 0.0, "std": 1.0})
        std = stat["std"] if stat["std"] > 1e-6 else 1.0
        z[key] = (val - stat["mean"]) / std
    return z


def _heuristic_grade(features, stats, dataset_count):
    """无训练模型时的启发式回退：多图 z-score 加权 / 单图物理区间归一化。"""
    z = zscore_features(features, stats)
    if dataset_count >= 2:
        score = sum(GRADE_WEIGHTS.get(k, 0.0) * z.get(k, 0.0) for k in FEATURE_KEYS)
        return 1.25 + score * 0.28, z
    # 单张图：直接用该图真实特征值映射到品位区间
    parts = [
        1.0 - _norm01(features.get("bubble_avg_size", 40), "bubble_avg_size"),
        _norm01(features.get("texture_contrast", 2), "texture_contrast"),
        _norm01(features.get("texture_entropy", 3), "texture_entropy"),
        _norm01(features.get("n_foams", 50), "n_foams"),
        _norm01(features.get("bubble_shape_roundness", 0.5), "bubble_shape_roundness"),
        _norm01(features.get("r_mean", 128), "r_mean"),
    ]
    return 0.75 + float(np.mean(parts)) * 1.85, z


def predict_grade(features, stats, dataset_count=1):
    """根据真实图片特征预测品位。

    优先级：XGBoost 真模型（3004 张图训练）> model_config.json 训练模型 > 手写权重启发式。
    """
    # ---- 路径 1：XGBoost 真模型 ----
    xgb_result = _predict_xgb(features)
    if xgb_result is not None:
        return xgb_result

    # ---- 路径 2：model_config.json 里的训练模型 ----
    model = load_model()
    mtype = model.get("type")

    if mtype == "linear_regression":
        fstats = model.get("feature_stats") or {}
        weights = model.get("weights") or {}
        keys = model.get("feature_keys") or FEATURE_KEYS
        z = {}
        score = 0.0
        for k in keys:
            val = float(features.get(k, 0.0))
            s = fstats.get(k, {"mean": 0.0, "std": 1.0})
            std = s["std"] if s["std"] > 1e-6 else 1.0
            z[k] = (val - s["mean"]) / std
            score += weights.get(k, 0.0) * z[k]
        grade = float(model.get("b0", 0.0)) + score
    elif mtype in ("random_forest", "gradient_boosting"):
        keys = model.get("feature_keys") or FEATURE_KEYS
        fstats = model.get("feature_stats") or {}
        z = zscore_features(features, fstats)
        mf = model.get("model_file")
        path = os.path.join(os.path.dirname(MODEL_FILE), mf) if mf else None
        grade = None
        if path and os.path.exists(path):
            try:
                import joblib

                reg = joblib.load(path)
                x = np.array([[float(features.get(k, 0.0)) for k in keys]], dtype=np.float64)
                grade = float(reg.predict(x)[0])
            except Exception:
                grade = None
        if grade is None:
            grade, z = _heuristic_grade(features, stats, dataset_count)
    else:
        grade, z = _heuristic_grade(features, stats, dataset_count)

    grade = round(float(np.clip(grade, 0.3, 4.0)), 2)
    return grade, z


def save_model_from_dataset(stats, dataset_count):
    # 已有训练好的回归模型时不覆盖，保留数据学到的权重（否则下次刷新统计会冲掉训练结果）
    existing = load_model()
    if existing.get("type") == "linear_regression":
        return existing
    payload = {
        "source": "real_images",
        "dataset_count": dataset_count,
        "stats": stats,
        "weights": GRADE_WEIGHTS,
    }
    os.makedirs(os.path.dirname(MODEL_FILE), exist_ok=True)
    with open(MODEL_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def load_model():
    if os.path.exists(MODEL_FILE):
        with open(MODEL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"stats": {}, "dataset_count": 0}
