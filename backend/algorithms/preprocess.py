import cv2
import numpy as np
from scipy.ndimage import gaussian_filter


def _single_scale_retinex(img: np.ndarray, sigma: float) -> np.ndarray:
    img_f = img.astype(np.float64) + 1.0
    blur = gaussian_filter(img_f, sigma=sigma)
    return np.log10(img_f) - np.log10(blur + 1.0)


def apply_msr(img: np.ndarray, sigmas=(15, 80, 250), weights=None) -> np.ndarray:
    """
    Multi-Scale Retinex enhancement.
    Improves contrast under uneven illumination.
    """
    if weights is None:
        weights = [1.0 / len(sigmas)] * len(sigmas)

    if len(img.shape) == 3:
        channels = cv2.split(img)
        enhanced = []
        for ch in channels:
            msr = np.zeros_like(ch, dtype=np.float64)
            for w, s in zip(weights, sigmas):
                msr += w * _single_scale_retinex(ch, s)
            msr = cv2.normalize(msr, None, 0, 255, cv2.NORM_MINMAX)
            enhanced.append(msr.astype(np.uint8))
        return cv2.merge(enhanced)

    msr = np.zeros_like(img, dtype=np.float64)
    for w, s in zip(weights, sigmas):
        msr += w * _single_scale_retinex(img, s)
    return cv2.normalize(msr, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)


def apply_bilateral(
    img: np.ndarray,
    d: int = 9,
    sigma_color: float = 75,
    sigma_space: float = 75,
) -> np.ndarray:
    """Bilateral filter: denoise while preserving edges."""
    return cv2.bilateralFilter(img, d, sigma_color, sigma_space)


def preprocess(img: np.ndarray) -> dict:
    """Run MSR then bilateral filtering."""
    msr = apply_msr(img)
    denoised = apply_bilateral(msr)
    return {"msr": msr, "denoised": denoised, "preprocessed": denoised}
