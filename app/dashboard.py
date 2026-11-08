"""看板数据（确定性仪表盘）。

与 Agent 路径的分工 —— 同一个数据底座，两种执行方式：

- Agent：LLM 生成 SQL → AST 护栏 → 执行
  探索式问答，慢（秒级到几十秒）、有 token 成本、同一问题两次结果可能不同。
- 看板：本文件里的固定 SQL → 直接执行
  确定式仪表盘，秒开、零 token、结果可复现。

两条路径共用同一套语义层口径（GMV 必带 status='completed'、客单价 = GMV/订单量、
退款率分母是全部订单金额）和同一个只读连接。

看板 SQL 是本文件里的白名单常量：没有模型参与、没有用户输入拼接，
因此刻意不经过 guardrails.check_sql —— 这是分路径，不是漏护栏。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .db import query

# 可选区间：近 1/2/3/6/12/24/36 个完整自然月（预设；自定义起止走 start/end 参数）
RANGE_MONTHS = {"1m": 1, "2m": 2, "3m": 3, "6m": 6, "12m": 12, "24m": 24, "36m": 36}
RANGE_LABELS = {"1m": "近 1 个月", "2m": "近 2 个月", "3m": "近 3 个月", "6m": "近 6 个月",
                "12m": "近 12 个月", "24m": "近 24 个月", "36m": "近 36 个月"}


def _add_months(d: dt.date, n: int) -> dt.date:
    total = d.year * 12 + (d.month - 1) + n
    y, m0 = divmod(total, 12)
    return dt.date(y, m0 + 1, 1)


def _anchor_month() -> dt.date:
    """数据锚点 = 库里最后一条订单所在的月份，而不是 today()。

    演示数据是「截至上月的 12 个完整自然月」，且窗口随运行日期滚动。
    若用 today()，每月 1~2 号会出现「本月还没有数据 → 看板全是 0」的假故障。
    以数据自身的最后一个月为锚点，任何一天打开都是完整的 N 个自然月。
    """
    _, rows = query("SELECT MAX(created_at) FROM orders")
    last = rows[0][0]
    if last is None:
        raise RuntimeError("orders 表为空，请先运行: python -m app.seed_data")
    if isinstance(last, dt.datetime):
        last = last.date()
    return last.replace(day=1)


def _window(range_key: str, anchor: dt.date,
            start: str | None = None, end: str | None = None) -> tuple[int, dt.date, dt.date, dt.date]:
    """返回 (月数, 本期起, 本期止[开区间], 上期起)。

    自定义模式（start/end 同时给出，YYYY-MM-DD）：窗口 = 用户选的起止（止日含当天），
    上期 = 紧贴本期的等长区间；月数按天数折算（展示用）。预设模式走锚点滚动。
    """
    if start and end:
        cs = dt.date.fromisoformat(start)
        ce = dt.date.fromisoformat(end) + dt.timedelta(days=1)
        if ce <= cs:
            raise ValueError("自定义时间范围的结束日期必须晚于开始日期")
        days = (ce - cs).days
        ps = cs - dt.timedelta(days=days)
        return max(1, round(days / 30.44)), cs, ce, ps
    months = RANGE_MONTHS.get(range_key, 6)
    cur_start = _add_months(anchor, -(months - 1))
    cur_end = _add_months(anchor, 1)
    prev_start = _add_months(cur_start, -months)
    return months, cur_start, cur_end, prev_start


def _q(ident: str) -> str:
    """标识符安全引用：表名/列名来自 CSV 表头，可能含特殊字符或保留字。"""
    return '"' + str(ident).replace('"', '""') + '"'


# 一条 SQL 同时算本期与上期的 8 个基数，环比在 Python 侧算。
# WHERE 已把范围收在 [上期起, 本期止)，所以 >=cur_start 即本期、<cur_start 即上期。
_KPI_SQL = """
SELECT
  COALESCE(SUM(amount) FILTER (WHERE status = 'completed' AND created_at >= TIMESTAMP '{cs}'), 0) AS gmv_cur,
  COALESCE(SUM(amount) FILTER (WHERE status = 'completed' AND created_at <  TIMESTAMP '{cs}'), 0) AS gmv_prev,
  COUNT(*)      FILTER (WHERE status = 'completed' AND created_at >= TIMESTAMP '{cs}')          AS ord_cur,
  COUNT(*)      FILTER (WHERE status = 'completed' AND created_at <  TIMESTAMP '{cs}')          AS ord_prev,
  COALESCE(SUM(amount) FILTER (WHERE created_at >= TIMESTAMP '{cs}'), 0)                        AS amt_cur,
  COALESCE(SUM(amount) FILTER (WHERE status = 'refunded' AND created_at >= TIMESTAMP '{cs}'), 0) AS ref_cur,
  COALESCE(SUM(amount) FILTER (WHERE created_at < TIMESTAMP '{cs}'), 0)                         AS amt_prev,
  COALESCE(SUM(amount) FILTER (WHERE status = 'refunded' AND created_at < TIMESTAMP '{cs}'), 0)  AS ref_prev
