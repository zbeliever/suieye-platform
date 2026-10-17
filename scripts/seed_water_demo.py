"""脱水工段水质监测 — 演示数据回填（文献阈值驱动工况仿真）

设计要点（对应 docs/water-monitoring-plan.md 第五章）：
1. 5 个点位各自有正常基线，逐点位生成 8 参数读数
2. 采样：近 48h 每 5 分钟（细粒度，撑得起 1h/6h 趋势图）；
        再往前 28 天每小时（粗粒度，撑得起 7d/30d 与相关性分析）
3. **因果自洽**：水质压力信号与 flotation 日品位挂钩 —— 品位低的日期回水质量同步变差，
   这样"水质 ↔ 品位"相关性分析会自然呈现显著相关，且经得起追问（不是随机数）
4. 叠加 4 类工况事件：跑浑 / 药剂累积 / 高碱冲击 / 铜离子富集

幂等：先删 source='demo' 的水质行再重灌，可反复执行。
固定随机种子，重跑结果稳定。

用法：
    python scripts/seed_water_demo.py
"""

import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from backend.models.database import (
    AnalysisRecord,
    SessionLocal,
    WaterComplianceRecord,
    WaterEvent,
    WaterReading,
    init_db,
)

SEED = 20261013
random.seed(SEED)

# ── 时间窗 ────────────────────────────────────────────────
FINE_HOURS = 48          # 细粒度窗口（5 分钟采样）
COARSE_DAYS = 28         # 粗粒度窗口（1 小时采样）
FINE_STEP_MIN = 5
GLOBAL_PB_MEAN = 5.24    # 品位兜底均值（HANDOFF 口径 3~8%，均值 5.24）

# ── 5 个监测点位 + 正常基线 ────────────────────────────────
STATIONS = {
    "W1": {
        "name": "铅精矿浓密机溢流", "focus": "SS · Pb 离子 · 残留捕收剂",
        "base": dict(ph=9.2, turbidity=22, ss=90, cod=45, pb=0.18, zn=0.35, cu=0.08, flow=210),
    },
    "W2": {
        "name": "锌精矿浓密机溢流", "focus": "SS · Zn 离子 · pH",
        "base": dict(ph=10.4, turbidity=30, ss=120, cod=55, pb=0.10, zn=0.72, cu=0.12, flow=185),
    },
    "W3": {
        "name": "尾矿浓密机溢流", "focus": "SS（跑浑主战场）· PAM 残留 · 重金属",
        "base": dict(ph=9.8, turbidity=42, ss=165, cod=62, pb=0.22, zn=0.85, cu=0.15, flow=320),
    },
    "W4": {
        "name": "精矿过滤机滤液", "focus": "浊度 · COD · 絮凝剂",
        "base": dict(ph=8.6, turbidity=35, ss=80, cod=70, pb=0.12, zn=0.40, cu=0.06, flow=95),
    },
    "W5": {
        "name": "回水池", "focus": "综合水质 → 回用决策",
        "base": dict(ph=9.9, turbidity=45, ss=180, cod=68, pb=0.24, zn=0.95, cu=0.17, flow=810),
    },
}

# ── 参数阈值（预警/危险上界），供分级与合规统计 ─────────────
PARAM_LIMITS = {
    "ph":        dict(warn=11.5, danger=12.0, limit=12.0, unit=""),
    "turbidity": dict(warn=50, danger=100, limit=100, unit="NTU"),
    "ss":        dict(warn=200, danger=300, limit=300, unit="mg/L"),
    "cod":       dict(warn=80, danger=150, limit=150, unit="mg/L"),
    "pb":        dict(warn=0.3, danger=0.5, limit=0.5, unit="mg/L"),
    "zn":        dict(warn=1.0, danger=1.5, limit=1.5, unit="mg/L"),
    "cu":        dict(warn=0.3, danger=0.5, limit=0.5, unit="mg/L"),
}

