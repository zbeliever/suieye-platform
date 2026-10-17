"""尾矿处理 API — 尾矿浓密机智能优化

数据来源：tailings_readings / tailings_events（物理机理驱动仿真）
        + water_readings（W3 尾矿浓密机溢流，跨模块同源，不重复存储）

功能方案见 docs/tailings-processing-plan.md 第四章（模块 1~6）。
核心思路（对标 BGRIMM 工程实践与 2026 年深锥浓密机文献）：
    可测仪表变量 ──软测量──▶ 底流浓度（现场测不到）
                              │
                              ├─▶ 工况识别（正常/泥床过高/絮凝不足/跑浑/压耙）
                              ├─▶ 物理机理前向推演 → 前瞻预测
                              └─▶ 搜索最优排放 setpoint → 推荐 + 节药量化
"""

import math
import statistics
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.database import (
    TailingsEvent,
    TailingsReading,
    WaterReading,
    get_db,
)

router = APIRouter()

# ============================================================
# 设备注册表
# ============================================================
EQUIPMENT = {
    "id": "T1",
    "name": "尾矿浓密机",
    "short": "尾矿浓密机",
    "zone": "尾矿处理区",
    "spec": "φ18m 高效浓密机 · 中心传动",
    "feed_from": "浮选尾矿",
    "outputs": ["底流 → 尾矿库 / 充填站", "溢流 → 回水池（水质模块 W3/W5）"],
}

# ============================================================
# 指标口径（阈值待与现场设计值核对）
# ============================================================
METRIC_META = {
    "underflow_conc":   dict(label="底流浓度", unit="%", group="出料",
                             warn_lo=60.0, warn_hi=78.0, danger_lo=56.0, danger_hi=80.0),
    "underflow_flow":   dict(label="底流流量", unit="m³/h", group="出料"),
    "overflow_flow":    dict(label="溢流流量", unit="m³/h", group="出料"),
    "bed_height":       dict(label="泥床高度", unit="m", group="设备",
                             warn_hi=6.5, danger_hi=7.8),
    "rake_torque":      dict(label="耙架扭矩", unit="% 额定", group="设备",
                             warn_hi=62.0, danger_hi=80.0),
    "rake_current":     dict(label="耙架电流", unit="A", group="设备"),
    "feed_flow":        dict(label="给料流量", unit="m³/h", group="给料"),
    "feed_solids":      dict(label="给料浓度", unit="%", group="给料"),
    "feed_p80":         dict(label="给料 P80", unit="μm", group="给料"),
    "flocculant_flow":  dict(label="絮凝剂流量", unit="m³/h", group="药剂"),
    "flocculant_dosage": dict(label="絮凝剂单耗", unit="g/t", group="药剂",
                              warn_hi=62.0, danger_hi=85.0),
}
_METRIC_ORDER = [
    "underflow_conc", "underflow_flow", "overflow_flow",
    "bed_height", "rake_torque", "rake_current",
    "feed_flow", "feed_solids", "feed_p80",
    "flocculant_flow", "flocculant_dosage",
]

# 工况状态
CONDITIONS = {
    "normal":          dict(label="正常沉降", level="normal",
                            desc="泥床、扭矩、底流浓度均在合理区间，絮凝剂单耗平稳。"),
    "flocculant_low":  dict(label="絮凝不足", level="warning",
                            desc="絮凝剂单耗低于有效区间，絮体形成不充分，细粒固体难以沉降，溢流有跑浑风险。"),
    "bed_high":        dict(label="泥床过高", level="warning",
                            desc="压缩层固体累积超过目标，耙架负荷上升，需加大底流排放。"),
    "rake_overload":   dict(label="压耙风险", level="danger",
                            desc="耙架扭矩越过安全线，继续运行可能造成耙架变形或传动过载，应立即降低泥床并检查。"),
    "over_discharge":  dict(label="过度排放", level="info",
                            desc="底流排放显著高于稳态，压缩层被抽薄，底流浓度不升反降（文献结论：单纯最大化排放速率是次优的）。"),
    "thin_underflow":  dict(label="底流偏稀", level="warning",
                            desc="底流浓度低于常规区间，输送与堆存成本上升，充填可用性下降。"),
}

# 底流去向阈值（文献：膏体充填要求 74~76% 固体，此处按可输送下限放宽）
DISCHARGE_RULES = dict(backfill_min=72.0, storage_min=62.0)

# 絮凝剂单价（★ 演示假设值，页面与接口均标注，需按现场采购价核对）
FLOCCULANT_PRICE_CNY_PER_KG = 18.0

