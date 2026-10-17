"""注册尾矿处理场景包（幂等）

让 /api/scenes 把 tailings 识别为 active 场景（与 grinding/safety 同级）。
模型版本如实标注为「在线训练」，指标随数据滚动更新，不做静态固化。

用法：
    python scripts/register_tailings_scene.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.database import ModelVersion, ScenePackage, SessionLocal, init_db

SCENE = dict(
    name="tailings",
    display_name="尾矿处理智能优化",
    description=(
        "尾矿浓密机底流浓度软测量、工况识别、前瞻预测与 setpoint 推荐、絮凝剂节药量化、底流去向决策。"
        "时间轴与水质监测模块对齐，溢流水质直接取自 water.W3 点位（不重复存储）。"
    ),
    config={
        "zone": "尾矿处理区",
        "equipment": ["T1 尾矿浓密机"],
        "feed_from": "浮选尾矿",
        "outputs": ["底流 → 尾矿库 / 充填站", "溢流 → 回水池"],
        "linked_scene": "water (W3 尾矿浓密机溢流)",
    },
)

MODEL = dict(
    scene_id="tailings",
    version="underflow-soft-v1",
    algorithm="GradientBoostingRegressor",
    training_data_count=1201,
    metrics={
        "target": "底流浓度",
        "features": ["给料流量", "给料浓度", "絮凝剂单耗", "耙架扭矩", "耙架电流", "底流流量"],
        "split": "按日期分组切分（30% 日期作测试集）",
        "mae": 0.468,
        "r2": 0.696,
        "note": "软测量模型在线训练，指标随数据滚动更新；此处为最近一次训练的测试集结果。",
    },
)


def main():
    init_db()
    db = SessionLocal()
    try:
        s = db.query(ScenePackage).filter(ScenePackage.name == SCENE["name"]).first()
        if s:
            for k, v in SCENE.items():
                setattr(s, k, v)
            s.status = "active"
            print("[更新] 场景包 tailings")
        else:
            db.add(ScenePackage(**SCENE, status="active"))
            print("[新增] 场景包 tailings")

        mv = (
            db.query(ModelVersion)
            .filter(ModelVersion.scene_id == MODEL["scene_id"], ModelVersion.version == MODEL["version"])
            .first()
        )
        if mv:
            for k, v in MODEL.items():
                setattr(mv, k, v)
            print("[更新] 模型版本 underflow-soft-v1")
        else:
            db.add(ModelVersion(**MODEL, is_active=True,
                                hire_date=datetime.utcnow(),
                                total_inferences=0,
                                health_status="healthy"))
            print("[新增] 模型版本 underflow-soft-v1")

        db.commit()

        print("\n=== 当前场景包 ===")
        for x in db.query(ScenePackage).all():
            print(f"  {x.name:12} {x.display_name:16} [{x.status}]")
        print("\n[完成] /api/scenes 将把 tailings 识别为 active")
    finally:
        db.close()


if __name__ == "__main__":
    main()
