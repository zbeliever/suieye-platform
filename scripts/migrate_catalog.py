"""导入 znkc_catalog.json 的 3004 条历史记录到数据库

一次性脚本（幂等：重复运行会先清空 flotation 场景数据）。

用法：
    cd suieye-platform
    python scripts/migrate_catalog.py [catalog.json路径]
"""

import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.models.database import (
    init_db,
    SessionLocal,
    ScenePackage,
    AnalysisRecord,
    ModelVersion,
)

DEFAULT_CATALOG = "../铅锌浮选品位预测系统/data/znkc_catalog.json"


def parse_group_time(group: str) -> datetime:
    """group 形如 202009090951 → 2020-09-09 09:51:00"""
    return datetime.strptime(str(group), "%Y%m%d%H%M")


def main():
    catalog_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CATALOG
    print(f"读取 catalog: {catalog_path}")
    with open(catalog_path, encoding="utf-8") as f:
        cat = json.load(f)

    init_db()
    db = SessionLocal()

    try:
        # ---- 幂等：清空 flotation 旧数据 ----
        deleted = db.query(AnalysisRecord).filter(
            AnalysisRecord.scene_id == "flotation",
            AnalysisRecord.source == "batch_import",
        ).delete()
        if deleted:
            print(f"清除旧导入记录 {deleted} 条")
        db.query(ScenePackage).filter(ScenePackage.name == "flotation").delete()
        db.query(ModelVersion).filter(ModelVersion.scene_id == "flotation").delete()
        db.commit()

        # ---- 注册浮选场景包 ----
        scene = ScenePackage(
            name="flotation",
            display_name="浮选品位预测",
            description="首个场景包：泡沫图像分割 → 特征提取 → XGBoost 品位预测 → XRF 台账对标",
            config={
                "prediction_targets": [
                    {"name": "pb_grade", "unit": "%", "range": [0.3, 4.0]},
                    {"name": "zn_grade", "unit": "%", "range": [0.05, 1.5]},
                ],
                "assay_mapping": {"ledger_type": "xrf", "time_window_min": 30},
                "history": {
                    "images": cat.get("image_count"),
                    "groups": cat.get("group_count"),
                    "date_range": ["2020-09-09", "2020-09-18"],
                },
            },
            status="active",
        )
        db.add(scene)

        # ---- 注册 XGBoost 模型版本（员工档案） ----
        model = ModelVersion(
            scene_id="flotation",
            version="xgb-v1",
            algorithm="XGBoost",
            file_path="data/model_pb_xgb_v1.json",
            metrics={
                "test_mae_temporal": 0.222,   # 时间外推
                "test_mae_manual_baseline": 0.267,
                "improvement_vs_manual": "17%",
                "features": 7,
                "note": "7 维共享特征；3004 张真实生产图像训练",
            },
            training_data_count=cat.get("image_count"),
            trained_at=datetime(2026, 9, 30, 16, 0),
            is_active=True,
            hire_date=datetime(2026, 9, 30, 16, 0),  # 入职日期
            total_inferences=0,
            health_status="healthy",
        )
        db.add(model)

        # ---- 导入分析记录 ----
        records = cat["records"]
        count = 0
        for fname, rec in records.items():
            group = rec.get("group")
            pb = rec.get("pb_grade")
            zn = rec.get("zn_grade")
            ar = AnalysisRecord(
                scene_id="flotation",
                group_id=str(group),
                image_path=rec.get("source_path") or rec.get("image") or fname,
                features=rec.get("features_dataset"),
                prediction={
                    "pb_grade_xrf": pb,   # 历史数据：XRF 台账真值
                    "zn_grade_xrf": zn,
                },
                model_version="legacy-catalog",
                actual_value=float(pb) if pb is not None else None,
                source="batch_import",
                created_at=parse_group_time(group) if group else datetime.utcnow(),
            )
            db.add(ar)
            count += 1
            if count % 1000 == 0:
                db.commit()
                print(f"  已导入 {count}/{len(records)}")

        db.commit()

        # ---- 校验 ----
        total = db.query(AnalysisRecord).filter(
            AnalysisRecord.scene_id == "flotation"
        ).count()
        groups = db.query(AnalysisRecord.group_id).filter(
            AnalysisRecord.scene_id == "flotation"
        ).distinct().count()
        with_actual = db.query(AnalysisRecord).filter(
            AnalysisRecord.scene_id == "flotation",
            AnalysisRecord.actual_value.isnot(None),
        ).count()

        print()
        print("=== 导入完成 ===")
        print(f"analysis_records (flotation): {total} 条")
        print(f"时间组: {groups} 个")
        print(f"带品位真值: {with_actual} 条")
        print(f"scene_packages: flotation 已注册")
        print(f"model_versions: xgb-v1 已注册（active）")

    finally:
        db.close()


if __name__ == "__main__":
    main()
