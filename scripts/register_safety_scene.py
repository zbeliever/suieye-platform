"""
注册安全巡检场景到数据库
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.database import SessionLocal, ScenePackage, ModelVersion


def register_safety_scene():
    """注册 safety 场景包和 risk-v1 模型版本"""
    db = SessionLocal()
    try:
        # 检查是否已存在
        existing = db.query(ScenePackage).filter(ScenePackage.name == "safety").first()

        if existing:
            print(f"✅ 安全巡检场景已存在: id={existing.id}, status={existing.status}")
            scene = existing
        else:
            scene = ScenePackage(
                name="safety",
                display_name="安全巡检",
                status="active",
                description=(
                    "选矿厂皮带输送机与危险区域安全巡检。"
                    "检测目标：皮带跑偏、人员入侵、异物、PPE缺失。"
                    "输出：检测框可视化、风险评级、告警记录。"
                    "支持 YOLOv8（需GPU）和规则模式（CPU fallback）。"
                ),
                config={"yaml_path": "scenes/safety/safety.yaml"},
            )
            db.add(scene)
            db.flush()
            print(f"📦 创建安全巡检场景: id={scene.id}")

        # 注册/更新模型版本
        model_version_str = "risk-v1"
        existing_model = (
            db.query(ModelVersion)
            .filter(
                ModelVersion.scene_id == scene.id,
                ModelVersion.version == model_version_str,
            )
            .first()
        )

        if existing_model:
            print(f"  ✅ 模型已存在: {model_version_str} (inference_count={existing_model.total_inferences})")
        else:
            model_version = ModelVersion(
                scene_id=scene.id,
                version=model_version_str,
                algorithm="rule_based",
                metrics={
                    "method": "auto",
                    "yolo_model": "yolov8n.pt",
                    "description": (
                        "安全风险规则评估模型 v1.0。"
                        "基于检测结果的加权风险评分："
                        "危险区人员(4x) + 皮带跑偏(3x) + 异物(2.5x) + PPE缺失(2x)。"
                        "支持 YOLOv8 目标检测 + OpenCV 规则检测双模式。"
                    ),
                    "risk_weights": {
                        "person_in_danger_zone": 4.0,
                        "belt_misalignment": 3.0,
                        "foreign_object": 2.5,
                        "ppe_missing": 2.0,
                        "person_safe_zone": 1.0,
                    },
                    "risk_thresholds": {
                        "low": 2.0,
                        "medium": 5.0,
                        "high": 10.0,
                        "critical": 20.0,
                    },
                },
                is_active=True,
            )
            db.add(model_version)
            print(f"  🤖 注册模型: {model_version_str} (rule_based v1.0)")

        db.commit()
        print("\n✅ 安全巡检场景注册完成！")

        # 验证
        scenes = db.query(ScenePackage).all()
        print(f"\n📋 当前所有场景:")
        for s in scenes:
            models = db.query(ModelVersion).filter(ModelVersion.scene_id == s.id).count()
            print(f"  - {s.name}: {s.display_name} ({s.status}) [{models} 个模型]")

    except Exception as e:
        db.rollback()
        print(f"❌ 注册失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    register_safety_scene()
