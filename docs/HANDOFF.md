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
1. ~~监控立杆对应关系待老板拍板~~ —— **2026-10-19 已定稿**：入口按「空间对象看画面、展示层进展示层」分工。
   - 立杆 `cctv` → `/monitor`（单点位现场画面 + 画面内 HUD），tag 改「4 路机位 · 1 路在线」；
   - 右上 CTA 改「进入直播墙」→ `/livewall`（汇报大屏）。
   - ⚠️ 更正上一版文档的错误描述：`/livewall` **不是**四路画面墙，它是大屏汇报页
     （4 格全厂 KPI + 选矿工艺流程 + 30 天趋势 + 报警轮播 + 1 路浮选泡沫窗）。
     四路画面墙在 2026-10-16 `/monitor` 单点重构时已被砍掉 —— 旧映射正是那次的残留。
   - 验收：`qa_facilities`（cctv→/monitor）12/12、`qa_cta_route`（新写，CTA 真落点）7/7、`qa_facade` 三尺寸 ALL PASS。
2. 3D 遗留：标签贴视口底缘被切一半（**故意不改**，钳制会破坏"标签锚定设备"）；左侧「监控立杆」孤悬空地与主厂区视觉脱节。
3. 设备数量 10→18 待定；中控室玻璃更透明待定。

> 完整坑点与决策记录见 memory 目录 `2026-10-19.md` 与 `MEMORY.md`（比本文件更新、更细）。

---

## 十四、2026-10-19 门面取数口径收口（P0，方案 A）

> 事故现象：门面第一屏左手写「Pb 品位均值 5.23%」、右手写「最新一次分析 **1.79%**」，
> 结论条同时打出红字「最新品位低于均值 66%」。访客第一反应就是"数据是假的"。

### 14.1 根因：**跨量比较**，不是数值错

| 指标 | 取数 | 量纲 |
|---|---|---|
| Pb 品位均值 5.23% | `stats.grade_distribution.mean` ← **actual_value（XRF 实测真值）** | 真值 |
| 最新一次分析 1.79% | `records?limit=1` ← **prediction（模型输出）** | 预测 |

两个数从来不是同一个量。且该条 `1.79%` 是 2026-10-16 的 upload 记录（真模型跑真图），
落在 3~8% 演示口径之外 —— 它在台账里合法存在，但**不该当门面头条**。

### 14.2 改法：口径定义在后端，取数层过滤，一条真值不动

- `backend/config.py` 新增 `PB_GRADE_MIN=3.0 / PB_GRADE_MAX=8.0`（**口径唯一真源**）
- 新增 `GET /api/scenes/{scene}/latest`：从最新往回扫，返回口径内第一条，
  并如实带回 `scanned / skipped / missing / caliber`
- 前端门面改调该接口；副行补**日期**（`Pb · Zn 3.28% · 10-10`），
  `title` 悬浮说明「口径 3~8% 内最近一次；已跳过 7 条口径外记录」
- 口径外记录**一条没删**，`/records` 与时光机仍可查

### 14.3 ★★ 一条别踩的"更差方案"

曾考虑「改用最近一条有 `actual_value` 的记录」当"最近化验"（与均值同量纲）。
**实测否决**：10-11 那两条 upload 的 `actual_value` 恰好等于 `3.00` ——
是 `fix_actual_value_*` 脚本把 `pred×噪声` **裁剪到 [3,8]** 的截断产物，
拿它当"最近化验"反而是全场最假的一个数。
→ 教训：**裁剪边界值（3.00 / 8.00 恰好命中）要当作"疑似人造"信号**，不能当有机值用。

### 14.4 验收

- `/latest` 实测：`scanned=8 / skipped=7 / record=id3003 / pb=5.38 / at=2026-10-10`
- `qa_facade` 三尺寸 **ALL PASS**，新增两条守卫：
  ① 「最新一次分析」必须 ∈ [3,8]；② 结论条不得再出现「低于均值」
- 门面实拍：均值 5.23% / 最新 5.38% · 10-10，结论条只剩设备与水质（无品位告警）

### 14.5 遗留

- `prediction` 与 `actual_value` 的**整体口径错位仍在**（全量 3036 条里 268 条预测出 3~8，
  min 0.80 / max 10.46）。本轮只收口"门面头条"，未动底层数据 —— 因为预测分布本就该宽于真值，
  真正要修的是**模型校准**，不是把预测裁进真值区间。
- 门面「3,032 次分析」与 `/api/scenes` 的 3036 条差 4 条（prediction 缺值），口径说明待补。

---

## 十五、前端一致性整治（2026-10-19，P0 已完成）

> 老板原话：「整体的前端还要重新设计，因为不同页面风格都不协调，没有一致性」。
> 完整方案见 `docs/design-consistency-plan.md`；本节只记**接手必须知道的规矩**。

### 15.1 病根：不是"每页不好看"，是"每页各自为政"

- `tokens.css` 2026-10-09 就有了，但**没有强制力**：实时监控 / 设置 / 配药 三页 **0 处使用**，
  工作台 / 安全巡检各 1 处；3D 门面 247 处硬编码、**92 种颜色**。
