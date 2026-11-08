"""冒烟自测：python tests/smoke.py（在项目根目录、seed 之后运行）。"""

import asyncio
import json
import sys

sys.path.insert(0, ".")


def test_guardrails():
    from app.guardrails.checks import check_sql

    r = check_sql("SELECT * FROM orders")
    assert not r["valid"], "SELECT * 应被拒绝"

    r = check_sql("SELECT name, phone FROM users LIMIT 20")
    assert not r["valid"] and "隐私" in r["reason"], f"PII 应被拦截：{r}"

    r = check_sql("SELECT u.phone FROM users u")
    assert not r["valid"] and "隐私" in r["reason"], "别名引用 PII 应被拦截"

    r = check_sql("WITH t AS (SELECT phone FROM users) SELECT id FROM t")
    assert not r["valid"] and "隐私" in r["reason"], "CTE 内 PII 应被拦截"

    r = check_sql("SELECT COUNT(phone) FROM users")
    assert not r["valid"] and "隐私" in r["reason"], "聚合内 PII 应被拦截"

    r = check_sql("DELETE FROM orders")
    assert not r["valid"], "DELETE 应被拒绝"

    r = check_sql("SELECT something FROM nope")
    assert not r["valid"] and "未知表" in r["reason"], "未知表应被拒绝"

    r = check_sql("SELECT o.amount FROM orders o JOIN regions r ON o.region_id = r.id")
    assert r["valid"], f"合法 JOIN 查询应通过：{r}"

    r = check_sql("SELECT SUM(amount) AS gmv FROM orders WHERE status='completed' AND created_at >= '2026-09-01'")
    assert r["valid"] and "LIMIT" in r["sql_out"], "无 LIMIT 应自动改写追加"
    print("[OK] 护栏：SELECT* / PII（含 CTE/聚合）/ 只写语句 / 未知表 / join 路径 / LIMIT 改写 全部符合预期")


def test_guardrail_limit_bypass():
    """LIMIT 强制上限回归：UNION / 表达式 / LIMIT ALL / 负数均不得绕过（历史绕过点）。"""
    from app.guardrails.checks import check_sql

    def limit_of(sql_out: str) -> str:
        import re
        m = re.search(r"LIMIT\s+(\d+)\s*$", sql_out.strip(), re.IGNORECASE)
        return m.group(1) if m else ""

    # UNION 无 LIMIT：必须自动追加（曾绕过）
    r = check_sql("SELECT id FROM orders UNION SELECT id FROM users")
    assert r["valid"] and limit_of(r["sql_out"]) == "1000", f"UNION 应自动补 LIMIT 1000：{r.get('sql_out')}"

    # UNION 自带小 LIMIT：保留
    r = check_sql("SELECT id FROM orders UNION SELECT id FROM users LIMIT 7")
    assert r["valid"] and limit_of(r["sql_out"]) == "7", f"UNION 自带合法 LIMIT 应保留：{r.get('sql_out')}"

    # 表达式 LIMIT：int() 解析不出字面量，必须改写（曾绕过）
    r = check_sql("SELECT id FROM orders LIMIT 5+5")
    assert r["valid"] and limit_of(r["sql_out"]) == "1000", f"表达式 LIMIT 应改写为 1000：{r.get('sql_out')}"

    # LIMIT ALL：必须改写（曾绕过）
    r = check_sql("SELECT id FROM orders LIMIT ALL")
    assert r["valid"] and limit_of(r["sql_out"]) == "1000", f"LIMIT ALL 应改写为 1000：{r.get('sql_out')}"

    # 负数 LIMIT：必须改写
    r = check_sql("SELECT id FROM orders LIMIT -1")
    assert r["valid"] and limit_of(r["sql_out"]) == "1000", f"负数 LIMIT 应改写为 1000：{r.get('sql_out')}"

    # 超上限压缩 & 合法小 LIMIT 保留（原有行为）
    r = check_sql("SELECT id FROM orders LIMIT 99999")
    assert r["valid"] and limit_of(r["sql_out"]) == "1000", "超上限 LIMIT 应压缩"
    r = check_sql("SELECT id FROM orders LIMIT 50")
    assert r["valid"] and limit_of(r["sql_out"]) == "50", "合法小 LIMIT 应保留"
    print("[OK] LIMIT 强制上限：UNION / 表达式 / LIMIT ALL / 负数 / 超限压缩 全部收口")


def test_guardrail_source_whitelist():
    """数据来源白名单回归：表函数/UNNEST 不得绕过语义层读任意文件（历史绕过点）。

    read_csv_auto 在 sqlglot 里解析成 Table(this=ReadCSV)、glob 解析成
    Table(this=Anonymous)，t.name 为空串会跳过「未知表」检查；UNNEST 甚至
    不是 Table 节点。修复后必须在来源白名单层拦截。
    """
    from app.guardrails.checks import check_sql

    blocked = [
        "SELECT secret_value FROM read_csv_auto('C:/tmp/secret.csv')",
        "SELECT file FROM glob('app/**/*.py')",
        "SELECT i FROM generate_series(1, 10) AS t(i)",
        "SELECT x FROM UNNEST([1, 2, 3]) AS t(x)",
        # 子查询内嵌表函数也不得漏
        "SELECT x FROM (SELECT secret FROM read_csv('C:/x.csv')) t",
        # UNION 分支里的表函数也不得漏
        "SELECT id FROM orders UNION SELECT c0 FROM read_csv('C:/x.csv')",
    ]
    for sql in blocked:
        r = check_sql(sql)
        assert not r["valid"], f"表函数来源应被拦截：{sql} -> {r.get('reason')}"
    # 正常物理表 / 子查询 / VALUES 不受影响
    r = check_sql("SELECT o.amount FROM orders o WHERE o.id IN (SELECT id FROM orders)")
    assert r["valid"], f"普通表与子查询应放行：{r}"
    print("[OK] 来源白名单：read_csv/glob/generate_series/UNNEST（含子查询/UNION 内嵌）全部拦截，"
          "普通表与子查询放行")


def test_retrieval():
    from app.tools.retrieval import search_metrics_core, search_schema_core

    m = search_metrics_core("上个月成交额多少")
    assert any(x["name"] == "gmv" for x in m["metrics"]), f"成交额应召回 gmv：{m}"

    t = search_schema_core("订单表")
    assert any(x["name"] == "orders" for x in t["tables"]), f"订单表应召回 orders：{t}"

    t2 = search_schema_core("用户 手机号")
    assert any(x["name"] == "users" for x in t2["tables"])
    print("[OK] 语义层检索：指标与表召回正常")


def test_sql_tools():
    from app.tools.sql import generate_chart_core, run_sql_core

    # 正常路径：带 required_filters（status='completed'）才算按 order_count 口径
    d = run_sql_core("SELECT COUNT(*) AS c FROM orders WHERE status='completed'")
    assert d["ok"] and d["row_count"] == 1 and d["rows"][0]["c"] > 1000, f"订单量异常：{d}"

    # 漏 required_filters 必须被口径护栏拦下（护栏「指标口径」项，历史误伤点）
    d_missing = run_sql_core("SELECT COUNT(*) AS c FROM orders")
    assert not d_missing["ok"] and "口径" in d_missing["reason"], \
        f"COUNT(*) 漏 status 过滤应被口径护栏拦截：{d_missing}"

    # 跨表聚合不应被 orders 类指标口径误伤（历史误伤：COUNT(*) 丢表名）
    d_prod = run_sql_core("SELECT COUNT(*) AS c FROM products")
    assert d_prod["ok"], f"COUNT(*) FROM products 不该被 orders 口径拦截：{d_prod}"

    d2 = run_sql_core("SELECT name, email FROM users")
    assert not d2["ok"] and "隐私" in d2["reason"], "email 字段应被拦截"

    c = generate_chart_core(
        "SELECT strftime(created_at,'%Y-%m') AS month, SUM(amount) AS gmv FROM orders "
        "WHERE status='completed' GROUP BY 1 ORDER BY 1",
        "line", "month", "gmv", "月度 GMV")
    assert c["ok"] and c["option"]["series"], f"图表生成异常：{c}"
    print(f"[OK] SQL 工具：执行 {d['rows'][0]['c']} 笔 completed 订单、口径护栏、PII 拦截、图表生成均正常")


