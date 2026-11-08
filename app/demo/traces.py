"""演示模式：无 API Key 时的脚本化 Agent 轨迹。

关键设计：SQL 校验、执行、图表全部**真实运行**（走真实工具与护栏），
只有 LLM 的决策与话术是脚本模拟 —— 未配 Key 也能完整体验链路与护栏。
"""

import asyncio

from ..rag import retrieve
from ..report.tools import build_report_core, export_excel_core_checked, fetch_data_core, render_chart_core
from ..tools.date_range import resolve_date_range_core
from ..tools.retrieval import search_metrics_core, search_schema_core
from ..tools.scratch import persist_result_core
from ..tools.sql import generate_chart_core, run_sql_core, validate_sql_core

PACING = 0.45


def pick_scenario(msg: str):
    m = (msg or "").lower()
    # 口径/方法类问题先于数据类判断（"GMV 怎么算"应走知识库而不是查数）
    if any(k in m for k in ["怎么算", "如何计算", "怎么定义", "口径", "怎么分析",
                            "怎么排查", "怎么做", "复盘", "怎么拆", "什么区别"]):
        return "knowledge"
    if any(k in m for k in ["手机", "电话", "phone", "邮箱", "email", "身份证", "联系方式"]):
        return "pii"
    if any(k in m for k in ["报告", "导出", "excel", "汇报", "下载"]):
        return "report"
    if any(k in m for k in ["趋势", "每月", "按月", "逐月", "月度", "近6", "近 6", "最近6", "最近 6", "近半年"]):
        return "trend"
    if any(k in m for k in ["地区", "区域", "大区", "占比", "分布"]):
        return "region"
    if any(k in m for k in ["gmv", "成交额", "交易额", "销售额", "营收", "交易总额"]):
        return "gmv"
    return None


async def run_demo(message: str):
    key = pick_scenario(message)
    if key is None:
        yield {"type": "final", "markdown": FALLBACK}
        yield {"type": "done", "usage": {"steps": 0}}
        return
    async for ev in SCENARIOS[key](message):
        yield ev


async def _p():
    await asyncio.sleep(PACING)


# ---------- 场景 1：GMV ----------

async def scenario_gmv(message: str = ""):
    # 日期一律走 resolve_date_range_core 动态解析（与 live 模式同一纪律：不硬编码日期）
    prev = resolve_date_range_core("上个月")
    prev2 = resolve_date_range_core("上上个月")
    p_key, p2_key = prev["start_date"][:7], prev2["start_date"][:7]

    yield {"type": "thinking", "text": "用户问上月 GMV。先检索指标定义，确认计算口径。"}
    await _p()
    yield {"type": "step", "tool": "search_metrics", "args": {"query": "GMV 成交额"}}
    r = search_metrics_core("GMV 成交额")
    await _p()
    yield {"type": "result", "tool": "search_metrics", "content": r}

    yield {"type": "thinking", "text": "GMV = SUM(orders.amount)，必须带 status='completed'。再确认订单表的时间字段。"}
    await _p()
    yield {"type": "step", "tool": "search_schema", "args": {"query": "订单表 orders"}}
    r2 = search_schema_core("订单表 orders")
    await _p()
    yield {"type": "result", "tool": "search_schema", "content": r2}

    sql = ("SELECT strftime(created_at, '%Y-%m') AS month, SUM(amount) AS gmv "
           f"FROM orders WHERE status = 'completed' AND created_at >= '{prev2['start_date']}' "
           f"AND created_at < '{prev['end_date']}' GROUP BY 1 ORDER BY 1")
    yield {"type": "thinking", "text": f"按月聚合 {p2_key} 与 {p_key} 完成订单金额，顺带算环比。SQL 先过护栏校验。"}
    await _p()
    yield {"type": "step", "tool": "validate_sql", "args": {"sql": sql}}
    v = validate_sql_core(sql)
    await _p()
    yield {"type": "result", "tool": "validate_sql", "content": v}

    yield {"type": "step", "tool": "run_sql", "args": {"sql": sql}}
    d = run_sql_core(sql)
    await _p()
    yield {"type": "result", "tool": "run_sql", "content": d}

    rows = {r["month"]: r["gmv"] for r in d.get("rows", [])}
    prev2_gmv, prev_gmv = rows.get(p2_key, 0) or 0, rows.get(p_key, 0) or 0
    mom = (prev_gmv - prev2_gmv) / prev2_gmv * 100 if prev2_gmv else 0
    sign = "增长" if mom >= 0 else "下降"
    md = (
        f"上月（{p_key}）GMV 为 **¥{prev_gmv:,.0f}**，环比{sign} **{abs(mom):.1f}%**"
        f"（{p2_key} 为 ¥{prev2_gmv:,.0f}）。\n\n"
        f"> 口径：GMV = 已完成订单金额合计（status = 'completed'），不含退款、待支付与已取消订单。\n\n"
        f"想继续看趋势或拆解，可以问「近 6 个月每月订单量趋势」或「各地区销售额占比」。"
    )
    await _p()
    yield {"type": "final", "markdown": md}
    yield {"type": "done", "usage": {"steps": 4}}


