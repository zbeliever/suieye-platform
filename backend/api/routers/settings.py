"""系统设置 API —— 让设置页的配置**真的生效**。

背景：设置页原来是"演示模式"—— 阈值是编的（0.8/2.5），保存只弹个提示，刷新即还原，
页面自己还写着"仅作展示用途"。本模块把它接到真实的配置源上：

    配置真源 = 场景包 YAML（`backend/scenes/{scene}/{scene}.yaml`）
      · alarm_rules        告警阈值/级别 —— 由 `backend/engines/alarm.py` 通用规则引擎消费
      · pipeline.filters   过滤器链（只读展示）
      · input.sources      数据源 upload / rtsp / 定时抓拍（只读展示）
      · prediction_targets 预测目标与量程（只读展示）

★★ 两条必须守住的规则（否则"改了像没改"）：
  1. 写回 YAML 后**必须清 runtime 的配置缓存** —— load_scene_config() 是模块级缓存，
     不清的话 run_analysis() 仍用旧规则。统一走 runtime.reload_scene_config()。
  2. YAML 用**文本级替换**（只改 threshold 那几行），不用 yaml.dump 重写整个文件 ——
     那会把文件里的设计注释（如"CRF 边界精修已回退停用"的来龙去脉）全部抹掉。

只允许修改**已存在规则**的 threshold（不新增/删除规则）：规则增删属于改管线，
应当直接编辑 YAML 并走代码评审，不适合在网页上一键完成。
"""

import re
import time
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import settings as app_settings
from backend.engines.alarm import FIELD_LABELS, OP_LABELS
from backend.models.database import ModelVersion, get_db
from backend.scenes import runtime
from backend.scenes.runtime import SCENES_DIR, list_available_scenes, load_scene_config

# 路径写全（本项目约定：router 不设 prefix，全路径写在装饰器里，与其他 11 个 router 一致）
router = APIRouter(tags=["settings"])

# 模块加载 ≈ 后端启动（main.py 会 import 本模块），用于展示运行时长
_BOOT_TS = time.time()

# 阈值允许范围（宽松护栏，防止误填出离谱数字；精确量程看各场景 prediction_targets）
_THRESHOLD_MIN, _THRESHOLD_MAX = 0.0, 10000.0


# ── 系统信息 ──────────────────────────────────────────────────────────


def _db_path() -> Path:
    """从 DATABASE_URL（sqlite:///./suieye.db）解析出真实文件路径。"""
    url = app_settings.DATABASE_URL
    rel = url.split("sqlite:///")[-1] if "sqlite:///" in url else url
    p = Path(rel)
    if not p.is_absolute():
        # 相对路径是相对项目根（uvicorn 的工作目录），不是 backend/
        p = (Path(__file__).resolve().parents[3] / p).resolve()
    return p


def _system_info(db: Session) -> dict:
    dbp = _db_path()
    db_size = dbp.stat().st_size if dbp.exists() else 0

    tables = []
    try:
        names = [
            r[0]
            for r in db.execute(
                text("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")
            )
        ]
        for n in names:
            try:
                cnt = db.execute(text(f'select count(*) from "{n}"')).scalar() or 0
            except Exception:
                cnt = None
            tables.append({"name": n, "rows": cnt})
    except Exception:
        pass

    data_dir = Path(__file__).resolve().parents[3] / app_settings.DATA_DIR
    data_size = 0
    if data_dir.exists():
        for f in data_dir.rglob("*"):
            try:
                if f.is_file():
                    data_size += f.stat().st_size
            except Exception:
                continue

    return {
        "app": app_settings.APP_NAME,
        "version": app_settings.APP_VERSION,
        "debug": app_settings.DEBUG,
        "db_path": str(dbp),
        "db_size_mb": round(db_size / 1024 / 1024, 2),
        "data_dir": str(data_dir),
        "data_dir_mb": round(data_size / 1024 / 1024, 1),
        "table_count": len(tables),
        "tables": tables,
        "boot_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_BOOT_TS)),
        "uptime_sec": int(time.time() - _BOOT_TS),
    }


# ── 场景配置（读 YAML） ────────────────────────────────────────────────


