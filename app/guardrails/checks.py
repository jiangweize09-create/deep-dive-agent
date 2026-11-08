"""确定性护栏（AST 级 SQL 校验）。

设计要点（对应实施指南第三阶段）：
- 关键字黑名单不可靠，必须 AST 级校验：仅放行 SELECT / UNION
- 禁止 SELECT *（含 t.* 等所有作用域）
- 未知表 / 未知字段拦截（逼 LLM 先走语义层检索）
- PII 字段黑名单：含别名、CTE 内引用、未限定列名回溯
- join 连通性校验：只允许走语义层声明过的 join 路径
- 强制 LIMIT（SELECT 与 UNION 同规：无 limit 自动补；超上限压到 max_rows；
  LIMIT 5+5 / LIMIT ALL 等非字面量一律改写为字面量，不留绕过口）

返回结构化错误（reason + suggestion），让 LLM 自修复而不是抛异常。
"""

import re

from sqlglot import exp
import sqlglot

from ..config import settings
from ..semantic.loader import get_catalog


def _fail(reason: str, suggestion: str = "") -> dict:
    return {"valid": False, "reason": reason, "suggestion": suggestion, "checks": []}


def check_sql(sql: str) -> dict:
    catalog = get_catalog()
    sql = (sql or "").strip()

    # 1) 语法解析
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception as e:  # sqlglot.errors.ParseError 等
        return _fail(f"SQL 语法错误：{e}", "请检查 SQL 语法后重试")

    checks = []

    # 2) 只读：仅 SELECT / UNION
    is_readonly = isinstance(tree, (exp.Select, exp.Union))
    checks.append({"name": "只读校验（仅 SELECT）", "passed": is_readonly,
                   "detail": "" if is_readonly else f"语句类型 {type(tree).__name__} 不被允许"})
    if not is_readonly:
        return {"valid": False, "reason": "仅允许只读 SELECT 查询", "suggestion": "本工具只支持查询，不支持写入/删除操作",
                "checks": checks}

    # 2.5) 数据来源白名单：From/Join 的直接来源只允许语义层登记的物理表、子查询或 VALUES。
    #      表函数（read_csv / read_csv_auto / glob / generate_series 等）在 sqlglot 里
    #      解析成 Table(this=ReadCSV/Anonymous/…) —— this 不是 Identifier，且 t.name
    #      取出来是空串，会被「未知表」检查的 if t.name 过滤掉而整体绕过，EXPLAIN
    #      通过后即可读任意本地文件。UNNEST 更是直接以 exp.Unnest 出现在 From 下，
    #      find_all(exp.Table) 根本看不到。所有 From（含子查询内的）与 Join 逐一校验。
    def _source_problem(src) -> str | None:
        if isinstance(src, exp.Table):
            return None if isinstance(src.this, exp.Identifier) else "表函数来源（read_csv/glob 等文件读取）"
        if isinstance(src, (exp.Subquery, exp.Values)):
            return None
        return "非物理表来源（UNNEST 等）"

    source_problems = []
    for from_node in tree.find_all(exp.From):
        if from_node.this is not None:
            problem = _source_problem(from_node.this)
            if problem:
                source_problems.append(problem)
    for join in tree.find_all(exp.Join):
        if join.this is not None:
            problem = _source_problem(join.this)
            if problem:
                source_problems.append(problem)
    sources_ok = not source_problems
    checks.append({"name": "数据来源白名单", "passed": sources_ok,
                   "detail": "" if sources_ok else f"不允许的查询来源：{'、'.join(sorted(set(source_problems)))}"})
    if not sources_ok:
        return {"valid": False, "reason": "仅允许查询语义层登记的物理表",
                "suggestion": "禁止 read_csv/glob/generate_series/UNNEST 等表函数与文件读取；"
                              "请先调用 search_schema 检索可用表，再基于返回的表名重写 SQL",
                "checks": checks}

    # 别名与 CTE 解析
    # alias_map 用 dict[str, list[str]]：一个别名可能出现在多个 FROM 片段
    #（典型是 UNION 的两个分支复用同一别名o）。历史实现用扁平 dict，
    # 后者覆盖前者，导致 PII 检查把users.o.phone 解析成 orders.phone 而放行。
    # 扁平 dict 只在单FROM 场景下等价，Union/多FROM 场景下会漏判。
    cte_names = {c.alias for c in tree.find_all(exp.CTE)}
    alias_map: dict[str, list[str]] = {}
    for t in tree.find_all(exp.Table):
        if t.name and t.name not in cte_names:
            alias_map.setdefault(t.alias_or_name, []).append(t.name)

    def resolve(tname: str) -> str:
        """单个候选：给字段存在性 / join 键校验用（取第一个候选）。"""
        got = alias_map.get(tname)
        return got[0] if got else tname

    def resolve_all(tname: str) -> list[str]:
        """全部候选：给 PII 检查用——任一候选表上是 PII 就必须拦。"""
        return alias_map.get(tname) or [tname]

    # 3) 禁止 SELECT *（聚合函数内的 COUNT(*) 等除外）
    stars = list(tree.find_all(exp.Star))
    bare_stars = [s for s in stars if not isinstance(s.parent, exp.Func)]
    no_star = not bare_stars
    checks.append({"name": "禁止 SELECT *", "passed": no_star, "detail": "" if no_star else "请显式列出需要的字段名（聚合请写 COUNT(字段) 或 COUNT(*)）"})

    # 4) 表存在性
    used_raw = [t.name for t in tree.find_all(exp.Table) if t.name and t.name not in cte_names]
    used = sorted({resolve(n) for n in used_raw})
    unknown = [t for t in used if t not in catalog.tables]
    tables_ok = not unknown
    checks.append({"name": "表存在（语义层登记）", "passed": tables_ok,
                    "detail": "" if tables_ok else f"未知表：{', '.join(unknown)}"})
    known_used = [t for t in used if t in catalog.tables]

    # 5) 字段存在性（限定列名）
    col_errors = []
    for c in tree.find_all(exp.Column):
        t = resolve(c.table) if c.table else None
        if t and t in catalog.tables and c.name not in catalog.columns[t]:
            col_errors.append(f"{t}.{c.name}")
    cols_ok = not col_errors
    checks.append({"name": "字段存在（语义层登记）", "passed": cols_ok,
                    "detail": "" if cols_ok else f"未知字段：{', '.join(col_errors)}"})

    # 6) PII 字段拦截（别名 / CTE / 未限定列名 / 同别名多表 都要兜住）
    #    限定列名时遍历该别名的全部候选表：UNION 复用别名时alias_map 里有多个表名，
    #    任一候选是 PII 就必须拦，否则 SELECT o.phone FROM users o
    #    UNION SELECT o.amount FROM orders o 会被后者覆盖而放行。
    pii_hits = []
    for c in tree.find_all(exp.Column):
        if c.table:
            for t in resolve_all(c.table):
                if (t, c.name) in catalog.pii:
                    pii_hits.append(f"{t}.{c.name}")
                    break
        else:
            owners = [tt for tt in known_used if c.name in catalog.columns[tt]]
            for tt in owners:
                if (tt, c.name) in catalog.pii:
                    pii_hits.append(f"{tt}.{c.name}")
                    break
    pii_hits = sorted(set(pii_hits))
    pii_ok = not pii_hits
    checks.append({"name": "PII 字段拦截", "passed": pii_ok,
                   "detail": "" if pii_ok else f"涉及隐私字段：{', '.join(pii_hits)}"})

    # 7) join 连通性 + join 键与声明一致
    join_ok = catalog.joinable(known_used)
    checks.append({"name": "join 路径可达", "passed": join_ok,
                    "detail": "" if join_ok else f"这些表之间没有已声明的 join 路径：{'、'.join(known_used)}"})
    if join_ok:
        bad_keys = _check_join_keys(tree, catalog, resolve, cte_names)
        keys_ok = not bad_keys
        checks.append({"name": "join 键与声明一致", "passed": keys_ok,
                        "detail": "" if keys_ok else f"join 条件不符合语义层声明：{'；'.join(bad_keys)}"})

    # 7.5) 指标口径（required_filters）：命中指标聚合形状却完全没带 status 过滤 → 拦截。
    #      查询里任何位置出现 status 列（按状态拆解 / CASE 分支 / 显式过滤）则跳过——
    #      那是有意为之的状态维度查询，不能按 GMV 口径强拦。
    metric_errors = _check_required_filters(tree, catalog, resolve, known_used)
    metrics_ok = not metric_errors
    checks.append({"name": "指标口径（required_filters）", "passed": metrics_ok,
                    "detail": "" if metrics_ok else "；".join(metric_errors)})

    # 汇总错误
    errors = [c for c in checks if not c["passed"]]
    if errors:
        first = errors[0]
        reason = first["detail"] or first["name"] + " 未通过"
        suggestion = _suggest(first["name"])
        return {"valid": False, "reason": reason, "suggestion": suggestion, "checks": checks}

    # 8) 强制 LIMIT 上限（SELECT 与 UNION 同规）：无 LIMIT 自动补；超上限压到
    #    max_rows；LIMIT 5+5 / LIMIT ALL / 负数等解析不出可信字面量的写法，
    #    一律当作无上限改写为字面量 max_rows（int() 失败≠已受限）
    sql_out = sql.rstrip().rstrip(";")
    limit_n = None
    limit_arg = tree.args.get("limit")
    if limit_arg is not None:
        try:
            limit_n = int(limit_arg.expression.this)   # Literal 的 this 是数值字符串
        except (TypeError, ValueError, AttributeError):
            limit_n = None
    if isinstance(tree, (exp.Select, exp.Union)) and not (
        limit_n is not None and 0 <= limit_n <= settings.max_rows
    ):
        sql_out = tree.limit(settings.max_rows).sql(dialect="duckdb")
        if limit_arg is None:
            action = f"自动追加 LIMIT {settings.max_rows}"
        elif limit_n is None:
            action = f"LIMIT 改写为字面量 {settings.max_rows}"
        else:
            action = f"LIMIT 压缩到 {settings.max_rows}"
        checks.append({"name": action, "passed": True, "detail": ""})
    else:
        checks.append({"name": "LIMIT 已存在", "passed": True, "detail": ""})

    # 9) EXPLAIN 试运行：AST 校验不了函数存在性/类型匹配（如 MySQL 的 DATE_FORMAT），
    #    DuckDB 只做查询计划不执行，语义错误在护栏层提前拦截
    try:
        from ..db import query as _db_query
        _db_query("EXPLAIN " + sql_out)
        checks.append({"name": "函数与类型校验（EXPLAIN 试运行）", "passed": True, "detail": ""})
    except RuntimeError:
        pass  # 演示库未初始化等环境问题不属于 SQL 本身的错误，跳过
    except Exception as e:
        checks.append({"name": "函数与类型校验（EXPLAIN 试运行）", "passed": False, "detail": str(e)})
        return {"valid": False, "reason": f"SQL 试运行失败：{e}",
                "suggestion": "检查函数名与方言。本库是 DuckDB：按月分组用 strftime(created_at,'%Y-%m') 或 date_trunc('month',col)，没有 DATE_FORMAT；IFNULL 用 coalesce",
                "checks": checks}

    return {"valid": True, "sql_out": sql_out, "checks": checks}


