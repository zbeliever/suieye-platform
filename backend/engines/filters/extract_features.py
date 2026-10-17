"""特征提取 Filter：21 维泡沫视觉特征（形态/纹理/颜色）。"""

from backend.algorithms.features import extract_features
from backend.engines.base import Engine, EngineContext


class ExtractFeaturesEngine(Engine):
    name = "extract_features"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        feats = extract_features(ctx.cropped_image, ctx.labels, ctx.fg_mask)
        # 泡沫计数并入特征（预测与展示共用）
        if "n_foams" in ctx.metrics:
            feats["n_foams"] = float(ctx.metrics["n_foams"])
        ctx.features = {k: round(float(v), 6) for k, v in feats.items()}
        return ctx
