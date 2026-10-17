import base64
import os
from io import BytesIO
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageOps


def load_image(path: str) -> np.ndarray:
    pil = Image.open(path)
    pil = ImageOps.exif_transpose(pil)
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    arr = np.array(pil)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def save_uploaded_image(file_storage, path: str) -> tuple[int, int]:
    """Save upload with EXIF orientation applied; return (width, height)."""
    pil = Image.open(file_storage)
    pil = ImageOps.exif_transpose(pil)
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        pil.save(path, format="JPEG", quality=95)
    else:
        pil.save(path, format="PNG")
    w, h = pil.size
    return w, h

def save_image(img: np.ndarray, path: str) -> str:
    """保存图像（中文路径安全：imencode + tofile，cv2.imwrite 在 Windows
    非 ASCII 路径下会静默失败）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    ext = os.path.splitext(path)[1].lower() or ".jpg"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise ValueError(f"图像编码失败: {ext}")
    buf.tofile(path)
    return path


def encode_image_b64(img: np.ndarray, fmt: str = ".png") -> str:
    if len(img.shape) == 2:
        pil = Image.fromarray(img)
    else:
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    buf = BytesIO()
    pil.save(buf, format=fmt.lstrip(".").upper() if fmt != ".jpg" else "JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def decode_image_b64(data: str) -> np.ndarray:
    raw = base64.b64decode(data)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Invalid base64 image data")
    return img


def _watershed_ridges(labels: np.ndarray, foreground_mask: Optional[np.ndarray] = None) -> np.ndarray:
    ridge = (labels == -1).astype(np.uint8) * 255
    if foreground_mask is not None:
        ridge[foreground_mask == 0] = 0

    h, w = ridge.shape
    band = 8
    ridge[:band, :] = 0
    ridge[-band:, :] = 0
    ridge[:, :band] = 0
    ridge[:, -band:] = 0

    n, cc, stats, _ = cv2.connectedComponentsWithStats(ridge, connectivity=8)
    cleaned = np.zeros_like(ridge)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 4:
            cleaned[cc == i] = 255

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel, iterations=1)
    return cleaned


def build_probability_map(labels: np.ndarray, foreground_mask: Optional[np.ndarray] = None) -> np.ndarray:
    ridge = _watershed_ridges(labels, foreground_mask)
    out = np.zeros((*ridge.shape, 3), dtype=np.uint8)
    out[ridge > 0] = (255, 255, 255)
    return out


def draw_boundary_overlay(
    original: np.ndarray,
    labels: np.ndarray,
    foreground_mask: Optional[np.ndarray] = None,
    color: tuple = (0, 0, 255),
) -> np.ndarray:
    out = original.copy()
    if len(out.shape) == 2:
        out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)

    ridge = _watershed_ridges(labels, foreground_mask)
    out[ridge > 0] = color
    return out


def colorize_labels(labels: np.ndarray) -> np.ndarray:
    h, w = labels.shape[:2]
    out = np.zeros((h, w, 3), dtype=np.uint8)
    unique = np.unique(labels)
    rng = np.random.default_rng(42)
    for lbl in unique:
        if lbl <= 0:
            continue
        out[labels == lbl] = rng.integers(50, 255, size=3, dtype=np.uint8)
    out[labels == -1] = (0, 0, 255)
    return out


def overlay_watershed(original: np.ndarray, labels: np.ndarray, alpha: float = 0.45) -> np.ndarray:
    colored = colorize_labels(labels)
    mask = labels != 0
    out = original.copy()
    out[mask] = cv2.addWeighted(original, 1 - alpha, colored, alpha, 0)[mask]
    out[labels == -1] = (0, 0, 255)
    return out
