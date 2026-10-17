"""设备监测 API — 实时指标仿真 + 台账/工单/维护落库 + 3D 可视化数据源

v2（2026-10-14）：
    原先维护记录 / 报修工单 / 启停状态 / 操作日志全部存在进程内存里，后端一重启
    即归零；工单还"只写不读"（前端从未查询 /repairs），提交完在系统里无处可见。
    现在：
      - equipment_assets            台账（身份字段以库为准）
      - equipment_maintenance_records 维护记录（工单闭环可自动回写）
      - equipment_repair_orders     报修工单（pending→assigned→repairing→fixed/rejected）
      - equipment_repair_actions    工单流转流水（审计）
      - equipment_operation_logs    操作日志
    实时指标（温度/振动/功率…）仍是仿真值，属"示教数据"，不入库。

路由注意：工单中心走 /api/repairs 顶级前缀，**不要**写成 /api/equipment/repairs，
否则会被 /api/equipment/{equip_id} 抢先匹配。
"""

import math
import random
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.models.database import (
    EquipmentAsset,
    EquipmentMaintenanceRecord,
    EquipmentOperationLog,
    EquipmentRepairAction,
    EquipmentRepairOrder,
    get_db,
)

router = APIRouter()

# 设备基线配置（每台设备的"性格"——均值和正常波动范围）
_EQUIPMENT_BASELINE = {
    "flotation_1": {
        "name": "1#浮选槽", "type": "flotation",
        "desc": "粗选浮选槽，负责铅锌矿物与脉石分离",
        "position": [0, 0, 0],
        "base_metrics": {"temperature": 42.0, "vibration": 1.2, "power": 185, "rpm": 180, "flow_rate": 320},
        "noise": {"temperature": 2.5, "vibration": 0.4, "power": 12, "rpm": 3, "flow_rate": 18},
        "base_health": 92,
        "model": "JJF-8", "manufacturer": "北矿院机电",
        "install_date": "2024-03", "total_hours": 14500,
        "last_maintain": "2026-09-15", "mtbf_hours": 7200, "location_tag": "FL-101",
    },
    "flotation_2": {
        "name": "2#浮选槽", "type": "flotation",
        "desc": "扫选浮选槽，回收剩余有用矿物",
        "position": [-5, 0, 0],
        "base_metrics": {"temperature": 52.0, "vibration": 2.8, "power": 200, "rpm": 175, "flow_rate": 300},
        "noise": {"temperature": 6.0, "vibration": 1.0, "power": 15, "rpm": 4, "flow_rate": 22},
        "base_health": 68,
        "model": "JJF-8", "manufacturer": "北矿院机电",
        "install_date": "2024-03", "total_hours": 14200,
        "last_maintain": "2026-09-20", "mtbf_hours": 6800, "location_tag": "FL-102",
    },
    "ball_mill": {
        "name": "球磨机", "type": "mill",
        "desc": "湿式溢流型球磨机，磨矿细度-200目占70%",
        "position": [6, 0, -3],
        "base_metrics": {"temperature": 65.0, "vibration": 2.1, "power": 1250, "rpm": 22, "flow_rate": 450},
        "noise": {"temperature": 4.0, "vibration": 0.6, "power": 50, "rpm": 1, "flow_rate": 25},
        "base_health": 88,
        "model": "MQY2740", "manufacturer": "沈阳重型机械",
        "install_date": "2023-11", "total_hours": 21500,
        "last_maintain": "2026-09-01", "mtbf_hours": 18000, "location_tag": "BM-201",
    },
    "pump_1": {
        "name": "1#渣浆泵", "type": "pump",
        "desc": "AH型渣浆泵，输送浮选给矿矿浆",
        "position": [-3, 0, 4],
        "base_metrics": {"temperature": 38.0, "vibration": 0.8, "power": 75, "rpm": 1480, "flow_rate": 380},
        "noise": {"temperature": 2.0, "vibration": 0.3, "power": 6, "rpm": 10, "flow_rate": 20},
        "base_health": 95,
        "model": "100AH", "manufacturer": "石家庄工业泵",
        "install_date": "2024-06", "total_hours": 12800,
        "last_maintain": "2026-09-25", "mtbf_hours": 9000, "location_tag": "PP-301",
    },
    "conveyor": {
        "name": "皮带输送机", "type": "conveyor",
        "desc": "TD75型带式输送机，精矿运输",
        "position": [0, 0, -6],
        "base_metrics": {"temperature": None, "vibration": 3.5, "power": 30, "rpm": 120, "flow_rate": 200},
        "noise": {"temperature": 0, "vibration": 1.2, "power": 4, "rpm": 5, "flow_rate": 15},
        "base_health": 35,
        "model": "TD75-800", "manufacturer": "鹤壁煤机",
        "install_date": "2023-08", "total_hours": 23100,
        "last_maintain": "2026-08-10", "mtbf_hours": 15000, "location_tag": "CV-401",
    },
    "thickener": {
        "name": "浓密机", "type": "thickener",
        "desc": "高效浓密机，底流浓度45-50%",
        "position": [5, 0, 4],
        "base_metrics": {"temperature": 32.0, "vibration": 0.3, "power": 15, "rpm": 0.15, "flow_rate": 500},
        "noise": {"temperature": 1.5, "vibration": 0.15, "power": 3, "rpm": 0.03, "flow_rate": 20},
        "base_health": 85,
        "model": "NZ-12", "manufacturer": "隆特重工",
        "install_date": "2024-01", "total_hours": 13500,
        "last_maintain": "2026-09-05", "mtbf_hours": 20000, "location_tag": "TH-501",
    },
    "agitator": {
        "name": "搅拌槽", "type": "agitator",
        "desc": "药剂搅拌槽，石灰与捕收剂调配",
        "position": [-6, 0, -4],
        "base_metrics": {"temperature": 35.0, "vibration": 1.0, "power": 45, "rpm": 240, "flow_rate": 280},
        "noise": {"temperature": 2.0, "vibration": 0.3, "power": 5, "rpm": 8, "flow_rate": 16},
        "base_health": 90,
        "model": "XB-1500", "manufacturer": "烟台鑫海矿机",
        "install_date": "2024-05", "total_hours": 13900,
        "last_maintain": "2026-09-12", "mtbf_hours": 12000, "location_tag": "AG-601",
    },
}

