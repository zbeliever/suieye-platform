import cv2
import numpy as np
from scipy.ndimage import binary_fill_holes, maximum_filter


def crop_black_background(img: np.ndarray, threshold: int = 15) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    mask = gray > threshold
    if not np.any(mask):
        h, w = gray.shape
        return img, (0, 0, w, h)

    coords = np.column_stack(np.where(mask))
    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0) + 1
    return img[y0:y1, x0:x1], (x0, y0, x1, y1)


def _foam_foreground(gray: np.ndarray) -> np.ndarray:
    fg = np.where(gray > 28, 255, 0).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel, iterations=2)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel, iterations=1)
    return binary_fill_holes(fg > 0).astype(np.uint8) * 255


def _binary_display(gray: np.ndarray) -> np.ndarray:
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.count_nonzero(otsu) > otsu.size * 0.5:
        return otsu
    return cv2.bitwise_not(otsu)


def _distance_for_display(fg: np.ndarray) -> np.ndarray:
    work = fg.copy()
    work[0, :] = work[-1, :] = work[:, 0] = work[:, -1] = 0
    opened = cv2.morphologyEx(
        work, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)), iterations=1
    )
    return cv2.distanceTransform(opened, cv2.DIST_L2, 5)


def _estimate_min_dist(gray: np.ndarray, fg: np.ndarray) -> int:
    area = np.count_nonzero(fg)
    if area == 0:
        return 10
    avg_bubble_area = area / max(40, min(200, area // 800))
    radius = int(np.sqrt(avg_bubble_area / np.pi))
    return max(8, min(24, int(radius * 1.1)))


def _find_bubble_peaks(gray: np.ndarray, fg: np.ndarray, min_dist: int) -> np.ndarray:
    """Detect bubble centers as bright local maxima on CLAHE image."""
    blurred = cv2.GaussianBlur(gray, (5, 5), 1.0)
    size = min_dist * 2 + 1
    local_max = maximum_filter(blurred, size=size) == blurred

    fg_vals = blurred[fg > 0]
    if fg_vals.size == 0:
        return np.zeros(gray.shape, dtype=bool)
    thresh = float(np.percentile(fg_vals, 65))
    peaks = local_max & (blurred > thresh) & (fg > 0)

    peak_u8 = peaks.astype(np.uint8) * 255
    n_labels, labels = cv2.connectedComponents(peak_u8)
    if n_labels <= 1:
        return peaks

    kept = np.zeros_like(peaks)
    for lbl in range(1, n_labels):
        ys, xs = np.where(labels == lbl)
        cy, cx = int(ys.mean()), int(xs.mean())
        kept[cy, cx] = True
    return kept


def _trim_foam_roi(cropped: np.ndarray, gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Remove top gray bar only when detected; keep full frame for standard foam photos."""
    h = gray.shape[0]
    if h < 80:
        return cropped, gray

    row_edge = cv2.Canny(gray, 40, 120).sum(axis=1).astype(np.float64)
    top_h = max(20, h // 4)
    top_mean = row_edge[:top_h].mean()
    rest_mean = row_edge[top_h:].mean() if top_h < h else top_mean

    # Top uniform gray bar: low edge density in top quarter vs rest
    if top_mean >= rest_mean * 0.45:
        return cropped, gray

    active = np.where(row_edge > row_edge.max() * 0.15)[0]
    if active.size == 0:
        return cropped, gray

    y0, y1 = int(active[0]), int(active[-1]) + 1
    if y1 - y0 < h * 0.55:
        return cropped, gray

    return cropped[y0:y1], gray[y0:y1]


def extract_foam_roi(img: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Crop foam region from the original upload (no MSR)."""
    cropped, bbox = crop_black_background(img)
    gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY) if len(cropped.shape) == 3 else cropped
    cropped, _gray = _trim_foam_roi(cropped, gray)
    return cropped, bbox


def analyze_markers(cropped: np.ndarray) -> dict:
    """Run marker detection on an already cropped ROI."""
    gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY) if len(cropped.shape) == 3 else cropped

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    filled = _foam_foreground(enhanced)
    binary_vis = _binary_display(enhanced)
    dist = _distance_for_display(filled)
    dist_norm = cv2.normalize(dist, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

    min_dist = _estimate_min_dist(enhanced, filled)
    peaks = _find_bubble_peaks(enhanced, filled, min_dist)

    peak_map = np.zeros(enhanced.shape, dtype=np.uint8)
    peak_map[peaks] = 255
    n_labels, labels = cv2.connectedComponents(peak_map)

    markers_vis = np.zeros((*gray.shape, 3), dtype=np.uint8)
    markers_vis[peaks] = (255, 255, 255)

    return {
        "clahe": cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR),
        "binary": cv2.cvtColor(binary_vis, cv2.COLOR_GRAY2BGR),
        "distance": cv2.applyColorMap(dist_norm, cv2.COLORMAP_JET),
        "markers_raw": markers_vis,
        "markers": labels.astype(np.int32),
        "peak_mask": peaks,
        "seed_radius": max(3, min_dist // 2),
        "n_markers": n_labels - 1,
        "foreground_mask": filled,
        "distance_raw": dist,
    }


def prepare_markers(img: np.ndarray) -> dict:
    cropped, bbox = extract_foam_roi(img)
    data = analyze_markers(cropped)
    data["cropped"] = cropped
    data["cropped_display"] = cropped
    data["bbox"] = bbox
    return data
