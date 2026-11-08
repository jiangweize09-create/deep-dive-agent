"""数据质量体检工具：分析前先验货，结论里能带上可信度依据。

动机：数据本身有坑（大量空值、异常大额、近似唯一列）时，分析结论会歪，
而 LLM 默认不吭声。profile_table 用纯 SQL 统计给出结构化的质量画像，
LLM 据此决定口径、在结论中注明可信度——质量判断在代码里，不在 prompt 里。

与护栏的关系：只读、不引入新攻击面——
- 表名必须命中语义层白名单（未知表直接拒绝，逼 LLM 先走 search_schema）
- PII 列完全不进入画像（连空值率都不统计，比护栏的「禁止查询」更保守）
- 全部走只读连接，SELECT 统计函数，无任何写路径
"""

import asyncio
import json

from langchain_core.tools import tool

# 语义层类型名 → 数值列（可算 min/p50/p95/异常）
_NUMERIC_TYPES = ("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT",
                  "INT", "INT32", "INT64", "DOUBLE", "FLOAT", "REAL", "DECIMAL")
_MAX_PROFILE_COLS = 20      # 单表最多画像列数（防超宽表刷屏）
_MISSING_WARN = 0.2         # 缺失率告警阈值（与 insights.py 的缺失规则一致）
_OUTLIER_Z = 3.0            # 表级异常值阈值（结果集级 insights 用 |z|>=2，这里更保守）


def _quote(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _is_numeric(ctype: str) -> bool:
    return str(ctype or "").upper().startswith(_NUMERIC_TYPES)


def profile_table_core(table: str) -> dict:
    """对一张白名单表做质量画像：行数、各列空值率/基数、数值列分布与异常行。

    返回 {ok, table, rows, columns:[...], notes:[...]}，notes 为确定性生成的
    质量提示（缺失率/异常值/近似唯一列），LLM 只做转述不允许发明数字。
    """
    name = (table or "").strip()
    from ..semantic.loader import get_catalog

    spec = get_catalog().tables.get(name)
    if spec is None:
        return {"ok": False,
                "reason": f"未知表 {name}（不在语义层白名单）",
                "suggestion": "先用 search_schema 查可用表，再对命中的表做体检"}

    cols = [c for c in spec.columns if not c.pii][: _MAX_PROFILE_COLS]
    if not cols:
        return {"ok": False, "reason": "该表没有可画像的非 PII 列", "suggestion": ""}

    from ..db import jsonify_value, query

    tbl = _quote(name)
    try:
        total = query(f"SELECT COUNT(*) FROM {tbl}")[1][0][0]
    except Exception as e:
        return {"ok": False, "reason": f"体检失败：{e}", "suggestion": "表可能刚被删除，请 search_schema 确认"}
    if not total:
        return {"ok": True, "table": name, "rows": 0, "columns": [],
                "notes": ["表为空（0 行），任何分析都不会有结果，请先确认数据范围"]}

    out: list[dict] = []
    notes: list[str] = []
    for c in cols:
        col = _quote(c.name)
        row: dict = {"name": c.name, "type": c.type}
        try:
            nulls, distinct = query(
                f"SELECT COUNT(*) - COUNT({col}), COUNT(DISTINCT {col}) FROM {tbl}")[1][0]
            row["null_rate"] = round(nulls / total, 4)
            row["distinct"] = int(distinct)
            if _is_numeric(c.type):
                stats = query(
                    f"SELECT MIN({col}), MAX({col}), "
                    f"AVG({col}), STDDEV_SAMP({col}) FROM {tbl}")[1][0]
                mn, mx, avg, std = (jsonify_value(v) for v in stats)
                if avg is not None:
                    row["min"], row["max"], row["avg"] = mn, mx, round(avg, 4)
                    # |z|>=_OUTLIER_Z 的行数：确定性 SQL，不采样不近似
                    if std:
                        outliers = query(
                            f"SELECT COUNT(*) FROM {tbl} WHERE {col} IS NOT NULL "
                            f"AND ABS({col} - {avg}) / {std} >= {_OUTLIER_Z}")[1][0][0]
                        if outliers:
                            row["outliers"] = int(outliers)
        except Exception:
            row["error"] = "统计失败（类型不支持或列已变化）"
        out.append(row)

    # 质量提示：确定性规则生成，宁缺毋滥
    for row in out:
        nr = row.get("null_rate") or 0
        if nr >= _MISSING_WARN:
            notes.append(f"列 {row['name']} 缺失率 {nr * 100:.0f}%，涉及它的结论需注明口径")
        if row.get("outliers"):
            notes.append(f"列 {row['name']} 有 {row['outliers']} 行偏离均值 "
                         f"（|z|≥{_OUTLIER_Z:g}，min={row.get('min')}，max={row.get('max')}），"
                         f"均值类指标建议先核查这些记录")
    text_cols = [r for r in out if not _is_numeric(r.get("type", ""))
                 and "null_rate" in r and r.get("distinct")]
    for r in text_cols:
        if r["distinct"] / total > 0.9 and total > 100:
            notes.append(f"列 {r['name']} 近似唯一（{r['distinct']}/{total}），适合做主键而非维度")
    if total < 30:
        notes.append(f"表仅 {total} 行，样本量小，趋势/占比结论仅供参考")

    return {"ok": True, "table": name, "rows": int(total), "columns": out, "notes": notes}


@tool
async def profile_table(table: str) -> str:
    """数据质量体检：对一张表统计行数、各列空值率/取值基数、数值列分布（min/max/avg）
    与异常行数（|z|>=3），并给出确定性质量提示。对一张没见过的表做深入分析前，
    或对结论可疑时建议先调用本工具，把质量结论写进你的回答（如「该列缺失率 30%，结论仅供参考」）。
    table 必须是语义层登记的表名（search_schema 命中过的）。"""
    return json.dumps(await asyncio.to_thread(profile_table_core, table), ensure_ascii=False)