def test_demo_traces():
    from app.demo.traces import run_demo

    async def run(msg):
        events = []
        async for ev in run_demo(msg):
            events.append(ev)
        return events

    for q, need_chart in [
        ("上个月GMV多少", False),
        ("近 6 个月每月订单量趋势", True),
        ("各地区销售额占比", True),
        ("导出所有用户手机号", False),
    ]:
        evs = asyncio.run(run(q))
        types = [e["type"] for e in evs]
        assert "final" in types and "done" in types, f"{q} 缺少 final/done：{types}"
        tools = [e["tool"] for e in evs if e["type"] == "step"]
        assert "validate_sql" in tools, f"{q} 未走护栏校验：{tools}"
        if need_chart:
            assert "generate_chart" in tools, f"{q} 未生成图表：{tools}"
        if "手机" in q:
            results = [e for e in evs if e["type"] == "result" and e["tool"] == "validate_sql"]
            assert results and not results[0]["content"]["valid"], "PII 场景应校验失败"
    print("[OK] Demo traces：4 个场景全链路（真实校验/执行/图表）通过")


def test_demo_dates_dynamic():
    """demo 轨迹日期动态化回归：SQL 中的月份必须与 resolve_date_range 当月解析一致，不允许硬编码过期。"""
    from app.demo.traces import run_demo
    from app.tools.date_range import resolve_date_range_core

    async def run(msg):
        events = []
        async for ev in run_demo(msg):
            events.append(ev)
        return events

    prev = resolve_date_range_core("上个月")
    prev2 = resolve_date_range_core("上上个月")
    r6 = resolve_date_range_core("近6个月")

    evs = asyncio.run(run("上个月GMV多少"))
    sqls = [e["args"]["sql"] for e in evs if e["type"] == "step" and e.get("tool") == "run_sql"]
    assert sqls, "GMV 场景应有 run_sql 步骤"
    # 环比 SQL 覆盖 [上上个月初, 上个月末)，两端点必须与日期工具返回值一致
    assert prev2["start_date"] in sqls[0] and prev["end_date"] in sqls[0], \
        f"GMV SQL 区间应来自 resolve_date_range：{sqls[0]}"

    evs2 = asyncio.run(run("近 6 个月每月订单量趋势"))
    sqls2 = [e["args"]["sql"] for e in evs2 if e["type"] == "step" and e.get("tool") == "run_sql"]
    assert sqls2 and r6["start_date"] in sqls2[0], f"趋势 SQL 应以近6个月起点开始：{sqls2}"
    print(f"[OK] Demo 日期动态化：SQL 端点与 resolve_date_range 当月解析一致（上个月 = {prev['start_date'][:7]}）")


def test_graph_compile():
    from app.agent.graph import build_graph

    g = build_graph("http://127.0.0.1:9/v1", "sk-test", "test-model")
    assert g is not None
    print("[OK] LangGraph：主图编译成功（agent + tools 两节点 + 条件回边）")


def test_asr_config():
    """语音输入：未配 key 时必须优雅降级，不能抛异常。"""
    from app.asr import asr_info, asr_ready, transcribe
    from app.config import settings

    info = asr_info()
    assert "ready" in info and "max_seconds" in info, f"asr_info 结构不对：{info}"
    assert info["ready"] == asr_ready()

    if asr_ready():
        r = transcribe(b"", "a.webm")
        assert not r["ok"] and r["code"] == "empty_audio", f"空音频应被拒：{r}"
        r2 = transcribe(b"x" * 5000, "a.exe")
        assert not r2["ok"] and r2["code"] == "unsupported_format", f"格式应被拒：{r2}"
        print("[OK] 语音：已配置 key，空音频/格式校验正常")
    else:
        r = transcribe(b"x" * 3000, "a.webm")
        assert not r["ok"] and r["code"] == "asr_not_configured", f"未配置应优雅降级：{r}"
        assert r["status"] == 503
        print(f"[OK] 语音：未配置 key（{settings.asr_model} 待填），优雅降级不报错，语音为可选增强")


def test_event_bus():
    """ContextVar 事件旁路：报告精简（无 events）+ 请求级隔离不串流。"""
    from app.agent.sql_agent import run_sql_agent, subagent_events

    class FakeMsg:
        def __init__(self, *, name="", content="", tool_calls=None):
            self.name = name
            self.content = content
            self.tool_calls = tool_calls

    class FakeGraph:
        """伪子图：agent 出一步工具调用 + 一步结论，tools 出一个结果。"""

        def __init__(self):
            self.updates = [
                ("agent", {"messages": [FakeMsg(content="先查一下", tool_calls=[{"name": "run_sql", "args": {}}])]}),
                ("tools", {"messages": [FakeMsg(name="run_sql", content='{"ok": true, "rows": []}')]}),
                ("agent", {"messages": [FakeMsg(content="结论：GMV 为 100")]}),
            ]

        async def astream(self, inputs, config=None, stream_mode=None):
            for node, update in self.updates:
                yield {node: update}

    async def one_request():
        bus: list = []
        token = subagent_events.set(bus)
        try:
            report = await run_sql_agent(FakeGraph(), "测试任务")
        finally:
            subagent_events.reset(token)
        return report, bus

    report1, bus1 = asyncio.run(one_request())
    assert "events" not in report1, f"报告应精简（不含 events）：{list(report1)}"
    assert report1["final"] == "结论：GMV 为 100" and report1["ok"], f"结论应正确：{report1}"
    kinds = [e["type"] for e in bus1]
    assert kinds == ["thinking", "step", "result"], f"事件应旁路到总线：{kinds}"

    # 第二个请求：总线隔离，不残留第一个请求的事件
    report2, bus2 = asyncio.run(one_request())
    assert len(bus2) == len(bus1) and all(e not in bus1 or e in bus2 for e in bus2), "第二个请求事件数应一致"
    assert subagent_events.get() is None, "reset 后总线应恢复为 None（防跨请求串流）"
    print("[OK] 事件总线：报告精简 + 过程事件旁路 + 请求级隔离（set/reset）正常")


def test_guardrail_join_keys():
    """第 8 项护栏「join 键与声明一致」：拦截笛卡尔积与无关列关联。"""
    from app.guardrails.checks import check_sql

    # 正常 JOIN：声明过的键应放行
    for sql in [
        "SELECT o.amount, u.name FROM orders o JOIN users u ON o.user_id = u.id",
        "SELECT o.amount FROM orders AS o JOIN regions AS r ON o.region_id = r.id",
    ]:
        r = check_sql(sql)
        assert r["valid"], f"声明过的 join 键应放行：{r.get('reason')}"

    # 笛卡尔积：无 ON 条件应拦截
    r = check_sql("SELECT o.amount, u.name FROM orders o, users u")
    assert not r["valid"] and "ON" in r["reason"], f"无 ON 的逗号连接应拦截：{r['reason']}"

    # 无关列关联：用金额等非键列做 join 应拦截
    r = check_sql("SELECT o.amount, u.name FROM orders o JOIN users u ON o.amount = u.id")
    assert not r["valid"] and "join" in r["reason"].lower(), f"错误 join 键应拦截：{r['reason']}"
    print("[OK] join 键校验：声明键放行、笛卡尔积与无关列关联均拦截")


