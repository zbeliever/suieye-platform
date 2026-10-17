import cv2
import numpy as np
from scipy.ndimage import gaussian_laplace
from typing import Optional


def _enhance_gray(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def detect_log_edges(gray: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    log_resp = gaussian_laplace(gray.astype(np.float64), sigma=sigma)
    edges = np.abs(log_resp)
    edges = cv2.normalize(edges, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    thresh = max(30, int(edges.max() * 0.18))
    _, edges_bin = cv2.threshold(edges, thresh, 255, cv2.THRESH_BINARY)
    return edges_bin


def morphological_gradient(gray: np.ndarray, ksize: int = 5) -> np.ndarray:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
    return cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, kernel)


def _canny_edges(gray: np.ndarray) -> np.ndarray:
    v = float(np.median(gray))
    lower = int(max(0, 0.5 * v))
    upper = int(min(255, 1.2 * v))
    edges = cv2.Canny(gray, lower, upper)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    return cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)


def merge_edges(log_edges: np.ndarray, morph_grad: np.ndarray, canny: np.ndarray) -> np.ndarray:
    _, morph_bin = cv2.threshold(morph_grad, 50, 255, cv2.THRESH_BINARY)
    merged = cv2.bitwise_and(log_edges, morph_bin)
    merged = cv2.bitwise_or(merged, cv2.bitwise_and(canny, morph_bin))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    merged = cv2.morphologyEx(merged, cv2.MORPH_CLOSE, kernel, iterations=1)
    return merged


def build_gradient(
    img: np.ndarray,
    markers: np.ndarray,
    foreground_mask: np.ndarray,
    dist: Optional[np.ndarray] = None,
) -> dict:
    gray = _enhance_gray(img)

    morph_grad = morphological_gradient(gray)
    log_edges = detect_log_edges(gray, sigma=1.0)
    canny = _canny_edges(gray)
    edges = merge_edges(log_edges, morph_grad, canny)

    gradient = morph_grad.copy()
    strong = cv2.bitwise_and(edges, (morph_grad > 60).astype(np.uint8) * 255)
    gradient[strong > 0] = 255
    gradient[markers > 0] = 0
    gradient[foreground_mask == 0] = 255

    return {
        "morph_gradient": morph_grad,
        "log_edges": log_edges,
        "merged_edges": edges,
        "gradient": gradient,
    }


def watershed_segment(
    img: np.ndarray,
    markers: np.ndarray,
    gradient: np.ndarray,
    peak_mask: Optional[np.ndarray] = None,
    seed_radius: int = 4,
) -> dict:
    ws_markers = np.zeros_like(markers, dtype=np.int32)

    if peak_mask is not None and np.any(peak_mask):
        sure_fg = np.zeros(markers.shape, dtype=np.uint8)
        for y, x in zip(*np.where(peak_mask)):
            cv2.circle(sure_fg, (int(x), int(y)), seed_radius, 255, -1)
        n_fg, fg_labels = cv2.connectedComponents(sure_fg)
        for lbl in range(1, n_fg):
            ws_markers[fg_labels == lbl] = lbl
    else:
        for idx, lbl in enumerate(np.unique(markers)[1:], start=1):
            ws_markers[markers == lbl] = idx

    ws_markers[gradient == 255] = 0

    gradient_3ch = cv2.cvtColor(gradient, cv2.COLOR_GRAY2BGR)
    labels = cv2.watershed(gradient_3ch, ws_markers)

    n_regions = len(np.unique(labels[labels > 0]))
    return {"labels": labels, "n_regions": n_regions}


def _merge_small_regions(labels: np.ndarray, min_area_ratio: float = 0.003) -> np.ndarray:
    """Merge regions smaller than min_area_ratio * total_foreground into their
    largest adjacent neighbor. This fixes over-segmentation from watershed.

    Args:
        labels: Watershed output label map (0 = background).
        min_area_ratio: Minimum region size as fraction of total non-zero area.
    """
    out = labels.copy()
    unique_lbls = np.unique(out)
    foreground = out > 0
    total_fg = foreground.sum()
    if total_fg == 0:
        return out
    min_area = int(total_fg * min_area_ratio)

    # Compute region sizes
    sizes = {}
    for lbl in unique_lbls:
        if lbl == 0:
            continue
        sizes[lbl] = (out == lbl).sum()

    # Find small regions and merge each into its largest neighbor
    small = [lbl for lbl, sz in sizes.items() if sz < min_area]
    if not small:
        return out

    # Build adjacency by dilating each region and finding overlaps
    for lbl in small:
        mask = (out == lbl).astype(np.uint8)
        # Dilate to find neighbors
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        dilated = cv2.dilate(mask, kernel, iterations=2)
        # Neighbor labels (excluding self and background)
        neighbor_mask = (dilated > 0) & (out != lbl) & (out > 0)
        neighbor_lbls = np.unique(out[neighbor_mask])
        if len(neighbor_lbls) == 0:
            continue
        # Pick the largest neighbor
        best_neighbor = max(neighbor_lbls, key=lambda n: sizes.get(n, 0))
        out[out == lbl] = best_neighbor
        # Update size of the receiving region
        sizes[best_neighbor] = sizes.get(best_neighbor, 0) + sizes[lbl]

    # Relabel to make contiguous (optional but clean)
    return _relabel_contiguous(out)


def _relabel_contiguous(labels: np.ndarray) -> np.ndarray:
    """Relabel so that non-zero labels are contiguous starting from 1."""
    out = np.zeros_like(labels)
    unique_sorted = sorted(np.unique(labels))
    mapping = {old: new for new, old in enumerate(unique_sorted) if old != 0}
    for old, new in mapping.items():
        out[labels == old] = new
    return out


def watershed_only(img: np.ndarray) -> dict:
    gray = _enhance_gray(img)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    opening = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=2)
    sure_bg = cv2.dilate(opening, kernel, iterations=3)
    dist = cv2.distanceTransform(opening, cv2.DIST_L2, 5)
    _, sure_fg = cv2.threshold(dist, 0.4 * dist.max(), 255, 0)
    sure_fg = sure_fg.astype(np.uint8)
    unknown = cv2.subtract(sure_bg, sure_fg)
    _, markers = cv2.connectedComponents(sure_fg)
    markers = markers + 1
    markers[unknown == 255] = 0

    gradient_3ch = cv2.cvtColor(morphological_gradient(gray), cv2.COLOR_GRAY2BGR)
    labels = cv2.watershed(gradient_3ch, markers.astype(np.int32))
    return {"labels": labels, "n_regions": len(np.unique(labels[labels > 0]))}