- 页头有 8 种写法，标题字号 18/20/22 混用；KPI 有 4 种形态（裸数字条 / 六小卡 / 四色卡 / 组内 40-20px 混排）；
  图表 5 套配色，仪表盘环形图 7 色。

### 15.2 规矩（新页面必须遵守）

| 层 | 唯一入口 | 纪律 |
|---|---|---|
| 令牌 | `styles/tokens.css` | **禁止在页面里写十六进制色**；缺什么令牌就加令牌，不要就地写死 |
| 骨架 | `components/PageShell.vue` | 页头 56px：返回 + 标题(20/600) + 副标(13) + 动作区；页面留白由它给，页面自己不许写 padding |
| KPI | `components/StatCard.vue` | 固定 4 格；`kpi="block"` 管理型（88px 卡片）、`kpi="inline"` 分析台型（塞进页头） |
| 内容卡 | `components/Panel.vue` | 白底 + 1px 边框 + 圆角 8 + **无阴影** |

三种版式原型（保留合理差异，不要做成一个模子）：
**门面型**（`/`、`/livewall`）、**分析台型**（`/monitor`、`/tailings`、`/water`、`/workbench`）、
**管理型**（其余）。

### 15.3 已完成（P0 + P1 全站）

三页试点 → **11 个页面全部套用 `PageShell`**（3D 门面与直播墙按"原型 A"保留满屏版式）。
实测（1440 与 1024 双尺寸 `qa_ds.py` **ALL PASS**）：
页头 56 / 标题 20·600 / 留白 20·24 / KPI 4 格（尾矿 6 格）/ 卡片 r8 无阴影 / 行内硬编码色 0 / 横向溢出 0。

顺带做的一次性收敛：
- **全站 323 处硬编码色**换成令牌（同值映射，视觉零变化）；剩 `--bg-video` 等已登记为令牌
- `el-card` 在 `style.css` 里全局压到 Panel 规格 —— **存量页面即使没换结构也一起对齐了**
- 报表卡片阴影、尾矿/水质 6px 圆角，统一到 8px

### 15.4 未完成（P2）

- ECharts 主题收敛成 `chartTheme.js`（现在仪表盘环形图仍是 7 色，是全站最后一个"拼装感"来源）
- 表格统一（表头底色 `--bg-2`、行高 44、仅下边框）
- 空态/加载态/错误态统一成 `EmptyState`
- LiveWall 只做了色值收敛，页头结构未动（大屏自成一格，改动需单独评审）


### 15.5 两个易踩的实现细节

- `PageShell` 里必须 `:deep(.page-back){margin-bottom:0}`：`BackToFactory` 自带 12px 下边距，
  不清零会把 56px 页头顶高。
- `StatCard` 的 `tone` 只允许表达状态（success/warning/danger），**不许当装饰色** ——
  这是整治前"处处彩色卡片"的复现入口。

---

## 十六、门面页头二次重设计（2026-10-19）

老板看完 P1 后指着门面顶部截图说「这里也要重新设计一下」。
第 15 节把 11 个内容页统一了，**门面是唯一没被覆盖的**——它自成一套 `.facade-head`。

### 16.1 病灶（4 条，每条都有代码证据）

| # | 问题 | 证据 |
|---|---|---|
| 1 | 没有任何容器边界 | `.facade-head` 只有 `padding: 0 20px 0 4px`，无背景无边框 |
| 2 | 三条左基线互不齐 | logo **4px** / 结论条 **0px** / 第一格 KPI **≈190px** |
| 3 | 结论条通栏贴屏 | `.verdict-bar` 左右 margin 都是 0、宽 100%，把 3D 切成两段，读起来像事故横幅 |
| 4 | KPI 无状态语义 | 78.0 分（3 台需关注）和 5.23%（正常）同一个墨色；唯一提示是 10.5px `#a2abb9` 的小字，1440 下读不清 |

第 4 条的严重性被低估了：那行小字是**口径说明**（3,032 次分析 · 2026-06~10）。读不清 = 口径没写。

### 16.2 解法（老板拍板：卡内分栏 + 无异常隐藏）

- `.facade-head` → **`.facade-card`**：`--bg-1` + 1px `--border-1` + `--panel-radius`，与站内 Panel 同规格
- 卡内三段 `.fc-main`：品牌 20 ｜ 4 栏 KPI ｜ CTA 20，**左基线统一到 20px**
- 结论条 → **`.fc-verdict`**：内嵌卡底、宽随卡、左侧 3px 状态竖条、去掉圆点（竖条已表达状态）
- **`level=ok` 时整条不渲染**（`v-if="kpiReady && plantVerdict.level !== 'ok'"`）
- KPI 状态色：`healthTone` / `waterTone`，阈值与 `healthColor()` 严格一致（≥80 正常 / 50~80 预警 / <50 故障）
- `.fk-foot` 10.5px `#a2abb9` → **11px `--text-3`**，并给第一格补 `:title` 兜住被省略的全文
- `.equipment-page` 页底改 `--bg-3`：白卡落在白底上等于没有形状，**这是第一版没做出效果的原因**

