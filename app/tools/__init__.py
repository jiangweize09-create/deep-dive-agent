"""工具层：SQL 子 Agent 的核心工具（查数 + 白名单落表 + 统计检验）。

工具可裁剪（前端「工具」页的开关）：
- get_tools(allow) 按白名单过滤，allow=None 表示全开
- LOCKED 里的工具不允许被关掉：run_sql / validate_sql 关了 SQL Agent 就无法执行，
  「关掉护栏」不应该是一个可选项
"""

from .ab_test import ab_test  # noqa: F401
from .data_coverage import data_coverage  # noqa: F401
from .date_range import resolve_date_range  # noqa: F401
from .profile import profile_table  # noqa: F401
from .retrieval import search_schema, search_metrics  # noqa: F401
from .scratch import persist_result  # noqa: F401
from .sql import validate_sql, run_sql, generate_chart  # noqa: F401

# 工具元信息：(中文名, 一句话说明)。给前端「工具」页用，不参与 LLM 提示词
TOOL_META: dict[str, tuple[str, str]] = {
    "search_metrics": ("指标检索", "查 GMV / 客单价 / 退款率等指标口径与必带过滤条件"),
    "search_schema": ("表结构检索", "查表字段、样例值、join 关系与 PII 标记"),
    "resolve_date_range": ("日期解析", "把「上个月 / 近 6 个月 / 今年 Q2」换成左闭右开的确定区间"),
    "data_coverage": ("覆盖范围查询", "各表实际数据的时间范围；查询 0 行时先看覆盖再决定重查或说明"),
    "profile_table": ("数据质量体检", "行数 / 空值率 / 数值分布 / 异常行（|z|≥3），分析前先验货"),
    "validate_sql": ("SQL 护栏校验", "AST 校验：只读 / 禁 SELECT * / PII / join 键 / 指标口径 / 强制 LIMIT"),
    "run_sql": ("执行 SQL", "执行前重新过护栏，在只读连接上跑，返回前几行预览"),
    "generate_chart": ("生成图表", "查询结果 → ECharts 配置（纯函数）"),
    "persist_result": ("结果落表", "已校验的结果集落进 scratch 临时库，返回句柄"),
    "ab_test": ("A/B 检验", "p 值与置信区间由 scipy 确定性计算，不靠模型编造"),
}

# 不可关闭：关掉就无法查数/无法护栏，属于「不可选项」而不是「默认开启」
LOCKED = frozenset({"run_sql", "validate_sql"})

_CANONICAL = [
    "search_schema", "search_metrics", "resolve_date_range", "profile_table",
    "validate_sql", "run_sql", "generate_chart", "persist_result", "ab_test",
    "data_coverage",
]


def all_tools() -> list:
    return [search_schema, search_metrics, resolve_date_range, profile_table,
            validate_sql, run_sql, generate_chart, persist_result, ab_test,
            data_coverage]


def get_tools(allow: list[str] | None = None) -> list:
    """按白名单返回工具。

    allow=None 或空列表 → 全开（前端全取消勾选是误操作，不该把 Agent 变成哑巴）。
    LOCKED 里的工具即使不在 allow 里也保留。
    """
    tools = all_tools()
    if not allow:
        return tools
    keep = set(allow) | LOCKED
    return [t for t in tools if t.name in keep]


def tool_specs() -> list[dict]:
    """给 /api/tools 用的清单（顺序固定，便于前端稳定渲染）。"""
    by_name = {t.name: t for t in all_tools()}
    out = []
    for name in _CANONICAL:
        t = by_name.get(name)
        if t is None:   # 理论上不会发生，防将来改名漏改这里
            continue
        label, desc = TOOL_META.get(name, (name, t.description or ""))
        out.append({
            "name": name,
            "label": label,
            "desc": desc,
            "locked": name in LOCKED,
            # 给前端展示 LLM 实际看到的签名，说明「这不是摆设」
            "params": list(getattr(t, "args", {}) or {}),
        })
    return out
