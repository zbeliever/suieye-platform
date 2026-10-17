"""尾矿处理 — 演示数据回填（物理机理驱动，非随机数）

对应 docs/tailings-processing-plan.md 第六章「数据方案」。

设计要点
--------
1. **时间轴完全对齐 water_readings**（同一 min/max，同为 28 天逐时 + 近 48h 每 5 分钟），
   保证尾矿模块与水质模块的时间戳一一对应，跨模块数字不可能打架。
2. **状态递推而非逐点随机**：泥床高度由「给料固体 − 底流带走固体」积分得到，
   底流浓度由泥床高度 / 絮凝剂单耗 / 停留时间共同决定，耙架扭矩由泥床高度决定。
   → 参数之间存在真实耦合与时滞，不是各自独立的随机游走。
3. **固体物料平衡可检验**：给料固体 = 底流固体 + 溢流固体；
   由体积守恒反推的溢流密度必须落在清水合理区间，相对残差入库供质证。
4. **溢流浊度不生成**：它就是 water_readings 的 W3 点位，接口层实时取用。
5. 絮凝剂不足事件 **锚定 water_events 的 W3 跑浑时刻**，让两个模块讲同一件事。

物理口径
--------
- 尾矿真密度 ρs = 2.9 t/m³，水 ρw = 1.0 t/m³
- 矿浆密度 1/ρ = (1−c)/ρw + c/ρs（c 为固体质量分数）
- 基准工况：给料 420 m³/h @ 22% → 底流 68% @ ~86 m³/h，溢流约 334 m³/h

幂等：先删 source='demo' 的行再重灌。固定随机种子，重跑结果稳定。

用法：
    python scripts/seed_tailings_demo.py
"""

import os
import random
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from backend.models.database import (
    AnalysisRecord,
    SessionLocal,
    TailingsEvent,
    TailingsReading,
    WaterEvent,
    WaterReading,
    init_db,
)

SEED = 20261014
random.seed(SEED)

EQUIPMENT_ID = "T1"
EQUIPMENT_NAME = "尾矿浓密机"

# ── 物理常数 ──────────────────────────────────────────────
RHO_S = 2.9          # 尾矿真密度 t/m³
RHO_W = 1.0          # 水密度 t/m³
RHO_CLEAR = 1.005    # 澄清溢流的期望密度（含极少量细粒）

# ── 基准工况 ──────────────────────────────────────────────
BASE_FEED_FLOW = 420.0     # m³/h
BASE_FEED_SOLIDS = 22.0    # %
BASE_DOSAGE = 45.0         # g/t 干尾
BASE_BED = 5.0             # m
BED_TARGET = 5.0           # 操作员控床目标
RATIO_BASE = 0.2083        # 稳态底流体积流量 / 给料体积流量（由固体物料平衡标定）
K_SHEAR = 140.0            # 排放超基准时的抽稀惩罚（%浓度 / 体积比偏离）
TAU_PEN = 12.0             # 给料超基准时的停留时间惩罚（%浓度 / 给料倍率偏离）

# ── 递推系数 ──────────────────────────────────────────────
DT_MIN = 5.0               # 统一递推步长（分钟），粗粒度靠降采样得到
K_BED = 0.0075             # 泥床对「固体净累积」的响应系数 m / (t/h·h)
K_BED_CTRL = 0.055         # 操作员按泥床偏差调整排放的比例（每 m）
CTRL_LAG = 0.22            # 排放指令一阶滞后（执行器/人员反应）
NOISE_AR = 0.86            # AR(1) 噪声自相关

# ── 指标阈值（对应 docs/tailings-processing-plan.md 3.2）──
# 展示口径集中在 backend/api/routers/tailings.py 的 METRIC_META，此处不重复定义


def _pulp_rho(c_pct: float) -> float:
    """矿浆密度 t/m³（c 为固体质量分数 %）"""
    c = max(0.0, min(c_pct, 100.0)) / 100.0
    return 1.0 / ((1.0 - c) / RHO_W + c / RHO_S)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _pulse(x, dur, rise=0.15, hold=0.35):
    """脉冲型工况：快升 → 保持 → 衰减，返回 0~1"""
    if x < 0 or x > dur:
        return 0.0
    if x < dur * rise:
        return x / (dur * rise)
    if x < dur * hold:
        return 1.0
    return max(0.0, 1.0 - (x - dur * hold) / (dur * (1.0 - hold)))


