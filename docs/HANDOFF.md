# SuiEye 淬眸平台 — 项目交接文档

> **适用场景**：在另一个 WorkBuddy 账号（新 Buddy）上继续开发此项目
> **项目路径**：`D:/铅锌浮选品位预测系统/suieye-platform/`
> **最后更新**：2026-10-17（★ 新增第十二节「10-12~10-17 重大进展」，**必读**）
> **当前阶段**：演示/汇报版本打磨（`/monitor` 实时监控页为当前主战场）

---

## 〇、给新 Buddy 的第一句话

这是一个**铅锌矿浮选 AI 视觉分析平台**的演示/汇报项目。当前重点不是训练新模型，而是**把已有的 13 个前端页面 + 3D 设备监控 + 凡口真实生产数据打磨到"可演示、可汇报、数据可信"**。

**接手顺序（务必按此走）**：
1. 读本文第十二节（10-12~10-17 最新进展，最重要的上下文）
2. 读工作记忆：`C:/Users/believer/WorkBuddy/2026-10-11-16-27-15/.workbuddy/memory/MEMORY.md`（长期备忘）+ 最近两天日志 `2026-10-1*.md`
3. 启动前后端确认系统能跑（见第五节）
4. 向老板汇报"你了解到的当前状态 + 打算做的下一步"，确认后再动手

**当前主战场**：`/monitor` 实时监控页（2026-10-16 当晚多轮迭代定稿）
**口头称呼**：叫「老板」；沟通用中文；总结表格化 + P0/P1/P2 分层。

---

## 一、项目是什么

**SuiEye（淬眸）** — 铅锌矿浮选过程智能视觉分析平台。

**六个业务场景**（Scene，YAML 配置的 Filter 管道；唯一真源 = `scene_packages` 表，经 `/api/scenes` 暴露）：

| 场景 | 模型 | 说明 |
|------|------|------|
| flotation | xgb-v1 | 泡沫图像 → 品位预测（Pb/Zn） |
| grinding | p80-v1 | 颗粒图像 → 粒度分析（P80 工况判定） |
| safety | yolov8n-safety-v1 | 皮带/人员图像 → 风险评分 + 巡检闭环 |
| tailings | underflow-soft-v1 | 尾矿浓密机 + 底流软测量 |
| water | reuse-index-v1 | 水质监测 + 回水可回用评分 |
| equipment | health-score-v1 | 设备健康分 + 工单闭环（`SCENE_NO_STORE`，无记录数） |

**技术栈**：
| 层 | 技术 | 端口 |
|----|------|------|
| 前端 | Vue3 + Vite + Element Plus + ECharts + Three.js | :5188 |
| 后端 | FastAPI（Python 3.13 venv） | :8300 |
| 算法 | torch 2.14.1+cpu、opencv 5.0、xgboost、skimage | — |
| 数据 | SQLite（项目根目录 `suieye.db`） | — |

---

## 二、⚠️ 三条项目铁律（违反必出问题）

1. **FastAPI 入口是 `backend.main:app`**，不是 `routes:app`。启动命令：
   ```bash
   cd D:/铅锌浮选品位预测系统/suieye-platform
   C:/Users/believer/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8300
   ```
2. **axios 拦截器已自动解包 `res.data`**，前端调用接口时**不要再取一层 `.data`**（`res` 直接就是业务数据）。
3. **Filter 注册只能写在 `backend/engines/filters/__init__.py`**，严禁在单个 filter 文件里写 `register_engine()`（会循环导入）。

---

## 三、业务关键数据口径（演示数据真实性的根基）

| 项 | 值 | 说明 |
|----|-----|------|
| **品位字段名** | `pb_grade_xrf` / `zn_grade_xrf` | ⚠️ 不是 `pb_grade` / `zn_grade`（曾因字段名不统一导致全站数据显示 "—"） |
| 实际 Pb 品位范围 | 3% ~ 8% | 均值约 5.24% |
| 实际 Zn 品位范围 | 2% ~ 6% | — |
| 告警阈值 | warning < 4%，danger < 2.5% | Pb 品位 |
| 磨矿 P80 工况 | 正常 ~130μm / 过磨 ~58μm / 欠磨 ~245μm | — |

**重要教训**：老板对"数据假"非常敏感。任何改字段、改数据、改算法的地方，都要先确认口径一致。

---

## 四、当前完成度（2026-10-11）

