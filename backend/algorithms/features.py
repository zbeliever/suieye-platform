import cv2
import numpy as np


def _glcm_features(gray, levels=16):
    small = cv2.resize(gray, (256, 256), interpolation=cv2.INTER_AREA)
    q = np.clip(small // (256 // levels), 0, levels - 1).astype(np.uint8)
    qf = q.ravel()
    glcm = np.zeros((levels, levels), dtype=np.float64)
    for dy, dx in ((0, 1), (-1, 1), (-1, 0), (-1, -1)):
        shifted = np.roll(np.roll(q, dx, axis=1), dy, axis=0).ravel()
        np.add.at(glcm, (qf, shifted), 1.0)
    glcm /= glcm.sum() + 1e-10

    i_idx, j_idx = np.indices(glcm.shape)
    mu_i = (glcm * i_idx).sum()
    mu_j = (glcm * j_idx).sum()
    sig_i = np.sqrt((glcm * (i_idx - mu_i) ** 2).sum() + 1e-10)
    sig_j = np.sqrt((glcm * (j_idx - mu_j) ** 2).sum() + 1e-10)

    contrast = (glcm * (i_idx - j_idx) ** 2).sum()
    correlation = ((glcm * (i_idx - mu_i) * (j_idx - mu_j)).sum()) / (sig_i * sig_j + 1e-10)
    p = glcm[glcm > 0]
    entropy = -np.sum(p * np.log2(p + 1e-10))
    uniformity = np.sum(glcm ** 2)
    energy = np.sqrt(uniformity)
    homogeneity = float(np.sum(glcm / (1.0 + (i_idx - j_idx) ** 2)))  # 逆差矩，纹理稳定性
    variance = float(np.sum(glcm * (i_idx - mu_i) ** 2))  # 纹理方差

    return {
        "texture_contrast": float(contrast),
        "texture_correlation": float(correlation),
        "texture_entropy": float(entropy),
        "texture_uniformity": float(uniformity),
        "texture_energy": float(energy),
        "texture_homogeneity": homogeneity,
        "texture_variance": variance,
    }


def _bubble_metrics(labels, min_area=80):
    sizes = []
    roundness = []
    for label_id in range(1, int(labels.max()) + 1):
        mask = (labels == label_id).astype(np.uint8)
        area = int(mask.sum())
        if area < min_area:
            continue
        sizes.append(float(np.sqrt(area / np.pi) * 2))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        peri = cv2.arcLength(contours[0], True)
        if peri <= 0:
            continue
        circ = 4.0 * np.pi * area / (peri * peri)
        roundness.append(float(np.clip(circ, 0, 1)))

    if not sizes:
        return {
            "bubble_avg_size": 0.0,
            "bubble_size_std": 0.0,
            "bubble_shape_roundness": 0.0,
            "bubble_shape_dispersion": 0.0,
            "n_valid_bubbles": 0,
            "bubble_size_skew": 0.0,
            "bubble_size_kurtosis": 0.0,
        }

    sizes = np.array(sizes, dtype=np.float64)
    roundness = np.array(roundness, dtype=np.float64) if roundness else np.array([0.0])
    mean = float(sizes.mean())
    std = float(sizes.std())
    if std > 1e-6 and len(sizes) > 2:
        skew = float(((sizes - mean) ** 3).mean() / (std ** 3))
        kurt = float(((sizes - mean) ** 4).mean() / (std ** 4))
    else:
        skew = 0.0
        kurt = 0.0
    return {
        "bubble_avg_size": mean,
        "bubble_size_std": std,
        "bubble_shape_roundness": float(roundness.mean()),
        "bubble_shape_dispersion": float(roundness.std()),
        "n_valid_bubbles": int(len(sizes)),
        "bubble_size_skew": skew,
        "bubble_size_kurtosis": kurt,
    }


def extract_features(cropped_bgr, labels, fg_mask=None):
    gray_full = cv2.cvtColor(cropped_bgr, cv2.COLOR_BGR2GRAY)
    gray = gray_full
    if fg_mask is not None:
        gray = gray.copy()
        gray[fg_mask == 0] = 0

    bubble = _bubble_metrics(labels)
    texture = _glcm_features(gray)
    r_mean = float(cropped_bgr[:, :, 2].mean())
    g_mean = float(cropped_bgr[:, :, 1].mean())
    b_mean = float(cropped_bgr[:, :, 0].mean())
    gray_mean = float(gray_full.mean())

    features = {
        **bubble,
        **texture,
        "r_mean": r_mean,
        "g_mean": g_mean,
        "b_mean": b_mean,
        "gray_mean": gray_mean,
        "relative_red": r_mean / (gray_mean + 1e-6),
        "relative_green": g_mean / (gray_mean + 1e-6),
        "relative_blue": b_mean / (gray_mean + 1e-6),
    }
    return features
