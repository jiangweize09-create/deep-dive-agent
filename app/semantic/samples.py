"""维度值样本注入：把表里真实存在的高频取值喂给 LLM，杜绝字面量心算。

问题背景：LLM 写 SQL 时对文字维度只能靠猜——语义层声明了 region 维度，
但不知道库里存的是「华东」还是「华东大区」，猜错就查出 0 行还一本正经。
与 resolve_date_range（杜绝日期心算）同一哲学：能确定性给到的事实，
就不要让模型去猜。

机制：
- 对每张表的低基数文本列（distinct ≤ MAX_CARD 的 VARCHAR 系），
  按 COUNT(*) 取 TOP MAX_VALUES 个真实取值
- 内置表从业务库（只读连接）采样；上传表从其 parquet 直接采样
  （内存连接，不依赖主库是否已播种，任何时刻可重算）
- 结果落 data/samples.json；loader.get_catalog() 构建时覆盖列 sample，
  search_schema 返回给 LLM 的列即带真实取值——下游零改动

运行：python -m app.gen_samples（seed_data 末尾会自动跑一次）；
上传表确认入库时自动采样，删除上传表时自动清样。
"""

import json
import threading

from ..config import settings

SAMPLES_PATH = settings.root / "data" / "samples.json"

MAX_VALUES = 10     # 每列最多保留的高频取值数
MAX_CARD = 60       # distinct 超过此数视为自由文本，不采样（避免无意义列举）
MAX_LEN = 40        # 单个取值截断长度，防长文本撑爆 schema
_TEXT_TYPES = ("VARCHAR", "TEXT", "STRING")

_lock = threading.Lock()


def _is_text_col(ctype: str) -> bool:
    return str(ctype or "").upper().startswith(_TEXT_TYPES)


def _quote(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def sample_columns(conn, table: str, columns: list[dict],
                   max_values: int = MAX_VALUES, max_card: int = MAX_CARD) -> dict:
    """对一张表（conn 内可见，TEMP 视图亦可）的低基数文本列采样。

    columns 传 [{name, type, pii?}, ...]；返回 {列名: [取值...]}，只含有结果的列。
    单列失败（类型漂移/权限等）跳过，不拖垮整体。
    """
    out: dict[str, list] = {}
    for c in columns:
        name = str(c.get("name") or "").strip()
        if not name or c.get("pii") or not _is_text_col(c.get("type", "")):
            continue
        col, tbl = _quote(name), _quote(table)
        try:
            card = conn.execute(
                f"SELECT COUNT(DISTINCT {col}) FROM {tbl} WHERE {col} IS NOT NULL"
            ).fetchone()[0]
            if not card or card > max_card:
                continue
            rows = conn.execute(
                f"SELECT {col} FROM {tbl} WHERE {col} IS NOT NULL "
                f"GROUP BY 1 ORDER BY COUNT(*) DESC, 1 LIMIT {int(max_values)}"
            ).fetchall()
        except Exception:
            continue
        vals = [str(r[0])[:MAX_LEN] for r in rows if r[0] is not None]
        if vals:
            out[name] = vals
    return out


def load_samples() -> dict:
    """读 data/samples.json（损坏/缺失返回空 dict，采样是增强不是依赖）。"""
    try:
        data = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_samples(data: dict) -> None:
    SAMPLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAMPLES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _sample_parquet(name: str) -> dict:
    """对上传表的 parquet 直接采样（内存连接，不依赖主库连接状态）。"""
    from ..uploads import _parquet_path

    pq = _parquet_path(name)
    if not pq.exists():
        return {}
    import duckdb
    conn = duckdb.connect()
    try:
        esc = pq.as_posix().replace("'", "''")
        conn.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{esc}')")
        columns = [
            {"name": c[0], "type": c[1]}
            for c in conn.execute("DESCRIBE t").fetchall()
        ]
        return sample_columns(conn, "t", columns)
    finally:
        conn.close()


def generate_upload_samples(name: str) -> dict:
    """上传表确认入库时调用：采样并写 samples.json，返回 {列名: [取值]}。"""
    cols_map = _sample_parquet(name)
    with _lock:
        data = load_samples()
        if cols_map:
            data[name] = cols_map
        else:
            data.pop(name, None)
        _save_samples(data)
    return cols_map


def remove_table_samples(name: str) -> None:
    """删除上传表时清掉它的样本，避免 samples.json 留孤儿。"""
    with _lock:
        data = load_samples()
        if data.pop(name, None) is not None:
            _save_samples(data)


def rebuild_all() -> dict:
    """全量重建 samples.json：内置表（业务库）+ 上传表（parquet 直读）。

    返回 {表名: 采样列数}。主库未播种时内置表跳过（只采样上传表）。
    """
    import duckdb
    import yaml

    from ..uploads import _load_registry
    from .loader import SEMANTIC_DIR

    data = load_samples()
    result: dict[str, int] = {}

    # 上传表：parquet 直读，主库没播种也能跑
    for u in _load_registry():
        try:
            cols_map = _sample_parquet(u["name"])
        except Exception:
            continue
        if cols_map:
            data[u["name"]] = cols_map
        else:
            data.pop(u["name"], None)
        result[u["name"]] = len(cols_map)

    # 内置表：列声明来自 tables.yaml，取值来自业务库只读连接
    if settings.db_path.exists():
        with open(SEMANTIC_DIR / "tables.yaml", encoding="utf-8") as f:
            tables_raw = yaml.safe_load(f)["tables"]
        conn = duckdb.connect(str(settings.db_path), read_only=True)
        try:
            for t in tables_raw:
                columns = [
                    {"name": c["name"], "type": c.get("type", ""),
                     "pii": bool(c.get("pii", False))}
                    for c in t["columns"]
                ]
                cols_map = sample_columns(conn, t["name"], columns)
                if cols_map:
                    data[t["name"]] = cols_map
                else:
                    data.pop(t["name"], None)
                result[t["name"]] = len(cols_map)
        finally:
            conn.close()
    else:
        print("提示：业务库未播种（先跑 python -m app.seed_data），本次只采样上传表")

    with _lock:
        _save_samples(data)
    return result
