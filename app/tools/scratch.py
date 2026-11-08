"""句柄协议：子 Agent 之间只传句柄，不传数据本身。

数据面与控制面分离：
- 控制面：主 Agent 的委派调用（delegate_sql -> delegate_viz）只传结论与句柄字符串；
- 数据面：结果集落在独立临时库（scratch.duckdb，读写），下游凭句柄取数。

安全边界（「读路径零信任，写路径白名单」）：
- 业务库连接保持只读不变，LLM 的 SQL 仍然是纯 SELECT，全量过 AST 护栏；
- 全系统唯一的写入口是 persist_result：由确定性代码把已校验的查询结果
  INSERT 进临时库，LLM 拿不到任何写 SQL 的通道；
- 句柄形如 h_<会话指纹>_<序号>，fetch 侧用正则 + 会话指纹双重校验，
  跨会话句柄一律拒绝（服务端判定，不靠 prompt 约束）。

生命周期：每次请求结束时由 main.py 调 discard_created() 清掉本次创建的
临时表（报告 HTML / Excel 在请求内已生成落盘，不依赖临时表存活）。
_handles 元数据表同时承担孤儿回收（超过 24h 的句柄在下次 persist 时顺带清除）。
"""

import re
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta

from ..config import settings

# 请求级会话上下文：main.py 在请求开始时 set(session_id)，finally 中 reset。
# 未 set 时（直调 core / 单测）退回固定 "adhoc"，同一进程内仍可自洽工作。
current_session: ContextVar[str | None] = ContextVar("xihuang_current_session", default=None)
# 本次请求创建的句柄（按创建顺序），finally 里精确清理，不误删并发请求的同会话表
_created_this_request: ContextVar[list | None] = ContextVar(
    "xihuang_created_handles", default=None)

_lock = threading.Lock()
_conn = None

_HANDLE_RE = re.compile(r"^h_([0-9a-f]{8})_([1-9][0-9]*)$")


def _session_tag() -> str:
    import hashlib
    sess = current_session.get() or "adhoc"
    return hashlib.sha256(sess.encode()).hexdigest()[:8]


def _scratch_conn():
    global _conn
    if _conn is None:
        settings.scratch_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = __import__("duckdb").connect(str(settings.scratch_path))
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS _handles "
            "(handle VARCHAR, session_tag VARCHAR, created_at TIMESTAMP)")
    return _conn


def _purge_expired(conn) -> None:
    """孤儿回收：元数据里超过 24h 的句柄连表一起删（不依赖请求 finally）。"""
    cutoff = datetime.now() - timedelta(hours=24)
    rows = conn.execute(
        "SELECT handle FROM _handles WHERE created_at < ?", [cutoff]).fetchall()
    for (h,) in rows:
        if _HANDLE_RE.match(h or ""):
            conn.execute(f'DROP TABLE IF EXISTS "{h}"')
    conn.execute("DELETE FROM _handles WHERE created_at < ?", [cutoff])


