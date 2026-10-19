"""SuiEye 淬眸 · 铅锌浮选全流程智能视觉分析平台 —— 配置"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # 应用
    # ★ 这里是 FastAPI 的 title，会显示在 /docs 与 /openapi.json 上，
    #   所以用**系统正式全称**，不要写成产品代号。
    APP_NAME: str = "SuiEye 淬眸 · 铅锌浮选全流程智能视觉分析平台"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    # 数据库（Phase 1.2 启用，先用 SQLite 过渡）
    DATABASE_URL: str = "sqlite:///./suieye.db"
    POSTGRES_URL: str = "postgresql://suieye:suieye@localhost:5432/suieye"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # LLM（Phase 5 启用）
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = ""
    LLM_MODEL: str = "qwen-plus"

    # 场景包目录
    SCENE_PACKAGES_DIR: str = "backend/scenes"

    # 数据目录（模型 / 上传图像存档）
    DATA_DIR: str = "data"

    # 旧项目算法模块路径（复用浮选算法）
    LEGACY_ALGORITHMS_DIR: str = "../铅锌浮选品位预测系统/backend/algorithms"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