### 前端（10 个页面，全部可用）
```
Dashboard.vue        — 总览
Workbench.vue        — 主工作台（三场景分析入口，浮选结果可一键跳转配药）
LiveWall.vue         — 实时流程墙（KPI 滚动、报警轮播、节点流光、30s 自动刷新）
TimeMachine.vue      — 历史回溯（已做磨矿/浮选场景自适应）
Reports.vue          — 报表（4 KPI + 品位趋势 + 直方图 + 报警趋势）
Alarms.vue           — 告警中心
DosingAssistant.vue  — 配药助手（读 URL 参数自动填充）
Equipment3D.vue      — 3D 设备监控（Three.js，7 个设备模型）
ModelProfile.vue     — 模型档案
Settings.vue         — 设置
```

### 磨矿粒度图像（照片级真实感）
- 生成器：`scripts/gen_grinding_images.py`（v2，Perlin 噪声形状 + 3D Phong-Blinn 光照 + 真实矿物光学属性）
- 输出：`data/uploads/grinding/grinding_000~024_<工况>.jpg`（25 张，normal/overground/underground 三种工况）
- 对比图：`data/uploads/grinding/_comparison.png`
- ⚠️ 已固定 `random.seed(BASE_SEED)`，重新生成后文件名与工况稳定，否则 DB 关联会错位

### 算法状态
- **分割**：已回退到参考项目版本（`markers.py` 用 65th 百分位 + `gray>28` 固定阈值；`watershed.py` 去掉小区域合并）——参考项目压缩包在微信文件 `铅锌浮选品位预测系统.zip`
- **CRF**：已砍（真实场景表现不佳）。**硬规则：所有算法更新上线前必须经过真实图像验证**

### 数据填充状态（source 字段标记）
| 场景 | demo 记录数 | 说明 |
|------|-----------|------|
| flotation | ~3029 | 品位已重生成到真实范围 |
| grinding | 18 demo + 8 upload | demo 已关联生成图片 |
| safety | 26 | features 20 字段齐全 |
| dosing | 32 | 配药方案 |

---

## 五、环境搭建（新账号必读）

### Python（managed venv 已装好，直接用）
```bash
PYTHON="C:/Users/believer/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
# 已装：fastapi uvicorn sqlalchemy torch(2.14.1+cpu) opencv(5.0) scikit-image scipy Pillow xgboost numpy
```

### 前端
```bash
cd D:/铅锌浮选品位预测系统/suieye-platform/frontend
npm run dev   # :5188
# 依赖已装（node_modules 存在）
```

### 启动顺序
1. 后端 `uvicorn backend.main:app --port 8300`（**改 Python 代码必须重启后端**，无热重载）
2. 前端 `npm run dev`
3. 浏览器访问 `http://127.0.0.1:5188`

---

## 六、已知坑（踩过才知道）

1. **循环导入**：Filter 注册集中在 `filters/__init__.py`，单个文件不注册
2. **numpy 数组判空**：用 `is not None`，不能用 `or`（`ValueError: truth value ambiguous`）
3. **opencv-python 冲突**：装 `opencv-python-headless`，不要装 `opencv-python`
4. **IPv6 代理**：`vite.config.js` 的 proxy target 必须写 `http://127.0.0.1:8300`，不能写 `localhost`（会解析成 `::1` 导致 ECONNREFUSED）
5. **Windows 中文路径 + cv2.imwrite**：静默失败，用 `cv2.imencode()` + `Path.write_bytes()`
6. **SQLAlchemy JSON 列原地改 dict**：必须 `flag_modified(record, 'column_name')` 才生效
7. **AnalysisRecord 无 `metrics` 列**：前端读 `r.features`（不是 `r.metrics`）
8. **图片路径双模式**：`serve_image` 支持相对路径（`data/uploads/...`）和绝对路径（`D:\flaoting\dataset\...`）

### 2026-10-11 新增坑（换机接手踩到的）
9. **Vite 默认绑 IPv6 `::1`**：导致 `127.0.0.1:5188` 拒绝连接。已在 `vite.config.js` 显式加 `host: '127.0.0.1'`，不要删
10. **本机 curl 必须绕过系统代理**：此环境有 `http_proxy=127.0.0.1:52539`（WorkBuddy 内部代理），测本地服务要加 `--noproxy "*"`，否则一律 502
11. **FastAPI 空字符串查询参数会 422**：`level=''` 撞 regex 校验、`acknowledged=''` 撞 bool 解析，且**校验发生在函数体之前**，函数体内归一化无效，必须在签名层去掉校验再手动判断（见 `alarms.py`）
12. **箭头函数丢 `=>` 的隐蔽笔误**：`preds.filter(v > 7.0)` 会抛 `ReferenceError: v is not defined`，且是在 computed 求值时炸 → 表现为"页面渲染到一半、图表空白"，控制台只报"加载失败"。搜这类 bug 时不要用 `.filter\(v` 之类的排除模式，会把案发现场滤掉
13. **排查前端运行时错误的神器**：`agent-browser` 未装，可直接用 Chrome + CDP（Node 22 内置 WebSocket），脚本见工作日志；`--enable-logging=stderr` 也能抓控制台日志

