from backend.algorithms.features import extract_features

from backend.algorithms.markers import analyze_markers, extract_foam_roi

from backend.algorithms.preprocess import preprocess

from backend.algorithms.watershed import build_gradient, watershed_segment

from backend.utils.image_utils import build_probability_map, colorize_labels, draw_boundary_overlay





def run_full_pipeline(img, with_prediction=False):

    # 显示用：保留原始上传图的裁剪区域（不做 MSR）

    cropped_display, bbox = extract_foam_roi(img)



    # 算法用：仅在 ROI 上做 MSR + 双边滤波，避免整图发白、模糊

    prep = preprocess(cropped_display)

    preprocessed = prep["preprocessed"]



    marker_data = analyze_markers(preprocessed)

    marker_data["cropped"] = preprocessed

    marker_data["cropped_display"] = cropped_display

    marker_data["bbox"] = bbox



    markers = marker_data["markers"]

    fg_mask = marker_data["foreground_mask"]



    grad_data = build_gradient(preprocessed, markers, fg_mask, marker_data["distance_raw"])

    ws_data = watershed_segment(

        preprocessed,

        markers,

        grad_data["gradient"],

        peak_mask=marker_data.get("peak_mask"),

        seed_radius=marker_data.get("seed_radius", 4),

    )

    labels = ws_data["labels"]



    result = {

        "preprocess": prep,

        "markers": marker_data,

        "gradient": grad_data,

        "watershed": ws_data,

        "cropped": cropped_display,

        "probability_map": build_probability_map(labels, fg_mask),

        "boundary_overlay": draw_boundary_overlay(cropped_display, labels, fg_mask),

        "overlay": draw_boundary_overlay(cropped_display, labels, fg_mask),

        "colored_labels": colorize_labels(labels),

        "n_foams": ws_data["n_regions"],

    }



    if with_prediction:

        features = extract_features(cropped_display, labels, fg_mask)

        features["n_foams"] = float(ws_data["n_regions"])

        result["features"] = features



    return result