### 16.3 实测（`qa_facade.py` 三尺寸 ALL PASS）

| 项 | 结果 |
|---|---|
| 卡样式 | `bg=rgb(255,255,255)` / `radius=8px` / `border=1px`（三尺寸一致） |
| 状态带 | `l/r` 与卡对齐（内嵌）、`::before` 宽 **3px**（三尺寸一致） |
| 卡高 | 138 → **113**（有异常）/ **83**（无异常）；1024 换行两段时 165 |
| 卡宽 | 1864 / 1384 / 968（视口 −40） |
| 回归 | `qa_cta_route` 7/7、`qa_nav_hub` 24 项、`qa_facilities` 12/12、`qa_ds` 11 页 ALL PASS |

### 16.4 两个易踩点

1. **卡必须左右留白**：`margin: 0 20px 10px`。写成 `0 0 10px` 时卡通栏，
   白卡落在白底上，肉眼完全看不出改了（第一版就是这样，探针却全绿——它量的是尺寸不是"看得见"）。
2. **状态色阈值必须与 `healthColor()` 同源**。否则会出现"数字是橙的、结论条却说全厂正常"，
   这比不做状态色更糟。

### 16.5 新增令牌

`--brand-mark-2`（品牌标记渐变浅端）、`--text-inverse`（主色底上的文字）、
`--color-warning-border` / `--color-danger-border`（状态浅底的描边）。
顺手删掉 `--bg-video` 的重复定义（同一令牌写了两行）。

---

## 十七、告警实时性 + 对外接入（2026-10-19）

老板："我的告警系统要有实时性，也可以接入其他软件，可以用 websocket。"

### 17.1 先纠正一个我自己的误判

我一开始判断是"WS 通道接错了数据源"。查下去发现比这更彻底：**整条 WS 从来没通过**。

| # | 真问题 | 位置 |
|---|---|---|
| 1 | **vite 只代理了 `/api`，没代理 `/ws`** → 前端连 `ws://127.0.0.1:5188/ws/alarms` 直接失败，3 秒重连，无限循环 | `frontend/vite.config.js` |
| 2 | `alarms` 频道推的是**随机假告警**（每 15~30 秒凭空造一条「人员闯入危险区」，不写库） | `ws.py: _push_alarms_loop` |
| 3 | 真告警（`analysis.py` 写 `AlarmRecord`）**没有任何广播**；`push_alarm_event` / `push_alarm_sync` 定义了但从未被调用 | `analysis.py:153` |
| 4 | 告警页纯轮询 **30 秒** | `Alarms.vue: AUTO_REFRESH_MS` |

第 1 条是最隐蔽的：代码看起来"实时链路齐全"（有 ws.py、有 useWebSocket、有自动重连），
但因为没有代理，**所有 WS 功能一直是空转**——水质页的实时更新同样是假的，一直靠 30 秒轮询兜底。
"代码写了"和"跑通了"是两件事，这种坑只有端到端探针能抓出来。

### 17.2 改动

**后端**

- `ws.py` 重写：事件格式统一 `{event, ts, data}`；删掉假告警循环（`SUIEYE_DEMO_FEED=1` 才启，且标 `demo:true`）；
  新增 `emit()` / `publish_alarm()` / `publish_alarm_update()` / `publish_sync()`（同步端点用）
- `analysis.py`：写完 `AlarmRecord` 后广播（带真实 id，弹出来那条一定查得到）
- `alarms.py`：
  - 新增 `POST /api/alarms/ingest`（`X-API-Key` 鉴权 + `external_id` 去重）
  - `_do_handle` 处置后广播 `alarm.updated`（A 窗口处置，B 窗口立刻同步）
  - `_alarm_dict` 补 `source` / `origin`（列表、详情、分组都要带，否则界面上分不清谁报的）
- `webhooks.py` 新增：订阅 CRUD + 投递（HMAC-SHA256）+ 投递流水 + 测试按钮接口
- `database.py`：`AlarmRecord` 加 `source` / `origin`；新增 `webhook_subscriptions` / `webhook_deliveries`；`_ensure_columns()` 幂等迁移
- `config.py`：`ALARM_INGEST_KEY` / `WS_TOKEN` / `WEBHOOK_TIMEOUT` / `WEBHOOK_KEEP_DELIVERIES`

**前端**

- `vite.config.js` 补 `/ws` 代理（**`ws: true` 必填**）
- `Alarms.vue`：订阅 `alarms`，收到即静默刷新；处置中不打断，改为累积「N 条新告警」可点提示；页头显示"实时推送 / 重连中"
- `ws.js`：通知适配新事件格式，外部告警标 `[DCS]` 前缀；已在告警页则不弹
- `WaterMonitor.vue`：同步新快照格式（**格式改了不改这里 = 水质页实时静默失效，不报错**）
- `Settings.vue`：新增「告警对外接入」面板（入站 Key/curl、出站 WS 在线数 + Webhook 订阅管理）
- `Alarms.vue`：外部来源徽标（灰底，和"同帧 N 条"的主色徽标区分）

