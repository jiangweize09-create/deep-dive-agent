"""工作空间：用户产生的三类资产。

- 我上传的表：data/uploads/*.parquet + registry.json（已挂成 TEMP VIEW，Agent 可查）
- 我的文档：data/corpus_user/*.md（已进 RAG 索引，检索可命中）
- 历史报告：data/reports/*.html（单文件，可离线打开）

这里只做「列出来」，不重复 uploads / report 的写入逻辑。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings

_TITLE_RE = re.compile(r"<h1>(.*?)</h1>", re.S)
_TS_RE = re.compile(r"_(\d{14})\.md$")


def _unescape(s: str) -> str:
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&#39;", "'"))


def _kb(path: Path) -> float:
    try:
        return round(path.stat().st_size / 1024, 1)
    except OSError:
        return 0.0


def _mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


def _tables() -> list[dict[str, Any]]:
    from .uploads import list_uploads

    out = []
    for t in list_uploads().get("tables", []):
        cols = t.get("columns") or []
        out.append({
            "name": t.get("name", ""),
            "display": t.get("display") or t.get("name", ""),
            "filename": t.get("filename", ""),
            "rows": t.get("rows"),
            "column_count": len(cols),
            "pii_columns": [c.get("name") for c in cols if c.get("pii")],
            "uploaded_at": t.get("uploaded_at", ""),
        })
    out.sort(key=lambda x: x.get("uploaded_at") or "", reverse=True)
    return out


def _docs() -> list[dict[str, Any]]:
    from .uploads import USER_CORPUS_DIR

    if not USER_CORPUS_DIR.exists():
        return []
    out = []
    for p in USER_CORPUS_DIR.glob("*.md"):
        m = _TS_RE.search(p.name)
        stamp = ""
        if m:
            try:
                stamp = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
            except ValueError:
                stamp = ""
        out.append({
            "name": p.name,
            "size_kb": _kb(p),
            "uploaded_at": stamp or _mtime(p),
        })
    out.sort(key=lambda x: x["uploaded_at"], reverse=True)
    return out


def _reports() -> list[dict[str, Any]]:
    d = settings.reports_dir
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("rep_*.html"), key=lambda x: x.stat().st_mtime, reverse=True):
        rid = p.stem
        title = rid
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:4000]
            m = _TITLE_RE.search(head)
            if m:
                title = _unescape(m.group(1)).strip() or rid
        except OSError:
            pass
        out.append({
            "id": rid,
            "title": title,
            "created": _mtime(p),
            "size_kb": _kb(p),
            "url": f"/api/report/{rid}",
        })
    return out


def build_workspace() -> dict[str, Any]:
    tables, docs, reports = _tables(), _docs(), _reports()
    return {
        "tables": tables,
        "docs": docs,
        "reports": reports,
        "totals": {
            "tables": len(tables),
            "docs": len(docs),
            "reports": len(reports),
            "rows": sum(t.get("rows") or 0 for t in tables),
        },
    }
