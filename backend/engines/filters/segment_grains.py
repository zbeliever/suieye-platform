"""颗粒分割引擎（粒度场景专用）

与泡沫分割的区别：
- 泡沫分割：找气泡边界（暗缝隙），用 Felzenszwalb 图分割
- 颗粒分割：找矿石颗粒（亮目标），用自适应阈值 + 形态学 + 连通域

方法：
1. adaptive_morphology: 自适应阈值 → 开运算去噪 → 闭运算填充 → 连通域标记
2. watershed_grains: 距离变换 → 分水岭分离粘连颗粒（更精确但更慢）
"""

import cv2
import math
import numpy as np
from scipy import ndimage

from backend.engines.base import Engine, EngineContext


class SegmentGrainsEngine(Engine):
    """矿石颗粒分割 Filter"""

    name = "segment_grains"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        method = params.get("method", "adaptive_morphology")
        img = ctx.cropped_image if ctx.cropped_image is not None else (ctx.preprocessed if ctx.preprocessed is not None else ctx.raw_image)
        if img is None:
            return ctx

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        if method == "adaptive_morphology":
            labels, fg_mask, n_grains = _segment_adaptive_morphology(
                gray,
                min_area=params.get("min_area", 100),
                max_area=params.get("max_area", 50000),
                circularity_threshold=params.get("circularity_threshold", 0.3),
            )
        elif method == "watershed_grains":
            labels, fg_mask, n_grains = _segment_watershed_grains(
                gray,
                min_area=params.get("min_area", 100),
            )
        else:
            raise ValueError(f"未知颗粒分割方法: {method}")

        ctx.labels = labels
        ctx.fg_mask = fg_mask
        ctx.metrics["n_grains"] = int(n_grains)

        # 生成可视化叠加图
        overlays = _build_grain_overlays(img, labels, fg_mask, n_grains)
        ctx.overlays.update(overlays)

        return ctx


def _segment_adaptive_morphology(
    gray: np.ndarray,
    min_area: int = 100,
    max_area: int = 50000,
    circularity_threshold: float = 0.3,
) -> tuple:
    """自适应阈值 + 形态学颗粒分割（默认方法，快速稳定）

    自动检测颗粒比背景亮还是暗，选择正确的阈值方向。

    Returns:
        (labels, fg_mask, n_grains)
    """
    # 1. 高斯模糊降噪
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # 2. 自适应阈值 —— 同时尝试两个方向，自动选优
    binary_light = cv2.adaptiveThreshold(   # 亮颗粒（正常矿石图）
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 21, -5
    )
    binary_dark = cv2.adaptiveThreshold(    # 暗颗粒（特殊光照）
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 21, -5
    )

    # 选前景像素数在合理范围（5%~60%）的那个方向
    ratio_light = binary_light.sum() / (255 * binary_light.size)
    ratio_dark = binary_dark.sum() / (255 * binary_dark.size)

    if 0.05 <= ratio_light <= 0.60:
        binary = binary_light
    elif 0.05 <= ratio_dark <= 0.60:
        binary = binary_dark
    else:
        # 都不在理想范围，选更接近 30% 的
        binary = binary_light if abs(ratio_light - 0.30) < abs(ratio_dark - 0.30) else binary_dark

    # 3. 形态学清理
    kernel_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    kernel_tiny = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))

    # 开运算：去除小噪点（保持迭代以有效去噪）
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel_small, iterations=2)
    # 闭运算：轻微填充颗粒内部孔洞（用小核+少迭代，避免合并相邻颗粒）
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel_tiny, iterations=1)

    # 4. 连通域分析
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(closed, connectivity=8)

    # 5. 按面积和圆度过滤
    output_labels = np.zeros_like(labels)
    fg_mask = np.zeros_like(gray)
    valid_count = 0

    for i in range(1, num_labels):  # 跳过背景(0)
        area = stats[i, cv2.CC_STAT_AREA]
        x, y, w, h = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP], \
                      stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]

        # 面积过滤
        if area < min_area or area > max_area:
            continue

        # 圆度过滤 —— 使用真实轮廓周长（不用外接矩形近似）
        mask_i = (labels == i).astype(np.uint8)
        contours, _ = cv2.findContours(mask_i, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        perimeter = cv2.arcLength(contours[0], True)
        if perimeter > 0:
            circularity = min(4 * math.pi * area / (perimeter ** 2), 1.0)  # 截断到[0,1]
        else:
            circularity = 0.0
        if circularity < circularity_threshold:
            continue

        valid_count += 1
        output_labels[labels == i] = valid_count
        fg_mask[labels == i] = 255

    return output_labels, fg_mask, valid_count


def _segment_watershed_grains(
    gray: np.ndarray,
    min_area: int = 100,
) -> tuple:
    """分水岭算法分割粘连颗粒（更精确，适用于颗粒密集粘连场景）

    Returns:
        (labels, fg_mask, n_grains)
    """
    # 1. Otsu 二值化
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # 2. 形态学清理
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)

    # 3. 距离变换
    dist_transform = cv2.distanceTransform(cleaned, cv2.DIST_L2, 5)

    # 4. 寻找距离局部极大值作为种子点
    from skimage.feature import peak_local_max
    coords = peak_local_max(dist_transform, min_distance=10, exclude_border=False)

    # 5. 创建标记图
    markers = np.zeros(gray.shape, dtype=np.int32)
    for idx, (r, c) in enumerate(coords, start=1):
        markers[r, c] = idx

    # 6. 分水岭
    labels = cv2.watershed(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), markers)

    # 分水岭结果：背景=-1，从1开始编号；统一为0=背景
    labels_out = np.where(labels == -1, 0, labels)
    n_grains = len(coords)

    # 过滤小区域
    fg_mask = np.zeros_like(gray)
    for i in range(1, n_grains + 1):
        mask_i = (labels_out == i).astype(np.uint8)
        area = mask_i.sum()
        if area >= min_area:
            fg_mask[mask_i > 0] = 255
        else:
            labels_out[labels_out == i] = 0

    # 重编号使标签连续
    unique_labels = np.unique(labels_out)
    unique_labels = unique_labels[unique_labels != 0]
    renumber_map = {old: new for new, old in enumerate(unique_labels, start=1)}
    final_labels = np.zeros_like(labels_out)
    for old, new in renumber_map.items():
        final_labels[labels_out == old] = new

    return final_labels, fg_mask, len(unique_labels)