FROM orders
WHERE created_at >= TIMESTAMP '{ps}' AND created_at < TIMESTAMP '{ce}'
"""

_TREND_SQL = """
SELECT strftime(created_at, '%Y-%m') AS month,
       COALESCE(SUM(amount) FILTER (WHERE status = 'completed'), 0) AS gmv,
       COUNT(*) FILTER (WHERE status = 'completed') AS ord
FROM orders
WHERE created_at >= TIMESTAMP '{cs}' AND created_at < TIMESTAMP '{ce}'
GROUP BY 1 ORDER BY 1
"""

_CHANNEL_SQL = """
SELECT c.name AS name, COALESCE(SUM(o.amount), 0) AS value
FROM orders o JOIN channels c ON o.channel_id = c.id
WHERE o.status = 'completed'
  AND o.created_at >= TIMESTAMP '{cs}' AND o.created_at < TIMESTAMP '{ce}'
GROUP BY 1 ORDER BY 2 DESC
"""

_REGION_SQL = """
SELECT r.name AS name, COALESCE(SUM(o.amount), 0) AS value
FROM orders o JOIN regions r ON o.region_id = r.id
WHERE o.status = 'completed'
  AND o.created_at >= TIMESTAMP '{cs}' AND o.created_at < TIMESTAMP '{ce}'
GROUP BY 1 ORDER BY 2 DESC
"""

_PRODUCT_SQL = """
SELECT p.name AS name, COALESCE(SUM(o.amount), 0) AS value, COUNT(*) AS ord
FROM orders o JOIN products p ON o.product_id = p.id
WHERE o.status = 'completed'
  AND o.created_at >= TIMESTAMP '{cs}' AND o.created_at < TIMESTAMP '{ce}'
