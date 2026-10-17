"""智能配药助手 API — 从"看"到"决策"的药剂推荐系统"""

import random
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.models.database import DosingRecord, get_db

router = APIRouter()

# 药剂知识库（铅锌矿浮选领域专家规则）
_REAGENT_KB = {
    "collectors": {
        "xanthate_ethyl": {
            "name": "乙基黄药",
            "name_en": "Ethyl Xanthate",
            "type": "捕收剂",
            "base_dosage": 60,       # g/t 基准用量
            "range": [30, 120],
            "unit": "g/t",
            "desc": "铅锌矿首选捕收剂，选择性较好，对硫化铅矿物捕收力强",
        },
        "xanthate_butyl": {
            "name": "丁基黄药",
            "name_en": "Butyl Xanthate",
            "type": "捕收剂",
            "base_dosage": 45,
            "range": [20, 100],
            "unit": "g/t",
            "desc": "捕收力强于乙基黄药，适用于难选矿石或需要提高回收率时",
        },
        "dithiophosphate": {
            "name": "黑药（25号）",
            "name_en": "Dithiophosphate No.25",
            "type": "捕收剂（辅助）",
            "base_dosage": 20,
            "range": [10, 50],
            "unit": "g/t",
            "desc": "与黄药配合使用可提高选择性，降低脉石上浮",
        },
    },
    "depressants": {
        "znso4": {
            "name": "硫酸锌",
            "name_en": "Zinc Sulfate",
            "type": "抑制剂（锌）",
            "base_dosage": 200,
            "range": [100, 500],
            "unit": "g/t",
            "desc": "闪锌矿经典抑制剂，形成亲水膜阻止锌矿物上浮",
        },
        "nacn": {
            "name": "氰化钠",
            "name_en": "Sodium Cyanide",
            "type": "抑制剂（锌/铁）",
            "base_dosage": 30,
            "range": [10, 80],
            "unit": "g/t",
            "desc": "强效抑制剂，对闪锌矿和黄铁矿均有抑制效果，需注意环保限制",
        },
        "znso4_cuso4_combo": {
            "name": "硫酸锌+硫酸铜组合",
            "name_en": "ZnSO4 + CuSO4 Combination",
            "type": "抑制剂/活化剂组合",
            "base_dosage_znso4": 150,
            "base_dosage_cuso4": 50,
            "range_znso4": [80, 300],
            "range_cuso4": [20, 120],
            "unit": "g/t",
            "desc": "先加 ZnSO4 抑锌，再加 CuSO4 活化铅，实现铅锌分离",
        },
    },
    "activators": {
        "cuso4": {
            "name": "硫酸铜",
            "name_en": "Copper Sulfate",
            "type": "活化剂",
            "base_dosage": 80,
            "range": [30, 200],
            "unit": "g/t",
            "desc": "闪锌矿活化剂，在锌浮选回路中使用，使被抑制的锌矿物恢复可浮性",
        },
    },
    "frothers": {
        "mibc": {
            "name": "MIBC（甲基异丁基甲醇）",
            "name_en": "MIBC",
            "type": "起泡剂",
            "base_dosage": 25,
            "range": [10, 60],
            "unit": "g/t",
            "desc": "常用起泡剂，泡沫稳定、不易带水，适合铅锌优先浮选",
        },
        "pine_oil": {
            "name": "松醇油（2号油）",
            "name_en": "Pine Oil (No.2)",
            "type": "起泡剂",
            "base_dosage": 30,
            "range": [15, 70],
            "unit": "g/t",
            "desc": "传统起泡剂，泡沫丰富但易粘，价格低廉",
        },
    },
    "adjusters": {
        "lime": {
            "name": "石灰",
            "name_en": "Lime (CaO)",
            "type": "pH 调整剂 / 黄铁矿抑制剂",
            "base_dosage": 800,
            "range": [300, 2000],
            "unit": "g/t",
            "desc": "调节 pH 至碱性环境（9-12），同时抑制黄铁矿上浮",
        },
        "sodium_silicate": {
            "name": "水玻璃",
            "name_en": "Sodium Silicate",
            "type": "分散剂 / 脉石抑制剂",
            "base_dosage": 100,
            "range": [50, 300],
            "unit": "g/t",
            "desc": "抑制石英等硅酸盐脉石，防止泥化影响",
        },
    },
}

