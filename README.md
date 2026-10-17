# SuiEye 淬眸

**铅锌矿浮选过程智能视觉分析平台** — 以泡沫图像、颗粒图像与巡检画面为输入，覆盖品位预测、粒度分析、安全巡检、尾矿、水系统与设备健康六条业务线，并接入凡口铅锌矿的真实生产化验数据。

> 面向演示与汇报场景构建。前端 13 个业务页面全部可交互，数据链路真实：模型推理写入 `analysis_records`，告警写入 `alarm_records`，化验数据来自真实台账。

---

## 目录

- [核心能力](#核心能力)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [页面导览](#页面导览)
- [六大业务场景](#六大业务场景)
- [目录结构](#目录结构)
- [数据说明](#数据说明)
- [API 概览](#api-概览)
- [开发约定](#开发约定)
- [常见问题](#常见问题)

---

## 核心能力

| 能力 | 实现 |
|---|---|
| 浮选泡沫品位预测 | 泡沫图像 → ROI 提取 → MSR 增强 → 分水岭分割 → 11 维形态特征 → XGBoost 回归，预测 Pb / Zn 品位 |
| 磨矿粒度分析 | 颗粒图像 → P80 粒径统计 → 正常 / 过磨 / 欠磨 工况判定 |
| 皮带安全巡检 | YOLOv8n 人员检测 → 违规风险评分 → 隐患工单闭环（4 路机位） |
| 尾矿与水系统 | 浓密机底流软测量、回水可回用指数评分与合规记录 |
| 设备健康管理 | 健康分计算、维修工单流转、操作日志全量落库 |
| 工况时光机 | 按时间轴回放历史分析记录，浮选 / 磨矿场景自适应 |
| 实时监控大屏 | 凡口 2019 真实录像逐帧驱动，数据时钟 30× 快进，曲线与工艺链实时联动 |
| 3D 设备监测 | Three.js 场景，7 类设备模型 + 工业细节（法兰、护栏、爬梯、铭牌） |

---

## 技术栈

| 层 | 技术 | 端口 |
|---|---|---|
| 前端 | Vue 3 · Vite · Element Plus · ECharts · Three.js · Pinia | `5188` |
| 后端 | FastAPI · Uvicorn · SQLAlchemy 2 | `8300` |
| 算法 | PyTorch（CPU）· OpenCV · scikit-image · XGBoost | — |
| 数据 | SQLite（项目根 `suieye.db`）· WebSocket 推送 | — |

---

## 快速开始

### 前置要求

- Python **3.10+**（开发环境使用 3.13）
- Node.js **18+**（开发环境使用 22.x）
- 无需 GPU，全部推理在 CPU 上完成

### 1. 安装依赖

```bash
# 后端
pip install -r requirements.txt

# PyTorch 需从官方 CPU 源单独安装
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 安全巡检检测器（可选，--no-deps 避免覆盖 headless 版 OpenCV）
pip install ultralytics --no-deps
pip install requests httpx psutil ultralytics-thop

# 前端
cd frontend && npm install
```

> **注意**：本项目使用 `opencv-python-headless`，**不要**安装 `opencv-python`，两者会争抢 `cv2.pyd` 导致导入冲突。

### 2. 准备素材与数据

仓库不包含数据集、模型权重与运行时数据库，需在本地补齐（见 [数据说明](#数据说明)）：

```bash
python scripts/register_platform_scenes.py   # 注册六个场景包（幂等）
python scripts/seed_demo_data.py             # 灌入浮选演示记录
python scripts/import_courier_reports.py     # 导入凡口化验台账（需先备好源压缩包）
```

数据库表由 SQLAlchemy `init_db()` 自动创建（`backend/models/database.py`），**无需手动建表**。

### 3. 启动

**方式一：一键启动（Windows）**

双击项目根目录的 `start_dev.bat`，它会先清理占用 `5188` / `8300` 的残留进程，再分别开两个窗口启动前后端。

**方式二：手动启动**

```bash
# 终端 1 — 后端（必须从项目根目录启动）
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8300

# 终端 2 — 前端（必须在 frontend/ 目录下）
cd frontend
npm run dev
```

打开 <http://127.0.0.1:5188>。

> **改 Python 代码必须重启后端**（未启用热重载）；改前端由 Vite 热更新。

---

## 页面导览

| 路由 | 页面 | 说明 |
|---|---|---|
| `/` | 直播墙 | 首页。KPI、需关注告警、品位趋势、工艺节点流光、浮选泡沫实时画面 |
| `/monitor` | 实时监控 | 单点位大屏：凡口录像逐帧驱动 + 数据时钟联动 + 工艺链参照带 |
| `/dashboard` | 仪表盘 | 全场景总览与关键指标 |
| `/workbench/:sceneId` | 分析工作台 | 三场景分析入口，浮选结果可一键跳转配药 |
| `/timemachine` | 工况时光机 | 历史记录按时间轴回放，浮选 / 磨矿自适应 |
| `/safety` | 安全中心 | 4 路机位巡检画面、风险评分、隐患工单 |
| `/water` | 水质监测 | 水质指标与回水可回用评分 |
| `/tailings` | 尾矿监测 | 浓密机运行状态与底流软测量 |
| `/equipment` | 设备 3D | Three.js 设备模型、健康分、维修工单 |
| `/dosing` | 配药助手 | 依据品位偏差给出药剂配比建议 |
| `/alarms` | 告警中心 | 告警分级、确认与处置闭环 |
| `/reports` | 报表 | 4 项 KPI、品位趋势、分布直方图、告警趋势 |
| `/settings` | 设置 | 场景包、模型版本与阈值配置 |

---

## 六大业务场景

场景由 YAML 声明的 Filter 管道驱动，唯一真源为 `scene_packages` 表，经 `GET /api/scenes` 对外暴露。前端不做任何场景硬编码。

| 场景 | 模型版本 | 输入 → 输出 | 记录表 |
|---|---|---|---|
| `flotation` | `xgb-v1` | 泡沫图像 → Pb / Zn 品位 | `analysis_records` |
| `grinding` | `p80-v1` | 颗粒图像 → P80 粒度工况 | `analysis_records` |
| `safety` | `yolov8n-safety-v1` | 皮带画面 → 风险评分 | `analysis_records` |
| `tailings` | `underflow-soft-v1` | 浓密机参数 → 底流浓度 | `tailings_readings` |
| `water` | `reuse-index-v1` | 水质指标 → 回用指数 | `water_readings` |
| `equipment` | `health-score-v1` | 运行参数 → 健康分 | 不落库（实时计算） |

> `equipment` 场景为纯实时计算，`records` 返回 `null`，前端渲染为「—」而非 `0`。

### 可用的分割实现

`backend/engines/filters/` 提供了多种分割 Filter，可在场景 YAML 中通过 `method` 参数切换：

| Filter | 说明 |
|---|---|
| `segment` | 经典分水岭分割（浮选默认） |
| `segment_felzenszwalb` | Felzenszwalb 超像素分割 |
| `segment_sam` | SAM 分割 |
| `segment_unet` | U-Net 泡沫分割（需自备权重） |
| `segment_grains` | 颗粒分割（磨矿场景） |
| `refine_crf` | DenseCRF 边界精修（接在任意 segment 之后） |

> **CRF 当前默认关闭**。浮选 YAML 中 `refine_crf` 已注释停用 —— 在现有合成数据基线上实测会降低分割效果。改用 U-Net 方案前需先用真实图像验证。

---

## 目录结构

```
suieye-platform/
├── backend/
│   ├── main.py                 # FastAPI 入口（务必用 backend.main:app 启动）
│   ├── config.py
│   ├── api/
│   │   ├── routes.py           # 路由聚合
│   │   └── routers/            # 13 个业务路由
│   │       ├── analysis.py     alarms.py      courier.py
│   │       ├── dosing.py       equipment.py   reports.py
│   │       ├── safety.py       scenes.py      settings.py
│   │       └── tailings.py     water.py       ws.py
│   ├── engines/                # 场景引擎与 Filter 管道
│   │   ├── registry.py         alarm.py       base.py
│   │   └── filters/            # 15 个 Filter 实现
│   │       └── __init__.py     # ★ 唯一允许注册 Filter 的位置
│   ├── scenes/                 # 场景实现与 YAML 配置
│   │   ├── flotation/          grinding/       safety/
│   │   └── runtime.py
│   ├── models/                 # SQLAlchemy 模型、U-Net 定义、权重目录
│   ├── algorithms/             # 分割、特征、粒度、DenseCRF、根因分析
│   └── utils/
├── frontend/
│   ├── src/
│   │   ├── views/              # 13 个页面组件
│   │   ├── components/         # BrandMark、FrothLive 等
│   │   ├── api/                # axios 封装与 WebSocket
│   │   ├── router/  styles/  utils/
│   │   └── App.vue  main.js
│   └── public/media/           # 浮选泡沫循环视频与封面
├── scripts/                    # 数据导入、迁移、训练、测试脚本
├── docs/                       # 设计与实施方案文档
├── data/                       # 模型文件与巡检底图（数据集不入库）
├── 样例图像/                   # 演示用样例图
├── requirements.txt
└── start_dev.bat               # Windows 一键启动
```

---

## 数据说明

仓库**有意排除**以下内容，克隆后需按需在本地补齐：

| 路径 | 内容 | 说明 |
|---|---|---|
| `data/_datasets/` | 凡口源数据集（约 5 GB） | 含 30 段录像与 2191 份化验报告 |
| `outputs/` | QA 截图、日志、训练产物 | 可再生 |
| `*.pt` / `*.pth` | YOLO 与 U-Net 权重 | YOLO 可用 `scripts/download_yolo_model.py` 重新下载；U-Net 需重新训练 |
| `suieye.db` | 运行时数据库 | 由 `init_db()` 重建后重新灌数据 |
| `frontend/public/media/froth2019/` | 22 段凡口长录像 | `/monitor` 页依赖；缺失时该页视频不可用 |
| `data/safety_assets/sh17_workers/` | 安全巡检人员图库 | 安全场景演示素材 |

**已入库的素材**：`froth-loop.mp4`（4.3 MB，直播墙与监控页的循环视频）、其封面 `froth-poster.jpg`、`data/model_pb_xgb_v1.json`（品位模型）、`belt_cam01~04` 四路巡检底图、`样例图像/`。

### 化验数据

`courier_assays` 表存放凡口铅锌矿真实化验台账：

- 62,798 条记录，覆盖 2019-07-31 ~ 2019-08-30
- 9 个取样点 × Fe / Zn / Pb 三元素，每取样点约 17 分钟一测
- 由 `scripts/import_courier_reports.py` 从原始报表流式导入，`on_conflict_do_nothing` 保证幂等

---

## API 概览

后端共 **70 个接口**，分布在 13 个路由模块中（见 `backend/api/routers/`）。核心接口：

| 接口 | 说明 |
|---|---|
| `POST /api/analyze/{scene}` | 上传图像执行场景分析 |
| `GET /api/scenes` | 场景包列表与统计 |
| `GET /api/alarms` | 告警列表（支持分级 / 状态筛选） |
| `GET /api/courier/overview` | 化验台账总览 |
| `GET /api/courier/day?date=` | 某日全天化验数据 |
| `GET /api/courier/series?point=&element=` | 单测点单元素时间序列 |
| `GET /api/courier/bands` | 9 测点 × 元素的全月分位数（P01/P10/P50/P90/P99） |
| `GET /api/tailings/*` | 浓密机读数、事件与建议 |
| `GET /api/water/*` | 水质读数、事件与合规记录 |
| `GET /api/equipment/*` | 设备健康分、工单、操作日志 |
| `GET /api/reports/*` | 报表聚合数据 |
| `GET /api/dosing/*` | 药剂配比建议 |
| `GET /api/image` | 分析记录图片服务 |

**WebSocket 实时通道**（注意：无 `/api` 前缀）

| 通道 | 说明 |
|---|---|
| `WS /ws/alarms` | 新告警推送 |
| `WS /ws/safety` | 安全巡检状态 |
| `WS /ws/water` | 水质监测数据 |
| `WS /ws/equipment` | 设备运行指标 |

交互式文档：启动后端后访问 <http://127.0.0.1:8300/docs>。

---

## 开发约定

### 三条铁律

1. **入口必须是 `backend.main:app`**，且从项目根目录启动 —— 在 `backend/` 内启动会报 `ModuleNotFoundError: No module named 'backend'`。
2. **axios 拦截器已解包 `res.data`** —— 调用方拿到的就是业务数据，不要再取一层 `.data`。同时 `baseURL` 已是 `/api`，请求路径写 `/courier/...` 而非 `/api/courier/...`。
3. **Filter 注册只能写在 `backend/engines/filters/__init__.py`** —— 在单个 filter 文件内注册会触发循环导入。

### 数据口径

| 项 | 值 |
|---|---|
| 品位字段 | `pb_grade_xrf` / `zn_grade_xrf`（**不是** `pb_grade` / `zn_grade`） |
| Pb 品位范围 | 3% ~ 8%，均值约 5.24% |
| 告警阈值 | warning < 4%，danger < 2.5% |
| 磨矿 P80 | 正常 ~130 μm / 过磨 ~58 μm / 欠磨 ~245 μm |

### 路由书写

路由一律 `APIRouter()` 不设 `prefix`，**完整路径写在装饰器里**。

---

## 常见问题

**页面能打开但数据全是「—」**
多半是品位字段名写成了 `pb_grade` / `zn_grade`，正确是 `pb_grade_xrf` / `zn_grade_xrf`。

**后端返回 422 但函数体内没报错**
FastAPI 的参数校验发生在函数体之前。`level=''` 这类空字符串查询参数会在签名层就撞上 regex / bool 校验，必须在签名层放宽再手动判断。

**前端地址变了，页面却没更新**
Vite 在 `5188` 被占用时会静默改用 `5189` / `5190`。检查终端里的 `Port 5188 is in use` 提示，或直接看浏览器地址栏端口。

**本地 curl 一律 502**
本机存在系统代理，测本地服务需加 `--noproxy "*"`。

**Vite 起来后 `127.0.0.1:5188` 拒绝连接**
Vite 默认绑 `::1`。`vite.config.js` 已显式设置 `host: '127.0.0.1'`，不要删除；同时 proxy target 必须写 `http://127.0.0.1:8300` 而非 `localhost`，否则会解析成 IPv6 导致 `ECONNREFUSED`。

**Windows 中文路径下 `cv2.imwrite` 静默失败**
改用 `cv2.imencode()` + `Path.write_bytes()`。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | 项目交接说明：完整状态、铁律、历史变更与待办 |
| [`docs/数据资源分析报告.md`](docs/数据资源分析报告.md) | 数据资产盘点与可用性分析 |
| [`docs/数据资产落地实施方案.md`](docs/数据资产落地实施方案.md) | 数据落地路径与实施步骤 |
| [`docs/alarm-center-plan.md`](docs/alarm-center-plan.md) | 告警中心设计方案 |
| [`docs/tailings-processing-plan.md`](docs/tailings-processing-plan.md) | 尾矿处理模块方案 |
| [`docs/water-monitoring-plan.md`](docs/water-monitoring-plan.md) | 水质监测模块方案 |
| [`docs/froth-video-plan.md`](docs/froth-video-plan.md) | 浮选泡沫视频素材建设方案 |

---

## 许可与数据声明

本仓库为**私有**项目，未经授权不得分发。

其中凡口铅锌矿的化验台账、现场录像与工艺资料属于企业生产数据，仅限本项目内部研究使用，不得对外公开或用于商业用途。
