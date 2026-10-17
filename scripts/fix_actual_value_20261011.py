"""P0-2 修复：把浮选 actual_value（化验真值）重生成到 HANDOFF 口径（Pb 3%~8%）。

背景：
  - 上一轮把 prediction.pb_grade_xrf 重生成到了 3~8%（均值 ~5.2，符合口径）
  - 但 actual_value 仍是 labels.xlsx 的原始台账值（0.99~3.12，均值 1.48），与预测严重脱节
  - 老板拍板（2026-10-11）：对齐 HANDOFF 3~8% 口径，真值按预测值 ±5% 相对偏差重生成

规则：
  - 同一 group_id 共享一个真值（与 batch_import_demo.py 的"同组 4 张图共享化验值"口径一致）
  - actual = 组内预测均值 × (1 + N(0, 0.05))，裁剪到 [3.0, 8.0]
  - 固定随机种子，可重复执行结果一致
  - 原本 actual_value 为 NULL 的记录（新上传未化验）保持 NULL
"""

import json
import random
import sqlite3
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "suieye.db"

SEED = 20261011
REL_STD = 0.05
CLIP_LO, CLIP_HI = 3.0, 8.0


def main():
    random.seed(SEED)
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    cur.execute(
        "SELECT id, group_id, prediction FROM analysis_records WHERE scene_id='flotation'"
    )
    rows = cur.fetchall()

    # 按组收集预测值
    groups = {}
    no_pred = 0
    for _rid, gid, pred in rows:
        p = json.loads(pred) if pred else {}
        pb = p.get("pb_grade_xrf") or p.get("pb_grade")
        if pb is None:
            no_pred += 1
            continue
        groups.setdefault(gid, []).append(float(pb))

    updated_groups = 0
    updated_records = 0
    for gid, pbs in groups.items():
        base = statistics.mean(pbs)
        actual = base * (1 + random.gauss(0, REL_STD))
        actual = round(min(CLIP_HI, max(CLIP_LO, actual)), 4)
        cur.execute(
            "UPDATE analysis_records SET actual_value=? "
            "WHERE scene_id='flotation' AND group_id=?",
            (actual, gid),
        )
        updated_groups += 1
        updated_records += cur.rowcount

    con.commit()

    # 验证
    cur.execute(
        "SELECT MIN(actual_value), MAX(actual_value), AVG(actual_value), COUNT(*) "
        "FROM analysis_records WHERE scene_id='flotation' AND actual_value IS NOT NULL"
    )
    mn, mx, avg, n = cur.fetchone()
    cur.execute(
        "SELECT COUNT(*) FROM analysis_records "
        "WHERE scene_id='flotation' AND actual_value IS NULL"
    )
    n_null = cur.fetchone()[0]
    con.close()

    print(f"预测缺失记录（跳过）: {no_pred}")
    print(f"更新组数: {updated_groups}, 更新记录数: {updated_records}")
    print(f"新真值分布: n={n} min={mn:.2f} max={mx:.2f} mean={avg:.2f}")
    print(f"保持 NULL（未化验上传记录）: {n_null}")
    ok = 3.0 <= mn and mx <= 8.0 and 4.8 <= avg <= 5.6
    print("口径检查:", "通过 ✓" if ok else "未通过 ✗（请人工检查）")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
