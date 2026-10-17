"""淬眸 SuiEye 数据库层

5 张核心表（架构 v2.0 第四章）：
- scene_packages   场景包注册
- analysis_records 统一分析记录（所有场景共用）
- model_versions   模型版本注册（含"员工档案"扩展字段）
- assay_ledger     化验台账（飞轮入口）
- alarm_records    告警记录

SQLite 起步（零部署成本），字段类型用通用 JSON —— 换 PostgreSQL 时
只需改 DATABASE_URL，JSON 自动映射为 JSONB。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.config import settings

Base = declarative_base()

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},  # SQLite 需要
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI 依赖注入用"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ScenePackage(Base):
    """场景包注册表"""
    __tablename__ = "scene_packages"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    description = Column(Text)
    config = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), default="active")  # active / paused / archived
    created_at = Column(DateTime, default=datetime.utcnow)


class AnalysisRecord(Base):
    """统一分析记录表（所有场景共用）"""
    __tablename__ = "analysis_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scene_id = Column(String(50), nullable=False, index=True)
    group_id = Column(String(50), index=True)  # 时间组（防泄漏划分用）
    image_path = Column(String(500))
    features = Column(JSON)                    # 特征向量（各场景不同维度）
    prediction = Column(JSON)                  # 预测结果 {pb_grade: ..., model: ...}
    model_version = Column(String(50))
    actual_value = Column(Float)               # 台账回填真值
    source = Column(String(20), default="upload")  # upload / video_stream / batch_import / api
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ModelVersion(Base):
    """模型版本注册表（含员工档案字段）"""
    __tablename__ = "model_versions"

    id = Column(Integer, primary_key=True)
    scene_id = Column(String(50), nullable=False, index=True)
    version = Column(String(50), nullable=False)
    algorithm = Column(String(50), nullable=False)  # XGBoost / RandomForest / Watershed-Heuristic
    file_path = Column(String(500))
    metrics = Column(JSON)                    # {mae: ..., r2: ..., test_mae: ...}
    training_data_count = Column(Integer)
    trained_at = Column(DateTime)
    is_active = Column(Boolean, default=False)
    # 模型员工档案扩展
    hire_date = Column(DateTime)              # 入职日期
    total_inferences = Column(Integer, default=0)
    last_health_check = Column(DateTime)
    health_status = Column(String(20), default="healthy")  # healthy / drifting / stale


class AssayLedger(Base):
    """化验台账统一表（飞轮入口）"""
    __tablename__ = "assay_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scene_id = Column(String(50), nullable=False, index=True)
    ledger_type = Column(String(50), nullable=False)  # xrf / sieve / water_quality / maintenance
    sample_time = Column(DateTime, nullable=False, index=True)
    value_json = Column(JSON, nullable=False)          # {pb: 1.85, zn: 0.52}
    source = Column(String(100))                        # 化验员 / 仪器 / excel_import
    matched_analysis_ids = Column(JSON)                 # 时间窗内自动匹配的记录 ID
    used_for_training = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class CourierAssay(Base):
    """Courier 在线分析仪历史品位（凡口铅锌矿 2019-08，真实工业数据）

    来源：2020csu浮选视频.zip 内 2191 个 'II 20min report_*.xls' 报表，
    由 scripts/import_courier_reports.py 一次性导入。
    ★ 与 AssayLedger（训练飞轮入口）**分开存**：这批是 2019 年历史数据，
    不参与现有飞轮语义（used_for_training / matched_analysis_ids），
    仅供历史查询、时段回放与真实品位展示。
    """
    __tablename__ = "courier_assays"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sample_time = Column(DateTime, nullable=False, index=True)      # 测量时刻
    sample_point = Column(String(30), nullable=False, index=True)   # 取样点（分析仪原列名，如 Kpb II）
    element = Column(String(6), nullable=False)                     # Fe / Zn / Pb
    value = Column(Float, nullable=False)                           # 品位 %
    __table_args__ = (
        UniqueConstraint("sample_time", "sample_point", "element", name="uq_courier_point"),
    )


class AlarmRecord(Base):
    """告警记录统一表"""
    __tablename__ = "alarm_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scene_id = Column(String(50), nullable=False, index=True)
    level = Column(String(10), nullable=False)  # warning / danger
    rule_name = Column(String(100))
    message = Column(Text)
    evidence = Column(JSON)                    # 截图路径 + 指标快照（黑匣子）
    is_acknowledged = Column(Boolean, default=False)
    acknowledged_by = Column(String(50))
    acknowledged_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # ---- 处置闭环（v2, 2026-10-13）----
    # new / acknowledged / in_progress / closed / false_positive
    status = Column(String(20), default="new", index=True)
    group_key = Column(String(80), index=True)   # 同源聚合键，如 "frame:3120"
    handled_by = Column(String(50))              # 处理人（岗位或姓名）
    handled_at = Column(DateTime)                # 最后一次处置时间
    handled_note = Column(Text)                  # 处理意见（流转时必填）
    action_taken = Column(Text)                  # 处置措施（选填）
    false_positive = Column(Boolean, default=False)
    silenced_until = Column(DateTime)            # 静默截止时间
    silence_reason = Column(String(200))
    suppressed_by = Column(Integer)              # 因果抑制的根因告警 id（P2 预留）


class AlarmAction(Base):
    """告警处置流水（审计日志）— 每次状态流转写一条

    action: acknowledge / start / close / false_positive / reopen / silence
    """
    __tablename__ = "alarm_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alarm_id = Column(Integer, nullable=False, index=True)
    action = Column(String(30), nullable=False)
    from_status = Column(String(20))
    to_status = Column(String(20))
    operator = Column(String(50))
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class DosingRecord(Base):
    """智能配药记录表"""
    __tablename__ = "dosing_records"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 输入条件
    ore_type = Column(String(50), nullable=False)          # 矿石类型: lead_zinc_mixed / high_lead / high_zinc / oxidized
    target_pb_grade = Column(Float, nullable=False)         # 目标铅品位 %
    target_zn_grade = Column(Float)                        # 目标锌品位 %（可选）
    current_ph = Column(Float, nullable=False)              # 当前矿浆 pH
    pulp_density = Column(Float)                            # 矿浆浓度 %
    froth_state = Column(String(30))                        # 泡沫状态: normal / fragile / sticky / overloaded

    # 输出推荐
    recommendation = Column(JSON, nullable=False)            # 完整推荐结果

    # 元数据
    applied = Column(Boolean, default=False)                # 是否已应用
    feedback_score = Column(Integer)                        # 操作员反馈 1-5
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class HazardTicket(Base):
    """安全巡检隐患工单表（发现 → 整改 → 复查销项闭环）"""
    __tablename__ = "hazard_tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    camera_id = Column(String(20), nullable=False, index=True)   # 巡检点位
    record_id = Column(Integer, index=True)                      # 来源分析记录
    level = Column(String(10), nullable=False)                   # danger / warning
    title = Column(String(200), nullable=False)                  # 隐患标题
    detail = Column(Text)                                        # 隐患详情
    evidence = Column(JSON)                                      # 图路径 + 指标快照
    status = Column(String(20), default="pending", index=True)   # pending / rectifying / closed
    assigned_to = Column(String(50))                             # 责任人
    action_taken = Column(Text)                                  # 整改措施
    resolved_at = Column(DateTime)                               # 销项时间
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WaterReading(Base):
    """脱水工段水质读数表（5 个点位共用一个表，station_id 区分）

    参数口径见 docs/water-monitoring-plan.md 3.2：
    pH / 浊度 / SS / COD / 总铅 / 总锌 / 总铜 / 流量
    + 派生：残留药剂估算（由 COD 反推）
    """
    __tablename__ = "water_readings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    station_id = Column(String(10), nullable=False, index=True)
    ph = Column(Float)                 # 无量纲
    turbidity = Column(Float)          # NTU
    ss = Column(Float)                 # 悬浮物 mg/L
    cod = Column(Float)                # 化学需氧量 mg/L
    pb = Column(Float)                 # 总铅 mg/L
    zn = Column(Float)                 # 总锌 mg/L
    cu = Column(Float)                 # 总铜 mg/L
    flow = Column(Float)               # 流量 m³/h
    residual_reagent = Column(Float)   # 残留药剂估算 mg/L（COD 派生）
    source = Column(String(20), default="demo")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class WaterEvent(Base):
    """水质异常事件表（跑浑 / 药剂累积 / 高碱冲击 / 铜离子富集）"""
    __tablename__ = "water_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    station_id = Column(String(10), nullable=False, index=True)
    event_type = Column(String(30), nullable=False)   # turbidity_surge / reagent_accumulation / alkali_shock / copper_enrichment
    level = Column(String(10), nullable=False)        # info / warning / danger
    title = Column(String(120), nullable=False)
    detail = Column(Text)
    metric = Column(String(20))                       # 主指标字段名
    peak_value = Column(Float)
    baseline_value = Column(Float)
    evidence = Column(JSON)                           # 事件快照
    acknowledged = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class WaterComplianceRecord(Base):
    """水质合规统计表（对标 GB 25466，按日 × 点位 × 参数）"""
    __tablename__ = "water_compliance_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False, index=True)   # YYYY-MM-DD
    station_id = Column(String(10), nullable=False, index=True)
    param = Column(String(20), nullable=False)
    avg_value = Column(Float)
    max_value = Column(Float)
    limit_value = Column(Float)        # 标准限值
    exceed_count = Column(Integer, default=0)
    exceed_hours = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class TailingsReading(Base):
    """尾矿浓密机运行时序表（尾矿处理场景）

    口径见 docs/tailings-processing-plan.md 3.1：
    给料（流量/浓度/粒度）、絮凝剂、泥床高度、耙架扭矩、底流（流量/浓度）、溢流流量

    ★ 溢流浊度**不在此表冗余存储** —— 它就是 water_readings 的 W3 点位，
    接口层实时取用，从物理上杜绝"跨模块数字打架"。

    ★ underflow_conc 是软测量目标。演示环境里它有"真值"（本表），
    前端展示的软测量值是模型仅用可测变量反推出来的，两者对比可给出 MAE。
    """
    __tablename__ = "tailings_readings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    equipment_id = Column(String(20), nullable=False, index=True)  # T1 = 尾矿浓密机
    # —— 给料侧 ——
    feed_flow = Column(Float)          # 给料矿浆流量 m³/h
    feed_solids = Column(Float)        # 给料固体质量分数 %
    feed_p80 = Column(Float)           # 给料 P80 粒度 μm（来自 grinding，可能缺失）
    # —— 药剂 ——
    flocculant_flow = Column(Float)    # 絮凝剂溶液流量 m³/h（0.1% 浓度）
    flocculant_dosage = Column(Float)  # 絮凝剂单耗 g/t 干尾
    # —— 设备 ——
    bed_height = Column(Float)         # 泥床（压缩层）高度 m
    rake_torque = Column(Float)        # 耙架扭矩 % 额定
    rake_current = Column(Float)       # 耙架驱动电流 A
    # —— 出料 ——
    underflow_flow = Column(Float)     # 底流流量 m³/h
    underflow_conc = Column(Float)     # 底流固体质量分数 %  ← 软测量目标
    overflow_flow = Column(Float)      # 溢流流量 m³/h
    # —— 自洽性证据 ——
    solids_residual = Column(Float)    # 固体物料平衡相对残差 %
    source = Column(String(20), default="demo")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class TailingsEvent(Base):
    """尾矿处理工况事件表

    event_type: flocculant_shortage / bed_rise / feed_surge / over_discharge / rake_overload
    """
    __tablename__ = "tailings_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    equipment_id = Column(String(20), nullable=False, index=True)
    event_type = Column(String(40), nullable=False)
    level = Column(String(10), nullable=False)        # info / warning / danger
    title = Column(String(120), nullable=False)
    detail = Column(Text)
    metric = Column(String(30))                       # 主指标字段名
    peak_value = Column(Float)
    baseline_value = Column(Float)
    evidence = Column(JSON)                           # 事件快照（含关联的水质事件）
    acknowledged = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class TailingsAdviceLog(Base):
    """setpoint 推荐流水（推荐 → 采纳 → 效果回填，构成闭环）"""
    __tablename__ = "tailings_advice_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    equipment_id = Column(String(20), nullable=False, index=True)
    advice_type = Column(String(30), nullable=False)   # underflow_discharge / flocculant_dosage
    current_value = Column(Float)
    recommended_value = Column(Float)
    unit = Column(String(20))
    reason = Column(Text)
    expected_effect = Column(Text)
    accepted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ============================================================
# 设备监测域（v2，2026-10-14）
#
# 背景：设备模块原先全部走进程内存（defaultdict + list），后端一重启
# 报修工单/维护记录/操作日志全部归零，且工单"只写不读"（前端从未查询
# /repairs），提交完在系统里无处可见 —— 演示时等同于假交互。
# 现在全部落库，工单走闭环状态机。
# ============================================================


class EquipmentAsset(Base):
    """设备台账（设备域唯一真源）

    身份字段从代码基线 seed 进来，之后**以库为准**（维护完成、工单闭环
    会更新 last_maintain 等）。仿真参数（base_metrics / noise）仍留在
    代码里，不属于台账事实，不进库。
    """
    __tablename__ = "equipment_assets"

    id = Column(String(30), primary_key=True)          # flotation_1 / ball_mill ...
    name = Column(String(50), nullable=False)
    type = Column(String(20), nullable=False)          # flotation/mill/pump/conveyor/thickener/agitator
    desc = Column(Text)
    model = Column(String(50))
    manufacturer = Column(String(50))
    install_date = Column(String(10))                  # YYYY-MM
    total_hours = Column(Float, default=0)             # 累计运行小时
    last_maintain = Column(String(10))                 # YYYY-MM-DD
    mtbf_hours = Column(Float)                         # 平均无故障时间（h）
    location_tag = Column(String(30))                  # FL-101 ...
    base_health = Column(Float)                        # 基准健康分
    control_state = Column(String(10), default="running")  # running/stopped（人工启停覆盖）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EquipmentMaintenanceRecord(Base):
    """设备维护记录（巡检 / 定期保养 / 故障维修）

    故障维修记录可由工单闭环自动回写（source_order_id 记录来源工单）。
    """
    __tablename__ = "equipment_maintenance_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    record_id = Column(String(32), index=True)         # M001（展示用编号）
    equipment_id = Column(String(30), nullable=False, index=True)
    date = Column(String(10), nullable=False)          # YYYY-MM-DD
    type = Column(String(20))                          # 巡检 / 定期保养 / 故障维修
    technician = Column(String(50))
    content = Column(Text)
    result = Column(String(20))                        # 正常 / 已修复
    source_order_id = Column(String(32), index=True)   # 来源工单（自动回写时填写）
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class EquipmentRepairOrder(Base):
    """报修工单（闭环状态机）

    状态流转：pending → assigned → repairing → fixed
                    └────────→ rejected（误报/无需维修）
              fixed / rejected --reopen--> pending
    """
    __tablename__ = "equipment_repair_orders"

    order_id = Column(String(32), primary_key=True)    # WO-20261014-8AA4
    equipment_id = Column(String(30), nullable=False, index=True)
    equipment_name = Column(String(50))
    category = Column(String(30))                      # mechanical/electrical/thermal/vibration/leak/other
    priority = Column(String(10), default="high")      # critical/high/medium
    description = Column(Text)
    reporter = Column(String(50))
    status = Column(String(20), default="pending", index=True)
    assignee = Column(String(50))                      # 维修负责人
    handled_by = Column(String(50))                    # 最后操作人
    handled_at = Column(DateTime)
    resolve_note = Column(Text)                        # 维修/驳回说明
    action_taken = Column(Text)                        # 处置措施
    downtime_minutes = Column(Integer)                 # 停机时长（可选）
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = Column(DateTime)


class EquipmentRepairAction(Base):
    """工单流转流水（审计留痕，一次操作一条）"""
    __tablename__ = "equipment_repair_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(32), nullable=False, index=True)
    action = Column(String(20), nullable=False)        # create/assign/start/fix/reject/reopen/comment
    from_status = Column(String(20))
    to_status = Column(String(20))
    operator = Column(String(50))
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class EquipmentOperationLog(Base):
    """设备操作日志（启停 / 报修 / 维护 / 工单流转，全量落库）"""
    __tablename__ = "equipment_operation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    equipment_id = Column(String(30), index=True)      # None = 全局
    action = Column(String(200), nullable=False)
    operator = Column(String(50))
    type = Column(String(20), default="info")          # info/success/warning/danger
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


def init_db():
    """建表（幂等）"""
    Base.metadata.create_all(engine)
    return engine
