#!/usr/bin/env python3
"""批处理灌库脚本 —— 把 dataset/ 的真实图像跑进 suieye.db

用法:
    python scripts/batch_import_demo.py                    # 全量 3004 张
    python scripts/batch_import_demo.py --limit 100        # 只跑前 100 张（测试用）
    python scripts/batch_import_demo.py --skip-existing    # 跳过已存在的 group

数据流:
    dataset/allpictures/YYYYMMDDHHMM_N.jpg
      → cv2.imread → run_analysis('flotation')
      → AnalysisRecord (features + prediction + alarms)
      → actual_value 从 labels.xlsx 回填（按组序号对齐）
"""

import argparse
import os
import sys
import time
import hashlib
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

# ---- 项目根目录 ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 路径常量 ----
# 数据集在旧项目目录 D:/flaoting/dataset/
_DATASET_ROOT = Path("D:/flaoting/dataset")
DATASET_DIR = _DATASET_ROOT / "allpictures"
LABELS_XLSX = _DATASET_ROOT / "labels.xlsx"
UPLOAD_ROOT = PROJECT_ROOT / "uploads"

# 延迟导入（避免影响模块加载速度）
def _get_db_session():
    from backend.models.database import SessionLocal, init_db
    init_db()
    return SessionLocal()

def _get_labels_df():
    """加载化验台账，返回 DataFrame（行号 = 组序号）。"""
    if not LABELS_XLSX.exists():
        print(f"[WARN] 化验台账不存在: {LABELS_XLSX}，actual_value 将为空")
        return None
    df = pd.read_excel(str(LABELS_XLSX))
    print(f"[INFO] 台账已加载: {len(df)} 行, 列={list(df.columns)}")
    return df


def scan_images(limit=None):
    """扫描数据集，返回 (filepath, group_id, seq) 列表，按文件名排序。"""
    if not DATASET_DIR.exists():
        print(f"[ERROR] 数据集目录不存在: {DATASET_DIR}")
        sys.exit(1)

    files = sorted(DATASET_DIR.glob("*.jpg"))
    if not files:
        # 也试试 .jpeg / .png
        files = sorted(DATASET_DIR.glob("*.jpeg")) + sorted(DATASET_DIR.glob("*.png"))

    results = []
    for f in files:
        name = f.stem  # YYYYMMDDHHMM_N
        parts = name.rsplit("_", 1)
        if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) >= 10:
            group_id = parts[0]
            seq = int(parts[1])
            results.append((str(f), group_id, seq))

    if limit:
        results = results[:limit]

    print(f"[INFO] 扫描到 {len(results)} 张图像 (数据集共 {len(files)} 张)")
    return results