def _scene_snapshot(name: str) -> dict:
    cfg = load_scene_config(name)
    rules = []
    for r in (cfg.get("alarm_rules") or []):
        fld = r.get("field")
        cond = r.get("condition")
        rules.append({
            **r,
            # 中文名取自规则引擎的映射表（与告警文案同源）—— 前端不再自建一套，
            # 否则两套映射迟早漂移，页面上会出现"铅品位"和"Pb品位"两种说法。
            # 映射表没有的字段退回**规则名**（safety 的规则名本身就是中文短语，比英文路径可读）。
            "field_label": FIELD_LABELS.get(fld) or r.get("name") or fld,
            "op_label": OP_LABELS.get(cond, cond),
            "level_label": "危险" if r.get("level") == "danger" else "警告",
        })
    return {
        "name": name,
        "display_name": cfg.get("display_name") or name,
        "version": cfg.get("version"),
        "sources": (cfg.get("input") or {}).get("sources") or [],
        "pipeline": [
            {"name": f.get("name"), "params": f.get("params") or {}}
            for f in ((cfg.get("pipeline") or {}).get("filters") or [])
        ],
        "targets": cfg.get("prediction_targets") or [],
        "alarm_rules": rules,
    }


# ── 模型列表 ──────────────────────────────────────────────────────────


def _models(db: Session) -> list:
    rows = db.query(ModelVersion).order_by(ModelVersion.scene_id, ModelVersion.id).all()
    out = []
    for m in rows:
        out.append({
            "id": m.id,
            "scene_id": m.scene_id,
            "version": m.version,
            "algorithm": m.algorithm,
            "file_path": m.file_path,
            "metrics": m.metrics,
            "training_data_count": m.training_data_count,
            "trained_at": m.trained_at.isoformat() if m.trained_at else None,
            "is_active": bool(m.is_active),
            "total_inferences": m.total_inferences,
            "last_health_check": m.last_health_check.isoformat() if m.last_health_check else None,
            "health_status": m.health_status,
        })
    return out


@router.get("/api/settings/overview")
def settings_overview(db: Session = Depends(get_db)):
    """设置页一次性拉全：系统信息 + 各场景配置 + 模型列表。

    场景配置只覆盖**有场景包 YAML 的**场景（flotation/grinding/safety）；
    water/tailings/equipment 走专用仿真链路、没有 pipeline/alarm_rules 配置，
    不能为了"好看"给它们编一份。
    """
    scenes = []
    for s in list_available_scenes():
        try:
            scenes.append(_scene_snapshot(s))
        except Exception as e:  # 单个场景坏了不拖垮整页
            scenes.append({"name": s, "error": str(e)})
    return {
        "system": _system_info(db),
        "scenes": scenes,
        "models": _models(db),
    }


# ── 改告警阈值（写 YAML + 清缓存） ────────────────────────────────────


class RulePatch(BaseModel):
    name: str
    threshold: float = Field(..., ge=_THRESHOLD_MIN, le=_THRESHOLD_MAX)


class AlarmRulesPatch(BaseModel):
    rules: list[RulePatch]


_RULE_NAME_RE = re.compile(r"^\s*-\s*name:\s*(.+?)\s*$")
_THRESHOLD_RE = re.compile(r"^(\s*threshold:\s*)(\S+?)(\s*)(#.*)?$")
_TOP_KEY_RE = re.compile(r"^[A-Za-z_]")


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _fmt_num(v: float, template: str = "") -> str:
    """按原行的写法输出数字，避免制造无意义的格式 diff。

    实测踩过：`threshold: 4.0` 被写成 `threshold: 4` —— 数值等价，但污染了文件风格，
    也让 git 里出现一行"什么都没改"的改动。规则：
      · 原值带小数点（4.0 / 0.0）→ 新值也保留浮点形态（1.5 → "1.5"，4.0 → "4.0"）
      · 原值是整数（0 / 50 / 200）→ 整值输出整数，非整值再退回浮点
    """
    f = float(v)
    if f == int(f):
        wants_float = ("." in template) or ("e" in template.lower())
        return f"{f:.1f}" if wants_float else str(int(f))
    return f"{f:g}"


