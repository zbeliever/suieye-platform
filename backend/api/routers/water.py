"""脱水工段水质监测 API

数据来源：water_readings / water_events / water_compliance_records（演示仿真数据）
+ analysis_records（scene_id='flotation'，用于"水质 ↔ 品位"联动分析）

功能方案见 docs/water-monitoring-plan.md 第四章（6 模块）。
本文件覆盖 P0（实时看板 / 异常预警）+ P1（回水可回用评分 / 水质→品位联动）。
"""

import csv
import io
import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.database import (
    AnalysisRecord,
    DosingRecord,
    WaterComplianceRecord,
    WaterEvent,
    WaterReading,
    get_db,
)

router = APIRouter()

# ============================================================
# 5 个监测点位注册表（与 scripts/seed_water_demo.py 保持一致）
# ============================================================
WATER_STATIONS = [
    {
        "id": "W1", "name": "铅精矿浓密机溢流", "short": "铅浓密溢流",
        "zone": "铅精矿脱水区", "focus": "SS · Pb 离子 · 残留捕收剂",
        "base": dict(ph=9.2, turbidity=22, ss=90, cod=45, pb=0.18, zn=0.35, cu=0.08, flow=210),
    },
    {
        "id": "W2", "name": "锌精矿浓密机溢流", "short": "锌浓密溢流",
        "zone": "锌精矿脱水区", "focus": "SS · Zn 离子 · pH",
        "base": dict(ph=10.4, turbidity=30, ss=120, cod=55, pb=0.10, zn=0.72, cu=0.12, flow=185),
    },
    {
        "id": "W3", "name": "尾矿浓密机溢流", "short": "尾矿浓密溢流",
        "zone": "尾矿脱水区", "focus": "SS（跑浑主战场）· PAM 残留 · 重金属",
        "base": dict(ph=9.8, turbidity=42, ss=165, cod=62, pb=0.22, zn=0.85, cu=0.15, flow=320),
    },
    {
        "id": "W4", "name": "精矿过滤机滤液", "short": "精矿滤液",
        "zone": "精矿过滤区", "focus": "浊度 · COD · 絮凝剂",
        "base": dict(ph=8.6, turbidity=35, ss=80, cod=70, pb=0.12, zn=0.40, cu=0.06, flow=95),
    },
    {
        "id": "W5", "name": "回水池", "short": "回水池",
        "zone": "回水汇合点", "focus": "综合水质 → 回用决策",
        "base": dict(ph=9.9, turbidity=45, ss=180, cod=68, pb=0.24, zn=0.95, cu=0.17, flow=810),
    },
]
_STATION_MAP = {s["id"]: s for s in WATER_STATIONS}

# ============================================================
# 参数口径（阈值 + 标签 + 单位）
# ============================================================
PARAM_META = {
    "ph":        dict(label="pH", unit="", warn=11.5, danger=12.0, limit=12.0, group="基础"),
    "turbidity": dict(label="浊度", unit="NTU", warn=50, danger=100, limit=100, group="基础"),
    "ss":        dict(label="悬浮物 SS", unit="mg/L", warn=200, danger=300, limit=300, group="基础"),
    "cod":       dict(label="COD", unit="mg/L", warn=80, danger=150, limit=150, group="基础"),
    "pb":        dict(label="总铅", unit="mg/L", warn=0.3, danger=0.5, limit=0.5, group="重金属"),
    "zn":        dict(label="总锌", unit="mg/L", warn=1.0, danger=1.5, limit=1.5, group="重金属"),
    "cu":        dict(label="总铜", unit="mg/L", warn=0.3, danger=0.5, limit=0.5, group="重金属"),
    "flow":      dict(label="流量", unit="m³/h", warn=None, danger=None, limit=None, group="工况"),
    "residual_reagent": dict(label="残留药剂", unit="mg/L", warn=50, danger=75, limit=None, group="派生"),
}
_METRIC_ORDER = ["ph", "turbidity", "ss", "cod", "pb", "zn", "cu", "flow", "residual_reagent"]

_LEVEL_CN = {"normal": "正常", "warning": "预警", "danger": "危险"}

# 合规对标参数（GB 25466 口径，限值见 PARAM_META.limit）
_COMPLIANCE_PARAMS = ["ph", "ss", "cod", "pb", "zn", "cu"]


