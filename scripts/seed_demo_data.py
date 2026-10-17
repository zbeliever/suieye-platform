"""演示数据生成脚本 v3（幂等，可重复运行）

让数据更像真实铅锌选矿生产记录：
- 品位范围对齐实际铅锌矿（Pb 3-8%, Zn 2-6%）
- 时间分布不均匀（工作日密、周末疏、有随机聚集）
- 告警消息多样化
- 加入班次差异、设备波动、连续异常等真实特征
- 数值带合理噪声
- v3 新增：安全巡检记录 + 配药助手历史

用法：
    python scripts/seed_demo_data.py
"""
import json
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import cast, String

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models.database import (
    AlarmRecord,
    AnalysisRecord,
    DosingRecord,
    ScenePackage,
    SessionLocal,
    init_db,
)

# 用不同的种子让每次运行略有不同但可复现
random.seed(2026)
NOW = datetime.now(UTC).replace(tzinfo=None)
DEMO_MARKER = '"demo": true'


def clean_old_demo(db):
    """删除上次生成的演示数据（幂等）"""
    n1 = db.query(AlarmRecord).filter(
        cast(AlarmRecord.evidence, String).like(f"%{DEMO_MARKER}%")
    ).delete()
    n2 = db.query(AnalysisRecord).filter(AnalysisRecord.source == "demo").delete()
    db.commit()
    print(f"[clean] 删除旧演示数据: 告警 {n1} 条, 分析记录 {n2} 条")


def _workday_bias(hours_ago_max=12):
    """模拟工作日分析更频繁：工作日 70% 概率密集，周末 30%"""
    days_ago = random.uniform(0.3, 88)
    # 工作日稍微往前堆一点（演示时"最近"数据丰富）
    if random.random() < 0.4:
        days_ago = min(days_ago, random.uniform(0.5, 14))
    # 加一点班次偏好（早班 8-16 点更多）
    hour_offset = random.choices(
        population=[random.uniform(0, 8), random.uniform(8, 16), random.uniform(16, 24)],
        weights=[20, 50, 30],
    )[0]
    return NOW - timedelta(days=days_ago, hours=hour_offset)


def _realistic_pb_zn(base_pb=None):
    """生成符合铅锌矿实际的品位值，带相关性和噪声

    真实特征：
    - Pb 一般 3-8%（精矿），这里预测的是原矿/精矿边界品位置
    - Zn 与 Pb 正相关（共生矿）
    - 偶尔有极端值（给矿性质突变）
    """
    if base_pb is None:
        # 基准品位围绕 5.2% 波动（系统历史均值附近）
        base_pb = random.gauss(5.2, 1.4)

    pb = round(max(0.8, min(12.5, base_pb + random.gauss(0, 0.3))), 2)
    # Zn 与 Pb 正相关，比例约 0.5-0.7
    zn_ratio = random.uniform(0.48, 0.72)
    zn = round(max(0.5, min(9.0, pb * zn_ratio + random.gauss(0, 0.2))), 2)
    return pb, zn


# ---- 告警消息模板（多样化）----

FLOTATION_DANGER_MSGS = [
    "铅品位严重偏低: {pb}%（阈值 2.5%），泡沫层大面积塌陷，泡沫颜色发暗且气泡尺寸偏小",
    " critical: 铅品位骤降至 {pb}%，泡沫图像显示矿化程度严重不足，疑似捕收剂断料",
    "铅回收率预警: 当前预测铅品位仅 {pb}%，泡沫虚泡占比超过 75%，建议立即检查给药泵",
    "品位异常: 检测到铅品位 {pb}%，远低于工艺目标区间 [4.0%, 7.0%]，泡沫形态呈\"水化\"特征",
    "严重偏离: 预测 Pb={pb}%, Zn={zn}%，双金属同时偏低，可能原矿性质发生显著变化",
]

FLOTATION_WARNING_MSGS = [
    "铅品位略低: {pb}%（目标下限 4.0%），泡沫稳定性一般，建议关注矿浆 pH",
    "品位波动: Pb={pb}%, 较前次分析下降明显，泡沫颜色偏黄，考虑微调起泡剂用量",
    "轻微异常: 预测铅品位 {pb}%，接近警告阈值，泡沫厚度偏薄",
    "趋势提醒: 连续 3 组品位呈下降趋势，当前 Pb={pb}%, 建议核查给药管路",
    "Zn 偏低伴生: Zn={zn}% 低于正常区间，Pb={pb}% 处于临界值，关注活化剂效果",
    "泡沫状态: 泡沫图像显示脆性泡沫增多，预测 Pb={pb}%，建议适当增加捕收剂",
]

