"""数据覆盖区间：每张表实际数据的时间范围，供 AI 需要时按需查询。

设计定位（按需工具，非常驻注入）：区间是「派生事实」的快照——预注入到
提示词里会每轮被重读（白烧 token），且在活库/模糊日期场景可能过时误导。
这里只提供即时计算能力，由 data_coverage 工具在 AI 需要时（如查询 0 行）
调用，调用那一刻现算，永远新鲜。

- 每张表独立：内置表与上传表（含年份完全不同的）各自计算，互不误判
- 日期列识别：语义层声明的 DATE/TIMESTAMP 列取第一个；没有日期列的表跳过
"""

from .loader import get_catalog

_DATE_TYPES = ("DATE", "TIMESTAMP")


def _quote(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _date_column(table) -> str | None:
    """该表第一个日期/时间类型的列名；没有则 None。"""
    for c in table.columns:
        if str(c.type or "").upper().startswith(_DATE_TYPES):
            return c.name
    return None


def coverage_rows() -> list[dict]:
    """逐表计算 {table, column, lo, hi}；空表/无日期列/查询失败的表跳过。"""
    from ..db import jsonify_value, query

    rows: list[dict] = []
    for t in get_catalog().tables.values():
        col = _date_column(t)
        if not col:
            continue
        try:
            lo, hi = query(
                f"SELECT MIN({_quote(col)}), MAX({_quote(col)}) FROM {_quote(t.name)}"
            )[1][0]
        except Exception:
            continue    # 表刚被删/连接异常：单表失败不拖垮整体
        if lo is None and hi is None:
            continue    # 空表
        rows.append({
            "table": t.name, "column": col,
            "lo": jsonify_value(lo), "hi": jsonify_value(hi),
        })
    return rows


def coverage_note() -> str:
    """把覆盖区间编译成紧凑文本（data_coverage 工具的返回体）。"""
    rows = coverage_rows()
    if not rows:
        return ""
    return "\n".join(f"- {r['table']}（{r['column']}）：{r['lo']} ~ {r['hi']}" for r in rows)
