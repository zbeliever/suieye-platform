"""引擎注册表：Filter name -> Engine 实例。

场景包 YAML 的 pipeline.filters[].name 在这里解析。
新增场景引擎时 import 对应模块并自动注册（见 filters/__init__.py）。
"""

from typing import Dict

from backend.engines.base import Engine

_REGISTRY: Dict[str, Engine] = {}


def register(engine: Engine) -> Engine:
    """注册引擎实例（幂等：同名覆盖，便于热替换测试）。"""
    _REGISTRY[engine.name] = engine
    return engine


def get_engine(name: str) -> Engine:
    if name not in _REGISTRY:
        raise KeyError(
            f"未注册的引擎: {name!r}，已注册: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def list_engines():
    return sorted(_REGISTRY)


# 触发所有内置 Filter 的注册（import 即注册）
from backend.engines import filters as _filters  # noqa: E402,F401
