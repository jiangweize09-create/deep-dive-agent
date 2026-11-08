import { useEffect, useMemo, useState } from "react";
import ChartBox from "../components/ChartBox";

const LS_RANGE = "xihuang_dash_range";
const LS_SRC = "xihuang_dash_source";
const LS_CUSTOM = "xihuang_dash_custom";

type RangeKey = "3m" | "6m" | "12m";
const RANGES: RangeKey[] = ["3m", "6m", "12m"];
const RANGE_LABEL: Record<RangeKey, string> = {
  "3m": "近 3 个月", "6m": "近 6 个月", "12m": "近 12 个月",
};

/** 从 localStorage 读范围：非法值一律回 6m，绝不让脏值进请求 */
function readRange(): RangeKey {
  const v = localStorage.getItem(LS_RANGE);
  return RANGES.includes(v as RangeKey) ? (v as RangeKey) : "6m";
}

/** 自定义起止（ISO 日期）；非法/不完整一律回 null */
function readCustom(): { start: string; end: string } | null {
  try {
    const v = JSON.parse(localStorage.getItem(LS_CUSTOM) || "null");
    if (v && /^\d{4}-\d{2}-\d{2}$/.test(v.start) && /^\d{4}-\d{2}-\d{2}$/.test(v.end) && v.start < v.end) {
      return { start: v.start, end: v.end };
    }
  } catch { /* 隐私模式等 */ }
  return null;
}

/** 数据源：只接受 builtin 或 up_xxx 形状，其它（含已删表）交给后端回退 */
function readSource(): string {
  const v = localStorage.getItem(LS_SRC) || "";
  return v === "builtin" || /^up_[a-z0-9_]{1,39}$/.test(v) ? v : "builtin";
}

const KEY = { text: "#1f2229", muted: "#646a76", faint: "#98a0ad", line: "#e5e7eb", grid: "#f2f3f7", accent: "#1F2329", ok: "#12b76a" };

function fmtKpi(k: any) {
  const v = Number(k.value) || 0;
  if (k.fmt === "percent") return (v * 100).toFixed(2) + "%";
  if (k.fmt === "money2") return v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (k.fmt === "money") return v.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
  return v.toLocaleString("zh-CN");
}

function fmtDelta(k: any) {
  if (k.delta == null) return "无上期对比";
  const sign = k.delta >= 0 ? "+" : "";
  if (k.delta_type === "pp") return `环比 ${sign}${(k.delta * 100).toFixed(2)}pp`;
  return `环比 ${sign}${(k.delta * 100).toFixed(1)}%`;
}

function lineOption(t: any) {
  return {
    grid: { left: 58, right: 58, top: 34, bottom: 30 },
    tooltip: { trigger: "axis" },
    // 图例只放右上角；去掉双轴的 name 文本（与图例重复且互相挤压遮挡）
    legend: { right: 8, top: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 11, color: KEY.muted } },
    xAxis: {
      type: "category", data: t.months, boundaryGap: false,
      axisLine: { lineStyle: { color: KEY.line } }, axisTick: { show: false },
      axisLabel: { color: KEY.faint, fontSize: 11 },
    },
    yAxis: [
      {
        type: "value",
        axisLabel: { color: KEY.faint, fontSize: 11, formatter: (v: number) => (v >= 10000 ? (v / 10000).toFixed(0) + "万" : v) },
        splitLine: { lineStyle: { color: KEY.grid } },
      },
      {
        type: "value",
        axisLabel: { color: KEY.faint, fontSize: 11 }, splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "GMV", type: "line", smooth: true, symbolSize: 6, data: t.gmv,
        itemStyle: { color: KEY.accent }, lineStyle: { width: 2 },
        areaStyle: { color: "rgba(31,35,41,0.07)" },
      },
      {
        name: "订单量", type: "line", smooth: true, symbolSize: 6, yAxisIndex: 1, data: t.orders,
        itemStyle: { color: KEY.ok }, lineStyle: { width: 2, type: "dashed" },
      },
    ],
  };
}

function pieOption(rows: any[]) {
  return {
    tooltip: { trigger: "item", formatter: (p: any) => `${p.name}<br/>¥${Number(p.value).toLocaleString("zh-CN")}（${p.percent}%）` },
    legend: { bottom: 0, itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 11, color: KEY.muted } },
    series: [{
      type: "pie", radius: ["48%", "70%"], center: ["50%", "43%"],
      avoidLabelOverlap: true, label: { show: false },
      itemStyle: { borderColor: "#fff", borderWidth: 2 },
      data: rows.map((r) => ({ name: r.name, value: r.value })),
    }],
  };
}