---

## 七、数据库状态

- SQLite：项目根目录 `suieye.db`（约 3.7MB）
- 备份：`suieye.db.bak_20261010`
- 表：`analysis_records`（含 `image_path`/`features`/`prediction` JSON 列）、`alarm_records`、`dosing_records`、`scenes` 等
- 演示数据通过 `scripts/seed_demo_data.py` 生成，`source="demo"` 标记可幂等清理重灌

---

## 八、快速上手 Checklist

- [ ] 读本文 + 三条铁律
- [ ] `cd D:/铅锌浮选品位预测系统/suieye-platform`
- [ ] 启动后端（`uvicorn backend.main:app --port 8300`）
- [ ] 启动前端（`cd frontend && npm run dev`）
- [ ] 打开 `http://127.0.0.1:5188`，遍历 10 个页面确认可用
- [ ] 记住品位字段是 `pb_grade_xrf` / `zn_grade_xrf`
- [ ] 改前端后看热更新；改 Python 后重启后端
- [ ] 算法改动前先确认"是否经过真实图像验证"

---

## 九、上下文恢复

- 工作日志：`C:/Users/believer/WorkBuddy/2026-10-07-15-15-33/.workbuddy/memory/2026-10-*.md`
- 参考项目（分割方法来源）：微信文件 `铅锌浮选品位预测系统.zip`
- 老板偏好：浅色 UI（否决过深色方案）、表格化总结、P0/P1/P2 优先级、逐阶段确认、称呼"老板"

---

## 十、2026-10-11 接手日变更记录（新 Buddy）

> 本轮完成了接手三件事 + 演示前 P0/P1 修复。数据库表结构以实际为准（`scene_packages`/`analysis_records`/`model_versions`/`assay_ledger`/`alarm_records`/`dosing_records`，**无 `scenes` 表**，上文第七节已过时）。

### 品位口径裁决（重要！）
- **真实台账** `D:/flaoting/dataset/labels.xlsx`（无表头）：col1=Pb 0.99~3.12%（均值 1.48）、col2=0.43~0.68
- **裁决**：老板拍板**对齐 HANDOFF 3~8% 口径**（原决策维持），真值不跟台账走
- 已用 `scripts/fix_actual_value_20261011.py`（固定种子 20261011，同 group 共享真值，pred×(1+N(0,0.05)) 裁剪 [3,8]）重灌 `actual_value`：3032 条，min 3.0 / max 8.0 / mean 5.23
- 备份：`suieye.db.bak_20261011_pre_actualfix`（改前状态）
- 已知残留：今天 2 条 upload 记录是**真实模型输出** Pb≈1.8%（口径外），报表趋势末尾会下坠、预测范围显示 0.80~9.83。属"真模型输出"，演示时可直接解释

### 代码修复
| 项 | 文件 | 内容 |
|----|------|------|
| P0-1 告警页全 0 | `Alarms.vue` + `backend/api/routers/alarms.py` | 前端过滤空参；后端 level/acknowledged 改手动校验（空串放行、非法值 400） |
| P1-3 配药历史恒空 | `DosingAssistant.vue` | 5 处 `api.get('/api/dosing/...')` → `'/dosing/...'`（baseURL 已含 /api） |
| P0-2 品位脱节 | `scripts/fix_actual_value_20261011.py` | 见上 |
| 时光机图表空白 | `TimeMachine.vue` L73 | `filter(v > 7.0)` 补回 `=>`（summary computed 求值即炸导致渲染中断） |
| 仪表盘趋势图空白 | `Dashboard.vue` L68 | yAxis 去掉写死的 min:0.5/max:4，改 scale:true（品位升到 5.2 后全在轴外） |

