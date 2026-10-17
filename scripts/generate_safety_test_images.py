"""
生成安全巡检测试图像
======================
模拟4种工况：
  1. normal_belt      — 正常运行的皮带（无异常）
  2. belt_misaligned  — 皮带跑偏（边缘偏移）
  3. person_intrusion — 人员进入危险区域
  4. foreign_object   — 皮带上有异物
"""

import os
import sys
import numpy as np
import cv2

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "outputs", "safety_test"
)
os.makedirs(OUTPUT_DIR, exist_ok=True)


def create_normal_belt():
    """正常皮带：灰色均匀表面 + 两条平行边缘线"""
    img = np.full((480, 640, 3), 120, dtype=np.uint8)  # 灰色背景

    # 皮带主体（稍亮的中心区域）
    belt_left = 150
    belt_right = 490
    img[:, belt_left:belt_right] = 160

    # 皮带边缘线（深色）
    cv2.line(img, (belt_left, 0), (belt_left, 480), (80, 80, 80), 3)
    cv2.line(img, (belt_right, 0), (belt_right, 480), (80, 80, 80), 3)

    # 皮带纹理（横向细线，模拟传送带纹路）
    for y in range(0, 480, 20):
        cv2.line(img, (belt_left, y), (belt_right, y), (140, 140, 140), 1)

    # 托辊标记（底部小矩形）
    for x in range(belt_left + 50, belt_right - 50, 100):
        cv2.rectangle(img, (x - 15, 450), (x + 15, 478), (100, 100, 100), -1)

    path = os.path.join(OUTPUT_DIR, "normal_belt.jpg")
    cv2.imencode('.jpg', img)[1].tofile(path)
    print(f"  ✅ {path}")
    return img


