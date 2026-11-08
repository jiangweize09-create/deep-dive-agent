"""Excel 导出：句柄结果集 -> xlsx（openpyxl，纯 Python 无系统依赖）。

文件落盘到 data/reports/<report_id>.xlsx，通过 /api/report/<id>/excel 下载。
"""

import datetime as _dt
import re
from pathlib import Path

from ..config import settings
from ..tools.scratch import fetch_handle_core
from . import store


def _cell(v):
    if isinstance(v, _dt.datetime):
        return v
    if isinstance(v, _dt.date):
        return v
    if hasattr(v, "as_tuple"):   # decimal.Decimal
        return float(v)
    return v


def export_excel_core(handle: str, filename: str = "") -> dict:
    d = fetch_handle_core(handle)
    if not d["ok"]:
        return d
    from openpyxl import Workbook

    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w-]", "", filename or "")[:40] or "data"
    # 文件名带报告 id：多报告不互相覆盖，/api/report/<id>/excel 按前缀定位
    rep = store.current_report.get()
    name = f"{rep['id']}_{safe}.xlsx" if rep and rep.get("id") else f"{safe}.xlsx"
    path = Path(settings.reports_dir) / name

    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(list(d["columns"]))
    for r in d["rows"]:
        ws.append([_cell(r.get(c)) for c in d["columns"]])
    # 简单列宽：按表头与前几行的字符串长度估
    for i, c in enumerate(d["columns"], 1):
        width = max([len(str(c))] + [len(str(r.get(c, ""))) for r in d["rows"][:20]])
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(width + 4, 40)
    wb.save(path)

    return {"ok": True, "file": path.name, "row_count": d["row_count"],
            "columns": d["columns"]}