/** 墨青金品牌色系调色板：堆叠/分组系列与徽标同一色族 */
const PALETTE = ["#1F2329", "#1E8E7E", "#E9C46A", "#7C8794", "#0C4F44"];

function scatterOption(d: any) {
  return {
    grid: { left: 58, right: 24, top: 24, bottom: 38 },
    tooltip: {
      trigger: "item",
      formatter: (p: any) => `${d.x_label}: ${Number(p.value[0]).toLocaleString("zh-CN")}<br/>${d.y_label}: ${Number(p.value[1]).toLocaleString("zh-CN")}`,
    },
    xAxis: {
      type: "value", name: d.x_label, nameTextStyle: { color: KEY.faint, fontSize: 11 },
      axisLabel: { color: KEY.faint, fontSize: 10 }, splitLine: { lineStyle: { color: KEY.grid } },
    },
    yAxis: {
      type: "value", name: d.y_label, nameTextStyle: { color: KEY.faint, fontSize: 11 },
      axisLabel: { color: KEY.faint, fontSize: 10 }, splitLine: { lineStyle: { color: KEY.grid } },
    },
    series: [{
      type: "scatter", symbolSize: 7,
      data: (d.x || []).map((v: number, i: number) => [v, (d.y || [])[i]]),
      itemStyle: { color: "rgba(30,142,126,.5)" },
    }],
  };
}

/** 双维度交叉：grouped=false 堆叠 / true 并排 */
function crossOption(d: any, grouped: boolean) {
  return {
    grid: { left: 8, right: 12, top: 32, bottom: 6, containLabel: true },
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps: any) => ps[0].name + "<br/>" +
        ps.map((p: any) => `${p.marker}${p.seriesName}: ${Number(p.value).toLocaleString("zh-CN")}`).join("<br/>"),
    },
    legend: { top: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 11, color: KEY.muted } },
    xAxis: {
      type: "category", data: d.dims,
      axisLine: { lineStyle: { color: KEY.line } }, axisTick: { show: false },
      axisLabel: { color: KEY.faint, fontSize: 10, interval: 0 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: KEY.faint, fontSize: 10, formatter: (v: number) => (v >= 10000 ? (v / 10000).toFixed(0) + "万" : v) },
      splitLine: { lineStyle: { color: KEY.grid } },
    },
    series: (d.series || []).map((s: any, i: number) => ({
      name: s.name, type: "bar", data: s.data,
      stack: grouped ? undefined : "total",
      barMaxWidth: 22,
      itemStyle: { color: PALETTE[i % PALETTE.length], borderRadius: grouped ? [3, 3, 0, 0] : 0 },
    })),
  };
}

function barOption(rows: any[]) {
  return {
    grid: { left: 8, right: 12, top: 14, bottom: 6, containLabel: true },
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps: any) => `${ps[0].name}<br/>¥${Number(ps[0].value).toLocaleString("zh-CN")}`,
    },
    xAxis: {
      type: "category", data: rows.map((r) => r.name),
      axisLine: { lineStyle: { color: KEY.line } }, axisTick: { show: false },
      axisLabel: { color: KEY.faint, fontSize: 10, interval: 0 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: KEY.faint, fontSize: 10, formatter: (v: number) => (v >= 10000 ? (v / 10000).toFixed(0) + "万" : v) },
      splitLine: { lineStyle: { color: KEY.grid } },
    },
    series: [{ type: "bar", data: rows.map((r) => r.value), barMaxWidth: 26, itemStyle: { color: KEY.accent, borderRadius: [4, 4, 0, 0] } }],
  };
}