def test_guardrail_metric_caliber():
    """第 9.5 项护栏「指标口径（required_filters）」。

    重点回归两个历史缺陷：
    1) COUNT(*) 丢失表名→ 跨表误伤（SELECT COUNT(*) FROM products 被当成 order_count）
    2) 触发键通配 ("*", col, fn) → 任意表的 SUM(x) 命中所有 SUM 类指标
    """
    from app.guardrails.checks import check_sql

    # 漏 required_filters 必须拦截
    for sql, why in [
        ("SELECT SUM(amount) FROM orders", "GMV 漏 status"),
        ("SELECT COUNT(*) FROM orders", "订单量漏 status"),
        ("SELECT COUNT(DISTINCT user_id) FROM orders", "活跃用户漏 status"),
    ]:
        r = check_sql(sql)
        assert not r["valid"] and "口径" in r["reason"], f"{why} 应被拦截：{r.get('reason')}"

    # 跨表聚合不得被 orders 类指标误伤（回归点1）
    for sql, why in [
        ("SELECT COUNT(*) FROM products", "数商品"),
        ("SELECT SUM(price) FROM products", "汇总商品单价"),
        ("SELECT COUNT(*) FROM users", "数用户"),
        ("SELECT COUNT(*) FROM categories", "数类目"),
    ]:
        r = check_sql(sql)
        assert r["valid"], f"{why} 不该被 orders 口径拦截：{r.get('reason')}"

    # 带对过滤条件必须放行
    for sql, why in [
        ("SELECT SUM(amount) FROM orders WHERE status='completed'", "GMV 带状态"),
        ("SELECT COUNT(*) FROM orders WHERE status='completed'", "订单量带状态"),
        ("SELECT COUNT(DISTINCT user_id) FROM orders WHERE status='completed'", "活跃用户带状态"),
        ("SELECT status, COUNT(*) FROM orders GROUP BY status", "按状态拆解"),
    ]:
        r = check_sql(sql)
        assert r["valid"], f"{why} 应放行：{r.get('reason')}"
    print("[OK] 指标口径护栏：漏过滤拦截、跨表不误伤（COUNT(*) 表归属 + 去通配）")


def test_semantic_join_normalize():
    """YAML 1.1 把裸键on 解析成布尔 True 的归一化修复回归。"""
    from app.semantic.loader import get_catalog

    cat = get_catalog()
    assert cat.edge_keys, "edge_keys 应非空"
    # 每条声明的 join 都应能解析出 (表,列) 对
    expected = {frozenset({("orders", "user_id"), ("users", "id")}),
                frozenset({("orders", "product_id"), ("products", "id")})}
    assert expected <= set(cat.edge_keys), f"缺少关键 join 边：{cat.edge_keys}"
    # 归一化后 joins 里的键必须是字符串 "on"，不是布尔 True
    for t in cat.tables.values():
        for j in t.joins:
            assert "on" in j and True not in j, f"{t.name} 的 join 键未归一化：{j}"
    print(f"[OK] 语义层 join 归一化：{len(cat.edge_keys)} 条 join 边解析正常")


def test_guardrail_pii_alias_collision():
    """PII 拦截不能靠字段存在性「意外兜底」。

    历史缺陷：alias_map 是扁平 dict，UNION 两个分支复用同一别名时后者覆盖前者，
    PII 检查把 users.o.phone 解析成 orders.phone 而放行。当前库orders 没有
    phone 列，所以当时是靠「未知字段」侥幸拦住的——一旦业务给 orders 加了
    phone（收货电话）就是真实泄露。

    这里用「别名 o 同时出现在 users 和 orders 上」+ 显式别名两种写法，
    断言拦截项里必须出现 PII 字段拦截（而不是只靠字段存在性）。
    """
    from app.guardrails.checks import check_sql

    for sql, why in [
        ("SELECT o.phone FROM users o UNION SELECT o.amount FROM orders o", "UNION 复用别名 o"),
        ("SELECT u.phone AS p FROM users AS u UNION SELECT o.amount FROM orders AS o", "显式 AS 复用"),
    ]:
        r = check_sql(sql)
        assert not r["valid"], f"{why} 应被拦截：{r.get('sql_out')}"
        failed = [c["name"] for c in r["checks"] if not c["passed"]]
        assert any("PII" in n for n in failed), f"{why} 必须由 PII 项拦住，实际拦截项：{failed}"
    print("[OK] PII 别名碰撞：UNION 复用别名由 PII 项拦截（不依赖字段存在性兜底）")


def test_rag_retrieval():
    """RAG 混合检索（零配置本地向量化）：口径/SOP 查询应在 top-3 命中目标文档。"""
    from app.rag import retrieve

    cases = [
        ("GMV 的计算口径是什么", "metrics-handbook"),
        ("退款率为什么变高了", "sop-refund-anomaly"),
        ("大促复盘怎么做", "sop-campaign-review"),
        ("新用户转化怎么算", "sop-new-user"),
        ("各渠道表现对比", "sop-channel-analysis"),
        ("GMV 跌了怎么分析", "sop-gmv-drop"),
    ]
    for q, expect in cases:
        r = retrieve.search(q)
        assert r["ok"], f"{q} 应有命中：{r}"
        hits = [c["source"] for c in r["chunks"]]
        assert any(expect in s for s in hits[:3]), f"{q} top-3 应命中 {expect}，实际：{hits[:3]}"

    r2 = retrieve.search("")
    assert not r2["ok"] and r2.get("reason"), "空查询应结构化失败"
    print("[OK] RAG 混合检索：口径/SOP 六类查询 top-3 命中 + 空查询结构化兜底")


def test_rag_agent_wiring():
    """主 Agent 工具池：rag_retrieve 直连为普通工具（不再有知识子 Agent），
    委派工具 = SQL + viz 两个子 Agent；rag_retrieve 真实检索可用。"""
    from app.agent.sql_agent import make_delegate_tools
    from app.agent.viz_agent import build_viz_agent

    gv = build_viz_agent("http://127.0.0.1:9/v1", "sk-test", "test-model")
    assert gv is not None

    tools = make_delegate_tools("http://127.0.0.1:9/v1", "sk-test", "test-model")
    names = sorted(t.name for t in tools)
    assert set(names) == {"delegate_sql", "delegate_viz", "rag_retrieve", "generate_dashboard"}, names

    rag_tool = next(t for t in tools if t.name == "rag_retrieve")

    async def call():
        return await rag_tool.ainvoke({"query": "GMV 口径"})

    raw = asyncio.run(call())
    data = json.loads(raw) if isinstance(raw, str) else raw
    assert data.get("ok") and data.get("chunks"), f"rag_retrieve 应真实命中知识库：{raw[:200]}"
    print("[OK] 主 Agent 工具池：delegate_sql + delegate_viz（子 Agent）+ rag_retrieve（直连工具，真实检索命中）")