def _level_of(param: str, value) -> str:
    """按阈值给单参数分级"""
    meta = PARAM_META.get(param)
    if not meta or value is None or meta.get("danger") is None:
        return "normal"
    if value >= meta["danger"]:
        return "danger"
    if value >= meta["warn"]:
        return "warning"
    return "normal"


def _reading_dict(r: WaterReading) -> dict:
    """读数 → 前端结构（原始值 + 分级）"""
    params = {}
    for p in _METRIC_ORDER:
        v = getattr(r, p, None)
        params[p] = {
            "value": v,
            "level": _level_of(p, v),
            "label": PARAM_META[p]["label"],
            "unit": PARAM_META[p]["unit"],
            "warn": PARAM_META[p]["warn"],
            "danger": PARAM_META[p]["danger"],
        }
    return {"id": r.id, "ts": r.created_at.isoformat() if r.created_at else None, "params": params}


def _param_score(param: str, v: float) -> float:
    """单参数评分 0~100（100 = 最优）"""
    if v is None:
        return 100.0
    if param == "ph":
        if 8.0 <= v <= 11.0:
            return 100.0
        if v > 11.0:
            return max(0.0, 100.0 - (v - 11.0) / (12.0 - 11.0) * 100.0)
        return max(0.0, 100.0 - (8.0 - v) / (8.0 - 6.0) * 100.0)
    meta = PARAM_META.get(param)
    if not meta or meta.get("danger") is None:
        return 100.0
    w, d = meta["warn"], meta["danger"]
    good = w * 0.5
    if v <= good:
        return 100.0
    if v <= w:
        return 100.0 - (v - good) / (w - good) * 40.0   # 100 → 60
    if v <= d:
        return 60.0 - (v - w) / (d - w) * 60.0          # 60 → 0
    return 0.0


def _reuse_index(reading: WaterReading) -> dict:
    """回水可回用评分（多因子加权）★P1 创新

    权重：SS 30% + pH 20% + 药剂残留 25% + 重金属 25%
    """
    ss_s = _param_score("ss", reading.ss)
    ph_s = _param_score("ph", reading.ph)
    cod_s = _param_score("cod", reading.cod)
    reagent_s = _param_score("residual_reagent", reading.residual_reagent)
    reagent_factor = (cod_s * 0.5 + reagent_s * 0.5)      # COD 与残留药剂各半
    heavy_parts = [_param_score("pb", reading.pb), _param_score("zn", reading.zn), _param_score("cu", reading.cu)]
    heavy_s = min(heavy_parts) if heavy_parts else 100.0  # 取最差重金属为主导

    total = ss_s * 0.30 + ph_s * 0.20 + reagent_factor * 0.25 + heavy_s * 0.25

    if total >= 85:
        ratio, advice = 90, "水质优良，可按高比例回用，无需分流"
    elif total >= 70:
        ratio, advice = 75, "水质良好，正常回用，关注残留药剂累积趋势"
    elif total >= 50:
        ratio, advice = 55, "水质一般，建议降至 55% 回用，其余用新水补充"
    elif total >= 30:
        ratio, advice = 35, "水质偏差，建议分流 65% 至沉淀池，仅小比例回用"
    else:
        ratio, advice = 15, "水质差，建议全部切至沉淀/处理系统，暂停回用"

    return {
        "score": round(total, 1),
        "level": "good" if total >= 85 else "fair" if total >= 70 else "poor" if total >= 50 else "bad" if total >= 30 else "critical",
        "level_cn": "优良" if total >= 85 else "良好" if total >= 70 else "一般" if total >= 50 else "偏差" if total >= 30 else "差",
        "suggested_reuse_ratio": ratio,
        "advice": advice,
        "factors": [
            {"key": "ss", "label": "悬浮物 SS", "weight": 0.30, "score": round(ss_s, 1),
             "value": reading.ss, "unit": "mg/L"},
            {"key": "ph", "label": "pH", "weight": 0.20, "score": round(ph_s, 1),
             "value": reading.ph, "unit": ""},
            {"key": "reagent", "label": "药剂残留", "weight": 0.25, "score": round(reagent_factor, 1),
             "value": reading.residual_reagent, "unit": "mg/L"},
            {"key": "heavy", "label": "重金属", "weight": 0.25, "score": round(heavy_s, 1),
             "value": None, "unit": "mg/L"},
        ],
    }