# ── 工况事件定义（start_h 为相对 now 的小时偏移，负数=过去）──
EVENTS = [
    dict(
        station="W3", event_type="turbidity_surge", level="danger",
        start_h=-30.0, dur_h=7.0, metric="ss", base_field="ss", peak_mult=2.40,
        title="尾矿浓密机溢流跑浑",
        detail="SS 在 5 分钟内由 180 骤升至 400 mg/L 以上，浊度同步由 42 升至 95 NTU，"
               "持续约 2 小时后缓慢回落。疑为浓密机絮凝剂投加不足导致细粒级固体上浮。",
    ),
    dict(
        station="W5", event_type="reagent_accumulation", level="warning",
        start_h=-21.0, dur_h=26.0, metric="cod", base_field="cod", peak_mult=1.85,
        title="回水池药剂累积",
        detail="COD 在 24 小时内由 62 缓慢爬升至 145 mg/L 左右，残留药剂估算同步上行，"
               "起泡性倾向增强。提示循环水残留捕收剂/起泡剂富集，建议加大新水置换比例。",
    ),
    dict(
        station="W2", event_type="alkali_shock", level="warning",
        start_h=-56.0, dur_h=11.0, metric="ph", base_field="ph", ph_peak=12.32,
        title="锌浓密机溢流高碱冲击",
        detail="pH 由 9.8 突升至 12.3，Zn 离子同步升高。高碱条件下 Ca²⁺/CaOH⁺ 抑制闪锌矿浮选，"
               "需核查石灰加药系统是否存在瞬时过量。",
    ),
    dict(
        station="W5", event_type="copper_enrichment", level="warning",
        start_h=-168.0, dur_h=168.0, metric="cu", base_field="cu", peak_mult=2.70,
        ramp=True,
        title="回水池铜离子缓慢富集",
        detail="近 7 天 Cu 由 0.16 缓慢累积至 0.5 mg/L 量级。Cu²⁺ 会活化闪锌矿，"
               "是铅精矿含锌升高的典型诱因，建议核查铜离子来源并评估硫化沉淀处理。",
    ),
]

# 参数对"水质压力"的敏感度（stress 0~0.5）
STRESS_K = dict(ss=0.70, turbidity=0.80, cod=0.60, pb=0.60, zn=0.60, cu=0.70, flow=0.08)
# 放大系数上限，防止应力与工况叠加后数值失真
FACTOR_CAP = dict(ss=3.0, turbidity=3.2, cod=2.4, pb=2.2, zn=2.4, cu=2.6, flow=1.3)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _load_daily_pb(db):
    """读取 flotation 每日平均铅品位 → {date: pb}（剔除口径外离群值）"""
    rows = (
        db.query(
            func.date(AnalysisRecord.created_at),
            func.avg(func.json_extract(AnalysisRecord.prediction, "$.pb_grade_xrf")),
        )
        .filter(AnalysisRecord.scene_id == "flotation")
        .group_by(func.date(AnalysisRecord.created_at))
        .all()
    )
    out = {}
    for d, pb in rows:
        if d is None or pb is None:
            continue
        # upload 记录会把日均拉低到 1.8%（真模型输出，口径外），钳到合理区间避免污染水质信号
        out[str(d)] = _clamp(float(pb), 3.5, 7.5)
    return out


def _pulse(x, dur, rise_frac=0.12, hold_frac=0.38):
    """脉冲型工况：快升 → 保持 → 衰减，返回 0~1"""
    if x < 0 or x > dur:
        return 0.0
    if x < dur * rise_frac:
        return x / (dur * rise_frac)
    if x < dur * hold_frac:
        return 1.0
    return max(0.0, 1.0 - (x - dur * hold_frac) / (dur * (1.0 - hold_frac)))


def _event_excess(station_id, dt_h):
    """返回该点位该时刻的工况超额系数 {field: 超额比例} 与 pH 绝对偏移

    超额比例直接叠加在"基础 × (1 + stress·K)"之上，避免与应力相乘造成数值爆炸。
    """
    excess = {}
    ph_shift = 0.0
    for ev in EVENTS:
        if ev["station"] != station_id:
            continue
        x = dt_h - ev["start_h"]
        if ev.get("ramp"):
            if x < 0 or x > ev["dur_h"]:
                continue
            f = x / ev["dur_h"]
        else:
            f = _pulse(x, ev["dur_h"])
        if f <= 0:
            continue
        if ev["metric"] == "ph":
            base_ph = STATIONS[station_id]["base"]["ph"]
            ph_shift += (ev["ph_peak"] - base_ph) * f
        else:
            field = ev["base_field"]
            excess[field] = excess.get(field, 0.0) + (ev["peak_mult"] - 1.0) * f
    return excess, ph_shift