def test_demo_knowledge_scenario():
    """demo 知识库场景：口径类问题由主 Agent 直连 rag_retrieve（真实检索），数据类问题不受影响。"""
    from app.demo.traces import pick_scenario, run_demo

    assert pick_scenario("GMV 怎么算") == "knowledge", "口径类应路由知识库场景"
    assert pick_scenario("GMV 怎么算") != pick_scenario("上个月GMV多少") == "gmv", "数据类问题不应被劫持"

    async def run(msg):
        return [ev async for ev in run_demo(msg)]

    evs = asyncio.run(run("GMV 怎么算"))
    steps = [(e["tool"]) for e in evs if e["type"] == "step"]
    assert "rag_retrieve" in steps, f"主 Agent 应直连 rag_retrieve 真实检索：{steps}"
    assert "delegate_knowledge" not in steps, "不应再出现知识子 Agent 委派"
    rr = [e for e in evs if e["type"] == "result" and e.get("tool") == "rag_retrieve"]
    assert rr and rr[0]["content"]["ok"], "rag_retrieve 应真实命中知识库"
    finals = [e for e in evs if e["type"] == "final"]
    assert finals and "来源" in finals[0]["markdown"], "结论应带来源引用"
    print("[OK] Demo 知识库场景：主 Agent 直连 rag_retrieve（真实检索）→ 带引用结论")


def test_eval_retrieval_items():
    """golden set 的 retrieval 条目离线全跑（不需要 LLM）：judge_retrieval 应全部通过。"""
    from app.eval.runner import judge_retrieval, load_items, run_one_retrieval

    items = [i for i in load_items() if i.get("category") == "retrieval"]
    assert len(items) >= 5, f"retrieval 条目应不少于 5 条：{len(items)}"
    for item in items:
        sources = asyncio.run(run_one_retrieval(item))
        ok, detail = judge_retrieval(item, sources)
        assert ok, f"{item['id']} 检索未命中：{detail}"
    print(f"[OK] Golden Set 检索评测：{len(items)}/{len(items)} 条离线全部命中")


def test_eval_dynamic_dates():
    """Golden Set 日期动态化回归：dates 声明 + {{key.start}}/{{key.end}} 占位符
    必须在评测时用 resolve_date_range_core 动态解析，YAML 里不允许再写死日期。"""
    from app.eval.runner import load_items, resolve_expect_sql
    from app.tools.date_range import resolve_date_range_core

    # 占位符替换：与 resolve_date_range_core 输出一致
    item = {
        "id": "t_dynamic",
        "expect_sql": "SELECT 1 FROM orders WHERE created_at >= '{{lm.start}}' AND created_at < '{{lm.end}}'",
        "dates": {"lm": "上个月"},
    }
    r = resolve_date_range_core("上个月")
    sql = resolve_expect_sql(item)
    assert "{{" not in sql, f"占位符未替换：{sql}"
    assert r["start_date"] in sql and r["end_date"] in sql, f"日期注入与 resolve_date_range 口径不一致：{sql}"

    # 无法解析的时间词必须立刻报错（不允许静默生成错误期望 SQL）
    bad = {"id": "t_bad", "expect_sql": "SELECT 1", "dates": {"x": "大前年"}}
    try:
        resolve_expect_sql(bad)
        raise AssertionError("无法解析的时间词应抛 ValueError")
    except ValueError:
        pass

    # 全量扫描：有日期条件的条目必须走 dates 声明，不允许残留写死日期
    date_items = [i for i in load_items() if i.get("dates")]
    assert len(date_items) >= 15, f"日期动态化条目应不少于 15 条：{len(date_items)}"
    import re
    for it in load_items():
        raw = (it.get("expect_sql") or "")
        dates_in_sql = re.search(r"(created_at|register_date)\s*(>=|<)", raw)
        if dates_in_sql:
            assert it.get("dates"), f"{it['id']} 含日期条件但未声明 dates（写死日期会在跨月后失效）"
    print(f"[OK] Golden Set 日期动态化：{len(date_items)} 条 dates 声明解析正常 + 全量无写死日期")


def test_handle_protocol():
    """句柄协议：落表 -> 凭句柄取数 -> 跨会话拒绝 -> 清理。"""
    from app.tools.scratch import (current_session, discard_created,
                                   fetch_handle_core, persist_result_core,
                                   _created_this_request)

    sql = ("SELECT strftime(created_at,'%Y-%m') AS month, SUM(amount) AS gmv FROM orders "
           "WHERE status='completed' GROUP BY 1 ORDER BY 1")

    tok = current_session.set("smoke-session-1")
    tok_created = _created_this_request.set([])
    try:
        p = persist_result_core(sql)
        assert p["ok"], f"落表失败：{p}"
        handle = p["handle"]
        assert handle.startswith("h_"), f"句柄格式：{handle}"

        d = fetch_handle_core(handle)
        assert d["ok"] and d["row_count"] > 0 and "month" in d["columns"], f"取数失败：{d}"

        # 非法句柄
        bad = fetch_handle_core("users; DROP TABLE orders")
        assert not bad["ok"], "非法句柄应被拒"

        # 跨会话拒绝
        tok2 = current_session.set("smoke-session-2")
        try:
            cross = fetch_handle_core(handle)
            assert not cross["ok"] and "会话" in cross["reason"], f"跨会话句柄应被拒：{cross}"
        finally:
            current_session.reset(tok2)

        discard_created()
        gone = fetch_handle_core(handle)
        assert not gone["ok"], "清理后句柄应失效"
    finally:
        _created_this_request.reset(tok_created)
        current_session.reset(tok)
    print("[OK] 句柄协议：落表/取数/非法拒绝/跨会话拒绝/请求级清理 全部符合预期")


def test_insights():
    """确定性洞察：趋势/环比/异常/集中度都能被纯代码算出。"""
    from app.report.insights import compute_insights

    cols = ["month", "gmv"]
    rows = [{"month": f"2026-0{i}", "gmv": v} for i, v in enumerate([100, 102, 101, 98, 103, 160], 1)]
    ins = compute_insights(cols, rows)
    kinds = {i["kind"] for i in ins}
    assert "trend" in kinds, f"应检出趋势：{kinds}"
    assert "anomaly" in kinds, f"末月 160 应被 z-score 判为异常：{kinds}"
    assert "mom" in kinds, f"环比 50%+ 应有提示：{kinds}"

    ins2 = compute_insights(["region", "gmv"],
                            [{"region": r, "gmv": v} for r, v in
                             [("华东", 900), ("华北", 60), ("华南", 40)]])
    assert any(i["kind"] == "concentration" for i in ins2), f"900/1000 应触发集中度：{ins2}"

    empty = compute_insights(["a"], [])
    assert empty and empty[0]["level"] == "warn", "空结果集应给出 warn 洞察"
    print("[OK] 确定性洞察：趋势/环比/z-score 异常/集中度/空集兜底 全部正常")


