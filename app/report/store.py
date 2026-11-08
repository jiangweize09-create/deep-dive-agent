"""报告产物仓库：请求内的图表/洞察登记处 + 落盘文件索引。

- current_report（ContextVar）：delegate_viz 在进入 viz 子 Agent 前 set，
  工具把 render_chart / export_excel / fetch_data 的产物登记进来；
  review 节点（真代码反思）读它判断「该做的是否做了」。
- _reports 只保证请求内可查；报告 HTML / Excel 是自包含文件，
  落盘后服务重启也能通过 /api/report/<id> 继续访问。
"""

import uuid
from contextvars import ContextVar
from datetime import datetime

from ..config import settings

current_report: ContextVar[dict | None] = ContextVar("xihuang_current_report", default=None)


def register_report(question: str) -> dict:
    rep = {
        "id": f"rep_{uuid.uuid4().hex[:8]}",
        "question": question,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "title": "",
        "summary_md": "",
        "row_count": 0,
        "numeric_cols": [],
        "charts": [],      # [{chart_id, title, chart_type, option}]
        "insights": [],    # [{kind, level, text}]
        "excel": None,     # 相对文件名
        "handles": [],
    }
    return rep


def _rep() -> dict | None:
    return current_report.get()


# ---- 工具侧登记接口（都写入 current_report，请求结束即随 ContextVar 消失）----

def add_chart(title: str, chart_type: str, option: dict) -> str:
    rep = _rep()
    cid = f"c{len(rep['charts']) + 1}" if rep else ""
    if rep:
        rep["charts"].append({"chart_id": cid, "title": title,
                              "chart_type": chart_type, "option": option})
    return cid


def add_insights(items: list[dict]) -> None:
    rep = _rep()
    if rep:
        rep["insights"].extend(items)


def set_data_meta(row_count: int, numeric_cols: list[str], handle: str) -> None:
    rep = _rep()
    if rep:
        rep["row_count"] = max(rep["row_count"], row_count)
        for c in numeric_cols:
            if c not in rep["numeric_cols"]:
                rep["numeric_cols"].append(c)
        if handle and handle not in rep["handles"]:
            rep["handles"].append(handle)


def set_summary(title: str, summary_md: str) -> None:
    rep = _rep()
    if rep:
        rep["title"] = title
        rep["summary_md"] = summary_md


def set_excel(filename: str) -> None:
    rep = _rep()
    if rep:
        rep["excel"] = filename


def report_path(name: str) -> "object":
    return settings.reports_dir / name