# ---------- 场景 2：月度趋势（折线图） ----------

async def scenario_trend(message: str = ""):
    yield {"type": "thinking", "text": "用户要看近 6 个月的月度趋势。先确认订单量指标口径。"}
    await _p()
    yield {"type": "step", "tool": "search_metrics", "args": {"query": "订单量"}}
    r = search_metrics_core("订单量")
    await _p()
    yield {"type": "result", "tool": "search_metrics", "content": r}

    yield {"type": "thinking", "text": "订单量 = COUNT(*)，仅统计完成订单。写 SQL 一次带出 GMV 与订单量两个序列。"}
    await _p()
    r6 = resolve_date_range_core("近6个月")
    sql = ("SELECT strftime(created_at, '%Y-%m') AS month, SUM(amount) AS gmv, COUNT(*) AS order_count "
           f"FROM orders WHERE status = 'completed' AND created_at >= '{r6['start_date']}' "
           f"AND created_at < '{r6['end_date']}' GROUP BY 1 ORDER BY 1")
    yield {"type": "step", "tool": "validate_sql", "args": {"sql": sql}}
    v = validate_sql_core(sql)
    await _p()
    yield {"type": "result", "tool": "validate_sql", "content": v}

    yield {"type": "step", "tool": "run_sql", "args": {"sql": sql}}
    d = run_sql_core(sql)
    await _p()
    yield {"type": "result", "tool": "run_sql", "content": d}

    yield {"type": "step", "tool": "generate_chart",
           "args": {"sql": sql, "chart_type": "line", "x": "month", "y": "gmv,order_count",
                    "title": "近 6 个月 GMV 与订单量趋势"}}
    c = generate_chart_core(sql, "line", "month", "gmv,order_count", "近 6 个月 GMV 与订单量趋势")
    await _p()
    yield {"type": "result", "tool": "generate_chart", "content": c}

    rows = d.get("rows", [])
    if rows:
        top = max(rows, key=lambda r: r.get("gmv") or 0)
        last = rows[-1]
        md = (
            f"近 6 个月共 **{sum(r['order_count'] for r in rows):,}** 笔完成订单，"
            f"GMV 峰值出现在 **{top['month']}**（¥{(top['gmv'] or 0):,.0f}）；"
            f"最新月份 {last['month']}：订单 {last['order_count']:,} 笔、GMV ¥{(last['gmv'] or 0):,.0f}。\n\n"
            f"| 月份 | 订单量 | GMV |\n|---|---:|---:|\n"
            + "\n".join(f"| {r['month']} | {r['order_count']:,} | ¥{(r['gmv'] or 0):,.0f} |" for r in rows)
            + "\n\n> 口径：仅统计 status = 'completed' 的订单。"
        )
    else:
        md = "查询结果为空，请确认演示数据已生成。"
    await _p()
    yield {"type": "final", "markdown": md}
    yield {"type": "done", "usage": {"steps": 4}}


# ---------- 场景 3：地区占比（JOIN + 饼图） ----------

