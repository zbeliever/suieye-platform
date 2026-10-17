"""内置 Filter 集合——import 即注册到 registry。

覆盖全场景：
- 浮选：roi_extract / enhance / segment / extract_features / predict
- 粒度：segment_grains / extract_grain_features / predict_p80
- 安全：detect_objects / extract_safety_features / assess_risk
- 深度学习分割：segment_unet (U-Net 泡沫分割)
- 未来：水质(颜色特征)
"""

# 导入所有 Filter 类（触发定义，不触发注册）
from backend.engines.filters import (  # noqa: F401
    roi_extract,
    enhance,
    segment,
    segment_felzenszwalb,
    segment_sam,
    segment_unet,
    refine_crf,
    extract_features,
    predict,
    # 粒度场景专用
    segment_grains,
    extract_grain_features,
    predict_p80,
    # 安全巡检专用
    detect_objects,
    extract_safety_features,
    assess_risk,
)

# 延迟导入 registry 并注册（避免循环导入）
from backend.engines.registry import register as _reg

# 浮选场景
_reg(roi_extract.RoiExtractEngine())
_reg(enhance.EnhanceEngine())
_reg(segment.SegmentEngine())
_reg(extract_features.ExtractFeaturesEngine())
_reg(predict.PredictEngine())

# 分割方法扩展
_reg(segment_felzenszwalb.FelzenszwalbEngine())
_reg(segment_sam.SAMSegmentEngine())
_reg(segment_unet.UNetSegmentEngine())

# CRF 边界精修（接在任意 segment 之后）
_reg(refine_crf.CRFRefineEngine())

# 粒度场景
_reg(segment_grains.SegmentGrainsEngine())
_reg(extract_grain_features.ExtractGrainFeaturesEngine())
_reg(predict_p80.PredictP80Engine())

# 安全巡检场景
_reg(detect_objects.DetectObjectsEngine())
_reg(extract_safety_features.ExtractSafetyFeaturesEngine())
_reg(assess_risk.AssessRiskEngine())
