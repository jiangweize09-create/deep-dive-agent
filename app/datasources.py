"""数据接入：已接入的数据资产清单。

两级能力，分级诚实：
1. 内置 DuckDB（只读）—— 完全可用，Agent 能查
2. 上传的 CSV/Excel（parquet + TEMP VIEW）—— 完全可用，Agent 能查
"""

from __future__ import annotations

from typing import Any


def list_sources() -> dict[str, Any]:
    """已接入的数据资产：内置库（可按行数排序）+ 上传表。"""
    from .db import db_ready, query
    from .uploads import list_uploads

    builtin: list[dict[str, Any]] = []
    if db_ready():
        try:
            from .semantic.loader import get_catalog

            cat = get_catalog()
            _, tabs = query(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY 1"
            )
            for (name,) in tabs:
                try:
                    _, c = query(f'SELECT COUNT(*) FROM "{name}"')
                    rows = int(c[0][0])
                except Exception:
                    rows = None
                # 语义层里每张表都有中文名与一句话说明——这里透传给前端说人话
                spec = cat.tables.get(name)
                builtin.append({
                    "name": name,
                    "rows": rows,
                    "display": spec.display if spec else name,
                    "description": spec.description if spec else "",
                })
        except Exception:
            pass

    ups = list_uploads()
    upload_tables = [
        {
            "name": t.get("name", ""),
            "display": t.get("display") or t.get("name", ""),
            "filename": t.get("filename", ""),
            "rows": t.get("rows"),
            "column_count": len(t.get("columns") or []),
            "uploaded_at": t.get("uploaded_at", ""),
        }
        for t in ups.get("tables", [])
    ]
    upload_tables.sort(key=lambda x: x.get("uploaded_at") or "", reverse=True)

    return {
        "builtin": {"engine": "DuckDB", "readonly": True, "tables": builtin},
        "uploads": upload_tables,
        "docs": ups.get("docs", []),
    }
