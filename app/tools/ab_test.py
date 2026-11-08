"""A/B 实验显著性检验工具：比例型（双比例 z 检验）与连续型（Welch t 检验）。

为什么做成工具而不是让 LLM 心算：p 值、置信区间是 LLM 最容易一本正经
编错的数字。本工具用 scipy 做确定性计算，LLM 只负责三件事：用 run_sql
查出两组样本数据、把汇总值喂给本工具、按返回的 verdict 解读结论。

典型调用（先用 run_sql 查出两组汇总，再调用本工具）：
- 比例型（转化率/点击率）：ab_test(metric_type="ratio",
    control={"n": 4820, "conversions": 231}, treatment={"n": 4795, "conversions": 262})
- 连续型（客单价/人均金额）：ab_test(metric_type="continuous",
    control={"n": 4820, "mean": 329.4, "std": 45.2}, treatment={"n": 4795, "mean": 341.8, "std": 47.9})
"""

import json
import math

from langchain_core.tools import tool
from scipy import stats


def _fail(reason: str, suggestion: str) -> dict:
    return {"ok": False, "reason": reason, "suggestion": suggestion}


def _check_group(g: dict, name: str, metric_type: str) -> str | None:
    if not isinstance(g, dict):
        return f"{name} 必须是对象（形如 {{\"n\": ..., ...}}）"
    if not isinstance(g.get("n"), (int, float)) or g["n"] <= 0:
        return f"{name}.n 必须为正数（样本量）"
    if metric_type == "ratio":
        x = g.get("conversions")
        if not isinstance(x, (int, float)) or x < 0 or x > g["n"]:
            return f"{name}.conversions 必须在 0 ~ n 之间（转化事件数）"
    else:
        if not isinstance(g.get("mean"), (int, float)):
            return f"{name}.mean 必须为数值（组内均值）"
        if not isinstance(g.get("std"), (int, float)) or g["std"] <= 0:
            return f"{name}.std 必须为正数（组内标准差，全组同值时无法检验）"
    return None


def _caveats(control: dict, treatment: dict, metric_type: str) -> list[str]:
    """样本量 / 事件数过小时的统计功效提醒。"""
    out = []
    if metric_type == "ratio":
        if min(control["conversions"], treatment["conversions"]) < 30:
            out.append("至少一组的转化事件数 < 30，检验功效可能不足，"
                       "不显著时更可能是样本不够而非真的无差异")
        if min(control["n"], treatment["n"]) < 100:
            out.append("至少一组样本量 < 100，比率估计本身不稳定")
    else:
        if min(control["n"], treatment["n"]) < 30:
            out.append("至少一组样本量 < 30，t 检验对非正态分布的稳健性变差")
    return out


def ab_test_core(metric_type: str, control: dict, treatment: dict, alpha: float = 0.05) -> dict:
    """两组样本的显著性检验（确定性计算）。metric_type: ratio | continuous。"""
    metric_type = (metric_type or "").strip().lower()
    if metric_type not in ("ratio", "continuous"):
        return _fail(f"不支持的 metric_type：{metric_type}",
                     "ratio（转化率等比例指标，传 n + conversions）或 "
                     "continuous（客单价等连续指标，传 n + mean + std）")
    if not (0 < alpha < 1):
        return _fail(f"alpha 必须在 (0,1) 区间，收到 {alpha}", "通常用 0.05，不传即可")
    for name, g in (("control", control), ("treatment", treatment)):
        if err := _check_group(g, name, metric_type):
            return _fail(err, "先由 run_sql 查出两组汇总值再调用本工具")

    if metric_type == "ratio":
        n1, x1 = control["n"], control["conversions"]
        n2, x2 = treatment["n"], treatment["conversions"]
        p1, p2 = x1 / n1, x2 / n2
        diff = p2 - p1
        # H0（两率相同）下的合并标准误；两组事件合计为 0/全转化时退化为不可检验
        pp = (x1 + x2) / (n1 + n2)
        se_pooled = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
        if se_pooled == 0:
            return _fail("两组转化事件合计为 0 或全部转化，无法检验",
                         "确认数据是否查错口径，或实验还在早期没有积累到事件")
        z = diff / se_pooled
        p_value = 2 * stats.norm.sf(abs(z))
        # 差值置信区间用非合并标准误（Wald），反映真实波动而非 H0 假设
        se_ci = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
        z_crit = stats.norm.ppf(1 - alpha / 2)
        ci = (diff - z_crit * se_ci, diff + z_crit * se_ci)
        effect = {"control_rate": p1, "treatment_rate": p2,
                  "abs_diff": diff, "rel_lift_pct": diff / p1 * 100 if p1 else None}
    else:
        n1, m1, s1 = control["n"], control["mean"], control["std"]
        n2, m2, s2 = treatment["n"], treatment["mean"], treatment["std"]
        diff = m2 - m1
        se = math.sqrt(s1 ** 2 / n1 + s2 ** 2 / n2)
        t_stat = diff / se
        # Welch–Satterthwaite 自由度（不假设方差齐性，电商两组方差常不同）
        v1, v2 = s1 ** 2 / n1, s2 ** 2 / n2
        df = (v1 + v2) ** 2 / (v1 ** 2 / (n1 - 1) + v2 ** 2 / (n2 - 1))
        p_value = 2 * stats.t.sf(abs(t_stat), df)
        t_crit = stats.t.ppf(1 - alpha / 2, df)
        ci = (diff - t_crit * se, diff + t_crit * se)
        effect = {"control_mean": m1, "treatment_mean": m2,
                  "abs_diff": diff, "rel_lift_pct": diff / m1 * 100 if m1 else None}

    significant = bool(p_value < alpha)   # np.bool_ -> bool，否则 json.dumps 崩
    caveats = _caveats(control, treatment, metric_type)
    caveats.append("p >= alpha 只说明「未检测到显著差异」，不等于「两组无差异」；"
                   "p < alpha 也不代表业务价值大，结合相对提升幅度解读")
    return {
        "ok": True,
        "metric_type": metric_type,
        "control": control,
        "treatment": treatment,
        "alpha": alpha,
        "effect": effect,
        "ci95_of_diff": [float(round(ci[0], 6)), float(round(ci[1], 6))],
        "p_value": float(round(p_value, 6)),
        "significant": significant,
        "verdict": (f"统计显著（p={p_value:.4f} < alpha={alpha}），95% CI [{ci[0]:.4f}, {ci[1]:.4f}]"
                    if significant else
                    f"不显著（p={p_value:.4f} >= alpha={alpha}），当前数据不足以支持「有差异」的结论"),
        "caveats": caveats,
    }


@tool
async def ab_test(metric_type: str, control: dict, treatment: dict, alpha: float = 0.05) -> str:
    """A/B 实验两组差异的显著性检验（确定性统计计算，禁止自行心算 p 值）。

    metric_type="ratio"：比例型指标（转化率/点击率/退款率），control/treatment
      传 {"n": 样本量, "conversions": 转化事件数}；
    metric_type="continuous"：连续型指标（客单价/人均金额），传
      {"n": 样本量, "mean": 均值, "std": 标准差}。
    数据先用 run_sql 分组查出（如实验分流字段 GROUP BY），把两组汇总喂进来。
    返回 p_value、差值的 95% 置信区间、significant 判定与 caveats（样本量提醒）。
    结论必须引用本工具返回的数字，禁止改写。"""
    return json.dumps(ab_test_core(metric_type, control, treatment, alpha), ensure_ascii=False)