def _latest_reading(db: Session, station_id: str):
    return (
        db.query(WaterReading)
        .filter(WaterReading.station_id == station_id)
        .order_by(WaterReading.created_at.desc())
        .first()
    )


# ============================================================
# 模块 1 —— 实时监测看板：点位快照
# ============================================================
@router.get("/api/water/stations")
def list_stations(db: Session = Depends(get_db)):
    """5 个点位实时快照（8 参数 + 状态 + 回用评分）"""
    now = datetime.utcnow()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)

    out = []
    for st in WATER_STATIONS:
        r = _latest_reading(db, st["id"])
        if not r:
            out.append({**{k: st[k] for k in ("id", "name", "short", "zone", "focus")},
                        "status": "offline", "status_cn": "离线", "today_count": 0,
                        "latest": None, "reuse": None})
            continue
        rd = _reading_dict(r)
        # 点位状态 = 各参数取最严重级别
        lv = "normal"
        for p, d in rd["params"].items():
            if d["level"] == "danger":
                lv = "danger"
                break
            if d["level"] == "warning" and lv == "normal":
                lv = "warning"
        # 今日读数条数
        cnt = (
            db.query(func.count(WaterReading.id))
            .filter(WaterReading.station_id == st["id"], WaterReading.created_at >= today0)
            .scalar()
        ) or 0
        out.append({
            **{k: st[k] for k in ("id", "name", "short", "zone", "focus")},
            "status": lv,
            "status_cn": _LEVEL_CN[lv],
            "today_count": cnt,
            "latest": rd,
            "reuse": _reuse_index(r),
        })

    return {"stations": out, "count": len(out)}


