"""viz/报告子 Agent 的工具池：凭句柄取数、图表、Excel、报告组装。

与 SQL 子 Agent 的工具同一套纪律：
- 工具返回结构化 JSON（含错误 reason/suggestion），不抛异常；
- core 函数同步，供 demo 与单测直调；@tool 包装层 asyncio.to_thread 下沉。
- 所有产物（图表/洞察/Excel）登记进请求级 report store；
  ToolMessage 只带元信息，图表 option 主要给前端渲染与报告落盘用。
"""

import asyncio
import json

from langchain_core.tools import tool

from ..tools.chart import build_option
from ..tools.scratch import fetch_handle_core
from . import store
from .excel import export_excel_core
from .insights import compute_insights

CHART_MAX_ROWS = 500     # 图表数据上限（与 generate_chart 同规）
PREVIEW_ROWS = 20        # fetch_data 返回给 LLM 的预览行数


def fetch_data_core(handle: str) -> dict:
    """凭句柄取数 + 确定性洞察。洞察同时登记进报告。"""
    d = fetch_handle_core(handle)
    if not d["ok"]:
        return d
    insights = compute_insights(d["columns"], d["rows"])
    store.add_insights(insights)
    store.set_data_meta(d["row_count"], d["columns"], handle)
    return {"ok": True, "handle": handle, "columns": d["columns"],
            "rows": d["rows"][:PREVIEW_ROWS], "row_count": d["row_count"],
            "truncated": d["row_count"] > PREVIEW_ROWS,
            "insights": insights}


def render_chart_core(handle: str, chart_type: str, x: str, y, title: str = "") -> dict:
    """凭句柄取数生成 ECharts 配置，登记进报告并返回（前端内联渲染）。"""
    d = fetch_handle_core(handle)
    if not d["ok"]:
        return d
    if isinstance(y, str):
        ys = [s.strip() for s in y.split(",") if s.strip()]
    else:
        ys = [str(s).strip() for s in (y or [])]
    ys = [s for s in ys if s]
    if not ys or not x:
        return {"ok": False, "stage": "chart", "reason": "缺少 x 或 y 字段",
                "suggestion": "x 为维度字段名，y 为数值字段名（多个用逗号分隔），都必须是 handle 结果集的列"}
    missing = [c for c in [x] + ys if c not in d["columns"]]
    if missing:
        return {"ok": False, "stage": "chart",
                "reason": f"字段不在结果集中：{', '.join(missing)}",
                "suggestion": f"可用列：{', '.join(d['columns'])}"}
    records = d["rows"][:CHART_MAX_ROWS]
    if not records:
        return {"ok": False, "stage": "chart", "reason": "结果集为空，无法绘图",
                "suggestion": "回到 delegate_sql 放宽过滤条件后重新落表"}

    ct = (chart_type or "auto").lower()
    if ct == "auto":
        ct = "bar" if len(records) <= 8 and len(ys) == 1 else "line"
    if ct == "pie" and len(ys) > 1:
        ct = "bar"
    title = title or " ".join(ys)
    option = build_option(ct, title, records, x, ys)
    cid = store.add_chart(title, ct, option)
    return {"ok": True, "chart_id": cid, "chart_type": ct, "title": title,
            "x": x, "y": ys, "option": option, "row_count": len(records)}


def export_excel_core_checked(handle: str, filename: str = "") -> dict:
    """导出 Excel 并登记进报告。"""
    r = export_excel_core(handle, filename)
    if r["ok"]:
        store.set_excel(r["file"])
    return r


def build_report_core(title: str, summary_md: str) -> dict:
    """组装单文件 HTML 报告（图表/洞察/摘要/明细全部内嵌），返回访问链接。"""
    rep = store.current_report.get()
    if rep is None:
        return {"ok": False, "reason": "报告上下文不存在",
                "suggestion": "build_report 只能在报告 Agent 执行过程中调用"}
    if not rep["charts"]:
        return {"ok": False, "stage": "report", "reason": "还没有任何图表，报告为空",
                "suggestion": "先调用 render_chart 生成至少一张图表，再组装报告"}
    if not (summary_md or "").strip():
        return {"ok": False, "stage": "report", "reason": "摘要为空",
                "suggestion": "summary_md 用中文 Markdown 概括数据结论与口径，不要罗列原始行"}

    store.set_summary(title, summary_md)
    from .assemble import build_report_html
    path = build_report_html(rep)
    return {"ok": True, "report_id": rep["id"], "url": f"/api/report/{rep['id']}",
            "excel_url": f"/api/report/{rep['id']}/excel" if rep.get("excel") else "",
            "charts": len(rep["charts"]), "insights": len(rep["insights"])}


# ===== 工具层（async 包装）=====

@tool
async def fetch_data(handle: str) -> str:
    """凭句柄读取结果集：返回列名、前 20 行预览、总行数，以及由确定性统计代码
    计算的洞察（趋势/环比/异常/集中度）。报告中的数字与结论必须以这些洞察和
    预览数据为准，禁止编造。"""
    return json.dumps(await asyncio.to_thread(fetch_data_core, handle), ensure_ascii=False)


@tool
async def render_chart(handle: str, chart_type: str, x: str, y: str | list[str], title: str = "") -> str:
    """凭句柄生成报告图表（ECharts，前端会内联渲染并进入报告文件）。
    chart_type: line/bar/pie/auto；x 为维度字段名，y 为数值字段名（可多个，逗号分隔），
    必须是 fetch_data 返回的列名；title 为图表标题。"""
    return json.dumps(
        await asyncio.to_thread(render_chart_core, handle, chart_type, x, y, title),
        ensure_ascii=False)


@tool
async def export_excel(handle: str, filename: str = "") -> str:
    """把句柄结果集导出为 Excel 文件，报告中会出现下载按钮。
    filename 可选（如 gmv-monthly），默认按内容命名。"""
    return json.dumps(
        await asyncio.to_thread(export_excel_core_checked, handle, filename),
        ensure_ascii=False)


@tool
async def build_report(title: str, summary_md: str) -> str:
    """组装最终报告（单文件 HTML，含图表、洞察、结论与数据明细）。
    title 为报告标题；summary_md 为分析结论（中文 Markdown，含口径说明，
    数字必须来自 fetch_data 的洞察与预览）。返回报告链接，结论中请引用该链接。"""
    return json.dumps(
        await asyncio.to_thread(build_report_core, title, summary_md), ensure_ascii=False)
