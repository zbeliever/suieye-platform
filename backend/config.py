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

    # ── 品位演示口径（2026-10-11 老板拍板：Pb 3~8%） ──────────────────
    # ★ 这里只定义「展示层取数」的口径，**不改库里的真值，也不改模型输出**。
    #   背景：/api/analyze 产生的 upload 记录是真模型输出，实测 Pb 1.69~1.84%，
    #   落在口径之外。门面若直接取最新一条，就会出现
    #   「均值 5.23%」与「最新 1.79%」并排 + 红字"低于均值 66%"的自相矛盾画面。
    #   口径放这里而不是前端，是为了让它成为唯一真源：
    #   前端只问「给我口径内最近一条」，由后端决定什么叫口径内。
    PB_GRADE_MIN: float = 3.0
    PB_GRADE_MAX: float = 8.0

    # ── 告警对外接入（2026-10-19 老板：告警要能接入其他软件）──────────
    # ★ 三个口子三把钥匙，各管各的：
    #   · ALARM_INGEST_KEY —— 外部系统往 SuiEye **推**告警时校验（请求头 X-API-Key）
    #   · WS_TOKEN         —— 外部系统**连** /ws/* 时校验（?token=），留空则不校验
    #   · Webhook 签名密钥不回落到这里：它每个订阅一把，存在
    #     webhook_subscriptions.secret，撤一个订阅不影响别的接收方
    # 演示版给了默认值，方便一条 curl 就跑通；上线前必须改掉。
    ALARM_INGEST_KEY: str = "suieye-ingest-demo-key"
    WS_TOKEN: str = ""
    WEBHOOK_TIMEOUT: float = 5.0
    WEBHOOK_KEEP_DELIVERIES: int = 200   # 投递流水只留最近 N 条，防止无限膨胀

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
