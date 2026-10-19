"""场景管理 API — 健康检查 + 场景 CRUD + 统计 + 趋势 + 模型列表"""

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.database import (
    AlarmRecord,
    AnalysisRecord,
    ModelVersion,
    ScenePackage,
    get_db,
)

router = APIRouter()

# ── 场景 → 自有数据源映射 ────────────────────────────────────────────────
# 记录数的语义 = 「该场景自有数据表沉淀的条数」，跨表不混算。
# 早期实现一律查 analysis_records，导致 water / tailings 的记录数恒为 0
# （它们的数据落在各自的专用表里，不在统一分析表中）。
# 格式：scene_name -> (表名, 分组列)。表名/列名均为下方白名单常量，非用户输入。
SCENE_RECORD_SOURCE = {
    "water": ("water_readings", "station_id"),        # 5 个水质点位
    "tailings": ("tailings_readings", "equipment_id"),  # T1 尾矿浓密机
}
_DEFAULT_RECORD_SOURCE = ("analysis_records", "group_id")

# 实时计算、不沉淀历史数据的场景：记录数返回 None，前端渲染为「—」
# （equipment 的监测值按请求现算，本身不存在「累计记录」这个概念，填 0 是误导）
SCENE_NO_STORE = {"equipment"}


def _record_table(name: str):
    """场景自有数据表名；None 表示该场景不沉淀历史数据（实时计算）。"""
    if name in SCENE_NO_STORE:
        return None
    return SCENE_RECORD_SOURCE.get(name, _DEFAULT_RECORD_SOURCE)[0]


def _scene_counts(db: Session, name: str):
    """统计某场景的 (记录数, 分组数)。无自有持久化数据的场景返回 (None, None)。"""
    table = _record_table(name)
    if table is None:
        return None, None

    group_col = SCENE_RECORD_SOURCE.get(name, _DEFAULT_RECORD_SOURCE)[1]
    if table == "analysis_records":
        where, params = "scene_id = :sn", {"sn": name}
    else:
        where, params = "1 = 1", {}

    n = db.execute(
        text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), params
    ).scalar()
    g = db.execute(
        text(f"SELECT COUNT(DISTINCT {group_col}) FROM {table} WHERE {where}"), params
    ).scalar()
    return int(n or 0), int(g or 0)


@router.get("/api/health")
def health():
    """健康检查"""
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@router.get("/api/scenes")
def list_scenes(db: Session = Depends(get_db)):
    """场景列表 —— 以 scene_packages 注册表为唯一真源。

    记录数按各场景自有数据表统计（见 SCENE_RECORD_SOURCE），不再一律查
    analysis_records；场景是否「运行中」完全由注册表 status 决定，
    不再靠硬编码的 planned 名单兜底（历史上 water / equipment 早已上线，
    却因未注册而被显示成「规划中」）。
    """
    rows = db.query(ScenePackage).order_by(ScenePackage.id).all()

    result = []
    for s in rows:
        n_records, n_groups = _scene_counts(db, s.name)
        table = _record_table(s.name)
        active_model = db.query(ModelVersion).filter(
            ModelVersion.scene_id == s.name,
            ModelVersion.is_active == True,
        ).first()

        result.append({
            "name": s.name,
            "display_name": s.display_name,
            "status": s.status,
            "records": n_records,
            "groups": n_groups,
            # 是否写入统一分析台账（analysis_records）。
            # 工况时光机 / 工作台建立在分析台账之上，只有这类场景可查历史记录；
            # water / tailings 的数据在各自专用表里，equipment 不沉淀历史。
            "has_analysis_records": table == "analysis_records",
            "active_model": active_model.version if active_model else None,
            "description": s.description,
        })

    return {"scenes": result}


