"""单文件 HTML 报告组装。

自包含：echarts.min.js 直接内联（frontend/vendor 本地优先，缺失时回退 CDN），
数据（图表配置 + 数据表）全部嵌进 HTML，双击即可离线打开。
摘要 Markdown 服务端渲染，并做最小化消毒（去 script/事件属性/javascript: 协议）。
"""

import json
import re
from pathlib import Path

from ..config import settings
from .store import report_path

_VENDOR = settings.frontend_dir / "vendor" / "echarts.min.js"


def _md_to_html(md: str) -> str:
    try:
        import markdown as _md
        html = _md.markdown(md or "", extensions=["tables", "fenced_code"])
    except ImportError:
        # 依赖缺失时的兜底：整段按纯文本输出，不丢内容
        html = f"<pre>{_esc(md or '')}</pre>"
    return _sanitize(html)


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _sanitize(html: str) -> str:
    """最小化消毒：摘要由 LLM 生成，不能直接信任其 HTML 片段。"""
    html = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", html, flags=re.I | re.S)
    html = re.sub(r"<(script|iframe|object|embed|link|meta)\b[^>]*>?", "", html, flags=re.I)
    html = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", html, flags=re.I)
    html = re.sub(r"(href|src)\s*=\s*(\"|')?\s*javascript:[^\"'>\s]*(\"|')?", "", html, flags=re.I)
    return html


_CSS = """
:root{--bg:#f6f7fb;--card:#fff;--ink:#1f2430;--sub:#6b7280;--line:#e5e7eb;--blue:#3b82f6;--warn:#f59e0b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:32px 20px 60px}
.head h1{font-size:26px;margin:0 0 6px}.head .q{color:var(--sub);font-size:14px;margin-bottom:4px}
.badge{display:inline-block;font-size:12px;color:var(--blue);background:#eff6ff;
border:1px solid #bfdbfe;border-radius:999px;padding:2px 10px;margin-top:8px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:20px 24px;margin-top:18px}
section h2{font-size:16px;margin:0 0 14px;padding-left:10px;border-left:3px solid var(--blue)}
.insight{display:flex;gap:10px;padding:8px 0;border-bottom:1px dashed var(--line);font-size:14px}
.insight:last-child{border-bottom:0}
.tag{flex:none;font-size:12px;border-radius:4px;padding:1px 8px;height:fit-content;margin-top:2px}
.tag.info{color:#1d4ed8;background:#eff6ff}.tag.warn{color:#b45309;background:#fef3c7}
.chart{width:100%;height:340px;margin:10px 0}
.md table{border-collapse:collapse;width:100%;font-size:13px}
.md th,.md td{border:1px solid var(--line);padding:6px 10px;text-align:left}
.md th{background:#f9fafb}.md code{background:#f3f4f6;padding:1px 5px;border-radius:4px}
.md blockquote{margin:8px 0;padding:6px 14px;border-left:3px solid var(--line);color:var(--sub)}
table.data{border-collapse:collapse;width:100%;font-size:12.5px;white-space:nowrap}
table.data th,table.data td{border:1px solid var(--line);padding:5px 10px;text-align:right}
table.data th:first-child,table.data td:first-child{text-align:left}
table.data th{background:#f9fafb}
.foot{color:var(--sub);font-size:12.5px;margin-top:22px;line-height:1.8}
.excel-btn{display:inline-block;background:var(--blue);color:#fff;text-decoration:none;
font-size:13px;border-radius:8px;padding:7px 16px;margin-left:8px}
.back{display:inline-flex;align-items:center;gap:5px;font-size:13px;color:var(--sub);
text-decoration:none;background:var(--card);border:1px solid var(--line);
border-radius:999px;padding:6px 14px;margin-bottom:18px}
.back:hover{color:var(--blue);border-color:#bfdbfe}
@media print{.back{display:none}}
"""

_TMPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Deep Dive 分析报告</title>
<style>{css}</style>{echarts}</head>
<body><div class="wrap">
{back}
<div class="head">
  <h1>{title}</h1>
  <div class="q">用户问题：{question}</div>
  <div class="badge">Deep Dive Agent · {created} 生成 · 数据经只读护栏查询</div>
