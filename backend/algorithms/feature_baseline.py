"""泡沫特征基线 — 把原始特征换算成可解释的偏离度（z-score）

背景（2026-10-13 排查发现）：
    `predictor._predict_xgb` 里传给 `zscore_features` 的归一化参数键名被写死成字面量 "k"
    （`{"k": {"mean": 0, "std": 1} for k in FEATURE_KEYS}`），永远匹配不到真实特征名，
    于是每个特征都回落到 mean=0 / std=1，`z_scores` 退化成**原始特征值**——
    字段名说"偏离训练集均值的标准差倍数"，实际存的是 r_mean=81、n_foams=81 这种原始量纲。

本模块提供真实、可追溯的基线：
    直接统计 analysis_records 表里该场景的历史真实特征分布（浮选约 3000 条），
    算出每个特征的 μ / σ 与分位点，用于把原始值换算成真正的 z-score。

不依赖 SQLAlchemy（算法层保持独立），只读方式直连 SQLite，进程内缓存 300s。
"""

import json
import os
import sqlite3
import threading
import time

_CACHE = {"baseline": None, "meta": None, "ts": 0.0}
_LOCK = threading.Lock()
_TTL = 300.0          # 缓存有效期（秒）
_MIN_SAMPLES = 20     # 少于这个样本数不认为基线可信


def _is_sane(feat: dict) -> bool:
    """判定一条特征记录是否为「分割成功」的正常口径

    背景（2026-10-13 排查）：analysis_records 里 3006 / 3033 条浮选记录的
    特征其实是分割失败的产物——整张图被当成一个连通域，于是
    bubble_avg_size ≈ 2800px（应为几十像素）、n_foams ≈ 3（应为几十上百）、
    texture_energy ≈ 1e5（应在 0~1）。用这批数据算基线会得到毫无意义的 z。

    只有约 27 条是真实成功的泡沫分割结果，本函数把它们筛出来。
    """
    try:
        if float(feat.get("bubble_avg_size", 0.0)) >= 500:
            return False
        if float(feat.get("n_foams", 0.0)) < 5:
            return False
        if float(feat.get("texture_energy", 0.0)) > 10:
            return False
    except (TypeError, ValueError):
        return False
    return True

# 22 维泡沫特征（与 dataset_stats.FEATURE_KEYS 一致）
FEATURE_KEYS = [
    "bubble_avg_size",
    "bubble_size_std",
    "bubble_shape_roundness",
    "bubble_shape_dispersion",
    "texture_contrast",
    "texture_correlation",
    "texture_entropy",
    "texture_uniformity",
    "texture_energy",
    "r_mean",
    "n_foams",
    "n_valid_bubbles",
    "g_mean",
    "b_mean",
    "gray_mean",
    "relative_red",
    "relative_green",
    "relative_blue",
    "texture_homogeneity",
    "texture_variance",
    "bubble_size_skew",
    "bubble_size_kurtosis",
]


def _db_path() -> str:
    from backend.config import settings
    url = settings.DATABASE_URL or "sqlite:///./suieye.db"
    return os.path.abspath(url.split("sqlite:///")[-1])


def _pct(sorted_vals, q: float) -> float:
    """分位点（线性插值），q ∈ [0,1]"""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return round(sorted_vals[0], 4)
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return round(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac, 4)


def compute_baseline(scene_id: str = "flotation", min_samples: int = _MIN_SAMPLES):
    """扫描历史分析记录，统计每个特征的分布（均值/标准差/分位点）

    返回 (baseline, meta)：baseline = {特征: {mean,std,n,p5...}}，
    meta 说明样本口径（用了多少条、排除了多少条坏数据）。
    """
    path = _db_path()
    meta = {"scene": scene_id, "sample_n": 0, "excluded": 0, "sane": True}
    if not os.path.isfile(path):
        return {}, meta

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT features FROM analysis_records "
            "WHERE scene_id=? AND features IS NOT NULL",
            (scene_id,),
        ).fetchall()
    except sqlite3.Error:
        return {}, meta
    finally:
        con.close()

    cols = {}
    sane_n = 0
    excluded = 0
    for (raw,) in rows:
        try:
            feat = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        if not isinstance(feat, dict):
            continue
        if not _is_sane(feat):
            excluded += 1
            continue
        sane_n += 1
        for k, v in feat.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cols.setdefault(k, []).append(float(v))

    meta.update({"sample_n": sane_n, "excluded": excluded})

    baseline = {}
    for k, vals in cols.items():
        if len(vals) < min_samples:
            continue
        vals.sort()
        n = len(vals)
        mean = sum(vals) / n
        var = sum((x - mean) ** 2 for x in vals) / n
        sample_std = var ** 0.5
        p5, p95 = _pct(vals, 0.05), _pct(vals, 0.95)
        # σ 下限保护：样本少时 σ 可能被压得极小，导致 z 爆表。
        # 用 P5~P95 跨度（正态下 ≈ 3.29σ）的 1/6 兜底。
        floor = max((p95 - p5) / 6.0, 1e-6)
        baseline[k] = {
            "mean": round(mean, 4),
            "std": round(max(sample_std, floor), 4),
            "sample_std": round(max(sample_std, 1e-6), 4),
            "n": n,
            "min": round(vals[0], 4),
            "p5": p5,
            "p25": _pct(vals, 0.25),
            "p50": _pct(vals, 0.50),
            "p75": _pct(vals, 0.75),
            "p95": p95,
            "max": round(vals[-1], 4),
        }
    return baseline, meta


