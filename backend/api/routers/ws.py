"""WebSocket 实时推送 — 告警 / 巡检 / 设备状态

演示用：后端定时推送模拟事件，前端订阅后实时更新。
生产环境应替换为真实事件源（数据库触发器 / 消息队列）。
"""

import asyncio
import json
import random
import time
from datetime import datetime, timedelta
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from backend.models.database import (
    AlarmRecord,
    AnalysisRecord,
    HazardTicket,
    SessionLocal,
)

router = APIRouter()

# 活跃连接管理
_active_conns: dict[str, Set[WebSocket]] = {"alarms": set(), "safety": set(), "equipment": set(), "water": set()}


async def _broadcast(channel: str, message: dict):
    """向某频道所有连接广播消息"""
    dead = []
    for ws in _active_conns.get(channel, set()):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _active_conns[channel].discard(ws)


# ============================================================
# WebSocket 端点
# ============================================================
@router.websocket("/ws/alarms")
async def ws_alarms(websocket: WebSocket):
    """告警实时推送 — 每次新告警立即推送"""
    await websocket.accept()
    _active_conns["alarms"].add(websocket)
    try:
        await websocket.send_json({"type": "connected", "channel": "alarms", "ts": datetime.utcnow().isoformat()})
        while True:
            await websocket.receive_text()  # 心跳
    except WebSocketDisconnect:
        _active_conns["alarms"].discard(websocket)