def _check_join_keys(tree, catalog, resolve, cte_names) -> list[str]:
    """校验每条 JOIN 的 ON 等值键是否与语义层声明一致。

    规则：JOIN 必须带 ON（逗号笛卡尔积在 AST 层就是 on=None）；
    ON 中至少一个等值对 (t1.c1, t2.c2) 完全匹配某条声明的 join 键（方向不限）。
    额外的 AND 过滤条件不受影响，只要求存在一个匹配的等值键。
    """
    problems = []
    for j in tree.find_all(exp.Join):
        on = j.args.get("on")
        if on is None:
            problems.append("JOIN 缺少 ON 条件（不允许逗号笛卡尔积），请改为显式 JOIN ... ON")
            continue
        matched = False
        saw_cte = False
        pair_desc = []
        for eq in on.find_all(exp.EQ):
            lc, rc = eq.left.find(exp.Column), eq.right.find(exp.Column)
            if not lc or not rc:
                continue
            t1, t2 = resolve(lc.table), resolve(rc.table)
            if not t1 or not t2 or t1 in cte_names or t2 in cte_names:
                # CTE 的列血缘无法静态追溯到基表，含 CTE 的 join 不做强校验
                saw_cte = True
                continue
            if t1 not in catalog.tables or t2 not in catalog.tables:
                continue
            pair = frozenset({(t1, lc.name), (t2, rc.name)})
            pair_desc.append(f"{t1}.{lc.name} = {t2}.{rc.name}")
            if pair in catalog.edge_keys:
                matched = True
                break
        if not matched and not saw_cte:
            detail = "、".join(pair_desc) if pair_desc else on.sql()
            problems.append(f"等值键 [{detail}] 不在声明的 join 路径里（参考 search_schema 的 joins）")
    return problems