def test_report_pipeline():
    """报告链路：fetch_data -> render_chart -> export_excel -> build_report 全链真实运行。"""
    from app.report import store
    from app.report.tools import build_report_core, export_excel_core_checked, fetch_data_core, render_chart_core
    from app.tools.scratch import current_session, persist_result_core

    sql = ("SELECT strftime(created_at,'%Y-%m') AS month, SUM(amount) AS gmv FROM orders "
           "WHERE status='completed' GROUP BY 1 ORDER BY 1")
    tok = current_session.set("smoke-report")
    rep = store.register_report(question="冒烟测试报告")
    tok_rep = store.current_report.set(rep)
    try:
        p = persist_result_core(sql)
        assert p["ok"], f"落表失败：{p}"
        h = p["handle"]

        fd = fetch_data_core(h)
        assert fd["ok"] and fd["insights"], f"取数应有洞察：{fd.get('insights')}"

        c = render_chart_core(h, "line", "month", "gmv", "月度 GMV")
        assert c["ok"] and c["option"]["series"], f"图表生成失败：{c}"

        bad_chart = render_chart_core(h, "line", "month", "nope", "x")
        assert not bad_chart["ok"] and "不在结果集" in bad_chart["reason"], "未知列应结构化报错"

        x = export_excel_core_checked(h, "smoke-gmv")
        assert x["ok"] and x["file"].endswith(".xlsx"), f"Excel 导出失败：{x}"
        from app.config import settings
        assert (settings.reports_dir / x["file"]).exists(), "Excel 文件应落盘"

        br = build_report_core("冒烟测试报告", "测试摘要：GMV 整体平稳。\n\n> 口径：completed 订单。")
        assert br["ok"] and br["url"].startswith("/api/report/rep_"), f"报告组装失败：{br}"
        html_path = settings.reports_dir / f"{rep['id']}.html"
        assert html_path.exists(), "报告 HTML 应落盘"
        html = html_path.read_text(encoding="utf-8")
        assert "echarts" in html.lower(), "报告应内嵌 echarts"
        assert "<script>" in html and "冒烟测试报告" in html
        assert "javascript:" not in html.replace("javascript:".upper(), ""), "消毒检查"
    finally:
        store.current_report.reset(tok_rep)
        current_session.reset(tok)
    print("[OK] 报告链路：取数/洞察/图表/Excel/单文件 HTML 报告 全链真实运行通过")


def test_viz_review_edge():
    """viz 子 Agent 的真代码 reflection：验收函数对空报告必须打回，产出齐全后放行。"""
    from app.agent.viz_agent import _review_feedback
    from app.report import store

    rep = store.register_report(question="反思测试")
    tok = store.current_report.set(rep)
    try:
        # 空报告：没有任何产物 -> 应给返工反馈
        fb = _review_feedback()
        assert fb, "空报告应触发返工反馈"

        # 只有摘要没有图表（数据可绘图）-> 仍应打回补图表
        store.set_data_meta(6, ["gmv", "order_count"], "h_x_1")
        fb2 = _review_feedback()
        assert any("render_chart" in f for f in fb2), f"应提示补图表：{fb2}"

        # 图表登记后 -> 放行
        store.add_chart("测试图", "line", {"series": []})
        assert _review_feedback() == [], "图表齐全后验收应通过"
    finally:
        store.current_report.reset(tok)
    print("[OK] viz reflection：验收打回/补图表后放行（真代码条件边逻辑正确）")


def test_demo_report_scenario():
    """demo 报告场景：句柄协议全链（persist_result -> fetch_data -> build_report）真实运行。"""
    from app.demo.traces import pick_scenario, run_demo

    assert pick_scenario("近 6 个月 GMV 分析报告") == "report", "报告类问题应路由报告场景"
    assert pick_scenario("上个月GMV多少") == "gmv", "普通数据问题不应被报告场景劫持"

    async def run(msg):
        return [ev async for ev in run_demo(msg)]

    evs = asyncio.run(run("近 6 个月 GMV 分析报告"))
    steps = [e["tool"] for e in evs if e["type"] == "step"]
    for t in ["delegate_sql", "validate_sql", "persist_result", "delegate_viz",
              "fetch_data", "render_chart", "export_excel", "build_report"]:
        assert t in steps, f"报告场景缺 {t}：{steps}"

    br = [e for e in evs if e["type"] == "result" and e.get("tool") == "build_report"]
    assert br and br[0]["content"]["ok"], f"build_report 应成功：{br}"

    finals = [e for e in evs if e["type"] == "final"]
    assert finals and "/api/report/" in finals[0]["markdown"], "结论应含报告链接"
    print("[OK] Demo 报告场景：句柄协议全链（落表->取数->图表->Excel->HTML 报告）真实运行")


def test_clarify_detection():
    """口径歧义检测（纯代码，不耗 LLM）：术语命中多个口径不同的指标才算歧义。"""
    from app.agent.clarify import (ambiguous_terms, build_confirm_message,
                                   clarified_terms, detect_ambiguity)
    from langchain_core.messages import HumanMessage, SystemMessage

    assert "销售额" in ambiguous_terms(), "销售额应同时命中 gmv 与 net_sales"

    amb = detect_ambiguity("上个月销售额是多少")
    assert amb and amb["term"] == "销售额", f"销售额应触发澄清：{amb}"
    assert {o["value"] for o in amb["options"]} == {"gmv", "net_sales"}, amb["options"]

    # 无歧义 / 更具体的说法都不打断
    assert detect_ambiguity("上个月GMV多少") is None
    assert detect_ambiguity("近6个月订单量趋势") is None
    assert detect_ambiguity("净销售额趋势") is None, "长术语（净销售额）应覆盖短术语（销售额）"

    # 同会话已确认过口径的术语不再反问（防无限循环）
    note = SystemMessage(content=build_confirm_message("销售额", {"value": "net_sales"}))
    assert "口径确认（HITL）" in note.content and "净销售额" in note.content
    assert clarified_terms([HumanMessage(content="x"), note]) == {"销售额"}
    assert detect_ambiguity("销售额", already={"销售额"}) is None

    # 自由说明作答也能落成确认消息
    free = build_confirm_message("销售额", {"text": "按扣除退款的口径算"})
    assert "按扣除退款的口径算" in free and "「销售额」" in free
    print("[OK] 口径歧义检测：歧义触发 / 无歧义直通 / 长术语覆盖 / 已确认不重问 / 自由作答")


def test_clarify_hitl_interrupt_resume():
    """HITL 图级链路：歧义问题 interrupt 暂停 -> resume 后口径确认注回并推进到 agent。"""
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    from app.agent.graph import build_graph

    # dummy LLM 端点：clarify 在 agent 之前，interrupt 发生时不会真的调 LLM
    g = build_graph("http://127.0.0.1:9/v1", "sk-test", "test-model")
    cfg = {"configurable": {"thread_id": "smoke-clarify-1"}}

    async def run(inputs):
        out, err = [], None
        try:
            async for chunk in g.astream(inputs, config=cfg, stream_mode="updates"):
                out.append(chunk)
        except Exception as e:
            err = e
        return out, err

    # 1) 歧义问题：线程停在 clarify，__interrupt__ 携带问题 payload
    out, err = asyncio.run(run({"messages": [HumanMessage(content="上个月销售额是多少")], "steps": 0}))
    assert err is None, f"中断前不应报错：{err}"
    intrs = [c["__interrupt__"] for c in out if "__interrupt__" in c]
    assert intrs, f"歧义问题应触发 interrupt：{out}"
    payload = getattr(intrs[0][0], "value", None)
    assert isinstance(payload, dict) and payload["term"] == "销售额" and len(payload["options"]) == 2
    snap = g.get_state(cfg)
    assert "clarify" in snap.next, f"线程应停在 clarify 节点：{snap.next}"

    # 2) resume：口径确认 SystemMessage 注回，流程推进到 agent（连 dummy 端点必然失败）
    out2, err2 = asyncio.run(run(Command(resume={"value": "net_sales", "label": "净销售额"})))
    notes = [m for c in out2 if isinstance(c.get("clarify"), dict)
             for m in c["clarify"].get("messages", [])]
    assert notes and "口径确认（HITL）" in notes[0].content and "净销售额" in notes[0].content, \
        f"resume 后应注回口径确认消息：{out2}"
    assert err2 is not None, "dummy 端点应报错（证明已推进到 agent 节点）"

    # 3) hitl=off（评测器场景）：歧义问题也直通不中断
    cfg_off = {"configurable": {"thread_id": "smoke-clarify-3", "hitl": "off"}}

    async def run_off():
        out = []
        try:
            async for chunk in g.astream({"messages": [HumanMessage(content="销售额")], "steps": 0},
                                          config=cfg_off, stream_mode="updates"):
                out.append(chunk)
        except Exception:
            pass   # dummy 端点在 agent 节点报错属预期，只关心没有 __interrupt__
        return out

    out3 = asyncio.run(run_off())
    assert not any("__interrupt__" in c for c in out3), f"hitl=off 时不应中断：{out3}"
    print("[OK] HITL interrupt/resume：暂停payload正确 / resume 注回口径并推进 / 评测器hitl=off直通")


