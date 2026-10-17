"""场景包运行时：YAML 配置 → Filter 管线 → 分析结果。

职责：
1. 加载/缓存场景包 YAML（scenes/{name}/{name}.yaml）
2. 按 pipeline.filters 顺序组装引擎链
3. run_analysis()：一次调用跑完 分割→特征→预测→告警

纯计算层：不碰 HTTP / 不碰数据库（入库由 API 层负责）。
"""

import time
from pathlib import Path

import yaml

from backend.engines.alarm import evaluate_alarm_rules
from backend.engines.base import EngineContext
from backend.engines.registry import get_engine

SCENES_DIR = Path(__file__).parent

# 已加载场景配置缓存 {name: config_dict}
_scene_cache: dict = {}


class SceneNotFoundError(Exception):
    pass


def load_scene_config(scene_name: str) -> dict:
    """加载场景包配置（带缓存）。"""
    if scene_name in _scene_cache:
        return _scene_cache[scene_name]

    yaml_path = SCENES_DIR / scene_name / f"{scene_name}.yaml"
    if not yaml_path.exists():
        raise SceneNotFoundError(f"场景包不存在: {yaml_path}")

    with open(yaml_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # 基础校验
    filters = (cfg.get("pipeline") or {}).get("filters") or []
    if not filters:
        raise ValueError(f"场景包 {scene_name} 的 pipeline.filters 为空")

    # 提前解析引擎名，注册错误在加载期暴露而不是分析期
    for f in filters:
        get_engine(f["name"])

    _scene_cache[scene_name] = cfg
    return cfg


def list_available_scenes() -> list:
    """扫描 scenes/ 目录下所有带 YAML 的场景包。"""
    out = []
    for d in sorted(SCENES_DIR.iterdir()):
        if d.is_dir() and (d / f"{d.name}.yaml").exists():
            out.append(d.name)
    return out


def reload_scene_config(scene_name: str) -> dict:
    """丢弃缓存并重新加载场景包。

    ★ 必须由**所有修改了 YAML 的路径**调用（如设置页改告警阈值）——
      load_scene_config() 带模块级缓存，不清的话 run_analysis() 会继续用旧配置，
      表现就是"设置页改完阈值，分析却毫无变化"。
    """
    _scene_cache.pop(scene_name, None)
    return load_scene_config(scene_name)


def run_analysis(scene_name: str, image_bgr, source: str = "upload", **overrides) -> EngineContext:
    """跑一次完整分析：图像 → Filter 链 → 告警评估。

    Args:
        scene_name: 场景包名（如 "flotation"）
        image_bgr: BGR 图像 (numpy array)
        source: 数据来源标记
        **overrides: 可选参数覆盖，如 segment_method="sam" 会合并到对应 filter 的 params

    Returns: EngineContext（features / prediction / alarms / overlays / trace）
    """
    cfg = load_scene_config(scene_name)
    ctx = EngineContext(raw_image=image_bgr, scene=scene_name)

    t_total = time.perf_counter()
    for f in cfg["pipeline"]["filters"]:
        engine = get_engine(f["name"])
        params = {**(f.get("params") or {})}
        # Apply per-filter overrides (e.g. {"segment": {"method": "sam"}})
        if f["name"] in overrides and isinstance(overrides[f["name"]], dict):
            params.update(overrides[f["name"]])
        t0 = time.perf_counter()
        engine.run(ctx, params)
        ctx.add_trace(f["name"], (time.perf_counter() - t0) * 1000, params)

    # 告警评估（YAML alarm_rules 段）
    ctx.alarms = evaluate_alarm_rules(cfg.get("alarm_rules"), ctx)

    ctx.metrics["pipeline_ms"] = round((time.perf_counter() - t_total) * 1000, 1)
    return ctx