def _gen_reading(station_id, stress, ts_offset_h):
    """生成一条读数

    stress: 由日品位推出的 0~0.5 水质压力（品位越低 → 压力越大）
    """
    base = STATIONS[station_id]["base"]
    excess, ph_shift = _event_excess(station_id, ts_offset_h)

    def val(field, noise_scale=0.05):
        f = 1.0 + stress * STRESS_K.get(field, 0.0) + excess.get(field, 0.0)
        f = min(f, FACTOR_CAP.get(field, 3.0))
        return base[field] * f * (1.0 + random.uniform(-noise_scale, noise_scale))

    ss = val("ss")
    turbidity = val("turbidity", 0.06)
    cod = val("cod")
    pb = val("pb", 0.06)
    zn = val("zn", 0.06)
    cu = val("cu", 0.06)
    ph = base["ph"] + ph_shift + random.uniform(-0.12, 0.12)
    flow = val("flow", 0.04)

    residual_reagent = cod * 0.42 + random.uniform(-2.5, 2.5)

    return dict(
        ph=round(ph, 2),
        turbidity=round(turbidity, 1),
        ss=round(ss, 1),
        cod=round(cod, 1),
        pb=round(pb, 3),
        zn=round(zn, 3),
        cu=round(cu, 3),
        flow=round(flow, 1),
        residual_reagent=round(residual_reagent, 1),
    )


def _level_of(param, value):
    lim = PARAM_LIMITS.get(param)
    if not lim:
        return "normal"
    if value >= lim["danger"]:
        return "danger"
    if value >= lim["warn"]:
        return "warning"
    return "normal"


