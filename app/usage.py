"""使用统计：每次问答的 token / 耗时 / 工具落库（data/stats.db）+ 聚合查询。

设计：
- 记录点在 chat() 的 done 事件处（live 与 demo 都会经过），一行一次问答
- tools 记 JSON 数组（该轮实际调用过的工具名），聚合时拍平排行
- 查询窗口默认近 14 天；聚合全部在 SQL/Python 确定性计算

撤销友好：本模块 + 设置页「使用统计」标签是独立增量，整体删除不影响其他功能。
"""

import json
import sqlite3
import threading
from datetime import datetime

from .config import settings

DB_PATH = settings.root / "data" / "stats.db"

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS usage_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        session_id TEXT,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        steps INTEGER DEFAULT 0,
        ms INTEGER DEFAULT 0,
        tools TEXT
    )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts)")
    conn.commit()


def record_usage(session_id: str | None, input_tokens: int, output_tokens: int,
                 total_tokens: int, steps: int, ms: int, tools: list[str]) -> None:
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            conn.execute(
                "INSERT INTO usage_log (ts, session_id, input_tokens, output_tokens, "
                "total_tokens, steps, ms, tools) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), session_id or "adhoc",
                 int(input_tokens or 0), int(output_tokens or 0), int(total_tokens or 0),
                 int(steps or 0), int(ms or 0),
                 json.dumps([t for t in (tools or []) if t], ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()


def summary(days: int = 14) -> dict:
    """近 N 天聚合：逐日趋势 + 总量 + 工具排行（全部确定性计算）。"""
    days = max(1, min(int(days), 90))
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            day_rows = conn.execute(
                "SELECT substr(ts, 1, 10) AS day, COUNT(*) AS n, "
                "COALESCE(SUM(total_tokens), 0) AS tok, "
                "COALESCE(AVG(ms), 0) AS ams "
                "FROM usage_log WHERE ts >= datetime('now', 'localtime', ?) "
                "GROUP BY day ORDER BY day ASC",
                (f"-{days} day",),
            ).fetchall()
            tot = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(total_tokens), 0), "
                "COALESCE(AVG(total_tokens), 0), COALESCE(AVG(ms), 0) "
                "FROM usage_log WHERE ts >= datetime('now', 'localtime', ?)",
                (f"-{days} day",),
            ).fetchone()
            tool_rows = conn.execute(
                "SELECT tools FROM usage_log WHERE ts >= datetime('now', 'localtime', ?)",
                (f"-{days} day",),
            ).fetchall()
        finally:
            conn.close()

    counter: dict[str, int] = {}
    for r in tool_rows:
        try:
            for name in json.loads(r["tools"] or "[]"):
                if name:
                    counter[name] = counter.get(name, 0) + 1
        except Exception:
            continue
    tools = sorted(counter.items(), key=lambda x: -x[1])[:10]

    return {
        "window_days": days,
        "days": [
            {"day": r["day"], "count": r["n"], "tokens": r["tok"], "avg_ms": round(r["ams"])}
            for r in day_rows
        ],
        "totals": {
            "count": tot[0], "tokens": tot[1],
            "avg_tokens": round(tot[2]), "avg_ms": round(tot[3]),
        },
        "tools": [{"name": n, "count": c} for n, c in tools],
    }
