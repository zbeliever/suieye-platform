"""淬眸引擎抽象层 —— Filter 管线（Phase 2 核心）

设计原则（对应整体架构 v2.0 第三章"引擎层"）：
1. 引擎层是**通用底座**：Engine/EngineContext 不包含任何浮选专属逻辑，
   磨矿、安全、水质等未来场景包复用同一套抽象。
2. 场景差异全部下沉到 filters 实现与场景包 YAML 参数里。
3. 每个 Filter 只做一件事：读 ctx → 计算 → 写回 ctx，可独立替换
   （例：watershed 分割 Filter 换成深度学习分割 Filter，管线其余不动）。
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class EngineContext:
    """一次分析的全量上下文——Filter 链的统一数据契约。

    所有场景共用这一个上下文结构：图像类场景用 image 系列字段，
    未来时序/传感器场景可用 features/prediction 而不带图像。
    """

    # ---- 输入 ----
    raw_image: Optional[np.ndarray] = None       # 原始输入（BGR）
    scene: str = ""                              # 场景包名

    # ---- 中间产物（图像链路） ----
    cropped_image: Optional[np.ndarray] = None   # ROI 裁剪（显示+特征提取用）
    bbox: Optional[tuple] = None                 # ROI 在原图中的位置
    preprocessed: Optional[np.ndarray] = None    # 增强后图（算法用）
    labels: Optional[np.ndarray] = None          # 分割标签图
    fg_mask: Optional[np.ndarray] = None         # 前景掩码

    # ---- 产物 ----
    features: dict = field(default_factory=dict)   # 特征向量（维度由场景定）
    prediction: dict = field(default_factory=dict) # 预测结果（目标由场景定）
    alarms: list = field(default_factory=list)     # 触发的告警
    overlays: dict = field(default_factory=dict)   # 可视化图（分割叠加图等）
    metrics: dict = field(default_factory=dict)    # 泡沫数等附加指标
    trace: list = field(default_factory=list)      # 执行轨迹 [{filter, ms, params}]

    def add_trace(self, name: str, ms: float, params: dict):
        self.trace.append({"filter": name, "ms": round(ms, 1), "params": params})


class Engine:
    """Filter 基类：管线中的可替换单元。

    子类需实现 run(ctx, params) 并设置类属性 name（与场景包 YAML
    pipeline.filters[].name 对应）。
    """

    #: 注册名（YAML 中引用）
    name: str = "base"

    def run(self, ctx: EngineContext, params: dict) -> EngineContext:
        raise NotImplementedError

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.name!r}>"