def _agg_shape(tree, resolve, known_used):
    """收集 SQL 里的聚合形状 {(表, 列|'*', FUNC)}，只认 SUM/COUNT。

    COUNT(*) 必须绑定到它所在的表（FROM 后的主表），否则形状退化成 ('*','*','COUNT')
    会跨表误命中指标触发条件——例如 SELECT COUNT(*) FROM products 会被当成在算
    order_count（orders 表的口径）而错误拦截。这里用「第一个不是 CTE 的真实表」
    作为 COUNT(*) 的归属；表归属判定不出来时直接不产出该形状（宁可漏拦也不误伤）。
    """
    shapes = set()
    # 预扫描：记录首个真实表（排除 CTE），供COUNT(*) 绑定归属
    cte_names = cte_names_of(tree)
    first_real_table = None
    for t in tree.find_all(exp.Table):
        if t.name and t.name not in cte_names:
            first_real_table = resolve(t.alias_or_name) or t.name
            break

    for func_node in tree.find_all(exp.Sum, exp.Count):
        fn = "SUM" if isinstance(func_node, exp.Sum) else "COUNT"
        col = func_node.find(exp.Column)
        if col is None:
            if fn == "COUNT":
                # COUNT(*)：绑定到主表；绑定不到时保守放弃（不产生误伤形状）
                if first_real_table:
                    shapes.add((first_real_table, "*", fn))
            continue
        t = resolve(col.table) if col.table else None
        if not t:
            owners = [tt for tt in known_used if col.name in catalog_columns(tt)]
            t = owners[0] if owners else None
        if t:
            shapes.add((t, col.name, fn))
    return shapes