async def scenario_region(message: str = ""):
    yield {"type": "thinking", "text": "用户要看各地区销售额占比。销售额即 GMV，先确认口径与表结构。"}
    await _p()
    yield {"type": "step", "tool": "search_schema", "args": {"query": "订单 地区 regions"}}
    r = search_schema_core("订单 地区 regions")
    await _p()
    yield {"type": "result", "tool": "search_schema", "content": r}

    yield {"type": "thinking", "text": "订单表 region_id 关联地区表，JOIN 后按地区聚合完成订单金额。"}
    await _p()
    sql = ("SELECT r.name AS region, SUM(o.amount) AS gmv FROM orders o "
           "JOIN regions r ON o.region_id = r.id WHERE o.status = 'completed' "
           "GROUP BY 1 ORDER BY 2 DESC")
    yield {"type": "step", "tool": "validate_sql", "args": {"sql": sql}}
    v = validate_sql_core(sql)
    await _p()
    yield {"type": "result", "tool": "validate_sql", "content": v}

    yield {"type": "step", "tool": "run_sql", "args": {"sql": sql}}
    d = run_sql_core(sql)
    await _p()
    yield {"type": "result", "tool": "run_sql", "content": d}

    yield {"type": "step", "tool": "generate_chart",
           "args": {"sql": sql, "chart_type": "pie", "x": "region", "y": "gmv",
                    "title": "各地区 GMV 占比"}}
    c = generate_chart_core(sql, "pie", "region", "gmv", "各地区 GMV 占比")
    await _p()
    yield {"type": "result", "tool": "generate_chart", "content": c}

    rows = d.get("rows", [])
    md = "查询结果为空，请确认演示数据已生成。"
    if rows:
        total = sum(r["gmv"] or 0 for r in rows)
        top = rows[0]
        top_pct = (top["gmv"] or 0) / total * 100 if total else 0
        md = (
            f"近 12 个月 GMV 合计 **¥{total:,.0f}**，**{top['region']}** 以 ¥{(top['gmv'] or 0):,.0f} "
            f"（{top_pct:.1f}%）领跑。\n\n"
            f"| 地区 | GMV | 占比 |\n|---|---:|---:|\n"
            + "\n".join(
                f"| {r['region']} | ¥{(r['gmv'] or 0):,.0f} | {(r['gmv'] or 0) / total * 100:.1f}% |"
                for r in rows
            )
            + "\n\n> 口径：仅统计完成订单；地区取自订单上的 region_id。想再按渠道或类目拆解，直接说即可。"
        )
    await _p()
    yield {"type": "final", "markdown": md}
    yield {"type": "done", "usage": {"steps": 4}}


# ---------- 场景 4：PII 护栏拦截 ----------

async def scenario_pii(message: str = ""):
    yield {"type": "thinking", "text": "用户要导出用户手机号——先检索用户表结构确认字段。"}
    await _p()
    yield {"type": "step", "tool": "search_schema", "args": {"query": "用户表 users"}}
    r = search_schema_core("用户表 users")
    await _p()
    yield {"type": "result", "tool": "search_schema", "content": r}

    yield {"type": "thinking", "text": "表结构显示 phone 是 PII 字段。按纪律不能导出，但我还是走一遍护栏校验确认拦截。"}
    await _p()
    sql = "SELECT name, phone FROM users LIMIT 20"
    yield {"type": "step", "tool": "validate_sql", "args": {"sql": sql}}
    v = validate_sql_core(sql)
    await _p()
    yield {"type": "result", "tool": "validate_sql", "content": v}

    md = (
        "这个请求我不能执行：**手机号属于 PII（个人敏感信息），护栏已拦截**。\n\n"
        "- 语义层将 `users.phone`、`users.email` 标记为 PII 字段；\n"
        "- AST 级校验会拦截对它们的一切引用（包括别名、CTE 改名后的引用）；\n"
        "- 即便校验被绕过，数据库连接本身也是只读的。\n\n"
        "如果你要做用户分析，我可以提供**聚合统计**替代，例如：\n"
        "- 各地区用户数分布；\n"
        "- 会员等级（普通/白银/黄金）构成；\n"
        "- 近 12 个月新增注册用户趋势。\n\n"
        "直接说「各地区用户数分布」即可。"
    )
    await _p()
    yield {"type": "final", "markdown": md}
    yield {"type": "done", "usage": {"steps": 2}}


# ---------- 场景 5：知识库（RAG，真实检索） ----------

async def scenario_knowledge(message: str = ""):
    yield {"type": "thinking", "text": "口径/方法类问题——主 Agent 直接调用 rag_retrieve 检索口径手册与分析 SOP。"}
    await _p()

    # 主 Agent 直连工具：rag_retrieve 真实执行混合检索（向量 + BM25 + RRF）。
    # 不再包知识子 Agent —— 单工具没有决策空间，ReAct 循环是伪多 Agent。
    q = (message or "").strip("?？ ")
    yield {"type": "step", "tool": "rag_retrieve", "args": {"query": q}}
    r = retrieve.search(q)
    await _p()
    yield {"type": "result", "tool": "rag_retrieve", "content": r}

    if not r.get("ok"):
        md = ("知识库中没有检索到相关内容。可以直接换个问法，"
              "或把具体的数据问题发给我（会转给 SQL Agent 查数）。")
        yield {"type": "final", "markdown": md}
        yield {"type": "done", "usage": {"steps": 1}}
        return

    chunks = r["chunks"][:3]
    yield {"type": "thinking", "text": f"检索命中 {len(chunks)} 段（引擎：{r.get('engine', '')}），按片段作答并标注来源。"}
    await _p()

    lines = ["根据知识库检索结果，回答如下：\n"]
    for c in chunks:
        excerpt = " ".join(str(c.get("text", "")).split())
        if len(excerpt) > 160:
            excerpt = excerpt[:160] + "…"
        lines.append(f"**〔来源：{c['source']} · {c['title']}〕**\n\n{excerpt}\n")
    lines.append("> 以上内容只来自知识库片段；要算具体数字，直接发数据问题（如「上个月 GMV 多少」），"
                 "我会转给 SQL Agent 真实查数。")
    final_md = "\n".join(lines)

    yield {"type": "final", "markdown": final_md}
    yield {"type": "done", "usage": {"steps": 2}}


