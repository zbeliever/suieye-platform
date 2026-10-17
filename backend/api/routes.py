"""API 路由 — 已拆分为模块化结构（向后兼容）

拆分后模块位于 backend/api/routers/ 包下：
  - scenes.py    健康检查 + 场景 CRUD + 统计 + 趋势 + 模型列表
  - alarms.py     告警列表/确认 + 报表聚合 + 图像服务
  - equipment.py  设备监测（模拟数据 + API）
  - dosing.py     智能配药助手全部代码
  - analysis.py   图像分析上传端点

原有导入路径 `from backend.api.routes import router` 保持不变。
"""

from backend.api.routers import router

__all__ = ["router"]
