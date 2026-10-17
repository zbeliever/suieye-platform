import json
import os
import time

import numpy as np

from backend.algorithms.features import extract_features
from backend.algorithms.pipeline import run_full_pipeline
from backend.utils.foam_filter import filter_foam_filenames
from backend.utils.image_utils import load_image

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_FILE = os.path.join(BASE_DIR, "data", "dataset_stats.json")

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
    # 新增：颜色与纹理稳定性特征（原工具 features.xls 中与品位强相关的量）
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

FEATURE_BOUNDS = {
    "bubble_avg_size": (15.0, 80.0),
    "bubble_size_std": (5.0, 45.0),
    "bubble_shape_roundness": (0.2, 0.95),
    "bubble_shape_dispersion": (0.02, 0.45),
    "texture_contrast": (0.5, 8.0),
    "texture_correlation": (0.3, 0.99),
    "texture_entropy": (1.5, 5.5),
    "texture_uniformity": (0.02, 0.25),
    "texture_energy": (0.1, 0.55),
    "r_mean": (60.0, 220.0),
    "n_foams": (10.0, 400.0),
    "n_valid_bubbles": (10.0, 400.0),
    "g_mean": (40.0, 220.0),
    "b_mean": (40.0, 220.0),
    "gray_mean": (30.0, 200.0),
    "relative_red": (0.2, 1.5),
    "relative_green": (0.8, 3.0),
    "relative_blue": (0.6, 2.5),
    "texture_homogeneity": (0.05, 0.9),
    "texture_variance": (0.5, 60.0),
    "bubble_size_skew": (0.0, 5.0),
    "bubble_size_kurtosis": (1.0, 30.0),
}

MAX_NEW_PER_REFRESH = 8
MAX_ROWS_FOR_STATS = 120


def _is_image(name):
    return name.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp"))


def list_upload_images(upload_dir):
    if not os.path.isdir(upload_dir):
        return []
    files = []
    for name in sorted(os.listdir(upload_dir)):
        if _is_image(name):
            files.append(name)
    return filter_foam_filenames(files)


def _extract_from_path(path):
    img = load_image(path)
    result = run_full_pipeline(img, with_prediction=False)
    features = extract_features(
        result["cropped"],
        result["watershed"]["labels"],
        result["markers"]["foreground_mask"],
    )
    features["n_foams"] = float(result["n_foams"])
    return features


def _compute_stats(rows):
    stats = {}
    for key in FEATURE_KEYS:
        vals = [float(r.get(key, 0.0)) for r in rows if key in r]
        if len(vals) >= 2:
            arr = np.array(vals, dtype=np.float64)
            stats[key] = {"mean": float(arr.mean()), "std": float(max(arr.std(), 1e-6))}
        elif len(vals) == 1:
            v = vals[0]
            lo, hi = FEATURE_BOUNDS.get(key, (v * 0.5, v * 1.5))
            stats[key] = {"mean": v, "std": max((hi - lo) / 4.0, 1e-6)}
        else:
            lo, hi = FEATURE_BOUNDS.get(key, (0.0, 1.0))
            stats[key] = {"mean": (lo + hi) / 2.0, "std": max((hi - lo) / 4.0, 1e-6)}
    return stats


def refresh_dataset_stats(upload_dir, force=False):
    images = list_upload_images(upload_dir)
    newest_mtime = 0.0
    for name in images:
        newest_mtime = max(newest_mtime, os.path.getmtime(os.path.join(upload_dir, name)))

    cached = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cached = json.load(f)

    if not force and cached.get("images") == images and cached.get("mtime", 0) >= newest_mtime:
        return cached

    row_map = {r["image"]: r for r in cached.get("rows", []) if r.get("image")}
    rows = []
    pending = []

    for name in images:
        path = os.path.join(upload_dir, name)
        mtime = os.path.getmtime(path)
        old = row_map.get(name)
        if not force and old and old.get("mtime") == mtime:
            rows.append(old)
        else:
            pending.append((name, path, mtime))

    for name, path, mtime in pending[:MAX_NEW_PER_REFRESH]:
        try:
            feats = _extract_from_path(path)
            rows.append({"image": name, "mtime": mtime, **feats})
        except Exception:
            continue

    for name, _path, mtime in pending[MAX_NEW_PER_REFRESH:]:
        old = row_map.get(name)
        if old:
            rows.append(old)

    rows = rows[-MAX_ROWS_FOR_STATS:]
    payload = {
        "updated_at": time.time(),
        "mtime": newest_mtime,
        "images": images,
        "count": len(rows),
        "rows": rows,
        "stats": _compute_stats(rows),
        "pending": max(0, len(pending) - MAX_NEW_PER_REFRESH),
    }
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def get_dataset_stats(upload_dir):
    return refresh_dataset_stats(upload_dir, force=False)


def get_cached_stats_fast(upload_dir):
    """预测用：只读缓存，不在请求里批量跑分割。"""
    images = list_upload_images(upload_dir)
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("stats"):
                cached["images"] = images
                return cached
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "updated_at": time.time(),
        "images": images,
        "count": 0,
        "rows": [],
        "stats": _compute_stats([]),
        "pending": len(images),
    }
