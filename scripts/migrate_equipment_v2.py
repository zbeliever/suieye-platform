"""设备域 v2 迁移（幂等）：台账 / 维护记录 / 报修工单 / 工单流水 / 操作日志 落库

背景（2026-10-14 修）：
    设备模块原先全部走进程内存（defaultdict / list），后端一重启，报修工单、
    维护记录、启停状态、操作日志全部归零；而且工单"只写不读"——前端从未查询
    /repairs，提交完只在系统里飘一个 toast，无处可见。演示时等同于假交互。

本脚本建 5 张表，并把代码里的设备台账与历史维护记录 seed 进库。
之后运行时一律**以库为准**（维护完成、工单闭环会更新台账 last_maintain）。

用法：
    python scripts/migrate_equipment_v2.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.api.routers.equipment import _EQUIPMENT_BASELINE, _maintenance_seeds
from backend.models.database import (
    EquipmentAsset,
    EquipmentMaintenanceRecord,
    SessionLocal,
    init_db,
)

# 从代码基线 seed 到台账的身份字段（仿真参数 base_metrics/noise 不进库）
IDENTITY_FIELDS = [
    "name", "type", "desc", "model", "manufacturer", "install_date",
    "total_hours", "last_maintain", "mtbf_hours", "location_tag", "base_health",
]


def main() -> int:
    # ---- 1. 建表（幂等） ----
    init_db()
    print("[1/3] 5 张设备表就绪: equipment_assets / equipment_maintenance_records / "
          "equipment_repair_orders / equipment_repair_actions / equipment_operation_logs")

    db = SessionLocal()

    # ---- 2. seed 台账（已存在的不动，避免覆盖运行时更新） ----
    added_assets = 0
    for eid, base in _EQUIPMENT_BASELINE.items():
        if db.query(EquipmentAsset).filter(EquipmentAsset.id == eid).first():
            continue
        db.add(EquipmentAsset(
            id=eid,
            control_state="running",
            **{f: base.get(f) for f in IDENTITY_FIELDS},
        ))
        added_assets += 1
    db.commit()
    total_assets = db.query(EquipmentAsset).count()
    print(f"[2/3] 台账 seed: 新增 {added_assets} 台，库中共 {total_assets} 台")

    # ---- 3. seed 历史维护记录（按设备补齐，避免重复） ----
    # ⚠️ 原逻辑是"整表为空才灌" —— 只要库里有任何一条就不灌。
    #    后果：后来新增的设备（如 2026-10-17 加的三台水处理设备）永远拿不到
    #    种子记录，点开维护记录是空列表。改为**逐设备按 record_id 补齐**，
    #    既幂等，又不受"加设备"影响。
    added = 0
    for eid, records in _maintenance_seeds.items():
        have = {rid for (rid,) in db.query(EquipmentMaintenanceRecord.record_id).filter(
            EquipmentMaintenanceRecord.equipment_id == eid).all()}
        for r in records:
            if r["id"] in have:
                continue
            db.add(EquipmentMaintenanceRecord(
                record_id=r["id"],
                equipment_id=eid,
                date=r["date"],
                type=r["type"],
                technician=r["technician"],
                content=r["content"],
                result=r["result"],
            ))
            added += 1
    db.commit()
    print(f"[3/3] 维护记录 seed: 本次新增 {added} 条")

    print("\n== 迁移后统计 ==")
    print("   equipment_assets            :", db.query(EquipmentAsset).count())
    print("   equipment_maintenance_records:", db.query(EquipmentMaintenanceRecord).count())
    for eid in _EQUIPMENT_BASELINE:
        n = db.query(EquipmentMaintenanceRecord).filter(
            EquipmentMaintenanceRecord.equipment_id == eid).count()
        print(f"      {eid:<14} {n} 条")

    db.close()
    print("\n[OK] 迁移完成（幂等，可重复执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