def get_baseline(scene_id: str = "flotation", force: bool = False) -> dict:
    """带缓存的基线读取"""
    return _cached(scene_id, force)[0]


def get_baseline_meta(scene_id: str = "flotation", force: bool = False) -> dict:
    """基线样本口径（用了多少条干净样本、排除了多少条坏数据）"""
    return _cached(scene_id, force)[1]


def _cached(scene_id: str, force: bool):
    now = time.time()
    with _LOCK:
        if not force and _CACHE["baseline"] is not None and (now - _CACHE["ts"]) < _TTL:
            return _CACHE["baseline"], _CACHE["meta"]
    baseline, meta = compute_baseline(scene_id)
    if baseline:
        with _LOCK:
            _CACHE["baseline"], _CACHE["meta"], _CACHE["ts"] = baseline, meta, now
    return baseline, meta


def get_stats_dict(scene_id: str = "flotation") -> dict:
    """转成 zscore_features / predict_grade 期望的 {key: {mean, std}} 格式"""
    bl = get_baseline(scene_id)
    if not bl:
        return {}
    return {k: {"mean": v["mean"], "std": v["std"]} for k, v in bl.items()}


def _percentile(value: float, b: dict) -> float:
    """用分位点分段线性估计百分位（0-100）"""
    pts = [("min", 0.0), ("p5", 5.0), ("p25", 25.0), ("p50", 50.0),
           ("p75", 75.0), ("p95", 95.0), ("max", 100.0)]
    xs = [(b.get(k), q) for k, q in pts if b.get(k) is not None]
    if len(xs) < 2:
        return 50.0
    if value <= xs[0][0]:
        return xs[0][1]
    if value >= xs[-1][0]:
        return xs[-1][1]
    for i in range(len(xs) - 1):
        x0, q0 = xs[i]
        x1, q1 = xs[i + 1]
        if x0 <= value <= x1:
            if x1 == x0:
                return round(q0, 1)
            return round(q0 + (value - x0) / (x1 - x0) * (q1 - q0), 1)
    return 50.0


def evaluate_features(raw: dict, scene_id: str = "flotation") -> dict:
    """把一份原始特征换算成带解释的偏离清单

    返回 {source, sample_n, items:[{key, value, mean, std, z, direction, percentile, severity}], 
          significant: 显著偏离条数}
    """
    bl = get_baseline(scene_id)
    meta = get_baseline_meta(scene_id)
    if not bl or not raw:
        return {"source": "unavailable", "sample_n": 0, "items": [], "significant": 0}

    items = []
    for k, v in raw.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        b = bl.get(k)
        if not b:
            continue
        val = float(v)
        z = (val - b["mean"]) / b["std"] if b["std"] else 0.0
        abs_z = abs(z)
        items.append({
            "key": k,
            "value": round(val, 3),
            "mean": b["mean"],
            "std": b["std"],
            "z": round(z, 2),
            "abs_z": round(abs_z, 2),
            "direction": "偏高" if z > 0 else "偏低",
            "percentile": _percentile(val, b),
            "severity": "high" if abs_z >= 2.5 else ("medium" if abs_z >= 1.5 else "normal"),
        })

    items.sort(key=lambda d: d["abs_z"], reverse=True)
    return {
        "source": "historical_baseline",   # 基线来自同口径的真实历史记录
        "sample_n": meta.get("sample_n", 0),
        "excluded_bad": meta.get("excluded", 0),
        "items": items,
        "significant": sum(1 for i in items if i["abs_z"] >= 1.5),
    }


def looks_like_raw_features(values) -> bool:
    """判断一组"z_scores"是不是原始特征值冒充的（历史脏数据）

    z-score 超过 8 的概率在正态下约 1e-15，实际不可能；而原始量纲里
    r_mean / n_foams 这类必然 > 8。所以只要有一个 |v| > 8 就判定为原始值。
    """
    if not values:
        return False
    for v in values:
        try:
            if abs(float(v)) > 8:
                return True
        except (TypeError, ValueError):
            continue
    return False
