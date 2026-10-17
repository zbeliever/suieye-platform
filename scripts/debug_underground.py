"""快速诊断 underground 图像"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np
import math

img_path = PROJECT_ROOT / "样例图像" / "grinding" / "underground.jpg"
raw = np.fromfile(str(img_path), dtype=np.uint8)
img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

h, w = gray.shape
crop_h, crop_w = int(h * 0.8), int(w * 0.8)
sy, sx = (h - crop_h) // 2, (w - crop_w) // 2
cropped = gray[sy:sy+crop_h, sx:sx+crop_w]
cropped_img = img[sy:sy+crop_h, sx:sx+crop_w]

# CLAHE
lab = cv2.cvtColor(cropped_img, cv2.COLOR_BGR2LAB)
l, a, b = cv2.split(lab)
clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
l_enhanced = clahe.apply(l)
enhanced = cv2.merge([l_enhanced, a, b])
enhanced_bgr = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
enhanced_gray = cv2.cvtColor(enhanced_bgr, cv2.COLOR_BGR2GRAY)

print(f"裁剪后: {cropped.shape}, 均值: {cropped.mean():.1f}")
print(f"CLAHE后: 均值: {enhanced_gray.mean():.1f}, 标准差: {enhanced_gray.std():.1f}")

blurred = cv2.GaussianBlur(enhanced_gray, (5, 5), 0)

for name, thresh_type in [("BINARY", cv2.THRESH_BINARY), ("INV", cv2.THRESH_BINARY_INV)]:
    binary = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, thresh_type, 21, -5)
    ratio = binary.sum() / (255 * binary.size)
    
    # 轻微形态学
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=2)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2)), iterations=1)
    
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(closed, connectivity=8)
    
    valid = []
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        mask_i = (labels == i).astype(np.uint8)
        contours, _ = cv2.findContours(mask_i, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        perimeter = cv2.arcLength(contours[0], True)
        circ = 4 * math.pi * area / (perimeter ** 2) if perimeter > 0 else 0
        
        status = "OK" if (100 <= area <= 50000 and circ >= 0.3) else f"REJ(area={area},circ={circ:.3f})"
        valid.append((i, area, circ, status))
        
    print(f"\n--- {name} (前景占比={ratio:.3f}) ---")
    print(f"  连通域: {num_labels-1} 个")
    for v in valid[:10]:  # 只显示前10个
        print(f"  颗粒{v[0]}: area={v[1]}, circ={v[2]:.3f} -> {v[3]}")
    if len(valid) > 10:
        print(f"  ... 还有 {len(valid)-10} 个")
    
    ok_count = sum(1 for v in valid if "OK" in v[3])
    print(f"  有效颗粒: {ok_count}")
    
    # 保存
    out = PROJECT_ROOT / "data" / "outputs" / "grinding_debug"
    out.mkdir(parents=True, exist_ok=True)
    cv2.imencode('.jpg', binary)[1].tofile(str(out / f"ug_{name.lower()}_binary.jpg"))
    cv2.imencode('.jpg', closed)[1].tofile(str(out / f"ug_{name.lower()}_closed.jpg"))
