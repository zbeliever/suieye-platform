"""淬眸 SuiEye 平台入口"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.api.routes import router

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="自进化工业视觉智能平台 - API",
)

# CORS 配置：开发环境允许本地访问，生产环境应改为前端实际域名
_ALLOWED_ORIGINS = [
    "http://localhost:5188",
    "http://127.0.0.1:5188",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
async def startup():
    print(f"[SuiEye] 启动 - 版本 {settings.APP_VERSION}")
    print(f"[SuiEye] 场景包目录: {settings.SCENE_PACKAGES_DIR}")

    # 预热：加载 XGBoost 模型 + 校验场景包，避免首帧分析承担 13s 懒加载
    from backend.algorithms.predictor import _load_xgb_model
    from backend.scenes.runtime import list_available_scenes, load_scene_config

    _load_xgb_model()
    for name in list_available_scenes():
        load_scene_config(name)
        print(f"[SuiEye] 场景包就绪: {name}")

    # 启动 WebSocket 实时推送后台任务
    from backend.api.routers.ws import start_ws_background_tasks
    start_ws_background_tasks()


@app.get("/")
def root():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "api": "/api",
    }
