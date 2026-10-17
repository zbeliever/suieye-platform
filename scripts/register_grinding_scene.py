"""注册粒度场景包和 P80 模型到数据库

用法: python scripts/register_grinding_scene.py
"""

import sys
from pathlib import Path

# 确保项目根目录在路径中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.models.database import ScenePackage, ModelVersion, init_db, SessionLocal


def main():
    init_db()
    db = SessionLocal()

    try:
        # ---- 1. 注册/更新粒度场景包 ----
        existing = db.query(ScenePackage).filter(ScenePackage.name == "grinding").first()

        if existing:
            existing.display_name = "矿石粒度分析"
            existing.status = "active"
            existing.description = (
                "磨矿段矿石颗粒分割与P80粒度预测。"
                "输入矿石/矿浆图像，输出颗粒分布曲线、P80值、工况判断（过磨/欠磨/正常）。"
                "基于自适应阈值+形态学颗粒分割 + 规则模型P80预测。"
            )
            print(f"✅ 场景包 'grinding' 已更新 (id={existing.id})")
        else:
            scene = ScenePackage(
                name="grinding",
                display_name="矿石粒度分析",
                status="active",
                description=(
                    "磨矿段矿石颗粒分割与P80粒度预测。"
                    "输入矿石/矿浆图像，输出颗粒分布曲线、P80值、工况判断。"
                ),
                config={"yaml_path": "scenes/grinding/grinding.yaml"},
            )
            db.add(scene)
            db.flush()
            print(f"✅ 场景包 'grinding' 已创建 (id={scene.id})")

        # ---- 2. 注册 P80 预测模型 ----
        model_existing = db.query(ModelVersion).filter(
            ModelVersion.scene_id == "grinding",
            ModelVersion.version == "p80-v1",
        ).first()

        if model_existing:
            model_existing.is_active = True
            model_existing.algorithm = "rule_based"
            model_existing.metrics = {
                "method": "规则模型（基于粒度特征+经验公式）",
                "features": ["d80", "mean_circularity", "uniformity_coefficient"],
                "pixel_to_um": 2000,  # 标定系数：图像宽度=2000μm
                "note": "未来可替换为 XGBoost / Random Forest 回归模型",
            }
            print(f"✅ 模型 'p80-v1' 已更新 (id={model_existing.id})")
        else:
            model = ModelVersion(
                scene_id="grinding",
                version="p80-v1",
                algorithm="rule_based",
                is_active=True,
                metrics={
                    "method": "规则模型（基于粒度特征+经验公式）",
                    "features": ["d80", "mean_circularity", "uniformity_coefficient"],
                    "pixel_to_um": 2000,
                    "note": "未来可替换为 XGBoost / Random Forest 回归模型",
                },
                training_data_count=0,  # 规则模型无需训练数据
            )
            db.add(model)
            db.flush()
            print(f"✅ 模型 'p80-v1' 已创建 (id={model.id})")

        db.commit()
        print("\n🎉 粒度场景注册完成！现在可以通过 POST /api/analyze/grinding 使用")

    except Exception as e:
        db.rollback()
        print(f"❌ 注册失败: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
