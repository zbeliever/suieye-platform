"""凡口 Courier 在线分析仪历史品位 API（2019-08 真实生产数据）

数据来源：scripts/import_courier_reports.py 导入的 2191 个报表 → courier_assays 表。
取样点语义按数值范围与工艺流程（先铅后锌优先浮选）解读：
  Feed II        给矿（原矿，Pb 3~5%）
  Kpb II         铅精矿（Pb 41~64%）
  Kzn II         锌精矿（Zn 42~57%）
  Pb KCX II      铅粗选尾矿
  Xpb II         铅扫选尾矿
  Zn KCX II      锌粗选尾矿
  Xzn II         锌扫选尾矿（最终尾矿）
  Pb CP II       硫精矿（Fe 32~38%，黄铁矿副产品）
  H pb-zn II     铅锌混合流（语义待与厂方流程图最终确认）

路由注意：router 不设 prefix，全路径写在装饰器里（项目铁律）。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.database import CourierAssay, get_db

router = APIRouter()

# 取样点中文标签（按品位数值范围与工艺流程解读；H/Pb CP 两点语义待厂方确认）
POINT_LABELS = {
    "Feed II": "给矿（原矿）",
    "Kpb II": "铅精矿",
    "Kzn II": "锌精矿",
    "Pb KCX II": "铅粗选尾矿",
    "Xpb II": "铅扫选尾矿",
    "Zn KCX II": "锌粗选尾矿",
    "Xzn II": "最终尾矿",
    "Pb CP II": "硫精矿",
    "H pb-zn II": "铅锌混合流",
}

# 服务器上默认有完整数据的一天（与真实录像对齐：2019-08-22）
DEFAULT_DAY = "2019-08-22"


def _day_range(date: str):
    d0 = datetime.strptime(date, "%Y-%m-%d")
    return d0, d0 + timedelta(days=1)


@router.get("/api/courier/overview")
def courier_overview(db: Session = Depends(get_db)):
    """数据概览：时间范围、总量、各取样点×元素的统计（前端数据源说明用）"""
    total = db.execute(select(func.count(CourierAssay.id))).scalar()
    lo, hi = db.execute(
        select(func.min(CourierAssay.sample_time), func.max(CourierAssay.sample_time))
    ).one()
    days = db.execute(
        select(func.date(CourierAssay.sample_time)).distinct().order_by(func.date(CourierAssay.sample_time))
    ).scalars().all()

    stats = db.execute(
        select(
            CourierAssay.sample_point,
            CourierAssay.element,
            func.count(CourierAssay.id),
            func.min(CourierAssay.value),
            func.max(CourierAssay.value),
            func.avg(CourierAssay.value),
        )
        .group_by(CourierAssay.sample_point, CourierAssay.element)
        .order_by(CourierAssay.sample_point, CourierAssay.element)
    ).all()

    points = {}
    for p, e, c, mn, mx, avg in stats:
        d = points.setdefault(p, {
            "point": p,
            "label": POINT_LABELS.get(p, p),
            "count": 0,
            "elements": {},
        })
        d["count"] += c
        d["elements"][e] = {"count": c, "min": round(mn, 3), "max": round(mx, 3), "avg": round(avg, 3)}

    return {
        "plant": "fankou",
        "source": "Courier 在线分析仪 · II 20min report 报表（2019-08 全月）",
        "total": total,
        "time_range": [lo.isoformat() if lo else None, hi.isoformat() if hi else None],
        "days": days,
        "points": list(points.values()),
    }


@router.get("/api/courier/day")
def courier_day(
    date: str = Query(DEFAULT_DAY, description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """一整天的全部取样点序列（历史回放页一次拉全，~7000 条）

    返回按「取样点×元素」分组的序列，时间升序，前端做时间轴联动。
    """
    try:
        d0, d1 = _day_range(date)
    except ValueError:
        raise HTTPException(400, "date 格式应为 YYYY-MM-DD")

    rows = db.execute(
        select(CourierAssay.sample_time, CourierAssay.sample_point, CourierAssay.element, CourierAssay.value)
        .where(CourierAssay.sample_time >= d0, CourierAssay.sample_time < d1)
        .order_by(CourierAssay.sample_time.asc())
    ).all()

    series = {}
    for ts, p, e, v in rows:
        key = f"{p}|{e}"
        s = series.setdefault(key, {
            "point": p, "label": POINT_LABELS.get(p, p), "element": e, "values": [],
        })
        s["values"].append({"ts": ts.strftime("%H:%M:%S"), "v": round(v, 3)})

    # 元素顺序固定（前端图例稳定）
    order = {"Pb": 0, "Zn": 1, "Fe": 2}
    return {
        "date": date,
        "count": len(rows),
        "series": sorted(series.values(), key=lambda s: (s["point"], order.get(s["element"], 9))),
    }


@router.get("/api/courier/series")
def courier_series(
    point: str = Query(..., description="取样点，如 Kpb II"),
    element: str = Query("Pb", pattern="^(Pb|Zn|Fe)$"),
    date: str = Query(None, description="YYYY-MM-DD（与 days 二选一）"),
    days: int = Query(None, ge=1, le=31, description="最近 N 天（从有数据的最后一天往回）"),
    db: Session = Depends(get_db),
):
    """单取样点×单元素的时间序列（趋势图用）"""
    if point not in POINT_LABELS:
        raise HTTPException(404, f"未知取样点: {point}（可选：{'、'.join(POINT_LABELS)}）")

    q = select(CourierAssay.sample_time, CourierAssay.value).where(
        CourierAssay.sample_point == point,
        CourierAssay.element == element,
    )
    if date:
        try:
            d0, d1 = _day_range(date)
        except ValueError:
            raise HTTPException(400, "date 格式应为 YYYY-MM-DD")
        q = q.where(CourierAssay.sample_time >= d0, CourierAssay.sample_time < d1)
        label = date
    elif days:
        last = db.execute(select(func.max(CourierAssay.sample_time))).scalar()
        if last is None:
            return {"point": point, "element": element, "days": days, "values": []}
        d0 = (last - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        q = q.where(CourierAssay.sample_time >= d0)
        label = f"{d0:%Y-%m-%d} ~ {last:%Y-%m-%d}"
    else:
        raise HTTPException(400, "必须提供 date 或 days")

    rows = db.execute(q.order_by(CourierAssay.sample_time.asc())).all()
    vals = [v for _, v in rows]
    return {
        "point": point,
        "label": POINT_LABELS[point],
        "element": element,
        "range": label,
        "count": len(rows),
        "stats": {
            "min": round(min(vals), 3) if vals else None,
            "max": round(max(vals), 3) if vals else None,
            "avg": round(sum(vals) / len(vals), 3) if vals else None,
        },
        "values": [{"ts": t.strftime("%Y-%m-%d %H:%M:%S"), "v": round(v, 3)} for t, v in rows],
    }


# ---------------- 工艺链「参照标准」：历史分位区间 ----------------
# ★ 数据诚实：参照标准 = 该测点**全月真实化验**的分位数，不是一个编出来的"目标值"。
#   P10~P90 = 正常区间；P90~P99 / P01~P10 = 偏高/偏低；越 P99/P01 = 超限。
_BANDS_CACHE: dict = {}


def _pct(sorted_vals, q: float) -> float:
    """线性插值分位数（与 numpy.percentile 一致，无需引入 numpy）"""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    k = (n - 1) * q
    f = int(k)
    c = min(f + 1, n - 1)
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


@router.get("/api/courier/bands")
def courier_bands(db: Session = Depends(get_db)):
    """各取样点×元素的历史分位区间（工艺链"参照标准"用）

    返回 { "Feed II|Pb": { p01, p10, p50, p90, p99, min, max, n }, ... }
    ★ 全量 62k 条一次算完并缓存（进程内），后续调用直接返回。
    """
    if _BANDS_CACHE:
        return _BANDS_CACHE

    rows = db.execute(
        select(CourierAssay.sample_point, CourierAssay.element, CourierAssay.value)
    ).all()
    buckets: dict = {}
    for p, e, v in rows:
        buckets.setdefault(f"{p}|{e}", []).append(v)

    out = {}
    for key, vals in buckets.items():
        s = sorted(vals)
        out[key] = {
            "n": len(s),
            "min": round(s[0], 3),
            "max": round(s[-1], 3),
            "p01": round(_pct(s, 0.01), 3),
            "p10": round(_pct(s, 0.10), 3),
            "p50": round(_pct(s, 0.50), 3),
            "p90": round(_pct(s, 0.90), 3),
            "p99": round(_pct(s, 0.99), 3),
        }
    _BANDS_CACHE.update(out)
    return out