def cte_names_of(tree) -> set:
    return {c.alias for c in tree.find_all(exp.CTE)}


def catalog_columns(tname: str) -> set:
    from ..semantic.loader import get_catalog
    return get_catalog().columns.get(tname, set())


def _table_from_filters(reqs: list) -> str | None:
    """从 required_filters 里的 "表.列=..." 反推出该口径所属的表。

    order_count 的 expression 是 COUNT(*)，本身不含表名；但它的
    required_filters 写着 "orders.status = 'completed'"，口径归属就是 orders。
    """
    for r in reqs or []:
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\.", r or "")
        if m and m.group(1) in get_catalog().tables:
            return m.group(1)
    return None


def _metric_triggers(catalog) -> dict:
    """从 metrics.yaml 的 expression 提取聚合形状 -> [(metric, required_filters)]。

    键是 (表, 列|'*', FUNC)。表名是口径的一部分——COUNT(*) 这类不带表名的
    表达式，归属表从 required_filters 反推（见 _table_from_filters），
    取不到就整条跳过（宁可漏拦也不误伤）。
    """
    triggers: dict[tuple, list] = {}
    for m in catalog.metrics:
        if not m.required_filters:
            continue
        default_table = _table_from_filters(m.required_filters)
        for fn, col in re.findall(r"\b(SUM|COUNT|AVG)\s*\(\s*(?:DISTINCT\s+)?([*\w.]+)", m.expression, re.IGNORECASE):
            fn = fn.upper()
            if fn == "AVG":
                # aov 的 expression 是 SUM/COUNT 组合，不含 AVG，已由 SUM 分支覆盖。
                # 这里跳过 AVG 是防御：若将来 YAML 里出现 AVG(x)，单独的平均值聚合
                # 语义与 required_filters 的口径未必一致，宁可不拦也不误伤。
                continue
            if col == "*":
                # COUNT(*)：绑定到口径所属表
                if not default_table:
                    continue
                key = (default_table, "*", fn)
            elif "." in col:
                t, c = col.split(".", 1)
                key = (t, c, fn)
            else:
                owners = [t for t in catalog.columns if col in catalog.columns[t]]
                if len(owners) != 1:
                    continue
                key = (owners[0], col, fn)
            triggers.setdefault(key, [])
            if (m.name, m.required_filters) not in triggers[key]:
                triggers[key].append((m.name, m.required_filters))
    return triggers


