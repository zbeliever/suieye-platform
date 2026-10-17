"""根因联动归因 — 把一条品位告警放回「磨矿 → 浮选 → 回水」工艺链上找可能原因

背景与动机（告警中心 P1，2026-10-13）：
    告警中心 P0 解决了「能处置 + 看得见证据」——点开一条告警能看到当时抓拍的
    图像与 22 维泡沫特征。但它仍只回答「哪里不对」，不回答「为什么」。
    本模块把告警时刻作为锚点，回溯同期三个工段的**真实记录**：

        磨矿  analysis_records(scene='grinding').prediction.p80   ← 上游原因
        浮选  dosing_records(froth_state / current_ph / 药剂用量) ← 同期工况
        回水  water_readings(pH / COD / SS / 残留药剂 …)          ← 同期环境

    再用选矿工艺先验（过磨泥化、欠磨解离不足、pH 抑制失效、捕收剂不足、
    泡沫脆化、回水残留药剂干扰）把候选原因排序输出。

三条不可违背的原则：
    1. **只用告警发生之前的信号** —— 不允许「用未来数据解释过去」。
    2. **不硬凑** —— 信号缺失就写进 missing；所有候选都弱时明确说「未发现显著异常」。
    3. **每个结论都可回查** —— 证据带原始值、参照基线、时间距离与来源表。

数据可达性实测（2026-10-13，浮选告警 71 条）：
    水质    中位间隔 29 min   ≤60min 37/71   ← 小时粒度，最密
    磨矿    中位间隔 22.8 h   ≤72h  64/71    ← 0.33 条/天，稀疏
    配药    中位间隔 16.1 h   ≤72h  40/71    ← 3.2 条/天，最稀
    因此本模块采用**分层窗口**：窗口内 = 同期证据；窗口外 7 天内 = 弱证据并标注距离。

不依赖 SQLAlchemy（算法层保持独立），只读方式直连 SQLite。
"""

import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from backend.algorithms.feature_baseline import _db_path

# ---------------------------------------------------------------------------
# 工艺常量（来源：项目工况定义 + 现场工艺常识）
# ---------------------------------------------------------------------------

# 磨矿产物粒度工况（μm）——项目既定口径：正常 ~130 / 过磨 ~58 / 欠磨 ~245
NORMAL_P80 = 130.0
OVERGRIND_P80 = 100.0      # 低于此值判过磨
UNDERGRIND_P80 = 200.0     # 高于此值判欠磨

# 分层窗口：建议窗口内视为同期证据；窗口外到 LOOKBACK_DAYS 内为弱证据
WINDOW_MIN = {
    "grinding": 360,       # 磨矿 → 浮选有停留滞后，给 6h
    "dosing": 180,         # 配药按班次生成，影响当期
    "water": 120,          # 回水直接进浮选
}
LOOKBACK_DAYS = 7          # 窗口外仍允许引用的最大回溯

# 时间衰减常数（分钟）：超过这个量级的滞后，证据力显著下降
TAU_MIN = {
    "grinding": 720.0,
    "dosing": 360.0,
    "water": 180.0,
}

# 工艺先验权重（0~1）：该原因「先验上有多可能是品位下降的成因」
PRIOR = {
    "overgrinding": 0.90,       # 过磨 → 矿泥夹带，选矿学强结论
    "undergrinding": 0.90,      # 欠磨 → 单体解离不足
    "ph_low": 0.80,             # pH 不足 → 黄铁矿抑制失效
    "collector_low": 0.85,      # 捕收剂不足 → 目的矿物上浮不足
    "frother_weak": 0.60,       # 起泡剂/泡沫脆化 → 泡沫不稳定
    "lime_low": 0.70,           # 石灰（pH 调整剂）不足
    "backwater_reagent": 0.50,  # 回水残留药剂偏高 → 干扰选择性
    "backwater_load": 0.45,     # 回水悬浮物/有机物偏高 → 恶化浮选环境
    "density_off": 0.55,        # 矿浆浓度偏离
}

