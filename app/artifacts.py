"""产物资产库：把每次分析产出的报告/文件登记成持久记录（SQLite）。

现状痛点：报告 dict 存在请求级 ContextVar（report/store.py），请求结束即丢；
HTML / Excel 文件虽落盘 data/reports/ 可继续访问，但「哪个会话产出了哪份
报告」服务端没有记录——报告入口只活在聊天文本里。

这里一行 SQLite 记录 = 一份用户可打开/下载的交付物：
- kind=report：url（单文件 HTML 报告）+ excel_url（结果集 Excel）
- 与 session_id（前端会话 id，即 thread_id）绑定，GET /api/artifacts 可回查
- delegate_viz 收尾时登记；SSE 下发 artifact 事件，前端渲染结构化产物卡

生产切换点：换对象存储 + 元数据落 MySQL 时，只需替换本模块实现，
register/list 两个签名不变。
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime

from .config import settings

DB_PATH = settings.root / "data" / "artifacts.db"

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT,
        url TEXT,
        excel_url TEXT,
        meta TEXT,
        created_at TEXT
    )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id)")
    conn.commit()


def register_artifact(session_id: str | None, kind: str, title: str,
                      url: str = "", excel_url: str = "",
                      meta: dict | None = None) -> dict:
    """登记一份交付物，返回含 id 的完整记录（即 SSE artifact 事件的载荷）。"""
    art = {
        "id": f"art_{uuid.uuid4().hex[:10]}",
        "session_id": session_id or "adhoc",
        "kind": kind,
        "title": (title or "未命名产物")[:120],
        "url": url or "",
        "excel_url": excel_url or "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            conn.execute(
                "INSERT INTO artifacts (id, session_id, kind, title, url, excel_url, meta, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (art["id"], art["session_id"], art["kind"], art["title"],
                 art["url"], art["excel_url"],
                 json.dumps(meta or {}, ensure_ascii=False), art["created_at"]),
            )
            conn.commit()
        finally:
            conn.close()
    return {**art, "meta": meta or {}}


def delete_by_report(rid: str) -> int:
    """删除报告文件时同步清理产物登记（url / excel_url 命中该报告的行）。"""
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            cur = conn.execute(
                "DELETE FROM artifacts WHERE url = ? OR url = ? OR excel_url = ?",
                (f"/api/report/{rid}", f"/api/report/{rid}", f"/api/report/{rid}/excel"),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def list_artifacts(session_id: str | None = None, limit: int = 200) -> list[dict]:
    """产物清单：传 session_id 查单会话，None 查全部（工作空间页用）。"""
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE session_id = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM artifacts ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        finally:
            conn.close()
    out = []
    for r in rows:
        try:
            meta = json.loads(r["meta"] or "{}")
        except Exception:
            meta = {}
        out.append({
            "id": r["id"], "session_id": r["session_id"], "kind": r["kind"],
            "title": r["title"], "url": r["url"], "excel_url": r["excel_url"],
            "meta": meta, "created_at": r["created_at"],
        })
    return out
