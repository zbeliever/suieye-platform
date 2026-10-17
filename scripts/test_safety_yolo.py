"""YOLO 模式安全巡检专项测试"""
import sys
import os
import time

# 项目根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SUIEYE_ENV', 'development')

from backend.scenes.runtime import run_analysis


def test_yolo_mode():
    """测试 YOLOv8 目标检测模式"""

    test_images = {
        'belt_normal': 'data/outputs/safety_test/normal_belt.jpg',
        'belt_misaligned': 'data/outputs/safety_test/belt_misaligned.jpg',
        'person_intrusion': 'data/outputs/safety_test/person_intrusion.jpg',
        'foreign_object': 'data/outputs/safety_test/foreign_object.jpg',
    }

    print("=" * 60)
    print("  YOLOv8 安全巡检模式测试")
    print("=" * 60)

    for name, path in test_images.items():
        if not os.path.exists(path):
            print(f"\n⚠️  跳过 {name}: 文件不存在 ({path})")
            continue

        print(f"\n{'='*60}")
        print(f"📷 测试图像: {name}")
        print(f"   路径: {path}")
        print(f"{'='*60}")

        try:
            # 读取图像为 BGR numpy array
            import cv2
            import numpy as np
            image_bgr = np.fromfile(path, dtype=np.uint8)
            image_bgr = cv2.imdecode(image_bgr, cv2.IMREAD_COLOR)

            # 强制使用 YOLO 模式
            t0 = time.time()
            ctx = run_analysis(
                scene_name='safety',
                image_bgr=image_bgr,
                detect_method='yolo',  # 强制 YOLO
            )
            elapsed = time.time() - t0

            # 结果
            pred = ctx.prediction or {}
            metrics = ctx.metrics or {}
            features = ctx.features or {}

            print(f"\n✅ YOLO 推理完成! 耗时: {elapsed:.2f}s")
            print(f"\n📊 预测结果:")
            print(f"   风险等级: {pred.get('risk_level', 'N/A')} (评分: {pred.get('risk_score', 0):.1f})")
            print(f"   置信度: {pred.get('confidence', 0):.2f}")

            print(f"\n📈 关键指标:")
            print(f"   总检测数: {metrics.get('total_detections', 0)}")
            print(f"   人员数: {features.get('person_count', 0)}")
            print(f"   皮带偏移: {features.get('belt_offset_px', 0):.1f}px")
            print(f"   异物数: {features.get('foreign_object_count', 0)}")
            print(f"   危险区入侵: {'是' if features.get('danger_zone_intrusion') else '否'}")

            # 检测详情
            detections = ctx.detections or []
            if detections:
                print(f"\n🔍 检测详情 ({len(detections)} 个):")
                for i, det in enumerate(detections[:10]):  # 最多显示10个
                    print(f"   [{i+1}] {det.get('class_name', '?')} "
                          f"@({det.get('x', 0):.0f}, {det.get('y', 0):.0f}) "
                          f"conf={det.get('confidence', 0):.2f}")

            # 保存可视化
            out_dir = 'data/outputs/safety_yolo_test'
            os.makedirs(out_dir, exist_ok=True)

            if ctx.overlays:
                for overlay_name, overlay_img in ctx.overlays.items():
                    if overlay_img is not None:
                        out_path = os.path.join(out_dir, f'{name}_{overlay_name}.jpg')
                        import cv2
                        cv2.imencode('.jpg', overlay_img)[1].tofile(out_path)
                        print(f"   💾 保存: {out_path}")

            # 管线追踪
            if ctx.trace:
                print(f"\n⏱️  管线耗时:")
                for step in ctx.trace:
                    print(f"   {step.get('filter', '?'):20s} : {step.get('elapsed_ms', 0):8.1f}ms")

        except Exception as e:
            print(f"\n❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print("  YOLO 模式测试完成!")
    print("=" * 60)


if __name__ == '__main__':
    test_yolo_mode()