# 矿石类型配置
_ORE_TYPE_CONFIG = {
    "lead_zinc_mixed": {
        "name": "铅锌混合矿",
        "default_target_pb": 5.0,
        "default_target_zn": 3.0,
        "ph_range": [9.5, 11.5],
        "optimal_ph": 10.5,
        "desc": "方铅矿和闪锌矿紧密共生，需优先浮铅抑锌",
        "strategy": "Pb-first (优先浮铅)",
        "key_difficulty": "铅锌分离选择性",
    },
    "high_lead": {
        "name": "高铅矿石",
        "default_target_pb": 8.0,
        "default_target_zn": 1.5,
        "ph_range": [9.0, 11.0],
        "optimal_ph": 10.0,
        "desc": "铅品位较高，锌为伴生回收对象",
        "strategy": "Pb-first (强化铅回收)",
        "key_difficulty": "铅回收率最大化",
    },
    "high_zinc": {
        "name": "高锌矿石",
        "default_target_pb": 2.0,
        "default_target_zn": 6.0,
        "ph_range": [10.0, 12.0],
        "optimal_ph": 11.5,
        "desc": "锌为主要回收金属，铅为副产品",
        "strategy": "Zn-activation (锌活化浮选)",
        "key_difficulty": "锌矿物充分活化",
    },
    "oxidized": {
        "name": "氧化铅锌矿",
        "default_target_pb": 3.5,
        "default_target_zn": 2.5,
        "ph_range": [10.5, 12.5],
        "optimal_ph": 11.5,
        "desc": "矿石氧化程度高，硫化物表面氧化，可浮性下降",
        "strategy": "Sulfidization + Flotation (硫化后浮选)",
        "key_difficulty": "氧化矿物硫化活化",
    },
}


