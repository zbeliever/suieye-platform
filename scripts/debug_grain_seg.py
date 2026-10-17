"""粒度分割诊断脚本 - 检查每步中间结果"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np

img_path = PROJECT_ROOT / "样例图像" / "grinding" / "normal_grinding.jpg"
raw = np.fromfile(str(img_path), dtype=np.uint8)
img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

print(f"图像尺寸: {img.shape}")
print(f"灰度均值: {gray.mean():.1f}, 标准差: {gray.std():.1f}")

# 模拟 ROI 裁剪（center_ratio=0.8）
h, w = gray.shape
crop_h, crop_w = int(h * 0.8), int(w * 0.8)
sy, sx = (h - crop_h) // 2, (w - crop_w) // 2
cropped = gray[sy:sy+crop_h, sx:sx+crop_w]
print(f"裁剪后: {cropped.shape}, 均值: {cropped.mean():.1f}")

# CLAHE 增强
lab = cv2.cvtColor(img[sy:sy+crop_h, sx:sx+crop_w], cv2.COLOR_BGR2LAB)
l, a, b = cv2.split(lab)
clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
l_enhanced = clahe.apply(l)
enhanced = cv2.merge([l_enhanced, a, b])
enhanced_bgr = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
enhanced_gray = cv2.cvtColor(enhanced_bgr, cv2.COLOR_BGR2GRAY)
print(f"CLAHE后灰度均值: {enhanced_gray.mean():.1f}, 标准差: {enhanced_gray.std():.1f}")

# 自适应阈值 - 两个方向
blurred = cv2.GaussianBlur(enhanced_gray, (5, 5), 0)

binary_light = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, -5)
binary_dark = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, -5)

ratio_light = binary_light.sum() / (255 * binary_light.size)
ratio_dark = binary_dark.sum() / (255 * binary_dark.size)
print(f"\nTHRESH_BINARY 前景占比: {ratio_light:.3f}")
print(f"THRESH_BINARY_INV 前景占比: {ratio_dark:.3f}")

# 形态学
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
opened_light = cv2.morphologyEx(binary_light, cv2.MORPH_OPEN, kernel, iterations=2)
closed_light = cv2.morphologyEx(opened_light, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=2)

# 连通域
num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(closed_light, connectivity=8)
print(f"\n连通域数量(含背景): {num_labels}")
valid = 0
for i in range(1, num_labels):
    area = stats[i, cv2.CC_STAT_AREA]
    bw = stats[i, cv2.CC_STAT_WIDTH]
    bh = stats[i, cv2.CC_STAT_HEIGHT]
    perimeter = 2 * (bw + bh)
    circ = 4 * np.pi * area / (perimeter ** 2) if perimeter > 0 else 0
    if area >= 100 and area <= 50000 and circ >= 0.3:
        valid += 1
        print(f"  颗粒{i}: area={area}, size={bw}x{bh}, circ={circ:.3f}")
    else:
        print(f"  颗粒{i}: REJECTED area={area}, circ={circ:.3f}")

print(f"\n有效颗粒数: {valid}")

# 保存中间结果
out = PROJECT_ROOT / "data" / "outputs" / "grinding_debug"
out.mkdir(parents=True, exist_ok=True)
cv2.imencode('.jpg', cropped)[1].tofile(str(out / "01_cropped.jpg"))
cv2.imencode('.jpg', enhanced_gray)[1].tofile(str(out / "02_enhanced.jpg"))
cv2.imencode('.jpg', binary_light)[1].tofile(str(out / "03_binary_light.jpg"))
cv2.imencode('.jpg', binary_dark)[1].tofile(str(out / "04_binary_dark.jpg"))
cv2.imencode('.jpg', closed_light)[1].tofile(str(out / "05_morphology.jpg"))
print(f"\n诊断图像已保存到: {out}")