export default function DashboardPage({ active }: { active: boolean }) {
  const [range, setRange] = useState<RangeKey>(readRange);
  const [custom, setCustom] = useState<{ start: string; end: string } | null>(readCustom);
  const [showCustom, setShowCustom] = useState(false);
  const [draftStart, setDraftStart] = useState("");
  const [draftEnd, setDraftEnd] = useState("");
  const [source, setSource] = useState<string>(readSource);
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);   // 刷新按钮：范围/源都没变时也能强制重算
  // 双视图：经营看板（业务指标随时间）/ 数据体检（数据本身的质量画像）
  const [view, setView] = useState<"dash" | "health">("dash");
  const [health, setHealth] = useState<any>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr("");
    const q =
      (custom
        ? `/api/dashboard?range=custom&start=${custom.start}&end=${custom.end}`
        : `/api/dashboard?range=${range}`) +
      (source && source !== "builtin" ? `&source=${encodeURIComponent(source)}` : "");

    fetch(q)
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`);
        return body;
      })
      .then((d) => {
        if (!alive) return;
        // 一律渲染后端返回的数据（后端已把失效数据源回退成内置源并带 note），
        // 绝不在前端拦截 —— 之前在这里 return 会让三档时间范围都不刷新
        setData(d);
        if (d?.source_fallback) {
          setSource("builtin");
          localStorage.setItem(LS_SRC, "builtin");
        }
      })
      .catch((e) => {
        if (!alive) return;
        setErr(String(e?.message || e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => { alive = false; };
  }, [range, custom, source, tick]);

  useEffect(() => { localStorage.setItem(LS_RANGE, range); }, [range]);
  useEffect(() => { localStorage.setItem(LS_SRC, source); }, [source]);
  useEffect(() => {
    localStorage.setItem(LS_CUSTOM, JSON.stringify(custom));
  }, [custom]);

  // 数据体检：按当前数据源拉取质量画像（与经营看板共用数据源选择，互不干扰）
  useEffect(() => {
    if (!active || view !== "health") return;
    let alive = true;
    setLoading(true);
    setErr("");
    fetch(`/api/profile/${source === "builtin" ? "builtin" : source}`)
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`);
        return body;
      })
      .then((d) => { if (alive) setHealth(d); })
      .catch((e) => { if (alive) setErr(String(e?.message || e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [active, view, source, tick]);

  function applyCustom() {
    if (!draftStart || !draftEnd || draftStart >= draftEnd) return;
    setCustom({ start: draftStart, end: draftEnd });
    setShowCustom(false);
  }

  /** 删除上传数据源（连带该表与其看板配置）：内置源不可删 */
  async function removeSource(s: any) {
    if (!window.confirm(`删除数据源「${s.label}」？\n该上传表与它的看板配置会一并删除（历史报告不受影响）。`)) return;
    try {
      const r = await fetch(`/api/upload/${encodeURIComponent(s.key)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      if (source === s.key) {
        setSource("builtin");
        localStorage.setItem(LS_SRC, "builtin");
      }
      setTick((t) => t + 1);   // 触发重取：sources 列表少一张表
    } catch (e: any) {
      setErr("删除失败：" + (e?.message || e));
    }
  }

  /** 未生成过看板的数据源：点选时就地触发 AI 生成，成功后自动切换过去。
      全程留在看板页 —— 之前入口藏在数据页行内，用户根本找不到。 */
  const [genBusy, setGenBusy] = useState<string | null>(null);
  const [genErr, setGenErr] = useState("");

  async function pickSource(s: any) {
    if (s.ready !== false || s.key === "builtin") {
      setGenErr("");
      setSource(s.key);
      return;
    }
    if (genBusy) return;
    setGenBusy(s.key);
    setGenErr("");
    try {
      const r = await fetch(`/api/dashboard/spec/${encodeURIComponent(s.key)}`, { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) {
        setGenErr("生成失败：" + (d.error || d.detail || `HTTP ${r.status}`));
        return;
      }
      if (d.suitable === false) {
        setGenErr(`这份数据暂不适合生成看板：${d.reason || "缺少时间列或数值列"}`);
        return;
      }
      setSource(s.key);
      setTick((t) => t + 1);   // sources 列表里这张表变成 ✦ ready，需要重取
    } catch (e: any) {
      setGenErr("生成失败：" + e.message);
    } finally {
      setGenBusy(null);
    }
  }

  const cards = data?.cards || [];

  const charts = useMemo(() => {
    if (!data) return {};
    const out: Record<string, any> = {};
    for (const c of cards) {
      const d = data.charts?.[c.slot];
      if (!d) continue;
      if (c.kind === "line") out[c.slot] = lineOption(d);
      else if (c.kind === "pie") out[c.slot] = pieOption(d);
      else if (c.kind === "bar") out[c.slot] = barOption(d);
      else if (c.kind === "scatter") out[c.slot] = scatterOption(d);
      else if (c.kind === "stack") out[c.slot] = crossOption(d, false);
      else if (c.kind === "group") out[c.slot] = crossOption(d, true);
    }
    return out;
  }, [data, cards]);

  const sources = data?.sources?.length ? data.sources : [{ key: "builtin", label: "内置演示数据" }];

  return (
    <section className="page page-scroll" hidden={!active}>
      <div className="db-wrap">
        <div className="db-head">
          <div>
            <div className="db-title">{data?.title || "销售运营看板"}</div>
            <div className="db-sub">
              {(data?.window?.label || "")}{data?.window?.label ? " · " : ""}固定查询直达，不走模型
            </div>
          </div>
          <div className="db-actions">
            <div className="db-ranges">
              <button className={"db-range" + (view === "dash" ? " active" : "")} onClick={() => setView("dash")} title="业务指标随时间的表现">
                经营看板
              </button>
              <button className={"db-range" + (view === "health" ? " active" : "")} onClick={() => setView("health")} title="数据本身的质量画像（空值/异常/分布）">
                数据体检
              </button>
            </div>
            <div className="db-ranges">
              {sources.map((s: any) => {
                const busy = genBusy === s.key;
                const label =
                  s.ready === false
                    ? busy ? "生成中…" : `${s.label} · 生成看板`
                    : (s.smart ? "✦ " : "") + s.label;
                const isUp = /^up_/.test(s.key);
                return (
                  <span key={s.key} className="db-src-wrap">
                    <button
                      className={"db-range" + (s.key === source ? " active" : "")}
                      disabled={busy}
                      onClick={() => void pickSource(s)}
                      title={
                        busy ? "AI 正在分析数据并规划看板（约 5~15 秒）"
                        : s.ready === false ? "点此让 AI 为这张表生成专属看板"
                        : s.time ? `历史看板 · 生成于 ${s.time}` : "切换数据源"
                      }
                    >
                      {label}
                    </button>
                    {isUp ? (
                      <button
                        className="db-src-del"
                        title={`删除数据源「${s.label}」`}
                        onClick={() => void removeSource(s)}
                      >
                        ×
                      </button>
                    ) : null}
                  </span>
                );
              })}
            </div>
            <div className="db-ranges">
              {view === "dash" &&
                RANGES.map((k) => (
                  <button
                    key={k}
                    className={"db-range" + (!custom && k === range ? " active" : "")}
                    onClick={() => { setCustom(null); setShowCustom(false); setRange(k); }}
                    title="切换时间范围"
                  >
                    {RANGE_LABEL[k]}
                  </button>
                ))}
              {view === "dash" && (
                <button
                  className={"db-range" + (custom ? " active" : "")}
                  onClick={() => {
                    setShowCustom((v) => !v);
                    setDraftStart(custom?.start || "");
                    setDraftEnd(custom?.end || "");
                  }}
                  title="自定义起止日期"
                >
                  自定义{custom ? ` · ${custom.start} ~ ${custom.end}` : ""}
                </button>
              )}
            </div>
            {view === "dash" && showCustom ? (
              <div className="db-custom">
                <label>起 <input type="date" value={draftStart} onChange={(e) => setDraftStart(e.target.value)} /></label>
                <label>止 <input type="date" value={draftEnd} onChange={(e) => setDraftEnd(e.target.value)} /></label>
                <button className="db-range active" disabled={!draftStart || !draftEnd || draftStart >= draftEnd} onClick={applyCustom}>
                  应用
                </button>
                <button className="db-range" onClick={() => setShowCustom(false)}>收起</button>
              </div>
            ) : null}
            <button className="btn-db-ghost" onClick={() => setTick((t) => t + 1)} disabled={loading}>
              {loading ? "计算中…" : "刷新"}
            </button>
          </div>
        </div>

        {genErr ? <p className="ev-empty" style={{ color: "var(--bad)" }}>{genErr}</p> : null}
        {err ? <p className="ev-empty">读取失败：{err}</p> : null}
        {!data && loading ? <p className="ev-empty">正在计算看板…</p> : null}

        {sources.length <= 1 ? (
          <p className="ev-empty" style={{ border: "1px dashed var(--line)", borderRadius: 10, padding: "14px 16px", lineHeight: 1.9 }}>
            想看你自己的数据？在<b>数据页</b>上传 CSV / Excel 并确认入库后回到本页——
            顶部会出现你的表名，点它即可让 AI 现场生成专属看板（KPI 与图表按数据内容自动规划），
            生成过的看板会一直留在历史列表里。
          </p>
        ) : null}

        {view === "health" ? (
          <div className="db-shell">
            {(health?.profiles || []).map((p: any) => (
              <HealthCard key={p.table} p={p} />
            ))}
            {health && !(health.profiles || []).length ? (
              <p className="ev-empty">没有可体检的表</p>
            ) : null}
          </div>
        ) : data ? (
          <div className={"db-shell" + (loading ? " is-loading" : "")}>
            <div className="db-kpis">
              {(data.kpis || []).map((k: any) => {
                const up = Number(k.delta) >= 0;
                const tone = k.delta == null ? "flat" : up === k.good_when_up ? "good" : "bad";
                return (
                  <div className="db-kpi" key={k.key} title={k.hint || ""}>
                    <div className="db-kpi-label">{k.label}</div>
                    <div className="db-kpi-value">{fmtKpi(k)}</div>
                    <div className={"db-kpi-delta " + tone}>{fmtDelta(k)}</div>
                  </div>
                );
              })}
            </div>

            <div className="db-grid">
              {cards.map((c: any) =>
                c.kind === "products" ? (
                  <div className={"db-card" + (c.wide ? " wide" : "")} key={c.slot}>
                    <div className="db-card-title">
                      {c.title}
                      <span>{c.sub || ""}</span>
                    </div>
                    <div className="db-products">
                      <ProductsTable rows={data.charts?.[c.slot] || []} nameLabel={c.name_label} valLabel={c.val_label} />
                    </div>
                  </div>
                ) : (
                  <div className={"db-card" + (c.wide ? " wide" : "")} key={c.slot}>
                    <div className="db-card-title">
                      {c.title}
                      <span>{c.sub || ""}</span>
                    </div>
                    {charts[c.slot] ? (
                      /* 尺寸完全交给旧版类名：趋势 300(.db-chart-lg)，饼/柱 240(.db-chart) */
                      <ChartBox
                        bare
                        option={charts[c.slot]}
                        className={"db-chart" + (c.wide ? " db-chart-lg" : "")}
                      />
                    ) : (
                      <p className="ev-empty">暂无数据</p>
                    )}
                  </div>
                )
              )}
            </div>
          </div>
        ) : null}

        {data?.note ? <div className="db-note">{data.note}</div> : null}
      </div>
    </section>
  );
}

/** 数据体检卡：单张表的质量画像（列/空值率/分布/异常行 + 确定性提示） */
function HealthCard({ p }: { p: any }) {
  const cols = p.columns || [];
  const notes = p.notes || [];
  return (
    <div className="db-card wide hp-card">
      <div className="db-card-title">
        {p.ok === false ? `${p.table || "未知表"} · 体检失败` : `${p.table} · ${p.rows} 行`}
        <span>数据质量画像 · 确定性统计</span>
      </div>
      {p.ok === false ? (
        <div className="detail-bad">{p.reason}</div>
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <table className="db-table">
              <thead>
                <tr>
                  <th>列</th><th>类型</th><th className="num">空值率</th>
                  <th className="num">取值数</th><th className="num">均值</th><th className="num">异常行</th>
                </tr>
              </thead>
              <tbody>
                {cols.map((c: any) => (
                  <tr key={c.name}>
                    <td>{c.name}</td>
                    <td>{c.type}</td>
                    <td className="num">{c.null_rate != null ? (c.null_rate * 100).toFixed(1) + "%" : "—"}</td>
                    <td className="num">{c.distinct ?? "—"}</td>
                    <td className="num">{c.avg != null ? Number(c.avg).toLocaleString("zh-CN", { maximumFractionDigits: 2 }) : "—"}</td>
                    <td className="num">{c.outliers ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {notes.length ? (
            <div style={{ marginTop: 8 }}>
              {notes.map((n: string, k: number) => (
                <div key={k} className="detail-line insight-line warn">{n}</div>
              ))}
            </div>
          ) : (
            <div className="detail-line" style={{ color: "var(--ok)", marginTop: 8 }}>未检出质量问题</div>
          )}
        </>
      )}
    </div>
  );
}

function ProductsTable({ rows, nameLabel = "商品", valLabel = "GMV" }: { rows: any[]; nameLabel?: string; valLabel?: string }) {
  if (!rows.length) return <p className="ev-empty">暂无数据</p>;
  return (
    <table className="db-table">
      <thead>
        <tr>
          <th>#</th>
          <th>{nameLabel}</th>
          <th className="num">{valLabel}</th>
          <th className="num">订单</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td className="rank">{i + 1}</td>
            <td className="pname">{r.name}</td>
            <td className="num">{Number(r.value).toLocaleString("zh-CN", { maximumFractionDigits: 0 })}</td>
            <td className="num">{Number(r.orders ?? 0).toLocaleString("zh-CN")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