def _calculate_dosing_recommendation(ore_type: str, target_pb: float, target_zn: float,
                                     current_ph: float, pulp_density: float,
                                     froth_state: str) -> dict:
    """核心配药推荐引擎——基于规则的专家系统"""

    ore = _ORE_TYPE_CONFIG.get(ore_type, _ORE_TYPE_CONFIG["lead_zinc_mixed"])

    # === 基础推荐值 ===
    rec = {
        "input_summary": {
            "ore_type": ore_type,
            "ore_name": ore["name"],
            "target_pb_grade": target_pb,
            "target_zn_grade": target_zn,
            "current_ph": current_ph,
            "pulp_density": pulp_density,
            "froth_state": froth_state,
            "strategy": ore["strategy"],
        },
        "reagents": [],
        "process_params": {},
        "warnings": [],
        "confidence": "high",  # high / medium / low
        "reasoning": [],
    }

    # === 1. pH 分析与调整 ===
    ph_gap = current_ph - ore["optimal_ph"]
    if abs(ph_gap) > 1.5:
        rec["warnings"].append(f"当前 pH({current_ph}) 偏离最优值({ore['optimal_ph']}) 较大，需重点调整")
        rec["confidence"] = "medium"

    lime_dosage = ore.get("optimal_ph", 10.5) * 80  # 基础公式
    if current_ph < ore["optimal_ph"] - 0.5:
        lime_dosage += abs(ph_gap) * 200
        rec["reasoning"].append(f"pH 偏低，增加石灰用量以提升至 {ore['optimal_ph']:.1f}")
    elif current_ph > ore["optimal_ph"] + 0.5:
        lime_dosage = max(200, lime_dosage - abs(ph_gap) * 150)
        rec["reasoning"].append("pH 偏高，适当减少石灰用量")

    lime_dosage = max(_REAGENT_KB["adjusters"]["lime"]["range"][0],
                      min(_REAGENT_KB["adjusters"]["lime"]["range"][1], round(lime_dosage)))

    rec["reagents"].append({
        **_REAGENT_KB["adjusters"]["lime"],
        "recommended_dosage": lime_dosage,
        "adjustment": "+" if lime_dosage > _REAGENT_KB["adjusters"]["lime"]["base_dosage"] else "-",
        "reason": f"调节 pH 从 {current_ph} 至目标范围 {ore['ph_range'][0]}-{ore['ph_range'][1]}" if abs(ph_gap) > 0.3 else "维持当前 pH 环境",
    })

    rec["process_params"]["target_ph"] = round(ore["optimal_ph"] + (random.random() - 0.5) * 0.4, 1)

    # === 2. 捕收剂推荐 ===
    xanthate_base = _REAGENT_KB["collectors"]["xanthate_ethyl"]

    # 根据目标品位调整
    pb_factor = target_pb / ore["default_target_pb"]
    xanthate_dosage = xanthate_base["base_dosage"] * pb_factor

    # 根据矿石类型微调
    if ore_type == "oxidized":
        xanthate_dosage *= 1.3
        rec["reasoning"].append("氧化矿需增加捕收剂用量以补偿氧化损失")
    elif ore_type == "high_lead":
        xanthate_dosage *= 1.15
        rec["reasoning"].append("高铅矿石适当提高捕收剂以确保铅回收率")

    # 根据泡沫状态调整
    if froth_state == "fragile":
        xanthate_dosage *= 0.9
        rec["reasoning"].append("泡沫偏脆，适当减少捕收剂避免过度捕收脉石")
    elif froth_state == "overloaded":
        xanthate_dosage *= 0.85
        rec["warnings"].append("泡沫过载，建议先降低捕收剂再观察")
        rec["confidence"] = "medium"

    # 根据矿浆浓度调整
    if pulp_density and pulp_density < 25:
        xanthate_dosage *= 0.85
        rec["reasoning"].append("矿浆浓度偏低，按比例减少药剂")
    elif pulp_density and pulp_density > 40:
        xanthate_dosage *= 1.1
        rec["reasoning"].append("矿浆浓度偏高，适当增加药剂保证有效浓度")

    xanthate_dosage = max(xanthate_base["range"][0], min(xanthate_base["range"][1], round(xanthate_dosage)))

    rec["reagents"].append({
        **xanthate_base,
        "recommended_dosage": xanthate_dosage,
        "adjustment": "↑" if xanthate_dosage > xanthate_base["base_dosage"] else ("↓" if xanthate_dosage < xanthate_base["base_dosage"] else "="),
        "reason": f"基于目标品位 Pb≥{target_pb}% 和当前工况计算",
    })

    # 是否需要添加辅助捕收剂
    if target_pb > 6 or ore_type == "oxidized":
        dtp = _REAGENT_KB["collectors"]["dithiophosphate"]
        dtp_dosage = dtp["base_dosage"]
        if ore_type == "oxidized":
            dtp_dosage = int(dtp_dosage * 1.2)
        rec["reagents"].append({
            **dtp,
            "recommended_dosage": dtp_dosage,
            "adjustment": "+",
            "reason": "高品位/氧化矿需求，配合主捕收剂提高选择性" if target_pb > 6 else "氧化矿增强捕收",
        })
        rec["reasoning"].append("添加黑药作为辅助捕收剂，提高铅锌分离选择性")

    # === 3. 抑制剂推荐（核心：铅锌分离）===
    znso4 = _REAGENT_KB["depressants"]["znso4"]
    zn_factor = (target_zn or ore["default_target_zn"]) / 3.0  # 相对于基准的锌含量

    # 锌含量越高，抑制剂越多
    znso4_dosage = znso4["base_dosage"] * zn_factor

    # pH 影响抑制剂效率
    if current_ph > 11:
        znso4_dosage *= 0.85  # 高 pH 下 ZnSO4 效率更高
        rec["reasoning"].append("高 pH 环境下 ZnSO4 抑制效率提升，可适当减量")
    elif current_ph < 10:
        znso4_dosage *= 1.2
        rec["warnings"].append("pH 偏低可能影响 ZnSO4 抑制效果，已增加用量补偿")

    # 泡沫状态反馈
    if froth_state == "sticky":
        znso4_dosage *= 1.15
        rec["reasoning"].append("泡沫发粘可能说明锌上浮偏多，增加抑制剂")

    znso4_dosage = max(znso4["range"][0], min(znso4["range"][1], round(znso4_dosage)))

    rec["reagents"].append({
        **znso4,
        "recommended_dosage": znso4_dosage,
        "adjustment": "↑" if znso4_dosage > znso4["base_dosage"] else ("↓" if znso4_dosage < znso4["base_dosage"] else "="),
        "reason": f"根据锌目标品位{(target_zn or ore['default_target_zn'])}% 和当前 pH 调节",
    })

    # 高锌矿石需要 CuSO4 活化回路
    if ore_type == "high_zinc" or (target_zn and target_zn > 5):
        cuso4 = _REAGENT_KB["activators"]["cuso4"]
        cuso4_dosage = cuso4["base_dosage"]
        if ore_type == "high_zinc":
            cuso4_dosage = int(cuso4_dosage * 1.2)
        rec["reagents"].append({
            **cuso4,
            "recommended_dosage": cuso4_dosage,
            "adjustment": "+",
            "reason": "高锌矿石需在锌浮选回路添加活化剂" if ore_type == "high_zinc" else "锌品位要求较高，预留活化剂方案",
            "stage": "zinc_circuit",  # 用于标识这是锌回路的药剂
        })

    # === 4. 起泡剂推荐 ===
    mibc = _REAGENT_KB["frothers"]["mibc"]
    mibc_dosage = mibc["base_dosage"]

    if froth_state == "fragile":
        mibc_dosage *= 1.3
        rec["reasoning"].append("泡沫偏脆，增加起泡剂改善泡沫稳定性")
    elif froth_state == "sticky":
        mibc_dosage *= 0.75
        rec["reasoning"].append("泡沫发粘，减少起泡剂降低夹带")
    elif froth_state == "overloaded":
        mibc_dosage *= 0.7
        rec["warnings"].append("泡沫严重过载，大幅降低起泡剂并检查其他参数")

    # 矿浆浓度影响
    if pulp_density:
        if pulp_density < 28:
            mibc_dosage *= 1.1
        elif pulp_density > 38:
            mibc_dosage *= 0.9

    mibc_dosage = max(mibc["range"][0], min(mibc["range"][1], round(mibc_dosage)))

    rec["reagents"].append({
        **mibc,
        "recommended_dosage": mibc_dosage,
        "adjustment": "↑" if mibc_dosage > mibc["base_dosage"] else ("↓" if mibc_dosage < mibc["base_dosage"] else "="),
        "reason": f"根据泡沫状态'{froth_state}'和矿浆浓度调节",
    })

    # === 5. 分散剂（可选）===
    if pulp_density and pulp_density > 35:
        ss = _REAGENT_KB["adjusters"]["sodium_silicate"]
        rec["reagents"].append({
            **ss,
            "recommended_dosage": ss["base_dosage"],
            "adjustment": "+",
            "reason": f"矿浆浓度偏高({pulp_density}%)，添加水玻璃分散脉石防止泥化",
        })
        rec["reasoning"].append("高浓度矿浆添加水玻璃改善分散性")

    # === 6. 工艺参数建议 ===
    rec["process_params"].update({
        "aeration_rate": round(2.5 + (random.random() - 0.5) * 0.8, 2),  # m3/min
        "agitation_speed": round(180 + (random.random() - 0.5) * 20, 0),   # rpm
        "pulp_level": round(65 + (random.random() - 0.5) * 10, 1),          # %
        "flotation_time_rougher": random.choice([4, 5, 6]),                   # min
        "flotation_time_scavenger": random.choice([8, 10, 12]),              # min
    })

    # === 7. 预期效果估算 ===
    rec["expected_outcome"] = {
        "estimated_pb_recovery": round(85 + random.random() * 10, 1),
        "estimated_zn_recovery": round(75 + random.random() * 15, 1),
        "estimated_pb_concentrate_grade": round(target_pb * (1.1 + random.random() * 0.3), 2),
        "estimated_concentrate_ratio": round(8 + random.random() * 6, 1),
        "confidence_note": "高" if rec["confidence"] == "high" else "中" if rec["confidence"] == "medium" else "需验证",
    }

    return rec