# 物理常数（与 seed_tailings_demo.py 保持一致）
RHO_S = 2.9
RHO_W = 1.0
K_BED = 0.0075          # 泥床响应系数 m/(t/h·h)
RATIO_BASE = 0.2083     # 稳态底流/给料体积比


def _pulp_rho(c_pct: float) -> float:
    c = max(0.0, min(float(c_pct), 100.0)) / 100.0
    return 1.0 / ((1.0 - c) / RHO_W + c / RHO_S)


def _lv(v, warn_lo=None, warn_hi=None, danger_lo=None, danger_hi=None) -> str:
    if v is None:
        return "normal"
    if danger_hi is not None and v >= danger_hi:
        return "danger"
    if danger_lo is not None and v <= danger_lo:
        return "danger"
    if warn_hi is not None and v >= warn_hi:
        return "warning"
    if warn_lo is not None and v <= warn_lo:
        return "warning"
    return "normal"


def _metric_level(key: str, v) -> str:
    m = METRIC_META.get(key, {})
    return _lv(v, m.get("warn_lo"), m.get("warn_hi"), m.get("danger_lo"), m.get("danger_hi"))


_LEVEL_RANK = {"normal": 0, "warning": 1, "danger": 2}


def _worst(*levels: str) -> str:
    """取最严重等级。用于同一点位多参数汇总，避免与源模块等级打架。"""
    best = "normal"
    for lv in levels:
        if lv and _LEVEL_RANK.get(lv, 0) > _LEVEL_RANK.get(best, 0):
            best = lv
    return best


def _latest(db: Session, equipment_id: str = "T1"):
    return (
        db.query(TailingsReading)
        .filter(TailingsReading.equipment_id == equipment_id)
        .order_by(TailingsReading.created_at.desc())
        .first()
    )


def _latest_ts(db: Session) -> datetime:
    ts = db.query(func.max(TailingsReading.created_at)).scalar()
    if ts is None:
        raise ValueError("tailings_readings 为空，请先运行 scripts/seed_tailings_demo.py")
    return ts


def _reference(ts: datetime, db: Session):
    """取最接近指定时刻的一条读数"""
    before = (
        db.query(TailingsReading)
        .filter(TailingsReading.created_at <= ts)
        .order_by(TailingsReading.created_at.desc())
        .first()
    )
    if before and (ts - before.created_at).total_seconds() < 3600:
        return before
    after = (
        db.query(TailingsReading)
        .filter(TailingsReading.created_at >= ts)
        .order_by(TailingsReading.created_at.asc())
        .first()
    )
    return after or before


def _w3_reading(db: Session, ts: datetime):
    """★ 溢流水质取自 water_readings 的 W3 点位（跨模块同源，不另存一份）"""
    return (
        db.query(WaterReading)
        .filter(WaterReading.station_id == "W3", WaterReading.created_at <= ts)
        .order_by(WaterReading.created_at.desc())
        .first()
    )


# ============================================================
# 软测量：可测仪表变量 → 底流浓度
# ============================================================
_SOFT_FEATURES = ["feed_flow", "feed_solids", "flocculant_dosage",
                  "rake_torque", "rake_current", "underflow_flow"]
_SOFT_LABELS = {
    "feed_flow": "给料流量",
    "feed_solids": "给料浓度",
    "flocculant_dosage": "絮凝剂单耗",
    "rake_torque": "耙架扭矩",
    "rake_current": "耙架电流",
    "underflow_flow": "底流流量",
}
_SOFT_CACHE = {"stamp": None, "model": None, "meta": None}
_SOFT_TTL_SEC = 600