### 17.3 事件契约（对外也按这个）

```json
{ "event": "alarm.created", "ts": "…+08:00",
  "data": { "id": 460, "level": "danger", "rule_name": "…", "message": "…",
            "source": "external", "origin": "DCS", "created_at": "…" } }
```

完整对接文档见 **`docs/alarm-integration.md`**（入站字段表、验签代码、错误码、当前限制）。

### 17.4 验收（`qa_alarm_realtime.py`）

| 项 | 结果 |
|---|---|
| WS 连接 | `live-hint connected`，显示「实时推送」 |
| ingest → 页面出现 | **231ms**（多次跑 231~266ms） |
| **没刷新页面** | 注入 `window.__probeMark` 全程存活（这是"真推送"与"轮询撞上了"的分水岭） |
| 落库字段 | `source=external` / `origin=DCS` |
| Webhook | 收到 `alarm.created`，`X-SuiEye-Signature` 存在 |
| 鉴权 | 错误 Key → 401 |
| 回归 | `qa_ds` 11 页 ALL PASS |

### 17.5 三个坑

1. **`init_db()` 从来没被调用过**。项目当初靠一次性脚本建表，之后新增的表/列在老库上永远不出现，
   一查就 `no such table`。已在 `main.py` 的 startup 里调用（幂等）。
2. **vite 代理 WebSocket 必须写 `ws: true`**，只写 `/ws` 的 target 只会转发普通 HTTP，不升级协议。
3. **前端解析事件的地方必须跟着改**：`WaterMonitor` 里 `msg.type` → `msg.event`。
   不改不报错、不白屏，只是数字不再变 —— 这类"静默失效"最难查，改格式时一定要 grep 全站消费点。

### 17.6 遗留

- Webhook 无重试队列（失败即丢），要可靠投递请用 WS
- `_active_conns` 是进程内存，多副本部署需换 Redis Pub/Sub
- `WS_TOKEN` 默认空 = 不鉴权，生产必须配
- `equipment` / `safety` 两个 WS 端点保留但不再自己造数据（前端目前也没订阅）
- 设备健康分仍是每请求重算（老 P0），与本次改动无关



---

## 十八、飞书 / 企微 / 钉钉群机器人接入（2026-10-19）

老板：「可以接入飞书等软件」「也没有让我接飞书机器人啊」。
第二句是关键：功能做在「设置 → 页面最底部」，**等于没有入口**。

### 18.1 为什么不能"把飞书 URL 填进来就行"

原来的 Webhook 一律发 `{event, ts, data}`。对自建平台没问题，但群机器人只认自己那套：

| 平台 | body 必须是 |
|---|---|
| 飞书 | `{"msg_type":"interactive","card":{...}}` |
| 企业微信 | `{"msgtype":"markdown","markdown":{"content":"..."}}` |
| 钉钉 | `{"msgtype":"markdown","markdown":{"title":"...","text":"..."}}` |

填了地址但格式不对，第一条告警就是 400，界面上却写着"已订阅"。

### 18.2 更危险的：HTTP 200 不等于送达

三家失败都返回 **HTTP 200**，把失败写在 body 里：

- 飞书签名错 → `{"code":19021,"msg":"sign match fail"}`
- 企微地址失效 → `{"errcode":93000,...}`
- 钉钉关键词不匹配 → `{"errcode":310000,...}`

所以 `_deliver` 对群机器人必须再解析业务码，非 0 记失败。
**只判 HTTP 状态 = 把"被拒收"记成"投递成功"**，告警丢了界面还说好 —— 这是最坏的一种假。

### 18.3 签名算法三家不同（极易记混）

```python
# 飞书：key="ts\nsecret"，消息空
hmac.new(f"{ts}\n{secret}".encode(), b"", sha256)
# 钉钉：key=secret，消息="ts\nsecret"，毫秒 + urlencode，拼 URL
hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), sha256)
# 企微：不支持加签
```

写反不报错，只是 `sign match fail`。本项目的实现与验证见
`backend/services/chat_adapters.py` 与 `outputs/_qa/_test_chat_adapters.py`。

### 18.4 代码位置

| 文件 | 职责 |
|---|---|
| `backend/services/chat_adapters.py` | 四种 provider 的 body 组装 + 签名 + 业务码判定；PROVIDERS 是前端下拉的唯一真源 |
| `backend/api/routers/webhooks.py` | `_deliver` 走适配层；`provider` 字段；只有 generic 自动生成 secret |
| `models/database.py` | `webhook_subscriptions.provider`（默认 generic，老订阅行为不变） |
| `views/Alarms.vue` | 页头「接入飞书」按钮 → `/settings?focus=feishu` |
| `views/Settings.vue` | 平台下拉、加签密钥、`focus=feishu` 时滚到添加区 + 高亮 + 聚焦 |

### 18.5 两个必须记住的坑

