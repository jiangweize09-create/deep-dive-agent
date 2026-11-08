"""AI 看板 spec 生成：「LLM 规划一次，确定性渲染一万次」。

上传表入库后，LLM 读一次 schema + 样本值，输出看板配置（spec JSON）：
KPI 列表 + 图表列表。spec 经白名单校验后写入上传注册表，
之后看板渲染（dashboard.py）按 spec 用确定性 SQL 聚合——
LLM 不参与每次渲染：秒开、零 token、结果可复现、无注入面。

判断与降级链：
- LLM 判定 unsuitable（如纯文本表无数值列）→ 不写 spec，返回原因与建议；
- LLM 输出非法（JSON 坏 / 列名编造 / 聚合不在白名单）→ 剔除非法项，
  剔除后不满足最低要求则报错，用户可重试；
- spec 在但渲染时数据异常 → dashboard.py 已有回退内置数据兜底。

顺带把 roles（时间/金额/维度列）写回注册表：即使 AI spec 暂时不可用，
该表也能进看板数据源列表，走旧通用模板兜底（_build_upload_dashboard）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime

import httpx

from .config import settings

# 白名单：聚合 / 图型 / 数量边界 —— 防 LLM 幻觉的三道闸
AGGS = {"SUM", "AVG", "MIN", "MAX", "COUNT", "COUNT_DISTINCT"}
CHART_KINDS = {"trend", "pie", "bar", "top", "scatter", "stack", "group"}
FMTS = {"money", "money2", "int", "pct2", "percent"}
KPI_MIN, KPI_MAX = 2, 4
CHART_MIN, CHART_MAX = 1, 4
MAX_SPEC_TOKENS = 2000

_SPEC_SYSTEM = """你是 BI 看板规划师。根据给定的数据表结构、列类型与样本值，规划一块通用 BI 看板。
只输出一个 JSON 对象：不要 markdown 围栏、不要解释文字、不要换行缩进之外的任何内容。

JSON 结构：
{
  "suitable": true 或 false,
  "reason": "suitable 为 false 时的原因与建议（中文）",
  "title": "看板标题（中文，不超过 16 字）",
  "roles": {"time": "时间列名", "amount": "金额列名或 null", "dims": ["维度列名", "..."]},
  "kpis": [
    {"key": "英文标识", "label": "中文指标名", "agg": "SUM|AVG|MIN|MAX|COUNT|COUNT_DISTINCT",
     "column": "列名", "fmt": "money|money2|int|pct2|percent"}
  ],
  "charts": [
    {"kind": "trend", "title": "中文标题"},
    {"kind": "pie 或 bar 或 top", "title": "中文标题",
     "dim": "分类列名", "metric": {"agg": "SUM|AVG|MIN|MAX|COUNT|COUNT_DISTINCT", "column": "列名"},
     "limit": 6 或 10},
    {"kind": "scatter", "title": "中文标题", "x": "数值列名", "y": "数值列名"},
    {"kind": "stack 或 group", "title": "中文标题", "dim": "主维度列名", "dim2": "次维度列名",
     "metric": {"agg": "SUM|AVG|MIN|MAX|COUNT|COUNT_DISTINCT", "column": "列名"}}
  ]
}

纪律（违反即视为不合格输出）：
1. 所有列名必须逐字来自给定的表结构，禁止编造、禁止大小写变体。
2. suitable=false 的条件：没有可解析为日期的列（趋势与时间窗口的前提），或没有任何数值列（KPI 无意义）。此时 kpis、charts 给空数组，reason 说明缺什么、建议用户怎么改数据。
3. roles.time 必须给出且必须真实存在；amount 给最核心的数值列（没有则 null）。
4. kpis 2~4 个：核心度量合计、记录数（COUNT, column 用 "*"），再加 1~2 个对该数据有业务意义的指标（去重数/均值/极值）。label 用业务语言（如"销售额合计"而不是"SUM(amount)"）。
5. charts 1~4 个：第一个必须是 trend（按月金额+笔数双轴，不需要额外参数）；其余按数据形状选：
   单维度分布用 pie（占比）/ bar（排行）/ top（TOP 榜）；两个数值列想看相关性用 scatter（x/y 都必须是
   数值列）；想看两个分类维度的交叉表现用 stack（堆叠）或 group（并排），dim 为横轴、dim2 拆系列。
   所有引用的列必须真实存在。