# ---------- 场景 6：句柄 + 报告 Agent（全链路真实执行） ----------

async def scenario_report(message: str = ""):
    """演示句柄协议：SQL Agent 落临时表 -> 主 Agent 只传句柄 -> 报告 Agent 凭句柄
    取数出报告。SQL 校验/执行/落表/洞察/图表/Excel/报告全部真实运行。"""
    yield {"type": "thinking", "text": "用户要分析报告。复合任务：先委派 SQL Agent 查数落表，再委派报告 Agent 加工。"}
    await _p()
    yield {"type": "step", "tool": "delegate_sql", "args": {"task": message}}

    # —— SQL 子 Agent 过程（真实执行）——
    r6 = resolve_date_range_core("近6个月")
    yield {"type": "thinking", "text": "任务要求出报告：查数后需调用 persist_result 把结果集落成临时表，返回句柄。"}
    await _p()
    yield {"type": "step", "tool": "search_metrics", "args": {"query": "GMV 订单量"}}
    r = search_metrics_core("GMV 订单量")
    await _p()
    yield {"type": "result", "tool": "search_metrics", "content": r}

    sql = ("SELECT strftime(created_at, '%Y-%m') AS month, SUM(amount) AS gmv, COUNT(*) AS order_count "
           f"FROM orders WHERE status = 'completed' AND created_at >= '{r6['start_date']}' "
           f"AND created_at < '{r6['end_date']}' GROUP BY 1 ORDER BY 1")
    yield {"type": "step", "tool": "validate_sql", "args": {"sql": sql}}
    v = validate_sql_core(sql)
    await _p()
    yield {"type": "result", "tool": "validate_sql", "content": v}

    yield {"type": "step", "tool": "run_sql", "args": {"sql": sql}}
    d = run_sql_core(sql)
    await _p()
    yield {"type": "result", "tool": "run_sql", "content": d}

    yield {"type": "step", "tool": "persist_result", "args": {"sql": sql}}
    p = persist_result_core(sql)
    await _p()
    yield {"type": "result", "tool": "persist_result", "content": p}
    handle = p.get("handle", "") if p.get("ok") else ""

    yield {"type": "result", "tool": "delegate_sql",
           "content": {"ok": bool(handle), "final": "近 6 个月月度 GMV 与订单量结果集已落临时表，句柄已带回。",
                       "handles": [handle] if handle else []}}
    if not handle:
        yield {"type": "final", "markdown": "落表失败，报告流程终止：" + str(p.get("reason", ""))}
        yield {"type": "done", "usage": {"steps": 4}}
        return

    # —— viz 报告子 Agent 过程（真实执行）——
    # 与 live 模式一致：delegate_viz 进入子 Agent 前注册报告上下文
    from ..report import store
    rep = store.register_report(question=message or "近 6 个月 GMV 分析报告")
    tok_rep = store.current_report.set(rep)
    try:
        async for ev in _viz_stage(handle, message):
            yield ev
    finally:
        store.current_report.reset(tok_rep)


