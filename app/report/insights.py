"""确定性洞察计算：对查询结果做纯代码统计分析。

设计动机：让「Insight」可答辩 —— 每条洞察都能指出计算方式（环比公式、
z-score 阈值、集中度占比），不是 LLM 的自由发挥。LLM 拿到洞察后只做
翻译与组织，不允许发明数字。

规则集（都是轻量统计，宁缺毋滥）：
- 趋势：时间列（month/date 类）+ 数值列的首尾变化与方向
- 环比：最后两期变化幅度 > 15% 时提示
- 异常：序列 z-score |z| >= 2 的点
- 集中度：维度列 top1 占比 >= 50% 时提示
- 缺失：任一列缺失率 >= 20% 时提示
"""

import math
import re


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _num_cols(cols: list[str], rows: list[dict]) -> list[str]:
    out = []
    for c in cols:
        vals = [r.get(c) for r in rows if r.get(c) is not None]
        if vals and sum(1 for v in vals if _is_num(v)) / len(vals) >= 0.8:
            out.append(c)
    return out


def _time_col(cols: list[str], rows: list[dict]) -> str | None:
    """识别时间维列：列名 month/date/day，或值形如 2026-09 / 2026-09-01。"""
    name_hit = [c for c in cols if re.search(r"month|date|day|week", c, re.I)]
    if len(name_hit) == 1:
        return name_hit[0]
    pat = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
    for c in cols:
        vals = [str(r.get(c) or "") for r in rows]
        if vals and sum(1 for v in vals if pat.match(v)) / len(vals) >= 0.8:
            return c
    return None


def compute_insights(columns: list[str], rows: list[dict]) -> list[dict]:
    """输入真实查询结果，输出洞察条目列表。纯函数，无 LLM。"""
    insights: list[dict] = []
    if not rows or not columns:
        return [{"kind": "summary", "level": "warn",
                 "text": "结果集为空，无法产出洞察；请检查过滤条件是否过严。"}]

    nums = _num_cols(columns, rows)
    tcol = _time_col(columns, rows)
    dims = [c for c in columns if c not in nums and c != tcol]

    # 趋势 + 环比 + 异常：时间列 + 数值列
    if tcol and nums:
        ordered = sorted(rows, key=lambda r: str(r.get(tcol, "")))
        for y in nums[:3]:   # 最多分析 3 个数值列，避免报告冗长
            series = [r.get(y) for r in ordered]
            vals = [v for v in series if _is_num(v)]
            if len(vals) < 3:
                continue
            first, last = vals[0], vals[-1]
            if first:
                chg = (last - first) / abs(first) * 100
                arrow = "上升" if chg >= 0 else "下降"
                insights.append({
                    "kind": "trend", "level": "info",
                    "text": f"{y} 从 {ordered[0].get(tcol)} 到 {ordered[-1].get(tcol)} "
                            f"整体{arrow} {abs(chg):.1f}%（{first:,.2f} → {last:,.2f}）。"})
            if len(vals) >= 2 and vals[-2]:
                mom = (vals[-1] - vals[-2]) / abs(vals[-2]) * 100
                if abs(mom) >= 15:
                    insights.append({
                        "kind": "mom", "level": "warn" if abs(mom) >= 30 else "info",
                        "text": f"最近一期 {ordered[-1].get(tcol)} 的 {y} 环比"
                                f"{'上升' if mom >= 0 else '下降'} {abs(mom):.1f}%，"
                                f"值得结合业务确认原因。"})
            # z-score 异常（|z|>=2）
            mean = sum(vals) / len(vals)
            std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)) or 1.0
            for r, v in zip(ordered, series):
                if _is_num(v) and abs(v - mean) / std >= 2:
                    insights.append({
                        "kind": "anomaly", "level": "warn",
                        "text": f"{r.get(tcol)} 的 {y}={v:,.2f} 明显偏离序列均值"
                                f"（|z|≥2，均值 {mean:,.2f}），建议重点核查。"})

    # 集中度：维度列 + 单一数值列
    if dims and len(nums) == 1:
        y = nums[0]
        total = sum(r.get(y) or 0 for r in rows if _is_num(r.get(y)))
        if total:
            ranked = sorted((r for r in rows if _is_num(r.get(y))),
                            key=lambda r: r.get(y), reverse=True)
            top = ranked[0]
            share = top.get(y) / total * 100
            if share >= 50 and len(ranked) >= 2:
                insights.append({
                    "kind": "concentration", "level": "warn",
                    "text": f"{top.get(dims[0])} 一项占 {y} 总量的 {share:.1f}%，"
                            f"集中度偏高（共 {len(ranked)} 个 {dims[0]}）。"})

    # 缺失值
    for c in columns:
        missing = sum(1 for r in rows if r.get(c) is None)
        if missing / len(rows) >= 0.2:
            insights.append({
                "kind": "missing", "level": "warn",
                "text": f"列 {c} 有 {missing / len(rows) * 100:.0f}% 的行缺失，"
                        f"相关结论需注明口径。"})

    if not insights:
        insights.append({"kind": "summary", "level": "info",
                         "text": f"结果集 {len(rows)} 行 × {len(columns)} 列，"
                                 f"未检出显著趋势/异常/集中度，结构平稳。"})
    return insights
