"""数据库访问层。

骨架阶段用 DuckDB 做零配置演示；连接以只读打开，
对应护栏分层里的「连接层兜底：数据库层面禁止写入」。
生产切换点：把 query() 换成 psycopg + 只读账号即可，上层无感。
"""

import datetime as _dt
import decimal
import threading

import duckdb

from .config import settings

_lock = threading.Lock()
_conn: "duckdb.DuckDBPyConnection | None" = None


def get_conn() -> "duckdb.DuckDBPyConnection":
    global _conn
    if _conn is None:
        if not settings.db_path.exists():
            raise RuntimeError(
                f"数据库文件不存在（{settings.db_path}），请先运行: python -m app.seed_data"
            )
        _conn = duckdb.connect(str(settings.db_path), read_only=True)
        _mount_uploads(_conn)
    return _conn


def _mount_uploads(conn) -> None:
    """连接建立时把用户上传表挂成 TEMP 视图（见 app/uploads.py 模块注释）。"""
    try:
        from .uploads import register_views
        register_views(conn)
    except Exception:
        pass    # 上传模块异常不应阻塞主库启动；注册表已持久化，下次连接重试


def refresh_upload_views() -> None:
    """上传/删除表后重建 TEMP 视图（线程安全；上传确认流程调用）。"""
    with _lock:
        from .uploads import register_views
        register_views(get_conn())


def query(sql: str):
    """执行只读查询，返回 (columns, rows)。线程安全。"""
    conn = get_conn()
    with _lock:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    return cols, rows


def query_typed(sql: str):
    """同 query()，额外返回 DuckDB 列类型名（scratch 落表建 schema 用）。

    con.execute() 返回连接本身（无 .types）；列类型从 description[1] 取
    （duckdb 返回 Python 类型对象），str() 后即 DuckDB 类型名（如 DECIMAL(x,y)
    会退化成其 Python 基类，落表列类型略有放宽，不影响取数）。
    """
    conn = get_conn()
    with _lock:
        cur = conn.execute(sql)
        desc = cur.description or []
        cols = [d[0] for d in desc]
        rows = cur.fetchall()
        types = [getattr(d[1], "__name__", str(d[1])) if d[1] else "VARCHAR" for d in desc]
    return cols, rows, types


def db_ready() -> bool:
    try:
        get_conn()
        return True
    except Exception:
        return False


def jsonify_value(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return v


def rows_to_dicts(cols, rows) -> list[dict]:
    return [dict(zip(cols, [jsonify_value(v) for v in row])) for row in rows]
