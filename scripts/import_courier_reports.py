"""导入凡口 Courier 在线分析仪报表（2019-08 全月 2191 个 xls）→ courier_assays 表

数据源：2020csu浮选视频.zip 内 '2020csu浮选视频/report/II 20min report_*.xls'
（zip 中文件名以 cp437 存储，需按 gbk 还原中文名）。

每个报表是分析仪 1 小时周期的导出，含 9 个取样点 × Fe/Zn/Pb 的逐次测量明细。
相邻报表的周期有重叠 → 用内存 dict 按 (sample_time, sample_point, element) 去重，
入库再叠加 UNIQUE 约束 + on_conflict_do_nothing，整体幂等（可重复执行）。

用法:
    python scripts/import_courier_reports.py [zip路径]
"""
import io
import os
import re
import sys
import zipfile
from datetime import datetime

import pandas as pd
import warnings

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from backend.models.database import CourierAssay, Base, engine

DEFAULT_ZIP = r"D:/QQ/2020csu浮选视频.zip/2020csu浮选视频.zip"


def fix_name(n: str) -> str:
    """zip 内中文名还原（cp437 → gbk，失败再试 utf-8）"""
    try:
        return n.encode("cp437").decode("gbk")
    except Exception:
        try:
            return n.encode("cp437").decode("utf-8")
        except Exception:
            return n


def parse_report(fobj) -> list:
    """解析单个报表 → [(sample_time, sample_point, element, value), ...]"""
    df = pd.read_excel(io.BytesIO(fobj.read()), header=None)

    hdr = None
    for r in range(len(df)):
        if str(df.iloc[r, 0]).strip() == "Sample Line":
            hdr = r
            break
    if hdr is None:
        return []

    # 列映射：row hdr = 取样线（合并单元格，需向右继承），row hdr+1 = 元素
    cols = []
    line = var = None
    for c in range(2, df.shape[1]):
        v = df.iloc[hdr, c]
        if pd.notna(v):
            line = str(v).strip()
        v2 = df.iloc[hdr + 1, c]
        if pd.notna(v2):
            var = str(v2).strip()
        cols.append((c, line, var))

    recs = []
    for r in range(hdr + 2, len(df)):
        t = df.iloc[r, 0]
        if pd.isna(t):
            continue
        s = str(t)
        if not re.match(r"\d{4}-\d{2}-\d{2}", s):
            continue
        ts = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        for c, ln, vr in cols:
            if not ln or not vr:
                continue
            val = df.iloc[r, c]
            if pd.notna(val) and isinstance(val, (int, float)):
                recs.append((ts, ln, vr, float(val)))
    return recs


def main():
    zp = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ZIP
    if not os.path.exists(zp):
        print(f"ERROR: 找不到数据包 {zp}")
        sys.exit(2)

    # 幂等建表（已存在则跳过）
    Base.metadata.create_all(engine)

    z = zipfile.ZipFile(zp)
    xls = [i for i in z.infolist() if fix_name(i.filename).lower().endswith(".xls")]
    xls.sort(key=lambda i: fix_name(i.filename))
    print(f"发现报表 {len(xls)} 个，开始解析…")

    rows = {}          # (ts, point, element) -> value   内存去重（跨文件周期重叠）
    ok = fail = 0
    errors = []
    for idx, it in enumerate(xls):
        name = os.path.basename(fix_name(it.filename))
        try:
            for ts, ln, vr, v in parse_report(z.open(it)):
                rows[(ts, ln, vr)] = v
            ok += 1
        except Exception as e:
            fail += 1
            if len(errors) < 8:
                errors.append(f"{name}: {e}")
        if (idx + 1) % 300 == 0:
            print(f"  … {idx + 1}/{len(xls)} 文件，累计唯一记录 {len(rows)}")

    print(f"\n解析完成：成功 {ok} / 失败 {fail}")
    for e in errors:
        print("  !!", e)

    # 入库（分批 + on_conflict_do_nothing 幂等）
    inserted = 0
    items = sorted(rows.items())
    with engine.begin() as conn:
        for i in range(0, len(items), 5000):
            chunk = [
                {"sample_time": ts, "sample_point": ln, "element": vr, "value": v}
                for (ts, ln, vr), v in items[i:i + 5000]
            ]
            stmt = sqlite_insert(CourierAssay).values(chunk)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["sample_time", "sample_point", "element"]
            )
            res = conn.execute(stmt)
            inserted += res.rowcount or 0

    print(f"入库：新插入 {inserted} 条（库内总数以查询为准）")

    # 统计
    from sqlalchemy import func, select
    with engine.connect() as conn:
        total = conn.execute(select(func.count(CourierAssay.id))).scalar()
        lo, hi = conn.execute(select(func.min(CourierAssay.sample_time),
                                      func.max(CourierAssay.sample_time))).one()
        print(f"\ncourier_assays 总数 {total}，时间范围 {lo} ~ {hi}")
        pts = conn.execute(
            select(CourierAssay.sample_point, CourierAssay.element,
                   func.count(CourierAssay.id),
                   func.min(CourierAssay.value), func.max(CourierAssay.value))
            .group_by(CourierAssay.sample_point, CourierAssay.element)
            .order_by(CourierAssay.sample_point, CourierAssay.element)
        ).all()
        print(f"{'取样点':14s} {'元素':4s} {'点数':>7s} {'最小':>9s} {'最大':>9s}")
        for p, e, c, mn, mx in pts:
            print(f"{p:14s} {e:4s} {c:7d} {mn:9.3f} {mx:9.3f}")


if __name__ == "__main__":
    main()