def _build_grain_overlays(
    img: np.ndarray,
    labels: np.ndarray,
    fg_mask: np.ndarray,
    n_grains: int,
) -> dict:
    """构建颗粒分割可视化图"""
    h, w = img.shape[:2]

    # 1. 边界叠加图（绿色轮廓标注每个颗粒）
    overlay = img.copy()
    if n_grains > 0:
        # 用不同颜色标记不同颗粒
        hsv_overlay = cv2.cvtColor(overlay, cv2.COLOR_BGR2HSV)
        for grain_id in range(1, n_grains + 1):
            mask = (labels == grain_id).astype(np.uint8)
            if mask.sum() == 0:
                continue

            # HSV 色相循环着色
            hue = (grain_id * 37) % 180  # 37 是接近黄金角度的色相步进
            color_mask = np.zeros_like(hsv_overlay)
            color_mask[:, :, 0] = hue
            color_mask[:, :, 1] = 120  # 中等饱和度
            color_mask[:, :, 2] = 200
            color_bgr = cv2.cvtColor(color_mask, cv2.COLOR_HSV2BGR)

            # 半透明叠加
            alpha = 0.25
            overlay = np.where(mask[..., None] > 0,
                               (overlay * (1 - alpha) + color_bgr * alpha).astype(np.uint8),
                               overlay)

            # 绘制轮廓
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, contours, -1, (0, 255, 0), 1)

    # 添加统计文字
    text = f"Grains: {n_grains}"
    cv2.putText(overlay, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

    # 2. 彩色标签图（纯色块，用于展示分割效果）
    colored = np.zeros((h, w, 3), dtype=np.uint8)
    for grain_id in range(1, n_grains + 1):
        mask = (labels == grain_id).astype(np.uint8)
        if mask.sum() == 0:
            continue
        hue = (grain_id * 37) % 180
        colored[mask > 0] = [
            int(128 + 127 * np.sin(hue * np.pi / 90)),
            int(128 + 127 * np.sin((hue + 60) * np.pi / 90)),
            int(128 + 127 * np.sin((hue + 120) * np.pi / 90)),
        ]

    # 3. 概率/置信度图（这里用距离边界的距离代替）
    if n_grains > 0:
        prob_map = cv2.distanceTransform(fg_mask, cv2.DIST_L2, 5)
        prob_norm = np.clip(prob_map / (prob_map.max() + 1e-6) * 255, 0, 255).astype(np.uint8)
        prob_color = cv2.applyColorMap(prob_norm, cv2.COLORMAP_JET)
    else:
        prob_color = np.zeros_like(img)

    return {
        "boundary_overlay": overlay,
        "colored_labels": colored,
        "probability_map": prob_color,
    }
