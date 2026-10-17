"""安全巡检中心 API — 点位监控 / 巡检时间线 / KPI 统计 / 隐患闭环

数据来源：analysis_records（scene_id='safety'，features.camera_id 标记点位）
+ alarm_records（evidence.record_id 关联记录）+ hazard_tickets（隐患工单闭环）。
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.database import (
    AlarmRecord,
    AnalysisRecord,
    HazardTicket,
    get_db,
)

router = APIRouter()

# ---- 巡检点位注册表（演示口径：4 点位覆盖皮带全线 + 破碎给料）----
CAMERAS = [
    {
        "id": "CAM-01", "name": "1#皮带·机头", "zone": "机头驱动滚筒区",
        "focus": "人员入侵 · PPE 合规", "danger_zone": "皮带机头",
    },
    {
        "id": "CAM-02", "name": "1#皮带·机尾", "zone": "机尾改向滚筒区",
        "focus": "皮带跑偏监测", "danger_zone": "皮带机尾",
    },
    {
        "id": "CAM-03", "name": "2#皮带·中段", "zone": "重锤张紧段",
        "focus": "异物检测 · 跑偏", "danger_zone": "皮带中段",
    },
    {
        "id": "CAM-04", "name": "破碎给料站", "zone": "颚破给料口",
        "focus": "人员入侵 · PPE 合规", "danger_zone": "皮带机头",
    },
]

# 风险等级 → 点位状态灯
_LEVEL_LIGHT = {"low": "normal", "medium": "normal", "high": "warning", "critical": "danger"}

_LEVEL_LABEL = {"low": "低风险", "medium": "中风险", "high": "高风险", "critical": "严重"}


def _cam_expr():
    """analysis_records.features.camera_id 的 JSON 提取表达式（SQLite）"""
    return func.json_extract(AnalysisRecord.features, "$.camera_id")


def _summarize_record(rec: AnalysisRecord) -> dict:
    """把一条 safety 记录组装成前端时间线/点位卡需要的结构"""
    feats = rec.features or {}
    pred = rec.prediction or {}
    level = pred.get("risk_level", "low")
    imgs = (feats.get("image_paths") or {})
    return {
        "id": rec.id,
        "camera_id": feats.get("camera_id"),
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "source": rec.source,
        "risk_level": level,
        "risk_label": _LEVEL_LABEL.get(level, level),
        "risk_score": pred.get("risk_score"),
        "action": pred.get("action"),
        "person_count": feats.get("person_count", 0),
        "persons_in_danger_zone": feats.get("persons_in_danger_zone", 0),
        "ppe_compliance_rate": feats.get("ppe_compliance_rate"),
        "belt_offset_px": feats.get("belt_offset_px"),
        "belt_severity": feats.get("belt_severity"),
        "foreign_object_count": feats.get("foreign_object_count", 0),
        "pipeline_ms": feats.get("pipeline_ms"),
        "image_path": rec.image_path,
        "detection_overlay": imgs.get("detection_overlay"),
        "danger_zone_map": imgs.get("danger_zone_map"),
        "risk_assessment": imgs.get("risk_assessment"),
    }


# ============================================================
# 点位监控
# ============================================================

@router.get("/api/safety/cameras")
def list_cameras(db: Session = Depends(get_db)):
    """4 个巡检点位的实时状态（各自最新一条巡检记录 + 今日巡检次数）"""
    now = datetime.utcnow()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cam_expr = _cam_expr()

    out = []
    for cam in CAMERAS:
        rec = (
            db.query(AnalysisRecord)
            .filter(AnalysisRecord.scene_id == "safety", cam_expr == cam["id"])
            .order_by(AnalysisRecord.created_at.desc())
            .first()
        )
        today_count = (
            db.query(func.count(AnalysisRecord.id))
            .filter(
                AnalysisRecord.scene_id == "safety",
                cam_expr == cam["id"],
                AnalysisRecord.created_at >= today0,
            )
            .scalar()
        )
        latest = _summarize_record(rec) if rec else None
        out.append({
            **cam,
            "status": _LEVEL_LIGHT.get(latest["risk_level"], "normal") if latest else "normal",
            "today_count": today_count or 0,
            "latest": latest,
        })
    return {"cameras": out, "count": len(out)}


# ============================================================
# 巡检时间线
# ============================================================

@router.get("/api/safety/timeline")
def safety_timeline(
    camera_id: str = None,
    limit: int = 30,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """巡检记录时间线（含关联告警、检测图路径）"""
    q = db.query(AnalysisRecord).filter(AnalysisRecord.scene_id == "safety")
    if camera_id:
        q = q.filter(_cam_expr() == camera_id)
    total = q.count()
    records = (
        q.order_by(AnalysisRecord.created_at.desc())
        .offset(offset)
        .limit(min(limit, 100))
        .all()
    )

    # 关联告警（evidence.record_id 匹配）
    rec_ids = [r.id for r in records]
    alarms_by_rec = {}
    if rec_ids:
        rows = (
            db.query(
                func.json_extract(AlarmRecord.evidence, "$.record_id"),
                AlarmRecord.id,
                AlarmRecord.level,
                AlarmRecord.rule_name,
                AlarmRecord.message,
            )
            .filter(AlarmRecord.scene_id == "safety")
            .all()
        )
        for rid, aid, level, rule_name, message in rows:
            if rid in rec_ids:
                alarms_by_rec.setdefault(rid, []).append({
                    "id": aid, "level": level, "rule_name": rule_name, "message": message,
                })

    items = []
    for r in records:
        d = _summarize_record(r)
        d["alarms"] = alarms_by_rec.get(r.id, [])
        items.append(d)

    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ============================================================
# KPI 统计
# ============================================================

@router.get("/api/safety/stats")
def safety_stats(db: Session = Depends(get_db)):
    """巡检中心 KPI：今日巡检次数 / 待处理隐患 / 30天危险区入侵 / PPE合规率 / 风险分布"""
    now = datetime.utcnow()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since30 = now - timedelta(days=30)

    base_q = db.query(AnalysisRecord).filter(AnalysisRecord.scene_id == "safety")

    today_count = base_q.filter(AnalysisRecord.created_at >= today0).count()
    total_count = base_q.count()

    # 30 天风险等级分布
    risk_rows = (
        db.query(
            func.json_extract(AnalysisRecord.prediction, "$.risk_level"),
            func.count(AnalysisRecord.id),
        )
        .filter(AnalysisRecord.scene_id == "safety", AnalysisRecord.created_at >= since30)
        .group_by(func.json_extract(AnalysisRecord.prediction, "$.risk_level"))
        .all()
    )
    risk_dist = {lvl or "low": cnt for lvl, cnt in risk_rows}

    # 30 天危险区入侵次数（人员进入危险区的巡检帧数）
    intrusion_30d = (
        db.query(func.count(AnalysisRecord.id))
        .filter(
            AnalysisRecord.scene_id == "safety",
            AnalysisRecord.created_at >= since30,
            func.json_extract(AnalysisRecord.features, "$.persons_in_danger_zone") > 0,
        )
        .scalar()
    ) or 0

    # 30 天平均 PPE 合规率（null 记录按 1.0 计）
    ppe_avg = (
        db.query(func.avg(func.json_extract(AnalysisRecord.features, "$.ppe_compliance_rate")))
        .filter(AnalysisRecord.scene_id == "safety", AnalysisRecord.created_at >= since30)
        .scalar()
    )

    # 隐患工单
    hazard_open = (
        db.query(func.count(HazardTicket.id))
        .filter(HazardTicket.status.in_(["pending", "rectifying"]))
        .scalar()
    ) or 0
    hazard_total = db.query(func.count(HazardTicket.id)).scalar() or 0

    # 30 天安全告警数
    alarm_30d = (
        db.query(func.count(AlarmRecord.id))
        .filter(AlarmRecord.scene_id == "safety", AlarmRecord.created_at >= since30)
        .scalar()
    ) or 0

    return {
        "today_count": today_count,
        "total_count": total_count,
        "intrusion_30d": intrusion_30d,
        "ppe_compliance_avg": round(ppe_avg, 3) if ppe_avg is not None else None,
        "risk_distribution_30d": risk_dist,
        "hazard_open": hazard_open,
        "hazard_total": hazard_total,
        "alarm_30d": alarm_30d,
    }


# ============================================================
# 隐患工单闭环
# ============================================================

class TransitionBody(BaseModel):
    action: str  # rectify / verify / reopen
    assigned_to: str = None
    action_taken: str = None


@router.get("/api/safety/hazard-tickets")
def list_hazard_tickets(
    status: str = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """隐患工单列表（按状态筛选）"""
    q = db.query(HazardTicket)
    if status:
        q = q.filter(HazardTicket.status == status)
    tickets = q.order_by(HazardTicket.created_at.desc()).limit(min(limit, 200)).all()

    status_count = dict(
        db.query(HazardTicket.status, func.count(HazardTicket.id)).group_by(HazardTicket.status).all()
    )

    return {
        "items": [
            {
                "id": t.id,
                "camera_id": t.camera_id,
                "record_id": t.record_id,
                "level": t.level,
                "title": t.title,
                "detail": t.detail,
                "evidence": t.evidence,
                "status": t.status,
                "assigned_to": t.assigned_to,
                "action_taken": t.action_taken,
                "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tickets
        ],
        "status_count": status_count,
    }


@router.post("/api/safety/hazard-tickets/{ticket_id}/transition")
def transition_ticket(ticket_id: int, body: TransitionBody, db: Session = Depends(get_db)):
    """隐患工单状态流转：pending → rectifying → closed（闭环），支持 reopen"""
    ticket = db.query(HazardTicket).filter(HazardTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(404, f"工单不存在: {ticket_id}")

    flow = {
        "rectify": {"from": "pending", "to": "rectifying"},
        "verify": {"from": "rectifying", "to": "closed"},
        "reopen": {"from": "closed", "to": "pending"},
    }
    rule = flow.get(body.action)
    if not rule:
        raise HTTPException(400, f"非法动作: {body.action}（支持 rectify/verify/reopen）")
    if ticket.status != rule["from"]:
        raise HTTPException(400, f"状态不匹配：工单当前 {ticket.status}，{body.action} 要求 {rule['from']}")

    ticket.status = rule["to"]
    if body.action == "rectify":
        if body.assigned_to:
            ticket.assigned_to = body.assigned_to
        if body.action_taken:
            ticket.action_taken = body.action_taken
    if body.action == "verify":
        ticket.resolved_at = datetime.utcnow()
        if body.action_taken:
            ticket.action_taken = body.action_taken
    if body.action == "reopen":
        ticket.resolved_at = None

    db.commit()
    return {"id": ticket.id, "status": ticket.status, "ok": True}
