import { useEffect, useState } from "react";
import Icon from "./Icon";
import type { Ev } from "../types";

export const TOOL_ICON: Record<string, string> = {
  delegate_sql: "shield",
  delegate_viz: "doc",
  search_metrics: "data",
  search_schema: "data",
  fetch_data: "data",
  rag_retrieve: "data",
  validate_sql: "shield",
  run_sql: "check",
  generate_chart: "bar",
  render_chart: "bar",
  persist_result: "data",
  export_excel: "doc",
  build_report: "doc",
  resolve_date_range: "chip",
  generate_dashboard: "bar",
  ab_test: "trend",
  profile_table: "shield",
  data_coverage: "data",
};

export const TOOL_LABEL: Record<string, string> = {
  delegate_sql: "委派 SQL 子 Agent",
  delegate_viz: "委派报告 Agent",
  search_metrics: "检索指标定义",
  search_schema: "检索表结构",
  fetch_data: "凭句柄取数",
  rag_retrieve: "检索知识库",
  validate_sql: "SQL 护栏校验",
  run_sql: "执行查询",
  generate_chart: "生成图表",
  render_chart: "报告图表",
  persist_result: "结果落表",
  export_excel: "导出 Excel",
  build_report: "组装报告",
  resolve_date_range: "解析时间范围",
  generate_dashboard: "生成 AI 看板",
  ab_test: "A/B 检验",
  profile_table: "数据质量体检",
  data_coverage: "查询覆盖范围",
};

const DELEGATES = new Set(["delegate_sql", "delegate_viz"]);

export type StepItem = { tool: string; args?: Record<string, unknown>; result?: any };

/**
 * 分组：一次委派 + 它在子 Agent 内部跑的步骤算一组。
 *
 * 必须按事件顺序判断归属——子步骤出现在「委派 step」之后、「委派 result」之前。
 * 只看最终 steps 数组分不出来（那时所有 result 都已回填）。
 */
export type StepGroup = { head: StepItem; children: StepItem[]; nested: boolean };

export function buildGroups(events: Ev[]): StepGroup[] {
  const groups: StepGroup[] = [];
  const flat: StepItem[] = [];      // 回填 result 用，按到达顺序
  let cur: StepGroup | null = null;
  let open = false;                 // 当前委派是否还在执行（未收到 result）

  for (const e of events) {
    if (e.type === "step") {
      const item: StepItem = { tool: e.tool, args: e.args };
      flat.push(item);

      if (DELEGATES.has(e.tool)) {
        cur = { head: item, children: [], nested: true };
        groups.push(cur);
        open = true;
      } else if (open && cur) {
        cur.children.push(item);
      } else {
        cur = { head: item, children: [], nested: false };
        groups.push(cur);
      }
    } else if (e.type === "result") {
      for (let i = flat.length - 1; i >= 0; i--) {
        if (flat[i].tool === e.tool && flat[i].result === undefined) {
          flat[i].result = e.content;
          break;
        }
      }
      if (DELEGATES.has(e.tool)) open = false;
    }
  }
  return groups;
}