6. 金额类列聚合用 SUM + fmt money；比率类列用 AVG + pct2；计数用 COUNT + int；占比率(0~1)用 AVG + percent。
7. kpi 的 key 用小写英文+下划线，全表唯一。"""


def _table_facts(table: str) -> dict:
    """从注册表读取生成 spec 所需事实：列名/类型/样本/行数。"""
    from .uploads import list_uploads

    entry = next((t for t in list_uploads()["tables"] if t["name"] == table), None)
    if entry is None:
        raise LookupError(f"上传表不存在：{table}")
    cols = entry.get("columns") or []
    return {
        "table": table,
        "display": entry.get("display") or table,
        "rows": entry.get("rows"),
        "columns": [
            {"name": c.get("name"), "type": c.get("type"),
             "sample": (c.get("sample") or [])[:3]}
            for c in cols
        ],
        "col_names": [c.get("name") for c in cols if c.get("name")],
        "roles_guess": entry.get("roles"),
    }


async def _chat(system: str, user: str) -> str:
    """与 .env 配置的 LLM 端点做一次补全（OpenAI 兼容），返回文本。"""
    base = (settings.env_base_url or "").rstrip("/")
    key = settings.env_api_key or ""
    model = settings.env_model or ""
    if not (base and model):
        raise RuntimeError("未配置 LLM（.env 缺少 base_url / model），无法生成智能看板")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"} if key else {},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": MAX_SPEC_TOKENS,
                "temperature": 0.2,
            },
        )
    if r.status_code >= 400:
        raise RuntimeError(f"LLM HTTP {r.status_code}: {(r.text or '')[:200]}")
    data = r.json()
    return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content", ""))


def _parse_json(raw: str) -> dict:
    """剥掉可能的 ```json 围栏 / 前后杂文，取第一个平衡的 JSON 对象。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("LLM 输出中没有 JSON 对象")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("LLM 输出的 JSON 不完整")


def _clean_label_key(key: str, used: set) -> str:
    k = re.sub(r"[^a-z0-9_]+", "_", str(key or "").lower()).strip("_")[:24]
    if not k:
        k = "kpi"
    base, i = k, 2
    while k in used:
        k = f"{base}_{i}"
        i += 1
    used.add(k)
    return k


def _validate_spec(spec: dict, col_names: list[str]) -> tuple[dict | None, dict | None, str | None]:
    """白名单校验 + 非法项剔除。返回 (合法 spec, 顺带回写的 roles, 错误信息)。

    校验失败的三种结局：
    - LLM 说 unsuitable 且理由成立 → (None, None, None)，由调用方返回建议；
    - 非法项剔除后仍满足最少要求 → 返回修剪后的 spec；
    - 连最低要求都不满足 → (None, None, 错误说明)。
    """
    if not isinstance(spec, dict):
        return None, None, "LLM 输出不是 JSON 对象"
    if spec.get("suitable") is False:
        return None, None, None

    cols = set(col_names)
    roles_in = spec.get("roles") if isinstance(spec.get("roles"), dict) else {}
    tcol = roles_in.get("time") if roles_in.get("time") in cols else None
    acol = roles_in.get("amount") if roles_in.get("amount") in cols else None
    if not tcol:
        return None, None, "LLM 未能给出有效的时间列（趋势与时间窗口的前提），请重试或改用手动标注"
    dims = [d for d in (roles_in.get("dims") or [])
            if d in cols and d != tcol and d != acol][:3]
    roles = {"time": tcol, "amount": acol, "dims": dims}

    # ---- KPI：剔除非法项 ----
    used_keys: set = set()
    kpis: list[dict] = []
    for k in (spec.get("kpis") or [])[:KPI_MAX]:
        if not isinstance(k, dict):
            continue
        agg = str(k.get("agg") or "").upper()
        col = k.get("column")
        if agg not in AGGS:
            continue
        if agg != "COUNT" and col not in cols:
            continue
        if agg == "COUNT" and col not in cols and col != "*":
            continue
        fmt = k.get("fmt") if k.get("fmt") in FMTS else ("int" if agg in ("COUNT", "COUNT_DISTINCT") else "money2")
        kpis.append({
            "key": _clean_label_key(k.get("key") or f"{agg.lower()}_{str(col)}", used_keys),
            "label": str(k.get("label") or f"{agg} {col}").strip()[:20],
            "agg": agg, "column": "*" if (agg == "COUNT" and col == "*") else col,
            "fmt": fmt,
        })
        if len(kpis) >= KPI_MAX:
            break

    # KPI 不足时补保底两项：记录数 + 时间列聚合
    if not any(k["agg"] == "COUNT" for k in kpis):
        kpis.append({"key": _clean_label_key("record_count", used_keys), "label": "记录数",
                     "agg": "COUNT", "column": "*", "fmt": "int"})
    if len(kpis) < KPI_MIN and acol:
        kpis.append({"key": _clean_label_key(f"sum_{acol}", used_keys),
                     "label": "金额合计", "agg": "SUM", "column": acol, "fmt": "money"})

    # ---- charts：第一个 trend 必留，维度图剔除非法 ----
    charts_in = [c for c in (spec.get("charts") or []) if isinstance(c, dict)]
    charts: list[dict] = []
    for c in charts_in[:CHART_MAX]:
        kind = c.get("kind")
        if kind == "trend":
            charts.append({"kind": "trend", "title": str(c.get("title") or "金额与笔数趋势")[:20]})
            break
    def _metric(c: dict) -> dict:
        m = c.get("metric") if isinstance(c.get("metric"), dict) else {}
        m_agg = str(m.get("agg") or "").upper()
        m_col = m.get("column")
        if m_agg not in AGGS or (m_agg != "COUNT" and m_col not in cols):
            m_agg, m_col = ("SUM", acol) if acol else ("COUNT", "*")
        return {"agg": m_agg, "column": "*" if m_col == "*" else m_col}

    for c in charts_in:
        if len(charts) >= CHART_MAX:
            break
        kind = c.get("kind")
        if kind == "scatter":
            x, y = c.get("x"), c.get("y")
            if x in cols and y in cols and x != y:
                charts.append({"kind": "scatter",
                               "title": str(c.get("title") or f"{x}×{y}")[:20], "x": x, "y": y})
            continue
        if kind in ("stack", "group"):
            dim, dim2 = c.get("dim"), c.get("dim2")
            if dim not in cols or dim2 not in cols or dim == dim2:
                continue
            charts.append({"kind": kind, "title": str(c.get("title") or f"{dim}×{dim2}")[:20],
                           "dim": dim, "dim2": dim2, "metric": _metric(c), "limit": 8})
            continue
        if kind not in ("pie", "bar", "top") or c.get("dim") not in cols:
            continue
        charts.append({
            "kind": kind,
            "title": str(c.get("title") or f"{c.get('dim')}分析")[:20],
            "dim": c["dim"], "metric": _metric(c),
            "limit": 6 if kind == "top" else 10,
        })

    if not charts:      # trend 保底
        charts.append({"kind": "trend", "title": "金额与笔数趋势"})

    if not (KPI_MIN <= len(kpis) <= KPI_MAX) or not (CHART_MIN <= len(charts) <= CHART_MAX):
        return None, None, "LLM 输出经校验后不满足看板最低要求（KPI 2~4 个、图表 1~4 个），请重试"

    clean = {
        "title": str(spec.get("title") or "智能看板")[:20],
        "roles": roles,
        "kpis": kpis,
        "charts": charts,
    }
    return clean, roles, None


def _save_spec(table: str, spec: dict, roles: dict) -> str:
    """spec + roles 写回上传注册表（AI 顺带补齐 roles，旧通用模板兜底也能用）。"""
    from .uploads import _load_registry, _save_registry

    now = datetime.now().isoformat(timespec="seconds")
    entries = _load_registry()
    for u in entries:
        if u["name"] == table:
            u["spec"] = spec
            u["roles"] = roles
            u["spec_at"] = now
            break
    else:
        raise LookupError(f"上传表不存在：{table}")
    _save_registry(entries)
    return now


async def generate_dashboard_spec(table: str) -> dict:
    """主入口：读表事实 → LLM 规划 → 校验 → 入库。全程不抛异常，返回结构化结果。"""
    try:
        facts = _table_facts(table)
    except LookupError as e:
        return {"ok": False, "error": str(e)}

    col_lines = "\n".join(
        f"- {c['name']}（{c['type']}）样本: {c['sample']}" for c in facts["columns"])
    user = (
        f"表名: {facts['table']}\n展示名: {facts['display']}\n"
        f"总行数: {facts['rows']}\n列结构:\n{col_lines}\n\n"
        "请按系统指令输出看板规划 JSON。"
    )

    try:
        raw = await _chat(_SPEC_SYSTEM, user)
        spec = _parse_json(raw)
    except ValueError as e:
        return {"ok": False, "error": f"LLM 输出无法解析：{e}"}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"LLM 调用失败：{e}"}

    clean, roles, err = _validate_spec(spec, facts["col_names"])
    if err:
        return {"ok": False, "error": err}
    if clean is None:
        reason = str(spec.get("reason") or "该数据不适合生成看板（缺少可解析的时间列或数值列）")
        return {"ok": True, "suitable": False, "reason": reason}

    try:
        spec_at = _save_spec(table, clean, roles)
    except LookupError as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True, "suitable": True, "table": table,
        "title": clean["title"], "spec_at": spec_at,
        "kpi_count": len(clean["kpis"]), "chart_count": len(clean["charts"]),
    }