# ============================================================
# 工况事件
# start_offset_h 为相对时间轴终点的偏移（负数=过去）；
# flocculant_shortage 的锚点由 water_events 的 W3 跑浑事件动态决定。
# ============================================================
EVENTS_SPEC = [
    dict(
        kind="flocculant_shortage", level="danger", dur_h=7.0, anchor="water_w3",
        title="絮凝剂投加不足导致跑浑",
        detail="絮凝剂单耗由 45 降至约 25 g/t，絮体形成不充分、细粒级固体无法有效沉降，"
               "上部澄清层被破坏。同期水质监测 W3（尾矿浓密机溢流）同步记录到 SS 与浊度飙升，"
               "两个模块指向同一起工况。",
    ),
    dict(
        kind="bed_rise", level="warning", dur_h=14.0, start_offset_h=-62.0,
        title="泥床持续上涨（排放偏保守）",
        detail="操作员按保守策略压低底流排放速率，压缩层固体净累积，泥床由 5.0 m 升至 7 m 以上，"
               "耙架扭矩随之越过预警线，存在压耙风险。",
    ),
    dict(
        kind="feed_surge", level="warning", dur_h=5.0, start_offset_h=-104.0,
        title="上游给料突增",
        detail="浮选段处理量上台阶，尾矿浆量同步增加约 35%。浓密机停留时间缩短，"
               "压缩层来不及增稠，底流浓度被动下滑。",
    ),
    dict(
        kind="over_discharge", level="info", dur_h=6.0, start_offset_h=-206.0,
        title="底流排放速率拉满（反直觉验证）",
        detail="底流泵开到最大档位。文献与仿真均显示：单纯最大化排放速率并不能提高底流浓度，"
               "反而因压缩层被抽薄导致浓度下降、絮凝剂单耗上升 —— 这是「排出越多越好」直觉的反例。",
    ),
]


def _load_daily_feed(db):
    """读取 flotation 日处理量 → 折算给料倍率

    浮选处理量大 → 尾矿产出多 → 浓密机给料量同步上行（物料流守恒方向）。
    """
    rows = (
        db.query(func.date(AnalysisRecord.created_at), func.count())
        .filter(AnalysisRecord.scene_id == "flotation")
        .group_by(func.date(AnalysisRecord.created_at))
        .all()
    )
    counts = {str(d): int(c) for d, c in rows if d is not None}
    if not counts:
        return {}, 1.0
    med = statistics.median(counts.values()) or 1
    recent = [v for k, v in sorted(counts.items())[-7:]]
    fallback = statistics.mean(recent) if recent else med
    return counts, fallback


def _load_p80_series(db):
    """读取 grinding 的 P80 预测（条数很少，按时间就近取用；取不到则 None）"""
    rows = (
        db.query(
            AnalysisRecord.created_at,
            func.json_extract(AnalysisRecord.prediction, "$.p80"),
        )
        .filter(AnalysisRecord.scene_id == "grinding")
        .order_by(AnalysisRecord.created_at.asc())
        .all()
    )
    out = []
    for ts, p80 in rows:
        if ts is None or p80 is None:
            continue
        try:
            v = float(p80)
        except (TypeError, ValueError):
            continue
        if 10.0 <= v <= 500.0:
            out.append((ts, round(v, 1)))
    return out


def _nearest_p80(series, ts, max_gap_h=36.0):
    """就近取 P80；超过 max_gap_h 视为无数据（不编）"""
    best, best_gap = None, None
    for s_ts, v in series:
        gap = abs((ts - s_ts).total_seconds()) / 3600.0
        if best_gap is None or gap < best_gap:
            best, best_gap = v, gap
    if best is None or (best_gap or 1e9) > max_gap_h:
        return None
    return best


