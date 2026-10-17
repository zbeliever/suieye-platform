"""补注册已上线但漏登记的场景包：water / equipment（幂等）

背景（2026-10-14 修）：
    /api/scenes 早期实现里，场景列表 = 「注册表中 status=active 的」+「硬编码 planned 兜底名单」。
    water / equipment 两个模块早已上线（/water、/equipment 页面 + 数据表），
    但从未写入 scene_packages，于是恒被兜底成「规划中 / 记录 0」。
    本脚本把它们正式登记进注册表；同时 /api/scenes 已改为以注册表为唯一真源。

模型版本命名沿用平台既有惯例（grinding 的 p80-v1 就是 rule_based 规则模型）：
    - water      reuse-index-v1  RuleBased+Correlation   回水可回用评分 + 水质-品位相关分析
    - equipment  health-score-v1 RuleBased+TrendExtrapolation  健康度评分 + 阈值趋势外推

用法：
    python scripts/register_platform_scenes.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.database import ModelVersion, ScenePackage, SessionLocal, init_db

SCENES = [
    dict(
        name="water",
        display_name="水质监测",
        description=(
            "脱水工段 5 个水质点位（W1~W5）8 参数连续监测；回水可回用评分、水质-品位相关性分析、"
            "水-药-品闭环建议。数据源为 water_readings 专用表。"
        ),
        config={
            "zone": "脱水工段 / 回水系统",
            "stations": ["W1", "W2", "W3", "W4", "W5"],
            "params": ["pH", "浊度", "SS", "COD", "Pb", "Zn", "Cu", "流量"],
            "linked_scene": "tailings (W3 尾矿浓密机溢流，同源取数)",
        },
    ),
    dict(
        name="equipment",
        display_name="设备健康监测",
        description=(
            "全厂 7 台关键设备（浮选槽 / 球磨机 / 渣浆泵 / 皮带机 / 浓密机 / 搅拌槽）温度、振动、"
            "功率、转速、流量实时监测 + 健康度评分 + 预测性维护建议。监测值按请求实时计算，不沉淀历史表。"
        ),
        config={
            "zone": "全厂",
            "equipment_count": 7,
            "data_mode": "live",  # 实时计算，无历史表 → /api/scenes 记录数返回 null
            "views": ["3D 设备场景", "设备详情", "趋势与预测", "检修工单"],
        },
    ),
]

MODELS = [
    dict(
        scene_id="water",
        version="reuse-index-v1",
        algorithm="RuleBased+Correlation",
        training_data_count=6005,  # water_readings 现有读数
        metrics={
            "target": "回水可回用评分",
            "method": "多参数加权规则 + 水位/流量修正",
            "analysis": ["水质-品位 Pearson 相关", "滞后互相关（最优滞后阶数）"],
            "price_note": "评分为 0~100 无量纲分，非直接经济量。",
        },
    ),
    dict(
        scene_id="equipment",
        version="health-score-v1",
        algorithm="RuleBased+TrendExtrapolation",
        training_data_count=None,
        metrics={
            "target": "设备健康度",
            "method": "基线偏离 + 阈值规则评分",
            "forecast": "按当前健康度做线性衰减外推（7 天，演示用）",
            "note": "监测值由仿真模型实时生成，非历史沉淀数据。",
        },
    ),
]


def main():
    init_db()
    db = SessionLocal()
    try:
        for sc in SCENES:
            s = db.query(ScenePackage).filter(ScenePackage.name == sc["name"]).first()
            if s:
                for k, v in sc.items():
                    setattr(s, k, v)
                s.status = "active"
                print(f"[更新] 场景包 {sc['name']}")
            else:
                db.add(ScenePackage(**sc, status="active"))
                print(f"[新增] 场景包 {sc['name']}")

        for m in MODELS:
            mv = (
                db.query(ModelVersion)
                .filter(
                    ModelVersion.scene_id == m["scene_id"],
                    ModelVersion.version == m["version"],
                )
                .first()
            )
            if mv:
                for k, v in m.items():
                    setattr(mv, k, v)
                print(f"[更新] 模型版本 {m['scene_id']}/{m['version']}")
            else:
                db.add(
                    ModelVersion(
                        **m,
                        is_active=True,
                        hire_date=datetime.utcnow(),
                        total_inferences=0,
                        health_status="healthy",
                    )
                )
                print(f"[新增] 模型版本 {m['scene_id']}/{m['version']}")

        db.commit()

        print("\n=== 当前场景包 ===")
        for x in db.query(ScenePackage).all():
            has_model = (
                db.query(ModelVersion)
                .filter(ModelVersion.scene_id == x.name, ModelVersion.is_active == True)
                .first()
            )
            print(
                f"  {x.name:12} {x.display_name:18} [{x.status}] 模型={has_model.version if has_model else '—'}"
            )
        print("\n[完成] /api/scenes 现在共 6 个场景，全部 running")
    finally:
        db.close()


if __name__ == "__main__":
    main()