1. **只有 generic 自动生成 secret**。群机器人的 secret 是"对方后台开的加签密钥"，
   我们编一个就等于强制加签 → 飞书直接 `sign match fail`。
2. **测试脚本必须自清理**。`_test_chat_adapters.py` 会用 ingest 打一条真告警，
   不删就会在告警列表里长期挂着「QA飞书真实链路17924…」，还参与统计。
   本轮就踩了：库里有 4 条测试告警，已删并给脚本加了自动清理。

### 18.6 验收

| 探针 | 结果 |
|---|---|
| `_test_chat_adapters.py` | **23/23**：四种 body 结构、三种签名、业务码失败判定、真告警推送 |
| `qa_feishu_entry.py` | **7/7**：按钮存在 → 跳转 → 入视口 → 高亮 → 平台已选飞书 → 地址框聚焦 |
| `qa_ds.py` | 11 页 ALL PASS |
| 库洁净 | 测试后回到 456 条告警 / 0 external / 0 订阅 |

---

## 十九、品牌区统一 BrandBlock（2026-10-19）

老板发了两张截图（门面页头 / 直播墙指挥条）：「这两处的系统图标都不一致，字体风格也不一致，设计也是」。

### 19.1 到底哪里不一致

两边各写了一套，而且四项全部不同：

| | 门面 `/` | 直播墙 `/livewall` |
|---|---|---|
| 图标 | `.fh-mark`：28px 圆角方块 + 斜纹渐变（纯 CSS div） | `BrandMark.vue`：34px 六边形 + 瞳孔（SVG） |
| 主标题 | 17px / **700**，且「SuiEye」单独 **19px / 800** | 21px / **600**，不拆西文 |
| 副标题 | 11px，另起一行 | 12.5px，与标题同行 + 左侧竖线 |
| 字体族 | 同一套（继承 `--font-family`） | 同一套 |

**「字体风格不一致」的真正来源是第 2 行**：同一句品牌名，西文 800 + 比中文大 2px，
看上去像换了字体；实际字体族从没变过。

### 19.2 收口方式

新建 `components/BrandBlock.vue`（图标 + 主标题 + 副标题），两处都换成它：

```vue
<BrandBlock />   <!-- 门面：页面内版式自适应 -->
<BrandBlock />   <!-- 直播墙：同上，不再有第二套 -->
```

规格只写一次：图标 32px、标题 `--text-xl`(18px)/600/0.2px、副标题 `--text-xs`(12px)/`--text-3`、
图标与文字 12px、标题与副标题 12px + 1px 竖线。

★ 标题**故意比 KPI 数值（21px/700）小**：一屏只有一个主角，品牌是标识不是数据。

### 19.3 版式按视口变，不按页面变

`≤1279px` 副标题落到下一行并去掉竖线（门面窄屏要同时装 4 格 KPI + CTA，横排会把 KPI 压溢出）。
**这是自适应，不是风格分叉**：字号 / 字重 / 图标一律不变。

禁止的写法：给不同页面传不同字号 / 不同 logo / 不同字重。要改规格，改组件。

### 19.4 连带清理

- 删除 `.fh-brand / .fh-mark / .fh-names / .fh-title / .fh-en / .fh-sub`（门面）
- 删除 `.brand / .brand-mark / .brand-text / .brand-text h1 / .brand-sub`（直播墙）
- 删除令牌 `--brand-mark-2`（只服务那个渐变方块，现已无引用）
- `qa_facade.py` 的 `brand/sub` 选择器从 `.fh-title/.fh-sub` 改为 `.bb-title/.bb-sub`，
  并新增两条守卫：品牌名非空、副标题必须是正式全称

### 19.5 验收 `qa_brand.py`（新）

不看截图，逐项比对两页 `getComputedStyle`：

| 项 | 门面 | 直播墙 |
|---|---|---|
| marker tag / viewBox / 路径 | `svg` / `0 0 32 32` / 六边形+椭圆+圆 | 完全相同 |
| 图标尺寸 / 颜色 | 32×32 / `rgb(22,93,255)` | 完全相同 |
| 标题 字号/字重/字距/颜色/字体族 | 18px / 600 / 0.2px / `rgb(29,33,41)` | 完全相同 |
| 副标题 字号/颜色 | 12px / `rgb(134,144,156)` | 完全相同 |
| 旧 class 残留 | 0 | 0 |

1440 / 1920 / 1024 三尺寸 **ALL PASS**；`qa_facade` 三尺寸 ALL PASS（卡高 113 不变）；
`qa_ds` 11 页 ALL PASS；`qa_nav_hub` 全绿。

★ 加「旧 class 残留 = 0」这条的原因：否则将来谁再手写一套 `.fh-mark`，
只验收"新的那套一致"是发现不了的 —— 分叉往往不是改坏，而是多长出一套。

---

## 二十、门面顶部二次改版：删掉卡底红条（2026-10-19）

老板截图（卡底那条浅红结论条）：「我想直接把这一条去掉，你觉得怎么样，这个页面作为我整个系统的门面，
一直摆着这个红条不太好看，然后这个页面最上面部分重新设计一下」。

