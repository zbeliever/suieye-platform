"""增强 Filter：多方法图像增强（按场景需求选择）

支持的方法：
- msr_bilateral: MSR + 双边滤波（浮选场景，处理不均匀光照）
- clahe_sharpen: CLAHE + 锐化（粒度场景，突出颗粒边缘）
- auto: 自动选择
"""

import cv2
import numpy as np

from backend.algorithms.preprocess import apply_msr, apply_bilateral, preprocess
from backend.engines.base import Engine, EngineContext


class EnhanceEngine(Engine):
    name = "enhance"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        img = ctx.cropped_image if ctx.cropped_image is not None else ctx.raw_image
        if img is None:
            return ctx

        method = params.get("method", "msr_bilateral")

        if method == "msr_bilateral":
            result = _enhance_msr_bilateral(img)
        elif method == "clahe_sharpen":
            result = _enhance_clahe_sharpen(img)
        elif method == "auto":
            # 根据图像特征自动选择
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            contrast = gray.std()
            if contrast > 50:
                result = _enhance_msr_bilateral(img)
            else:
                result = _enhance_clahe_sharpen(img)
        else:
            # 默认走原有逻辑（兼容旧配置）
            prep = preprocess(img)
            result = {"preprocessed": prep["preprocessed"]}

        ctx.preprocessed = result["preprocessed"]

        # 透传增强指标
        for k, v in result.items():
            if k != "preprocessed" and isinstance(v, (int, float)):
                ctx.metrics[f"enhance_{k}"] = v

        return ctx


def _enhance_msr_bilateral(img: np.ndarray) -> dict:
    """MSR + 双边滤波（浮选场景）"""
    msr = apply_msr(img)
    denoised = apply_bilateral(msr)
    return {
        "preprocessed": denoised,
        "method": "msr_bilateral",
    }


def _enhance_clahe_sharpen(img: np.ndarray) -> dict:
    """CLAHE 对比度增强 + 锐化（粒度/矿石场景）

    CLAHE (Contrast Limited Adaptive Histogram Equalization):
    - 自适应局部直方图均衡化，增强局部对比度
    - clipLimit 限制对比度放大倍数，避免噪声过度放大

    Unsharp Mask 锐化：
    - 突出颗粒边缘，便于后续分割
    """
    # 转 LAB 空间，只对 L 通道做 CLAHE（避免色偏）
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    # CLAHE
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l_channel)

    # 合并回 BGR
    enhanced = cv2.merge([l_enhanced, a_channel, b_channel])
    enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

    # Unsharp Mask 锐化
    blurred = cv2.GaussianBlur(enhanced, (0, 0), 3)
    sharpened = cv2.addWeighted(enhanced, 1.5, blurred, -0.5, 0)

    # 计算增强指标
    gray_orig = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray_new = cv2.cvtColor(sharpened, cv2.COLOR_BGR2GRAY)
    contrast_gain = gray_new.std() / (gray_orig.std() + 1e-6)

    return {
        "preprocessed": sharpened,
        "method": "clahe_sharpen",
        "contrast_gain": round(contrast_gain, 2),
    }