def test_upload_data_flow():
    """上传数据链路：草稿(PII 标记) -> 确认入库 -> 护栏白名单动态生效 -> 可查询 -> 可删除。"""
    from app import uploads
    from app.db import query
    from app.guardrails.checks import check_sql
    from app.semantic.loader import get_catalog

    csv_text = "订单号,金额,下单日期,顾客手机\n1001,199.9,2026-09-01,13800001111\n1002,89.5,2026-09-02,13900002222\n"
    r = uploads.ingest_data_upload("门店订单9月.csv", csv_text.encode("utf-8"))
    assert r["ok"] and r["table"].startswith("up_") and r["rows"] == 2, r
    cols = {c["name"]: c for c in r["columns"]}
    assert cols["顾客手机"]["pii"], "手机列应被 PII 启发式标记"
    assert not cols["金额"]["pii"], "金额列不应误标 PII"

    rc = uploads.confirm_upload(r["draft_id"])
    assert rc["ok"], rc
    t = rc["table"]

    # 语义层合并：上传表进入目录（= 护栏白名单），未知表拦截对新表自动生效
    cat = get_catalog()
    assert t in cat.tables, f"上传表应进入语义层目录：{sorted(cat.tables)[:5]}"
    g = check_sql(f'SELECT "订单号", "金额" FROM "{t}"')
    assert g["valid"], f"上传表普通查询应放行：{g}"
    assert "LIMIT" in g["sql_out"], "上传表查询同样被强制 LIMIT"
    g2 = check_sql(f'SELECT "顾客手机" FROM "{t}"')
    assert not g2["valid"] and "隐私" in g2["reason"], f"上传表 PII 列应被拦截：{g2}"

    # 执行层：TEMP 视图可查（免前缀，与主库表无差别）
    c, rows = query(f'SELECT "订单号", "金额" FROM "{t}"')
    assert len(rows) == 2 and rows[0][1] == 199.9, rows

    # 删除后白名单即时收缩
    rd = uploads.remove_upload(t)
    assert rd["ok"], rd
    assert t not in get_catalog().tables, "删除后应退出语义层目录"
    print(f"[OK] 上传数据链路：草稿/PII标记/确认入库/{t}白名单+LIMIT+隐私拦截/查询/删除 全通")

    # GBK 编码兜底
    r_gbk = uploads.ingest_data_upload("gbk.csv", "菜品,价格\n宫保鸡丁,38\n".encode("gbk"))
    assert r_gbk["ok"] and r_gbk["columns"][0]["name"] == "菜品", r_gbk

    # XLSX 路线
    import io as _io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["dish", "price"])
    ws.append(["麻婆豆腐", 38.0])
    buf = _io.BytesIO()
    wb.save(buf)
    r_x = uploads.ingest_data_upload("menu.xlsx", buf.getvalue())
    assert r_x["ok"] and r_x["rows"] == 1, r_x
    print("[OK] 上传编码兜底：GBK CSV 与 XLSX 均可解析")


def test_upload_doc_flow():
    """上传文档链路：提取文本 -> 用户语料 -> RAG 立即可检索 -> 删除。"""
    from app import uploads
    from app.rag import retrieve
    from app.rag.ingest import ensure_index

    marker = "xyzzy_marker_9527"
    text = f"门店运营须知：本店招牌菜是松鼠桂鱼。内部标记 {marker}，仅用于检索回归测试。"
    r = uploads.ingest_doc_upload("store_notice.txt", text.encode("utf-8"))
    assert r["ok"] and r["chunks"] >= 1, r

    res = retrieve.search(f"{marker} 招牌菜")
    assert res["ok"], res
    srcs = [c["source"] for c in res["chunks"][:3]]
    assert any(r["file"] in s for s in srcs), f"上传文档应可检索：{srcs}"

    rd = uploads.remove_upload(r["file"])
    assert rd["ok"] and rd["kind"] == "doc", rd
    ensure_index()   # 指纹变化重建，把测试文档从索引中清掉
    print("[OK] 上传文档链路：文本提取/入语料/检索命中/删除重建 全通")

    # PDF 路线：空白页（图片型/扫描版代理）应明确拒绝而非卡死
    from pypdf import PdfWriter
    import io as _io2
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=612, height=792)
    buf2 = _io2.BytesIO()
    w.write(buf2)
    r_pdf = uploads.ingest_doc_upload("blank.pdf", buf2.getvalue())
    assert not r_pdf["ok"] and "扫描版" in r_pdf["reason"], r_pdf
    print("[OK] PDF 兜底：无文本 PDF 明确拒绝（提示扫描版不支持），不会卡住前端")


def test_fast_lane_queue():
    """快车道直播：子 Agent 过程事件实时入队（live SSE 消费），token 碎片只走快车道；
    列表总线（评测器消费）保持批量语义且不含 sub_delta。伪图混吐 dict 与 (mode, chunk)。"""
    import asyncio
    from app.agent.events import subagent_events, subagent_queue
    from app.agent.sql_agent import run_sql_agent

    class FakeMsg:
        def __init__(self, *, name="", content="", tool_calls=None):
            self.name = name
            self.content = content
            self.tool_calls = tool_calls

    class FakeToken:
        def __init__(self, text, reasoning=None):
            self.content = text
            self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}

    class FakeGraph:
        """伪子图：agent 播报+工具调用 -> tools 结果 -> agent 结论；
        每步之前混入 messages 模式的 token 碎片（元数据 langgraph_node=agent）。"""

        def __init__(self):
            self.updates = [
                ("messages", (FakeToken("正在", reasoning="先想清楚口径"), {"langgraph_node": "agent"})),
                ("messages", (FakeToken("确认口径"), {"langgraph_node": "agent"})),
                ("messages", (FakeToken("工具内部碎片"), {"langgraph_node": "tools"})),  # 非 agent 节点应被过滤
                ("agent", {"messages": [FakeMsg(content="先查一下", tool_calls=[{"name": "run_sql", "args": {}}])]}),
                ("tools", {"messages": [FakeMsg(name="run_sql", content='{"ok": true, "rows": []}')]}),
                ("agent", {"messages": [FakeMsg(content="结论：GMV 为 100")]}),
            ]

        async def astream(self, inputs, config=None, stream_mode=None):
            for mode, payload in self.updates:
                # messages 模式产出 (mode, (chunk, meta)) 元组；updates 模式直接吐 dict（兼容旧桩形状）
                if mode == "messages":
                    yield (mode, payload)
                else:
                    yield {mode: payload}

    async def one():
        bus: list = []
        q: asyncio.Queue = asyncio.Queue()
        tok_b = subagent_events.set(bus)
        tok_q = subagent_queue.set(q)
        try:
            report = await run_sql_agent(FakeGraph(), "测试任务")
        finally:
            subagent_events.reset(tok_b)
            subagent_queue.reset(tok_q)
        queued = []
        while not q.empty():
            queued.append(q.get_nowait())
        return report, bus, queued

    report, bus, queued = asyncio.run(one())
    assert report["final"] == "结论：GMV 为 100", report

    kinds_bus = [e["type"] for e in bus]
    assert kinds_bus == ["thinking", "step", "result"], f"列表总线应保持批量语义：{kinds_bus}"
    assert all(e["type"] != "sub_delta" for e in bus), "sub_delta 碎片不得进列表总线（评测不需要）"

    sub = [e for e in queued if e["type"] == "sub_delta"]
    assert [e["text"] for e in sub] == ["正在", "确认口径"], f"agent 节点 token 应入快车道：{sub}"
    assert all("工具内部碎片" != e.get("text") for e in queued), "非 agent 节点的 token 必须被过滤"

    live_kinds = [e["type"] for e in queued]
    for k in ("thinking", "step", "result"):
        assert k in live_kinds, f"过程事件应实时入快车道：{live_kinds}"

    rs = [e for e in queued if e["type"] == "reasoning"]
    assert [e["text"] for e in rs] == ["先想清楚口径"], f"思考链应入快车道：{rs}"
    assert all(e["type"] != "reasoning" for e in bus), "思考链不得进列表总线（评测不需要）"
    print("[OK] 快车道直播：过程事件实时入队 + token/思考链只走快车道 + 列表总线批量语义不变")