</div>
<section><h2>核心洞察（确定性统计）</h2>{insights}</section>
<section><h2>图表</h2>{charts}</section>
<section><h2>分析结论</h2><div class="md">{summary}</div>{excel_btn}</section>
<section><h2>数据明细（前 {data_rows} 行）</h2>{table}</section>
<div class="foot">本报告由 Deep Dive Agent 生成：SQL 经 AST 护栏校验（只读 / PII 拦截 / 指标口径）后在 DuckDB
只读连接上执行；洞察由确定性统计代码计算（趋势 / 环比 / z-score 异常 / 集中度），图表配置与数据均内嵌本文件，可离线查看。</div>
</div>{init}</body></html>"""


def build_report_html(rep: dict) -> Path:
    """rep 是 store 里的报告字典；组装成自包含 HTML 并落盘，返回文件路径。"""
    # echarts：本地 vendor 内联（离线可用），缺失时回退 CDN
    if _VENDOR.exists():
        echarts = f"<script>{_VENDOR.read_text(encoding='utf-8')}</script>"
    else:
        echarts = '<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>'

    if rep["insights"]:
        ins = "".join(
            f'<div class="insight"><span class="tag {i["level"]}">'
            f'{"注意" if i["level"] == "warn" else "结论"}</span><span>{_esc(i["text"])}</span></div>'
            for i in rep["insights"])
    else:
        ins = '<div class="insight"><span class="tag info">结论</span><span>无统计洞察。</span></div>'

    if rep["charts"]:
        charts = "".join(
            f'<div class="chart" id="{c["chart_id"]}"></div>'
            for c in rep["charts"])
    else:
        charts = '<div style="color:var(--sub)">本次未生成图表。</div>'

    excel_btn = ""
    if rep.get("excel"):
        excel_btn = (f'<a class="excel-btn" href="/api/report/{rep["id"]}/excel">'
                     f"下载 Excel（{rep['excel']}）</a>")

    # 数据明细：第一个句柄的前 20 行
    table, data_rows = "<p style='color:var(--sub)'>无数据明细。</p>", 0
    if rep["handles"]:
        try:
            from ..tools.scratch import fetch_handle_core
            d = fetch_handle_core(rep["handles"][0], limit=20)
            if d["ok"] and d["rows"]:
                data_rows = len(d["rows"])
                th = "".join(f"<th>{_esc(c)}</th>" for c in d["columns"])
                trs = "".join(
                    "<tr>" + "".join(f"<td>{_esc(r.get(c, ''))}</td>" for c in d["columns"]) + "</tr>"
                    for r in d["rows"])
                table = (f"<table class='data'><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"
                         f"<p style='color:var(--sub);font-size:12px'>完整数据可通过上方 Excel 下载。</p>")
        except Exception:
            pass

    # 返回按钮：报告是独立页面，点链接进来时不能只靠浏览器后退键。
    # 有历史就 back（保留应用侧状态），没有就回首页（报告被单独分享时）。
    back = ('<a class="back" href="/" '
            'onclick="if(history.length>1){history.back();return false;}">&#8592; 返回应用</a>')

    html = _TMPL.format(
        title=_esc(rep.get("title") or "数据分析报告"),
        question=_esc(rep.get("question") or ""),
        created=_esc(rep.get("created") or ""),
        css=_CSS, echarts=echarts, insights=ins, charts=charts, back=back,
        summary=_md_to_html(rep.get("summary_md", "")),
        excel_btn=excel_btn, table=table, data_rows=data_rows,
        init=("<script>" + "".join(
            f'echarts.init(document.getElementById("{c["chart_id"]}")).setOption({json.dumps(c["option"], ensure_ascii=False)});'
            for c in rep["charts"]) + "</script>" if rep["charts"] else ""),
    )

    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    path = Path(report_path(f"{rep['id']}.html"))
    path.write_text(html, encoding="utf-8")
    return path