/** SQL 透明模式：展开查看原文 + 复制 + 手改重跑（与 AI 同护栏，结果不进 AI 上下文） */
function SqlBox({ sql, allowRerun }: { sql: string; allowRerun?: boolean }) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(sql);
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  if (!sql) return null;

  const rerun = () => {
    setBusy(true);
    fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql: draft }),
    })
      .then((r) => r.json())
      .then((d) => { setRes(d); setEditing(false); })
      .catch((e) => setRes({ ok: false, reason: e.message }))
      .finally(() => setBusy(false));
  };

  return (
    <div className="sql-box">
      <div className="sql-box-head">
        <button className="sql-toggle" onClick={() => setOpen((o) => !o)}>
          {open ? "▾" : "▸"} SQL
        </button>
        {open ? (
          <>
            <button
              className="sql-act"
              title="复制 SQL"
              onClick={() => navigator.clipboard?.writeText(editing ? draft : sql)}
            >
              复制
            </button>
            {allowRerun && !editing ? (
              <button className="sql-act" onClick={() => setEditing(true)}>
                手改重跑
              </button>
            ) : null}
          </>
        ) : null}
      </div>
      {open ? (
        editing ? (
          <>
            <textarea className="sql-edit" value={draft} onChange={(e) => setDraft(e.target.value)} rows={5} />
            <div className="sql-actions-row">
              <button className="sql-act primary" disabled={busy || !draft.trim()} onClick={rerun}>
                {busy ? "执行中…" : "重跑"}
              </button>
              <button className="sql-act" onClick={() => { setDraft(sql); setEditing(false); }}>
                还原
              </button>
            </div>
          </>
        ) : (
          <pre className="sql-view">{sql}</pre>
        )
      ) : null}
      {res ? (
        res.ok ? (
          <div className="sql-res">
            <div className="detail-line">
              手改重跑结果 · {res.row_count} 行
              <span className="sql-note">（仅展示，不进入 AI 上下文）</span>
            </div>
            <div style={{ overflowX: "auto" }}>
              <table className="mini-table">
                <thead>
                  <tr>{(res.columns || []).slice(0, 4).map((c: string) => <th key={c}>{c}</th>)}</tr>
                </thead>
                <tbody>
                  {(res.rows || []).slice(0, 3).map((row: any, i: number) => (
                    <tr key={i}>
                      {(res.columns || []).slice(0, 4).map((c: string, j: number) => (
                        <td key={j}>{String(Array.isArray(row) ? row[j] : row?.[c] ?? "")}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div className="detail-bad">{res.reason || "执行失败"}</div>
        )
      ) : null}
    </div>
  );
}

function stateText(tool: string, r: any): string {
  if (!r) return "";
  if (r.ok === false) return "失败";
  if (tool === "generate_chart" || tool === "render_chart") return "已生成";
  if (tool === "persist_result") return "已落表";
  if (tool === "export_excel") return "已导出";
  if (tool === "validate_sql") return "通过";
  if (tool === "run_sql") return `${r.row_count ?? 0} 行`;
  if (tool === "fetch_data") return `${r.row_count ?? 0} 行`;
  if (tool === "delegate_sql" || tool === "delegate_viz") return "已返回";
  if (tool === "generate_dashboard") return r.suitable === false ? "不适合" : "已生成";
  if (tool === "profile_table")
    return `${r.rows ?? 0} 行${(r.notes || []).length ? ` · ${(r.notes || []).length} 项提示` : ""}`;
  if (tool === "data_coverage") return `${(r.tables || []).length} 张表`;
  return "完成";
}

function Detail({ tool, r }: { tool: string; r: any }) {
  if (!r) return null;
  if (r.ok === false) {
    return (
      <>
        <div className="detail-bad">{r.reason || "失败"}</div>
        {r.suggestion ? <div className="detail-suggest">{r.suggestion}</div> : null}
      </>
    );
  }

  if (tool === "generate_chart" || tool === "render_chart") {
    return (
      <div className="detail-line">
        图表类型：{r.chart_type} · {r.row_count} 行数据
      </div>
    );
  }

  if (tool === "persist_result") {
    return (
      <div className="detail-line">
        句柄 {r.handle} · {r.row_count} 行 · 列：{(r.columns || []).join(", ")}
      </div>
    );
  }

  if (tool === "fetch_data") {
    return (
      <>
        <div className="detail-line">列：{(r.columns || []).join(", ")}</div>
        {(r.insights || []).map((i: any, k: number) => (
          <div key={k} className={"detail-line insight-line" + (i.level === "warn" ? " warn" : "")}>
            {(i.level === "warn" ? "注意 · " : "· ") + i.text}
          </div>
        ))}
      </>
    );
  }

  if (tool === "export_excel") {
    return (
      <div className="detail-line">
        {r.file} · {r.row_count} 行
      </div>
    );
  }

  if (tool === "validate_sql") {
    const checks = r.checks || [];
    return (
      <>
        <SqlBox sql={r.sql_out || r.sql || ""} />
        <div className="detail-line">护栏 {checks.length} 项全部通过</div>
        {checks.slice(0, 4).map((c: any, k: number) => (
          <div key={k} className="check-item">
            <Icon name="check" size={11} /> {c.name}
          </div>
        ))}
      </>
    );
  }

  if (tool === "run_sql") {
    const cols: string[] = Array.isArray(r.columns) ? r.columns : [];
    // rows 有两种形状：[[v1,v2]] 或 [{col: v}]。旧版两种都吃，
    // 这里必须兼容，否则对象数组会让 row.map 抛错、整棵 React 树卸载
    const rows: any[] = Array.isArray(r.rows) ? r.rows : [];
    const cell = (row: any, col: string, j: number) =>
      Array.isArray(row) ? row[j] : row?.[col];
    return (
      <>
        <SqlBox sql={r.sql || ""} allowRerun />
        {!cols.length ? (
          <div className="detail-line">0 行</div>
        ) : (
          <div style={{ overflowX: "auto", marginTop: 4 }}>
            <table className="mini-table">
              <thead>
                <tr>
                  {cols.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 3).map((row, i) => (
                  <tr key={i}>
                    {cols.map((c, j) => (
                      <td key={j}>{String(cell(row, c, j) ?? "")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </>
    );
  }

  if (tool === "search_metrics" || tool === "search_schema") {
    const list = r.metrics || r.tables || [];
    if (!list.length) return <div className="detail-line">{r.hint || "无命中"}</div>;
    return (
      <>
        {list.slice(0, 3).map((m: any, k: number) => (
          <div key={k} className="detail-line">
            {m.display_name || m.display || m.name}
          </div>
        ))}
      </>
    );
  }

  if (tool === "generate_dashboard") {
    if (r.suitable === false) return <div className="detail-line">{r.reason}</div>;
    return (
      <div className="detail-line">
        {r.title} · KPI {r.kpi_count} 个 · 图表 {r.chart_count} 张 · 去看板页切换数据源
      </div>
    );
  }

  if (tool === "profile_table") {
    const notes: string[] = r.notes || [];
    return (
      <>
        <div className="detail-line">
          {r.rows} 行 × {(r.columns || []).length} 列
          {notes.length ? ` · ${notes.length} 项质量提示` : " · 未检出质量问题"}
        </div>
        {notes.slice(0, 4).map((n: string, k: number) => (
          <div key={k} className="detail-line insight-line warn">{n}</div>
        ))}
      </>
    );
  }

  if (tool === "data_coverage") {
    const tables: any[] = r.tables || [];
    return (
      <>
        {tables.slice(0, 5).map((t: any, k: number) => (
          <div key={k} className="detail-line">
            {t.table}: {String(t.lo).slice(0, 10)} ~ {String(t.hi).slice(0, 10)}
          </div>
        ))}
        {tables.length > 5 ? <div className="detail-line">…共 {tables.length} 张表</div> : null}
      </>
    );
  }

  if (tool === "delegate_sql" || tool === "delegate_viz") {
    return r.final ? <div className="detail-line">{String(r.final).slice(0, 80)}…</div> : null;
  }

  return null;
}

function Row({ step, small, aborted }: { step: StepItem; small?: boolean; aborted?: boolean }) {
  const failed = step.result?.ok === false;
  return (
    <div className={"step" + (step.result !== undefined ? (failed ? " fail" : " ok") : "") + (small ? " small" : "")}>
      <div className="step-icon">
        <Icon name={TOOL_ICON[step.tool] || "data"} size={small ? 12 : 14} />
      </div>
      <div className="step-main">
        <div className="step-title">
          {TOOL_LABEL[step.tool] || step.tool}
          <span className="step-state">
            {step.result === undefined ? (
              aborted ? "已终止" : <span className="spinner" />
            ) : (
              stateText(step.tool, step.result)
            )}
          </span>
        </div>
        <div className="step-detail">
          <Detail tool={step.tool} r={step.result} />
        </div>
      </div>
    </div>
  );
}

/** 执行中每秒刷新一次，让「模型很慢」和「已经卡死」在界面上能区分开 */
function useElapsed(active: boolean) {
  const [sec, setSec] = useState(0);
  useEffect(() => {
    if (!active) {
      setSec(0);
      return;
    }
    const t0 = Date.now();
    const id = window.setInterval(() => setSec(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return sec;
}

/** 头部一行摘要：正常只给结论，异常才值得展开 */
function summarize(groups: StepGroup[], ms?: number): string {
  const steps = groups.reduce((n, g) => n + 1 + g.children.length, 0);
  const queries = groups
    .flatMap((g) => [g.head, ...g.children])
    .filter((s) => s.tool === "run_sql" || s.tool === "fetch_data").length;
  const charts = groups
    .flatMap((g) => [g.head, ...g.children])
    .filter((s) => (s.tool === "generate_chart" || s.tool === "render_chart") && s.result?.ok).length;
  const fails = groups
    .flatMap((g) => [g.head, ...g.children])
    .filter((s) => s.result?.ok === false).length;

  const parts = [`${steps} 步`];
  if (queries) parts.push(`${queries} 次查询`);
  if (charts) parts.push(`${charts} 张图`);
  if (fails) parts.push(`${fails} 处失败`);
  if (ms) parts.push(`${(ms / 1000).toFixed(1)}s`);
  return parts.join(" · ");
}

export default function ProcessCard({
  groups,
  live = false,
  ms,
  liveThink,
  aborted,
}: {
  groups: StepGroup[];
  live?: boolean;
  ms?: number;
  /** 子 Agent 实时思考行（token 直播）：步骤事件落地前显示在过程卡顶部 */
  liveThink?: string;
  /** 用户已终止：未完成步骤显示「已终止」而非无限 spinner */
  aborted?: boolean;
}) {
  // 用户点过就以用户为准；没点过则跟着执行状态走：
  // 流式中展开（让人看到在动），出结果后自动收起（把空间让给答案）
  const [userChoice, setUserChoice] = useState<boolean | null>(null);
  // Hook 必须全部放在条件 return 之前：否则 groups 从空 → 非空的第一次渲染里
  // Hook 数量由 1 变 3，React 抛 "Rendered more hooks than during the previous
  // render"（生产构建即 error #300），被 ErrorBoundary 接住后整条消息显示
  // 「这段内容渲染出错」——只要模型一发起工具调用就必然触发。
  const elapsed = useElapsed(live);
  if (!groups.length) return null;

  const collapsed = userChoice ?? !live;
  const total = groups.reduce((n, g) => n + 1 + g.children.length, 0);
  const done = groups.reduce(
    (n, g) => n + (g.head.result !== undefined ? 1 : 0) + g.children.filter((c) => c.result !== undefined).length,
    0
  );

  return (
    <div className={"process-card" + (collapsed ? " collapsed" : "")}>
      <div className="pc-head" onClick={() => setUserChoice(!collapsed)}>
        <span className="pc-summary">
          {live && done < total ? (
            <>
              <span className="spinner" /> 执行中 · {done}/{total}
              <span className={"elapsed" + (elapsed >= 60 ? " slow" : "")}>
                {elapsed}s{elapsed >= 60 ? " · 模型响应较慢" : ""}
              </span>
            </>
          ) : (
            <>执行过程 · {summarize(groups, ms)}</>
          )}
        </span>
        <span className="arrow">▼</span>
      </div>
      <div className="pc-body">
        {liveThink ? (
          <div className="live-think">
            <span className="spinner" />
            {liveThink}
          </div>
        ) : null}
        <div className="steps">
          {groups.map((g, i) => (
            <div className={"step-group" + (g.nested ? " nested" : "")} key={i}>
              <Row step={g.head} aborted={aborted} />
              {g.children.length > 0 && (
                <div className="step-children">
                  {g.children.map((c, j) => (
                    <Row key={j} step={c} small aborted={aborted} />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