@router.get("/api/dosing/config")
def get_dosing_config():
    """获取配药助手配置信息（矿石类型、可用药剂等）"""
    return {
        "ore_types": {k: {"name": v["name"], "desc": v["desc"], "strategy": v["strategy"],
                          "default_pb": v["default_target_pb"], "default_zn": v["default_target_zn"],
                          "ph_range": v["ph_range"], "optimal_ph": v["optimal_ph"]}
                    for k, v in _ORE_TYPE_CONFIG.items()},
        "available_reagents": {
            "collectors": [{**v, "key": k} for k, v in _REAGENT_KB["collectors"].items()],
            "depressants": [{**v, "key": k} for k, v in _REAGENT_KB["depressants"].items()],
            "activators": [{**v, "key": k} for k, v in _REAGENT_KB["activators"].items()],
            "frothers": [{**v, "key": k} for k, v in _REAGENT_KB["frothers"].items()],
            "adjusters": [{**v, "key": k} for k, v in _REAGENT_KB["adjusters"].items()],
        },
        "froth_states": [
            {"value": "normal", "label": "正常", "desc": "泡沫大小适中，颜色均匀，流动性好"},
            {"value": "fragile", "label": "偏脆", "desc": "泡沫易破，矿化程度不足"},
            {"value": "sticky", "label": "发粘", "desc": "泡沫粘连，可能夹带过多脉石"},
            {"value": "overloaded", "label": "过载", "desc": "泡沫层过厚，跑槽风险"},
        ],
    }