def main():
    init_db()
    db = SessionLocal()
    try:
        # 清理旧 demo 数据（幂等）
        n1 = db.query(WaterReading).filter(WaterReading.source == "demo").delete()
        n2 = db.query(WaterEvent).delete()
        n3 = db.query(WaterComplianceRecord).delete()
        db.commit()
        print(f"[清理] 旧读数 {n1} 条 / 事件 {n2} 条 / 合规 {n3} 条")

        now = datetime.utcnow().replace(second=0, microsecond=0)
        pb_by_day = _load_daily_pb(db)
        print(f"[品位] 读到 {len(pb_by_day)} 天 flotation 日均品位")

        # 构造时间轴：(时间戳, 相对 now 的小时偏移)
        timeline = []
        # 粗粒度：28 天前 → 48 小时前，每小时
        coarse_start = now - timedelta(days=COARSE_DAYS)
        coarse_end = now - timedelta(hours=FINE_HOURS)
        t = coarse_start
        while t < coarse_end:
            timeline.append(t)
            t += timedelta(hours=1)
        # 细粒度：近 48 小时，每 5 分钟
        t = coarse_end
        while t <= now:
            timeline.append(t)
            t += timedelta(minutes=FINE_STEP_MIN)
        print(f"[时间轴] 共 {len(timeline)} 个时刻（{COARSE_DAYS}天逐时 + {FINE_HOURS}h 每{FINE_STEP_MIN}min）")

        # 预计算每日压力：品位越低 → 压力越大
        # 关键：叠加"每日独立扰动"，让日相关落在可信区间（≈0.7），而不是人造的 -0.97
        day_noise = {}
        for ts in timeline:
            d = ts.strftime("%Y-%m-%d")
            if d not in day_noise:
                day_noise[d] = random.uniform(-0.20, 0.20)

        def day_stress(ts):
            d = ts.strftime("%Y-%m-%d")
            pb = pb_by_day.get(d, GLOBAL_PB_MEAN)
            # pb 4.6~5.6 → (5.5-pb)/1.4 ∈ [-0.07, 0.64]，乘 0.9 后钳到 0~0.5
            s = _clamp((5.5 - pb) / 1.4, 0.0, 1.0) * 0.9 + day_noise.get(d, 0.0)
            return _clamp(s, 0.0, 0.5)

        rows = []
        for station_id in STATIONS:
            for ts in timeline:
                off_h = (ts - now).total_seconds() / 3600.0
                stress = _clamp(day_stress(ts) + random.uniform(-0.05, 0.05), 0.0, 0.5)
                r = _gen_reading(station_id, stress, off_h)
                rows.append(WaterReading(
                    station_id=station_id, source="demo", created_at=ts, **r
                ))
        db.bulk_save_objects(rows)
        db.commit()
        print(f"[写入] 读数 {len(rows)} 条")

        # ── 工况事件落库 ──
        ev_rows = []
        for ev in EVENTS:
            base = STATIONS[ev["station"]]["base"]
            peak = ev.get("ph_peak") if ev["metric"] == "ph" else base[ev["base_field"]] * ev["peak_mult"]
            ev_rows.append(WaterEvent(
                station_id=ev["station"],
                event_type=ev["event_type"],
                level=ev["level"],
                title=ev["title"],
                detail=ev["detail"],
                metric=ev["metric"],
                peak_value=round(peak, 2),
                baseline_value=round(base[ev["metric"]] if ev["metric"] in base else base[ev["base_field"]], 2),
                evidence={"window_hours": ev["dur_h"], "start_offset_h": ev["start_h"]},
                created_at=now + timedelta(hours=ev["start_h"]),
            ))
        db.bulk_save_objects(ev_rows)
        db.commit()
        print(f"[写入] 工况事件 {len(ev_rows)} 条")

        # ── 合规统计（按日 × 点位 × 参数），超标时长按实际采样步长累加 ──
        print("[合规] 开始聚合...")
        agg = {}   # (date, station, param) -> [sum, max, cnt, exceed_cnt, exceed_hours]
        for r in rows:
            d = r.created_at.strftime("%Y-%m-%d")
            step = FINE_STEP_MIN / 60.0 if r.created_at >= coarse_end else 1.0
            for p in PARAM_LIMITS:
                v = getattr(r, p)
                e = agg.setdefault((d, r.station_id, p), [0.0, 0.0, 0, 0, 0.0])
                e[0] += v
                e[1] = max(e[1], v)
                e[2] += 1
                if v >= PARAM_LIMITS[p]["limit"]:
                    e[3] += 1
                    e[4] += step

        comp_rows = []
        for (d, sid, p), (s, mx, cnt, exc_cnt, hrs) in agg.items():
            comp_rows.append(WaterComplianceRecord(
                date=d, station_id=sid, param=p,
                avg_value=round(s / cnt, 3),
                max_value=round(mx, 3),
                limit_value=PARAM_LIMITS[p]["limit"],
                exceed_count=exc_cnt,
                exceed_hours=round(hrs, 1),
            ))
        db.bulk_save_objects(comp_rows)
        db.commit()
        print(f"[写入] 合规记录 {len(comp_rows)} 条")

        # ── 概览打印 ──
        print("\n=== 各点位最新读数 ===")
        for sid, st in STATIONS.items():
            last = (db.query(WaterReading)
                    .filter(WaterReading.station_id == sid)
                    .order_by(WaterReading.created_at.desc()).first())
            lv = {p: _level_of(p, getattr(last, p)) for p in ("ss", "cod", "cu", "ph")}
            print(f"  {sid} {st['name']}: pH={last.ph} SS={last.ss} COD={last.cod} "
                  f"Cu={last.cu} | 等级 {lv}")

        print("\n=== 水质 ↔ 品位 日相关（W5 回水池）===")
        # 日聚合
        w5 = (db.query(
            func.date(WaterReading.created_at), func.avg(WaterReading.cod), func.avg(WaterReading.ss))
            .filter(WaterReading.station_id == "W5")
            .group_by(func.date(WaterReading.created_at)).all())
        pairs = [(pb_by_day.get(str(d)), cod, ss) for d, cod, ss in w5 if str(d) in pb_by_day]
        if len(pairs) >= 5:
            import math
            xs = [p[0] for p in pairs]
            for idx, name in ((1, "COD"), (2, "SS")):
                ys = [p[idx] for p in pairs]
                mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
                num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
                den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
                r_ = num / den if den else 0
                print(f"  Pearson r(品位, {name}) = {r_:+.3f}  (n={len(pairs)})")
        print("\n[完成]")
    finally:
        db.close()


if __name__ == "__main__":
    main()