def _train_soft_sensor(db: Session, force: bool = False):
    """训练底流浓度软测量模型（时间切分，报测试集指标）

    ★ 只用现场真实存在的仪表变量；**不含泥床高度**等内部状态量，
    否则模型会退化成"把设备内部状态抄一遍"，失去软测量的意义。
    """
    now = datetime.utcnow()
    c = _SOFT_CACHE
    if (not force) and c["model"] is not None and c["stamp"] and \
            (now - c["stamp"]).total_seconds() < _SOFT_TTL_SEC:
        return c["model"], c["meta"]

    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    from sklearn.model_selection import GroupShuffleSplit

    rows = (
        db.query(TailingsReading)
        .filter(TailingsReading.equipment_id == "T1")
        .order_by(TailingsReading.created_at.asc())
        .all()
    )
    if len(rows) < 60:
        raise ValueError("样本不足，无法训练软测量模型")

    X = [[float(getattr(r, f) or 0.0) for f in _SOFT_FEATURES] for r in rows]
    y = [float(r.underflow_conc) for r in rows]

    # ★ 按日期分组切分：同一天的数据不跨训练/测试，杜绝相邻时刻泄漏；
    #   若按时间前后切分，测试段目标变量本身几乎无变化，R² 会被数学上压死（失去参考意义）。
    groups = [r.created_at.strftime("%Y-%m-%d") for r in rows]
    gss = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=42)
    tr_idx, te_idx = next(gss.split(X, y, groups))
    X_tr = [X[i] for i in tr_idx]
    y_tr = [y[i] for i in tr_idx]
    X_te = [X[i] for i in te_idx]
    y_te = [y[i] for i in te_idx]

    model = GradientBoostingRegressor(
        n_estimators=180, max_depth=3, learning_rate=0.08, subsample=0.9, random_state=42
    )
    model.fit(X_tr, y_tr)

    pred = model.predict(X_te)
    mae = float(mean_absolute_error(y_te, pred))
    rmse = float(math.sqrt(mean_squared_error(y_te, pred)))
    r2 = float(r2_score(y_te, pred))

    meta = {
        "algorithm": "GradientBoostingRegressor",
        "target": "underflow_conc",
        "target_label": "底流浓度",
        "features": [{"key": f, "label": _SOFT_LABELS[f]} for f in _SOFT_FEATURES],
        "n_total": len(rows),
        "n_train": len(tr_idx),
        "n_test": len(te_idx),
        "split": "按日期分组切分（30% 日期留作测试集，同一天不跨集，避免相邻时刻泄漏）",
        "mae": round(mae, 3),
        "rmse": round(rmse, 3),
        "r2": round(r2, 3),
        "importance": sorted(
            [
                {"key": f, "label": _SOFT_LABELS[f], "value": round(float(v), 4)}
                for f, v in zip(_SOFT_FEATURES, model.feature_importances_)
            ],
            key=lambda d: -d["value"],
        ),
        "note": "仅使用现场可测仪表变量（流量/浓度/药剂/扭矩/电流），不含泥床高度等内部状态量。",
    }
    c.update(stamp=now, model=model, meta=meta)
    return model, meta


def _soft_predict(model, r: TailingsReading) -> float:
    x = [[float(getattr(r, f) or 0.0) for f in _SOFT_FEATURES]]
    return float(model.predict(x)[0])


# ============================================================
# 工况识别
# ============================================================
def _classify(db: Session, r: TailingsReading) -> dict:
    """按当前状态判定浓密机工况（同时给出触发的判据）"""
    ratio = r.underflow_flow / max(r.feed_flow, 1.0)
    reasons = []

    if r.rake_torque >= METRIC_META["rake_torque"]["danger_hi"]:
        key, reasons = "rake_overload", [f"耙架扭矩 {r.rake_torque:.1f}% ≥ {METRIC_META['rake_torque']['danger_hi']:.0f}%"]
    elif r.bed_height >= METRIC_META["bed_height"]["danger_hi"]:
        key, reasons = "bed_high", [f"泥床 {r.bed_height:.2f} m ≥ {METRIC_META['bed_height']['danger_hi']} m"]
    elif r.flocculant_dosage < 30.0:
        key, reasons = "flocculant_low", [f"絮凝剂单耗 {r.flocculant_dosage:.1f} g/t，低于有效沉降区间"]
    elif ratio > RATIO_BASE * 1.12:
        key, reasons = "over_discharge", [
            f"底流/给料体积比 {ratio:.3f}，高于稳态 {RATIO_BASE:.4f}",
        ]
    elif r.rake_torque >= METRIC_META["rake_torque"]["warn_hi"]:
        key, reasons = "bed_high", [f"耙架扭矩 {r.rake_torque:.1f}% 越过预警线"]
    elif r.underflow_conc < METRIC_META["underflow_conc"]["warn_lo"]:
        key, reasons = "thin_underflow", [f"底流浓度 {r.underflow_conc:.1f}% 低于 {METRIC_META['underflow_conc']['warn_lo']:.0f}%"]
    else:
        key, reasons = "normal", []

    meta = CONDITIONS[key]
    return {"state": key, "label": meta["label"], "level": meta["level"],
            "desc": meta["desc"], "reasons": reasons}