@router.websocket("/ws/safety")
async def ws_safety(websocket: WebSocket):
    """安全巡检结果实时推送"""
    await websocket.accept()
    _active_conns["safety"].add(websocket)
    try:
        await websocket.send_json({"type": "connected", "channel": "safety", "ts": datetime.utcnow().isoformat()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _active_conns["safety"].discard(websocket)


@router.websocket("/ws/equipment")
async def ws_equipment(websocket: WebSocket):
    """设备状态实时推送 — 每 5 秒推送一次快照"""
    await websocket.accept()
    _active_conns["equipment"].add(websocket)
    try:
        await websocket.send_json({"type": "connected", "channel": "equipment", "ts": datetime.utcnow().isoformat()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _active_conns["equipment"].discard(websocket)


@router.websocket("/ws/water")
async def ws_water(websocket: WebSocket):
    """水质实时推送 — 每 10 秒推送 5 点位快照"""
    await websocket.accept()
    _active_conns["water"].add(websocket)
    try:
        await websocket.send_json({"type": "connected", "channel": "water", "ts": datetime.utcnow().isoformat()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _active_conns["water"].discard(websocket)


# ============================================================
# 后台推送任务（演示用定时器）
# ============================================================
_alarm_messages = [
    ("人员闯入危险区", "danger", "检测到人员进入危险区域，立即处理"),
    ("皮带严重跑偏", "danger", "皮带跑偏超过50px，可能引发事故"),
    ("泡沫品位异常", "warning", "铅品位低于预测下限，建议检查加药量"),
    ("PPE不合规", "warning", "检测到人员未佩戴安全帽"),
    ("异物检出", "warning", "皮带表面检出异物块"),
    ("设备温度预警", "warning", "2#浮选槽温度偏高"),
]
_cameras = ["CAM-01", "CAM-02", "CAM-03", "CAM-04"]


async def _push_alarms_loop():
    """每 15-30 秒推送一条模拟告警"""
    await asyncio.sleep(8)  # 启动后等 8 秒
    while True:
        try:
            msg = random.choice(_alarm_messages)
            alarm = {
                "type": "alarm",
                "level": msg[1],
                "rule_name": msg[0],
                "message": msg[2],
                "camera_id": random.choice(_cameras),
                "ts": datetime.utcnow().isoformat(),
            }
            await _broadcast("alarms", alarm)
        except Exception as e:
            print(f"[ws] alarms push error: {e}")
        await asyncio.sleep(random.uniform(15, 30))


async def _push_safety_loop():
    """每 20-40 秒推送一条巡检结果"""
    await asyncio.sleep(12)
    while True:
        try:
            levels = ["low", "low", "low", "medium", "medium", "high", "critical"]
            level = random.choice(levels)
            person = 1 if level in ("high", "critical") and random.random() < 0.7 else 0
            item = {
                "type": "inspection",
                "camera_id": random.choice(_cameras),
                "risk_level": level,
                "person_count": person,
                "belt_offset_px": round(random.uniform(0, 55), 1) if level == "high" else 0,
                "foreign_object_count": 1 if level == "critical" else 0,
                "ts": datetime.utcnow().isoformat(),
            }
            await _broadcast("safety", item)
        except Exception as e:
            print(f"[ws] safety push error: {e}")
        await asyncio.sleep(random.uniform(20, 40))


async def _push_equipment_loop():
    """每 5 秒推送一份设备状态快照"""
    await asyncio.sleep(5)
    while True:
        try:
            from backend.api.routers.equipment import _simulate_equipment, _EQUIPMENT_BASELINE
            import time as _time
            seed = int(_time.time() // 5)
            snapshot = []
            for eid in _EQUIPMENT_BASELINE:
                eq = _simulate_equipment(eid, seed=seed + hash(eid) % 10000)
                if eq:
                    snapshot.append({
                        "id": eid,
                        "name": eq["name"],
                        "status": eq["status"],
                        "health": eq["health"],
                        "temperature": eq["metrics"].get("temperature"),
                        "vibration": eq["metrics"].get("vibration"),
                        "power": eq["metrics"].get("power"),
                    })
            await _broadcast("equipment", {"type": "snapshot", "equipment": snapshot, "ts": datetime.utcnow().isoformat()})
        except Exception as e:
            print(f"[ws] equipment push error: {e}")
        await asyncio.sleep(5)


async def _push_water_loop():
    """每 10 秒推送一份水质快照（5 点位状态 + 回用评分）"""
    await asyncio.sleep(10)
    while True:
        try:
            from backend.api.routers.water import (
                WATER_STATIONS, _latest_reading, _reading_dict, _reuse_index,
            )
            db = SessionLocal()
            try:
                stations = []
                for st in WATER_STATIONS:
                    r = _latest_reading(db, st["id"])
                    if not r:
                        continue
                    rd = _reading_dict(r)
                    lv = "normal"
                    for _p, d in rd["params"].items():
                        if d["level"] == "danger":
                            lv = "danger"
                            break
                        if d["level"] == "warning" and lv == "normal":
                            lv = "warning"
                    stations.append({
                        "id": st["id"],
                        "name": st["name"],
                        "status": lv,
                        "reuse_score": _reuse_index(r)["score"],
                        "ss": r.ss,
                        "cod": r.cod,
                        "ph": r.ph,
                        "cu": r.cu,
                        "flow": r.flow,
                    })
            finally:
                db.close()
            await _broadcast("water", {
                "type": "snapshot",
                "stations": stations,
                "ts": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            print(f"[ws] water push error: {e}")
        await asyncio.sleep(10)


# ============================================================
# 外部调用接口（供其他模块触发推送）
# ============================================================
async def push_alarm_event(alarm_data: dict):
    """其他模块新增告警时调用此函数推送到 WS 客户端"""
    await _broadcast("alarms", {"type": "alarm", **alarm_data})


def push_alarm_sync(alarm_data: dict):
    """同步版本（非 async 上下文使用）"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_broadcast("alarms", {"type": "alarm", **alarm_data}))
        else:
            loop.run_until_complete(_broadcast("alarms", {"type": "alarm", **alarm_data}))
    except RuntimeError:
        # 没有事件循环，跳过（WS 客户端会等下一次定时推送）
        pass


# ============================================================
# 启动时注册后台任务
# ============================================================
_loop_started = False

def start_ws_background_tasks():
    """在 FastAPI async startup 中调用，启动后台推送协程"""
    global _loop_started
    if _loop_started:
        return
    _loop_started = True
    asyncio.create_task(_push_alarms_loop())
    asyncio.create_task(_push_safety_loop())
    asyncio.create_task(_push_equipment_loop())
    asyncio.create_task(_push_water_loop())
    print("[ws] 后台推送任务已启动")