### 遗留（未修，老板指示先放）
- P1-4 时光机最新 3 条 upload 记录泡沫图缺失（显示占位图）
- P1-5 模型档案 KPI 存疑（累计推理 26 次 vs 库里 3032 条记录）
- P2：Dashboard 两个未激活场景（water/equipment）显示 0 记录；Settings"数据概览"卡空白；1 条旧字段 `pb_grade` 遗留记录；Equipment3D 视口深色（老板浅色偏好，需确认）；磨矿 P80 demo 值域与三工况口径（~130/58/245）需核对

---

## 十一、2026-10-11 安全巡检模块（新增，演示版完成）

> 老板需求：巡检不能只有"上传图分析"，要做多点位监控 + 巡检记录流 + 隐患整改闭环。

### 新增页面
- **`/safety` 安全巡检中心**（`SafetyCenter.vue`，导航"安全巡检"）：KPI 条（今日巡检/待处理隐患/30天危险区入侵/PPE合规率）+ 4 点位监控网格（状态灯+最新检测图+核心指标）+ 隐患闭环看板（待整改→整改中→已销项，可点流转）+ 巡检记录流
- Workbench 单图分析保留为"临时抽检"入口（从巡检中心跳转）

### 新增后端
- `backend/api/routers/safety.py`：`/api/safety/cameras|timeline|stats|hazard-tickets`（+ transition 流转）
- `backend/models/database.py`：新表 `hazard_tickets`（pending→rectifying→closed 闭环）
- 点位口径：CAM-01 1#皮带·机头 / CAM-02 机尾 / CAM-03 2#皮带·中段 / CAM-04 破碎给料站（点位身份存 `features.camera_id`，未动表结构）

### 数据回填
- `scripts/backfill_safety_demo.py`（固定种子 20261011，幂等可重跑，~45s）：60 天 × 4 点位 × 3 班 = 712 条记录，全跑真管道，可视化三图落盘 `data/outputs/safety/`（路径在 `features.image_paths`）
- 风险分布：low 473 / medium 119 / high 80 / critical 40；告警 317；工单 120（closed 83/pending 15/rectifying 22）
- 人像素材：ultralytics assets（bus/zidane）YOLO 裁剪 5 个 person patch 贴图（COCO val2017 下载超时未用上；zip 在 `data/_datasets/`）

### 本轮修复的 4 个隐藏 Bug（重要！）
| Bug | 文件 | 说明 |
|-----|------|------|
| safety 告警段键名错误 | `backend/scenes/safety/safety.yaml` | `alarms:` → `alarm_rules:`（runtime 只读后者） |
| safety 告警规则格式不兼容 | 同上 | 复杂表达式 `a in [...] and b>0` 改为 alarm.py 的 `field/condition/threshold` 简单比较格式 |
| YOLO 模式下异物检测被跳过 | `backend/engines/filters/detect_objects.py` | `_detect_yolo` 只补了跑偏规则，补上 `_detect_foreign_objects` 调用 |
| OpenCV 5.x numpy 坐标崩溃 | 同上 | `_draw_detections` 的 `det["center"]` 必须 `int()` 转换（检出即崩） |
| danger_zones 配置位置错误 | `backend/scenes/safety/safety.yaml` | extract_safety_features 的 params 里没有 danger_zones（人员入侵判定恒 0），已复制一份到该段 |
| serve_image 相对路径 404 | `backend/api/routers/alarms.py` | 兼容 `data/...` 前缀路径（旧逻辑拼 DATA_DIR 变 `data/data/...`；这也修好了时光机 upload 挂图问题的一部分） |

### 合成图注意事项（改回填脚本必读）
- 皮带底图**不能画**结构性边缘线/横线纹理/托辊矩形——会被异物检测器（LAB 局部 std）误判为异物，normal 帧全变 high
- 噪声幅度 ≤ ±3
- `FORCE_HIGH_CONDITIONS`（assess_risk.py）：foreign>=3 或 belt_offset>50 强制 high；persons_in_danger_zone>0 强制 critical

### 巡检演示脚本建议
1. 巡检中心总览 → CAM-01/04 红色告警卡（人员入侵，危险区 1-3 人）
2. 点"开始整改" → 填整改措施 → "复查销项" 闭环演示
3. 点位"回放"跳时光机 → "临时抽检"跳 Workbench 上传新图实时分析

---

## 十二、2026-10-12 ~ 10-17 重大进展（★ 新账号必读）

> 这 6 天的变更量远超此前总和。按重要性排序，**第一条是当前主战场**。