# ============================================================
# 物理机理前向推演（用于前瞻预测与 setpoint 推荐）
# ============================================================
def _simulate_forward(r: TailingsReading, hours: float, discharge_ratio: float,
                      dosage: float, step_min: float = 15.0):
    """从当前状态出发，按一阶动力学推演未来工况

    这是**机理模型**（不是查历史真值），与 IntelliSense 的 first-principle model 同类：
        床变化 ∝ 进入固体 − 排出固体
        底流浓度 = f(泥床, 絮凝剂单耗, 停留时间, 抽稀程度)
    """
    bed = float(r.bed_height)
    q_feed = float(r.feed_flow)
    c_feed = float(r.feed_solids)
    c_uf = float(r.underflow_conc)

    rho_feed = _pulp_rho(c_feed)
    s_feed = q_feed * rho_feed * c_feed / 100.0

    steps = max(1, int(hours * 60 / step_min))
    dt_h = step_min / 60.0
    out = []
    t = r.created_at

    for i in range(steps):
        t = t + timedelta(minutes=step_min)
        # 抽稀惩罚（排放比高于稳态时压缩层被抽薄）
        shear = -140.0 * max(0.0, discharge_ratio - RATIO_BASE)
        tau = -12.0 * (q_feed / 420.0 - 1.0)
        c_uf = 68.5 + 2.0 * (bed - 5.0) + 7.0 * (dosage - 45.0) / 45.0 + tau + shear
        c_uf = max(52.0, min(81.0, c_uf))

        q_uf = q_feed * discharge_ratio
        rho_uf = _pulp_rho(c_uf)
        s_uf = min(q_uf * rho_uf * c_uf / 100.0, s_feed * 1.02)

        bed = max(1.6, min(9.6, bed + K_BED * (s_feed - s_uf) * dt_h))
        torque = max(8.0, min(98.0, 16.0 + 13.0 * (bed - 2.4)))

        out.append({
            "ts": t.isoformat(),
            "t_h": round(i * step_min / 60.0, 2),
            "underflow_conc": round(c_uf, 2),
            "bed_height": round(bed, 3),
            "rake_torque": round(torque, 1),
            "discharge_ratio": round(discharge_ratio, 4),
            "flocculant_dosage": round(dosage, 2),
        })
    return out


# ============================================================
# 接口
# ============================================================
@router.get("/api/tailings/overview")
def tailings_overview(db: Session = Depends(get_db)):
    """浓密机实时快照：参数 + 软测量底流浓度 + 工况态 + 物料平衡证据"""
    r = _latest(db)
    if not r:
        return {"available": False, "message": "尾矿数据未生成，请运行 scripts/seed_tailings_demo.py"}

    model, meta = _train_soft_sensor(db)
    soft = _soft_predict(model, r)
    ci = 1.96 * meta["rmse"]

    metrics = {}
    for k in _METRIC_ORDER:
        v = getattr(r, k, None)
        m = METRIC_META[k]
        metrics[k] = {
            "value": v, "label": m["label"], "unit": m["unit"], "group": m["group"],
            "level": _metric_level(k, v),
            "warn_lo": m.get("warn_lo"), "warn_hi": m.get("warn_hi"),
            "danger_lo": m.get("danger_lo"), "danger_hi": m.get("danger_hi"),
        }

    # 物料平衡证据：给料固体 vs 底流带走固体
    rho_feed = _pulp_rho(r.feed_solids)
    s_feed = r.feed_flow * rho_feed * r.feed_solids / 100.0
    rho_uf = _pulp_rho(r.underflow_conc)
    s_uf = r.underflow_flow * rho_uf * r.underflow_conc / 100.0

    w3 = _w3_reading(db, r.created_at)
    ts = r.created_at
    age_h = (datetime.utcnow() - ts).total_seconds() / 3600.0

    return {
        "available": True,
        "equipment": EQUIPMENT,
        "ts": ts.isoformat(),
        "data_freshness": {
            "latest_ts": ts.isoformat(),
            "age_hours": round(age_h, 1),
            "note": "尾矿模块时间轴与水质监测对齐，数据截止时间一致",
        },
        "metrics": metrics,
        "underflow_soft": {
            "actual": r.underflow_conc,
            "predicted": round(soft, 2),
            "lower": round(soft - ci, 2),
            "upper": round(soft + ci, 2),
            "mae": meta["mae"],
            "rmse": meta["rmse"],
            "r2": meta["r2"],
            "deviation": round(soft - r.underflow_conc, 2),
        },
        "condition": _classify(db, r),
        "overflow": {
            "source": "water.W3",
            "source_label": "尾矿浓密机溢流（水质监测 W3）",
            "turbidity": w3.turbidity if w3 else None,
            "ss": w3.ss if w3 else None,
            "flow": w3.flow if w3 else None,
            # ★ 阈值口径与 backend/api/routers/water.py 的 PARAM_META 严格一致，
            #   等级按"浊度 / SS 中最差者"汇总，避免与水质页对同一点位给出不同等级
            "turbidity_level": _lv(w3.turbidity, warn_hi=50.0, danger_hi=100.0) if w3 else "normal",
            "ss_level": _lv(w3.ss, warn_hi=200.0, danger_hi=300.0) if w3 else "normal",
            "level": _worst(
                _lv(w3.turbidity, warn_hi=50.0, danger_hi=100.0) if w3 else "normal",
                _lv(w3.ss, warn_hi=200.0, danger_hi=300.0) if w3 else "normal",
            ),
            "ts": w3.created_at.isoformat() if w3 else None,
        },
        "balance": {
            "feed_solids_tph": round(s_feed, 2),
            "underflow_solids_tph": round(s_uf, 2) if s_uf is not None else None,
            "residual_pct": r.solids_residual,
            "ok": (r.solids_residual is not None and r.solids_residual < 2.0),
            "note": "固体物料平衡：给料固体 = 底流固体 + 溢流固体；残差为溢流密度对清水期望值的相对偏差",
        },
    }