def test_samples_injection():
    """维度值样本注入：低基数文本列带真实取值，PII/高基数列不受影响。"""
    from app.semantic.loader import get_catalog
    from app.tools.retrieval import search_schema_core

    cat = get_catalog()
    regions = cat.tables.get("regions")
    assert regions is not None, "regions 表应存在（先跑 seed_data 与 gen_samples）"
    sample = next((c.sample for c in regions.columns if c.name == "name"), None)
    assert sample and len(sample) >= 4, f"regions.name 应带全部大区取值：{sample}"
    assert "华东" in sample, f"取值应来自真实数据：{sample}"

    r = search_schema_core("订单表")
    t = (r.get("tables") or [{}])[0]
    status = next((c for c in t.get("columns", []) if c["name"] == "status"), {})
    assert "completed" in (status.get("sample") or []), "search_schema 应透传样本值"

    users = cat.tables.get("users")
    phone = next(c for c in users.columns if c.name == "phone")
    assert not phone.sample, "PII 列禁止携带样本值"
    print("[OK] 维度值样本注入：真实取值经 search_schema 透传给 LLM，PII 列不受影响")


def test_profile_table():
    """数据质量体检：白名单拦截 / PII 列排除 / 确定性提示。"""
    from app.tools.profile import profile_table_core

    r = profile_table_core("orders")
    assert r["ok"] and r["rows"] > 0, f"orders 体检应成功：{r.get('reason')}"
    amt = next((c for c in r["columns"] if c["name"] == "amount"), {})
    assert "null_rate" in amt and "avg" in amt, f"数值列应有分布统计：{amt}"

    r2 = profile_table_core("users")
    names = [c["name"] for c in r2["columns"]]
    assert "phone" not in names and "email" not in names, "PII 列不得进入画像"

    r3 = profile_table_core("nope")
    assert not r3["ok"] and "未知表" in r3["reason"], "未知表应被拒绝"

    from app.tools import all_tools
    assert "profile_table" in [t.name for t in all_tools()], "第 9 个工具应注册进工具池"
    print("[OK] 数据质量体检：行数/空值/异常统计、PII 排除、未知表拦截、工具注册 全通过")


def test_artifacts():
    """产物资产化：登记 -> 按会话查询 -> demo 报告场景自动登记。"""
    import asyncio

    from app.artifacts import list_artifacts, register_artifact

    art = register_artifact("smoke-artifacts", "report", "冒烟测试报告",
                            url="/api/report/rep_00000000", excel_url="", meta={"charts": 1})
    lst = list_artifacts("smoke-artifacts")
    assert any(a["id"] == art["id"] and a["title"] == "冒烟测试报告" for a in lst)

    # demo 报告场景收尾应产出 artifact 事件（与会话绑定登记）
    from app.tools.scratch import current_session
    from app.demo.traces import scenario_report

    async def run():
        tok = current_session.set("smoke-artifacts-demo")
        arts = []
        try:
            async for ev in scenario_report("冒烟报告"):
                if ev.get("type") == "artifact":
                    arts.append(ev)
        finally:
            current_session.reset(tok)
        return arts

    arts = asyncio.run(run())
    assert arts and arts[0].get("url", "").startswith("/api/report/"), f"demo 应产出 artifact 事件：{arts}"
    assert any(a["session_id"] == "smoke-artifacts-demo" for a in list_artifacts("smoke-artifacts-demo"))
    print("[OK] 产物资产化：登记/查询/demo 场景自动登记 全通过")


def test_plan_hitl():
    """计划审批 HITL：hitl=off 自动批准 / interrupt payload / resume 修改生效。"""
    import asyncio
    import json as _json
    from typing import Annotated as _Ann, TypedDict as _TD

    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    from app.agent.graph import _make_propose_plan, build_graph

    propose_plan = _make_propose_plan()

    async def run_flow():
        # off：自动批准
        r = _json.loads(await propose_plan.ainvoke(
            {"steps": [{"tool": "delegate_sql", "task": "查数"}]},
            config={"configurable": {"hitl": "off"}}))
        assert r["ok"] and r["approved"] and r.get("auto"), f"hitl=off 应自动批准：{r}"

        # 非白名单工具剔除
        r2 = _json.loads(await propose_plan.ainvoke(
            {"steps": [{"tool": "run_sql", "task": "x"}, {"tool": "delegate_sql", "task": "查数"}]},
            config={"configurable": {"hitl": "off"}}))
        assert [s["tool"] for s in r2["plan"]] == ["delegate_sql"], "计划步骤应收紧为白名单委派工具"

        # interrupt / resume
        class S(_TD):
            x: _Ann[list, lambda a, b: a + b]

        async def node(state, config):
            out = await propose_plan.ainvoke(
                {"steps": [{"tool": "delegate_sql", "task": "查询"}, {"tool": "delegate_viz", "task": "报告"}]},
                config=config)
            return {"x": [out]}

        g = StateGraph(S)
        g.add_node("n", node)
        g.add_edge(START, "n")
        g.add_edge("n", END)
        mg = g.compile(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "smoke-plan"}}
        res = await mg.ainvoke({"x": []}, config=cfg)
        intr = (res.get("__interrupt__") or [None])[0]
        payload = getattr(intr, "value", None)
        assert payload and payload.get("kind") == "plan" and len(payload["steps"]) == 2, \
            f"interrupt payload 应为计划：{payload}"

        res2 = await mg.ainvoke(Command(resume={"action": "edit", "text": "只看华南区"}), config=cfg)
        out = _json.loads(res2["x"][0])
        assert out["ok"] and out["approved"] and out.get("modified"), f"resume 修改应生效：{out}"
        assert "只看华南区" in out["message"]

        # 主图带 propose_plan 编译
        build_graph("https://example.invalid", "sk-smoke", "smoke-model")

    asyncio.run(run_flow())
    print("[OK] 计划审批 HITL：自动批准/白名单收紧/interrupt payload/resume 修改/主图编译 全通过")