### 12.1 ★★ `/monitor` 实时监控页（当前主战场，2026-10-16 当晚定稿）

**定位**：单点位监控（粗选给矿 Feed II），砍掉了原四路画面墙；`Replay.vue` 已删。
**布局**：两栏 —— 左 = 现场视频（大幅，画面内 HUD）；右 = 给矿品位趋势 + 工艺链实时快照。

**★★ 三级时间语义（本页最核心的设计，改动前必读）**：
| 级 | 变量 | 含义 |
|----|------|------|
| ① 画面时刻 | `curSec` = `seg.startSec + video.currentTime` | 录像进度 1:1，**仅 HUD 时钟用**（`seg.startSec` 由文件名 `HHMMSS` 硬编码解出） |
| ② 数据时刻 | `dataSec` | **数据时钟 30x 快进**并在数据窗口内循环（`DATA_SPEED=30` / `DATA_T0=10:49` / `DATA_T1=12:51`） |
| ③ 节拍时刻 | `beatSec` | 固定节拍器每 `BEAT_MS=800` 对齐到 `dataSec` → **右侧所有数据的唯一来源** |

- ⚠️ **为何要数据时钟快进**：化验 16.6 分钟一测（实测录像窗口内仅 8 个测点），视频 1:1 播放则 17 分钟才动一格 → 右侧长期静止，老板判"看不出在动"。30x 快进后每 0.8s 画面 = 24s 数据，曲线持续左滚**肉眼可见**。
- ❌ **不要退回"本机时钟"**：旧版用本机时间 → 演示时数据只到 12:46，右侧冻住（老板判"没联动"的根因）。

**滚动窗口**：`WIN_MIN = 30`（分钟）；`xEnd = dayMs(0,0) + tickSec*1000`、`xStart = xEnd - WIN_MIN*60*1000`。
- ★★ **光标钉在最右端 = "现在"**，曲线从右往左滚（老板明确否掉"固定时段+游标扫"）
- 无 x `markLine`（右端即现在）；`markPoint` 在右端
- ★★ **x 轴刻度用 `minInterval`/`maxInterval = 10min` 强约束**（time 轴单写 `interval` 会被 ECharts 智能细分 → 曾出现 4min 一档 8 个标签显"满"）
- ★★ **必须"借"窗口左缘锚点**：`anchor = raw.filter(d => d[0] < X0).slice(-1)` → 曲线从左边缘连续进入
- three series：`已发生区间`(面积) / 主线实线蓝 / `实测点`(白底蓝环)

**★★ 趋势图叠加多测点（2026-10-16 晚，回应"整体变化太小"）**：
- `TREND_SERIES` = 给矿 Pb（蓝，左轴） / 铅扫选尾矿 Pb（绿，左轴） / 铅精矿 Pb（橙，**右轴**）
- 起伏来自**真实工艺差异**（精矿 59% vs 尾矿 0.45%），**不是放大 y 轴**（老板否过"太假"的压轴做法）
- `leftAxis`（0~5%，step 0.5）+ `rightAxis`（精矿段）；双轴 `splitNumber: 3`
- 公用构造抽成 `lineDataOf(point, element)`

**★★ 工艺链参照标准（2026-10-16 晚，回应"没有参照标准"）**：
- 后端新接口 **`GET /api/courier/bands`** = 9 测点×元素**全月真实分位** P01/P10/P50/P90/P99（进程内缓存；纯 Python 线性插值）
- **小线坐标系 = 历史 P10~P90**（不再各线自归一）→ 9 条线**横向可比** + 淡绿背景带 = 一眼看异常
- 每行 = 名称前**状态点**（绿/橙/红）+ 小线**淡绿背景带** + 数值右侧 **`正常/偏高/偏低/超上限/超下限`** 标签（hover 出区间）
- 判定：P10~P90=正常；出 P90/P10=偏高/偏低（橙）；出 P99/P01=超限（红）
- ★ 录像窗口内**真有越限点**（铅精矿 12:20=56.36% 偏低 / 铅粗选尾矿 11:11=1.46% 偏高 / …）→ 数据时钟循环快进时会自然扫过、**标签变色**，非永远绿

**工艺链小线**：`sparkPoints(key)` 返回 `{line, area, dot, band}`；普通折线（❌ 老板否掉 `step` 阶梯）+ 淡蓝面积 + 末端呼吸落点 `.cr-dot`；行高 26px；矮屏 `@media (max-height:880px)` 回落紧凑尺寸。
- ⚠️ **SVG 渐变 id 不能含空格/管道符** → 用 `gid = ${gi}-${i}`（曾用 `r.key="Feed II|Pb"` → 渲染成黑块）
- ⚠️ **SVG 引用嵌套属性要写全路径**：`v-if="r.spark.band"`（band 在 spark 返回值里）