def main():
    init_db()
    db = SessionLocal()
    try:
        # ── 幂等清理 ──
        n1 = db.query(TailingsReading).filter(TailingsReading.source == "demo").delete()
        n2 = db.query(TailingsEvent).delete()
        db.commit()
        print(f"[清理] 旧读数 {n1} 条 / 事件 {n2} 条")

        # ── 时间轴：完全对齐 water_readings ──
        w_min, w_max = db.query(
            func.min(WaterReading.created_at), func.max(WaterReading.created_at)
        ).first()
        if w_min is None:
            raise SystemExit(
                "water_readings 为空 —— 请先运行 scripts/seed_water_demo.py，"
                "尾矿模块的时间轴与跨模块一致性都依赖它。"
            )
        t_end = w_max.replace(second=0, microsecond=0)
        t_start = w_min.replace(second=0, microsecond=0)
        print(f"[时间轴] 对齐水质监测：{t_start} → {t_end}")

        fine_start = t_end - timedelta(hours=48)

        # 统一 5 分钟网格递推，粗粒度段靠降采样
        grid = []
        t = t_start
        while t <= t_end:
            grid.append(t)
            t += timedelta(minutes=DT_MIN)
        print(f"[时间轴] 递推网格 {len(grid)} 点（步长 {DT_MIN} 分钟）")

        # ── 事件锚点 ──
        w3_ev = (
            db.query(WaterEvent)
            .filter(WaterEvent.station_id == "W3", WaterEvent.event_type == "turbidity_surge")
            .first()
        )
        anchor_ts = w3_ev.created_at if w3_ev else (t_end - timedelta(hours=30))
        print(f"[锚点] 絮凝剂不足事件对齐 water W3 跑浑：{anchor_ts}")

        events = []
        for spec in EVENTS_SPEC:
            start = anchor_ts if spec.get("anchor") == "water_w3" else t_end + timedelta(hours=spec["start_offset_h"])
            events.append({**spec, "start": start, "end": start + timedelta(hours=spec["dur_h"])})

        def event_active(kind, ts):
            for ev in events:
                if ev["kind"] != kind:
                    continue
                if ev["start"] <= ts <= ev["end"]:
                    x = (ts - ev["start"]).total_seconds() / 3600.0
                    return _pulse(x, ev["dur_h"])
            return 0.0

        # ── 上游节奏 ──
        daily_counts, fallback_count = _load_daily_feed(db)
        print(f"[上游] flotation 覆盖 {len(daily_counts)} 天，近 7 天均值 {fallback_count:.1f} 条/天")
        p80_series = _load_p80_series(db)
        print(f"[上游] grinding P80 可用 {len(p80_series)} 条")

        # ── 状态递推 ──
        bed = BASE_BED
        q_uf_cmd = BASE_FEED_FLOW * RATIO_BASE   # 底流排放指令初值 m³/h
        n_feed = n_solids = n_dos = 0.0

        rows = []
        stats = dict(res_min=9e9, res_max=-9e9, res_sum=0.0, res_n=0,
                     cuf_min=9e9, cuf_max=-9e9, bed_min=9e9, bed_max=-9e9)

        for ts in grid:
            dt_h = DT_MIN / 60.0

            # AR(1) 平滑噪声（每 5 分钟缓变，不是跳变）
            n_feed = NOISE_AR * n_feed + (1 - NOISE_AR) * random.uniform(-1, 1)
            n_solids = NOISE_AR * n_solids + (1 - NOISE_AR) * random.uniform(-1, 1)
            n_dos = NOISE_AR * n_dos + (1 - NOISE_AR) * random.uniform(-1, 1)

            # —— 给料：跟随浮选处理量 + 日周期 + 事件 ——
            day_key = ts.strftime("%Y-%m-%d")
            cnt = daily_counts.get(day_key, fallback_count) or fallback_count
            day_factor = _clamp(1.0 + 0.28 * (cnt / max(fallback_count, 1.0) - 1.0), 0.8, 1.3)
            hour_frac = ts.hour + ts.minute / 60.0
            shift_wave = 1.0 + 0.045 * (1.0 if 8 <= hour_frac <= 20 else -1.0)  # 白班略高
            surge = event_active("feed_surge", ts)
            q_feed = BASE_FEED_FLOW * day_factor * shift_wave * (1 + 0.05 * n_feed) * (1 + 0.35 * surge)
            q_feed = max(120.0, q_feed)

            c_feed = BASE_FEED_SOLIDS * (1 + 0.06 * n_solids)
            c_feed = _clamp(c_feed, 15.0, 32.0)

            rho_feed = _pulp_rho(c_feed)
            m_feed = q_feed * rho_feed                # 矿浆质量 t/h
            s_feed = m_feed * c_feed / 100.0          # 固体质量 t/h

            # —— 絮凝剂 ——
            shortage = event_active("flocculant_shortage", ts)
            dosage = BASE_DOSAGE * (1 + 0.05 * n_dos) * (1 - 0.45 * shortage)
            dosage = _clamp(dosage, 18.0, 75.0)
            floc_mass = s_feed * dosage / 1000.0      # kg/h 干药剂
            floc_flow = floc_mass / 0.001 / 1000.0    # m³/h（0.1% 溶液）

            # ① 底流浓度：由当前泥床（上一刻递推结果，构成天然一步滞后）
            #    + 絮凝剂单耗 + 停留时间 + 抽稀程度共同决定
            ratio_prev = q_uf_cmd / max(q_feed, 1.0)
            tau_pen = -TAU_PEN * (q_feed / BASE_FEED_FLOW - 1.0)       # 给料偏大 → 停留时间不足
            shear_pen = -K_SHEAR * max(0.0, ratio_prev - RATIO_BASE)   # 抽得太狠 → 压缩层被抽薄
            c_uf = (68.5 + 2.0 * (bed - BASE_BED)
                    + 7.0 * (dosage - BASE_DOSAGE) / BASE_DOSAGE
                    + tau_pen + shear_pen + 0.6 * n_dos)
            c_uf = _clamp(c_uf, 52.0, 81.0)
            rho_uf = _pulp_rho(c_uf)

            # ② 操作员排放控制：基准随给料走，再按泥床偏差做比例调节（带一阶滞后）
            risk_averse = 1.0 - 0.35 * event_active("bed_rise", ts)    # 保守策略 → 少排
            flush = 1.0 + 0.30 * event_active("over_discharge", ts)    # 泵拉满 → 多排
            q_uf_target = (q_feed * RATIO_BASE
                           * (1.0 + K_BED_CTRL * (bed - BED_TARGET)) * risk_averse * flush)
            q_uf_target = _clamp(q_uf_target, 20.0, q_feed * 0.55)
            q_uf_cmd += (q_uf_target - q_uf_cmd) * CTRL_LAG
            q_uf_cmd = _clamp(q_uf_cmd, 20.0, q_feed * 0.55)

            # ③ 实际排出：排出能力受压缩层可供给性限制（允许约 2% 的床内库存消耗做缓冲）
            q_uf = q_uf_cmd
            s_uf_raw = q_uf * rho_uf * c_uf / 100.0
            s_uf = min(s_uf_raw, s_feed * 1.02)
            # 由固体守恒反推矿浆质量与体积，保证底流侧自洽
            m_uf = s_uf / (c_uf / 100.0)
            q_uf = m_uf / rho_uf

            # ④ 溢流：质量守恒 + 体积守恒；反推的溢流密度即自洽性证据
            q_of = max(1.0, q_feed - q_uf)
            m_of = m_feed - m_uf
            rho_of = m_of / q_of

            # 自洽性残差：溢流密度对清水期望值的相对偏差
            residual = abs(rho_of - RHO_CLEAR) / RHO_CLEAR * 100.0

            # ⑤ 泥床动力学：用「排出能力」s_uf_raw 驱动（而非被截断的 s_uf），
            #    否则负反馈会被截断打断、床高会单向漂移
            d_bed = K_BED * (s_feed - s_uf_raw) * dt_h + 0.010 * n_solids * dt_h
            bed = _clamp(bed + d_bed, 1.6, 9.6)

            # —— 耙架 ——
            rake_torque = 16.0 + 13.0 * (bed - 2.4) + 1.6 * n_solids
            rake_torque = _clamp(rake_torque, 8.0, 98.0)
            rake_current = _clamp(38.0 + 1.9 * (rake_torque - 20.0), 20.0, 130.0)

            p80 = _nearest_p80(p80_series, ts)

            rows.append(dict(
                ts=ts,
                feed_flow=round(q_feed, 1),
                feed_solids=round(c_feed, 2),
                feed_p80=p80,
                flocculant_flow=round(floc_flow, 2),
                flocculant_dosage=round(dosage, 1),
                bed_height=round(bed, 2),
                rake_torque=round(rake_torque, 1),
                rake_current=round(rake_current, 1),
                underflow_flow=round(q_uf, 1),
                underflow_conc=round(c_uf, 2),
                overflow_flow=round(q_of, 1),
                solids_residual=round(residual, 3),
                rho_of=rho_of,
            ))

            if ts >= fine_start:
                stats["res_min"] = min(stats["res_min"], residual)
                stats["res_max"] = max(stats["res_max"], residual)
                stats["res_sum"] += residual
                stats["res_n"] += 1
                stats["cuf_min"] = min(stats["cuf_min"], c_uf)
                stats["cuf_max"] = max(stats["cuf_max"], c_uf)
                stats["bed_min"] = min(stats["bed_min"], bed)
                stats["bed_max"] = max(stats["bed_max"], bed)

        # ── 降采样：粗粒度段每 12 点取 1（=1h），近 48h 全保留 ──
        kept = []
        for i, r in enumerate(rows):
            if r["ts"] >= fine_start:
                kept.append(r)
            elif i % 12 == 0:
                kept.append(r)
        print(f"[采样] 递推 {len(rows)} → 入库 {len(kept)}"
              f"（粗粒度 {len(kept) - sum(1 for r in kept if r['ts'] >= fine_start)} 逐时"
              f" + 细粒度 {sum(1 for r in kept if r['ts'] >= fine_start)} 每 5 分钟）")

        db_rows = [
            TailingsReading(
                equipment_id=EQUIPMENT_ID,
                feed_flow=r["feed_flow"], feed_solids=r["feed_solids"], feed_p80=r["feed_p80"],
                flocculant_flow=r["flocculant_flow"], flocculant_dosage=r["flocculant_dosage"],
                bed_height=r["bed_height"], rake_torque=r["rake_torque"], rake_current=r["rake_current"],
                underflow_flow=r["underflow_flow"], underflow_conc=r["underflow_conc"],
                overflow_flow=r["overflow_flow"], solids_residual=r["solids_residual"],
                source="demo", created_at=r["ts"],
            )
            for r in kept
        ]
        db.bulk_save_objects(db_rows)
        db.commit()
        print(f"[写入] 浓密机读数 {len(db_rows)} 条")

        # ── 工况事件落库 ──
        ev_rows = []
        for ev in events:
            seg = [r for r in rows if ev["start"] <= r["ts"] <= ev["end"]]
            if not seg:
                continue
            metric = {
                "flocculant_shortage": "flocculant_dosage",
                "bed_rise": "bed_height",
                "feed_surge": "feed_flow",
                "over_discharge": "underflow_flow",
            }[ev["kind"]]
            vals = [r[metric] for r in seg]
            base_vals = [r[metric] for r in rows if r["ts"] < ev["start"]][-60:] or vals
            peak = min(vals) if ev["kind"] == "flocculant_shortage" else max(vals)
            evidence = {
                "window_hours": ev["dur_h"],
                "peak_bed_height": max(r["bed_height"] for r in seg),
                "min_underflow_conc": min(r["underflow_conc"] for r in seg),
                "max_rake_torque": max(r["rake_torque"] for r in seg),
            }
            if ev["kind"] == "flocculant_shortage" and w3_ev:
                evidence["linked_water_event"] = {
                    "station_id": "W3", "event": "turbidity_surge",
                    "peak_ss": w3_ev.peak_value, "created_at": w3_ev.created_at.isoformat(),
                }
            ev_rows.append(TailingsEvent(
                equipment_id=EQUIPMENT_ID,
                event_type=ev["kind"], level=ev["level"], title=ev["title"], detail=ev["detail"],
                metric=metric, peak_value=round(peak, 2),
                baseline_value=round(statistics.mean(base_vals), 2),
                evidence=evidence, created_at=ev["start"],
            ))
        db.bulk_save_objects(ev_rows)
        db.commit()
        print(f"[写入] 工况事件 {len(ev_rows)} 条")

        # ── 自洽性报告 ──
        print("\n=== 固体物料平衡自洽性（近 48h）===")
        if stats["res_n"]:
            print(f"  溢流密度残差：min {stats['res_min']:.3f}% / max {stats['res_max']:.3f}% / "
                  f"均值 {stats['res_sum'] / stats['res_n']:.3f}%")
        print("=== 关键参数区间（近 48h）===")
        print(f"  底流浓度 {stats['cuf_min']:.1f} ~ {stats['cuf_max']:.1f} %")
        print(f"  泥床高度 {stats['bed_min']:.2f} ~ {stats['bed_max']:.2f} m")

        print("\n=== 各事件窗口内的参数表现 ===")
        for ev in events:
            seg = [r for r in rows if ev["start"] <= r["ts"] <= ev["end"]]
            if not seg:
                continue
            print(f"  [{ev['kind']:20}] bed {min(r['bed_height'] for r in seg):.2f}~"
                  f"{max(r['bed_height'] for r in seg):.2f} m | "
                  f"底流 {min(r['underflow_conc'] for r in seg):.1f}~"
                  f"{max(r['underflow_conc'] for r in seg):.1f} % | "
                  f"扭矩 {max(r['rake_torque'] for r in seg):.1f} % | "
                  f"单耗 {min(r['flocculant_dosage'] for r in seg):.1f}~"
                  f"{max(r['flocculant_dosage'] for r in seg):.1f} g/t")

        last = rows[-1]
        print(f"\n=== 最新时刻 {last['ts']} ===")
        print(f"  给料 {last['feed_flow']:.1f} m³/h @ {last['feed_solids']:.2f}% "
              f"| 絮凝剂 {last['flocculant_dosage']:.1f} g/t")
        print(f"  泥床 {last['bed_height']:.2f} m | 扭矩 {last['rake_torque']:.1f}% "
              f"| 底流 {last['underflow_conc']:.2f}% @ {last['underflow_flow']:.1f} m³/h")
        print(f"  溢流 {last['overflow_flow']:.1f} m³/h | 物料平衡残差 {last['solids_residual']:.3f}%")
        print("\n[完成]")
    finally:
        db.close()


if __name__ == "__main__":
    main()