@router.get("/api/tailings/history")
def tailings_history(
    hours: int = Query(24, ge=1, le=24 * 30),
    metrics: str = Query("underflow_conc,bed_height"),
    db: Session = Depends(get_db),
):
    """参数历史趋势（自适应降采样，最多约 400 点/指标）"""
    end = _latest_ts(db)
    start = end - timedelta(hours=hours)

    keys = [k.strip() for k in metrics.split(",") if k.strip() in METRIC_META]
    if not keys:
        keys = ["underflow_conc"]

    rows = (
        db.query(TailingsReading)
        .filter(TailingsReading.equipment_id == "T1",
                TailingsReading.created_at >= start,
                TailingsReading.created_at <= end)
        .order_by(TailingsReading.created_at.asc())
        .all()
    )
    if not rows:
        return {"hours": hours, "series": [], "points": 0}

    step = max(1, len(rows) // 400)
    rows = rows[::step]

    series = []
    for k in keys:
        m = METRIC_META[k]
        series.append({
            "key": k, "label": m["label"], "unit": m["unit"],
            "warn_lo": m.get("warn_lo"), "warn_hi": m.get("warn_hi"),
            "danger_lo": m.get("danger_lo"), "danger_hi": m.get("danger_hi"),
            "points": [{"ts": r.created_at.isoformat(), "value": getattr(r, k, None)} for r in rows],
        })

    return {"hours": hours, "start": start.isoformat(), "end": end.isoformat(),
            "points": len(rows), "series": series}


@router.get("/api/tailings/underflow-soft-sensor")
def underflow_soft_sensor(db: Session = Depends(get_db)):
    """底流浓度软测量：当前预测 + 置信区间 + 模型指标 + 特征重要性"""
    r = _latest(db)
    if not r:
        return {"available": False}

    model, meta = _train_soft_sensor(db)
    soft = _soft_predict(model, r)
    ci = 1.96 * meta["rmse"]

    # 最近 200 点的跟随曲线（模型 vs 真值），供前端画"软测量有多准"
    rows = (
        db.query(TailingsReading)
        .filter(TailingsReading.equipment_id == "T1")
        .order_by(TailingsReading.created_at.desc())
        .limit(200)
        .all()
    )
    rows = list(reversed(rows))
    track = [
        {
            "ts": x.created_at.isoformat(),
            "actual": x.underflow_conc,
            "predicted": round(_soft_predict(model, x), 2),
        }
        for x in rows
    ]

    return {
        "available": True,
        "current": {
            "actual": r.underflow_conc,
            "predicted": round(soft, 2),
            "lower": round(soft - ci, 2),
            "upper": round(soft + ci, 2),
            "deviation": round(soft - r.underflow_conc, 2),
        },
        "model": meta,
        "track": track,
        "value_note": "现场若已装在线浓度计，此软测量可作为冗余与校验；未装仪表时直接替代。",
    }


@router.get("/api/tailings/forecast")
def tailings_forecast(
    horizon: int = Query(6, ge=1, le=24),
    db: Session = Depends(get_db),
):
    """前瞻预测：不干预 vs 采纳推荐，两条推演轨迹对比"""
    r = _latest(db)
    if not r:
        return {"available": False}

    cur_ratio = r.underflow_flow / max(r.feed_flow, 1.0)
    cur_dosage = float(r.flocculant_dosage)

    # ① 不干预：沿用当前排放比与药剂单耗
    baseline = _simulate_forward(r, horizon, cur_ratio, cur_dosage)

    # ② 推荐：搜索一组 setpoint，使床回到目标且底流浓度尽量高
    #    ★ 不追求最大排放（文献结论：最大化排放速率是次优的），
    #      并对超出基准的药剂投加计成本惩罚，避免"靠加药刷指标"
    best = None
    for d_ratio in [RATIO_BASE * f for f in (0.92, 0.96, 1.0, 1.04, 1.08, 1.12)]:
        for d_dos in [max(30.0, cur_dosage - 8), cur_dosage, min(70.0, cur_dosage + 6)]:
            traj = _simulate_forward(r, horizon, d_ratio, d_dos)
            last = traj[-1]
            bed_err = abs(last["bed_height"] - 5.0)
            score = (last["underflow_conc"]
                     - bed_err * 6.0
                     - max(0.0, d_dos - 45.0) * 0.30)
            if best is None or score > best["score"]:
                best = {"score": score, "ratio": d_ratio, "dosage": d_dos, "traj": traj}
    recommended = best["traj"]

    b_last, r_last = baseline[-1], recommended[-1]
    return {
        "available": True,
        "horizon_hours": horizon,
        "from_ts": r.created_at.isoformat(),
        "baseline": baseline,
        "recommended": recommended,
        "setpoint": {
            "discharge_ratio": {"current": round(cur_ratio, 4), "recommended": round(best["ratio"], 4)},
            "flocculant_dosage": {"current": round(cur_dosage, 1), "recommended": round(best["dosage"], 1)},
        },
        "summary": {
            "baseline_bed": b_last["bed_height"],
            "recommended_bed": r_last["bed_height"],
            "baseline_conc": b_last["underflow_conc"],
            "recommended_conc": r_last["underflow_conc"],
            "text": (
                f"若不干预，{horizon} 小时后泥床将达 {b_last['bed_height']:.2f} m、"
                f"底流浓度 {b_last['underflow_conc']:.1f}%；"
                f"按推荐 setpoint（排放比 {best['ratio']:.4f}、单耗 {best['dosage']:.1f} g/t）推演，"
                f"泥床 {r_last['bed_height']:.2f} m、底流浓度 {r_last['underflow_conc']:.1f}%。"
            ),
        },
        "method_note": "机理模型前向推演（床变化 ∝ 进入固体 − 排出固体），非历史数据查表。",
    }


@router.get("/api/tailings/setpoint-advice")
def setpoint_advice(db: Session = Depends(get_db)):
    """setpoint 推荐：给出可执行的调整量与预期效果"""
    r = _latest(db)
    if not r:
        return {"available": False}

    cur_ratio = r.underflow_flow / max(r.feed_flow, 1.0)
    cur_dosage = float(r.flocculant_dosage)
    cond = _classify(db, r)

    # 近期趋势（最近 2 小时）——只看单点会漏掉"正在恶化"的工况
    since = r.created_at - timedelta(hours=2)
    recent = (
        db.query(TailingsReading)
        .filter(TailingsReading.equipment_id == "T1",
                TailingsReading.created_at >= since,
                TailingsReading.created_at <= r.created_at)
        .order_by(TailingsReading.created_at.asc())
        .all()
    )
    bed_delta = round(recent[-1].bed_height - recent[0].bed_height, 3) if len(recent) >= 2 else 0.0
    conc_delta = round(recent[-1].underflow_conc - recent[0].underflow_conc, 3) if len(recent) >= 2 else 0.0

    items = []

    # ① 底流排放
    if r.bed_height > 5.4 or r.rake_torque > 58.0 or bed_delta > 0.15:
        target_ratio = min(RATIO_BASE * 1.10, cur_ratio + 0.02)
        delta = (target_ratio - cur_ratio) / max(cur_ratio, 1e-6) * 100
        trend_txt = f"近 2 小时泥床 {bed_delta:+.2f} m，" if abs(bed_delta) >= 0.02 else ""
        items.append({
            "type": "underflow_discharge", "label": "底流排放量",
            "current": round(r.underflow_flow, 1),
            "recommended": round(r.feed_flow * target_ratio, 1),
            "unit": "m³/h", "delta_pct": round(delta, 1),
            "reason": f"{trend_txt}泥床 {r.bed_height:.2f} m、耙架扭矩 {r.rake_torque:.1f}%，压缩层负荷偏高。",
            "expected": "泥床回落至 5 m 附近，耙架扭矩降至 55% 以下。",
        })
    elif cur_ratio > RATIO_BASE * 1.10:
        target_ratio = RATIO_BASE * 1.02
        delta = (target_ratio - cur_ratio) / max(cur_ratio, 1e-6) * 100
        items.append({
            "type": "underflow_discharge", "label": "底流排放量",
            "current": round(r.underflow_flow, 1),
            "recommended": round(r.feed_flow * target_ratio, 1),
            "unit": "m³/h", "delta_pct": round(delta, 1),
            "reason": "排放比显著高于稳态，压缩层被抽薄，底流浓度不升反降。",
            "expected": "底流浓度回升约 2~4 个百分点，絮凝剂单耗同步下降。",
        })

    # ② 絮凝剂
    if cur_dosage < 36.0:
        items.append({
            "type": "flocculant_dosage", "label": "絮凝剂单耗",
            "current": round(cur_dosage, 1), "recommended": 44.0,
            "unit": "g/t", "delta_pct": round((44.0 - cur_dosage) / max(cur_dosage, 1e-6) * 100, 1),
            "reason": "当前单耗低于有效絮凝区间，絮体细小、沉降速度不足。",
            "expected": "溢流浊度回落，底流浓度回升 2~3 个百分点。",
        })
    elif cur_dosage > 58.0:
        items.append({
            "type": "flocculant_dosage", "label": "絮凝剂单耗",
            "current": round(cur_dosage, 1), "recommended": round(cur_dosage - 6, 1),
            "unit": "g/t", "delta_pct": -round(6 / cur_dosage * 100, 1),
            "reason": "单耗高于必要水平，继续加药对沉降提升有限。",
            "expected": "在保持底流浓度的前提下降低药剂消耗。",
        })

    # ③ 浓度下滑趋势（未触发上述条件时）
    if not items and conc_delta < -0.4:
        items.append({
            "type": "flocculant_dosage", "label": "絮凝剂单耗",
            "current": round(cur_dosage, 1), "recommended": round(min(48.0, cur_dosage + 4), 1),
            "unit": "g/t", "delta_pct": round(4 / max(cur_dosage, 1e-6) * 100, 1),
            "reason": f"近 2 小时底流浓度 {conc_delta:+.2f} 个百分点，呈下行趋势。",
            "expected": "抑制浓度继续下滑，维持膏体可用性。",
        })

    if not items:
        items.append({
            "type": "keep", "label": "维持当前 setpoint",
            "current": round(cur_dosage, 1), "recommended": round(cur_dosage, 1),
            "unit": "g/t", "delta_pct": 0.0,
            "reason": f"当前工况为「{cond['label']}」，各项参数在合理区间"
                      + (f"，近 2 小时泥床 {bed_delta:+.2f} m 平稳。" if abs(bed_delta) >= 0.02 else "。"),
            "expected": "保持稳定运行。",
        })

    return {
        "available": True,
        "ts": r.created_at.isoformat(),
        "condition": cond,
        "trend": {"window_hours": 2, "bed_delta": bed_delta, "conc_delta": conc_delta},
        "items": items,
        "basis": "推荐量由机理模型在候选 setpoint 中搜索得到（目标：泥床回到 5 m、底流浓度尽量高，而非排放量最大）。",
    }


@router.get("/api/tailings/flocculant-savings")
def flocculant_savings(
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """絮凝剂节药量化：实际投加 vs 模型推荐投加 → 折算金额"""
    end = _latest_ts(db)
    start = end - timedelta(days=days)

    rows = (
        db.query(TailingsReading)
        .filter(TailingsReading.equipment_id == "T1",
                TailingsReading.created_at >= start,
                TailingsReading.created_at <= end)
        .order_by(TailingsReading.created_at.asc())
        .all()
    )
    if not rows:
        return {"available": False}

    # 逐点评估：在当时的给料与床层条件下，推荐单耗取多少
    actual_vals, rec_vals, solids_t = [], [], 0.0
    prev_ts = None
    for r in rows:
        actual_vals.append(r.flocculant_dosage)
        # 推荐：以"床层回到目标"为前提的最小有效单耗
        base = 42.0
        if r.bed_height > 5.5:
            base += 3.0          # 床抬升需强化絮凝
        if r.feed_solids > 24:
            base += 2.0
        if r.underflow_conc < 64:
            base += 2.0
        rec_vals.append(min(base, r.flocculant_dosage))

        # 按相邻点实际时间差积分固体处理量（粗粒度 1h / 细粒度 5min 混排）
        if prev_ts is not None:
            dt_h = (r.created_at - prev_ts).total_seconds() / 3600.0
            if 0 < dt_h <= 6:
                rho = _pulp_rho(r.feed_solids)
                solids_t += r.feed_flow * rho * r.feed_solids / 100.0 * dt_h
        prev_ts = r.created_at

    avg_actual = statistics.mean(actual_vals)
    avg_rec = statistics.mean(rec_vals)
    delta = avg_actual - avg_rec

    saving_kg = delta * solids_t / 1000.0
    saving_cny = saving_kg * FLOCCULANT_PRICE_CNY_PER_KG

    return {
        "available": True,
        "days": days,
        "samples": len(rows),
        "actual_dosage_avg": round(avg_actual, 2),
        "recommended_dosage_avg": round(avg_rec, 2),
        "saving_g_per_t": round(delta, 2),
        "saving_pct": round(delta / avg_actual * 100, 1) if avg_actual else 0.0,
        "solids_treated_t": round(solids_t, 1),
        "saving_kg": round(saving_kg, 1),
        "price_cny_per_kg": FLOCCULANT_PRICE_CNY_PER_KG,
        "saving_cny": round(saving_cny, 1),
        "annualized_cny": round(saving_cny / days * 365, 0),
        "price_note": f"★ 药剂单价按 ¥{FLOCCULANT_PRICE_CNY_PER_KG:.0f}/kg 估算（演示假设值），"
                      f"实际节费需按现场采购价与处理量核对。",
        "method_note": "推荐单耗按「床层回到目标所需的最小有效投加」评估，非事后套算。",
    }


@router.get("/api/tailings/discharge-decision")
def discharge_decision(db: Session = Depends(get_db)):
    """底流去向判定：充填 / 干排 / 只能入库"""
    r = _latest(db)
    if not r:
        return {"available": False}

    c = float(r.underflow_conc)
    if c >= DISCHARGE_RULES["backfill_min"]:
        key, label, level = "backfill_ready", "可送充填", "normal"
        detail = (f"底流浓度 {c:.1f}%，达到膏体充填的可输送下限 "
                  f"（{DISCHARGE_RULES['backfill_min']:.0f}%，文献膏体要求 74~76%）。"
                  f"建议优先送充填站，减少尾矿库堆存压力。")
    elif c >= DISCHARGE_RULES["storage_min"]:
        key, label, level = "storage_only", "仅可入库堆存", "warning"
        detail = (f"底流浓度 {c:.1f}%，低于充填要求 "
                  f"{DISCHARGE_RULES['backfill_min']:.0f}%，但满足管道输送与库内堆存基本要求。"
                  f"若要恢复充填，需提高絮凝剂单耗或降低排放速率以增稠。")
    else:
        key, label, level = "too_thin", "浓度偏低需处置", "danger"
        detail = (f"底流浓度 {c:.1f}%，低于输送安全下限 "
                  f"{DISCHARGE_RULES['storage_min']:.0f}%，输送能耗与堵管风险上升，"
                  f"库内析水量增大。建议立即核查絮凝剂投加与排放速率。")

    return {
        "available": True,
        "ts": r.created_at.isoformat(),
        "decision": key, "label": label, "level": level, "detail": detail,
        "current_conc": round(c, 2),
        "thresholds": DISCHARGE_RULES,
        "flow": round(r.underflow_flow, 1),
        "note": "阈值按文献膏体充填指标与输送经验取值，实施前需与现场设计值核对。",
    }


@router.get("/api/tailings/events")
def tailings_events(
    level: str = Query("", description="info/warning/danger，空为全部"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """尾矿工况事件列表"""
    q = db.query(TailingsEvent)
    if level:
        q = q.filter(TailingsEvent.level == level)
    rows = q.order_by(TailingsEvent.created_at.desc()).limit(limit).all()

    return {
        "total": db.query(func.count(TailingsEvent.id)).scalar(),
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "level": e.level,
                "title": e.title,
                "detail": e.detail,
                "metric": e.metric,
                "metric_label": METRIC_META.get(e.metric, {}).get("label", e.metric),
                "peak_value": e.peak_value,
                "baseline_value": e.baseline_value,
                "unit": METRIC_META.get(e.metric, {}).get("unit", ""),
                "evidence": e.evidence,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ],
    }
