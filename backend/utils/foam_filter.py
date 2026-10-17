"""Heuristics to skip non-foam uploads (screenshots, test artifacts)."""

SKIP_NAME_PARTS = (
    "user_screen",
    "screenshot",
    "ref_screenshot",
    "ref_",
    "test_prob",
    "test_boundary",
    "test_foam",
    "screen.png",
    "screen.jpg",
    "cache_proc_",
    "cache_prob_",
    "cache_bound_",
    "cache_meta_",
    "result_",
)

def is_foam_filename(filename: str) -> bool:
    lower = filename.lower()
    return not any(part in lower for part in SKIP_NAME_PARTS)


def filter_foam_filenames(filenames, include_all=False):
    if include_all:
        return list(filenames)
    return [f for f in filenames if is_foam_filename(f)]


def foam_image_warning(filename: str, n_foams: int | None = None, n_markers: int = 0, width: int = 0, height: int = 0) -> str | None:
    lower = filename.lower()
    if any(part in lower for part in SKIP_NAME_PARTS):
        return "当前文件是界面截图或测试图，不是泡沫原图。请上传浮选槽相机拍摄的泡沫照片（.jpg）。"

    if width and height:
        ratio = width / max(height, 1)
        if ratio > 2.0:
            return (
                f"图片过宽（{width}×{height}），像横条缩略图而非完整泡沫图。"
                "您的原图应为竖版泡沫照片（高>宽），请重新上传。"
            )
        if height < 200 or width < 200:
            return f"图片尺寸过小（{width}×{height}），请上传完整原图，不要上传缩略图或裁剪条。"

    if n_foams is not None:
        if n_foams > 120:
            return (
                f"检测到 {n_foams} 个区域，数量异常偏多，可能不是标准泡沫图。"
                "请确认上传的是泡沫图像，而非界面截图。"
            )
        if n_foams == 0:
            return "未检测到有效泡沫区域，请换一张清晰的泡沫原图。"
        if n_markers > 0 and n_foams > n_markers * 1.5:
            return "分割区域数明显偏多，结果可能不可靠，建议使用标准泡沫原图。"
    return None