def _check_required_filters(tree, catalog, resolve, known_used) -> list[str]:
    """命中指标聚合形状且整条 SQL 完全没有 status 条件时，强制 required_filters。"""
    errors = []
    # status 列是否出现在查询任何位置（含 WHERE/SELECT/CASE/GROUP BY）
    status_cols = []
    for c in tree.find_all(exp.Column):
        if c.name != "status":
            continue
        t = resolve(c.table) if c.table else None
        if not t:
            owners = [tt for tt in known_used if "status" in catalog.columns.get(tt, set())]
            t = owners[0] if owners else None
        if t and t in catalog.tables:
            status_cols.append(t)
    has_status = bool(status_cols)
    if has_status:
        return errors   # 查询本身就是状态维度的，交给语义纪律，不在此强拦

    shapes = _agg_shape(tree, resolve, known_used)
    triggers = _metric_triggers(catalog)
    hit_metrics = {}
    for shape in shapes:
        t, c, fn = shape
        # 只认「表 + 聚合函数」都一致的形状。
        # 历史实现额外试了 ("*", c, fn) 与 (t, "*", fn) 两条通配，导致
        # SELECT COUNT(*) FROM products 命中 order_count（orders 的口径）而被误拦，
        # 也让任意表的 SUM(x) 命中所有 SUM 类指标。表是指标口径的一部分，不能通配。
        if (t, c, fn) in triggers:
            for name, reqs in triggers[(t, c, fn)]:
                hit_metrics[name] = reqs
    seen_reqs = set()
    for name, reqs in hit_metrics.items():
        req_key = tuple(reqs)
        if req_key in seen_reqs:
            continue
        seen_reqs.add(req_key)
        errors.append(
            f"按指标「{name}」的口径计算必须带过滤条件 {' AND '.join(reqs)}"
            f"（当前查询没有任何状态过滤）。若确要统计全部状态，请显式写出状态维度"
        )
    return errors


def _suggest(check_name: str) -> str:
    mapping = {
        "表存在（语义层登记）": "先调用 search_schema 检索可用表，再基于返回的表名重写 SQL",
        "字段存在（语义层登记）": "先调用 search_schema 查看该表的字段清单，使用语义层登记过的字段",
        "PII 字段拦截": "手机号/邮箱等隐私字段禁止查询；请改为提供聚合统计结果（如按地区统计用户数）",
        "join 路径可达": "参考 search_schema 返回的 joins 关系，只连接有声明 join 路径的表",
        "join 键与声明一致": "join 条件必须使用语义层声明的键（如 orders.user_id = users.id），禁止用金额等无关列关联",
        "指标口径（required_filters）": "先调用 search_metrics 确认指标口径，SQL 中补上 required_filters（如 status='completed'）",
        "禁止 SELECT *": "显式列出需要的字段名",
    }
    return mapping.get(check_name, "请根据 reason 修正 SQL 后重试")