GRINDING_FINE_MSGS = [
    "产品粒度过细: P80={p80}μm（阈值 85μm），过磨导致泥化严重，影响后续浮选回收率",
    "过磨警报: 检测到 P80={p80}μm，细粒级含量偏高，旋流器沉砂口可能磨损",
    "粒度偏细: 当前 P80={p80}μm，-200 目含量超标，建议减少钢球补加量或降低返砂比",
]

GRINDING_COARSE_MSGS = [
    "产品粒度过粗: P80={p80}μm（阈值 200μm），单体解离不足，有用矿物未充分解离",
    "欠磨警报: P80={p80}μm 明显偏粗，镜下可见连生体，建议提高磨矿浓度或延长停留时间",
    "解离不足: 检测 P80={p80}μm，+100 目含量偏高，可能影响铅锌综合回收率 3-5 个百分点",
]


def seed_alarms(db):
    """基于真实品位分布生成告警，近 90 天"""
    flot_img = "data/uploads/flotation/20261003_172706_b124f721_orig.jpg"
    flot_ov = "data/uploads/flotation/20261003_172706_b124f721_seg.jpg"
    grin_img = "data/uploads/grinding/20261009_202102_1e5b4737_orig.jpg"
    grin_ov = "data/uploads/grinding/20261009_202102_1e5b4737_seg.jpg"

    alarms = []
    operators = ["operator_01", "operator_02", "operator_03", "zhang_wei", "li_ming"]

    # === flotation 告警 ===
    # danger: ~10 条（品位 < 2.5%）
    for i in range(10):
        created = _workday_bias()
        days_ago = (NOW - created).total_seconds() / 86400
        pb, zn = _realistic_pb_zn(base_pb=random.uniform(1.2, 2.4))
        msg_template = random.choice(FLOTATION_DANGER_MSGS)
        msg = msg_template.format(pb=pb, zn=zn)

        ack = days_ago > 2 and random.random() < 0.65
        alarms.append(AlarmRecord(
            scene_id="flotation",
            level="danger",
            rule_name="pb_severely_low",
            message=msg,
            evidence={
                "image": flot_img,
                "overlay": flot_ov,
                "prediction": {
                    "pb_grade_xrf": pb,
                    "zn_grade_xrf": zn,
                    "froth_area_pct": round(random.uniform(45, 78), 1),
                    "bubble_size_avg": round(random.uniform(2.1, 4.8), 1),
                    "color_hue": round(random.uniform(30, 65), 1),
                },
                "demo": True,
                "shift": "night" if created.hour >= 20 or created.hour < 6 else ("afternoon" if created.hour >= 14 else "morning"),
            },
            is_acknowledged=ack,
            acknowledged_by=random.choice(operators) if ack else None,
            acknowledged_at=created + timedelta(minutes=random.uniform(15, 180)) if ack else None,
            created_at=created,
        ))

    # warning: ~55 条（品位 2.5-4.0% 或 Zn 偏低）
    for i in range(55):
        created = _workday_bias()
        days_ago = (NOW - created).total_seconds() / 86400
        pb, zn = _realistic_pb_zn(base_pb=random.uniform(2.6, 3.95))
        msg_template = random.choice(FLOTATION_WARNING_MSGS)
        msg = msg_template.format(pb=pb, zn=zn)

        # 模拟"连续异常期"：随机 15% 的告警集中在某几天内（设备故障/原矿变化）
        if random.random() < 0.15:
            created -= timedelta(hours=random.uniform(0, 48))

        ack = days_ago > 1.5 and random.random() < 0.75
        alarms.append(AlarmRecord(
            scene_id="flotation",
            level="warning",
            rule_name="pb_too_low",
            message=msg,
            evidence={
                "image": flot_img,
                "overlay": flot_ov,
                "prediction": {
                    "pb_grade_xrf": pb,
                    "zn_grade_xrf": zn,
                    "froth_area_pct": round(random.uniform(55, 88), 1),
                    "bubble_size_avg": round(random.uniform(3.5, 7.2), 1),
                    "color_hue": round(random.uniform(40, 75), 1),
                },
                "demo": True,
                "shift": "night" if created.hour >= 20 or created.hour < 6 else ("afternoon" if created.hour >= 14 else "morning"),
            },
            is_acknowledged=ack,
            acknowledged_by=random.choice(operators) if ack else None,
            acknowledged_at=created + timedelta(minutes=random.uniform(10, 120)) if ack else None,
            created_at=created,
        ))

    # === grinding 告警 ~12 条 ===
    for i in range(12):
        created = _workday_bias()
        days_ago = (NOW - created).total_seconds() / 86400

        if random.random() < 0.45:
            p80 = round(random.uniform(42, 82), 1)
            msg_template = random.choice(GRINDING_FINE_MSGS)
            msg = msg_template.format(p80=p80)
            rule = "p80_too_fine"
        else:
            p80 = round(random.uniform(205, 340), 1)
            msg_template = random.choice(GRINDING_COARSE_MSGS)
            msg = msg_template.format(p80=p80)
            rule = "p80_too_coarse"

        ack = days_ago > 2 and random.random() < 0.7
        alarms.append(AlarmRecord(
            scene_id="grinding",
            level="warning",
            rule_name=rule,
            message=msg,
            evidence={
                "image": grin_img,
                "overlay": grin_ov,
                "prediction": {
                    "p80": p80,
                    "d50": round(p80 * random.uniform(0.48, 0.62), 1),
                    "fines_fraction_pct": round(random.uniform(32, 52) if p80 < 85 else random.uniform(10, 25), 1),
                },
                "demo": True,
            },
            is_acknowledged=ack,
            acknowledged_by=random.choice(operators) if ack else None,
            acknowledged_at=created + timedelta(minutes=random.uniform(20, 150)) if ack else None,
            created_at=created,
        ))

    db.add_all(alarms)
    db.commit()
    print(f"[alarms] 生成 {len(alarms)} 条告警 "
          f"(flotation: 10 danger + 55 warning, grinding: 12 warning)")


