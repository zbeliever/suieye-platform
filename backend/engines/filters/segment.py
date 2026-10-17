"""分割 Filter：支持多种分割方法。

method 参数：
  - "watershed"（原默认）：经典分水岭，~3s
  - "felzenszwalb"（推荐）：图最小生成树，~250ms，边界最佳
  - "sam"：Segment Anything Model，不适用于泡沫纹理图
  - "unet"：U-Net 深度学习，~200ms(CPU)/~50ms(GPU)，精度最高(需训练)
"""

from backend.algorithms.markers import analyze_markers
from backend.algorithms.watershed import build_gradient, watershed_segment
from backend.engines.base import Engine, EngineContext
from backend.utils.image_utils import (
    build_probability_map,
    colorize_labels,
    draw_boundary_overlay,
)


class SegmentEngine(Engine):
    name = "segment"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        method = params.get("method", "watershed")
        if method == "sam":
            from backend.engines.filters.segment_sam import SAMSegmentEngine
            return SAMSegmentEngine().run(ctx, params)
        if method == "felzenszwalb":
            from backend.engines.filters.segment_felzenszwalb import FelzenszwalbEngine
            return FelzenszwalbEngine().run(ctx, params)
        if method == "unet":
            from backend.engines.filters.segment_unet import UNetSegmentEngine
            return UNetSegmentEngine().run(ctx, params)
        if method != "watershed":
            raise NotImplementedError(f"分割方法 {method!r} 尚未接入")

        # 1) 标记分析（前景掩码 / 峰值 / 种子）
        marker_data = analyze_markers(ctx.preprocessed)
        markers = marker_data["markers"]
        fg_mask = marker_data["foreground_mask"]

        # 2) 梯度构建
        grad_data = build_gradient(
            ctx.preprocessed, markers, fg_mask, marker_data["distance_raw"]
        )

        # 3) 分水岭分割
        ws_data = watershed_segment(
            ctx.preprocessed,
            markers,
            grad_data["gradient"],
            peak_mask=marker_data.get("peak_mask"),
            seed_radius=marker_data.get("seed_radius", 4),
        )
        labels = ws_data["labels"]

        # 4) 写回上下文
        ctx.labels = labels
        ctx.fg_mask = fg_mask
        ctx.metrics["n_foams"] = int(ws_data["n_regions"])
        ctx.metrics["foreground_ratio"] = round(
            float((fg_mask > 0).mean()) if fg_mask is not None else 0.0, 4
        )

        # 5) 可视化（叠加边界图 / 概率热图 / 彩色标签图）
        ctx.overlays["boundary_overlay"] = draw_boundary_overlay(
            ctx.cropped_image, labels, fg_mask
        )
        ctx.overlays["probability_map"] = build_probability_map(labels, fg_mask)
        ctx.overlays["colored_labels"] = colorize_labels(labels)
        return ctx