def run_batch(image_list, labels_df=None, skip_existing=False):
    """批量跑分析并入库。"""
    from backend.models.database import (
        AnalysisRecord, AlarmRecord, ModelVersion, init_db
    )
    from backend.scenes.runtime import run_analysis
    from backend.utils.image_utils import save_image

    init_db()
    db = _get_db_session()

    # 已有 group 去重
    existing_groups = set()
    if skip_existing:
        existing_groups = set(
            r[0] for r in db.query(AnalysisRecord.group_id)
            .filter(AnalysisRecord.scene_id == "flotation").distinct().all()
        )
        print(f"[INFO] 数据库已有 {len(existing_groups)} 个 group，将跳过")

    total = len(image_list)
    success = 0
    skipped = 0
    errors = 0
    total_ms = 0
    n_alarms = 0
    t_start = time.time()

    # 按组聚合（同组 4 张图共享 actual_value）
    groups = {}
    for filepath, gid, seq in image_list:
        if gid not in groups:
            groups[gid] = []
        groups[gid].append((filepath, seq))

    group_list = sorted(groups.items())

    for gidx, (gid, img_entries) in enumerate(group_list):
        if skip_existing and gid in existing_groups:
            skipped += len(img_entries)
            continue

        # 取该组 actual_value（从 labels_df 按组序号取）
        actual_val = None
        if labels_df is not None and gidx < len(labels_df):
            row = labels_df.iloc[gidx]
            # labels.xlsx 列: col0(杂质), pb_grade, zn_grade
            pb = row.get("pb_grade", None)
            zn = row.get("zn_grade", None)
            if pd.notna(pb):
                actual_val = float(pb)

        # 该组每张图都跑
        for filepath, seq in img_entries:
            try:
                img = cv2.imread(filepath)
                if img is None:
                    print(f"  [SKIP] 读图失败: {os.path.basename(filepath)}")
                    errors += 1
                    continue

                t0 = time.perf_counter()
                ctx = run_analysis("flotation", img, source="batch_import")
                ms = (time.perf_counter() - t0) * 1000
                total_ms += ms

                # 存档原图
                raw = open(filepath, "rb").read()
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                digest = hashlib.md5(raw).hexdigest()[:8]
                scene_dir = UPLOAD_ROOT / "flotation"
                scene_dir.mkdir(parents=True, exist_ok=True)
                orig_path = scene_dir / f"{stamp}_{digest}_orig.jpg"
                overlay_path = scene_dir / f"{stamp}_{digest}_seg.jpg"

                save_image(ctx.raw_image, str(orig_path))
                if "boundary_overlay" in ctx.overlays:
                    save_image(ctx.overlays["boundary_overlay"], str(overlay_path))

                # 入库：分析记录
                record = AnalysisRecord(
                    scene_id="flotation",
                    group_id=gid,
                    image_path=str(orig_path).replace("\\", "/"),
                    features=ctx.features,
                    prediction={
                        **ctx.prediction,
                        "n_foams": ctx.metrics.get("n_foams"),
                        "pipeline_ms": ctx.metrics.get("pipeline_ms"),
                    },
                    model_version=ctx.prediction.get("model"),
                    actual_value=actual_val,
                    source="batch_import",
                )
                db.add(record)
                db.flush()  # 拿 record.id

                # 入库：告警记录
                for alarm in ctx.alarms:
                    db.add(AlarmRecord(
                        scene_id="flotation",
                        level=alarm["level"],
                        rule_name=alarm["rule_name"],
                        message=alarm["message"],
                        evidence={
                            "image": str(orig_path).replace("\\", "/"),
                            "overlay": str(overlay_path).replace("\\", "/"),
                            "prediction": ctx.prediction,
                            "metrics": ctx.metrics,
                            "record_id": record.id,
                        },
                    ))
                    n_alarms += 1

                # 模型推理次数 +1
                mv = db.query(ModelVersion).filter(
                    ModelVersion.scene_id == "flotation",
                    ModelVersion.version == ctx.prediction.get("model"),
                ).first()
                if mv:
                    mv.total_inferences = (mv.total_inferences or 0) + 1

                db.commit()
                success += 1

            except Exception as e:
                errors += 1
                db.rollback()
                if errors <= 3:  # 只打印前 3 个错误避免刷屏
                    print(f"  [ERROR] {os.path.basename(filepath)}: {e}")

        # 进度
        done = gidx + 1
        avg_ms = total_ms / max(success, 1)
        eta_s = (total - done) * avg_ms / 1000 * (4 if img_entries else 1)
        if done % 50 == 0 or done == total:
            elapsed = time.time() - t_start
            print(f"  [{done}/{total}] 组 {gid} | "
                  f"成功={success} 跳过={skipped} 错误={errors} | "
                  f"告警={n_alarms} | "
                  f"耗时={elapsed:.0f}s | 预计剩余={eta_s:.0f}s")

    db.close()

    elapsed = time.time() - t_start
    print("\n" + "=" * 60)
    print(f"灌库完成！")
    print(f"  总图像:   {total}")
    print(f"  成功入库: {success}")
    print(f"  跳过:     {skipped}")
    print(f"  错误:     {errors}")
    print(f"  告警生成: {n_alarms}")
    print(f"  总耗时:   {elapsed:.1f}s ({total/elapsed:.1f} 张/秒)")
    print("=" * 60)

    return {"success": success, "errors": errors, "alarms": n_alarms}


def main():
    parser = argparse.ArgumentParser(description="批处理灌库：真实图像 → 分析管线 → SQLite")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 张图（测试用）")
    parser.add_argument("--skip-existing", action="store_true", help="跳过数据库已有的 group")
    args = parser.parse_args()

    print("=" * 60)
    print("SuiEye 批处理灌库工具")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 加载台账
    labels_df = _get_labels_df()

    # 2. 扫描图像
    image_list = scan_images(limit=args.limit)
    if not image_list:
        print("[ERROR] 没有找到可处理的图像，退出")
        sys.exit(1)

    # 3. 批量执行
    result = run_batch(image_list, labels_df=labels_df, skip_existing=args.skip_existing)

    # 4. 快速验证
    print("\n[验证] 查询数据库...")
    db = _get_db_session()
    from backend.models.database import AnalysisRecord, AlarmRecord
    n_records = db.query(AnalysisRecord).filter(AnalysisRecord.scene_id == "flotation").count()
    n_groups = db.query(AnalysisRecord.group_id).filter(
        AnalysisRecord.scene_id == "flotation"
    ).distinct().count()
    n_alarms_db = db.query(AlarmRecord).filter(AlarmRecord.scene_id == "flotation").count()
    n_with_actual = db.query(AnalysisRecord).filter(
        AnalysisRecord.scene_id == "flotation",
        AnalysisRecord.actual_value.isnot(None),
    ).count()
    print(f"  分析记录: {n_records} 条 ({n_groups} 个组)")
    print(f"  告警记录: {n_alarms_db} 条")
    print(f"  有真值(actual_value): {n_with_actual} 条 ({n_with_actual/max(n_records,1)*100:.1f}%)")
    db.close()


if __name__ == "__main__":
    main()