def seed_grinding_records(db):
    """粒度分析记录：不均匀时间分布，三种工况，带真实感细节"""
    records = []

    # 加载程序化生成的颗粒图像列表
    project_root = Path(__file__).resolve().parent.parent
    grinding_img_dir = project_root / "data" / "uploads" / "grinding"
    img_list_file = grinding_img_dir / "_file_list.txt"

    # 按工况分类可用图片: {condition: [filepath, ...]}
    cond_images = {"normal": [], "overground": [], "underground": []}
    if img_list_file.exists():
        with open(img_list_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or "|" not in line:
                    continue
                fname, cond = line.split("|", 1)
                fpath = str(grinding_img_dir / fname)
                if cond in cond_images and os.path.isfile(fpath):
                    cond_images[cond].append(fpath)

    # 如果没有生成图片，回退到旧图（不应发生）
    fallback_img = str(grinding_img_dir / "20261009_202102_1e5b4737_orig.jpg")
    if not os.path.isfile(fallback_img):
        fallback_img = r"D:\flaoting\dataset\allpictures\202009090951_0.jpg"

    def pick_image(condition):
        """根据工况选择一张对应图片（循环使用）"""
        # 映射：中文名 → 生成器英文名
        name_map = {"正常": "normal", "过磨": "overground", "欠磨": "underground"}
        eng_cond = name_map.get(condition, condition)
        pool = cond_images.get(eng_cond, [])
        if pool:
            idx = len(records) % len(pool)
            return pool[idx]
        return fallback_img

    # 不均匀时间间隔：模拟真实生产中"有问题多测、没问题少测"
    gaps = []
    day_cursor = 85
    while day_cursor > 0:
        # 大多数间隔 3-7 天，偶尔 1 天（连续监测）或 10+ 天（稳定期）
        gap = random.choices(
            [random.uniform(0.8, 2), random.uniform(2.5, 6), random.uniform(7, 14)],
            weights=[15, 60, 25],
        )[0]
        day_cursor -= gap
        if day_cursor >= 0:
            gaps.append(day_cursor)

    # 保证至少 18 条
    if len(gaps) < 18:
        extra = 18 - len(gaps)
        for i in range(extra):
            gaps.append(random.uniform(1, 80))

    gaps = sorted(gaps, reverse=True)

    for idx, days_ago in enumerate(gaps[:22]):  # 最多 22 条
        # 工作日白天更可能分析
        hour = random.choices(
            [random.uniform(7, 11), random.uniform(13, 17), random.uniform(18, 22), random.uniform(0, 6)],
            weights=[35, 40, 20, 5],
        )[0]
        created = NOW - timedelta(days=days_ago, hours=hour)
        group_id = created.strftime("%Y%m%d%H%M")

        # 工况分布：正常 35% / 过磨 40% / 欠磨 25%（过磨更常见于现场）
        r = random.random()
        if r < 0.35:
            p80 = round(random.gauss(130, 25), 1)
            p80 = max(90, min(185, p80))
            cond, label = "正常", 1
        elif r < 0.75:
            p80 = round(random.gauss(58, 15), 1)
            p80 = max(35, min(82, p80))
            cond, label = "过磨", 0
        else:
            p80 = round(random.gauss(245, 40), 1)
            p80 = max(195, min(350, p80))
            cond, label = "欠磨", 2

        d80 = round(p80 * random.uniform(0.88, 0.96), 1)
        d50 = round(p80 * random.uniform(0.50, 0.62), 1)
        d10 = round(p80 * random.uniform(0.22, 0.35), 1)
        d90 = round(p80 * random.uniform(1.08, 1.18), 1)
        fines_pct = round(
            random.uniform(35, 52) if cond == "过磨"
            else random.uniform(10, 26) if cond == "欠磨"
            else random.uniform(18, 32),
            1,
        )

        # 根据工况调整置信度（正常时模型更有信心）
        base_conf = 0.88 if cond == "正常" else (0.78 if cond == "过磨" else 0.82)
        confidence = round(base_conf + random.gauss(0, 0.05), 3)
        confidence = max(0.65, min(0.97, confidence))

        records.append(AnalysisRecord(
            scene_id="grinding",
            group_id=group_id,
            image_path=pick_image(cond),
            prediction={
                "p80": p80,
                "condition": cond,
                "condition_label": label,
                "confidence": confidence,
                "method": "rule_based",
                "message": f"磨矿产品 P80={p80}μm，工况判定: {cond}",
                "d10": d10, "d50": d50, "d80": d80, "d90": d90,
                "fines_fraction_pct": fines_pct,
                "uniformity": round(random.gauss(2.6, 0.6), 2),
                "grain_count": random.randint(80, 520),
                # 新增：模拟更丰富的输出
                "solid_density_g_cm3": round(random.uniform(2.6, 3.1), 2),
                "pulp_percent_solids": round(random.uniform(28, 42), 1),
                "circulating_load": round(random.uniform(150, 350), 0),
            },
            actual_value=None,
            model_version="p80-v1",
            source="demo",
            created_at=created,
        ))

    db.add_all(records)
    db.commit()
    # 统计各工况
    cond_counts = {"正常": 0, "过磨": 0, "欠磨": 0}
    for r in records:
        p = r.prediction if isinstance(r.prediction, dict) else json.loads(r.prediction)
        c = p.get("condition", "未知")
        cond_counts[c] = cond_counts.get(c, 0) + 1
    print(f"[grinding] 生成 {len(records)} 条分析记录（不均匀时间分布，{cond_counts.get('过磨', 0)} 条过磨 / {cond_counts.get('正常', 0)} 条正常 / {cond_counts.get('欠磨', 0)} 条欠磨）")


def set_safety_active(db):
    """safety 场景有数据了 — 状态改 active"""
    s = db.query(ScenePackage).filter(ScenePackage.name == "safety").first()
    if s and s.status != "active":
        old = s.status
        s.status = "active"
        db.commit()
        print(f"[safety] 状态 {old} → active（已生成巡检记录）")
    else:
        print(f"[safety] 当前状态 {s.status if s else None}，跳过")


# ---- 安全巡检消息模板 ----

SAFETY_CRITICAL_MSGS = [
    "人员闯入皮带机头危险区域！检测到 {person_count} 名人员进入高风险区，立即撤离",
    "CRITICAL: 皮带严重跑偏，偏移量达{belt_offset}px，存在物料洒落风险，紧急停机检查",
    "检测到人员未佩戴安全帽进入作业区，PPE合规率仅{ppe_rate:.0%}",
    "危险区域入侵警报：{zone} 检测到异常目标，风险评分 {risk_score:.0f}",
]

SAFETY_WARNING_MSGS = [
    "皮带轻微跑偏: 偏移{belt_offset}px（阈值30px），建议关注托辊状态",
    "检测到{foreign_count}个疑似异物落在皮带表面，请安排清理",
    "PPE佩戴不合规：现场人员安全帽佩戴率{ppe_rate:.0%}，需督促整改",
    "{zone} 区域检测到人员靠近，当前非作业时间",
    "皮带边缘磨损预警：检测到边缘毛刺，建议下次检修时更换",
]

SAFETY_INFO_MSGS = [
    "巡检正常：{zone} 区域无异常，设备运行平稳",
    "常规巡检完成：皮带对中良好，偏移仅{belt_offset}px",
    "安全巡查：现场{person_count}名人员均正确佩戴PPE，合规率100%",
    "{zone} 区域环境正常，无异物、无人员违规",
]


def _safety_time_bias():
    """安全巡检时间偏好：白班多、夜班少，整点附近"""
    days_ago = random.uniform(0.5, 45)
    # 40% 概率集中在最近两周
    if random.random() < 0.4:
        days_ago = min(days_ago, random.uniform(0.5, 14))
    # 巡检通常在整点或半点附近
    hour = random.choices(
        [random.uniform(8, 12), random.uniform(13, 17), random.uniform(18, 22), random.uniform(0, 6)],
        weights=[35, 35, 20, 10],
    )[0]
    hour = round(hour * 2) / 2  # 取半点精度
    return NOW - timedelta(days=days_ago, hours=hour)


def seed_safety_records(db):
    """生成安全巡检分析记录 + 对应告警"""
    records = []
    alarms = []
    operators = ["operator_01", "operator_02", "safety_officer_wang"]

    # 巡检区域
    zones = ["1#皮带机", "2#皮带机", "3#皮带机", "破碎车间入口", "浮选车间走廊", "磨矿车间"]
    
    # 复用一张浮选图作为安全巡检图（演示用）
    safe_img = r"D:\flaoting\dataset\allpictures\202009090951_0.jpg"

    # 风险等级分布：normal 50%, low 25%, medium 15%, high 8%, critical 2%
    risk_dist = ["normal"] * 13 + ["low"] * 6 + ["medium"] * 4 + ["high"] * 2 + ["critical"] * 1

    for i in range(26):
        created = _safety_time_bias()
        days_ago = (NOW - created).total_seconds() / 86400
        zone = random.choice(zones)
        risk_level = random.choice(risk_dist)

        # 根据风险等级生成合理的检测数据
        if risk_level == "critical":
            risk_score = round(random.uniform(18, 35), 1)
            person_count = random.randint(1, 3)
            belt_offset = round(random.uniform(45, 78), 0)
            foreign_count = random.randint(0, 2)
            ppe_rate = round(random.uniform(0.4, 0.7), 2)
            detection_count = random.randint(3, 7)
            msg_template = random.choice(SAFETY_CRITICAL_MSGS)
            alarm_level = "danger"
        elif risk_level == "high":
            risk_score = round(random.uniform(10, 18), 1)
            person_count = random.randint(0, 2)
            belt_offset = round(random.uniform(32, 55), 0)
            foreign_count = random.randint(0, 2)
            ppe_rate = round(random.uniform(0.65, 0.85), 2)
            detection_count = random.randint(2, 5)
            msg_template = random.choice(SAFETY_CRITICAL_MSGS)
            alarm_level = "danger" if random.random() < 0.3 else None
        elif risk_level == "medium":
            risk_score = round(random.uniform(5, 10), 1)
            person_count = random.randint(0, 2)
            belt_offset = round(random.uniform(20, 38), 0)
            foreign_count = random.randint(0, 2)
            ppe_rate = round(random.uniform(0.75, 0.92), 2)
            detection_count = random.randint(1, 4)
            msg_template = random.choice(SAFETY_WARNING_MSGS)
            alarm_level = "warning" if random.random() < 0.5 else None
        elif risk_level == "low":
            risk_score = round(random.uniform(2, 5), 1)
            person_count = random.randint(0, 2)
            belt_offset = round(random.uniform(8, 25), 0)
            foreign_count = 0
            ppe_rate = round(random.uniform(0.88, 1.0), 2)
            detection_count = random.randint(0, 2)
            msg_template = random.choice(SAFETY_WARNING_MSGS)
            alarm_level = None
        else:  # normal
            risk_score = round(random.uniform(0, 2), 1)
            person_count = random.randint(0, 3)
            belt_offset = round(random.uniform(0, 12), 0)
            foreign_count = 0
            ppe_rate = 1.0
            detection_count = random.randint(0, 2)
            msg_template = random.choice(SAFETY_INFO_MSGS)
            alarm_level = None

        inference_ms = round(random.uniform(45, 180), 0)

        prediction = {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "zone": zone,
            "model": "yolov8n-safety-v1",
            "confidence": round(random.uniform(0.72, 0.96), 3),
        }

        # features — 对齐前端 Workbench sideMetrics 的字段名
        # 注意：AnalysisRecord 只有 features + prediction 两个 JSON 字段
        # 前端 sideMetrics 从 r.features 和 r.metrics 读取，但 metrics 字段不存在于 AnalysisRecord
        # 所以把所有检测指标都放入 features
        features = {
            # === 人员安全 ===
            "person_count": person_count,
            "persons_in_danger_zone": max(0, person_count - 1) if risk_level in ("critical", "high") else 0,
            "persons_safe_zone": max(0, person_count - (max(0, person_count - 1) if risk_level in ("critical", "high") else person_count)),
            "ppe_compliance_rate": ppe_rate,
            "avg_person_confidence": round(random.uniform(0.7, 0.96), 3) if person_count > 0 else 0,
            # === 皮带状态 ===
            "belt_offset_px": int(belt_offset),
            "belt_offset_percent": round(abs(belt_offset) / 2.5, 1),
            "belt_direction": "left" if belt_offset > 0 else ("right" if belt_offset < 0 else "center"),
            "belt_severity": "severe" if abs(belt_offset) > 50 else ("moderate" if abs(belt_offset) > 25 else ("slight" if abs(belt_offset) > 10 else "normal")),
            "belt_misalignment_detected": abs(belt_offset) > 15,
            # === 异物检测 ===
            "foreign_object_count": foreign_count,
            "foreign_total_area": foreign_count * random.randint(200, 1200) if foreign_count else 0,
            "foreign_area_ratio": round(foreign_count * random.uniform(0.01, 0.08), 4) if foreign_count else 0,
            "avg_foreign_confidence": round(random.uniform(0.65, 0.94), 3) if foreign_count else 0,
            # === 综合指标（前端原读 r.metrics，合并到 features）===
            "detection_count": detection_count,
            "total_detections": detection_count + person_count + foreign_count,
            "pipeline_ms": inference_ms + random.randint(20, 80),
            "max_confidence": round(random.uniform(0.82, 0.98), 3),
            "avg_confidence": round(random.uniform(0.65, 0.9), 3),
            "raw_risk_score": risk_score,
        }

        msg = msg_template.format(
            person_count=person_count,
            belt_offset=int(belt_offset),
            foreign_count=foreign_count,
            ppe_rate=ppe_rate,
            zone=zone,
            risk_score=risk_score,
        )

        group_id = created.strftime("%Y%m%d%H%M")

        records.append(AnalysisRecord(
            scene_id="safety",
            group_id=group_id,
            image_path=safe_img,
            prediction=prediction,
            features=features,
            actual_value=None,
            model_version="yolov8n-safety-v1",
            source="demo",
            created_at=created,
        ))

        # 有告警的记录同时生成告警
        if alarm_level:
            ack = days_ago > 1 and random.random() < 0.7
            alarms.append(AlarmRecord(
                scene_id="safety",
                level=alarm_level,
                rule_name=f"safety_{risk_level}",
                message=msg,
                evidence={
                    "image": safe_img,
                    "prediction": prediction,
                    "demo": True,
                    "zone": zone,
                },
                is_acknowledged=ack,
                acknowledged_by=random.choice(operators) if ack else None,
                acknowledged_at=created + timedelta(minutes=random.uniform(5, 60)) if ack else None,
                created_at=created,
            ))

    db.add_all(records)
    db.add_all(alarms)
    db.commit()

    # 统计
    risk_counts = {}
    for r in records:
        p = r.prediction if isinstance(r.prediction, dict) else {}
        rl = p.get("risk_level", "?")
        risk_counts[rl] = risk_counts.get(rl, 0) + 1
    print(f"[safety] 生成 {len(records)} 条巡检记录 + {len(alarms)} 条告警 "
          f"(风险分布: {risk_counts})")


# ---- 配药助手数据 ----

ORE_TYPES = [
    ("lead_zinc_mixed", "铅锌混合矿"),
    ("high_lead", "高铅矿"),
    ("high_zinc", "高锌矿"),
    ("oxidized", "氧化矿"),
]

FROTH_STATES = ["normal", "fragile", "mineralized"]

REAGENT_TEMPLATES = {
    "lead_zinc_mixed": {
        "reagents": [
            {"name": "乙基黄药", "role": "捕收剂", "dosage_g_t": 65, "unit": "g/t"},
            {"name": "丁基黄药", "role": "捕收剂", "dosage_g_t": 35, "unit": "g/t"},
            {"name": "2#油", "role": "起泡剂", "dosage_g_t": 30, "unit": "g/t"},
            {"name": "硫酸铜", "role": "活化剂", "dosage_g_t": 120, "unit": "g/t"},
            {"name": "ZnSO4", "role": "抑制剂", "dosage_g_t": 200, "unit": "g/t"},
        ],
        "reasoning_template": "当前为铅锌混合矿，Pb={pb}%/Zn={zn}%，泡沫呈{froth}状态。采用'铅优先-锌同步'策略：黄药组合保证双金属回收率，硫酸铜活化闪锌矿，ZnSO4抑制次生泥化。pH 建议维持在 {ph:.1f}。",
    },
    "high_lead": {
        "reagents": [
            {"name": "乙基黄药", "role": "捕收剂", "dosage_g_t": 80, "unit": "g/t"},
            {"name": "25#黑药", "role": "辅助捕收剂", "dosage_g_t": 25, "unit": "g/t"},
            {"name": "2#油", "role": "起泡剂", "dosage_g_t": 25, "unit": "g/t"},
            {"name": "Na2S", "role": "硫化剂", "dosage_g_t": 150, "unit": "g/t"},
        ],
        "reasoning_template": "高铅矿石（Pb={pb}%），泡沫{froth}。强化铅回路：提高乙基黄药用量增强铅矿物捕收，25#黑药提升粗选选择性。注意控制 Zn 抑制强度避免过抑制。",
    },
    "high_zinc": {
        "reagents": [
            {"name": "丁基黄药", "role": "捕收剂", "dosage_g_t": 70, "unit": "g/t"},
            {"name": "硫酸铜", "role": "活化剂", "dosage_g_t": 200, "unit": "g/t"},
            {"name": "石灰", "role": "pH调整/抑制剂", "dosage_g_t": 800, "unit": "g/t"},
            {"name": "MIBC", "role": "起泡剂", "dosage_g_t": 20, "unit": "g/t"},
        ],
        "reasoning_template": "高锌矿石（Zn={zn}%，Pb={pb}%），泡沫{froth}。重点强化锌回路：足量硫酸铜充分活化闪锌矿，石灰维持高 pH（11-12）抑制黄铁矿。丁基黄药对锌矿物选择性好。",
    },
    "oxidized": {
        "reagents": [
            {"name": "戊基黄药", "role": "强捕收剂", "dosage_g_t": 90, "unit": "g/t"},
            {"name": "AERO 3418A", "role": "氧化矿捕收剂", "dosage_g_t": 45, "unit": "g/t"},
            {"name": "硫化钠", "role": "硫化剂", "dosage_g_t": 500, "unit": "g/t"},
            {"name": "2#油", "role": "起泡剂", "dosage_g_t": 35, "unit": "g/t"},
            {"name": "水玻璃", "role": "分散剂", "dosage_g_t": 100, "g/t": "g/t"},
        ],
        "reasoning_template": "氧化矿（Pb={pb}%/Zn={zn}%），泡沫{froth}，属难选类型。采用'先硫化后浮选'工艺：大量 Na2S 预硫化氧化矿物表面，配合强捕收剂（戊基黄药+AERO）提高回收率。需关注硫化剂消耗速度。",
    },
}

FROTH_LABELS = {"normal": "正常矿化", "fragile": "脆性/虚泡", "mineralized": "过度矿化/黏稠"}


def seed_dosing_records(db):
    """生成配药助手历史记录"""
    records = []

    for i in range(18):
        # 时间分布：近30天，不均匀
        days_ago = random.uniform(0.3, 28)
        if random.random() < 0.35:
            days_ago = min(days_ago, random.uniform(0.3, 7))
        hour = random.choices(
            [random.uniform(8, 11), random.uniform(14, 17), random.uniform(19, 21)],
            weights=[40, 40, 20],
        )[0]
        created = NOW - timedelta(days=days_ago, hours=hour)

        # 输入条件
        ore_type_key, ore_type_label = random.choice(ORE_TYPES)
        pb, zn = _realistic_pb_zn()
        ph = round(random.uniform(7.5, 12.0), 1)
        pulp_density = round(random.uniform(28, 42), 1)
        froth = random.choice(FROTH_STATES)

        # 获取药剂模板并加随机扰动
        template = REAGENT_TEMPLATES[ore_type_key]
        reagents = []
        for r in template["reagents"]:
            reagents.append({
                **r,
                "dosage_g_t": round(r["dosage_g_t"] * random.uniform(0.85, 1.15), 0),
            })

        # 推理文本
        reasoning = template["reasoning_template"].format(
            pb=pb, zn=zn, froth=FROTH_LABELS[froth], ph=ph,
        )

        # 评分：品位在合理范围内分数更高
        base_score = 85
        if 4.5 <= pb <= 7.0:
            base_score += random.uniform(0, 8)
        else:
            base_score -= random.uniform(5, 15)
        score = int(max(60, min(98, base_score + random.gauss(0, 5))))

        # 是否应用 + 反馈
        applied = random.random() < 0.65
        feedback = None
        if applied:
            # 已应用的：大部分正面反馈
            if random.random() < 0.75:
                feedback = random.choices([4, 5, 3], weights=[40, 35, 25])[0]
            else:
                feedback = random.choices([2, 3], weights=[30, 70])[0]

        records.append(DosingRecord(
            ore_type=ore_type_key,
            target_pb_grade=round(pb, 2),
            target_zn_grade=round(zn, 2),
            current_ph=ph,
            pulp_density=pulp_density,
            froth_state=froth,
            recommendation={
                "reagents": reagents,
                "reasoning": reasoning,
                "score": score,
                "strategy": template["reasoning_template"].split("：")[0].strip()
                            if "：" in template["reasoning_template"] else "常规浮选",
                "ph_suggestion": round(ph + random.uniform(-0.3, 0.3), 1),
                "expected_recovery_pb": round(random.uniform(78, 92), 1),
                "expected_recovery_zn": round(random.uniform(70, 88), 1),
            },
            applied=applied,
            feedback_score=feedback,
            created_at=created,
        ))

    db.add_all(records)
    db.commit()

    # 统计
    applied_cnt = sum(1 for r in records if r.applied)
    avg_score = sum(r.recommendation["score"] for r in records) / len(records)
    ore_dist = {}
    for r in records:
        ore_dist[r.ore_type] = ore_dist.get(r.ore_type, 0) + 1
    print(f"[dosing] 生成 {len(records)} 条配药记录（已应用 {applied_cnt}, 平均分 {avg_score:.0f}, 矿石类型: {ore_dist})")


def remap_history_timeline(db):
    """历史 3004 条记录全堆在 2020-09 → 按组序铺到最近 120 天

    v2 改进：
    - 加入工作日/周末密度差异
    - 品位值重新生成（用真实范围）
    - 组间间隔不均匀（模拟生产节奏）
    - 幂等：只处理 2021 年以前的记录
    """
    from datetime import datetime as dt

    old_records = (
        db.query(AnalysisRecord)
        .filter(AnalysisRecord.scene_id == "flotation")
        .filter(AnalysisRecord.created_at < dt(2021, 1, 1))
        .order_by(AnalysisRecord.group_id, AnalysisRecord.id)
        .all()
    )
    if not old_records:
        print("[timeline] 无 2020 年历史记录，跳过")
        return

    groups = {}
    for r in old_records:
        groups.setdefault(r.group_id, []).append(r)
    group_keys = sorted(groups.keys())
    n_groups = len(group_keys)

    span_days = 120
    now = datetime.now()
    start = now - timedelta(days=span_days)

    # 不均匀组间隔：基础间隔 + 随机扰动
    base_interval_minutes = (span_days * 24 * 60) / n_groups

    cursor = start
    for gi, gk in enumerate(group_keys):
        # 组间间隔不均匀：有时快有时慢
        jitter = random.uniform(0.5, 2.0) if gi > 0 else 1.0
        # 工作日稍密
        if cursor.weekday() >= 5:  # 周末
            jitter *= 1.4  # 周末间隔更大
        interval = base_interval_minutes * jitter

        base = cursor
        for j, r in enumerate(groups[gk]):
            # 组内记录间隔也带随机
            t = base + timedelta(minutes=j * random.uniform(1.5, 4.0))
            r.created_at = t
            r.group_id = t.strftime("%Y%m%d%H%M")

            # 重写 prediction 中的品位值为真实范围
            try:
                pred = r.prediction
                if isinstance(pred, str):
                    pred = json.loads(pred)
                if isinstance(pred, dict):
                    new_pb, new_zn = _realistic_pb_zn()
                    pred["pb_grade_xrf"] = new_pb
                    pred["zn_grade_xrf"] = new_zn
                    # 补充一些缺失字段让数据更丰富
                    pred.setdefault("froth_area_pct", round(random.uniform(50, 85), 1))
                    pred.setdefault("confidence", round(random.uniform(0.75, 0.95), 3))
                    r.prediction = pred
            except Exception:
                pass  # 保持原值不变

        cursor = cursor + timedelta(minutes=interval)

    db.commit()
    print(f"[timeline] {len(old_records)} 条记录 / {n_groups} 组 重映射到最近 {span_days} 天（品位已重生成）")


def main():
    init_db()
    db = SessionLocal()
    try:
        clean_old_demo(db)
        remap_history_timeline(db)
        seed_alarms(db)
        seed_grinding_records(db)
        seed_safety_records(db)
        seed_dosing_records(db)
        set_safety_active(db)

        # 汇总验证
        total = db.query(AlarmRecord).count()
        unack = db.query(AlarmRecord).filter(AlarmRecord.is_acknowledged == False).count()
        danger = db.query(AlarmRecord).filter(AlarmRecord.level == "danger").count()
        grin = db.query(AnalysisRecord).filter(AnalysisRecord.scene_id == "grinding").count()
        flot = db.query(AnalysisRecord).filter(AnalysisRecord.scene_id == "flotation").count()
        safety = db.query(AnalysisRecord).filter(AnalysisRecord.scene_id == "safety").count()
        dosing = db.query(DosingRecord).count()

        # 抽样检查品位范围
        sample = (
            db.query(AnalysisRecord)
            .filter(AnalysisRecord.scene_id == "flotation")
            .order_by(AnalysisRecord.created_at.desc())
            .limit(5)
            .all()
        )
        sample_pbs = []
        for r in sample:
            try:
                p = r.prediction if isinstance(r.prediction, dict) else json.loads(r.prediction)
                sample_pbs.append(p.get("pb_grade_xrf", "?"))
            except Exception:
                sample_pbs.append("?")

        print("\n========== 汇总 ==========")
        print(f"告警总数: {total}（未确认 {unack}, 危险 {danger}）")
        print(f"flotation 记录: {flot}")
        print(f"grinding 记录: {grin}")
        print(f"safety 记录: {safety}")
        print(f"dosing 记录: {dosing}")
        print(f"最近 5 条 Pb 品位抽样: {sample_pbs}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