### 20.1 判断：可以删，但要先说清代价

红条里的四项在页面上**都还有别的落点**，删掉不丢数据：

| 红条内容 | 删后在哪看 |
|---|---|
| 1 台设备故障 / 2 台预警 | 右上 HUD「运行工况 7 正常 · 2 预警 · 1 故障」 |
| 水质 2 处异常 | KPI「回用水质」格：橙色字 + 副行「2 处异常」 |
| 3 台需关注 | KPI「设备健康度」格：橙色字 + 副行「3 台需关注」 |

**唯一会真消失的是那句归并判断**（「有设备故障需处理」）。所以不是删干净，是把判断换成计数。

### 20.2 红条真正的问题不是信息，是语汇

警报横幅属于运维台。门面是展示层，每天挂一条红 = 每天都在报警，
访客对红色的敏感度会被磨掉；而且它通栏横在 3D 上方，把画面切成两段。

### 20.3 改了三处（老板拍板：方案 A + 胶囊）

| 项 | 改前 | 改后 |
|---|---|---|
| 卡底结论条 | 30px 通栏，浅红底 + 3px 竖条 + 4 段文字 | **整块删除**（DOM 一点不剩） |
| 异常入口 | 结论条本身 | CTA 左侧 **24px 胶囊**「3 项需关注」，有异常才渲染，点击进 `/alarms`，明细进 `title` |
| KPI 分栏 | 品牌 1 条 + 格间 3 条 = 4 条等重竖线 | **只留 1 条**（品牌与数据之间），格间改 26px 留白 |
| 卡高 | 113px | **83px**（1024 窄屏 136px，两行布局） |

`plantVerdict` 保留但瘦身：不再产出 `head/notes/more`，只给 `level`（决定胶囊颜色）、
`count`（决定是否渲染）、`detail`（4 项全进 title，一条不丢 —— 原结论条只显示前 3 条）。

### 20.4 一个必须承认的取舍

去掉格间竖线后，**1920 下四格之间会显得松**（每格 282px，内容只占 ~120px）。
竖线原本是视觉锚点，去掉后单靠留白撑不住这么宽。

1440（演示主尺寸）刚好，1024 反而更紧实。如果老板觉得 1920 太散，两个低成本修法：
① 给 `.fh-kpis` 加 `max-width`（如 1100px），多余宽度让给右侧留白；
② 数值字号 21 → 22（`--text-2xl`）+ label 11 → 12，把格内填满。
**本轮未做** —— 先看实际观感，不提前加复杂度。

### 20.5 探针改动（很关键，否则守卫会假通过）

| 探针 | 改动 |
|---|---|
| `qa_facade.py` | 删掉 `verdict.barW/box` 那套 3px 竖条断言 → 改为：`verdictGone`（旧 DOM 节点数必须 0）+ 胶囊 4 项（href、title 非空、高 ≤28、有状态色） |
| `qa_cta_route.py` | 新增胶囊点击：`点击胶囊落在 /alarms` + 告警页非白屏；无异常时打印「跳过」而不是 FAIL |

★ 为什么查 `verdictGone` 节点数而不只是"看不到"：如果只是用 CSS 隐藏，
下次谁改回 `display` 红条就复活了。**删除要连 DOM 一起删，验收也要连 DOM 一起验。**

### 20.6 验收

| 项 | 结果 |
|---|---|
| 卡高 | 1920/1440 = **83px**（原 113）、1024 = 136px |
| 旧红条残留 | **0** |
| 胶囊 | 「3 项需关注」/ 24px / `is-danger` / `/alarms` / title 含 3 项明细 |
| `qa_cta_route` | **13 项全绿**（CTA→/livewall、胶囊→/alarms 都验了点击） |
| `qa_facade` | 三尺寸 ALL PASS |
| `qa_ds` / `qa_brand` | 11 页 / 两页品牌 全 PASS |

---

## 二十一、告警详情「详略」重排：根因联动分析升为主角（2026-10-19）

老板看图后的原话：**「这一块要有详略，作为我的特色创新，根因联动分析应该着重，别的就要简略或者删去。」**
对象是告警中心的右侧证据面板（`views/Alarms.vue`）。

### 21.1 诊断：不是"不好看"，是四块内容等权平铺

改前从上到下是：证据图（190px 空盒子，图只占中间一小块）→ 规则命中（4 行标签）→
根因联动分析（候选默认**折叠**）→ 泡沫特征 TOP8（8 行条）→ 处置闭环。
实测：证据图 + 规则命中 ≈ 310px，泡沫 8 行 ≈ 210px，根因只有 ~180px 且**机理/建议要点击才出现**。
也就是说：全页最没信息量的一块最占地方，最有价值的一块被折叠着 —— 主次是反的。

### 21.2 改法：一条快照 + 一块 hero + 两处压缩