# ============================================================
# 模块 1 —— 单点位历史趋势（自适应采样）
# ============================================================
@router.get("/api/water/stations/{station_id}/history")
def station_history(
    station_id: str,
    hours: int = Query(24, ge=1, le=720),
    metric: str = "all",
    db: Session = Depends(get_db),
):
    """单点位历史趋势，按时间范围自适应采样（避免返回上千点压垮图表）"""
    if station_id not in _STATION_MAP:
        raise HTTPException(404, f"点位不存在: {station_id}")
    if metric != "all" and metric not in PARAM_META:
        raise HTTPException(400, f"未知指标: {metric}")

    since = datetime.utcnow() - timedelta(hours=hours)
    rows = (
        db.query(WaterReading)
        .filter(WaterReading.station_id == station_id, WaterReading.created_at >= since)
        .order_by(WaterReading.created_at.asc())
        .all()
    )

    # 自适应采样：目标点数按时间范围给出
    target = 72 if hours <= 6 else 96 if hours <= 48 else 84
    if len(rows) > target:
        stride = max(1, len(rows) // target)
        sampled = rows[::stride]
        if sampled and sampled[-1] is not rows[-1]:
            sampled.append(rows[-1])
    else:
        sampled = rows

    metrics = _METRIC_ORDER if metric == "all" else [metric]
    series = []
    for r in sampled:
        item = {"ts": r.created_at.isoformat()}
        for m in metrics:
            item[m] = getattr(r, m, None)
        series.append(item)

    thresholds = {m: {"warn": PARAM_META[m]["warn"], "danger": PARAM_META[m]["danger"],
                      "label": PARAM_META[m]["label"], "unit": PARAM_META[m]["unit"]}
                  for m in metrics}

    return {
        "station_id": station_id,
        "station_name": _STATION_MAP[station_id]["name"],
        "hours": hours,
        "metric": metric,
        "metrics": metrics,
        "thresholds": thresholds,
        "points": len(series),
        "series": series,
    }


# ============================================================
# 模块 2 —— 回水可回用评分 ★P1
# ============================================================
@router.get("/api/water/reuse-index")
def reuse_index(station_id: str = "W5", db: Session = Depends(get_db)):
    """回水可回用评分 + 因子分解 + 建议回用比例（默认取回水池 W5）"""
    if station_id not in _STATION_MAP:
        raise HTTPException(404, f"点位不存在: {station_id}")
    r = _latest_reading(db, station_id)
    if not r:
        raise HTTPException(404, f"点位无数据: {station_id}")

    idx = _reuse_index(r)

    # 近 7 天评分趋势（每日均值近似）
    since = datetime.utcnow() - timedelta(days=7)
    rows = (
        db.query(WaterReading)
        .filter(WaterReading.station_id == station_id, WaterReading.created_at >= since)
        .order_by(WaterReading.created_at.asc())
        .all()
    )
    buckets = {}
    for row in rows:
        buckets.setdefault(row.created_at.strftime("%Y-%m-%d"), []).append(row)
    trend = []
    for d in sorted(buckets):
        grp = buckets[d]
        avg_score = sum(_reuse_index(x)["score"] for x in grp) / len(grp)
        trend.append({"date": d, "score": round(avg_score, 1)})

    return {
        "station_id": station_id,
        "station_name": _STATION_MAP[station_id]["name"],
        "ts": r.created_at.isoformat(),
        **idx,
        "trend_7d": trend,
    }


# ============================================================
# 模块 3 —— 水质 → 品位联动 ★P1
# ============================================================
def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    return round(num / den, 3) if den else None


@router.get("/api/water/correlation")
def water_correlation(
    days: int = Query(30, ge=7, le=120),
    station_id: str = "W5",
    max_lag: int = Query(3, ge=0, le=7),
    db: Session = Depends(get_db),
):
    """水质指标 ↔ 浮选品位的相关性分析 + 滞后互相关

    做法：水质与浮选都按日聚合 → 对 lag 0..max_lag 天做 Pearson 相关，
    找 |r| 最大的滞后天数，量化"回水水质对精矿品位的影响"。
    """
    if station_id not in _STATION_MAP:
        raise HTTPException(404, f"点位不存在: {station_id}")

    since = datetime.utcnow() - timedelta(days=days + max_lag + 1)

    # 水质日聚合
    w_rows = (
        db.query(
            func.date(WaterReading.created_at),
            func.avg(WaterReading.cod),
            func.avg(WaterReading.ss),
            func.avg(WaterReading.turbidity),
            func.avg(WaterReading.cu),
            func.avg(WaterReading.zn),
        )
        .filter(WaterReading.station_id == station_id, WaterReading.created_at >= since)
        .group_by(func.date(WaterReading.created_at))
        .all()
    )
    water_by_day = {
        str(d): dict(cod=c, ss=s, turbidity=t, cu=cu, zn=z)
        for d, c, s, t, cu, z in w_rows
    }

    # 浮选日聚合
    f_rows = (
        db.query(
            func.date(AnalysisRecord.created_at),
            func.avg(func.json_extract(AnalysisRecord.prediction, "$.pb_grade_xrf")),
            func.avg(func.json_extract(AnalysisRecord.prediction, "$.zn_grade_xrf")),
        )
        .filter(AnalysisRecord.scene_id == "flotation", AnalysisRecord.created_at >= since)
        .group_by(func.date(AnalysisRecord.created_at))
        .all()
    )
    grade_by_day = {}
    for d, pb, zn in f_rows:
        # 剔除 upload 真模型输出的口径外离群值（HANDOFF：Pb 真实范围 3~8%）
        if pb is not None and 3.0 <= pb <= 8.0:
            grade_by_day[str(d)] = dict(pb=pb, zn=zn)

    # 对齐日期
    common = sorted(set(water_by_day) & set(grade_by_day))
    if len(common) < 5:
        return {"available": False, "reason": "重叠数据不足，无法做相关性分析",
                "overlap_days": len(common)}

    # 按日排序的连续序列（用于滞后位移）
    all_days = sorted(set(water_by_day) | set(grade_by_day))
    day_index = {d: i for i, d in enumerate(all_days)}

    pairs_water = ["cod", "ss", "turbidity", "cu"]
    results = []
    for wm in pairs_water:
        # 对每个 lag：水位 mv 对应品位日 +lag
        best = None
        lag_series = []
        for lag in range(0, max_lag + 1):
            xs, ys = [], []
            for d in common:
                i = day_index[d]
                if i + lag >= len(all_days):
                    continue
                d2 = all_days[i + lag]
                if d2 not in grade_by_day:
                    continue
                xs.append(water_by_day[d][wm])
                ys.append(grade_by_day[d2]["pb"])
            r = _pearson(xs, ys)
            lag_series.append({"lag_days": lag, "r": r, "n": len(xs)})
            if r is not None and (best is None or abs(r) > abs(best["r"])):
                best = {"lag_days": lag, "r": r, "n": len(xs)}
        results.append({
            "water_metric": wm,
            "label": PARAM_META[wm]["label"],
            "unit": PARAM_META[wm]["unit"],
            "best": best,
            "by_lag": lag_series,
        })

    # 散点数据（lag=0，用于前端画散点）—— 选 |r| 最大的指标
    best_metric = None
    best_abs = -1
    for item in results:
        if item["best"] and abs(item["best"]["r"]) > best_abs:
            best_abs = abs(item["best"]["r"])
            best_metric = item
    scatter = []
    if best_metric:
        lag = best_metric["best"]["lag_days"]
        for d in common:
            i = day_index[d]
            if i + lag >= len(all_days):
                continue
            d2 = all_days[i + lag]
            if d2 not in grade_by_day:
                continue
            scatter.append({
                "date": d,
                "x": water_by_day[d][best_metric["water_metric"]],
                "y": grade_by_day[d2]["pb"],
            })

    # 结论话术（明确标注为相关性，不宣称因果）
    conclusion = None
    if best_metric and best_metric["best"]:
        b = best_metric["best"]
        direction = "负" if b["r"] < 0 else "正"
        strength = "强" if abs(b["r"]) >= 0.7 else "中等" if abs(b["r"]) >= 0.4 else "弱"
        conclusion = (
            f"{best_metric['label']}与铅品位呈{strength}{direction}相关"
            f"（r={b['r']:+.2f}，样本 {b['n']} 天），"
            f"最优滞后 {b['lag_days']} 天。"
        )
        if b["r"] < -0.4:
            conclusion += (
                f"即{best_metric['label']}升高后约 {b['lag_days']} 天，"
                "精矿铅品位趋于下降——与文献所述回水悬浮物/药剂残留影响浮选选择性的机理方向一致。"
            )
        conclusion += "（统计相关，非因果结论）"

    return {
        "available": True,
        "station_id": station_id,
        "station_name": _STATION_MAP[station_id]["name"],
        "days": days,
        "overlap_days": len(common),
        "results": results,
        "best_metric": best_metric["water_metric"] if best_metric else None,
        "scatter": scatter,
        "conclusion": conclusion,
    }


# ============================================================
# 模块 4 —— 异常事件列表
# ============================================================
@router.get("/api/water/events")
def list_events(
    level: str = None,
    event_type: str = None,
    hours: int = Query(720, ge=1, le=8760),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """水质异常事件（跑浑 / 药剂累积 / 高碱冲击 / 铜离子富集）"""
    # 手动校验（避免空串撞校验 —— 见 HANDOFF 已知坑 11）
    if level == "":
        level = None
    if level and level not in ("info", "warning", "danger"):
        raise HTTPException(400, f"非法 level: {level}")
    if event_type == "":
        event_type = None

    since = datetime.utcnow() - timedelta(hours=hours)
    q = db.query(WaterEvent).filter(WaterEvent.created_at >= since)
    if level:
        q = q.filter(WaterEvent.level == level)
    if event_type:
        q = q.filter(WaterEvent.event_type == event_type)

    total = q.count()
    events = q.order_by(WaterEvent.created_at.desc()).limit(limit).all()

    by_level = dict(
        db.query(WaterEvent.level, func.count(WaterEvent.id))
        .filter(WaterEvent.created_at >= since)
        .group_by(WaterEvent.level)
        .all()
    )

    return {
        "items": [
            {
                "id": e.id,
                "station_id": e.station_id,
                "station_name": _STATION_MAP.get(e.station_id, {}).get("name", e.station_id),
                "event_type": e.event_type,
                "level": e.level,
                "level_cn": {"info": "提示", "warning": "预警", "danger": "危险"}.get(e.level, e.level),
                "title": e.title,
                "detail": e.detail,
                "metric": e.metric,
                "metric_label": PARAM_META.get(e.metric, {}).get("label", e.metric),
                "peak_value": e.peak_value,
                "baseline_value": e.baseline_value,
                "unit": PARAM_META.get(e.metric, {}).get("unit", ""),
                "acknowledged": e.acknowledged,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
        "total": total,
        "by_level": by_level,
    }


# ============================================================
# 模块 5 —— 合规统计与导出
# ============================================================
@router.get("/api/water/compliance")
def compliance_summary(
    date: str = None,
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """合规统计：按日 × 点位 × 参数的超标次数与时长（对标 GB 25466）"""
    if date == "":
        date = None
    if date:
        rows = (
            db.query(WaterComplianceRecord)
            .filter(WaterComplianceRecord.date == date)
            .order_by(WaterComplianceRecord.exceed_count.desc(), WaterComplianceRecord.station_id, WaterComplianceRecord.param)
            .all()
        )
        dates = [date]
    else:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = (
            db.query(WaterComplianceRecord)
            .filter(WaterComplianceRecord.date >= since)
            # 超标行优先，其次按日期倒序 —— 保证最值得看的明细排在最前
            .order_by(WaterComplianceRecord.exceed_count.desc(), WaterComplianceRecord.date.desc())
            .all()
        )
        dates = sorted({r.date for r in rows}, reverse=True)

    items = [
        {
            "date": r.date,
            "station_id": r.station_id,
            "station_name": _STATION_MAP.get(r.station_id, {}).get("name", r.station_id),
            "param": r.param,
            "param_label": PARAM_META.get(r.param, {}).get("label", r.param),
            "unit": PARAM_META.get(r.param, {}).get("unit", ""),
            "avg_value": round(r.avg_value, 2) if r.avg_value is not None else None,
            "max_value": round(r.max_value, 2) if r.max_value is not None else None,
            "limit_value": r.limit_value,
            "exceed_count": r.exceed_count,
            "exceed_hours": r.exceed_hours,
        }
        for r in rows
    ]

    total_exceed = sum(1 for i in items if i["exceed_count"] > 0)
    total_hours = round(sum(i["exceed_hours"] for i in items), 1)

    # 按参数汇总
    by_param = {}
    for i in items:
        b = by_param.setdefault(i["param"], {"param_label": i["param_label"], "exceed_count": 0, "exceed_hours": 0.0})
        b["exceed_count"] += i["exceed_count"]
        b["exceed_hours"] = round(b["exceed_hours"] + i["exceed_hours"], 1)

    return {
        "date": date,
        "dates": dates,
        "items": items,
        "summary": {
            "record_count": len(items),
            "exceed_records": total_exceed,
            "exceed_hours": total_hours,
            "params": list(by_param.values()),
        },
        "standard": "GB 25466 铅锌工业污染物排放标准（限值以现行版本为准）",
    }


@router.get("/api/water/compliance/export")
def compliance_export(
    format: str = "csv",
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """合规报表导出（csv / xlsx）"""
    data = compliance_summary(date=None, days=days, db=db)
    items = data["items"]
    headers = ["日期", "点位", "参数", "日均值", "最大值", "标准限值", "超标次数", "超标时长(h)"]

    if format == "xlsx":
        try:
            from openpyxl import Workbook
        except ImportError:
            raise HTTPException(500, "服务器未安装 openpyxl，无法导出 Excel，请改用 format=csv")
        wb = Workbook()
        ws = wb.active
        ws.title = "水质合规报表"
        ws.append(headers)
        for i in items:
            ws.append([i["date"], i["station_name"], i["param_label"], i["avg_value"],
                       i["max_value"], i["limit_value"], i["exceed_count"], i["exceed_hours"]])
        buf = io.BytesIO()
        wb.save(buf)
        return Response(
            content=buf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="water_compliance_{datetime.utcnow():%Y%m%d}.xlsx"'},
        )

    # CSV（UTF-8 BOM，Excel 直接打开不乱码）
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for i in items:
        w.writerow([i["date"], i["station_name"], i["param_label"], i["avg_value"],
                    i["max_value"], i["limit_value"], i["exceed_count"], i["exceed_hours"]])
    content = "\ufeff" + buf.getvalue()
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="water_compliance_{datetime.utcnow():%Y%m%d}.csv"'},
    )


# ============================================================
# 模块 6 —— 药剂联动建议
# ============================================================
@router.get("/api/water/dosing-advice")
def dosing_advice(station_id: str = "W5", db: Session = Depends(get_db)):
    """由回水残留药剂反推 → 建议减少新药剂投加★（与配药模块联动）"""
    if station_id not in _STATION_MAP:
        raise HTTPException(404, f"点位不存在: {station_id}")
    r = _latest_reading(db, station_id)
    if not r:
        raise HTTPException(404, f"点位无数据: {station_id}")

    cod = r.cod or 0
    reagent = r.residual_reagent or 0

    # 残留药剂 → 等效新药剂节约比例（文献：回水残留可比新水高 50~100 倍，
    # 循环得当可省药剂，东塘子铅锌矿案例药剂总用量降 51%）
    if reagent >= 75:
        save = 22
        level = "high"
        note = "回水残留药剂偏高，可显著减少新药剂投加，但需警惕起泡剂过量致泡沫溢槽"
    elif reagent >= 50:
        save = 14
        level = "medium"
        note = "回水残留药剂中等，建议按 12~16% 比例下调新药剂投加量"
    elif reagent >= 30:
        save = 7
        level = "low"
        note = "回水残留药剂较低，小幅下调新药剂投加量即可"
    else:
        save = 2
        level = "minimal"
        note = "回水残留药剂低，药剂用量基本按常规执行"

    # 关联最近一条配药记录（供前端展示对照）
    last_dosing = db.query(DosingRecord).order_by(DosingRecord.created_at.desc()).first()
    dosing_ref = None
    if last_dosing:
        dosing_ref = {
            "id": last_dosing.id,
            "ore_type": last_dosing.ore_type,
            "current_ph": last_dosing.current_ph,
            "target_pb_grade": last_dosing.target_pb_grade,
            "created_at": last_dosing.created_at.isoformat() if last_dosing.created_at else None,
        }

    est_collector = round(cod * 0.16, 2)     # 残留捕收剂估算 mg/L
    est_frother = round(cod * 0.09, 2)       # 残留起泡剂估算 mg/L

    return {
        "station_id": station_id,
        "station_name": _STATION_MAP[station_id]["name"],
        "ts": r.created_at.isoformat(),
        "cod": cod,
        "residual_reagent": reagent,
        "estimated_collector_mgl": est_collector,
        "estimated_frother_mgl": est_frother,
        "saving_level": level,
        "suggested_saving_pct": save,
        "note": note,
        "dosing_reference": dosing_ref,
        "caveat": "残留药剂浓度由 COD 与起泡性倾向反推，为估算值；实际投加调整需结合现场试验确认。",
    }


# ============================================================
# 概览（给直播墙节点 / 仪表盘卡用）
# ============================================================
@router.get("/api/water/overview")
def water_overview(db: Session = Depends(get_db)):
    """水质总览：点位数 / 异常点位数 / 平均回用评分 / 今日事件数 / 是否跑浑"""
    now = datetime.utcnow()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)

    statuses = []
    scores = []
    for st in WATER_STATIONS:
        r = _latest_reading(db, st["id"])
        if not r:
            continue
        rd = _reading_dict(r)
        lv = "normal"
        for p, d in rd["params"].items():
            if d["level"] == "danger":
                lv = "danger"
                break
            if d["level"] == "warning" and lv == "normal":
                lv = "warning"
        statuses.append(lv)
        scores.append(_reuse_index(r)["score"])

    events_today = (
        db.query(func.count(WaterEvent.id)).filter(WaterEvent.created_at >= today0).scalar()
    ) or 0
    events_7d = (
        db.query(func.count(WaterEvent.id))
        .filter(WaterEvent.created_at >= now - timedelta(days=7))
        .scalar()
    ) or 0

    avg_score = round(sum(scores) / len(scores), 1) if scores else None
    # 流量合计（回用水量）
    total_flow = 0.0
    for st in WATER_STATIONS:
        r = _latest_reading(db, st["id"])
        if r and r.flow:
            total_flow += r.flow

    return {
        "station_count": len(WATER_STATIONS),
        "abnormal_count": sum(1 for s in statuses if s != "normal"),
        "danger_count": sum(1 for s in statuses if s == "danger"),
        "avg_reuse_score": avg_score,
        "reuse_score_level": "good" if avg_score and avg_score >= 85 else "fair" if avg_score and avg_score >= 70
                            else "poor" if avg_score and avg_score >= 50 else "bad",
        "events_today": events_today,
        "events_7d": events_7d,
        "total_flow_m3h": round(total_flow, 1),
        "updated_at": now.isoformat(),
    }