**右栏高度铁律**：`.mn-side { grid-template-rows: minmax(0,1fr) minmax(0,auto) }`（❌ 别按比例 `1fr:1.35fr` → 卡片底部纯白块）。

**验收探针**（`outputs/_qa/`）：
- `qa_monitor_scroll.py`（最硬证据）：每 0.8s 采样节拍时刻 → 实测每步 +24s 数据 / +0.8s 画面，倍率 26.2x，8/8 连续在动
- `qa_monitor_roll.py`（落点钉最右 + tick 前进）、`qa_monitor_fast.py`（倍率）、`qa_monitor_seg.py`（跨段跳变）、`qa_monitor_layout.py`（量盒子找空洞）
- ★★ **像素级"在动"证明法**：连拍两张截图 → PIL 裁剪 + `ImageChops.difference` → 趋势区 16.4% / 工艺链区 15.7% 在变，**左侧导航 0.0%**（对照组，证明右侧独立刷新而非整页重绘）
- ⚠️ **探针不能读 `window.echarts`**（项目 `import * as echarts`，不挂 window）→ 读 DOM 文本或用像素对比

### 12.2 ★★ 凡口真实生产数据（2026-10-16，数据可信度的根基）

- 源：`D:/QQ/2020csu浮选视频.zip/2020csu浮选视频.zip`（4GB 双层 zip）；30 AVI（前 7 静态弃用 / 后 23 段真录像 10:56~12:46，每段 40-50s）+ 2191 XLS + 工艺 PDF
- 表 **`courier_assays`**（与 AssayLedger 飞轮分开存）：**62,798 条** / 2019-07-31~08-30 / 9 取样点 × Fe/Zn/Pb；**每点 ~16.6 分钟一测**（单日 83 点/点）
- 导入脚本 `scripts/import_courier_reports.py`（流式直读 zip、内存去重、`on_conflict_do_nothing` 幂等）
- **API**（`backend/api/routers/courier.py`）：`/api/courier/overview`、`/day?date=`（一次拉全天 ~2256 条）、`/series?point=&element=&date|days=`、`/bands`（历史分位）
- 取样点语义（数值自洽）：Feed II=给矿（Pb 3.2~5.2%）/ Kpb=铅精矿(59~60%) / Kzn=锌精矿 / Pb KCX、Zn KCX=粗选尾矿 / Xpb、Xzn=扫选尾矿 / Pb CP=硫精矿 / **H pb-zn=混合流（语义待厂方确认）**
- 凡口工艺（PDF）：先铅后锌优先浮选、高碱、丁黄药+乙硫氮+硫酸铜+2#油；"II"=二系统

### 12.3 ★★ 浮选泡沫实时画面（2026-10-15 建、10-16 换真实素材）

- 素材：`frontend/public/media/froth2019/froth-loop.mp4` = 凡口 2019-08-22 真实录像**无缝循环**（42.8s / 20fps / 4.1MB；选段 `20190822113630`，位移 NCC 0.924；循环点物理同帧 NCC 0.9985）
- 组件 `FrothLive.vue`：`variant='panel'`(4:3) / `'band'`（高度交外部）；落点 = 工作台左列顶部 + 直播墙左栏右侧
- ★★ **真视频 vs 照片集判别**：必须用「**位移搜索后 maxNCC**」——照片集 0.109、真视频 0.76~0.92（原位置 NCC 对高频流动纹理无判别力）
- ★ 循环构造：单视频无缝循环 = `xfade(tail,head) + concat 主体`；ffmpeg 用 `pip install imageio-ffmpeg`（本机无 ffmpeg）

### 12.4 ★★ 设备监测 3D 工业细节（10-15~10-16）

- 6 类机型；程序化构件库 23 构件（**不走 glTF**）；`frontend/src/utils/equipDetails.js`
- ★ 详见 skill `~/.workbuddy/skills/threejs-industrial-detail/`（含：染色铁律、欧拉序轴向陷阱、bloom 白斑定位法、浮层交互三铁律）
- ★ 停机语义：`control_state='stopped'` → 运动类指标归零 + 温度按停机时长冷却（env 25/τ=8min）+ health 冻结

