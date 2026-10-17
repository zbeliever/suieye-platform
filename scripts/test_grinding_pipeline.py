"""粒度场景端到端管线测试

验证：YAML加载 → ROI → 增强 → 颗粒分割 → 特征提取 → P80预测 → 告警
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np
from backend.scenes.runtime import run_analysis, list_available_scenes


def test_grinding_pipeline():
    print("=" * 60)
    print("  粒度场景 (grinding) 端到端管线测试")
    print("=" * 60)

    # 1. 检查场景是否可用
    scenes = list_available_scenes()
    print(f"\n📋 可用场景: {scenes}")
    if "grinding" not in scenes:
        print("❌ grinding 场景未找到！检查 scenes/grinding/grinding.yaml")
        return False
    print("✅ grinding 场景已识别")

    # 2. 加载测试图像
    test_dir = PROJECT_ROOT / "样例图像" / "grinding"
    test_images = ["normal_grinding.jpg", "overground.jpg", "underground.jpg"]

    results = []
    for img_name in test_images:
        img_path = test_dir / img_name
        if not img_path.exists():
            print(f"⚠️ 跳过 {img_name}（文件不存在）")
            continue

        print(f"\n{'─' * 50}")
        print(f"🖼️ 测试图像: {img_name}")

        # 读取（支持中文路径）
        raw = np.fromfile(str(img_path), dtype=np.uint8)
        img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if img is None:
            print(f"❌ 无法读取 {img_path}")
            continue
        print(f"   图像尺寸: {img.shape[1]}x{img.shape[0]}")

        # 3. 运行完整管线
        try:
            ctx = run_analysis("grinding", img, source="test")
            results.append((img_name, ctx))

            # 4. 输出关键结果
            pred = ctx.prediction
            feats = ctx.features
            metrics = ctx.metrics

            print(f"\n   📊 预测结果:")
            print(f"      P80: {pred.get('p80', 'N/A')} μm")
            print(f"      工况: {pred.get('condition', 'N/A')}")
            print(f"      置信度: {pred.get('confidence', 'N/A')}")
            print(f"      消息: {pred.get('message', 'N/A')}")

            print(f"\n   🔬 粒度特征:")
            print(f"      颗粒数: {feats.get('grain_count', 'N/A')}")
            print(f"      填充率: {feats.get('fill_ratio', 'N/A')}")
            print(f"      D10/D50/D80/D90: {feats.get('d10', 'N/A')} / "
                  f"{feats.get('d50', 'N/A')} / {feats.get('d80', 'N/A')} / "
                  f"{feats.get('d90', 'N/A')} μm")
            print(f"      平均圆度: {feats.get('mean_circularity', 'N/A')}")
            print(f"      过磨率/欠磨率: {feats.get('overgrinding_ratio', 'N/A')} / "
                  f"{feats.get('undergrinding_ratio', 'N/A')}")

            print(f"\n   ⏱️ 管线耗时:")
            for t in ctx.trace:
                print(f"      {t['filter']}: {t['ms']}ms")

            print(f"\n   🚨 告警: {len(ctx.alarms)} 条")
            for alarm in ctx.alarms:
                print(f"      [{alarm['level']}] {alarm['rule_name']}: {alarm['message']}")

            # 5. 保存可视化结果
            output_dir = PROJECT_ROOT / "data" / "outputs" / "grinding_test"
            output_dir.mkdir(parents=True, exist_ok=True)

            for overlay_name, overlay_img in ctx.overlays.items():
                if overlay_img is not None:
                    out_path = output_dir / f"{img_name.replace('.jpg', '')}_{overlay_name}.jpg"
                    cv2.imencode('.jpg', overlay_img)[1].tofile(str(out_path))
                    print(f"   💾 可视化: {out_path.name}")

        except Exception as e:
            print(f"❌ 管线执行失败: {e}")
            import traceback
            traceback.print_exc()
            continue

    # 6. 总结
    print(f"\n{'=' * 60}")
    print(f"  测试完成: {len(results)}/{len(test_images)} 张图像成功")
    if len(results) == len(test_images):
        print("  ✅ 所有测试通过！粒度场景管线正常运行")
    else:
        print("  ⚠️ 部分测试未通过，请检查上方错误信息")
    print("=" * 60)

    return len(results) == len(test_images)


if __name__ == "__main__":
    success = test_grinding_pipeline()
    sys.exit(0 if success else 1)