# ============================================================
# 存储层说明（v2）
#
# 内存 store（_maintenance_store / _repair_store / _control_state /
# _operation_logs）已全部移除，改走 SQLite 落库，重启不丢。
# 下方 _maintenance_seeds 只作为 scripts/migrate_equipment_v2.py 的
# 一次性 seed 源保留，运行时不再直接使用。
# ============================================================

# 维护记录种子数据（按设备预填，让前端一打开就有数据看）
_maintenance_seeds = {
    "flotation_1": [
        {"id": "M001", "date": "2026-09-15", "type": "定期保养", "technician": "张工", "content": "检查搅拌轴密封，补充减速机齿轮油", "result": "正常"},
        {"id": "M002", "date": "2026-08-20", "type": "巡检", "technician": "赵工", "content": "例行巡检，叶轮磨损轻微", "result": "正常"},
    ],
    "flotation_2": [
        {"id": "M003", "date": "2026-09-20", "type": "故障维修", "technician": "李工", "content": "温度偏高，清理冷却水过滤器后恢复", "result": "已修复"},
    ],
    "ball_mill": [
        {"id": "M004", "date": "2026-09-01", "type": "定期保养", "technician": "王工", "content": "更换进出料端衬板，检查主轴承润滑", "result": "正常"},
        {"id": "M005", "date": "2026-07-15", "type": "故障维修", "technician": "李工", "content": "小齿轮轴承异响，更换 6224 轴承", "result": "已修复"},
    ],
    "pump_1": [
        {"id": "M006", "date": "2026-09-25", "type": "定期保养", "technician": "赵工", "content": "更换机械密封，检查叶轮磨损", "result": "正常"},
    ],
    "conveyor": [
        {"id": "M007", "date": "2026-08-10", "type": "故障维修", "technician": "王工", "content": "皮带跑偏，调整张紧装置，更换 2 组托辊", "result": "已修复"},
    ],
    "thickener": [
        {"id": "M008", "date": "2026-09-05", "type": "定期保养", "technician": "张工", "content": "清理溢流堰，检查耙臂刮泥板磨损", "result": "正常"},
    ],
    "agitator": [
        {"id": "M009", "date": "2026-09-12", "type": "定期保养", "technician": "赵工", "content": "更换搅拌轴密封件，检查叶轮磨损", "result": "正常"},
    ],
}
# ============================================================
# 辅助函数：设备集合 / 台账合并 / 操作日志 / 快照仿真
# ============================================================

def _asset_ids(db: Session) -> list:
    """设备集合（顺序以代码基线为准，保证前端 3D 布局稳定）

    台账表为唯一真源，但代码基线里仍留着 position/仿真参数，且历史前端
    依赖固定顺序，所以：代码顺序在前，库里多出来的设备追加在后。
    """
    ids = list(_EQUIPMENT_BASELINE)
    seen = set(ids)
    for (eid,) in db.query(EquipmentAsset.id).all():
        if eid not in seen:
            ids.append(eid)
            seen.add(eid)
    return ids


def _baseline(db: Session, equip_id: str):
    """合并「台账身份字段（库）」+「仿真参数（代码）」

    身份字段以库为准 —— 否则维护完成、工单闭环对 last_maintain 的更新
    永远生效不了（这正是旧实现"报修也没人修"的根源之一）。
    """
    params = _EQUIPMENT_BASELINE.get(equip_id)
    row = db.query(EquipmentAsset).filter(EquipmentAsset.id == equip_id).first()
    if params is None and row is None:
        return None

    merged = dict(params) if params else {}
    if row is not None:
        merged.update({
            "name": row.name,
            "type": row.type,
            "desc": row.desc or merged.get("desc", ""),
            "base_health": row.base_health if row.base_health is not None
            else merged.get("base_health", 80),
            "model": row.model or "",
            "manufacturer": row.manufacturer or "",
            "install_date": row.install_date or "",
            "total_hours": row.total_hours or 0,
            "last_maintain": row.last_maintain or "",
            "mtbf_hours": row.mtbf_hours or 0,
            "location_tag": row.location_tag or "",
        })
        merged["control_state"] = row.control_state or "running"
        # 停机时刻 = 台账 updated_at（control 接口每次启停都会刷新它）
        # → 温度冷却曲线以它为起点，见 _stop_epoch()
        merged["state_changed_at"] = row.updated_at

    merged.setdefault("base_health", 80)
    merged.setdefault("control_state", "running")
    merged.setdefault("position", [0, 0, 0])
    merged.setdefault("desc", "")
    if not merged.get("base_metrics"):
        merged["base_metrics"] = {"temperature": None, "vibration": 0, "power": 0, "rpm": 0, "flow_rate": 0}
    if not merged.get("noise"):
        merged["noise"] = {k: 0 for k in merged["base_metrics"]}
    return merged


def _write_log(db: Session, action: str, operator: str = "操作员",
               equipment_id: str = None, type_: str = "info") -> EquipmentOperationLog:
    """写一条操作日志（落库，随调用方事务一起提交）"""
    entry = EquipmentOperationLog(
        equipment_id=equipment_id,
        action=action,
        operator=operator,
        type=type_,
        created_at=datetime.now(),
    )
    db.add(entry)
    return entry


def _log_dict(x: EquipmentOperationLog) -> dict:
    return {
        "id": x.id,
        "time": x.created_at.strftime("%Y-%m-%d %H:%M:%S") if x.created_at else "",
        "equipment_id": x.equipment_id,
        "action": x.action,
        "operator": x.operator,
        "type": x.type,
    }