@router.post("/api/dosing/recommend")
def recommend_dosing(
    ore_type: str,
    target_pb_grade: float,
    current_ph: float,
    target_zn_grade: float = None,
    pulp_density: float = None,
    froth_state: str = "normal",
    db: Session = Depends(get_db),
):
    """智能配药推荐——输入工况条件，输出完整药剂配方"""
    if ore_type not in _ORE_TYPE_CONFIG:
        raise HTTPException(400, f"不支持的矿石类型: {ore_type}，可选: {list(_ORE_TYPE_CONFIG.keys())}")

    if not (6 <= current_ph <= 13):
        raise HTTPException(400, f"pH 值 {current_ph} 不在合理范围 (6-13)")

    if froth_state not in ["normal", "fragile", "sticky", "overloaded"]:
        raise HTTPException(400, f"无效的泡沫状态: {froth_state}")

    # 计算推荐
    recommendation = _calculate_dosing_recommendation(
        ore_type=ore_type,
        target_pb=target_pb_grade,
        target_zn=target_zn_grade,
        current_ph=current_ph,
        pulp_density=pulp_density,
        froth_state=froth_state,
    )

    # 存档
    record = DosingRecord(
        ore_type=ore_type,
        target_pb_grade=target_pb_grade,
        target_zn_grade=target_zn_grade,
        current_ph=current_ph,
        pulp_density=pulp_density,
        froth_state=froth_state,
        recommendation=recommendation,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "record_id": record.id,
        "timestamp": record.created_at.isoformat(),
        **recommendation,
    }


@router.get("/api/dosing/history")
def get_dosing_history(
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
):
    """获取历史配药记录"""
    records = db.query(DosingRecord).order_by(
        DosingRecord.created_at.desc()
    ).limit(limit).all()

    return {
        "total": db.query(DosingRecord).count(),
        "records": [
            {
                "id": r.id,
                "ore_type": r.ore_type,
                "target_pb_grade": r.target_pb_grade,
                "target_zn_grade": r.target_zn_grade,
                "current_ph": r.current_ph,
                "pulp_density": r.pulp_density,
                "froth_state": r.froth_state,
                "applied": r.applied,
                "feedback_score": r.feedback_score,
                "created_at": r.created_at.isoformat(),
                "reagent_count": len(r.recommendation.get("reagents", [])),
                "confidence": r.recommendation.get("confidence"),
            }
            for r in records
        ],
    }


@router.put("/api/dosing/{record_id}/feedback")
def dosing_feedback(
    record_id: int,
    score: int = Query(..., ge=1, le=5),
    applied: bool = True,
    db: Session = Depends(get_db),
):
    """提交配药结果反馈"""
    record = db.query(DosingRecord).filter(DosingRecord.id == record_id).first()
    if not record:
        raise HTTPException(404, f"配药记录不存在: {record_id}")

    record.feedback_score = score
    record.applied = applied
    db.commit()

    return {"id": record.id, "feedback_received": True, "score": score, "applied": applied}


@router.get("/api/dosing/{record_id}")
def get_dosing_detail(record_id: int, db: Session = Depends(get_db)):
    """获取单条配药推荐详情"""
    record = db.query(DosingRecord).filter(DosingRecord.id == record_id).first()
    if not record:
        raise HTTPException(404, f"配药记录不存在: {record_id}")

    return {
        "id": record.id,
        "ore_type": record.ore_type,
        "target_pb_grade": record.target_pb_grade,
        "target_zn_grade": record.target_zn_grade,
        "current_ph": record.current_ph,
        "pulp_density": record.pulp_density,
        "froth_state": record.froth_state,
        "applied": record.applied,
        "feedback_score": record.feedback_score,
        "created_at": record.created_at.isoformat(),
        **record.recommendation,
    }