def persist_result_core(sql: str) -> dict:
    """把一条（已过护栏的）SELECT 结果落成临时表，返回句柄。

    写路径白名单：这里 INSERT 的是本函数查出来的数据，而不是 LLM 的 SQL ——
    LLM 的 SQL 先在只读业务库上过全套护栏并执行，任何写语句在护栏层就已拦截。
    """
    from ..db import query_typed
    from ..guardrails.checks import check_sql

    v = check_sql(sql)
    if not v["valid"]:
        return {"ok": False, "stage": "validate", "reason": v.get("reason", "校验未通过"),
                "suggestion": v.get("suggestion", ""), "checks": v.get("checks", [])}

    conn = _scratch_conn()
    tag = _session_tag()
    with _lock:
        _purge_expired(conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM _handles WHERE session_tag = ?", [tag]).fetchone()[0]
        if count >= settings.max_handles_per_session:
            return {"ok": False, "stage": "persist",
                    "reason": f"本会话临时表已达上限（{settings.max_handles_per_session}）",
                    "suggestion": "请在新会话中生成报告，或减少落表次数"}

        n = count + 1
        handle = f"h_{tag}_{n}"
        try:
            # 只读业务库执行（护栏已校验），拿列类型在临时库建同构 schema
            cols, rows, types = query_typed(v["sql_out"])
            col_defs = ", ".join(f'"{c}" {t}' for c, t in zip(cols, types))
            conn.execute(f'CREATE TABLE "{handle}" ({col_defs})')
            if rows:
                marks = ", ".join("?" for _ in cols)
                conn.executemany(f'INSERT INTO "{handle}" VALUES ({marks})',
                                 [tuple(r) for r in rows])
            conn.execute("INSERT INTO _handles VALUES (?, ?, ?)",
                         [handle, tag, datetime.now()])
        except Exception as e:
            conn.execute(f'DROP TABLE IF EXISTS "{handle}"')
            return {"ok": False, "stage": "persist",
                    "reason": f"落表失败：{e}", "suggestion": "请检查 SQL 后重试"}

    created = _created_this_request.get()
    if created is not None:
        created.append(handle)

    return {"ok": True, "handle": handle, "columns": list(cols),
            "row_count": len(rows),
            "note": "结果集已落临时表；请把 handle 原样传给下游（报告 Agent）"}


def fetch_handle_core(handle: str, limit: int = 1000) -> dict:
    """凭句柄取数。正则 + 会话指纹双重校验，跨会话句柄一律拒绝。"""
    m = _HANDLE_RE.match((handle or "").strip())
    if not m:
        return {"ok": False, "reason": "句柄格式不合法",
                "suggestion": "handle 只能来自 delegate_sql / persist_result 的返回值"}
    if m.group(1) != _session_tag():
        return {"ok": False, "reason": "句柄不属于当前会话，已拒绝",
                "suggestion": "请使用本次对话中 persist_result 返回的句柄"}
    conn = _scratch_conn()
    with _lock:
        exists = conn.execute(
            "SELECT COUNT(*) FROM _handles WHERE handle = ?", [handle]).fetchone()[0]
        if not exists:
            return {"ok": False, "reason": f"句柄 {handle} 不存在（可能已过期清理）",
                    "suggestion": "请重新执行 delegate_sql 生成新句柄"}
        cur = conn.execute(f'SELECT * FROM "{handle}" LIMIT {int(limit)}')
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    from ..db import rows_to_dicts
    return {"ok": True, "handle": handle, "columns": cols,
            "rows": rows_to_dicts(cols, rows), "row_count": len(rows)}


def discard_created() -> None:
    """请求结束清理：只删本次请求创建的表（并发安全，不碰其他请求的表）。"""
    created = _created_this_request.get()
    if not created:
        return
    conn = _scratch_conn()
    with _lock:
        for h in created:
            if _HANDLE_RE.match(h or ""):
                conn.execute(f'DROP TABLE IF EXISTS "{h}"')
        conn.executemany("DELETE FROM _handles WHERE handle = ?",
                         [(h,) for h in created])


# ===== 工具层（SQL 子 Agent 的第 7 个工具）=====

import asyncio
import json

from langchain_core.tools import tool


@tool
async def persist_result(sql: str) -> str:
    """把查询结果落成临时表并返回句柄（handle），供报告 Agent 凭句柄取数。
    仅当任务要求生成报告/导出/交给报告 Agent 加工时才调用。
    sql 会先过全套只读护栏（PII 拦截、口径校验、LIMIT），再由系统落表；
    返回 JSON：handle 为结果集句柄，row_count 为行数。结论中不要复述 handle，
    系统会自动把它带回给主 Agent。"""
    return json.dumps(await asyncio.to_thread(persist_result_core, sql), ensure_ascii=False)
