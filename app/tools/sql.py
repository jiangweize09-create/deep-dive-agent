"""SQL 校验 / 执行 / 图表工具。

关键设计：工具返回结构化错误（JSON），不抛异常 —— 让 LLM 依据
reason / suggestion 自修复。run_sql / generate_chart 执行前都会
重新过一遍护栏（不信任上游调用链）。

同步/异步边界：core函数（run_sql_core 等）保持同步，供 demo 与单测直调；
@tool 包装层用 asyncio.to_thread 下沉，因为护栏的 EXPLAIN 试运行和 DuckDB
执行都是同步阻塞的，直接在事件循环里调会卡住所有并发请求。
"""

import asyncio
import json
import time

from langchain_core.tools import tool

from ..config import settings
from ..db import query, rows_to_dicts
from ..guardrails.checks import check_sql


def _err(v: dict) -> dict:
    return {"ok": False, "stage": "validate", "reason": v.get("reason", "校验未通过"),
            "suggestion": v.get("suggestion", ""), "checks": v.get("checks", [])}


def validate_sql_core(sql: str) -> dict:
    return check_sql(sql)


def run_sql_core(sql: str) -> dict:
    v = check_sql(sql)
    if not v["valid"]:
        return _err(v)
    t0 = time.perf_counter()
    try:
        cols, rows = query(v["sql_out"])
    except Exception as e:  # 执行期错误也要结构化返回，让 LLM 能依据 reason 自修复
        return {"ok": False, "stage": "execute", "reason": f"执行失败：{e}",
                "suggestion": "检查函数名与方言。本库是 DuckDB：按月分组用 strftime(created_at,'%Y-%m') 或 date_trunc('month',col)，没有 DATE_FORMAT"}
    ms = int((time.perf_counter() - t0) * 1000)
    preview = rows_to_dicts(cols, rows[: settings.row_preview])
    return {
        "ok": True,
        "sql": v["sql_out"],
        "columns": cols,
        "rows": preview,
        "row_count": len(rows),
        "truncated": len(rows) >= settings.max_rows,
        "ms": ms,
    }


def generate_chart_core(sql: str, chart_type: str, x: str, y, title: str = "") -> dict:
    from .chart import build_option  # 延迟导入避免循环

    v = check_sql(sql)
    if not v["valid"]:
        return _err(v)
    if isinstance(y, str):
        ys = [s.strip() for s in y.split(",") if s.strip()]
    else:
        ys = [str(s).strip() for s in (y or [])]
    ys = [s for s in ys if s]
    if not ys or not x:
        return {"ok": False, "stage": "chart", "reason": "缺少 x 或 y 字段", "suggestion": "x 为维度字段，y 为数值字段（多个用逗号分隔）"}

    try:
        cols, rows = query(v["sql_out"])
    except Exception as e:
        return {"ok": False, "stage": "execute", "reason": f"执行失败：{e}",
                "suggestion": "检查函数名与方言（DuckDB：strftime / date_trunc / coalesce）"}
    records = rows_to_dicts(cols, rows[:500])
    if not records:
        return {"ok": False, "stage": "chart", "reason": "查询结果为空，无法绘图", "suggestion": "放宽过滤条件后重试"}

    ct = (chart_type or "auto").lower()
    if ct == "auto":
        ct = "bar" if len(records) <= 8 and len(ys) == 1 else "line"
    if ct == "pie" and len(ys) > 1:
        ct = "bar"

    title = title or " ".join(ys)
    option = build_option(ct, title, records, x, ys)
    return {"ok": True, "chart_type": ct, "title": title, "x": x, "y": ys,
            "option": option, "row_count": len(records)}


# ===== 工具层（async 包装）=====
# core 函数保持同步语义，供 demo/traces.py 与tests/smoke.py 直接调用；
# 真正的 DB 访问（check_sql 里的 EXPLAIN 试运行 + query 执行）是同步阻塞的，
# 而工具跑在 FastAPI 的 asyncio 事件循环里——一条慢 SQL 会卡住所有并发用户的
# SSE 流。所以工具层统一用 asyncio.to_thread 把同步工作下沉到线程池。
# 注意：这是为了不阻塞事件循环，不是为了提升吞吐——db.py 里的 threading.Lock
# 本来就让所有查询串行。
#
# （为什么不用 LangGraph 的 astream_events 做 token 级流式：项目用的是
#   graph.astream(stream_mode="updates")，同一个图对象不能并行跑两路，
#   双跑会把整个 ReAct 循环和所有工具副作用执行两遍。）


@tool
async def validate_sql(sql: str) -> str:
    """校验 SQL（只读护栏）。返回 valid 与逐项检查结果（只读/无 SELECT */表字段存在/PII 拦截/join 路径/join 键/LIMIT/指标口径）。
    校验不通过时给出 reason 与 suggestion，请据此修正 SQL。校验通过时返回追加 LIMIT 后的 sql_out，执行请用它。"""
    return json.dumps(await asyncio.to_thread(validate_sql_core, sql), ensure_ascii=False)


@tool
async def run_sql(sql: str) -> str:
    """执行 SQL 查询。会先过护栏校验（仅 SELECT、PII 拦截、自动 LIMIT 1000、指标口径），
    返回列名、前若干行预览、总行数与耗时。校验不通过返回结构化错误，请修正后重试。"""
    return json.dumps(await asyncio.to_thread(run_sql_core, sql), ensure_ascii=False)


@tool
async def generate_chart(sql: str, chart_type: str, x: str, y: str | list[str], title: str = "") -> str:
    """基于查询结果生成图表（ECharts 配置）。sql 传刚才执行过的同一条查询；
    chart_type: line/bar/pie/auto；x 为维度字段名，y 为数值字段名（字符串用逗号分隔，
    或传字符串数组）；title 为图表标题。返回图表配置，前端会渲染。"""
    return json.dumps(
        await asyncio.to_thread(generate_chart_core, sql, chart_type, x, y, title),
        ensure_ascii=False)
