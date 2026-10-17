"""
安全巡检场景 — 端到端管线测试
=================================
测试4张图像的正常/异常检测流程：
  1. normal_belt      → 应该：低风险，无异常
  2. belt_misaligned  → 应该：中高风险，皮带跑偏
  3. person_intrusion → 应该：紧急/高风险（人员在危险区）
  4. foreign_object   → 应该：中等风险，有异物
"""

import os
import sys
import time

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from backend.scenes.runtime import run_analysis


TEST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "outputs", "safety_test"
)
OUTPUT_DIR = TEST_DIR  # 覆盖输出到同目录


def load_image(name: str) -> np.ndarray:
    path = os.path.join(TEST_DIR, name)
    if not os.path.exists(path):
        print(f"  ❌ 文件不存在: {path}")
        return None
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    print(f"  📷 加载: {name} ({img.shape[1]}x{img.shape[0]})")
    return img


def save_overlay(ctx, name: str, suffix: str = ""):
    """保存所有叠加图"""
    overlay_names = {
        "detection_overlay": "_detection",
        "danger_zone_map": "_danger_zone",
        "risk_assessment": "_risk",
    }
    for key, suf in overlay_names.items():
        if key in ctx.overlays and ctx.overlays[key] is not None:
            out_name = name.replace(".jpg", f"{suffix}{suf}.jpg")
            out_path = os.path.join(OUTPUT_DIR, out_name)
            cv2.imencode('.jpg', ctx.overlays[key])[1].tofile(out_path)


def test_one(name: str) -> dict:
    """单张图像测试"""
    print(f"\n{'='*60}")
    print(f"🔍 测试: {name}")
    print(f"{'='*60}")

    img = load_image(name)
    if img is None:
        return {"status": "error", "message": "图像加载失败"}

    try:
        start = time.time()
        ctx = run_analysis("safety", img)
        elapsed = (time.time() - start) * 1000

        # 输出结果
        pred = ctx.prediction or {}
        metrics = ctx.metrics
        features = ctx.features or {}
        detections = getattr(ctx, 'detections', [])

        print(f"\n📊 结果:")
        print(f"   ⏱️  总耗时: {elapsed:.1f}ms")
        print(f"   推理耗时: {metrics.get('inference_time_ms', 'N/A')}ms")
        print(f"   检测方法: {metrics.get('detection_method', 'N/A')}")
        print(f"   检测目标数: {len(detections)}")

        if detections:
            print(f"\n   🎯 检测详情:")
            for d in detections:
                cls = d["class"]
                conf = d["confidence"]
                bbox = d["bbox"]
                extra = ""
                if "attributes" in d:
                    attr = d["attributes"]
                    if "offset_px" in attr:
                        extra = f" | 偏移={attr['offset_px']}px ({attr.get('direction', '')})"
                print(f"      - {cls}: 置信度={conf:.0%}, 框={bbox}{extra}")

        print(f"\n   📈 特征:")
        print(f"      - 人员总数: {features.get('person_count', 0)}")
        print(f"      - 危险区人员: {features.get('persons_in_danger_zone', 0)}")
        print(f"      - 皮带偏移: {features.get('belt_offset_px', 0)}px ({features.get('belt_direction', '正常')})")
        print(f"      - 异物数量: {features.get('foreign_object_count', 0)}")
        print(f"      - PPE合规率: {features.get('ppe_compliance_rate', 1.0)*100:.0f}%")

        print(f"\n   ⚠️  风险评级:")
        print(f"      - 等级: {pred.get('risk_level', 'N/A')}")
        print(f"      - 评分: {pred.get('risk_score', 'N/A')}/100")
        print(f"      - 置信度: {pred.get('confidence', 'N/A')}")
        print(f"      - 建议: {pred.get('action', 'N/A')}")

        if pred.get("force_reason"):
            print(f"      - ⚠️ {pred['force_reason']}")

        # 保存可视化
        save_overlay(ctx, name)

        return {
            "status": "ok",
            "elapsed_ms": round(elapsed, 1),
            "risk_level": pred.get("risk_level", "N/A"),
            "risk_score": pred.get("risk_score", 0),
            "detection_count": len(detections),
            "person_count": features.get("person_count", 0),
            "belt_offset": features.get("belt_offset_px", 0),
            "foreign_count": features.get("foreign_object_count", 0),
        }

    except Exception as e:
        print(f"\n   ❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


def main():
    print("=" * 60)
    print("🛡️  安全巡检场景 — 端到端管线测试")
    print("=" * 60)

    test_cases = [
        "normal_belt.jpg",         # 正常皮带 → 低风险
        "belt_misaligned.jpg",     # 皮带跑偏 → 中/高风险
        "person_intrusion.jpg",    # 人员入侵 → 高/紧急
        "foreign_object.jpg",      # 异物 → 中等风险
    ]

    results = []
    for case in test_cases:
        result = test_one(case)
        results.append((case, result))

    # 汇总
    print(f"\n\n{'='*60}")
    print("📋 测试汇总")
    print(f"{'='*60}")
    print(f"{'图像':<22} {'状态':<8} {'风险等级':<10} {'评分':<8} {'目标数':<8} {'耗时':<8}")
    print("-" * 70)

    ok_count = 0
    for name, r in results:
        status = "✅ PASS" if r.get("status") == "ok" else "❌ FAIL"
        level = r.get("risk_level", "N/A")
        score = r.get("risk_score", "N/A")
        dets = r.get("detection_count", "-")
        elapsed = f"{r.get('elapsed_ms', 0)}ms"
        print(f"{name:<22} {status:<8} {level:<10} {str(score):<8} {str(dets):<8} {elapsed:<8}")
        if r.get("status") == "ok":
            ok_count += 1

    print("-" * 70)
    print(f"通过: {ok_count}/{len(results)}")

    if ok_count == len(results):
        print("\n🎉 所有测试通过！安全巡检场景就绪。")
    else:
        print(f"\n⚠️ {len(results) - ok_count} 个测试未通过，请检查日志。")


if __name__ == "__main__":
    main()