def _rewrite_thresholds(yaml_path: Path, updates: dict) -> None:
    """按行扫描，只替换 `alarm_rules:` 段内目标规则的 threshold 值。

    ★ 为什么不用 yaml.safe_dump 重写：那会抹掉文件里的设计注释
      （例如"CRF 边界精修已回退停用"的来龙去脉、各字段的说明），
      而这些注释是场景包的一部分。文本级替换精确到行，其余内容一字不动。
    """
    text = yaml_path.read_text(encoding="utf-8")
    out = []
    in_alarm = False
    cur = None
    hit = set()

    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        eol = line[len(bare):] or "\n"

        if re.match(r"^alarm_rules:\s*$", bare):
            in_alarm, cur = True, None
            out.append(line)
            continue
        # 离开 alarm_rules 段（遇到下一个顶层键）
        if in_alarm and _TOP_KEY_RE.match(bare) and not bare[:1].isspace():
            in_alarm, cur = False, None

        if in_alarm:
            m = _RULE_NAME_RE.match(bare)
            if m:
                cur = _unquote(m.group(1))
                out.append(line)
                continue
            m2 = _THRESHOLD_RE.match(bare)
            if m2 and cur in updates:
                out.append(
                    f"{m2.group(1)}{_fmt_num(updates[cur], m2.group(2))}{m2.group(3)}{m2.group(4) or ''}{eol}"
                )
                hit.add(cur)
                continue
        out.append(line)

    missing = set(updates) - hit
    if missing:
        raise HTTPException(
            500, f"未能定位规则的 threshold：{sorted(missing)}（YAML 结构可能被改动过）"
        )
    yaml_path.write_text("".join(out), encoding="utf-8")


@router.put("/api/settings/scenes/{scene}/alarm-rules")
def update_alarm_rules(scene: str, patch: AlarmRulesPatch, db: Session = Depends(get_db)):
    """更新某场景的告警阈值：写回 YAML → 自校验 → 清缓存（下一次分析立即生效）。"""
    yaml_path = SCENES_DIR / scene / f"{scene}.yaml"
    if not yaml_path.exists():
        raise HTTPException(404, f"场景包不存在: {scene}（有 YAML 配置的场景：{list_available_scenes()}）")
    if not patch.rules:
        raise HTTPException(400, "rules 不能为空")

    cfg = load_scene_config(scene)
    known = {r.get("name"): r for r in (cfg.get("alarm_rules") or [])}
    if not known:
        raise HTTPException(400, f"场景 {scene} 没有 alarm_rules，无处可改")

    # 只允许改已存在的规则（新增/删除规则请直接改 YAML）
    unknown = [r.name for r in patch.rules if r.name not in known]
    if unknown:
        raise HTTPException(400, f"规则不存在：{unknown}；可改的是 {list(known)}")

    # 同一规则传两次以最后一条为准（去重，避免重复改同一行）
    updates: dict = {}
    for r in patch.rules:
        updates[r.name] = float(r.threshold)

    before = {n: known[n].get("threshold") for n in updates}
    _rewrite_thresholds(yaml_path, updates)

    # 自校验：重新解析一遍，确认阈值真写进去了（写坏了就报错，不留半截状态）
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            reparsed = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(500, f"写回后 YAML 解析失败：{e}")
    after = {r.get("name"): r.get("threshold") for r in (reparsed.get("alarm_rules") or [])}
    bad = {n: after.get(n) for n, v in updates.items() if abs(float(after.get(n, -1)) - v) > 1e-9}
    if bad:
        raise HTTPException(500, f"写回校验失败：{bad}")

    # ★ 清缓存 —— 不清则 run_analysis() 仍用旧阈值
    new_cfg = runtime.reload_scene_config(scene)

    changed = [
        {"name": n, "before": before[n], "after": updates[n]}
        for n in updates if float(before[n] or 0) != float(updates[n])
    ]
    return {
        "ok": True,
        "scene": scene,
        "changed": changed,
        "alarm_rules": new_cfg.get("alarm_rules") or [],
        "cache_reloaded": True,
    }
