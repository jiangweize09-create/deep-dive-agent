import { useEffect, useMemo, useState } from "react";
import ChartBox from "../components/ChartBox";
import { TOOL_LABEL } from "../components/ProcessCard";

/** 使用统计：token 消耗 / 问答量 / 工具排行（data/stats.db 确定性聚合）。
    独立增量模块：整体删除（本文件 + 设置导航项）不影响其他功能。 */

const KEY = { text: "#1f2229", muted: "#646a76", faint: "#98a0ad", line: "#e5e7eb", grid: "#f2f3f7", accent: "#1F2329" };

function usageChart(days: any[]) {
  return {
    grid: { left: 64, right: 16, top: 24, bottom: 30 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category", data: days.map((d) => d.day.slice(5)),
      axisLine: { lineStyle: { color: KEY.line } }, axisTick: { show: false },
      axisLabel: { color: KEY.faint, fontSize: 11 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: KEY.faint, fontSize: 11, formatter: (v: number) => (v >= 10000 ? (v / 10000).toFixed(0) + "万" : v) },
      splitLine: { lineStyle: { color: KEY.grid } },
    },
    series: [{
      name: "token", type: "bar", data: days.map((d) => d.tokens),
      barMaxWidth: 30, itemStyle: { color: KEY.accent, borderRadius: [4, 4, 0, 0] },
    }],
  };
}

export default function UsagePage({ active }: { active: boolean }) {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  const [days, setDays] = useState(14);

  useEffect(() => {
    if (!active) return;
    fetch(`/api/usage?days=${days}`)
      .then((r) => r.json())
      .then((d) => { setData(d); setErr(""); })
      .catch((e) => setErr(e.message));
  }, [active, days]);

  const totals = data?.totals || {};
  const dayList = data?.days || [];
  const option = useMemo(() => (dayList.length ? usageChart(dayList) : null), [dayList]);
  const maxTool = data?.tools?.[0]?.count || 1;

  return (
    <section className="page page-scroll" hidden={!active}>
      <div className="pg-wrap pg-wide">
        <div className="pg-head-row">
          <div>
            <div className="pg-title">使用统计</div>
            <div className="pg-sub">
              近 {data?.window_days ?? days} 天的问答量与 token 消耗（计数为该轮全部往返的累计：
              系统提示、工具定义、上下文与子 Agent 都计入，多 Agent 单轮 1-2 万属正常，并非净回答长度；
              供应商上下文缓存命中部分实际计价更低）
            </div>
          </div>
          <div className="db-ranges">
            {[7, 14, 30].map((d) => (
              <button key={d} className={"db-range" + (days === d ? " active" : "")} onClick={() => setDays(d)}>
                近 {d} 天
              </button>
            ))}
          </div>
        </div>

        {err ? <div className="error-banner">{err}</div> : null}

        <div className="db-kpis">
          <div className="db-kpi">
            <div className="db-kpi-label">问答次数</div>
            <div className="db-kpi-value">{Number(totals.count ?? 0).toLocaleString("zh-CN")}</div>
          </div>
          <div className="db-kpi">
            <div className="db-kpi-label">token 总量</div>
            <div className="db-kpi-value">{Number(totals.tokens ?? 0).toLocaleString("zh-CN")}</div>
          </div>
          <div className="db-kpi">
            <div className="db-kpi-label">平均 token / 次</div>
            <div className="db-kpi-value">{Number(totals.avg_tokens ?? 0).toLocaleString("zh-CN")}</div>
          </div>
          <div className="db-kpi">
            <div className="db-kpi-label">平均耗时</div>
            <div className="db-kpi-value">
              {totals.avg_ms ? (totals.avg_ms / 1000).toFixed(1) + "s" : "—"}
            </div>
          </div>
        </div>

        <div className="db-grid">
          <div className="db-card wide">
            <div className="db-card-title">逐日 token 消耗<span>bar · 确定性统计</span></div>
            {option ? (
              <ChartBox bare option={option} className="db-chart db-chart-lg" />
            ) : (
              <p className="ev-empty">还没有问答记录——去对话页问一句就有了。</p>
            )}
          </div>

          <div className="db-card wide">
            <div className="db-card-title">工具调用排行<span>近 {data?.window_days ?? days} 天</span></div>
            {(data?.tools || []).length ? (
              <div className="usage-tools">
                {(data?.tools || []).map((t: any) => (
                  <div key={t.name} className="usage-tool-row">
                    <span className="usage-tool-name">
                      {t.name}
                      {TOOL_LABEL[t.name] ? <span className="usage-tool-zh"> · {TOOL_LABEL[t.name]}</span> : null}
                    </span>
                    <span className="usage-tool-bar">
                      <span style={{ width: `${Math.round((t.count / maxTool) * 100)}%` }} />
                    </span>
                    <span className="usage-tool-count">{t.count}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="ev-empty">暂无工具调用记录</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
