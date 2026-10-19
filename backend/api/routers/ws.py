"""WebSocket 实时推送 —— 告警 / 巡检 / 设备 / 水质

★★ 2026-10-19 重构（老板：告警要有实时性，也要能接入其他软件）

   旧版有三个问题，合起来是"看起来实时，其实全是假的"：
     1. `_push_alarms_loop()` 每 15~30 秒**凭空造**一条告警（「人员闯入危险区」），
        **不写库**。前端 ElNotification 一弹，用户点进告警页根本找不到这条记录。
     2. 真告警（analysis.py 写 AlarmRecord）**没有任何广播**——
        `push_alarm_event` / `push_alarm_sync` 定义了却从未被调用过（死代码）。
     3. 告警页靠 30 秒轮询，所以所谓"实时"实际是"最多滞后半分钟"。

   新版：
     · 事件格式统一 `{event, ts, data}`，event 见 EVENTS
     · **假推送循环删除**。演示要动态告警，调 `POST /api/alarms/ingest`
       —— 那条会真写库，点进去查得到，事后也能删干净
     · 真告警由 `analysis.py` / `ingest` 调 `publish_alarm()` → 广播 + 派发 Webhook
     · 只保留 water 的快照循环（它读的是真库）。equipment / safety 端点仍然可用，
       但不再自己编数据——没有真数据源就不该造

★ 事件契约（对外也按这个格式，其他软件接入时直接照抄）：
     { "event": "alarm.created", "ts": "2026-10-19T16:20:00.123+08:00",
       "data": { "id": 1234, "scene_id": "flotation", "level": "danger",
                 "rule_name": "...", "message": "...", "source": "internal",
                 "origin": "SuiEye", "created_at": "..." } }
"""

import asyncio
import os
from datetime import datetime
from typing import Callable, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.config import settings
from backend.models.database import SessionLocal

router = APIRouter()

CHANNELS = ("alarms", "safety", "equipment", "water")

# 活跃连接管理
_active_conns: dict[str, Set[WebSocket]] = {c: set() for c in CHANNELS}

# 事件名登记表：写在这里是为了让"有哪些事件"只有一处真源，
# 前端 / Webhook 订阅 / 文档都从这里对。
EVENTS = {
    "connected": "连接成功",
    "alarm.created": "新告警产生",
    "alarm.updated": "告警状态变更（确认 / 处置 / 误报 / 静默）",
    "snapshot": "周期性快照（水质等）",
}

# 主事件循环引用：同步端点（FastAPI 的 def 路由跑在线程池）要靠它投递事件
_main_loop: asyncio.AbstractEventLoop | None = None


def _now_iso() -> str:
    """本地时区 ISO 时间（带毫秒）。

    ★ 用本地时区而不是 UTC：门面/告警页的"刚刚""2 分钟前"都按本地时间算，
      前后端再各自转一次时区，演示时最容易出现"显示 8 小时前"的假故障。
    """
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


# ============================================================
# 广播与事件派发
# ============================================================
async def _broadcast(channel: str, message: dict):
    """向某频道所有连接广播消息（死连接顺手清掉）"""
    dead = []
    for ws in list(_active_conns.get(channel, set())):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _active_conns[channel].discard(ws)


async def emit(event: str, data: dict, *, channel: str = "alarms", webhook: bool = True) -> dict:
    """统一出站入口：先广播给 WS 客户端，再派发 Webhook。

    ★ Webhook 必须 `create_task` 分离：对方可能是钉钉/企业微信这种 1~2 秒才回的
      慢接口，同步等它会把分析请求和 WS 广播一起拖死。
    """
    msg = {"event": event, "ts": _now_iso(), "data": data}
    await _broadcast(channel, msg)

    if webhook and event.startswith("alarm."):
        try:
            from backend.api.routers.webhooks import dispatch_event
            asyncio.create_task(dispatch_event(event, data))
        except Exception as e:  # 派发失败不能影响主流程
            print(f"[ws] webhook dispatch skipped: {e}")
    return msg


async def publish_alarm(alarm: dict):
    """新告警 → 广播 + 出站。alarm 必须是**已入库**的告警字典（带 id）。"""
    return await emit("alarm.created", alarm)


async def publish_alarm_update(alarm: dict):
    """告警状态变更（确认/处置/误报/静默）→ 让其他窗口同步。"""
    return await emit("alarm.updated", alarm)


def publish_sync(event: str, data: dict, *, channel: str = "alarms") -> bool:
    """从**同步上下文**投递事件（FastAPI 的 `def` 路由跑在线程池，不能直接 await）。

    ★ 这是处置接口（`def handle_alarm`）能广播状态变更的关键。
      没有它，A 窗口处置完，B 窗口要等 30 秒轮询才知道。
    """
    if _main_loop is None or _main_loop.is_closed():
        return False
    try:
        asyncio.run_coroutine_threadsafe(emit(event, data, channel=channel), _main_loop)
        return True
    except Exception as e:
        print(f"[ws] publish_sync failed: {e}")
        return False