@router.get("/api/scenes/{scene_name}/records")
def list_records(
    scene_name: str,
    db: Session = Depends(get_db),
    limit: int = Query(20, le=200),
    offset: int = Query(0, ge=0),
    group_id: str = None,
):
    """分页查询某场景的分析记录"""
    q = db.query(AnalysisRecord).filter(AnalysisRecord.scene_id == scene_name)
    if group_id:
        q = q.filter(AnalysisRecord.group_id == group_id)

    total = q.count()
    rows = q.order_by(
        AnalysisRecord.created_at.desc(), AnalysisRecord.id.desc()
    ).offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "records": [
            {
                "id": r.id,
                "group_id": r.group_id,
                "image_path": r.image_path,
                "prediction": r.prediction,
                "actual_value": r.actual_value,
                "model_version": r.model_version,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get("/api/scenes/{scene_name}/latest")
def scene_latest(
    scene_name: str,
    db: Session = Depends(get_db),
    field: str = Query("pb_grade_xrf", description="prediction 内字段名，兼容旧字段 pb_grade"),
    min_value: float = Query(None, alias="min", description="口径下限，默认取 settings.PB_GRADE_MIN"),
    max_value: float = Query(None, alias="max", description="口径上限，默认取 settings.PB_GRADE_MAX"),
    max_scan: int = Query(500, ge=1, le=5000, description="最多回看多少条"),
):
    """取「口径内」最近一条分析记录。

    ★ 为什么需要这个接口（2026-10-19，老板拍板方案 A）：
      门面 KPI「最新一次分析」原来直接取 `records?limit=1`。但 `/api/analyze`
      产生的 upload 记录是**真模型输出**，实测 Pb 1.69~1.84%，落在 3~8% 演示口径之外；
      而同一格的「均值 5.23%」来自 actual_value（XRF 实测真值）。
      于是门面左手写"均值 5.23%"、右手写"最新 1.79%"，结论条还冒出红字
      「最新品位低于均值 66%」—— 第一屏就自相矛盾，访客只会认定"数据是假的"。

    ★ 口径在取数层过滤，**库里一条真值都不改**：
      - 那些口径外记录仍在 `/records`、工况时光机里完整可查；
      - 响应里如实带回 `skipped`（跳过了几条口径外）与 `scanned`（回看了几条），
        调用方可以据此显示"已略过 N 条"，而不是假装它们不存在。

    ★ 为什么不用「最近一条有 actual_value 的记录」代替：
      实测 10-11 那两条 upload 的 actual_value 恰好等于 3.00（回填脚本把
      pred×噪声 裁剪到 [3,8] 的截断产物），拿它当"最近化验"反而是全场最假的一个数。
      真值口径本身没问题，问题在于**跨量比较**（真值均值 vs 预测最新），故此处只统一取预测值。
    """
    lo = settings.PB_GRADE_MIN if min_value is None else min_value
    hi = settings.PB_GRADE_MAX if max_value is None else max_value

    rows = (
        db.query(
            AnalysisRecord.id,
            AnalysisRecord.group_id,
            AnalysisRecord.image_path,
            AnalysisRecord.prediction,
            AnalysisRecord.actual_value,
            AnalysisRecord.model_version,
            AnalysisRecord.source,
            AnalysisRecord.created_at,
        )
        .filter(AnalysisRecord.scene_id == scene_name)
        .order_by(AnalysisRecord.created_at.desc(), AnalysisRecord.id.desc())
        .limit(max_scan)
        .all()
    )

    scanned = 0
    skipped = 0   # 有值但落在口径外
    missing = 0   # 该字段没值
    hit = None

    for r in rows:
        scanned += 1
        pred = r.prediction if isinstance(r.prediction, dict) else {}
        raw = pred.get(field)
        if raw is None and field == "pb_grade_xrf":
            raw = pred.get("pb_grade")   # 兼容 1 条旧字段记录
        try:
            v = float(raw)
        except (TypeError, ValueError):
            missing += 1
            continue
        if lo <= v <= hi:
            hit = r
            break
        skipped += 1

    record = None
    if hit is not None:
        record = {
            "id": hit.id,
            "group_id": hit.group_id,
            "image_path": hit.image_path,
            "prediction": hit.prediction,
            "actual_value": hit.actual_value,
            "model_version": hit.model_version,
            "source": hit.source,
            "created_at": hit.created_at.isoformat() if hit.created_at else None,
        }

    return {
        "scene": scene_name,
        "field": field,
        "caliber": {"min": lo, "max": hi},
        "scanned": scanned,
        "skipped": skipped,
        "missing": missing,
        "record": record,
    }


@router.get("/api/scenes/{scene_name}/stats")
def scene_stats(scene_name: str, db: Session = Depends(get_db)):
    """场景统计：记录数 / 时间组 / 品位分布"""
    q = db.query(AnalysisRecord).filter(AnalysisRecord.scene_id == scene_name)
    total = q.count()
    if total == 0:
        raise HTTPException(404, f"场景 {scene_name} 无记录")

    groups = db.query(AnalysisRecord.group_id).filter(
        AnalysisRecord.scene_id == scene_name
    ).distinct().count()

    # 品位分布：优先从 actual_value 取，为空则从 prediction 取
    actuals = db.query(AnalysisRecord.actual_value).filter(
        AnalysisRecord.scene_id == scene_name,
        AnalysisRecord.actual_value.isnot(None),
    ).all()
    actual_values = sorted(a[0] for a in actuals)

    # 如果真值太少（<10 条），改用预测值
    if len(actual_values) < 10:
        pred_rows = db.query(AnalysisRecord.prediction).filter(
            AnalysisRecord.scene_id == scene_name,
        ).all()
        pred_values = []
        for pr in pred_rows:
            p = pr[0] if isinstance(pr, tuple) else pr
            pred_dict = p if isinstance(p, dict) else {}
            # 兼容两种字段名
            v = pred_dict.get("pb_grade_xrf") or pred_dict.get("pb_grade")
            if v is not None:
                try:
                    pred_values.append(float(v))
                except (TypeError, ValueError):
                    pass
        values = sorted(pred_values)
    else:
        values = actual_values

    n = len(values)
    distribution = {
        "min": values[0] if values else None,
        "max": values[-1] if values else None,
        "mean": round(sum(values) / n, 4) if values else None,
        "median": values[n // 2] if values else None,
        "p25": values[n // 4] if values else None,
        "p75": values[3 * n // 4] if values else None,
        "count": n,
    }

    # 时间范围
    first = q.order_by(AnalysisRecord.created_at.asc()).first()
    last = q.order_by(AnalysisRecord.created_at.desc()).first()

    return {
        "scene": scene_name,
        "total_records": total,
        "total_groups": groups,
        "date_range": {
            "from": first.created_at.isoformat() if first and first.created_at else None,
            "to": last.created_at.isoformat() if last and last.created_at else None,
        },
        "grade_distribution": distribution,
    }


@router.get("/api/scenes/{scene_name}/trend")
def scene_trend(
    scene_name: str,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """时间趋势：按日聚合的预测值/真值/记录数（仪表盘图表用）。"""
    since = datetime.utcnow() - timedelta(days=days)

    # 查询原始记录，Python 端聚合（避免 SQLite JSON 提取兼容问题）
    rows = db.query(
        AnalysisRecord.created_at,
        AnalysisRecord.prediction,
        AnalysisRecord.actual_value,
    ).filter(
        AnalysisRecord.scene_id == scene_name,
        AnalysisRecord.created_at >= since,
    ).order_by(AnalysisRecord.created_at).all()

    # 按日聚合
    daily_agg = defaultdict(lambda: {"records": 0, "pred_sum": 0.0, "actual_sum": 0.0, "actual_cnt": 0})
    for r in rows:
        dt = r.created_at.date() if r.created_at else None
        if not dt:
            continue
        key = str(dt)
        daily_agg[key]["records"] += 1
        try:
            pred = r.prediction or {}
            # 兼容两种字段名：pb_grade_xrf (XRF) / pb_grade (通用)
            pb = pred.get("pb_grade_xrf") or pred.get("pb_grade")
            if pb is not None:
                daily_agg[key]["pred_sum"] += float(pb)
            av = r.actual_value
            if av is not None:
                daily_agg[key]["actual_sum"] += float(av)
                daily_agg[key]["actual_cnt"] += 1
        except (TypeError, ValueError):
            pass

    daily = []
    for d in sorted(daily_agg.keys()):
        a = daily_agg[d]
        daily.append({
            "date": d,
            "records": a["records"],
            "avg_pred_pb": round(a["pred_sum"] / a["records"], 3) if a["records"] else None,
            "avg_actual": round(a["actual_sum"] / a["actual_cnt"], 3) if a["actual_cnt"] else None,
        })

    # 告警统计
    n_warnings = db.query(AlarmRecord).filter(
        AlarmRecord.scene_id == scene_name,
        AlarmRecord.level == "warning",
        AlarmRecord.created_at >= since,
    ).count()
    n_dangers = db.query(AlarmRecord).filter(
        AlarmRecord.scene_id == scene_name,
        AlarmRecord.level == "danger",
        AlarmRecord.created_at >= since,
    ).count()

    # 最近告警
    recent_alarms = db.query(AlarmRecord).filter(
        AlarmRecord.scene_id == scene_name,
    ).order_by(AlarmRecord.created_at.desc()).limit(10).all()

    return {
        "scene": scene_name,
        "days": days,
        "daily": daily,
        "alarm_summary": {"warning": n_warnings, "danger": n_dangers},
        "recent_alarms": [
            {
                "id": a.id,
                "level": a.level,
                "rule_name": a.rule_name,
                "message": a.message,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "is_acknowledged": a.is_acknowledged,
            }
            for a in recent_alarms
        ],
    }


@router.get("/api/models/{scene_name}")
def list_models(scene_name: str, db: Session = Depends(get_db)):
    """模型版本列表。

    注：原前端「模型档案」页（views/ModelProfile.vue）已于 2026-10-13 整体下线并删除文件，
    该接口目前无前端调用方，保留备用（模型版本号现统一由 /api/scenes 的 active_model 字段承载）。
    """
    models = db.query(ModelVersion).filter(
        ModelVersion.scene_id == scene_name
    ).order_by(ModelVersion.trained_at.desc()).all()

    return {
        "scene": scene_name,
        "models": [
            {
                "id": m.id,
                "version": m.version,
                "algorithm": m.algorithm,
                "is_active": m.is_active,
                "metrics": m.metrics,
                "training_data_count": m.training_data_count,
                "trained_at": m.trained_at.isoformat() if m.trained_at else None,
                # 员工档案字段
                "hire_date": m.hire_date.isoformat() if m.hire_date else None,
                "total_inferences": m.total_inferences,
                "health_status": m.health_status,
                "last_health_check": m.last_health_check.isoformat() if m.last_health_check else None,
            }
            for m in models
        ],
    }