GROUP BY 1 ORDER BY 2 DESC
LIMIT {limit}
"""


def _delta(cur: float, prev: float) -> float | None:
    """环比。上期为 0 时返回 None（分母无意义），前端显示「无上期对比」。"""
    if not prev:
        return None
    return (cur - prev) / prev


def _round(v, nd: int = 2) -> float:
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return 0.0


def _kpis(cs: dt.date, ce: dt.date, ps: dt.date) -> list[dict[str, Any]]:
    _, rows = query(_KPI_SQL.format(cs=cs, ce=ce, ps=ps))
    gmv_cur, gmv_prev, ord_cur, ord_prev, amt_cur, ref_cur, amt_prev, ref_prev = rows[0]

    gmv_cur, gmv_prev = float(gmv_cur or 0), float(gmv_prev or 0)
    amt_cur, amt_prev = float(amt_cur or 0), float(amt_prev or 0)
    ref_cur, ref_prev = float(ref_cur or 0), float(ref_prev or 0)

    aov_cur = gmv_cur / ord_cur if ord_cur else 0.0
    aov_prev = gmv_prev / ord_prev if ord_prev else 0.0
    rr_cur = ref_cur / amt_cur if amt_cur else 0.0
    rr_prev = ref_prev / amt_prev if amt_prev else 0.0

    return [
        {
            "key": "gmv", "label": "GMV", "fmt": "money",
            "value": _round(gmv_cur), "delta": _delta(gmv_cur, gmv_prev),
            "delta_type": "pct", "good_when_up": True,
            "hint": "已完成订单金额合计，不含退款",
        },
        {
            "key": "orders", "label": "订单量", "fmt": "int",
            "value": int(ord_cur or 0), "delta": _delta(ord_cur, ord_prev),
            "delta_type": "pct", "good_when_up": True,
            "hint": "已完成订单笔数",
        },
        {
            "key": "aov", "label": "客单价", "fmt": "money2",
            "value": _round(aov_cur), "delta": _delta(aov_cur, aov_prev),
            "delta_type": "pct", "good_when_up": True,
            "hint": "GMV ÷ 订单量",
        },
        {
            "key": "refund_rate", "label": "退款率", "fmt": "percent",
            "value": _round(rr_cur, 4),
            # 比率型指标的环比用「百分点差」：9.5% 对 6.5% 是 +3.0pp，
            # 而不是 +46%（相对变化在这种小基数上会放大到失真）
            "delta": (rr_cur - rr_prev) if amt_prev else None,
            "delta_type": "pp", "good_when_up": False,
            "hint": "退款金额 ÷ 全部订单金额",
        },
    ]


def build_dashboard(range_key: str = "6m", source: str | None = None,
                    start: str | None = None, end: str | None = None) -> dict[str, Any]:
    if source:
        try:
            return _build_upload_dashboard(range_key, source, start, end)
        except (LookupError, ValueError, RuntimeError) as e:
            # 上传表被删 / 没标注时间列和金额列 → 回退内置源。
            # 直接抛错会让整页 404，而用户只是打开看板，不该因此看不到任何东西。
            out = build_dashboard(range_key, None, start, end)
            out["source"] = "builtin"
            out["source_fallback"] = True
            out["note"] = f"数据源 {source} 不可用（{e}），已回退内置演示数据"
            return out

    months, cs, ce, ps = _window(range_key, _anchor_month(), start, end)

    _, trend_rows = query(_TREND_SQL.format(cs=cs, ce=ce))
    _, ch_rows = query(_CHANNEL_SQL.format(cs=cs, ce=ce))
    _, rg_rows = query(_REGION_SQL.format(cs=cs, ce=ce))
    _, pd_rows = query(_PRODUCT_SQL.format(cs=cs, ce=ce, limit=6))

    return {
        "range": range_key,
        "source": "builtin",
        "title": "销售运营看板",
        "months": months,
        "ranges": [{"key": k, "label": RANGE_LABELS[k]} for k in RANGE_MONTHS],
        "sources": _eligible_sources(),
        "cards": [
            {"slot": "trend", "kind": "line", "wide": True,
             "title": "GMV 与订单量趋势", "sub": "按月 · 双轴"},
            {"slot": "channels", "kind": "pie", "title": "渠道占比", "sub": "按 GMV"},
            {"slot": "regions", "kind": "bar", "title": "地区排行", "sub": "按 GMV"},
            {"slot": "products", "kind": "products", "title": "热销商品 TOP 6", "sub": "按 GMV",
             "name_label": "商品", "val_label": "GMV"},
        ],
        "window": {
            "start": cs.isoformat(),
            "end": (ce - dt.timedelta(days=1)).isoformat(),
            "label": f"{cs:%Y-%m} ~ {(ce - dt.timedelta(days=1)):%Y-%m}",
        },
        "kpis": _kpis(cs, ce, ps),
        "charts": {
            "trend": {
                "months": [r[0] for r in trend_rows],
                "gmv": [_round(r[1]) for r in trend_rows],
                "orders": [int(r[2] or 0) for r in trend_rows],
            },
            "channels": [{"name": r[0], "value": _round(r[1])} for r in ch_rows],
            "regions": [{"name": r[0], "value": _round(r[1])} for r in rg_rows],
            "products": [
                {"name": r[0], "value": _round(r[1]), "orders": int(r[2] or 0)} for r in pd_rows
            ],
        },
        "note": "口径与 Agent 查数同源：GMV 只算 completed、退款率分母为全部订单金额",
    }


# ---------- 上传数据看板：按用户标注的列角色（HITL）生成同构看板 ----------

def _eligible_sources() -> list[dict[str, Any]]:
    """看板页数据源列表：内置演示库 + 全部上传表。

    ready=True 表示已有 AI spec 或完整列角色（点选即看）；
    ready=False 的表也列出（前端显示「点此生成」），用户在看板页就能
    触发 AI 生成，不用跑去数据页找按钮——可发现性优先。
    """
    from .uploads import list_uploads

    out = [{"key": "builtin", "label": "内置演示数据", "smart": False, "ready": True, "time": ""}]
    for t in list_uploads()["tables"]:
        has_spec = bool(t.get("spec"))
        r = t.get("roles") or {}
        ready = has_spec or bool(r.get("time") and r.get("amount"))
        out.append({
            "key": t["name"],
            "label": t.get("display") or t["name"],
            "smart": has_spec,
            "ready": ready,
            "time": t.get("spec_at") or t.get("uploaded_at") or "",
        })
    return out


def _anchor_from(table: str, col: str) -> dt.date:
    """上传表的锚点：时间列最大值所在月份。TRY_CAST 兼容字符串日期列。"""
    _, rows = query(f"SELECT MAX(TRY_CAST({_q(col)} AS TIMESTAMP)) FROM {_q(table)}")
    last = rows[0][0]
    if last is None:
        raise ValueError(f"表 {table} 的时间列「{col}」没有可解析的日期值")
    if isinstance(last, dt.datetime):
        last = last.date()
    return last.replace(day=1)


def _build_upload_dashboard(range_key: str, table: str,
                            start: str | None = None, end: str | None = None) -> dict[str, Any]:
    from .uploads import list_uploads

    hit = next((t for t in list_uploads()["tables"] if t["name"] == table), None)
    if hit is None:
        raise LookupError(f"上传表不存在：{table}")

    # AI 规划的 spec 优先：LLM 只在生成时跑一次，这里全是确定性 SQL。
    # spec 渲染出现任何数据异常（列被删/类型变化）都落回通用模板，不让看板白屏。
    if hit.get("spec"):
        try:
            return _render_spec_dashboard(range_key, hit, start, end)
        except (LookupError, ValueError, RuntimeError):
            pass

    roles = hit.get("roles") or {}
    tcol, acol = roles.get("time"), roles.get("amount")
    if not (tcol and acol):
        raise ValueError(
            "这张表没有标注时间列/金额列，无法生成看板（重新上传时在确认面板标注即可）")
    dims = (roles.get("dims") or [])[:3]

    months, cs, ce, ps = _window(range_key, _anchor_from(table, tcol), start, end)
    T, A, TBL = _q(tcol), _q(acol), _q(table)

    # TRY_CAST 一次归一：时间列无论是 TIMESTAMP 还是 ISO 字符串都统一成 ts；
    # 窗口过滤收在 [上期起, 本期止)，>=cs 即本期、<cs 即上期，与内置看板同构。
    base = (f"SELECT TRY_CAST({T} AS TIMESTAMP) AS ts, {A} AS amt FROM {TBL} "
            f"WHERE TRY_CAST({T} AS TIMESTAMP) IS NOT NULL")

    _, kpi_rows = query(f"""
        SELECT
          COALESCE(SUM(amt) FILTER (WHERE ts >= TIMESTAMP '{cs}'), 0)  AS amt_cur,
          COALESCE(SUM(amt) FILTER (WHERE ts <  TIMESTAMP '{cs}'), 0)  AS amt_prev,
          COUNT(*)    FILTER (WHERE ts >= TIMESTAMP '{cs}')            AS cnt_cur,
          COUNT(*)    FILTER (WHERE ts <  TIMESTAMP '{cs}')            AS cnt_prev,
          COUNT(DISTINCT CAST(ts AS DATE)) FILTER (WHERE ts >= TIMESTAMP '{cs}') AS day_cur,
          COUNT(DISTINCT CAST(ts AS DATE)) FILTER (WHERE ts <  TIMESTAMP '{cs}') AS day_prev
        FROM ({base}) WHERE ts >= TIMESTAMP '{ps}' AND ts < TIMESTAMP '{ce}'
    """)
    amt_cur, amt_prev, cnt_cur, cnt_prev, day_cur, day_prev = kpi_rows[0]
    amt_cur, amt_prev = float(amt_cur or 0), float(amt_prev or 0)
    cnt_cur, cnt_prev = int(cnt_cur or 0), int(cnt_prev or 0)
    day_cur, day_prev = int(day_cur or 0), int(day_prev or 0)
    aov_cur = amt_cur / cnt_cur if cnt_cur else 0.0
    aov_prev = amt_prev / cnt_prev if cnt_prev else 0.0

    kpis = [
        {
            "key": "amount", "label": "金额合计", "fmt": "money",
            "value": _round(amt_cur), "delta": _delta(amt_cur, amt_prev),
            "delta_type": "pct", "good_when_up": True,
            "hint": f"SUM({acol}) · 金额列由你上传时标注",
        },
        {
            "key": "count", "label": "记录数", "fmt": "int",
            "value": cnt_cur, "delta": _delta(cnt_cur, cnt_prev),
            "delta_type": "pct", "good_when_up": True,
            "hint": "窗口内的记录条数",
        },
        {
            "key": "aov", "label": "平均单笔", "fmt": "money2",
            "value": _round(aov_cur), "delta": _delta(aov_cur, aov_prev),
            "delta_type": "pct", "good_when_up": True,
            "hint": "金额合计 ÷ 记录数",
        },
        {
            "key": "days", "label": "活跃天数", "fmt": "int",
            "value": day_cur, "delta": _delta(day_cur, day_prev),
            "delta_type": "pct", "good_when_up": True,
            "hint": f"有记录的去重天数（按 {tcol}）",
        },
    ]

    _, trend_rows = query(f"""
        SELECT strftime(ts, '%Y-%m') AS month,
               COALESCE(SUM(amt), 0) AS v, COUNT(*) AS c
        FROM ({base})
        WHERE ts >= TIMESTAMP '{cs}' AND ts < TIMESTAMP '{ce}'
        GROUP BY 1 ORDER BY 1
    """)

    charts: dict[str, Any] = {
        "trend": {
            "months": [r[0] for r in trend_rows],
            "gmv": [_round(r[1]) for r in trend_rows],
            "orders": [int(r[2] or 0) for r in trend_rows],
        },
    }
    cards: list[dict[str, Any]] = [
        {"slot": "trend", "kind": "line", "wide": True,
         "title": "金额与笔数趋势", "sub": "按月 · 双轴"},
    ]
    dim_kinds = [("pie", "占比"), ("bar", "排行"), ("products", "TOP 6")]
    for i, dim in enumerate(dims):
        kind, suffix = dim_kinds[i]
        _, rows = query(f"""
            SELECT COALESCE(CAST(dim AS VARCHAR), '（空）') AS name,
                   COALESCE(SUM(amt), 0) AS value, COUNT(*) AS c
            FROM (SELECT TRY_CAST({T} AS TIMESTAMP) AS ts, {A} AS amt, {_q(dim)} AS dim
                  FROM {TBL})
            WHERE ts >= TIMESTAMP '{cs}' AND ts < TIMESTAMP '{ce}'
            GROUP BY 1 ORDER BY 2 DESC
            LIMIT {6 if kind == "products" else 10}
        """)
        slot = f"dim{i}"
        charts[slot] = [
            {"name": r[0], "value": _round(r[1]), **({"orders": int(r[2] or 0)} if kind == "products" else {})}
            for r in rows
        ]
        cards.append({"slot": slot, "kind": kind, "title": f"{dim} {suffix}", "sub": "按金额",
                      "name_label": dim, "val_label": "金额"})

    role_desc = f"时间列 {tcol} · 金额列 {acol}" + (f" · 维度列 {'、'.join(dims)}" if dims else "")
    return {
        "range": range_key,
        "source": table,
        "title": f"{hit.get('display') or table} · 上传数据看板",
        "months": months,
        "ranges": [{"key": k, "label": RANGE_LABELS[k]} for k in RANGE_MONTHS],
        "sources": _eligible_sources(),
        "cards": cards,
        "window": {
            "start": cs.isoformat(),
            "end": (ce - dt.timedelta(days=1)).isoformat(),
            "label": f"{cs:%Y-%m} ~ {(ce - dt.timedelta(days=1)):%Y-%m}",
        },
        "kpis": kpis,
        "charts": charts,
        "note": f"列角色来自你上传确认时的标注（{role_desc}）· 固定查询直达，不走模型",
    }


# ---------- AI spec 看板：按 dash_spec.py 生成的配置渲染（LLM 规划一次，这里确定性执行） ----------

_SPEC_FMT = {"money": (2, "money"), "money2": (2, "money2"),
             "int": (0, "int"), "pct2": (2, "pct2"), "percent": (4, "percent")}


def _agg_expr(agg: str, col: str, where: str | None = None) -> str:
    """聚合表达式。col='*' 表示 COUNT(*)；列名已经过白名单校验，这里安全引用。

    where 非空时生成 `FILTER (WHERE ...)`。**FILTER 必须紧贴聚合函数**，
    不能写成 COALESCE(SUM(x), 0) FILTER (...) —— DuckDB 会报
    `syntax error at or near "FILTER"`。
    """
    c = _q(col)
    f = f" FILTER (WHERE {where})" if where else ""
    if agg == "COUNT":
        return f"COALESCE(COUNT(*){f}, 0)" if col == "*" else f"COALESCE(COUNT({c}){f}, 0)"
    body = {
        "SUM": f"SUM({c}){f}",
        "AVG": f"AVG({c}){f}",
        "MIN": f"MIN({c}){f}",
        "MAX": f"MAX({c}){f}",
        "COUNT_DISTINCT": f"COUNT(DISTINCT {c}){f}",
    }[agg]
    return f"COALESCE({body}, 0)"


def _render_spec_dashboard(range_key: str, entry: dict[str, Any],
                           start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """按 AI spec 渲染看板：输出与通用模板完全同构（cards/charts/kpis），
    前端零改动即可渲染。任何列缺失/类型不符都会抛异常，由调用方落回通用模板。"""
    spec = entry["spec"]
    roles = spec.get("roles") or {}
    tcol, acol = roles.get("time"), roles.get("amount")
    if not tcol:
        raise ValueError("spec 缺少时间列")
    table = entry["name"]
    months, cs, ce, ps = _window(range_key, _anchor_from(table, tcol), start, end)
    T, TBL = _q(tcol), _q(table)

    def _base(extra_cols: list[str]) -> tuple[str, dict[str, str]]:
        """归一时间列子查询：TRY_CAST 兼容字符串日期，TRY_CAST 失败的行不进窗口。

        返回 (SQL, 列名映射)：extra 列在子查询里统一重命名为 c_0/c_1...，
        外层聚合必须引用映射后的名字（排序去重保证同列总是同一映射）。
        """
        uniq = sorted({c for c in extra_cols if c and c != "*"})
        mapping = {c: f"c_{i}" for i, c in enumerate(uniq)}
        sel = (", " + ", ".join(f'{_q(c)} AS "{m}"' for c, m in mapping.items())) if uniq else ""
        sql = (f"SELECT TRY_CAST({T} AS TIMESTAMP) AS ts{sel} FROM {TBL} "
               f"WHERE TRY_CAST({T} AS TIMESTAMP) IS NOT NULL")
        return sql, mapping

    # ---- KPI：一条 SQL 同时算全部 KPI 的当期与上期（>=cs 本期 / <cs 上期，窗口 [ps, ce)）----
    kpis_spec = spec.get("kpis") or []
    kpi_cols: list[str] = []
    for k in kpis_spec:
        if k.get("column") != "*":
            kpi_cols.append(k["column"])
    base_cols = list(kpi_cols) + ([acol] if acol else [])
    base_sql, col_map = _base(base_cols)
    selects: list[str] = []
    for i, k in enumerate(kpis_spec):
        col = "*" if k["column"] == "*" else col_map[k["column"]]
        selects.append(_agg_expr(k["agg"], col, f"ts >= TIMESTAMP '{cs}'") + f" AS k{i}_cur")
        selects.append(_agg_expr(k["agg"], col, f"ts < TIMESTAMP '{cs}'") + f" AS k{i}_prev")
    _, rows = query(
        f"SELECT {', '.join(selects)} FROM ({base_sql}) "
        f"WHERE ts >= TIMESTAMP '{ps}' AND ts < TIMESTAMP '{ce}'")
    vals = rows[0]

    kpis: list[dict[str, Any]] = []
    for i, k in enumerate(kpis_spec):
        cur, prev = vals[2 * i], vals[2 * i + 1]
        nd, fmt = _SPEC_FMT.get(k.get("fmt"), (2, "money2"))
        if fmt == "int":
            cur, prev = int(cur or 0), int(prev or 0)
        else:
            cur, prev = float(cur or 0), float(prev or 0)
        is_ratio = fmt == "percent"
        kpis.append({
            "key": k["key"], "label": k["label"], "fmt": fmt,
            "value": _round(cur, nd),
            # 比率型指标环比用百分点差，避免小基数上相对变化失真（与内置退款率同策略）
            "delta": (cur - prev) if is_ratio else _delta(cur, prev),
            "delta_type": "pp" if is_ratio else "pct",
            "good_when_up": True,
            "hint": f"{k['agg']}({k['column'] if k['column'] != '*' else '记录数'})",
        })

    # ---- 图表：trend（金额+笔数双轴）+ 维度图（pie/bar/top → 前端 pie/bar/products 卡片）----
    charts: dict[str, Any] = {}
    cards: list[dict[str, Any]] = []
    trend_title = next((c["title"] for c in spec.get("charts", [])
                        if c.get("kind") == "trend"), "金额与笔数趋势")
    _, trend_rows = query(f"""
        SELECT strftime(ts, '%Y-%m') AS month,
               COALESCE(SUM("amt"), 0) AS v, COUNT(*) AS c
        FROM (SELECT TRY_CAST({T} AS TIMESTAMP) AS ts
              {", " + _q(acol) + " AS amt" if acol else ""}
              FROM {TBL} WHERE TRY_CAST({T} AS TIMESTAMP) IS NOT NULL)
        WHERE ts >= TIMESTAMP '{cs}' AND ts < TIMESTAMP '{ce}'
        GROUP BY 1 ORDER BY 1
    """)
    charts["trend"] = {
        "months": [r[0] for r in trend_rows],
        "gmv": [_round(r[1]) for r in trend_rows],
        "orders": [int(r[2] or 0) for r in trend_rows],
    }
    cards.append({"slot": "trend", "kind": "line", "wide": True,
                  "title": trend_title, "sub": "按月 · 双轴"})

    dim_kinds = [("pie", "占比"), ("bar", "排行"), ("bar", "TOP")]
    dim_charts = [c for c in spec.get("charts", []) if c.get("kind") != "trend"][:3]
    for i, c in enumerate(dim_charts):
        kind0 = c.get("kind")
        slot = f"dim{i}"

        # ---- scatter：两数值列相关性散点（采样 500 点防卡顿）----
        if kind0 == "scatter":
            xc, yc = c["x"], c["y"]
            sc_sql, cmap = _base([xc, yc])
            _, pts = query(
                f'SELECT "{cmap[xc]}", "{cmap[yc]}" FROM ({sc_sql}) '
                f"WHERE ts >= TIMESTAMP '{cs}' AND ts < TIMESTAMP '{ce}' "
                f'AND "{cmap[xc]}" IS NOT NULL AND "{cmap[yc]}" IS NOT NULL LIMIT 500')
            charts[slot] = {"x": [_round(r[0]) for r in pts], "y": [_round(r[1]) for r in pts],
                            "x_label": xc, "y_label": yc}
            cards.append({"slot": slot, "kind": "scatter",
                          "title": c.get("title") or f"{xc} × {yc}", "sub": "散点 · 采样 500 点"})
            continue

        # ---- stack / group：双维度交叉（横轴=dim 总量TOP8，系列=dim2 总量TOP5）----
        if kind0 in ("stack", "group"):
            m = c.get("metric") or {}
            m_agg, m_col = m.get("agg", "COUNT"), m.get("column", "*")
            e = "COUNT(*)" if m_col == "*" else _agg_expr(m_agg, "mcol")
            sub_select = (f"TRY_CAST({T} AS TIMESTAMP) AS ts, {_q(c['dim'])} AS dim, "
                          f"{_q(c['dim2'])} AS dim2"
                          + ("" if m_col == "*" else f", {_q(m_col)} AS mcol"))
            _, x_rows = query(f"""
                SELECT COALESCE(CAST(dim AS VARCHAR), '（空）'),
                       COALESCE(CAST(dim2 AS VARCHAR), '（空）'), {e}
                FROM (SELECT {sub_select} FROM {TBL}
                      WHERE TRY_CAST({T} AS TIMESTAMP) IS NOT NULL)
                WHERE ts >= TIMESTAMP '{cs}' AND ts < TIMESTAMP '{ce}'
                GROUP BY 1, 2
            """)
            tot_d: dict[str, float] = {}
            tot_d2: dict[str, float] = {}
            cell: dict[tuple[str, str], float] = {}
            for dn, d2n, v in x_rows:
                v = float(v or 0)
                tot_d[dn] = tot_d.get(dn, 0) + v
                tot_d2[d2n] = tot_d2.get(d2n, 0) + v
                cell[(dn, d2n)] = v
            dims_top = [k for k, _ in sorted(tot_d.items(), key=lambda x: -x[1])][:8]
            ser_top = [k for k, _ in sorted(tot_d2.items(), key=lambda x: -x[1])][:5]
            charts[slot] = {
                "dims": dims_top,
                "series": [{"name": sn, "data": [_round(cell.get((dn, sn), 0)) for dn in dims_top]}
                           for sn in ser_top],
            }
            cards.append({"slot": slot, "kind": kind0,
                          "title": c.get("title") or f"{c['dim']} × {c['dim2']}",
                          "sub": ("堆叠" if kind0 == "stack" else "分组") +
                                 f" · 按{m_agg.lower()} · 系列为 {c['dim2']}"})
            continue

        m = c.get("metric") or {}
        m_agg, m_col = m.get("agg", "COUNT"), m.get("column", "*")
        # 子查询把度量列统一别名为 mcol，外层聚合只引用别名（COUNT(*) 不需要列）
        e = "COUNT(*)" if m_col == "*" else _agg_expr(m_agg, "mcol")
        sub_select = (f"TRY_CAST({T} AS TIMESTAMP) AS ts, {_q(c['dim'])} AS dim"
                      + ("" if m_col == "*" else f", {_q(m_col)} AS mcol"))
        limit = int(c.get("limit") or 10)
        _, d_rows = query(f"""
            SELECT COALESCE(CAST(dim AS VARCHAR), '（空）') AS name,
                   {e} AS value
            FROM (SELECT {sub_select} FROM {TBL}
                  WHERE TRY_CAST({T} AS TIMESTAMP) IS NOT NULL)
            WHERE ts >= TIMESTAMP '{cs}' AND ts < TIMESTAMP '{ce}'
            GROUP BY 1 ORDER BY 2 DESC
            LIMIT {limit}
        """)
        charts[slot] = [{"name": r[0], "value": _round(r[1])} for r in d_rows]
        kind, suffix = dim_kinds[i]
        cards.append({"slot": slot, "kind": kind, "title": c.get("title") or f"{c['dim']} {suffix}",
                      "sub": f"按{m_agg.lower()}", "name_label": c["dim"], "val_label": c.get("title") or "数值"})

    return {
        "range": range_key,
        "source": table,
        "title": f"{entry.get('display') or table} · 智能看板",
        "months": months,
        "ranges": [{"key": k, "label": RANGE_LABELS[k]} for k in RANGE_MONTHS],
        "sources": _eligible_sources(),
        "cards": cards,
        "window": {
            "start": cs.isoformat(),
            "end": (ce - dt.timedelta(days=1)).isoformat(),
            "label": f"{cs:%Y-%m} ~ {(ce - dt.timedelta(days=1)):%Y-%m}",
        },
        "kpis": kpis,
        "charts": charts,
        "note": f"AI 规划看板（{spec.get('title', '')}）：KPI {len(kpis)} 个 · 图表 {len(cards)} 张，"
                f"生成于 {entry.get('spec_at', '')} · 确定性查询，不走模型",
    }