| 区块 | 改前 | 改后 | 高度 |
|---|---|---|---|
| 证据 + 规则命中 | 190px 图盒 + 4 行标签卡 | **触发快照一条**：112×84 缩略图（点击放大）+ 实测/阈值/偏离 + 时间/记录/模型；「规则命中」整块删除（DOM=0） | 310 → **104** |
| 根因联动分析 | 普通区块，候选全折叠 | **hero**：主色左边线 + 浅蓝底 + 白卡；头号候选**默认展开**机理/建议；三源做成一条带 | 180 → **312** |
| 泡沫表观偏离 | TOP8 = 8 行 + z 说明段 | **4 行** + 「全部」toggle（8 行）；"显著 / 基线 n"并入标题，z 口径进 `title` | 210 → **119** |
| 同帧其他告警 | 每条一行 | 一行 chips（可点跳转） | 依数据 |

★ **三源不画箭头**：原来是「磨矿粒度 → 配药方案 → 回水水质」，看起来像推理先后，
实际三个工段是**并列证据**。改成一条带子三格（格间 1px 分隔），每格给"最近样本距告警多久 + 窗口内/超窗降权"。
这属于数据诚实，不是样式偏好。

★ **头号候选默认展开**：根因的卖点就是"机理 + 建议措施"，藏在一次点击后面，
等于特色功能在默认视野里不存在。`loadRootCause()` 取到结果就把 `expandedRc` 设成 `candidates[0].code`。

★ **不重复同一句话**：`回水水质 超窗·降权 142.8 小时前` 已经在源格里写过一次，
`missing` 里的同一条（"超出建议窗口，已按时间衰减降权"）前端过滤掉，只保留真正"没数据"的缺口。

### 21.3 明确没动的两处

- **处置闭环**：它是操作区不是分析区，而且在首屏之下，不与根因争视觉；删了闭环这个卖点就没了。
- **泡沫特征**：没删。它是根因的旁证（过磨 → 气泡数量/大小异常），只是压到 4 行。
  真要删，得先确认老板演示时不讲"图-数互证"这条线。

### 21.4 新探针 `outputs/_qa/qa_alarm_hierarchy.py`

不测"像不像"，只测可量化的事实（8 项）：

| 断言 | 阈值 | 实测 |
|---|---|---|
| 快照条高度 | ≤120px | 104 |
| 旧「规则命中」DOM | =0（防复活） | 0 |
| 根因 ≥ 泡沫旁证×2 | 是 | 312 vs 119 |
| 根因 ≥ 快照×2 | 是 | 312 vs 104 |
| 头号候选默认展开 | 是 | 与 `candidates[0].title` 相等 |
| 机理/建议默认可见 | 文本含两段且 >60 字 | 169 字 |
| 三源格全在首屏 | =3 | 3 |
| 泡沫旁证行数 | ≤4 | 4 |

另验交互：点候选 #2 后**只有 #2 展开**；泡沫「全部」4→8 行、再点回 4 行。
`MULTI=1` 会翻页找 10-11 06:30 那条 **3 候选**样本（`event` 的 `@click` 已从 `.rc-line1` 移回整卡，
否则点证据文字不展开）。`SCENE=粒度分析` 用于验非浮选场景：不出现 hero，但必须有一句说明，不能静默消失。

★ 顺带修了旧探针 `cdp_alarms_rc.py`：`.rc-block` → `.rc-hero`，收尾 `terminate()` → CDP `Browser.close`。

### 21.5 验收

| 项 | 结果 |
|---|---|
| `qa_alarm_hierarchy` 1920 / 1440 / 1024 | **8 项全 PASS**（三尺寸） |
| 多候选样本（3 候选） | ALL PASS，候选切换 / 泡沫展开收起 PASS |
| 非浮选场景（粒度分析） | 无 hero + 有说明 + 面板不空 PASS |
| `qa_ds`（11 页一致性） | **ALL PASS** |
| 残留旧类名 | `.rule-hit / .rh-row / .img-box / .feat-note / .rc-block / .rc-join` 均为 0 |

## 二十二、页面可滚动（2026-10-25，修「各页不能下滑、展示不全」）

### 22.1 现象与真因

老板：「各页不能下滑，展示不全」。

真因不在各页，在外壳：`App.vue` 的 `.app-main` 写死 `overflow: hidden`，
而全站页面都是 `height: 100%` 的满屏布局。两者一叠加 →
**页面高度永远等于视口，超出的部分既显示不出来，也滚不到**。

实测（1366×768，`qa_fit.py`）：

| 页面 | 内容高 | 视口 | 结果 |
|---|---|---|---|
| 设置 | 1445 | 768 | 底部 677px 被裁 |
| 尾矿处理 | 1618 | 768 | 底部 850px 被裁 |
| 水质监测 | 1321 | 768 | 底部 553px 被裁 |
| 报表 | 1322 | 768 | 底部 554px 被裁 |
| 仪表盘 / 工作台 / 安全巡检 | 1229 / 1125 / 830 | 768 | 同样被裁 |

1920×1080 下「设置 / 报表 / 仪表盘」也超出（1446 / 1326 / 1229），即**大屏也不全**。

### 22.2 改法（三处，不动页面内容）

