"""路由聚合包 — 将 routes.py 拆分为功能模块"""

from fastapi import APIRouter

from .scenes import router as scenes_router
from .alarms import router as alarms_router
from .equipment import router as equipment_router
from .dosing import router as dosing_router
from .analysis import router as analysis_router
from .safety import router as safety_router
from .reports import router as reports_router
from .ws import router as ws_router
from .water import router as water_router
from .tailings import router as tailings_router
from .settings import router as settings_router
from .courier import router as courier_router
from .webhooks import router as webhooks_router

router = APIRouter()
router.include_router(scenes_router)
router.include_router(alarms_router)
router.include_router(equipment_router)
router.include_router(dosing_router)
router.include_router(analysis_router)
router.include_router(safety_router)
router.include_router(reports_router)
router.include_router(water_router)
router.include_router(tailings_router)
router.include_router(settings_router)
router.include_router(courier_router)
router.include_router(webhooks_router)
router.include_router(ws_router)