# 兼容旧调用名（历史代码里可能还有引用，别让它 ImportError）
async def push_alarm_event(alarm_data: dict):
    return await publish_alarm(alarm_data)


def push_alarm_sync(alarm_data: dict):
    return publish_sync("alarm.created", alarm_data)


# ============================================================
# WebSocket 端点
# ============================================================
async def _serve(websocket: WebSocket, channel: str, token: str | None):
    """统一的连接生命周期。

    ★ 鉴权是可选的：`WS_TOKEN` 配了才校验。演示版默认不配（前端不用改 URL），
      要讲安全性时设置里填上 token，外部系统连接必须带 `?token=`。
    """
    required = (getattr(settings, "WS_TOKEN", "") or "").strip()
    if required and token != required:
        # 未 accept 就 close，客户端会收到 4401 而不是静默挂住
        await websocket.close(code=4401)
        print(f"[ws] {channel} 拒绝连接：token 不匹配")
        return

    await websocket.accept()
    _active_conns[channel].add(websocket)
    try:
        await websocket.send_json({
            "event": "connected",
            "ts": _now_iso(),
            "data": {"channel": channel},
        })
        while True:
            # 客户端心跳：收到任何文本都当存活，不解析内容
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[ws] {channel} 异常断开: {e}")
    finally:
        _active_conns[channel].discard(websocket)


@router.websocket("/ws/alarms")
async def ws_alarms(websocket: WebSocket, token: str | None = Query(default=None)):
    """告警实时推送 —— 新告警 / 状态变更"""
    await _serve(websocket, "alarms", token)


@router.websocket("/ws/safety")
async def ws_safety(websocket: WebSocket, token: str | None = Query(default=None)):
    """安全巡检结果实时推送"""
    await _serve(websocket, "safety", token)


@router.websocket("/ws/equipment")
async def ws_equipment(websocket: WebSocket, token: str | None = Query(default=None)):
    """设备状态实时推送"""
    await _serve(websocket, "equipment", token)


@router.websocket("/ws/water")
async def ws_water(websocket: WebSocket, token: str | None = Query(default=None)):
    """水质实时推送 —— 每 10 秒推送 5 点位快照"""
    await _serve(websocket, "water", token)


def channel_stats() -> dict:
    """当前各频道在线连接数（设置页 / 健康检查用）"""
    return {c: len(s) for c, s in _active_conns.items()}


# ============================================================
# 后台推送任务
# ============================================================
async def _push_water_loop():
    """每 10 秒推送一份水质快照（5 点位状态 + 回用评分）。

    ★ 这是唯一保留下来的快照循环：它读的是**真实库表**，不是随机数。
    """
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
                "event": "snapshot",
                "ts": _now_iso(),
                "data": {"stations": stations},
            })
        except Exception as e:
            print(f"[ws] water push error: {e}")
        await asyncio.sleep(10)


# ============================================================
# 启动
# ============================================================
_loop_started = False


def start_ws_background_tasks():
    """在 FastAPI startup 中调用：捕获事件循环 + 启动快照推送。"""
    global _loop_started, _main_loop
    _main_loop = asyncio.get_running_loop()
    if _loop_started:
        return
    _loop_started = True
    asyncio.create_task(_push_water_loop())
    demo = os.getenv("SUIEYE_DEMO_FEED", "0") in ("1", "true", "True")
    if demo:
        # 彩排用：没有真实数据源时也能看到推送效果。
        # ★ 刻意不默认开启——凭空造告警会让"告警页里查不到"变成演示事故。
        print("[ws] ⚠️ SUIYE_DEMO_FEED=1：将推送标记为 demo 的模拟告警")
        asyncio.create_task(_demo_alarm_loop())
    print(f"[ws] 快照任务已启动｜WS_TOKEN={'已启用' if (settings.WS_TOKEN or '') else '未启用'}")


async def _demo_alarm_loop():
    """仅彩排用：推 demo=true 的事件，前端会加「演示」标记，不会冒充真实告警。"""
    import random
    samples = [
        ("人员闯入危险区", "danger", "检测到人员进入危险区域"),
        ("皮带严重跑偏", "danger", "皮带跑偏超过 50px"),
        ("泡沫品位异常", "warning", "铅品位低于预测下限，建议检查加药量"),
    ]
    await asyncio.sleep(8)
    while True:
        try:
            name, level, msg = random.choice(samples)
            await emit("alarm.created", {
                "id": None,
                "scene_id": "flotation",
                "level": level,
                "rule_name": name,
                "message": msg,
                "source": "demo",
                "origin": "演示源",
                "demo": True,
                "created_at": _now_iso(),
            })
        except Exception as e:
            print(f"[ws] demo push error: {e}")
        await asyncio.sleep(random.uniform(15, 30))