### 12.5 其他模块进展摘要

| 模块 | 状态 |
|------|------|
| **直播墙 `/`** | 五块通栏布局；浮选泡沫窗与流程面板并排；`fitAlarmRows()` 条数自适应 |
| **水质 `/water`** | 9 接口 + 3 表 6005 读数；`WS /ws/water` 每 10s 推 5 点位；回水可回用评分 |
| **尾矿 `/tailings`** | 8 接口；软测量 R²=0.696（**必须按日期分组切分** `GroupShuffleSplit`，时间切只有 0.033） |
| **告警中心 `/alarms`** | 446 条 → **349 事件**（`group_key` 消 21.7% 同源冗余）；P1 根因归因 `root_cause.py` |
| **设备工单** | 5 张表；状态机 `pending→assigned→repairing→fixed`；工单中心挂 **`/api/repairs`**（非 `/api/equipment/repairs`，会被 `/{equip_id}` 抢先匹配） |
| **设置 `/settings`** | 写 YAML 文本级替换后**必须** `runtime.reload_scene_config()` |

### 12.6 当前待办（按优先级）

1. **`/monitor` 可选增强**（等老板拍板）：测点间"仪表连续读数"还原（现仅 16.6min 一点 → 曲线单段斜线；**涉数据诚实红线，不得擅自做**）
2. **凡口 `H pb-zn II` / `Pb CP II` 取样点语义**待厂方确认
3. `/api/equipment` 健康分每请求波动 → **P0-2 状态递推**（接台账+其他模块真数据）
4. 是否重跑 3006 张浮选图分割（当前 3033 条中 3006 条分割失败，整图当单气泡）
5. `prediction.actual_value` 口径脱节待修
6. 数据陈旧：`analysis_records` 最新 10-11、water/tailings 10-13
7. 3D P2：场景级（管廊桥架/围栏）

### 12.7 本机环境速查（新账号必看）

```bash
PYTHON="C:/Users/believer/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
NODE="C:/Users/believer/.workbuddy/binaries/node/versions/22.22.2-3/node.exe"
CHROME="C:/Program Files/Google/Chrome/Application/chrome.exe"

# 后端（★ 必须从项目根起，不是 backend/ 目录）
cd D:/铅锌浮选品位预测系统/suieye-platform
$PYTHON -m uvicorn backend.main:app --host 127.0.0.1 --port 8300

# 前端（★ 必须在 frontend/ 里起）
cd D:/铅锌浮选品位预测系统/suieye-platform/frontend
$NODE node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5188
```

**★★ 本机 6 个必踩坑**：
1. **curl 本地服务必须加 `--noproxy "*"`**（系统有内部代理，否则一律 502）
2. **vite/后端进程在 turn 结束被回收** → 「启动服务 + 探针/截图」**必须写在同一条命令里**
3. **测试前先查端口残留**：旧进程抢答会让新代码"看起来没生效"却不报错；杀端口用 PowerShell `Get-NetTCPConnection -LocalPort ... | Stop-Process`
4. **vite 会自动改端口**（5188 被占静默挪 5189）→ `vite.config.js` 已加 `strictPort: true`
5. **Chrome CDP 探针**：必须 `--remote-allow-origins=*`；**不能加 `--virtual-time-budget`**（Chrome 会提前退出、连接被掐断）；视频自动播放加 `--autoplay-policy=no-user-gesture-required`
6. **一个 Chrome profile 目录 = 一次启动**（复用会得到空场景）；截图找输出用绝对路径

**语法验证**：`cd frontend && $NODE node_modules/vite/bin/vite.js build --logLevel warn`

---

## 十三、2026-10-18 ~ 10-19 重大进展（★ 比十二节更新，优先读这里）

> 这三天完成三件方向性的事，**改变了系统的门面定位**。上一节（十二）写的是 `/monitor` 内部细节，这一节是「首页长什么样、导航怎么走、品牌叫什么」。

### 13.1 ★★ 去全局左侧栏：3D 厂区 = 全站唯一入口层（提交 `5d046c6`）

- **背景**：左侧栏与厂区导览卡各列一遍功能，14 项里 10 项重复、6 组同一页两种叫法。
- **决定**：**删掉全局 220px 左侧栏**。老板原话「3D 页作为首页和主页，做成其他功能页的入口」。
- **两条动线**：① 进入 = 首页双击建筑 / 点工艺段 ② 返回 = 页头「← 智能工厂」。
  - `components/BackToFactory.vue` 接入 11 页；⚠️ 必须 `align-self:flex-start`（否则被 flex 拉成 1440px 隐形大按钮）。