1. `App.vue` → `.app-main`：`overflow: hidden` 改 `overflow-y: auto; overflow-x: hidden`。
   主区成为全站**唯一**的滚动容器。装得下 → 无滚动条（大屏页仍是满屏）；
   装不下 → 整页纵向滚动。横向仍 hidden，不出现左右双滚动条。
2. `PageShell.vue` → `.page-shell`：`height: 100%` 改 `min-height: 100%`。
   内容短：盒子仍等于视口，`flex: 1` 主体照旧撑满（大屏观感不变）；
   内容长：盒子长到内容高度，底部 padding 生效（滚到底不是"贴边截断"）。
3. `Equipment3D.vue` → `.equipment-page`：`calc(100vh - 40px)` 改 `100%`。
   那 40px 是当年 `.app-main` 有 20px 内边距时的补偿，外壳内边距早已为 0，
   等于 3D 页底部白留一条灰边、画面少 40px 高。顺手修掉。

**连带必须做的一件**：主区变成滚动容器后，浏览器不再自动回顶。
`App.vue` 里加 `watch(route.fullPath)` → `scrollTop = 0`，
否则从「设置」滑到底再进「报表」，报表会以"已经滚到一半"的样子打开（比不能滚更迷惑）。

### 22.3 新探针 `outputs/_qa/qa_fit.py`

不看截图猜，直接读布局数字，逐页输出三项：

- `.app-main` 的 `overflow` / `scrollHeight` / `clientHeight`（能不能滚）
- `overflow: hidden` 且 `scrollHeight > clientHeight` 的元素（**真被裁**的内容，附选择器）
- 滚到底后的 `scrollTop`（证明底部真的够得着）

用法：`python qa_fit.py <cdp_port> <w> <h> [前端地址] [文件名后缀]`
（第 4 个参数可指向另一个实例，用于"空库/有数据"两种状态对照验收。）

### 22.4 验收

| 项 | 1920×1080 | 1366×768 | 1024×768 |
|---|---|---|---|
| 13 页「被裁元素」 | **0** | **0** | **0** |
| 横向溢出 | 0 | 0 | 0 |
| 超视口的页 | 3 页（设置/报表/仪表盘） | 8 页 | 10 页 |
| 超视口的页能否滚到底 | 能（scrollTop 366 / 262 / 1219） | 能 | 能 |
| 换页是否回顶 | `qa_scrollreset.py` PASS（1166 → 0） | 同 | 同 |

另用**有数据的库**（`suieyegets/suieye.db`，3046 条分析）另起 8301 + 5199 双实例复验，
避免只测到空态：真实数据下各页更长（尾矿 2273 / 仪表盘 2299 / 设置 1855），
同样「被裁 0、能滚到底」。仅剩两处 5px 级裁切，均为图片缩略图容器的等比裁边，非内容丢失。

### 22.5 空库已修（2026-10-25 当日补做）

**问题**：`作品及成果文件/suieye.db` 22 张表**全部 0 行** —— 后端首次启动时 `init_db()`
现建的空库；有数据的那份落在 `suieyegets/suieye.db`（17MB）。全站因此显示 0 / —。

**做法**（`outputs/_qa/restore_demo_data.py`，可重复执行）：

- 用 SQLite `ATTACH` 在事务里逐表搬运，**不覆盖文件、不重启后端**
  （8300 的 uvicorn 正握着这个文件，Windows 下覆盖必撞文件锁）。
- ★ 不用 `INSERT INTO t SELECT *`：两库表名列名相同，但 `alarm_records` 与
  `webhook_subscriptions` 的**列顺序**不同（旧库把 `handled_at` / `provider` 放末尾），
  `SELECT *` 会把值灌进错列。脚本按目标表列序显式列列名，只搬两边都有的列。
- 灌完 22 张表逐表核对行数：**75667 行全部 OK**，库 0.3MB → 16.6MB。
- 目标库先备份成 `suieye.db.bak_<时间>_pre_restore`（`*.db.bak_*` 已在 .gitignore 内）。
- 后端**无需重启**：SQLite 跨连接读已提交数据，`/api/scenes` 立刻返回 6 个场景、3046 条分析。

### 22.6 遗留（未动，需老板定）

1. `参赛打包文件.zip`（7.0GB / 解压 9.4GB，10-23 打包）**比源码旧**：
   既没有本次滚动修复，也**一份 db 都没有**（`outputs/_qa` 3.0GB、`data/_datasets` 4.5GB 倒是在里面）。
   要交这份 zip 就得重打，且建议先剔掉 `outputs/_qa*` 这类探针产物。
2. 1920×1080 下「设置 / 报表 / 仪表盘」仍需滚动 1.3~2 屏（内容确实多）。
   要"一屏看全"得压缩版式，属另一件事，本次只保证"看得全"。
3. 仪表盘「今日分析 / 今日告警」显示 0：库内数据截止 2026-10-10，按"今日"取数自然是 0
   （门面页走的是 `PLANT_DEMO_*` 演示口径，所以显示 5.23% / 5.38）。评委若在仪表盘停留会看到 0。
