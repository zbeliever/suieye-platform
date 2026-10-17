"""告警表 v2 迁移：处置闭环 + 同源聚合

幂等脚本，可重复执行。

做四件事：
1. alarm_records 补齐 v2 列（status / group_key / handled_by / handled_note /
   action_taken / false_positive / silenced_until / silence_reason / suppressed_by）
2. 新建 alarm_actions 处置流水表
3. 回填 status：is_acknowledged=1 → 'acknowledged'，其余 → 'new'
4. 回填 group_key：从 evidence.record_id 生成 'frame:{rid}'（同源聚合键）

用法：
    python scripts/migrate_alarm_v2.py
"""

import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "suieye.db"

# 列名 → SQLite 类型定义
NEW_COLUMNS = {
    "status": "VARCHAR(20) DEFAULT 'new'",
    "group_key": "VARCHAR(80)",
    "handled_by": "VARCHAR(50)",
    "handled_at": "DATETIME",
    "handled_note": "TEXT",
    "action_taken": "TEXT",
    "false_positive": "BOOLEAN DEFAULT 0",
    "silenced_until": "DATETIME",
    "silence_reason": "VARCHAR(200)",
    "suppressed_by": "INTEGER",
}

ALARM_ACTIONS_DDL = """
CREATE TABLE IF NOT EXISTS alarm_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alarm_id INTEGER NOT NULL,
    action VARCHAR(30) NOT NULL,
    from_status VARCHAR(20),
    to_status VARCHAR(20),
    operator VARCHAR(50),
    note TEXT,
    created_at DATETIME
)
"""


def main() -> int:
    if not DB_PATH.exists():
        print(f"[ERR] 数据库不存在: {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    # ---- 1. 补列 ----
    existing = {r[1] for r in cur.execute("PRAGMA table_info(alarm_records)")}
    added = []
    for col, ddl in NEW_COLUMNS.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE alarm_records ADD COLUMN {col} {ddl}")
            added.append(col)
    print(f"[1/4] alarm_records 新增列: {added or '无（已是最新）'}")

    # ---- 2. 建流水表 ----
    cur.execute(ALARM_ACTIONS_DDL)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_alarm_actions_alarm_id ON alarm_actions(alarm_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_alarm_actions_created_at ON alarm_actions(created_at)")
    print("[2/4] alarm_actions 表就绪")

    # ---- 3. 回填 status ----
    n_ack = cur.execute(
        "UPDATE alarm_records SET status='acknowledged' "
        "WHERE is_acknowledged=1 AND (status IS NULL OR status='new')"
    ).rowcount
    n_new = cur.execute(
        "UPDATE alarm_records SET status='new' WHERE status IS NULL"
    ).rowcount
    print(f"[3/4] status 回填: 已确认 {n_ack} 条 → acknowledged, 其余 {n_new} 条 → new")

    # 3b. 历史确认的处置时间回填（用 acknowledged_at 兜底）
    n_hat = cur.execute(
        "UPDATE alarm_records SET handled_at=acknowledged_at "
        "WHERE handled_at IS NULL AND acknowledged_at IS NOT NULL"
    ).rowcount
    print(f"       handled_at 回填: {n_hat} 条")

    # ---- 4. 回填 group_key（同源聚合键）----
    rows = cur.execute(
        "SELECT id, evidence FROM alarm_records WHERE group_key IS NULL"
    ).fetchall()
    grouped = 0
    for aid, ev in rows:
        rid = None
        if ev:
            try:
                data = json.loads(ev) if isinstance(ev, str) else ev
                rid = data.get("record_id")
            except (ValueError, TypeError, AttributeError):
                rid = None
        if rid is not None:
            cur.execute(
                "UPDATE alarm_records SET group_key=? WHERE id=?", (f"frame:{rid}", aid)
            )
            grouped += 1
    print(f"[4/4] group_key 回填: {grouped} / {len(rows)} 条可归到帧")

    # 索引
    cur.execute("CREATE INDEX IF NOT EXISTS ix_alarm_records_status ON alarm_records(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_alarm_records_group_key ON alarm_records(group_key)")

    con.commit()

    # ---- 校验输出 ----
    print("\n== 迁移后统计 ==")
    for k, v in cur.execute(
        "SELECT status, COUNT(*) FROM alarm_records GROUP BY status"
    ).fetchall():
        print(f"   status={k}: {v}")
    print("   group_key 非空:", cur.execute(
        "SELECT COUNT(*) FROM alarm_records WHERE group_key IS NOT NULL").fetchone()[0])
    dup = cur.execute(
        "SELECT COUNT(*) FROM (SELECT group_key FROM alarm_records "
        "WHERE group_key IS NOT NULL GROUP BY group_key HAVING COUNT(*)>1)"
    ).fetchone()[0]
    print(f"   多告警同帧分组: {dup} 组")
    print("   alarm_actions 行数:", cur.execute("SELECT COUNT(*) FROM alarm_actions").fetchone()[0])

    con.close()
    print("\n[OK] 迁移完成（幂等，可重复执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
