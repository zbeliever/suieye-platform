"""告警中心 API — 列表 / 帧级分组 / 详情（证据链）/ 处置闭环 + 报表聚合 + 图像服务

v2（2026-10-13）新增，对应 docs/alarm-center-plan.md 的 P0：
  - GET  /api/alarms/groups            帧级分组列表（同源聚合，消除同帧多规则冗余）
  - GET  /api/alarms/{alarm_id}        告警详情（结构化证据 + 规则命中 + 同组 + 处置流水）
  - POST /api/alarms/{alarm_id}/handle 处置流转（处理意见必填、写审计流水）
  - PUT  /api/alarms/{alarm_id}/acknowledge 保留（兼容旧前端，内部走 handle）

注意：/api/alarms/groups 必须注册在 /api/alarms/{alarm_id} **之前**，
否则 "groups" 会被当作 alarm_id 做 int 解析 → 422。
"""

import json
import os
import os.path
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.algorithms.feature_baseline import evaluate_features, looks_like_raw_features
from backend.algorithms.root_cause import analyze as analyze_root_cause
from backend.config import settings
from backend.models.database import (
    AlarmAction,
    AlarmRecord,
    AnalysisRecord,
    get_db,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# 字典与常量
# ---------------------------------------------------------------------------

_STATUS_LABELS = {
    "new": "待处理",
    "acknowledged": "已确认",
    "in_progress": "处理中",
    "closed": "已闭环",
    "false_positive": "误报",
}

# 动作中文名 —— 状态机报错里必须说「动作」（如"开始处理"），不能说「目标状态」（"处理中"），
# 否则会出现"当前状态「处理中」不能执行「处理中」"这种同词重复（批量处置的失败原因会直接透出）
_ACTION_LABELS = {
    "acknowledge": "确认收到",
    "start": "开始处理",
    "close": "闭环",
    "false_positive": "标记误报",
    "reopen": "重新打开",
}

_LEVEL_LABELS = {"warning": "预警", "danger": "严重"}

# 处置动作 → (允许的来源状态集合, 目标状态)
_TRANSITIONS = {
    "acknowledge": ({"new"}, "acknowledged"),
    "start": ({"new", "acknowledged"}, "in_progress"),
    "close": ({"new", "acknowledged", "in_progress"}, "closed"),
    "false_positive": ({"new", "acknowledged", "in_progress"}, "false_positive"),
    "reopen": ({"closed", "false_positive"}, "new"),
}

# 必须填写处理意见的动作（ISA-18.2：报警合理化要求记录原因/动作）
_NOTE_REQUIRED = {"start", "close", "false_positive"}

# 组状态汇总优先级（越靠前越"未处理完"）
_GROUP_STATUS_ORDER = ["new", "in_progress", "acknowledged", "closed"]

# 22 维泡沫特征中文名（flotation 场景）
_FEATURE_LABELS = {
    "bubble_avg_size": "气泡平均尺寸",
    "bubble_size_std": "气泡尺寸标准差",
    "bubble_size_skew": "气泡尺寸偏度",
    "bubble_size_kurtosis": "气泡尺寸峰度",
    "bubble_shape_roundness": "气泡圆形度",
    "bubble_shape_dispersion": "气泡形状离散度",
    "n_foams": "泡沫数量",
    "n_valid_bubbles": "有效气泡数",
    "texture_contrast": "纹理对比度",
    "texture_correlation": "纹理相关性",
    "texture_entropy": "纹理熵",
    "texture_uniformity": "纹理均匀度",
    "texture_energy": "纹理能量",
    "texture_homogeneity": "纹理同质性",
    "texture_variance": "纹理方差",
    "r_mean": "红通道均值",
    "g_mean": "绿通道均值",
    "b_mean": "蓝通道均值",
    "gray_mean": "灰度均值",
    "relative_red": "相对红色分量",
    "relative_green": "相对绿色分量",
    "relative_blue": "相对蓝色分量",
}

_FIELD_LABELS = {
    "predicted.pb_grade_xrf": "铅品位",
    "predicted.pb_grade": "铅品位",
    "predicted.zn_grade_xrf": "锌品位",
    "predicted.p80": "P80 粒度",
    "features.overgrinding_ratio": "过磨率",
    "metrics.overgrinding_ratio": "过磨率",
    "features.persons_in_danger_zone": "危险区人数",
    "features.belt_offset_px": "皮带偏移像素",
}

_OP_LABELS = {"<": "低于", "<=": "不超过", ">": "高于", ">=": "不低于", "==": "等于", "!=": "不等于"}

_FIELD_ALIASES = {
    "pb_grade": "pb_grade_xrf",
    "pb_grade_xrf": "pb_grade",
    "zn_grade": "zn_grade_xrf",
    "zn_grade_xrf": "zn_grade",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _load_evidence(raw) -> dict:
    """evidence 可能是 dict（SQLAlchemy JSON）或 str（裸 sqlite 写入），统一成 dict"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


_SCENE_LABELS = {
    "flotation": "浮选",
    "grinding": "磨矿",
    "safety": "安全巡检",
    "water": "脱水",
}

_LEVEL_RANK = {"danger": 0, "warning": 1}


def _collapse_by_frame(rows: list) -> list:
    """同帧聚合：同一帧（group_key）只保留一条代表，消除"一张图触发多条规则"的冗余。

    代表选取规则：级别高者优先（danger > warning），同级保留先出现的（rows 已按时间降序）。
    无 group_key 的单条各自独立成组。
    """
    best = {}
    order = []
    for a in rows:
        k = a.group_key or f"solo:{a.id}"
        cur = best.get(k)
        if cur is None:
            best[k] = a
            order.append(k)
        elif _LEVEL_RANK.get(a.level, 9) < _LEVEL_RANK.get(cur.level, 9):
            best[k] = a
    return [best[k] for k in order]


def _brief(a: AlarmRecord) -> dict:
    """列表卡片用的轻量提炼：场景中文名 + 关键读数 + 点位。

    全部从 evidence 里现取，取不到就留空——不编造、不用默认值顶替。
    """
    out = {
        "scene_label": _SCENE_LABELS.get(a.scene_id, a.scene_id),
        "metric": None,
        "location": None,
    }
    ev = _load_evidence(a.evidence)
    if not ev:
        return out
    pred = ev.get("prediction") or {}
    try:
        if a.scene_id == "flotation":
            v = pred.get("pb_grade_xrf")
            if v is not None:
                out["metric"] = f"Pb {float(v):.2f}%"
        elif a.scene_id == "grinding":
            v = pred.get("p80")
            if v is not None:
                out["metric"] = f"P80 {float(v):.0f}μm"
        elif a.scene_id == "safety":
            v = pred.get("risk_score")
            if v is not None:
                out["metric"] = f"风险 {float(v):.0f}"
            cam = ev.get("camera_id")
            if cam:
                out["location"] = str(cam)
        elif a.scene_id == "water":
            v = pred.get("reuse_score")
            if v is not None:
                out["metric"] = f"回用 {float(v):.0f}分"
    except (TypeError, ValueError):
        pass
    return out


def _alarm_dict(a: AlarmRecord) -> dict:
    status = a.status or ("acknowledged" if a.is_acknowledged else "new")
    return {
        "id": a.id,
        "scene_id": a.scene_id,
        "level": a.level,
        "level_label": _LEVEL_LABELS.get(a.level, a.level),
        "rule_name": a.rule_name,
        "brief": _brief(a),
        "message": a.message,
        "evidence": a.evidence,
        "is_acknowledged": bool(a.is_acknowledged),
        "acknowledged_by": a.acknowledged_by,
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        # ---- v2 处置闭环 ----
        "status": status,
        "status_label": _STATUS_LABELS.get(status, status),
        "group_key": a.group_key,
        # ★ 来源必须一路带到接口层：前端要显示"这条是 DCS 推的"，
        #   否则接入外部系统后，界面上所有告警长得一样，出了问题说不清是谁报的。
        "source": a.source or "internal",
        "origin": a.origin or "SuiEye",
        "handled_by": a.handled_by,
        "handled_at": a.handled_at.isoformat() if a.handled_at else None,
        "handled_note": a.handled_note,
        "action_taken": a.action_taken,
        "false_positive": bool(a.false_positive),
        "silenced_until": a.silenced_until.isoformat() if a.silenced_until else None,
    }


def _ws_payload(a: AlarmRecord) -> dict:
    """WebSocket / Webhook 出站用的告警载荷（2026-10-19 新增）。

    ★ 刻意比 `_alarm_dict` 轻：推送要表达的是"来了一条新告警"，
      不是"把详情整包搬过去"。evidence 里的图像路径对外部系统没有意义，
      接收方要详情按 id 拉 GET /api/alarms/{id} 即可。
    ★ 但 source / origin 必须在：对方要能判断这条是 SuiEye 算的还是别家推的。
    """
    b = _brief(a)
    status = a.status or ("acknowledged" if a.is_acknowledged else "new")
    return {
        "id": a.id,
        "scene_id": a.scene_id,
        "scene_label": b.get("scene_label"),
        "level": a.level,
        "level_label": _LEVEL_LABELS.get(a.level, a.level),
        "rule_name": a.rule_name,
        "message": a.message,
        "metric": b.get("metric"),
        "location": b.get("location"),
        "status": status,
        "status_label": _STATUS_LABELS.get(status, status),
        "source": a.source or "internal",
        "origin": a.origin or "SuiEye",
        "group_key": a.group_key,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _action_dict(x: AlarmAction) -> dict:
    return {
        "id": x.id,
        "action": x.action,
        "action_label": _STATUS_LABELS.get(x.to_status, x.action),
        "from_status": x.from_status,
        "to_status": x.to_status,
        "operator": x.operator,
        "note": x.note,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    }


def _match_rule(scene_id: str, rule_name: str) -> Optional[dict]:
    """从场景包 YAML 里找同名规则，用于展示"命中详情"（字段/比较符/阈值）"""
    if not scene_id or not rule_name:
        return None
    try:
        from backend.scenes.runtime import load_scene_config
        cfg = load_scene_config(scene_id) or {}
    except Exception:
        return None
    for r in cfg.get("alarm_rules") or []:
        if r.get("name") == rule_name:
            return r
    return None


def _field_value(evidence: dict, dotted: Optional[str]):
    """按点路径取证据里的实测值：predicted.pb_grade_xrf → evidence['prediction']['pb_grade_xrf']"""
    parts = (dotted or "").split(".")
    if len(parts) != 2:
        return None
    section, key = parts
    bag = {
        "predicted": evidence.get("prediction") or {},
        "features": evidence.get("features") or {},
        "metrics": evidence.get("metrics") or {},
    }.get(section) or {}
    val = bag.get(key)
    if val is None:
        alias = _FIELD_ALIASES.get(key)
        if alias:
            val = bag.get(alias)
    return val if isinstance(val, (int, float)) and not isinstance(val, bool) else None


def _group_status(statuses) -> str:
    uniq = set(statuses)
    if uniq and uniq <= {"false_positive", "closed"}:
        return "false_positive" if uniq == {"false_positive"} else "closed"
    for s in _GROUP_STATUS_ORDER:
        if s in uniq:
            return s
    return "new"


def _build_stats(db: Session) -> dict:
    q = db.query
    total = q(AlarmRecord).count()

    by_status = dict(
        q(AlarmRecord.status, func.count(AlarmRecord.id)).group_by(AlarmRecord.status).all()
    )
    # 历史数据兜底：status 为空的行按 is_acknowledged 归一
    blank = by_status.pop(None, 0) + by_status.pop("", 0)
    if blank:
        ack_legacy = q(AlarmRecord).filter(
            AlarmRecord.status.is_(None) | (AlarmRecord.status == ""),
            AlarmRecord.is_acknowledged == True,  # noqa: E712
        ).count()
        by_status["acknowledged"] = by_status.get("acknowledged", 0) + ack_legacy
        by_status["new"] = by_status.get("new", 0) + (blank - ack_legacy)

    pending = total - by_status.get("closed", 0) - by_status.get("false_positive", 0)

    # 待处理告警（status=new，即尚未有人接手）的级别构成
    # ⚠️ 注意别和 total 口径的 warning/danger 混用——那是全量，这是待处理，两者数字不等
    todo_danger = q(AlarmRecord).filter(
        AlarmRecord.status == "new", AlarmRecord.level == "danger"
    ).count()
    todo_warning = q(AlarmRecord).filter(
        AlarmRecord.status == "new", AlarmRecord.level == "warning"
    ).count()

    # 响应时长分布：均值会被极端值拉偏（个别告警跨天未处置），
    # 因此同时给出中位数与 P90——这也是 ISA-18.2 / EEMUA 191 建议的看分布而非只看均值
    durs = sorted(
        v for (v,) in q(
            (func.julianday(AlarmRecord.handled_at) - func.julianday(AlarmRecord.created_at)) * 1440
        ).filter(AlarmRecord.handled_at.isnot(None)).all() if v is not None
    )

    def _pctile(frac: float):
        if not durs:
            return None
        return round(durs[min(int(len(durs) * frac), len(durs) - 1)], 1)

    # 同源冗余：按 group_key 聚合；未分组的单条各自算一个事件
    g = q(AlarmRecord.group_key, func.count(AlarmRecord.id)).filter(
        AlarmRecord.group_key.isnot(None)
    ).group_by(AlarmRecord.group_key).all()
    n_grouped = len(g)
    redundant = sum(c - 1 for _, c in g if c > 1)
    n_ungrouped = q(AlarmRecord).filter(AlarmRecord.group_key.is_(None)).count()
    events = n_grouped + n_ungrouped

    return {
        "total": total,
        "unacknowledged": q(AlarmRecord).filter(
            AlarmRecord.is_acknowledged == False  # noqa: E712
        ).count(),
        "warning": q(AlarmRecord).filter(AlarmRecord.level == "warning").count(),
        "danger": q(AlarmRecord).filter(AlarmRecord.level == "danger").count(),
        # ---- v2 ----
        "new": by_status.get("new", 0),
        "acknowledged": by_status.get("acknowledged", 0),
        "in_progress": by_status.get("in_progress", 0),
        "closed": by_status.get("closed", 0),
        "false_positive": by_status.get("false_positive", 0),
        "pending": pending,
        # 待处理口径的级别构成（todo_danger + todo_warning == new）
        "todo_danger": todo_danger,
        "todo_warning": todo_warning,
        "events": events,
        "mtta_minutes": round(sum(durs) / len(durs), 1) if durs else None,
        "mtta_median_minutes": _pctile(0.5),
        "mtta_p90_minutes": _pctile(0.9),
        "mtta_samples": len(durs),
        "redundancy": {
            "groups": n_grouped,
            "redundant": redundant,
            "ratio": round(redundant / total, 3) if total else 0,
        },
    }


def _parse_status_filter(status: Optional[str]):
    if not status:
        return None
    items = [s.strip() for s in status.split(",") if s.strip()]
    for s in items:
        if s not in _STATUS_LABELS:
            raise HTTPException(400, f"无效的处置状态: {s}")
    return items or None


# ---------------------------------------------------------------------------
# 入站：外部系统把告警推进来（2026-10-19 新增）
# ---------------------------------------------------------------------------

class AlarmIngestIn(BaseModel):
    """外部系统推送的告警。字段刻意保持少而明确，对接方不用猜。"""
    scene_id: str = "flotation"
    level: str                                    # warning / danger
    rule_name: str
    message: str = ""
    origin: str = "外部系统"                       # DCS / MES / 视频分析盒子 …
    external_id: Optional[str] = None             # 对方系统内的唯一 id，用于重推去重
    group_key: Optional[str] = None
    created_at: Optional[str] = None              # ISO 时间；不传则用服务端收到的时间
    evidence: Optional[dict] = None


@router.post("/api/alarms/ingest")
async def ingest_alarm(
    body: AlarmIngestIn,
    x_api_key: str = Header(default="", alias="X-API-Key"),
    db: Session = Depends(get_db),
):
    """**外部系统 → SuiEye**：把别家的告警推进来。

    ★ 这是"接入其他软件"的入站口。写库 + 立即广播，
      告警页 / 门面在 1 秒内就能看到，不用等 30 秒轮询。
    ★ 必须带 X-API-Key（config.ALARM_INGEST_KEY）：否则任何人都能往告警库里塞数据，
      而告警库恰好是演示时最容易被质疑"是不是真的"的地方。
    ★ 必须注册在 `/api/alarms/{alarm_id}` 之前 —— 虽然 GET/POST 方法不同不会真撞上，
      但保持"固定路径在前"这个习惯，免得以后有人补一个 GET 版本时踩坑。
    """
    expected = (settings.ALARM_INGEST_KEY or "").strip()
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="X-API-Key 无效")

    level = (body.level or "").strip().lower()
    if level not in ("warning", "danger"):
        raise HTTPException(status_code=422, detail="level 只能是 warning 或 danger")

    rule = (body.rule_name or "").strip()
    if not rule:
        raise HTTPException(status_code=422, detail="rule_name 不能为空")

    origin = (body.origin or "外部系统").strip()[:50]

    # 重推去重：外部系统遇网络抖动会重试，同一条告警不该在列表里出现两次
    gk = body.group_key
    if not gk and body.external_id:
        gk = f"ext:{origin}:{body.external_id}"
    if body.external_id and gk:
        exist = (
            db.query(AlarmRecord)
            .filter(AlarmRecord.group_key == gk, AlarmRecord.source == "external")
            .first()
        )
        if exist:
            return {"ok": True, "id": exist.id, "duplicate": True, "broadcast": False}

    # created_at：外部给的时间可能带时区，库里统一存 naive UTC
    created = None
    if body.created_at:
        try:
            dt = datetime.fromisoformat(body.created_at.replace("Z", "+00:00"))
            created = dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            raise HTTPException(status_code=422, detail="created_at 需为 ISO 8601 时间")

    rec = AlarmRecord(
        scene_id=(body.scene_id or "flotation").strip(),
        level=level,
        rule_name=rule[:100],
        message=(body.message or "")[:2000],
        evidence={
            **(body.evidence or {}),
            "ingest": {
                "origin": origin,
                "external_id": body.external_id,
                "received_at": datetime.utcnow().isoformat(),
            },
        },
        source="external",
        origin=origin,
        group_key=gk,
    )
    if created:
        rec.created_at = created

    db.add(rec)
    db.commit()
    db.refresh(rec)

    # 立即广播（WS + Webhook）。广播失败不能影响"已经入库"这个事实
    from backend.api.routers.ws import publish_alarm
    try:
        await publish_alarm(_ws_payload(rec))
    except Exception as e:
        print(f"[ingest] 广播失败（记录已入库 id={rec.id}）: {e}")

    return {"ok": True, "id": rec.id, "duplicate": False, "broadcast": True}


# ---------------------------------------------------------------------------
# 列表 / 分组
# ---------------------------------------------------------------------------

@router.get("/api/alarms/groups")
def list_alarm_groups(
    scene_id: str = Query(None),
    level: str = Query(None),
    status: str = Query(None),
    limit: int = Query(20, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """帧级分组列表：同一帧图像触发的多条规则合并为一条「事件」

    组内代表 = 级别最高（danger 优先）→ 时间最早的那条。
    组状态 = 组内"最未处理完"的状态。
    """
    scene_id = scene_id or None
    level = level or None
    if level is not None and level not in ("warning", "danger"):
        raise HTTPException(400, f"无效的告警级别: {level}")
    statuses = _parse_status_filter(status)

    q = db.query(AlarmRecord)
    if scene_id:
        q = q.filter(AlarmRecord.scene_id == scene_id)
    if level:
        q = q.filter(AlarmRecord.level == level)
    if statuses:
        q = q.filter(AlarmRecord.status.in_(statuses))

    rows = q.order_by(AlarmRecord.created_at.desc()).all()

    buckets = defaultdict(list)
    for a in rows:
        key = a.group_key or f"single:{a.id}"
        buckets[key].append(a)

    level_weight = {"danger": 2, "warning": 1}
    groups = []
    for key, items in buckets.items():
        items.sort(key=lambda x: (-level_weight.get(x.level, 0), x.created_at or datetime.min))
        primary = items[0]
        ev = _load_evidence(primary.evidence)
        latest = max((i.created_at for i in items if i.created_at), default=None)
        groups.append({
            "group_key": key,
            "count": len(items),
            "primary": _alarm_dict(primary),
            "rules": [
                {
                    "id": i.id,
                    "rule_name": i.rule_name,
                    "level": i.level,
                    "status": i.status or "new",
                    "status_label": _STATUS_LABELS.get(i.status or "new", ""),
                    "message": i.message,
                }
                for i in items
            ],
            "levels": {
                "danger": sum(1 for i in items if i.level == "danger"),
                "warning": sum(1 for i in items if i.level == "warning"),
            },
            "status": _group_status([(i.status or "new") for i in items]),
            "unhandled": sum(1 for i in items if (i.status or "new") in ("new", "in_progress")),
            "scene_id": primary.scene_id,
            "thumb": ev.get("image"),
            "overlay": ev.get("overlay"),
            "latest_at": latest.isoformat() if latest else None,
        })

    groups.sort(key=lambda g: g["latest_at"] or "", reverse=True)
    total = len(groups)
    page = groups[offset: offset + limit]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "stats": _build_stats(db),
        "groups": page,
    }


@router.get("/api/alarms")
def list_alarms(
    scene_id: str = Query(None),
    level: str = Query(None),
    acknowledged: str = Query(None),
    status: str = Query(None),
    keyword: str = Query(None),
    collapse: int = Query(0, ge=0, le=1, description="1=按帧聚合，一帧只返回一条代表"),
    db: Session = Depends(get_db),
    limit: int = Query(20, le=200),
    offset: int = Query(0, ge=0),
):
    """告警列表（分页 + 筛选）。v2 增加 status / keyword / collapse，返回体保持向后兼容。

    collapse=1 时按 group_key 做帧级聚合，返回的是"事件"而非"告警条"——
    这也是告警中心「446 条 → 349 事件」的口径，避免两处数字打架。
    """
    scene_id = scene_id or None
    level = level or None
    keyword = keyword or None
    if level is not None and level not in ("warning", "danger"):
        raise HTTPException(400, f"无效的告警级别: {level}")
    if acknowledged is None or acknowledged == "":
        acknowledged = None
    else:
        if acknowledged.lower() in ("true", "1", "yes"):
            acknowledged = True
        elif acknowledged.lower() in ("false", "0", "no"):
            acknowledged = False
        else:
            raise HTTPException(400, f"无效的确认状态: {acknowledged}")
    statuses = _parse_status_filter(status)

    q = db.query(AlarmRecord)
    if scene_id:
        q = q.filter(AlarmRecord.scene_id == scene_id)
    if level:
        q = q.filter(AlarmRecord.level == level)
    if acknowledged is not None:
        q = q.filter(AlarmRecord.is_acknowledged == acknowledged)
    if statuses:
        q = q.filter(AlarmRecord.status.in_(statuses))
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(AlarmRecord.message.like(like) | AlarmRecord.rule_name.like(like))

    total = q.count()
    if collapse:
        # 折叠会减少条数，多取一些原始行再截断，保证折叠后仍有 limit 条
        raw = q.order_by(AlarmRecord.created_at.desc()).offset(offset).limit(limit * 8).all()
        rows = _collapse_by_frame(raw)[:limit]
    else:
        rows = q.order_by(AlarmRecord.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "collapsed": bool(collapse),
        "stats": _build_stats(db),
        "alarms": [_alarm_dict(a) for a in rows],
    }


# ---------------------------------------------------------------------------
# 详情（证据链）
# ---------------------------------------------------------------------------

@router.get("/api/alarms/{alarm_id}")
def get_alarm(alarm_id: int, db: Session = Depends(get_db)):
    """告警详情：结构化证据 + 规则命中 + 同帧其他告警 + 处置流水"""
    alarm = db.query(AlarmRecord).filter(AlarmRecord.id == alarm_id).first()
    if not alarm:
        raise HTTPException(404, f"告警记录不存在: {alarm_id}")

    ev = _load_evidence(alarm.evidence)
    scene = alarm.scene_id
    pred = ev.get("prediction") or {}
    metrics = ev.get("metrics") or {}

    # 场景分型：flotation 走泡沫特征，safety 走风险评分，grinding 走粒度
    kind = "vision_quality"
    if scene == "safety":
        kind = "risk"
    elif scene == "flotation":
        kind = "foam"
    elif scene == "grinding":
        kind = "particle"

    # ---- 泡沫特征快照 → 可解释的偏离度 ----
    # ⚠️ 字段名陷阱：evidence.prediction.z_scores 在 2026-10-13 之前存的是**原始特征值**
    # （predictor._predict_xgb 的归一化参数键名被写死成字面量 "k"，永远匹配不到真实特征名，
    # zscore_features 全部回落到 mean=0/std=1，z 退化成原始量纲）。
    # 这里自动判别：是老数据就用可追溯的历史基线现场换算真实的 z。
    raw_feat = pred.get("z_scores") or {}
    feature_eval = {"source": "unavailable", "baseline_n": 0, "significant": 0, "items": []}
    if raw_feat:
        if looks_like_raw_features(raw_feat.values()):
            evl = evaluate_features(raw_feat, scene)
            feature_eval = {
                "source": "baseline_recomputed",     # 原始值 → 用同口径历史基线换算
                "baseline_n": evl.get("sample_n", 0),
                "excluded_bad": evl.get("excluded_bad", 0),
                "significant": evl.get("significant", 0),
                "items": [
                    {**it, "label": _FEATURE_LABELS.get(it["key"], it["key"])}
                    for it in evl["items"]
                ],
            }
        else:
            items = []
            for k, v in raw_feat.items():
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                items.append({
                    "key": k,
                    "label": _FEATURE_LABELS.get(k, k),
                    "z": round(fv, 2),
                    "abs_z": round(abs(fv), 2),
                    "direction": "偏高" if fv > 0 else "偏低",
                    "severity": "high" if abs(fv) >= 2.5 else ("medium" if abs(fv) >= 1.5 else "normal"),
                })
            items.sort(key=lambda d: d["abs_z"], reverse=True)
            feature_eval = {
                "source": "z_score",
                "baseline_n": 0,
                "significant": sum(1 for i in items if i["abs_z"] >= 1.5),
                "items": items,
            }

    # 规则命中详情
    rule = _match_rule(scene, alarm.rule_name)
    rule_hit = None
    if rule:
        field = rule.get("field")
        val = _field_value(ev, field)
        threshold = rule.get("threshold")
        rule_hit = {
            "name": rule.get("name"),
            "field": field,
            "field_label": _FIELD_LABELS.get(field, field),
            "condition": rule.get("condition"),
            "condition_label": _OP_LABELS.get(rule.get("condition"), rule.get("condition")),
            "threshold": threshold,
            "value": val,
            "cooldown_sec": rule.get("cooldown_sec"),
            "deviation": (round(val - float(threshold), 3)
                          if val is not None and threshold is not None else None),
            "source": "scene_yaml",
        }
    else:
        rule_hit = {
            "name": alarm.rule_name,
            "source": "unknown",
            "note": "该规则未在当前场景包 YAML 中找到定义",
        }

    # 同帧其他告警
    siblings = []
    if alarm.group_key:
        sib_rows = db.query(AlarmRecord).filter(
            AlarmRecord.group_key == alarm.group_key,
            AlarmRecord.id != alarm.id,
        ).order_by(AlarmRecord.created_at.asc()).all()
        siblings = [_alarm_dict(s) for s in sib_rows]

    # 处置流水
    actions = db.query(AlarmAction).filter(
        AlarmAction.alarm_id == alarm.id
    ).order_by(AlarmAction.created_at.asc()).all()

    # 关联的完整分析记录（record_id → analysis_records）
    record = None
    rid = ev.get("record_id")
    if rid is not None:
        ar = db.query(AnalysisRecord).filter(AnalysisRecord.id == rid).first()
        if ar:
            record = {
                "id": ar.id,
                "scene_id": ar.scene_id,
                "model_version": ar.model_version,
                "created_at": ar.created_at.isoformat() if ar.created_at else None,
                "source": ar.source,
                "image_path": ar.image_path,
            }

    return {
        "alarm": _alarm_dict(alarm),
        "evidence": {
            "kind": kind,
            "image": ev.get("image"),
            "overlay": ev.get("overlay"),
            "overlay_kind": "det" if scene == "safety" else "seg",
            "metrics": metrics,
            # 去掉 z_scores 原始键，避免前端把它当 z-score 误用
            "prediction": {k: v for k, v in pred.items() if k != "z_scores"},
            "features": feature_eval,
            "risk": ({
                "risk_level": pred.get("risk_level"),
                "risk_score": pred.get("risk_score"),
                "confidence": pred.get("confidence"),
                "action": pred.get("action"),
                "camera_id": pred.get("camera_id"),
                "model": pred.get("model"),
            } if scene == "safety" else None),
        },
        "rule_hit": rule_hit,
        "siblings": siblings,
        "actions": [_action_dict(x) for x in actions],
        "record": record,
    }


# ---------------------------------------------------------------------------
# 处置流转
# ---------------------------------------------------------------------------

class HandleRequest(BaseModel):
    action: str = Field(..., description="acknowledge / start / close / false_positive / reopen")
    operator: str = Field(..., min_length=1, max_length=50, description="处理人（岗位或姓名）")
    note: Optional[str] = Field(None, description="处理意见（start/close/false_positive 必填）")
    action_taken: Optional[str] = Field(None, description="处置措施")


@router.get("/api/alarms/{alarm_id}/root-cause")
def alarm_root_cause(alarm_id: int, db: Session = Depends(get_db)):
    """根因联动归因（P1）

    把一条浮选品位告警放回「磨矿 → 浮选 → 回水」工艺链上，回溯告警发生**之前**
    同期三个工段的真实记录，用选矿工艺先验给候选原因排序。

    返回 candidates（候选原因，带证据/机理/建议措施）、sources（各源最近样本与
    时间距离）、missing（数据缺口）、caveats（免责说明）。不做因果断言。
    """
    a = db.query(AlarmRecord).filter(AlarmRecord.id == alarm_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="告警不存在")
    return analyze_root_cause(a.id, a.scene_id, a.created_at, a.rule_name)


def _do_handle(alarm: AlarmRecord, req: HandleRequest, db: Session) -> dict:
    if req.action not in _TRANSITIONS:
        raise HTTPException(400, f"无效的处置动作: {req.action}（可选：{'/'.join(_TRANSITIONS)}）")

    cur_status = alarm.status or ("acknowledged" if alarm.is_acknowledged else "new")
    allowed_from, to_status = _TRANSITIONS[req.action]
    if cur_status not in allowed_from:
        raise HTTPException(
            409,
            f"当前状态「{_STATUS_LABELS.get(cur_status, cur_status)}」不能执行"
            f"「{_ACTION_LABELS.get(req.action, req.action)}」，允许的来源状态："
            + "、".join(_STATUS_LABELS.get(s, s) for s in allowed_from),
        )

    note = (req.note or "").strip()
    if req.action in _NOTE_REQUIRED and not note:
        raise HTTPException(400, f"执行「{_STATUS_LABELS.get(to_status)}」必须填写处理意见")

    now = datetime.utcnow()
    alarm.status = to_status
    alarm.handled_by = req.operator.strip()
    alarm.handled_at = now
    if note:
        alarm.handled_note = note
    if req.action_taken is not None:
        alarm.action_taken = req.action_taken.strip() or None
    alarm.false_positive = (req.action == "false_positive")

    # 兼容旧字段 / 旧接口
    if req.action == "acknowledge":
        alarm.is_acknowledged = True
        alarm.acknowledged_by = alarm.acknowledged_by or req.operator.strip()
        alarm.acknowledged_at = alarm.acknowledged_at or now
    elif req.action == "reopen":
        alarm.is_acknowledged = False
        alarm.acknowledged_by = None
        alarm.acknowledged_at = None

    db.add(AlarmAction(
        alarm_id=alarm.id,
        action=req.action,
        from_status=cur_status,
        to_status=to_status,
        operator=req.operator.strip(),
        note=note or None,
        created_at=now,
    ))
    db.commit()
    db.refresh(alarm)

    # ---- 实时同步：A 窗口处置完，B 窗口立刻看到（2026-10-19）----
    # ★ `_do_handle` 是同步函数（FastAPI 的 def 路由跑在线程池，不能 await），
    #   所以走 publish_sync 把协程投递回主事件循环。
    #   没有这一步，两个窗口之间要等 30 秒轮询才一致 —— 演示时"我这边点完了你那边还挂着"最尴尬。
    try:
        from backend.api.routers.ws import publish_sync
        publish_sync("alarm.updated", _ws_payload(alarm))
    except Exception as e:
        print(f"[alarms] 状态变更广播失败 id={alarm.id}: {e}")

    return {
        "id": alarm.id,
        "status": alarm.status,
        "status_label": _STATUS_LABELS.get(alarm.status, alarm.status),
        "handled_by": alarm.handled_by,
        "handled_at": alarm.handled_at.isoformat() if alarm.handled_at else None,
        "false_positive": bool(alarm.false_positive),
    }


@router.post("/api/alarms/{alarm_id}/handle")
def handle_alarm(alarm_id: int, req: HandleRequest, db: Session = Depends(get_db)):
    """处置流转（写审计流水）— 处置闭环主入口"""
    alarm = db.query(AlarmRecord).filter(AlarmRecord.id == alarm_id).first()
    if not alarm:
        raise HTTPException(404, f"告警记录不存在: {alarm_id}")
    return _do_handle(alarm, req, db)


@router.put("/api/alarms/{alarm_id}/acknowledge")
def acknowledge_alarm(alarm_id: int, db: Session = Depends(get_db)):
    """确认告警（兼容旧前端）— 内部走 v2 处置流转，写审计流水"""
    alarm = db.query(AlarmRecord).filter(AlarmRecord.id == alarm_id).first()
    if not alarm:
        raise HTTPException(404, f"告警记录不存在: {alarm_id}")

    cur_status = alarm.status or ("acknowledged" if alarm.is_acknowledged else "new")
    if cur_status == "acknowledged":
        return {"id": alarm.id, "message": "该告警已确认过",
                "acknowledged_at": alarm.acknowledged_at.isoformat() if alarm.acknowledged_at else None}

    if cur_status in ("closed", "false_positive", "in_progress"):
        raise HTTPException(409, f"当前状态「{_STATUS_LABELS.get(cur_status, cur_status)}」无需确认")

    return _do_handle(alarm, HandleRequest(action="acknowledge", operator="operator"), db)


# ---------------------------------------------------------------------------
# 批量处置（帧组一键闭环）
# ---------------------------------------------------------------------------

class BatchHandleRequest(BaseModel):
    alarm_ids: List[int]
    action: str = Field(..., description="同单条处置动作")
    operator: str = Field(..., min_length=1, max_length=50)
    note: Optional[str] = None
    action_taken: Optional[str] = None


@router.post("/api/alarms/batch-handle")
def batch_handle(req: BatchHandleRequest, db: Session = Depends(get_db)):
    """批量处置（组内一次闭环），部分失败不阻断，返回逐条结果"""
    if not req.alarm_ids:
        raise HTTPException(400, "alarm_ids 不能为空")
    ok, failed = [], []
    for aid in req.alarm_ids[:200]:
        alarm = db.query(AlarmRecord).filter(AlarmRecord.id == aid).first()
        if not alarm:
            failed.append({"id": aid, "reason": "不存在"})
            continue
        try:
            _do_handle(alarm, HandleRequest(
                action=req.action, operator=req.operator,
                note=req.note, action_taken=req.action_taken,
            ), db)
            ok.append(aid)
        except HTTPException as e:
            failed.append({"id": aid, "reason": e.detail})
    return {"ok": ok, "failed": failed, "ok_count": len(ok), "failed_count": len(failed)}


# ---------------------------------------------------------------------------
# 报表聚合
# ---------------------------------------------------------------------------

@router.get("/api/reports/summary")
def report_summary(
    scene_name: str = Query("flotation"),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """报表聚合数据（日/周/月统计）"""
    since = datetime.utcnow() - timedelta(days=days)

    rows = db.query(
        AnalysisRecord.created_at,
        AnalysisRecord.prediction,
        AnalysisRecord.actual_value,
        AnalysisRecord.model_version,
    ).filter(
        AnalysisRecord.scene_id == scene_name,
        AnalysisRecord.created_at >= since,
    ).order_by(AnalysisRecord.created_at).all()

    daily = defaultdict(lambda: {"records": 0, "preds": [], "actuals": []})
    for r in rows:
        dt = r.created_at.date() if r.created_at else None
        if not dt:
            continue
        key = str(dt)
        daily[key]["records"] += 1
        try:
            pred = r.prediction or {}
            pb = pred.get("pb_grade_xrf") or pred.get("pb_grade")
            if pb is not None:
                daily[key]["preds"].append(float(pb))
            if r.actual_value is not None:
                daily[key]["actuals"].append(float(r.actual_value))
        except (TypeError, ValueError):
            pass

    daily_list = []
    for d in sorted(daily.keys()):
        a = daily[d]
        preds = a["preds"]
        actuals = a["actuals"]
        daily_list.append({
            "date": d,
            "records": a["records"],
            "avg_pred": round(sum(preds) / len(preds), 3) if preds else None,
            "avg_actual": round(sum(actuals) / len(actuals), 3) if actuals else None,
            "min_pred": round(min(preds), 3) if preds else None,
            "max_pred": round(max(preds), 3) if preds else None,
        })

    all_preds = []
    all_actuals = []
    for a in daily.values():
        all_preds.extend(a["preds"])
        all_actuals.extend(a["actuals"])

    alarm_daily = defaultdict(int)
    alarm_rows = db.query(
        AlarmRecord.created_at, AlarmRecord.level
    ).filter(
        AlarmRecord.scene_id == scene_name,
        AlarmRecord.created_at >= since,
    ).all()
    for ar in alarm_rows:
        dt = ar.created_at.date() if ar.created_at else None
        if dt:
            alarm_daily[str(dt)] += 1

    return {
        "scene": scene_name,
        "period": {"days": days, "from": str(since.date()), "to": str(datetime.utcnow().date())},
        "overview": {
            "total_records": len(rows),
            "total_days": len(daily_list),
            "avg_records_per_day": round(len(rows) / max(len(daily_list), 1), 1),
            "overall_avg_pred": round(sum(all_preds) / len(all_preds), 3) if all_preds else None,
            "overall_avg_actual": round(sum(all_actuals) / len(all_actuals), 3) if all_actuals else None,
            "prediction_range": {
                "min": round(min(all_preds), 3) if all_preds else None,
                "max": round(max(all_preds), 3) if all_preds else None,
            },
        },
        "daily": daily_list,
        "alarm_trend": [{"date": d, "count": c} for d, c in sorted(alarm_daily.items())],
    }


# ---------------------------------------------------------------------------
# 静态资源服务（时光机缩略图用）
# ---------------------------------------------------------------------------

@router.get("/api/image/{path:path}")
def serve_image(path: str):
    """返回已存储的分析图像（原图/分割叠加图）

    支持两种路径模式：
    1. 相对路径：data/uploads/flotation/xxx.jpg → 从 DATA_DIR 下查找
    2. 绝对路径：D:\\flaoting\\dataset\\xxx.jpg → 直接访问（训练数据集原始图）
    """
    import unicodedata

    path = unicodedata.normalize('NFC', path)

    base_dir = os.path.realpath(settings.DATA_DIR)
    candidates = [
        os.path.join(settings.DATA_DIR, path),
        path,
    ]
    for cand in candidates:
        full = os.path.realpath(cand)
        if full.startswith(base_dir + os.sep) and os.path.isfile(full):
            return FileResponse(full, media_type='image/jpeg')

    if os.path.isabs(path):
        abs_path = os.path.realpath(path)
        _IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
        _BLOCKED = {'windows', 'program files', 'system32', 'appdata'}

        if (os.path.isfile(abs_path)
            and os.path.splitext(abs_path)[1].lower() in _IMAGE_EXTS
            and not any(b in abs_path.lower() for b in _BLOCKED)):
            return FileResponse(abs_path, media_type='image/jpeg')

    raise HTTPException(404, f"图像不存在: {path}")