# ============================================================
# 停机语义（2026-10-16）
# —— 单击「停机」只翻状态**不算真停机**：转速/功率/流量/振动必须真的归零，
#    温度按热惯性向环境温度冷却（起点 = 台账 updated_at，即最后一次启停操作时刻）。
#    否则面板上「已停机」的设备还在 1300kW / 22rpm 地跳、温度还在涨，一眼假。
# ============================================================
_COOL_ENV_TEMP = 25.0    # 环境温度（°C）
_COOL_TAU_MIN = 8.0      # 冷却时间常数（分钟）——演示口径：停机后 1 分钟可见缓慢下降

# 停机后立即归零的"运动类"指标
_STOP_ZERO_FIELDS = ("rpm", "power", "flow_rate", "vibration")


def _cooled_temperature(base: dict, elapsed_min: float):
    """停机后的温度：从运行温度向环境温度指数冷却 T = T_env + (T_run - T_env)·e^(-t/τ)"""
    t_run = (base.get("base_metrics") or {}).get("temperature")
    if t_run is None:
        return None
    return round(_COOL_ENV_TEMP + (t_run - _COOL_ENV_TEMP) * math.exp(-elapsed_min / _COOL_TAU_MIN), 1)


def _apply_stopped(base: dict, metrics: dict, elapsed_min: float) -> dict:
    """把停机语义套到一组指标上（原地修改）：运动量归零 + 温度冷却

    只处理已存在的键 —— 兼容趋势接口的 metric 过滤（被过滤掉的指标不补 0）。
    """
    for k in _STOP_ZERO_FIELDS:
        if k in metrics:
            metrics[k] = 0.0
    if metrics.get("temperature") is not None:
        metrics["temperature"] = _cooled_temperature(base, elapsed_min)
    return metrics


def _stop_epoch(base: dict):
    """停机时刻的 epoch（秒）；未停机 / 无记录返回 None

    ⚠️ 时间基准有两代写入：新代码写 datetime.utcnow()；历史版本（≤2026-10-15）
    写的是 datetime.now()（本地时间）。本地时间被当 UTC 解释会落在未来 8 小时，
    检出后回退按本地时区解释 —— 保证两代数据都能算出正确的停机时长。
    """
    since = base.get("state_changed_at")
    if base.get("control_state") != "stopped" or since is None:
        return None
    epoch = since.replace(tzinfo=timezone.utc).timestamp()
    if epoch > _time.time() + 60:      # 落在未来 → 老数据（本地时间语义），回退本地解释
        epoch = since.timestamp()
    return epoch