def test_coverage_note():
    """数据覆盖区间：data_coverage 按需工具（第 10 个）——结构化区间 + 工具注册。

    方案 A：覆盖信息不再常驻注入提示词，由 AI 在查询 0 行时按需调用，
    调用那一刻现算（活库新数据/新上传表自动生效，无快照过时问题）。

    自包含：全新 clone / CI 环境没有任何上传表，这里临时建一张带日期列的
    up_ 夹具表，验证「刚上传的表立刻进入覆盖区间」，跑完删除，
    不依赖开发者本机的历史上传数据。
    """
    import asyncio
    import json as _json

    from app import uploads
    from app.tools.data_coverage import data_coverage, data_coverage_core

    created = None
    if not any(t["table"].startswith("up_") for t in data_coverage_core()["tables"]):
        csv_text = "金额,下单日期\n199.9,2026-09-01\n89.5,2026-09-02\n"
        r0 = uploads.ingest_data_upload("coverage_fixture.csv", csv_text.encode("utf-8"))
        assert r0["ok"], f"夹具上传失败：{r0}"
        rc = uploads.confirm_upload(r0["draft_id"])
        assert rc["ok"], f"夹具入库失败：{rc}"
        created = rc["table"]

    try:
        r = data_coverage_core()
        assert r["ok"] and r["tables"], f"应返回各表覆盖区间：{r}"
        names = [t["table"] for t in r["tables"]]
        assert "orders" in names, "内置表应在列"
        assert any(n.startswith("up_") for n in names), "上传表（含临时夹具）应在列"
        orders = next(t for t in r["tables"] if t["table"] == "orders")
        assert orders["lo"] and orders["hi"], "orders 应有区间"
        up = next(t for t in r["tables"] if t["table"].startswith("up_"))
        assert up["lo"] and up["hi"], "上传表应有区间"

        r2 = _json.loads(asyncio.run(data_coverage.ainvoke({}, config={"configurable": {}})))
        assert r2["ok"] and "note" in r2, f"工具层应正常返回：{r2}"

        from app.tools import all_tools
        assert "data_coverage" in [t.name for t in all_tools()], "data_coverage 应注册进工具池"
        print("[OK] 数据覆盖区间：按需工具/结构化区间/上传表覆盖/工具注册 全通过")
    finally:
        if created:
            uploads.remove_upload(created)


def test_memory_prefs():
    """语义偏好记忆：保存/覆盖/注入提示词/口径反问跳过/HITL 自动记录/save_memory 工具。

    全程用临时库（重绑 memory.DB_PATH），不碰真实用户记忆。
    """
    import asyncio
    import json as _json
    from pathlib import Path as _Path

    import app.memory as memory

    memory.DB_PATH = memory.settings.root / "data" / "tmp" / "memory_smoke.db"
    try:
        memory.clear()
        memory.save_pref("caliber", "销售额",
                         "净销售额｜口径：SUM(orders.amount)（必须带 orders.status = completed）",
                         source="hitl")
        memory.save_pref("workflow", "报告流程", "出报告前先做数据质量体检", source="chat")
        memory.save_pref("profile", "职责范围", "负责华南区运营", source="manual")
        assert len(memory.list_prefs()) == 3 and memory.pref_keys() == {"销售额"}

        # 注入提示：三类齐全且带管理指引
        note = memory.note_for_prompt()
        assert "净销售额" in note and "数据质量体检" in note and "华南区" in note
        assert "用户偏好记忆" in note

        # 口径已记住 → 反问永久跳过
        from app.agent.clarify import detect_ambiguity, record_caliber_choice

        assert detect_ambiguity("上个月销售额多少", already=memory.pref_keys()) is None

        # HITL 自动记录：明确选择才记；数据范围/自由作答不入库
        before = len(memory.list_prefs())
        record_caliber_choice("销售额", {"value": "net_sales", "label": "净销售额"})
        record_caliber_choice("数据范围", {"value": "up_x", "label": "x"})
        record_caliber_choice("销售额", {"value": None, "text": "随口说说"})
        assert len(memory.list_prefs()) == before, "数据范围/自由作答不应入库"
        assert "净销售额" in next(p["value"] for p in memory.list_prefs() if p["key"] == "销售额")

        # 覆盖语义 + save_memory 工具（chat 来源）
        memory.save_pref("caliber", "销售额", "GMV", source="manual")
        assert [p["value"] for p in memory.list_prefs() if p["key"] == "销售额"] == ["GMV"]

        from app.memory import make_save_memory_tool

        tool = make_save_memory_tool()
        r = _json.loads(asyncio.run(tool.ainvoke(
            {"category": "workflow", "key": "默认时间", "value": "近12个月"},
            config={"configurable": {}})))
        assert r["ok"] and r["pref"]["source"] == "chat", f"save_memory 应成功：{r}"
    finally:
        memory.clear()
        _Path(memory.DB_PATH).unlink(missing_ok=True)
    print("[OK] 语义偏好记忆：保存/覆盖/注入提示词/反问跳过/HITL 自动记录/save_memory 工具 全通过")


if __name__ == "__main__":
    # 冒烟可重复性：清掉上次运行残留的临时句柄库（adhoc 会话计数会累积到上限）
    from pathlib import Path
    import sys as _sys
    _sys.path.insert(0, ".")
    from app.config import settings as _settings
    if _settings.scratch_path.exists():
        Path(_settings.scratch_path).unlink()
        print(f"[准备] 已清理临时句柄库 {_settings.scratch_path}")

    # 冒烟可重复性：清掉上次运行留下的测试产物记录（不碰真实会话数据）
    try:
        import sqlite3 as _sq
        _adb = _settings.root / "data" / "artifacts.db"
        if _adb.exists():
            _c = _sq.connect(str(_adb))
            _c.execute("DELETE FROM artifacts WHERE session_id LIKE 'smoke-%'")
            _c.commit()
            _c.close()
    except Exception:
        pass

    # 偏好记忆隔离：冒烟全程重定向到临时库——口径 HITL 测试的选择会被
    # record_caliber_choice 记入记忆，不能污染真实用户记忆
    import app.memory as _memory
    _memory.DB_PATH = _settings.root / "data" / "tmp" / "memory_smoke.db"
    try:
        _Path(_memory.DB_PATH).unlink(missing_ok=True)
    except Exception:
        pass

    test_guardrails()
    test_guardrail_limit_bypass()
    test_guardrail_source_whitelist()
    test_guardrail_join_keys()
    test_guardrail_metric_caliber()
    test_guardrail_pii_alias_collision()
    test_semantic_join_normalize()
    test_retrieval()
    test_rag_retrieval()
    test_sql_tools()
    test_demo_traces()
    test_demo_knowledge_scenario()
    test_demo_dates_dynamic()
    test_graph_compile()
    test_rag_agent_wiring()
    test_event_bus()
    test_fast_lane_queue()
    test_handle_protocol()
    test_insights()
    test_report_pipeline()
    test_viz_review_edge()
    test_demo_report_scenario()
    test_clarify_detection()
    test_clarify_hitl_interrupt_resume()
    test_eval_retrieval_items()
    test_eval_dynamic_dates()
    test_upload_data_flow()
    test_upload_doc_flow()
    test_samples_injection()
    test_profile_table()
    test_artifacts()
    test_plan_hitl()
    test_memory_prefs()
    test_coverage_note()
    test_asr_config()
    print("\n全部冒烟测试通过 ✔")