- `utils/navMap.js` 仍是**名称唯一真源**（虽不再渲染侧栏，勿删）。
- 工作台族入口用 `workbenchEntryName()` 消歧（否则 `/workbench/safety`、`/workbench/flotation` 都叫「分析工作台」→ 重名）。

### 13.2 ★★ 3D 页升格为「全站门面 / 数字孪生导览屏」（提交 `6f0227b`）

**定位转变**：门面答"这座厂此刻怎么样"；运维台才答"1#浮选槽健康度多少"。
删掉运维台语汇：设备清单浮层、操作日志、状态 tag 组、调试按钮组。

**新增**：
- `.facade-head` 页头 = 品牌 + 四格全厂 KPI + 单一去处「进入实时监控」
- `.verdict-bar` 全厂结论条（一句话归并设备/品位/水质三路真实字段）
- `.corner-orders` 角落工单入口（**右下角**；左下角会被导览卡压住 26px）

**浅色沙盘**（深色工业夜视 → 浅色沙盘），两个必踩的坑（详见 skill `threejs-industrial-detail` §12）：
1. **照搬深色版光强必过曝**：设备变白色剪影 → 压光总量（半球 .72→.38、环境 .18→.10、主光 1.35→1.10），别压地面。
2. **地面不能纯白**：会溶进浅灰设备外壳，只剩标签浮着像"没加载" → 中心 `#c6d0dc` 比外壳暗一档，边缘反亮。

**相机"只后撤不拉近"**（skill §13）：`Math.max(k, FIT_MIN_SCALE=1.08)` 把宽屏顶死，1920 下厂房仅 585×259px。补 `k = sqrt(HOME_ASPECT/aspect)` 宽屏拉近，下限 `WIDE_MIN_SCALE=0.86`（0.72 会切顶棚钢架，**只能截图定**）。

**KPI 口径铁律**（skill §14.2）：门面别放恒定指标。`in_service/total` 恒 100%（故障/预警设备仍在役）→ 与结论条"1 台故障"打架像数据假。改 `avg_health`。

**导览卡默认收起**：展开态 620px 高会盖住整个左侧厂区；改默认值**必须升 localStorage key 版本号**（`.v1`→`.v2`，否则旧状态顶掉新默认值）。

### 13.3 ★★ 品牌统一（提交 `262faeb`）

**系统正式全称：`SuiEye 淬眸 · 铅锌浮选全流程智能视觉分析平台`**（西文在前、中文在后）。
- 不再写「淬眸 SuiEye」；副标题用全称。
- `router.afterEach` 首页特判不拼品牌后缀（否则变成「…平台 · 淬眸 SuiEye」同一品牌名一屏两遍）。
- `backend/config.py` 的 `APP_NAME` = 全称（FastAPI `/docs` title）。

### 13.4 ★★ 新增 QA 必踩坑（修正了旧结论）

- **探针收尾必须 CDP `Browser.close`，不能 `terminate()`**：terminate 让 Chrome 回 RST，vite（Node 22）吃 ECONNRESET 直接退出 → 后续尺寸拿到错误页（textLen 恒 91 / querySelector 返 null），看着像"布局坏了"，实为探针自杀。
- **探针别假设浮层是展开态**：`v-show` 隐藏时点它不展开 → 误报"空壳按钮"。
- **判遮挡用真实重叠面积**，别用矩形相交。
- 验收探针新增：`qa_facade.py`（门面三尺寸）、`qa_net_throttle.py`（网络错误节流）。

### 13.5 本轮待办（交接时刻）

1. **监控立杆对应关系待老板拍板**：3D 里 `cctv`（监控立杆，4 路）现在 `entries: [{ path: '/livewall' }]`；但「实时监控」是 `/monitor`（单点位趋势）、「直播墙」是 `/livewall`（四路画面墙）。老板正在权衡「右上角 CTA 进实时监控」与「监控立杆进直播墙」的语义是否要调。
2. 3D 遗留：标签贴视口底缘被切一半（**故意不改**，钳制会破坏"标签锚定设备"）；左侧「监控立杆」孤悬空地与主厂区视觉脱节。
3. 设备数量 10→18 待定；中控室玻璃更透明待定。

> 完整坑点与决策记录见 memory 目录 `2026-10-19.md` 与 `MEMORY.md`（比本文件更新、更细）。