async def _viz_stage(handle: str, message: str):
    yield {"type": "step", "tool": "delegate_viz", "args": {"task": message, "handle": handle}}
    yield {"type": "thinking", "text": "报告 Agent 没有查库工具，凭句柄取数 —— 句柄经会话指纹校验，跨会话数据拿不到。"}
    await _p()
    yield {"type": "step", "tool": "fetch_data", "args": {"handle": handle}}
    fd = fetch_data_core(handle)
    await _p()
    yield {"type": "result", "tool": "fetch_data", "content": fd}

    yield {"type": "step", "tool": "render_chart",
           "args": {"handle": handle, "chart_type": "line", "x": "month",
                    "y": "gmv,order_count", "title": "近 6 个月 GMV 与订单量趋势"}}
    c = render_chart_core(handle, "line", "month", "gmv,order_count", "近 6 个月 GMV 与订单量趋势")
    await _p()
    yield {"type": "result", "tool": "render_chart", "content": c}

    yield {"type": "step", "tool": "export_excel",
           "args": {"handle": handle, "filename": "gmv-monthly"}}
    x = export_excel_core_checked(handle, "gmv-monthly")
    await _p()
    yield {"type": "result", "tool": "export_excel", "content": x}

    rows = fd.get("rows", [])
    ins = fd.get("insights", [])
    summary_lines = []
    if rows:
        total_gmv = sum(r2.get("gmv") or 0 for r2 in rows)
        total_ord = sum(r2.get("order_count") or 0 for r2 in rows)
        summary_lines.append(f"近 6 个月共完成订单 **{total_ord:,}** 笔，GMV 合计 **¥{total_gmv:,.0f}**。")
    for i in ins[:3]:
        summary_lines.append(f"- {i['text']}")
    summary_lines.append("\n> 口径：仅统计 status = 'completed' 的订单；洞察由确定性统计代码计算（趋势/环比/z-score 异常/集中度）。")
    summary_md = "\n".join(summary_lines)

    yield {"type": "step", "tool": "build_report",
           "args": {"title": "近 6 个月 GMV 与订单量分析报告", "summary_md": summary_md}}
    br = build_report_core("近 6 个月 GMV 与订单量分析报告", summary_md)
    await _p()
    yield {"type": "result", "tool": "build_report", "content": br}

    # 产物资产化（与 live 模式一致）：报告登记 + 下发 artifact 事件
    if br.get("ok"):
        try:
            from ..artifacts import register_artifact
            from ..tools.scratch import current_session

            art = register_artifact(
                session_id=current_session.get(),
                kind="report",
                title="近 6 个月 GMV 与订单量分析报告",
                url=br.get("url", ""), excel_url=br.get("excel_url", ""),
                meta={"task": message or "近 6 个月 GMV 分析报告",
                      "charts": br.get("charts", 0), "insights": br.get("insights", 0)},
            )
            yield {"type": "artifact", **art}
        except Exception:
            pass    # 登记失败不影响报告本身

    viz_final = ("报告已生成。" if br.get("ok") else "报告组装失败：" + br.get("reason", ""))
    yield {"type": "result", "tool": "delegate_viz",
           "content": {"ok": bool(br.get("ok")), "final": viz_final}}

    if br.get("ok"):
        md = (
            f"报告已完成：**{br['charts']}** 张图表、**{br['insights']}** 条确定性洞察、附 Excel 数据明细。\n\n"
            f"[打开分析报告]({br['url']}) · [下载 Excel]({br['excel_url']})\n\n"
            f"{summary_md}\n\n"
            f"> 本轮走的是**句柄协议**：SQL Agent 把结果集落成临时表返回句柄，"
            f"主 Agent 只把句柄字符串传给报告 Agent，数据本身不进任何 LLM 上下文。"
            f"报告是单文件 HTML（图表内嵌），可离线打开。"
        )
    else:
        md = viz_final
    await _p()
    yield {"type": "final", "markdown": md}
    yield {"type": "done", "usage": {"steps": 8}}


FALLBACK = """当前运行在**演示模式**（未配置 LLM），内置场景可完整体验全链路：

- **「上个月 GMV 多少」** — 指标检索 → 护栏校验 → 执行 → 结论
- **「近 6 个月每月订单量趋势」** — 全链路 + 双序列折线图
- **「各地区销售额占比」** — JOIN 查询 + 饼图
- **「近 6 个月 GMV 分析报告」** — 句柄协议：SQL 落表 → 报告 Agent 洞察/图表/Excel/HTML 报告
- **「GMV 怎么算 / 退款率升高怎么排查」** — 知识库混合检索（RAG）+ 来源引用
- **「导出所有用户手机号」** — 看 PII 护栏如何拦截

以上场景中的 **SQL 校验、执行、落表、洞察、图表、Excel、报告、知识库检索都是真实运行的**，只有 LLM 决策是脚本模拟。

要解锁完整 Agent（回答任意自然语言问题），请在左下角 **「API / 模型配置」** 填入任意 OpenAI 兼容端点（DeepSeek / 通义千问 / 智谱 GLM / Moonshot / OpenAI 均可）。"""


SCENARIOS = {
    "gmv": scenario_gmv,
    "trend": scenario_trend,
    "region": scenario_region,
    "pii": scenario_pii,
    "knowledge": scenario_knowledge,
    "report": scenario_report,
}