def create_misaligned_belt():
    """跑偏的皮带：整体向右偏移 ~60px"""
    img = np.full((480, 640, 3), 120, dtype=np.uint8)

    # 皮带主体（明显右偏）
    belt_left = 210   # 正常是150，右偏60px
    belt_right = 550  # 正常是490
    img[:, belt_left:min(belt_right, 640)] = 160

    # 皮带边缘线
    cv2.line(img, (belt_left, 0), (belt_left, 480), (80, 80, 80), 3)
    if belt_right <= 640:
        cv2.line(img, (belt_right, 0), (belt_right, 480), (80, 80, 80), 3)

    # 皮带纹理
    for y in range(0, 480, 20):
        cv2.line(img, (belt_left, y), (min(belt_right, 640), y), (140, 140, 140), 1)

    # 跑偏警告标记（红色箭头）
    cv2.arrowedLine(img, (320, 240), (400, 240), (0, 0, 255), 4, tipLength=0.3)
    cv2.putText(img, "MISALIGNED", (320, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

    path = os.path.join(OUTPUT_DIR, "belt_misaligned.jpg")
    cv2.imencode('.jpg', img)[1].tofile(path)
    print(f"  ✅ {path}")
    return img


def create_person_intrusion():
    """人员闯入危险区域：在皮带左侧（机头区域）画一个人形"""
    img = np.full((480, 640, 3), 110, dtype=np.uint8)

    # 皮带
    belt_left, belt_right = 150, 490
    img[:, belt_left:belt_right] = 155
    cv2.line(img, (belt_left, 0), (belt_left, 480), (80, 80, 80), 2)
    cv2.line(img, (belt_right, 0), (belt_right, 480), (80, 80, 80), 2)

    # 危险区域标注（左上角，机头区域）— 半透明红色
    danger_overlay = img[0:250, 0:200].copy()
    danger_overlay[:] = [40, 20, 20]  # 暗红底
    img[0:250, 0:200] = cv2.addWeighted(img[0:250, 0:200], 0.5, danger_overlay, 0.5, 0)
    cv2.rectangle(img, (0, 0), (200, 250), (0, 0, 180), 2)
    cv2.putText(img, "DANGER ZONE", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 200), 2)

    # 人员（简化人形：头+身+腿）
    px, py = 80, 180  # 人员在危险区域内！

    # 头部（圆形）
    cv2.circle(img, (px, py - 50), 22, (180, 140, 100), -1)  # 肤色
    cv2.circle(img, (px, py - 50), 23, (100, 80, 50), 2)     # 头发/轮廓

    # 身体（矩形）
    cv2.rectangle(img, (px - 20, py - 25), (px + 20, py + 50), (0, 100, 200), -1)  # 蓝色工装

    # 腿
    cv2.rectangle(img, (px - 18, py + 50), (px - 3, py + 110), (50, 50, 80), -1)
    cv2.rectangle(img, (px + 3, py + 50), (px + 18, py + 110), (50, 50, 80), -1)

    # 安全帽（橙色头盔）
    cv2.ellipse(img, (px, py - 65), (24, 14), 0, 0, 180, (30, 120, 200), -1)
    cv2.ellipse(img, (px, py - 65), (24, 14), 0, 180, 360, (20, 80, 160), 2)

    # 反光条（衣服上的亮条）
    cv2.rectangle(img, (px - 18, py - 10), (px + 18, py + 5), (220, 220, 200), -1)

    # 标注
    cv2.putText(img, "PERSON IN DANGER!", (220, 170), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    # 画框标出人员
    cv2.rectangle(img, (px - 25, py - 85), (px + 25, py + 115), (0, 255, 0), 2)

    path = os.path.join(OUTPUT_DIR, "person_intrusion.jpg")
    cv2.imencode('.jpg', img)[1].tofile(path)
    print(f"  ✅ {path}")
    return img


def create_foreign_object():
    """皮带上的异物：模拟大块矿石/木块"""
    img = np.full((480, 640, 3), 120, dtype=np.uint8)

    # 皮带
    belt_left, belt_right = 150, 490
    img[:, belt_left:belt_right] = 160
    cv2.line(img, (belt_left, 0), (belt_left, 480), (80, 80, 80), 2)
    cv2.line(img, (belt_right, 0), (belt_right, 480), (80, 80, 80), 2)

    # 皮带纹理
    for y in range(0, 480, 20):
        cv2.line(img, (belt_left, y), (belt_right, y), (145, 145, 145), 1)

    # 异物1: 大块矿石（不规则多边形，深灰色）
    ore_pts = np.array([[280, 200], [340, 180], [370, 230], [350, 290], [300, 310], [260, 260]], np.int32)
    cv2.fillPoly(img, [ore_pts], (70, 70, 75))
    cv2.polylines(img, [ore_pts], True, (40, 40, 45), 2)
    # 高光
    cv2.circle(img, (310, 215), 12, (100, 100, 105), -1)

    # 异物2: 木块（棕色矩形）
    cv2.rectangle(img, (400, 320), (470, 380), (139, 90, 43), -1)
    cv2.rectangle(img, (400, 320), (470, 380), (100, 60, 20), 2)
    # 木纹
    for i in range(3):
        cv2.line(img, (405, 330 + i * 18), (465, 335 + i * 18), (110, 70, 30), 1)

    # 异物3: 金属零件（小而亮）
    cv2.rectangle(img, (220, 360), (260, 390), (180, 180, 190), -1)
    cv2.rectangle(img, (220, 360), (260, 390), (140, 140, 150), 2)
    # 反光点
    cv2.circle(img, (228, 368), 4, (220, 220, 230), -1)

    # 标注
    cv2.putText(img, "FOREIGN OBJECTS DETECTED", (200, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    # 画框
    cv2.rectangle(img, (255, 170), (378, 320), (0, 0, 255), 2)
    cv2.putText(img, "Ore", (280, 165), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 2)

    path = os.path.join(OUTPUT_DIR, "foreign_object.jpg")
    cv2.imencode('.jpg', img)[1].tofile(path)
    print(f"  ✅ {path}")
    return img


def main():
    print("🎨 生成安全巡检测试图像...")
    print(f"   输出目录: {OUTPUT_DIR}\n")

    create_normal_belt()
    create_misaligned_belt()
    create_person_intrusion()
    create_foreign_object()

    print(f"\n✅ 测试图像生成完成！共 4 张")


if __name__ == "__main__":
    main()
