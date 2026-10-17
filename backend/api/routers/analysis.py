"""图像分析 API — 上传图像 → Filter 管线 → 入库"""

import hashlib
import os
from datetime import datetime

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.database import (
    AlarmRecord,
    AnalysisRecord,
    ModelVersion,
    get_db,
)
from backend.scenes.runtime import SceneNotFoundError, run_analysis
from backend.utils.image_utils import encode_image_b64, save_image

router = APIRouter()

UPLOAD_ROOT = os.path.join(settings.DATA_DIR, "uploads")
os.makedirs(UPLOAD_ROOT, exist_ok=True)


def _b64(img):
    """ndarray → base64 data URL（None 安全）"""
    if img is None:
        return None
    return "data:image/png;base64," + encode_image_b64(img)


def _slim_result(ctx, scene_name):
    """精简响应体：实时轮询面板只需结论，不必回传多张 base64 图"""
    feats = ctx.features or {}
    keep = (
        "bubble_avg_size", "bubble_shape_roundness", "bubble_shape_dispersion",
        "texture_contrast", "texture_entropy", "texture_uniformity",
        "n_valid_bubbles", "relative_red",
    )
    return {
        "scene": scene_name,
        "prediction": {
            "pb_grade_xrf": ctx.prediction.get("pb_grade_xrf"),
            "zn_grade_xrf": ctx.prediction.get("zn_grade_xrf"),
            "model": ctx.prediction.get("model"),
        },
        "metrics": {
            "n_foams": ctx.metrics.get("n_foams"),
            "foreground_ratio": ctx.metrics.get("foreground_ratio"),
            "pipeline_ms": ctx.metrics.get("pipeline_ms"),
        },
        "features": {k: feats.get(k) for k in keep},
        "alarms": [
            {"rule_name": a.get("rule_name"), "level": a.get("level"), "message": a.get("message")}
            for a in ctx.alarms
        ],
    }


@router.post("/api/analyze/{scene_name}/probe")
async def analyze_probe(scene_name: str, file: UploadFile = File(...)):
    """★ 干跑分析（不落库）—— 实时监控页「实时 AI 研判」专用。

    与 `/api/analyze/{scene}` 的区别：**只跑管线、只返回结论，绝不写
    analysis_records / alarm_records**。实时面板会按秒级反复调用，
    若走正式接口会瞬间灌爆演示库（见 MEMORY「测试分析会污染演示库」）。

    ⚠️ 本路径比上传接口多一段 `/probe`，FastAPI 按整段匹配，二者不冲突
    （已实测：probe 返回精简体、正式接口照旧落库）。
    """
    try:
        raw = await file.read()
    except Exception as e:
        raise HTTPException(400, f"读取上传文件失败: {e}")
    if not raw:
        raise HTTPException(400, "上传文件为空")

    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "无法解码图像")

    try:
        ctx = run_analysis(scene_name, img, source="probe")
    except SceneNotFoundError:
        raise HTTPException(404, f"场景包不存在: {scene_name}")

    return _slim_result(ctx, scene_name)


@router.post("/api/analyze/{scene_name}")
async def analyze_image(
    scene_name: str,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    group_id: str = None,
):
    """上传一张图，跑完整场景管线（分割→特征→预测→告警），结果入库。

    返回：预测值 / 22 维特征 / 分割可视化图(base64) / 告警 / 执行轨迹
    """
    try:
        raw = await file.read()
    except Exception as e:
        raise HTTPException(400, f"读取上传文件失败: {e}")
    if not raw:
        raise HTTPException(400, "上传文件为空")

    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, f"无法解码图像: {file.filename}")

    # ---- 跑管线 ----
    try:
        ctx = run_analysis(scene_name, img, source="upload")
    except SceneNotFoundError:
        raise HTTPException(404, f"场景包不存在: {scene_name}")

    # ---- 存档：原图 + 分割叠加图（黑匣子证据链）----
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    digest = hashlib.md5(raw).hexdigest()[:8]
    scene_dir = os.path.join(UPLOAD_ROOT, scene_name)
    os.makedirs(scene_dir, exist_ok=True)

    orig_path = os.path.join(scene_dir, f"{stamp}_{digest}_orig.jpg")
    overlay_path = os.path.join(scene_dir, f"{stamp}_{digest}_seg.jpg")
    save_image(ctx.raw_image, orig_path)
    if "boundary_overlay" in ctx.overlays:
        save_image(ctx.overlays["boundary_overlay"], overlay_path)

    # ---- 入库：分析记录 ----
    gid = group_id or datetime.now().strftime("%Y%m%d%H%M")
    record = AnalysisRecord(
        scene_id=scene_name,
        group_id=gid,
        image_path=orig_path.replace("\\", "/"),
        features=ctx.features,
        prediction={
            **ctx.prediction,
            "n_foams": ctx.metrics.get("n_foams"),
            "pipeline_ms": ctx.metrics.get("pipeline_ms"),
        },
        model_version=ctx.prediction.get("model"),
        source="upload",
    )
    db.add(record)
    db.flush()  # 先拿 record.id，供告警证据链引用

    # ---- 入库：告警记录 ----
    for alarm in ctx.alarms:
        db.add(AlarmRecord(
            scene_id=scene_name,
            level=alarm["level"],
            rule_name=alarm["rule_name"],
            message=alarm["message"],
            evidence={
                "image": orig_path.replace("\\", "/"),
                "overlay": overlay_path.replace("\\", "/"),
                "prediction": ctx.prediction,
                "metrics": ctx.metrics,
                "record_id": record.id,
            },
        ))

    db.commit()
    db.refresh(record)

    # 模型员工档案：累计推理次数 +1
    mv = db.query(ModelVersion).filter(
        ModelVersion.scene_id == scene_name,
        ModelVersion.version == ctx.prediction.get("model"),
    ).first()
    if mv:
        mv.total_inferences = (mv.total_inferences or 0) + 1
        db.commit()

    # ---- 响应 ----
    return {
        "record_id": record.id,
        "scene": scene_name,
        "group_id": gid,
        "image": {
            "cropped": _b64(ctx.cropped_image),
            "boundary_overlay": _b64(ctx.overlays.get("boundary_overlay")),
            "probability_map": _b64(ctx.overlays.get("probability_map")),
            "colored_labels": _b64(ctx.overlays.get("colored_labels")),
        },
        "prediction": ctx.prediction,
        "features": ctx.features,
        "metrics": ctx.metrics,
        "alarms": ctx.alarms,
        "trace": ctx.trace,
        "created_at": record.created_at.isoformat(),
    }