_CACHE = {"water": None, "ts": 0.0}
_LOCK = threading.Lock()
_TTL = 600.0


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _con():
    """只读连接"""
    path = _db_path()
    if not os.path.isfile(path):
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _parse_ts(v):
    """把 SQLAlchemy datetime 或 SQLite 字符串归一成 datetime"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _ts_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _lag_minutes(t_ref: datetime, t_sample: datetime) -> float:
    """样本相对告警的时间距离（分钟，正 = 在告警之前）"""
    return (t_ref - t_sample).total_seconds() / 60.0


def _decay(lag_min: float, kind: str) -> float:
    """时间衰减：滞后越久，证据力越弱（负滞后=未来数据，直接判 0）"""
    if lag_min < 0:
        return 0.0
    return math.exp(-lag_min / TAU_MIN.get(kind, 360.0))


def _severity(abs_z: float) -> str:
    return "high" if abs_z >= 2.5 else ("medium" if abs_z >= 1.5 else "low")


def _water_baseline():
    """各点位各参数的全历史 μ/σ（用于判定水质异常），进程内缓存"""
    now = time.time()
    with _LOCK:
        if _CACHE["water"] is not None and (now - _CACHE["ts"]) < _TTL:
            return _CACHE["water"]

    params = ["ph", "turbidity", "ss", "cod", "pb", "zn", "cu", "flow", "residual_reagent"]
    out = {}
    con = _con()
    if con is None:
        return out
    try:
        selects = ", ".join(
            f"AVG({p}) m_{p}, SQRT(MAX(AVG({p}*{p}) - AVG({p})*AVG({p}), 1e-9)) s_{p}"
            for p in params
        )
        for row in con.execute(
            f"SELECT station_id, COUNT(*), {selects} FROM water_readings GROUP BY station_id"
        ):
            sid = row[0]
            stat = {"n": row[1]}
            for i, p in enumerate(params):
                stat[p] = {"mean": row[2 + i * 2] or 0.0, "std": row[3 + i * 2] or 1e-9}
            out[sid] = stat
    except sqlite3.Error:
        pass
    finally:
        con.close()

    if out:
        with _LOCK:
            _CACHE["water"], _CACHE["ts"] = out, now
    return out


# ---------------------------------------------------------------------------
# 三源检索（全部「只回溯」，取告警时刻之前最近的样本）
# ---------------------------------------------------------------------------

def _fetch_grinding(t_ref: datetime):
    """磨矿粒度：取告警之前最近一条 grinding 分析记录的 p80 预测"""
    lo = _ts_str(t_ref - timedelta(days=LOOKBACK_DAYS))
    hi = _ts_str(t_ref)
    con = _con()
    if con is None:
        return None
    try:
        row = con.execute(
            "SELECT id, prediction, features, created_at FROM analysis_records "
            "WHERE scene_id='grinding' AND created_at <= ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 1",
            (hi, lo),
        ).fetchone()
        n = con.execute(
            "SELECT COUNT(*) FROM analysis_records WHERE scene_id='grinding'"
        ).fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if not row:
        return {"available": False, "count": 0}

    pred = row[1]
    if isinstance(pred, str):
        try:
            pred = json.loads(pred)
        except ValueError:
            pred = {}
    pred = pred or {}
    t_sample = _parse_ts(row[3])
    lag = _lag_minutes(t_ref, t_sample) if t_sample else None
    return {
        "available": True,
        "record_id": row[0],
        "p80": pred.get("p80"),
        "condition": pred.get("condition"),
        "confidence": pred.get("confidence"),
        "sample_at": _ts_str(t_sample) if t_sample else None,
        "lag_minutes": round(lag, 1) if lag is not None else None,
        "in_window": (lag is not None and 0 <= lag <= WINDOW_MIN["grinding"]),
        "count": n,
    }


def _fetch_dosing(t_ref: datetime):
    """配药方案：取告警之前最近一条 dosing 记录"""
    lo = _ts_str(t_ref - timedelta(days=LOOKBACK_DAYS))
    hi = _ts_str(t_ref)
    con = _con()
    if con is None:
        return None
    try:
        row = con.execute(
            "SELECT id, ore_type, current_ph, pulp_density, froth_state, recommendation, created_at "
            "FROM dosing_records WHERE created_at <= ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 1",
            (hi, lo),
        ).fetchone()
        n = con.execute("SELECT COUNT(*) FROM dosing_records").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if not row:
        return {"available": False, "count": 0}

    rec = row[5]
    if isinstance(rec, str):
        try:
            rec = json.loads(rec)
        except ValueError:
            rec = {}
    rec = rec or {}
    t_sample = _parse_ts(row[6])
    lag = _lag_minutes(t_ref, t_sample) if t_sample else None
    return {
        "available": True,
        "record_id": row[0],
        "ore_type": row[1],
        "current_ph": row[2],
        "pulp_density": row[3],
        "froth_state": row[4],
        "target_ph": ((rec.get("process_params") or {}).get("target_ph")
                      or (rec.get("input_summary") or {}).get("target_ph")),
        "reagents": rec.get("reagents") or [],
        "input_summary": rec.get("input_summary") or {},
        "sample_at": _ts_str(t_sample) if t_sample else None,
        "lag_minutes": round(lag, 1) if lag is not None else None,
        "in_window": (lag is not None and 0 <= lag <= WINDOW_MIN["dosing"]),
        "count": n,
    }


def _fetch_water(t_ref: datetime):
    """回水水质：取告警之前窗口内各点位最新一条读数"""
    lo = _ts_str(t_ref - timedelta(days=LOOKBACK_DAYS))
    hi = _ts_str(t_ref)
    con = _con()
    if con is None:
        return None
    try:
        rows = con.execute(
            "SELECT station_id, ph, turbidity, ss, cod, pb, zn, cu, flow, residual_reagent, created_at "
            "FROM water_readings WHERE created_at <= ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 60",
            (hi, lo),
        ).fetchall()
        n = con.execute("SELECT COUNT(*) FROM water_readings").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if not rows:
        return {"available": False, "count": 0}

    # 每个点位保留最新一条
    latest = {}
    for r in rows:
        latest.setdefault(r[0], r)
    return {
        "available": True,
        "stations": latest,
        "count": n,
    }


# ---------------------------------------------------------------------------
# 候选原因生成
# ---------------------------------------------------------------------------

def _mk(code, category, title, evidence, mechanism, action, prior_key=None):
    return {
        "code": code,
        "category": category,
        "title": title,
        "evidence": evidence,
        "mechanism": mechanism,
        "suggested_action": action,
        "_prior": PRIOR.get(prior_key or code, 0.5),
    }


def _cand_grinding(g, t_ref):
    """磨矿粒度 → 过磨 / 欠磨"""
    if not g or not g.get("available"):
        return []
    p80 = g.get("p80")
    if p80 is None:
        return []
    p80 = float(p80)
    lag = g.get("lag_minutes") or 0.0
    cond = g.get("condition") or ""
    dev = (p80 - NORMAL_P80) / NORMAL_P80 * 100.0
    out = []
    ev = {
        "source": "grinding",
        "label": "分级溢流 P80",
        "value": round(p80, 1),
        "unit": "μm",
        "reference": NORMAL_P80,
        "deviation_pct": round(dev, 1),
        "sample_at": g.get("sample_at"),
        "lag_minutes": lag,
        "in_window": g.get("in_window"),
        "record_id": g.get("record_id"),
    }

    if p80 < OVERGRIND_P80:
        # 强度按偏离幅度，2 倍正常偏差封顶
        s = min(abs(dev) / 60.0, 1.0)
        out.append(_mk(
            "overgrinding", "grinding",
            "磨矿过细（过磨）→ 矿泥夹带",
            [dict(ev, note=f"系统判定工况「{cond}」")],
            "过磨使脉石矿物泥化，浮选时随泡沫机械夹带进入精矿，"
            "表现为铅精矿品位下降、而铅回收率虚高。",
            "核查分级机返砂量与旋流器压力，适当降低磨机给矿量或提高分级效率，"
            f"把 P80 从 {p80:.0f}μm 恢复到 {NORMAL_P80:.0f}μm 附近。",
            prior_key="overgrinding",
        ))
        out[-1]["_strength"] = s
    elif p80 > UNDERGRIND_P80:
        s = min(abs(dev) / 115.0, 1.0)
        out.append(_mk(
            "undergrinding", "grinding",
            "磨矿欠磨 → 单体解离不足",
            [dict(ev, note=f"系统判定工况「{cond}」")],
            "目的矿物未充分单体解离，连生体上浮后仍带脉石，"
            "精矿品位与回收率同时下降。",
            "检查磨机衬板磨损与钢球配比，或降低给矿量、延长磨矿时间，"
            f"把 P80 从 {p80:.0f}μm 回调至 {NORMAL_P80:.0f}μm 附近。",
            prior_key="undergrinding",
        ))
        out[-1]["_strength"] = s
    return out


def _cand_dosing(d, t_ref):
    """配药方案 → pH / 捕收剂 / 石灰 / 泡沫状态 / 矿浆浓度"""
    if not d or not d.get("available"):
        return []
    lag = d.get("lag_minutes") or 0.0
    base_ev = {
        "source": "dosing",
        "sample_at": d.get("sample_at"),
        "lag_minutes": lag,
        "in_window": d.get("in_window"),
        "record_id": d.get("record_id"),
    }
    out = []

    # 1) 矿浆 pH 相对目标偏低 → 黄铁矿抑制失效
    ph, tph = d.get("current_ph"), d.get("target_ph")
    if ph is not None and tph:
        gap = ph - tph
        if gap < -0.3:
            s = min(abs(gap) / 1.5, 1.0)
            out.append(_mk(
                "ph_low", "flotation",
                "矿浆 pH 偏低 → 黄铁矿抑制失效",
                [dict(base_ev, label="矿浆 pH", value=round(ph, 2),
                      reference=round(tph, 2), deviation=round(gap, 2),
                      note="低于目标值")],
                "碱性不足时石灰对黄铁矿的抑制作用减弱，黄铁矿随方铅矿一同上浮，"
                "污染铅精矿、拉低品位。",
                f"提高石灰添加量，把矿浆 pH 从 {ph:.2f} 提至目标 {tph:.2f} 以上。",
                prior_key="ph_low",
            ))
            out[-1]["_strength"] = s

    # 2) 捕收剂（乙基黄药）用量建议偏低 / 大幅偏离基准
    for rg in d.get("reagents") or []:
        name = rg.get("name") or ""
        base, recd = rg.get("base_dosage"), rg.get("recommended_dosage")
        if not base or recd is None:
            continue
        dev = (recd - base) / base * 100.0
        if "黄药" in name and dev <= -15:
            s = min(abs(dev) / 50.0, 1.0)
            out.append(_mk(
                "collector_low", "flotation",
                "捕收剂用量偏低 → 目的矿物上浮不足",
                [dict(base_ev, label=f"{name}推荐用量", value=recd, unit=rg.get("unit"),
                      reference=base, deviation_pct=round(dev, 1),
                      note=f"基准 {base} {rg.get('unit','')}")],
                "捕收剂不足时方铅矿表面疏水化不充分，上浮量减少，"
                "精矿品位与回收率同时走低。",
                f"按配药方案把{name}用量从基准 {base} {rg.get('unit','')} "
                f"提高至 {recd} {rg.get('unit','')} 左右，并复检品位响应。",
                prior_key="collector_low",
            ))
            out[-1]["_strength"] = s

    # 3) 石灰（pH 调整剂）用量建议显著高于基准 → 说明抑制环境吃紧
    for rg in d.get("reagents") or []:
        name = rg.get("name") or ""
        base, recd = rg.get("base_dosage"), rg.get("recommended_dosage")
        if not base or recd is None:
            continue
        dev = (recd - base) / base * 100.0
        if "石灰" in name and dev >= 20:
            s = min(dev / 45.0, 1.0)
            out.append(_mk(
                "lime_low", "flotation",
                "石灰用量需大幅上调 → 抑制环境不足",
                [dict(base_ev, label=f"{name}推荐用量", value=recd, unit=rg.get("unit"),
                      reference=base, deviation_pct=round(dev, 1),
                      note="配药系统判定需显著加量")],
                "配药模型判断当前矿浆需要比常规多两成以上的石灰，"
                "通常对应黄铁矿/脉石抑制不足的工况。",
                f"按方案上调石灰至 {recd} {rg.get('unit','')} 并复核 pH 与精矿品位。",
                prior_key="lime_low",
            ))
            out[-1]["_strength"] = s

    # 4) 泡沫脆化（froth_state=fragile）
    if (d.get("froth_state") or "") == "fragile":
        out.append(_mk(
            "frother_weak", "flotation",
            "泡沫脆化 → 矿化不充分",
            [dict(base_ev, label="泡沫状态", value="fragile（脆）",
                  reference="normal（正常）", note="配药入口工况")],
            "泡沫强度不足、易兼并破裂，矿化泡沫层不稳定，"
            "精选时品位波动加剧。",
            "适当提高起泡剂（MIBC）用量改善泡沫稳定性，同时排查矿浆黏度与油类污染。",
            prior_key="frother_weak",
        ))
        out[-1]["_strength"] = 0.8

    # 5) 矿浆浓度偏离常规（28~41，见配药记录分布）
    pd = d.get("pulp_density")
    if pd is not None and (pd < 28 or pd > 41):
        s = 0.6
        out.append(_mk(
            "density_off", "flotation",
            "矿浆浓度偏离常规",
            [dict(base_ev, label="矿浆浓度", value=round(pd, 1),
                  reference="28~41 常规区间", note="配药入口工况")],
            "矿浆过稀时长浮选时间不足、过稠时充气与分选变差，两者都会影响精矿品位。",
            "复核浓密机/加水系统，把矿浆浓度调回常规区间。",
            prior_key="density_off",
        ))
        out[-1]["_strength"] = s

    return out


def _cand_water(w, t_ref, station_names=None):
    """回水水质 → 残留药剂干扰 / 悬浮物有机物负荷"""
    if not w or not w.get("available"):
        return []
    bl = _water_baseline()
    out = []
    for sid, row in (w.get("stations") or {}).items():
        st = bl.get(sid)
        if not st:
            continue
        t_sample = _parse_ts(row[10])
        lag = _lag_minutes(t_ref, t_sample) if t_sample else 0.0
        if lag < 0:
            continue
        in_window = 0 <= lag <= WINDOW_MIN["water"]
        vals = {
            "ph": row[1], "turbidity": row[2], "ss": row[3], "cod": row[4],
            "pb": row[5], "zn": row[6], "cu": row[7], "flow": row[8],
            "residual_reagent": row[9],
        }

        def z_of(p):
            v = vals.get(p)
            s = st.get(p) or {}
            sd = s.get("std") or 0
            if v is None or not sd:
                return None, None
            return (float(v) - (s.get("mean") or 0)) / sd, s.get("mean")

        # 残留药剂偏高
        z, m = z_of("residual_reagent")
        if z is not None and z >= 1.5:
            out.append(_mk(
                "backwater_reagent", "water",
                "回水残留药剂偏高",
                [{"source": "water", "label": "残留药剂", "station": sid,
                  "value": round(vals["residual_reagent"], 1), "unit": "mg/L",
                  "reference": round(m, 1), "z": round(z, 2),
                  "sample_at": _ts_str(t_sample) if t_sample else None,
                  "lag_minutes": round(lag, 1), "in_window": in_window}],
                "回水中残余浮选药剂偏高会改变浮选槽内的实际药剂量，"
                "造成选择性与配药模型失配，是品位波动的常见隐性来源。",
                f"核查 {sid} 回水的水处理单元（混凝沉淀/活性炭）运行情况，"
                "必要时降低回水掺配比例。",
                prior_key="backwater_reagent",
            ))
            out[-1]["_strength"] = min(z / 3.0, 1.0)

        # 悬浮物 / COD 负荷偏高
        for p, label, unit in (("cod", "COD", "mg/L"), ("ss", "悬浮物 SS", "mg/L")):
            z, m = z_of(p)
            if z is not None and z >= 2.0:
                out.append(_mk(
                    f"backwater_{p}", "water",
                    f"回水{label}偏高",
                    [{"source": "water", "label": label, "station": sid,
                      "value": round(vals[p], 1), "unit": unit,
                      "reference": round(m, 1), "z": round(z, 2),
                      "sample_at": _ts_str(t_sample) if t_sample else None,
                      "lag_minutes": round(lag, 1), "in_window": in_window}],
                    "回水悬浮物与有机物偏高会恶化浮选环境、消耗药剂并夹带脉石。",
                    f"检查 {sid} 澄清/过滤环节，控制回水浊度与 COD。",
                    prior_key="backwater_load",
                ))
                out[-1]["_strength"] = min(z / 3.5, 1.0)
    return out


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def analyze(alarm_id: int, scene_id: str, created_at, rule_name: str = None) -> dict:
    """对一条告警做根因联动归因

    参数：
        alarm_id    告警 ID（回显用）
        scene_id    场景（仅 flotation 支持完整归因）
        created_at  告警发生时间（datetime 或字符串）
        rule_name   规则名（回显用）
    """
    t_ref = _parse_ts(created_at)
    base = {
        "alarm_id": alarm_id,
        "scene_id": scene_id,
        "rule_name": rule_name,
        "analyzed_at": _ts_str(datetime.now()),
        "supported": False,
        "window_minutes": WINDOW_MIN,
        "lookback_days": LOOKBACK_DAYS,
        "candidates": [],
        "sources": [],
        "missing": [],
        "caveats": (
            "本分析基于告警发生**之前**同期跨工段数据的统计关联，"
            "列出的「可能原因」按工艺先验与偏离强度排序，**不构成因果结论**。"
            "请结合现场实际核查后判断。"
        ),
    }

    if t_ref is None:
        base["reason"] = "无法解析告警时间，跳过归因"
        return base

    if scene_id != "flotation":
        base["reason"] = f"当前仅对浮选品位告警提供根因联动归因（本告警场景：{scene_id}）"
        return base

    base["supported"] = True
    base["alarm_at"] = _ts_str(t_ref)

    # ---- 三源检索 ----
    g = _fetch_grinding(t_ref)
    d = _fetch_dosing(t_ref)
    w = _fetch_water(t_ref)

    # ---- 候选生成 ----
    cands = []
    cands += _cand_grinding(g, t_ref)
    cands += _cand_dosing(d, t_ref)
    cands += _cand_water(w, t_ref)

    # ---- 评分：先验 × 偏离强度 × 时间衰减 ----
    for c in cands:
        strength = c.pop("_strength", 0.5)
        prior = c.pop("_prior", 0.5)
        ev0 = c["evidence"][0] if c["evidence"] else {}
        lag = ev0.get("lag_minutes") or 0.0
        cat = c["category"]
        dec = _decay(lag, cat)
        score = prior * strength * dec
        c["prior"] = round(prior, 2)
        c["strength"] = round(strength, 2)
        c["time_decay"] = round(dec, 3)
        c["confidence"] = round(score, 3)
        c["confidence_label"] = (
            "高" if score >= 0.45 else ("中" if score >= 0.22 else "低")
        )

    # ---- 同类合并：同一原因只保留最强的一条，证据并入（如多点位水质异常）----
    grouped = {}
    for c in cands:
        grouped.setdefault(c["code"], []).append(c)
    merged = []
    for code, items in grouped.items():
        items.sort(key=lambda x: x["confidence"], reverse=True)
        main = items[0]
        for it in items[1:]:
            main["evidence"] = main["evidence"] + it["evidence"]
        merged.append(main)

    # 水质类候选在标题上补出具体点位
    for c in merged:
        stations = sorted({e.get("station") for e in c["evidence"] if e.get("station")})
        if stations:
            c["title"] = f"{c['title']}（{'、'.join(stations)}）"

    merged.sort(key=lambda x: x["confidence"], reverse=True)
    merged = [c for c in merged if c["confidence"] >= 0.05][:5]
    for i, c in enumerate(merged):
        c["rank"] = i + 1
    cands = merged
    base["candidates"] = cands

    # ---- 数据源可见性（让用户知道「为什么没归因出来」）----
    srcs = []
    for key, label, obj in (("grinding", "磨矿粒度", g),
                            ("dosing", "配药方案", d),
                            ("water", "回水水质", w)):
        if obj and obj.get("available"):
            if key == "water":
                lag_vals = []
                for sid, row in (obj.get("stations") or {}).items():
                    ts = _parse_ts(row[10])
                    if ts:
                        lag_vals.append(_lag_minutes(t_ref, ts))
                lag = min(lag_vals) if lag_vals else None
                srcs.append({
                    "key": key, "label": label, "available": True,
                    "stations": sorted((obj.get("stations") or {}).keys()),
                    "lag_minutes": round(lag, 1) if lag is not None else None,
                    "in_window": (lag is not None and 0 <= lag <= WINDOW_MIN["water"]),
                    "count": obj.get("count", 0),
                })
            else:
                srcs.append({
                    "key": key, "label": label, "available": True,
                    "sample_at": obj.get("sample_at"),
                    "lag_minutes": obj.get("lag_minutes"),
                    "in_window": obj.get("in_window"),
                    "count": obj.get("count", 0),
                })
        else:
            srcs.append({"key": key, "label": label, "available": False,
                         "count": (obj or {}).get("count", 0)})
            base["missing"].append(
                f"{label}：告警前 {LOOKBACK_DAYS} 天内无记录，无法比对"
            )
    base["sources"] = srcs

    # 弱证据提示：有样本但超出建议窗口
    for s in srcs:
        if s.get("available") and s.get("lag_minutes") is not None and not s.get("in_window"):
            hours = s["lag_minutes"] / 60.0
            base["missing"].append(
                f"{s['label']}：最近一条距告警 {hours:.1f} 小时，超出建议窗口"
                f"（{WINDOW_MIN.get(s['key'], 0)} 分钟），已按时间衰减降权"
            )

    if not cands:
        # 「没异常」和「没数据」是两回事，必须分开说
        avail = [s for s in srcs if s.get("available")]
        inwin = [s for s in avail if s.get("in_window")]
        if not avail:
            base["unsupported_reason"] = "no_data"
            base["conclusion"] = (
                "该告警发生时段跨工段数据尚未接入（磨矿 / 配药 / 水质均无记录），"
                "不具备归因条件。"
            )
        elif not inwin:
            base["unsupported_reason"] = "too_far"
            base["conclusion"] = (
                "三个工段虽有历史记录，但最近样本均超出建议窗口，"
                "时间距离过远、证据力不足，未给出归因结论。"
            )
        else:
            base["unsupported_reason"] = "no_anomaly"
            base["conclusion"] = (
                "同期三个工段的指标均未出现显著异常，未找到可归因的工艺原因，"
                "建议按「工艺波动或来矿性质变化」人工核查。"
            )
    else:
        top = cands[0]
        base["conclusion"] = (
            f"最可能原因：{top['title']}（置信度{top['confidence_label']}）"
        )

    return base