def _sample_point(equip_id: str, base: dict, t: float, metric: str = "all") -> dict:
    """生成某时刻的仿真采样（health + 指标），含停机语义。

    趋势（history）与详情（detail）共用同一实现：停机时刻之后的采样点
    运动量归零、温度走冷却曲线、健康分冻结 —— 保证「停机断崖」在
    所有时间序列接口里表现一致，而不是只有实时看板对、趋势图照跳。
    """
    rng = random.Random(int(t // 5) + hash(equip_id) % 10000)
    point = {}
    stop_epoch = _stop_epoch(base)
    stopped = stop_epoch is not None and t >= stop_epoch
    if stopped:
        point["health"] = int(base["base_health"])      # 停机后无运行应力，健康分冻结
    else:
        point["health"] = max(10, min(100, base["base_health"] + rng.randint(-8, 8)))
    for k, v in base["base_metrics"].items():
        if metric != "all" and k != metric:
            continue
        if v is None:
            point[k] = None
            continue
        if stopped:
            point[k] = 0.0
        else:
            n = base["noise"][k]
            val = v + rng.gauss(0, n)
            ranges = {"temperature": (20, 90), "vibration": (0, 10),
                      "power": (0, 2000), "rpm": (0, 3000), "flow_rate": (0, 800)}
            lo, hi = ranges.get(k, (0, 9999))
            point[k] = round(max(lo, min(hi, val)), 1)
    if stopped and point.get("temperature") is not None:
        point["temperature"] = _cooled_temperature(base, (t - stop_epoch) / 60.0)
    return point


# 用固定种子保证同一秒内多次调用返回一致结果（避免前端闪烁）
def _simulate_equipment(equip_id: str, seed: int = None, base: dict = None):
    """生成单台设备的模拟快照（base 由调用方从台账合并后传入）"""
    base = base or _EQUIPMENT_BASELINE.get(equip_id)
    if not base:
        return None

    rng = random.Random(seed or int(_time.time() // 5))  # 每 5 秒换一组随机数

    # 指标 = 基线 + 高斯噪声（截断到合理范围）
    metrics = {}
    for k, v in base["base_metrics"].items():
        if v is None:
            metrics[k] = None
            continue
        n = base["noise"][k]
        val = v + rng.gauss(0, n)
        # 截断到物理合理范围
        ranges = {
            "temperature": (20, 90), "vibration": (0, 10),
            "power": (0, 2000), "rpm": (0, 3000), "flow_rate": (0, 800),
        }
        lo, hi = ranges.get(k, (0, 9999))
        metrics[k] = round(max(lo, min(hi, val)), 1)

    # 健康度：基于指标偏离程度动态计算
    health = base["base_health"]
    if metrics.get("temperature") and metrics["temperature"] > 55:
        health -= int((metrics["temperature"] - 55) * 1.5)
    if metrics.get("vibration") and metrics["vibration"] > 3:
        health -= int((metrics["vibration"] - 3) * 5)
    health = max(10, min(100, health + rng.randint(-3, 3)))

    # 状态判定
    if health < 40:
        status = "danger"
        alarm = "设备健康度过低，建议立即停机检修"
    elif health < 65 or (metrics.get("temperature") and metrics["temperature"] > 55) or (metrics.get("vibration") and metrics["vibration"] > 3.5):
        status = "warning"
        alarms_map = {
            "flotation_2": "温度偏高({t}°C)，检查冷却系统".format(t=metrics.get("temperature", "?")),
            "conveyor": "振动偏大({v}mm/s)，检查皮带跑偏和张紧".format(v=metrics.get("vibration", "?")),
        }
        alarm = alarms_map.get(equip_id, "运行参数异常，请关注")
    else:
        status = "running"
        alarm = None

    # 手动启停覆盖（已落库，刷新/重启都不还原）
    if base.get("control_state") == "stopped":
        status = "stopped"
        alarm = None
        # ★ 停机 = 机器真的停（2026-10-16 修）：状态翻成 stopped 只是"标记"，
        #   · 运动类指标（转速/功率/流量/振动）立即归零 —— 不归零就是"停机了还在耗 1300kW"；
        #   · 温度按热惯性向环境温度冷却（起点 = 最后一次启停操作时刻）；
        #   · 健康分冻结在基准值 —— 停机后没有运行应力，不该再 ±3 随机抖动。
        health = int(base["base_health"])
        stop_epoch = _stop_epoch(base) or _time.time()
        elapsed_min = min(max(0.0, (_time.time() - stop_epoch) / 60.0), 48 * 60)
        _apply_stopped(base, metrics, elapsed_min)

    return {
        "id": equip_id,
        "name": base["name"],
        "type": base["type"],
        "status": status,
        "health": health,
        "metrics": metrics,
        "position": base["position"],
        "alarm": alarm,
        "desc": base["desc"],
        "model": base.get("model", ""),
        "manufacturer": base.get("manufacturer", ""),
        "install_date": base.get("install_date", ""),
        "total_hours": base.get("total_hours", 0),
        "last_maintain": base.get("last_maintain", ""),
        "mtbf_hours": base.get("mtbf_hours", 0),
        "location_tag": base.get("location_tag", ""),
    }


@router.get("/api/equipment")
def list_equipment(db: Session = Depends(get_db)):
    """设备监测 3D 场景数据源——实时指标为仿真值，台账字段取自库"""
    seed = int(_time.time() // 5)  # 5 秒窗口内稳定
    equipment_list = []
    for eid in _asset_ids(db):
        eq = _simulate_equipment(eid, seed=seed + hash(eid) % 10000, base=_baseline(db, eid))
        if eq:
            equipment_list.append(eq)

    # 聚合统计
    total = len(equipment_list)
    stats = {
        "total": total,
        "running": sum(1 for e in equipment_list if e["status"] == "running"),
        "warning": sum(1 for e in equipment_list if e["status"] == "warning"),
        "danger": sum(1 for e in equipment_list if e["status"] == "danger"),
        # 停机必须单独成档：running/warning/danger 是"在转"的三种健康度，
        # stopped 是"不转"。不加这一档，前端"运行+预警+故障"凑不齐 total，看着像丢了设备。
        "stopped": sum(1 for e in equipment_list if e["status"] == "stopped"),
        "in_service": sum(1 for e in equipment_list if e["status"] != "stopped"),
        "avg_health": round(sum(e["health"] for e in equipment_list) / total, 0) if total else 0,
        "timestamp": datetime.utcnow().isoformat(),
        "refresh_interval_sec": 5,
    }

    return {
        "equipment": equipment_list,
        "stats": stats,
    }


@router.get("/api/equipment/{equip_id}")
def get_equipment_detail(equip_id: str, db: Session = Depends(get_db)):
    """单台设备详情（含历史趋势模拟）"""
    base = _baseline(db, equip_id)
    if not base:
        raise HTTPException(404, f"设备不存在: {equip_id}")

    seed = int(_time.time() // 5)
    current = _simulate_equipment(equip_id, seed=seed + hash(equip_id) % 10000, base=base)

    # 模拟最近 1 小时的趋势数据（12 个点，每 5 分钟一个；含停机语义）
    now = _time.time()
    trend = []
    for i in range(12):
        t = now - (11 - i) * 300  # 300s = 5min
        point = {"time": datetime.fromtimestamp(t).isoformat()}
        point.update(_sample_point(equip_id, base, t))
        trend.append(point)

    return {**current, "trend": trend}


# ============================================================
# 历史趋势 + 预测性维护（新增）
# ============================================================
@router.get("/api/equipment/{equip_id}/history")
def equipment_history(
    equip_id: str,
    hours: int = Query(24, ge=1, le=168, description="回溯小时数（1-168）"),
    metric: str = Query("all", pattern="^(all|temperature|vibration|power|health)$"),
    db: Session = Depends(get_db),
):
    """设备历史趋势 — 基于种子确定性生成的历史数据

    返回从当前时间往回推 hours 小时的趋势数据点，
    采样间隔随 hours 自适应（短时密、长时疏）。
    """
    base = _baseline(db, equip_id)
    if not base:
        raise HTTPException(404, f"设备不存在: {equip_id}")

    now = _time.time()

    # 自适应采样点数：1h→72点(1分钟), 6h→72点(5分钟), 24h→96点(15分钟), 168h→84点(2小时)
    if hours <= 1:
        n_points = 72
        interval = 60
    elif hours <= 6:
        n_points = 72
        interval = 300
    elif hours <= 24:
        n_points = 96
        interval = 900
    else:
        n_points = 84
        interval = 7200

    points = []
    for i in range(n_points):
        t = now - (n_points - 1 - i) * interval
        point = {
            "time": datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": int(t),
        }
        point.update(_sample_point(equip_id, base, t, metric))
        points.append(point)

    # 统计摘要
    def _summary(key):
        vals = [p[key] for p in points if key in p and p[key] is not None]
        if not vals:
            return None
        avg = sum(vals) / len(vals)
        return {
            "avg": round(avg, 2),
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "latest": vals[-1],
            "trend": "up" if vals[-1] > avg else ("down" if vals[-1] < avg else "flat"),
        }

    return {
        "equipment_id": equip_id,
        "name": base["name"],
        "period": {"hours": hours, "points": n_points, "interval_sec": interval},
        "points": points,
        "summary": {
            "health": _summary("health"),
            "temperature": _summary("temperature") if metric in ("all", "temperature") and base["base_metrics"].get("temperature") is not None else None,
            "vibration": _summary("vibration"),
            "power": _summary("power"),
        },
    }


@router.get("/api/equipment/{equip_id}/prediction")
def equipment_prediction(
    equip_id: str,
    horizon_days: int = Query(7, ge=1, le=30, description="预测天数"),
    db: Session = Depends(get_db),
):
    """预测性维护建议 — 基于当前状态做简单阈值规则推断

    演示版规则：
    - 温度>60 持续 → 冷却系统预警
    - 振动>3.5 持续 → 轴承/托辊预警
    - 健康度<50 → 建议停机检修
    - 健康度<70 → 建议安排计划检修
    """
    base = _baseline(db, equip_id)
    if not base:
        raise HTTPException(404, f"设备不存在: {equip_id}")

    seed = int(_time.time() // 5)
    current = _simulate_equipment(equip_id, seed=seed + hash(equip_id) % 10000, base=base)

    metrics = current["metrics"]
    health = current["health"]

    # 风险评估
    risks = []
    recs = []

    temp = metrics.get("temperature")
    vib = metrics.get("vibration")
    power = metrics.get("power")

    if temp and temp > 60:
        risks.append({"factor": "temperature", "value": temp, "threshold": 60, "severity": "high",
                       "desc": f"温度持续偏高({temp}°C > 60°C)，冷却系统可能异常"})
        recs.append("检查冷却水流量和油位，必要时清理散热器")
    elif temp and temp > 55:
        risks.append({"factor": "temperature", "value": temp, "threshold": 55, "severity": "medium",
                       "desc": f"温度略高({temp}°C)"})

    if vib and vib > 3.5:
        risks.append({"factor": "vibration", "value": vib, "threshold": 3.5, "severity": "high",
                       "desc": f"振动偏大({vib}mm/s)，轴承/托辊可能磨损"})
        recs.append("检查轴承润滑，测量振动频谱定位异常源")
    elif vib and vib > 3.0:
        risks.append({"factor": "vibration", "value": vib, "threshold": 3.0, "severity": "medium",
                       "desc": f"振动略高({vib}mm/s)"})

    if health < 50:
        risks.append({"factor": "health", "value": health, "threshold": 50, "severity": "critical",
                       "desc": f"健康度过低({health})"})
        recs.append("立即停机检修，避免设备故障扩大的次生事故")
    elif health < 70:
        risks.append({"factor": "health", "value": health, "threshold": 70, "severity": "high",
                       "desc": f"健康度下降({health})，建议安排计划检修"})

    if not risks:
        recs.append("设备运行状态良好，按计划周期巡检即可")

    # 预测：模拟未来 horizon_days 天的每日健康度衰减
    decay_rate = 0.5 if health < 70 else 0.3
    forecast = []
    for d in range(1, horizon_days + 1):
        h = max(15, health - int(d * decay_rate * random.uniform(0.8, 1.2)))
        level = "normal" if h >= 70 else ("attention" if h >= 50 else "danger")
        forecast.append({"day_offset": d, "date": (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d"),
                          "health": h, "level": level})

    # 推荐检修时间
    first_danger = next((f for f in forecast if f["level"] == "danger"), None)
    first_attention = next((f for f in forecast if f["level"] == "attention"), None)

    next_maintenance = None
    if first_danger:
        next_maintenance = {"date": first_danger["date"], "urgency": "urgent", "reason": "预测健康度将跌入危险区"}
    elif first_attention:
        next_maintenance = {"date": first_attention["date"], "urgency": "planned", "reason": "预测健康度下降需关注"}
    else:
        next_maintenance = {"date": (datetime.now() + timedelta(days=horizon_days)).strftime("%Y-%m-%d"),
                             "urgency": "routine", "reason": "周期内状态稳定，按原计划检修"}

    return {
        "equipment_id": equip_id,
        "name": current["name"],
        "current_status": current["status"],
        "current_health": health,
        "current_metrics": metrics,
        "risks": risks,
        "recommendations": recs,
        "forecast": forecast,
        "next_maintenance": next_maintenance,
        "confidence": 0.72,  # 演示用固定置信度
        "generated_at": datetime.utcnow().isoformat(),
    }


# ============================================================
# 维护记录 / 报修工单 / 启停控制 / 操作日志 接口（v2 全量落库）
# ============================================================

class MaintenanceRecordIn(BaseModel):
    date: str = Field(..., description="维护日期 YYYY-MM-DD")
    type: str = Field("定期保养", description="类型")
    technician: str = Field("操作员")
    content: str = Field(...)
    result: str = Field("正常")


class RepairOrderIn(BaseModel):
    category: str = Field(..., description="故障类型 mechanical/electrical/thermal/vibration/leak/other")
    priority: str = Field("high", description="critical/high/medium")
    description: str = Field(..., min_length=2, description="故障现象描述")
    reporter: str = Field("操作员")


class ControlActionIn(BaseModel):
    action: str = Field(..., description="start 或 stop")
    operator: str = Field("操作员")


class RepairTransitionIn(BaseModel):
    action: str = Field(..., description="assign / start / fix / reject / reopen")
    operator: str = Field(..., min_length=1, max_length=50, description="操作人（岗位或姓名）")
    assignee: Optional[str] = Field(None, description="维修负责人（assign 必填）")
    note: Optional[str] = Field(None, description="处理说明（fix / reject / reopen 必填）")
    action_taken: Optional[str] = Field(None, description="处置措施")
    downtime_minutes: Optional[int] = Field(None, ge=0, description="停机时长（分钟）")


# ---- 工单字典与状态机 ----

_ORDER_STATUS_LABELS = {
    "pending": "待处理",
    "assigned": "已指派",
    "repairing": "维修中",
    "fixed": "已修复",
    "rejected": "已驳回",
}

_ORDER_CATEGORY_LABELS = {
    "mechanical": "机械故障",
    "electrical": "电气故障",
    "thermal": "温度异常",
    "vibration": "振动异常",
    "leak": "泄漏",
    "other": "其他",
}

_ORDER_PRIORITY_LABELS = {"critical": "紧急", "high": "高", "medium": "中"}

_ORDER_ACTION_LABELS = {
    "create": "提交报修",
    "assign": "指派维修",
    "start": "开始维修",
    "fix": "维修完成",
    "reject": "驳回工单",
    "reopen": "重新打开",
}

# 流转动作 → (允许的来源状态集合, 目标状态)
_ORDER_TRANSITIONS = {
    "assign": ({"pending"}, "assigned"),
    "start": ({"pending", "assigned"}, "repairing"),
    "fix": ({"assigned", "repairing"}, "fixed"),
    "reject": ({"pending", "assigned"}, "rejected"),
    "reopen": ({"fixed", "rejected"}, "pending"),
}

# 必须填写处理说明的动作（维修/驳回/重开都要留痕）
_ORDER_NOTE_REQUIRED = {"fix", "reject", "reopen"}

# 列表排序权重：未闭环优先，其次紧急度，再次时间倒序
_ORDER_STATUS_RANK = {"pending": 0, "repairing": 1, "assigned": 2, "rejected": 3, "fixed": 4}
_ORDER_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2}

_OPEN_STATUSES = ("pending", "assigned", "repairing")


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _order_dict(o: EquipmentRepairOrder, timeline=None) -> dict:
    """工单 → 前端契约（含中文标签、挂单时长、可执行动作）"""
    now = datetime.now()
    age_h = round((now - o.created_at).total_seconds() / 3600, 1) if o.created_at else 0
    d = {
        "order_id": o.order_id,
        "equipment_id": o.equipment_id,
        "equipment_name": o.equipment_name,
        "category": o.category,
        "category_label": _ORDER_CATEGORY_LABELS.get(o.category, o.category or "—"),
        "priority": o.priority,
        "priority_label": _ORDER_PRIORITY_LABELS.get(o.priority, o.priority or "—"),
        "description": o.description,
        "reporter": o.reporter,
        "status": o.status,
        "status_label": _ORDER_STATUS_LABELS.get(o.status, o.status),
        "assignee": o.assignee,
        "handled_by": o.handled_by,
        "handled_at": _fmt(o.handled_at),
        "resolve_note": o.resolve_note,
        "action_taken": o.action_taken,
        "downtime_minutes": o.downtime_minutes,
        "created_at": _fmt(o.created_at),
        "updated_at": _fmt(o.updated_at),
        "closed_at": _fmt(o.closed_at),
        "age_hours": age_h,
        "is_open": o.status in _OPEN_STATUSES,
        "next_actions": _next_actions(o.status),
    }
    if timeline is not None:
        d["timeline"] = timeline
    return d


def _next_actions(status: str) -> list:
    """当前状态下可执行的流转（前端按钮由后端驱动，避免前后端状态机各写一套）"""
    out = []
    for act, (allowed, to_status) in _ORDER_TRANSITIONS.items():
        if status in allowed:
            out.append({
                "action": act,
                "to_status": to_status,
                "label": _ORDER_ACTION_LABELS[act],
                "need_note": act in _ORDER_NOTE_REQUIRED,
                "need_assignee": act == "assign",
            })
    return out


def _repair_action_dict(x: EquipmentRepairAction) -> dict:
    return {
        "id": x.id,
        "action": x.action,
        "action_label": _ORDER_ACTION_LABELS.get(x.action, x.action),
        "from_status": x.from_status,
        "from_status_label": _ORDER_STATUS_LABELS.get(x.from_status, x.from_status or ""),
        "to_status": x.to_status,
        "to_status_label": _ORDER_STATUS_LABELS.get(x.to_status, x.to_status or ""),
        "operator": x.operator,
        "note": x.note,
        "created_at": _fmt(x.created_at),
    }


def _sync_last_maintain(db: Session, equip_id: str, date_str: str) -> None:
    """维护/维修日期同步到台账 last_maintain（只前进，不后退）"""
    row = db.query(EquipmentAsset).filter(EquipmentAsset.id == equip_id).first()
    if row is None:
        return
    if not row.last_maintain or date_str >= row.last_maintain:
        row.last_maintain = date_str


@router.get("/api/equipment/{equip_id}/maintenance")
def list_maintenance(equip_id: str, db: Session = Depends(get_db)):
    """按设备查询维护记录（落库，重启不丢）"""
    base = _baseline(db, equip_id)
    if not base:
        raise HTTPException(404, f"设备不存在: {equip_id}")
    rows = (
        db.query(EquipmentMaintenanceRecord)
        .filter(EquipmentMaintenanceRecord.equipment_id == equip_id)
        .order_by(EquipmentMaintenanceRecord.date.desc(), EquipmentMaintenanceRecord.id.desc())
        .all()
    )
    records = [{
        "id": r.record_id,
        "db_id": r.id,
        "date": r.date,
        "type": r.type,
        "technician": r.technician,
        "content": r.content,
        "result": r.result,
        "source_order_id": r.source_order_id,
    } for r in rows]
    return {
        "equipment_id": equip_id,
        "name": base["name"],
        "records": records,
        "total": len(records),
    }


@router.post("/api/equipment/{equip_id}/maintenance")
def add_maintenance(equip_id: str, body: MaintenanceRecordIn, db: Session = Depends(get_db)):
    """新增维护记录（落库 + 同步台账 last_maintain）"""
    base = _baseline(db, equip_id)
    if not base:
        raise HTTPException(404, f"设备不存在: {equip_id}")
    rec = EquipmentMaintenanceRecord(
        record_id=f"M{uuid.uuid4().hex[:6].upper()}",
        equipment_id=equip_id,
        date=body.date,
        type=body.type,
        technician=body.technician,
        content=body.content,
        result=body.result,
        created_at=datetime.now(),
    )
    db.add(rec)
    _sync_last_maintain(db, equip_id, body.date)
    _write_log(db, f"新增维护记录 [{body.type}]", body.technician, equip_id, "info")
    db.commit()
    db.refresh(rec)
    return {
        "ok": True,
        "record": {
            "id": rec.record_id, "db_id": rec.id, "date": rec.date, "type": rec.type,
            "technician": rec.technician, "content": rec.content, "result": rec.result,
        },
    }


@router.post("/api/equipment/{equip_id}/repair")
def submit_repair(equip_id: str, body: RepairOrderIn, db: Session = Depends(get_db)):
    """提交报修工单 — 落库并生成第一条流转流水（工单从此可查、可流转）"""
    base = _baseline(db, equip_id)
    if not base:
        raise HTTPException(404, f"设备不存在: {equip_id}")
    now = datetime.now()
    order_id = f"WO-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
    order = EquipmentRepairOrder(
        order_id=order_id,
        equipment_id=equip_id,
        equipment_name=base["name"],
        category=body.category,
        priority=body.priority,
        description=body.description,
        reporter=body.reporter,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(order)
    db.add(EquipmentRepairAction(
        order_id=order_id, action="create", from_status=None, to_status="pending",
        operator=body.reporter, note=body.description, created_at=now,
    ))
    _write_log(
        db,
        f"提交报修工单 {order_id} [{_ORDER_CATEGORY_LABELS.get(body.category, body.category)}]",
        body.reporter, equip_id, "warning",
    )
    db.commit()
    db.refresh(order)
    return {"ok": True, "order": _order_dict(order)}


@router.get("/api/equipment/{equip_id}/repairs")
def list_repairs(equip_id: str, db: Session = Depends(get_db)):
    """查询某设备的报修工单（落库）"""
    base = _baseline(db, equip_id)
    if not base:
        raise HTTPException(404, f"设备不存在: {equip_id}")
    rows = (
        db.query(EquipmentRepairOrder)
        .filter(EquipmentRepairOrder.equipment_id == equip_id)
        .order_by(EquipmentRepairOrder.created_at.desc())
        .all()
    )
    return {
        "equipment_id": equip_id,
        "orders": [_order_dict(o) for o in rows],
        "total": len(rows),
    }


# ---------------------------------------------------------------------------
# 工单中心（全局）— 注意：必须挂在 /api/repairs 顶级前缀，
# 不能写成 /api/equipment/repairs，否则会被 /api/equipment/{equip_id} 抢先匹配
# ---------------------------------------------------------------------------

@router.get("/api/repairs")
def list_all_repairs(
    status: str = Query("all", description="all / open / pending / assigned / repairing / fixed / rejected"),
    equipment_id: Optional[str] = Query(None, description="按设备过滤"),
    keyword: Optional[str] = Query(None, description="按工单号/设备名/描述模糊搜索"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """工单中心列表 + 状态统计

    列表排序：未闭环优先 → 紧急度 → 时间倒序（避免已修复的老单占满首屏）。
    """
    q = db.query(EquipmentRepairOrder)
    if equipment_id:
        q = q.filter(EquipmentRepairOrder.equipment_id == equipment_id)
    if keyword:
        kw = f"%{keyword.strip()}%"
        q = q.filter(
            EquipmentRepairOrder.order_id.like(kw)
            | EquipmentRepairOrder.equipment_name.like(kw)
            | EquipmentRepairOrder.description.like(kw)
        )
    all_rows = q.all()

    # 统计口径固定为「全库」，与筛选无关（避免筛选后统计数字跳动）
    stats_rows = db.query(EquipmentRepairOrder).all()
    stats = {k: 0 for k in _ORDER_STATUS_LABELS}
    for o in stats_rows:
        stats[o.status] = stats.get(o.status, 0) + 1
    stats["total"] = len(stats_rows)
    stats["open"] = sum(stats.get(s, 0) for s in _OPEN_STATUSES)

    # 平均处理时长（已修复工单：建单 → 闭环）
    done = [o for o in stats_rows if o.status == "fixed" and o.created_at and o.closed_at]
    stats["avg_handle_hours"] = (
        round(sum((o.closed_at - o.created_at).total_seconds() for o in done) / len(done) / 3600, 1)
        if done else None
    )
    stats["done_sample"] = len(done)

    rows = all_rows
    if status == "open":
        rows = [o for o in rows if o.status in _OPEN_STATUSES]
    elif status not in ("all", ""):
        rows = [o for o in rows if o.status == status]

    rows.sort(key=lambda o: (
        _ORDER_STATUS_RANK.get(o.status, 9),
        _ORDER_PRIORITY_RANK.get(o.priority, 9),
        -(o.created_at.timestamp() if o.created_at else 0),
    ))

    return {
        "orders": [_order_dict(o) for o in rows[:limit]],
        "total": len(rows),
        "stats": stats,
        "status_labels": _ORDER_STATUS_LABELS,
    }


@router.get("/api/repairs/{order_id}")
def repair_detail(order_id: str, db: Session = Depends(get_db)):
    """工单详情：基本信息 + 流转时间线 + 由本单回写的维护记录"""
    o = db.query(EquipmentRepairOrder).filter(EquipmentRepairOrder.order_id == order_id).first()
    if not o:
        raise HTTPException(404, f"工单不存在: {order_id}")
    actions = (
        db.query(EquipmentRepairAction)
        .filter(EquipmentRepairAction.order_id == order_id)
        .order_by(EquipmentRepairAction.created_at.asc(), EquipmentRepairAction.id.asc())
        .all()
    )
    linked = (
        db.query(EquipmentMaintenanceRecord)
        .filter(EquipmentMaintenanceRecord.source_order_id == order_id)
        .all()
    )
    data = _order_dict(o, timeline=[_repair_action_dict(x) for x in actions])
    data["linked_maintenance"] = [{
        "id": r.record_id, "date": r.date, "type": r.type,
        "technician": r.technician, "content": r.content, "result": r.result,
    } for r in linked]
    return data


@router.post("/api/repairs/{order_id}/transition")
def transition_repair(order_id: str, body: RepairTransitionIn, db: Session = Depends(get_db)):
    """工单流转（闭环状态机）

    assign → assigned（必填维修负责人）
    start  → repairing
    fix    → fixed   ★ 自动回写一条「故障维修」维护记录并更新台账 last_maintain
    reject → rejected（必填驳回说明）
    reopen → pending （已修复/已驳回可重开）
    """
    o = db.query(EquipmentRepairOrder).filter(EquipmentRepairOrder.order_id == order_id).first()
    if not o:
        raise HTTPException(404, f"工单不存在: {order_id}")

    if body.action not in _ORDER_TRANSITIONS:
        raise HTTPException(400, f"无效的流转动作: {body.action}（可选：{'/'.join(_ORDER_TRANSITIONS)}）")

    cur_status = o.status
    allowed_from, to_status = _ORDER_TRANSITIONS[body.action]
    if cur_status not in allowed_from:
        raise HTTPException(
            409,
            f"当前状态「{_ORDER_STATUS_LABELS.get(cur_status, cur_status)}」不能执行"
            f"「{_ORDER_ACTION_LABELS[body.action]}」，允许的来源状态："
            + "、".join(_ORDER_STATUS_LABELS.get(s, s) for s in allowed_from),
        )

    note = (body.note or "").strip()
    if body.action in _ORDER_NOTE_REQUIRED and not note:
        raise HTTPException(400, f"执行「{_ORDER_ACTION_LABELS[body.action]}」必须填写处理说明")

    assignee = (body.assignee or "").strip()
    if body.action == "assign" and not assignee:
        raise HTTPException(400, "指派维修必须填写维修负责人")

    now = datetime.now()
    o.status = to_status
    o.handled_by = body.operator.strip()
    o.handled_at = now
    o.updated_at = now
    if assignee:
        o.assignee = assignee
    if note:
        o.resolve_note = note
    if body.action_taken is not None:
        o.action_taken = body.action_taken.strip() or None
    if body.downtime_minutes is not None:
        o.downtime_minutes = body.downtime_minutes

    log_type = "info"
    if to_status in ("fixed", "rejected"):
        o.closed_at = now
        if to_status == "fixed":
            # ★ 闭环回写：工单修完 → 自动生成维护记录 + 台账 last_maintain 前移
            rec = EquipmentMaintenanceRecord(
                record_id=f"M{uuid.uuid4().hex[:6].upper()}",
                equipment_id=o.equipment_id,
                date=now.strftime("%Y-%m-%d"),
                type="故障维修",
                technician=o.assignee or o.handled_by or body.operator.strip(),
                content=note or (o.description or ""),
                result="已修复",
                source_order_id=o.order_id,
                created_at=now,
            )
            db.add(rec)
            _sync_last_maintain(db, o.equipment_id, rec.date)
            log_type = "success"
        else:
            log_type = "warning"
    elif body.action == "reopen":
        o.closed_at = None

    db.add(EquipmentRepairAction(
        order_id=o.order_id,
        action=body.action,
        from_status=cur_status,
        to_status=to_status,
        operator=body.operator.strip(),
        note=note or None,
        created_at=now,
    ))
    _write_log(
        db,
        f"工单 {o.order_id} {_ORDER_ACTION_LABELS[body.action]}"
        f"（{_ORDER_STATUS_LABELS.get(cur_status, cur_status)} → "
        f"{_ORDER_STATUS_LABELS.get(to_status, to_status)}）",
        body.operator.strip(), o.equipment_id, log_type,
    )
    db.commit()
    db.refresh(o)
    return {"ok": True, "order": _order_dict(o)}


@router.post("/api/equipment/{equip_id}/control")
def control_equipment(equip_id: str, body: ControlActionIn, db: Session = Depends(get_db)):
    """设备启停控制（落库到台账 control_state，刷新/重启都不还原）"""
    base = _baseline(db, equip_id)
    if not base:
        raise HTTPException(404, f"设备不存在: {equip_id}")
    if body.action not in ("start", "stop"):
        raise HTTPException(400, "action 必须是 start 或 stop")

    new_state = "stopped" if body.action == "stop" else "running"
    row = db.query(EquipmentAsset).filter(EquipmentAsset.id == equip_id).first()
    if row is not None:
        row.control_state = new_state
        # ⚠️ 必须用 utcnow：它是 _stop_epoch() 里温度冷却曲线的起点。
        #    写成 datetime.now()（本地）会和 onupdate=datetime.utcnow 的其它写入混基准，差 8 小时。
        row.updated_at = datetime.utcnow()

    action_text = "停机" if body.action == "stop" else "启动"
    entry = _write_log(
        db, f"{action_text} {base['name']}", body.operator, equip_id,
        "warning" if body.action == "stop" else "success",
    )
    db.commit()
    db.refresh(entry)
    return {
        "ok": True,
        "equipment_id": equip_id,
        "new_status": new_state,
        "log": _log_dict(entry),
    }


@router.get("/api/equipment/{equip_id}/logs")
def list_operation_logs(equip_id: str, limit: int = Query(50, ge=1, le=200),
                        db: Session = Depends(get_db)):
    """查询操作日志（equip_id='all' 返回全部；已落库，前端刷新不再丢）"""
    q = db.query(EquipmentOperationLog)
    if equip_id and equip_id != "all":
        if not _baseline(db, equip_id):
            raise HTTPException(404, f"设备不存在: {equip_id}")
        q = q.filter(EquipmentOperationLog.equipment_id == equip_id)
    total = q.count()
    rows = (
        q.order_by(EquipmentOperationLog.created_at.desc(), EquipmentOperationLog.id.desc())
        .limit(limit).all()
    )
    return {"logs": [_log_dict(x) for x in rows], "total": total}

