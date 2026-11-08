import { useCallback, useEffect, useRef, useState } from "react";

type Table = { name: string; display?: string; rows?: number; column_count?: number; pii_columns?: string[]; uploaded_at?: string };
type Doc = { name: string; size_kb: number; uploaded_at: string };
type Report = { id: string; title: string; created: string; size_kb: number; url: string };

export default function DataPage({ active, onAsk }: { active: boolean; onAsk: (q: string) => void }) {
  const [builtin, setBuiltin] = useState<{ name: string; rows: number | null; display?: string; description?: string }[]>([]);
  const [tables, setTables] = useState<Table[]>([]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [totals, setTotals] = useState<any>({});
  const [draft, setDraft] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [specBusy, setSpecBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [dsR, wsR] = await Promise.all([fetch("/api/datasources"), fetch("/api/workspace")]);
      const ds = await dsR.json();
      const ws = await wsR.json();
      setBuiltin(ds.builtin?.tables || []);
      setTables(ws.tables || []);
      setDocs(ws.docs || []);
      setReports(ws.reports || []);
      setTotals(ws.totals || {});
    } catch (e: any) {
      setMsg("读取失败：" + e.message);
    }
  }, []);

  useEffect(() => {
    if (active) void load();
  }, [active, load]);

  async function onFile(f: File) {
    setBusy(true);
    setMsg("");
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch("/api/upload", { method: "POST", body: fd });
      const d = await r.json();
      if (!r.ok) {
        setMsg("上传失败：" + (d.detail?.reason || d.detail || `HTTP ${r.status}`));
        return;
      }
      if (d.kind === "doc") {
        setMsg(`已入知识库：${f.name}`);
        await load();
      } else {
        setDraft(d); // 数据文件需要确认表名后才入库
      }
    } catch (e: any) {
      setMsg("上传失败：" + e.message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmDraft() {
    if (!draft) return;
    setBusy(true);
    try {
      const r = await fetch("/api/upload/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft_id: draft.draft_id, table: draft.table }),
      });
      const d = await r.json();
      if (!r.ok) {
        setMsg("入库失败：" + (d.detail?.reason || `HTTP ${r.status}`));
        return;
      }
      setDraft(null);
      setMsg(
        `已入库 ${d.table} · ${Number(d.rows || 0).toLocaleString()} 行` +
        (d.spec_pending ? " · AI 正在生成看板（约 5~15 秒），完成后到「看板」页顶部切换数据源查看" : "")
      );
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string) {
    if (!confirm(`删除「${name}」？删除后 Agent 将不能再查询或检索它。`)) return;
    await fetch(`/api/upload/${encodeURIComponent(name)}`, { method: "DELETE" });
    await load();
  }

  /** 删除历史报告：文件（HTML+Excel）与产物登记一并清理 */
  async function removeReport(id: string) {
    if (!confirm(`删除报告 ${id}？文件将一并删除，不可恢复。`)) return;
    await fetch(`/api/report/${encodeURIComponent(id)}`, { method: "DELETE" });
    await load();
  }

  /** Smart Dashboard：LLM 读一次 schema+样本生成看板 spec（之后看板渲染零 LLM 参与） */
  async function genSpec(name: string) {
    setSpecBusy(name);
    setMsg("");
    try {
      const r = await fetch(`/api/dashboard/spec/${encodeURIComponent(name)}`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) {
        setMsg("生成失败：" + (d.detail || `HTTP ${r.status}`));
        return;
      }
      if (!d.ok) {
        setMsg("生成失败：" + (d.error || "未知错误"));
        return;
      }
      if (d.suitable === false) {
        setMsg(`这份数据暂不适合生成看板：${d.reason}`);
        return;
      }
      setMsg(`看板已生成（${d.title} · KPI ${d.kpi_count} 个 · 图表 ${d.chart_count} 张）——去「看板」页顶部切换数据源即可查看，历史记录会一直保留`);
    } catch (e: any) {
      setMsg("生成失败：" + e.message);
    } finally {
      setSpecBusy(null);
      await load();
    }
  }

  return (
    <section className="page page-scroll" hidden={!active}>
      <div className="pg-wrap pg-wide">
        <div className="pg-title">数据</div>
        <div className="pg-sub">表进语义层（Agent 可查）· 文档进知识库（可检索）· 报告是单文件 HTML（可离线打开）</div>

        <div className="pg-stats">
          <div className="pg-stat"><span>{totals.tables ?? 0}</span>张上传表</div>
          <div className="pg-stat"><span>{totals.docs ?? 0}</span>份文档</div>
          <div className="pg-stat"><span>{totals.reports ?? 0}</span>份报告</div>
          <div className="pg-stat"><span>{Number(totals.rows ?? 0).toLocaleString("zh-CN")}</span>行上传数据</div>
        </div>

        <div className="pg-shell">
          <div className="pg-card">
            <div className="pg-card-head">
              上传文件
              <span className="pg-card-sub">CSV / Excel → 确认后入库可查；Word / PDF → 进知识库</span>
            </div>
            <div className="src-drop" onClick={() => fileRef.current?.click()}>
              <div className="src-drop-main">{busy ? "处理中…" : "拖拽或点击选择文件"}</div>
              <div className="src-drop-sub">数据文件会在下方出现确认条，确认表名与字段即入库</div>
            </div>
            <input
              ref={fileRef}
              type="file"
              hidden
              accept=".csv,.xlsx,.txt,.md,.json,.docx,.pptx,.pdf"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void onFile(f);
                e.target.value = "";
              }}
            />

            {draft ? (
              <div style={{ borderTop: "1px solid var(--line-2)", paddingTop: 10, marginTop: 8 }}>
                <div style={{ fontSize: 12.5, marginBottom: 6 }}>
                  待确认：<b>{draft.table}</b> · {Number(draft.rows || 0).toLocaleString()} 行 ·{" "}
                  {(draft.columns || []).length} 列
                  {(draft.pii_columns || []).length ? ` · PII ${draft.pii_columns.join("、")}` : ""}
                </div>
                <div className="pg-actions" style={{ padding: 0 }}>
                  <button className="btn-primary" onClick={() => void confirmDraft()} disabled={busy}>
                    确认入库
                  </button>
                  <button className="btn-db-ghost" onClick={() => setDraft(null)}>取消</button>
                </div>
              </div>
            ) : null}

            {msg ? <div className="pg-note">{msg}</div> : null}
          </div>

          <div className="dt-grid">
            <div className="dt-col">
              <div className="pg-card">
                <div className="pg-card-head">
                  内置数据库
                  <span className="pg-card-sub">演示用虚构电商库 · DuckDB · 只读连接</span>
                </div>
                {builtin.length ? (
                  builtin.map((t) => (
                    <div className="ws-row" key={t.name}>
                      <div className="ws-main">
                        <div className="ws-name">
                          {t.display || t.name}
                          <span className="ws-en">{t.name}</span>
                        </div>
                        {t.description ? <div className="ws-desc">{t.description}</div> : null}
                        <div className="ws-meta">
                          {t.rows == null ? "行数未知" : Number(t.rows).toLocaleString() + " 行"} · 只读
                        </div>
                      </div>
                      <div className="ws-side">
                        <button
                          className="ws-open"
                          title="跳到对话页，自动发一条关于这张表的问题"
                          onClick={() => onAsk(`用 ${t.name} 表看看数据`)}
                        >
                          问这张表
                        </button>
                      </div>
                    </div>
                  ))
                ) : (
                  <p className="ev-empty">内置库未初始化，先运行 python -m app.seed_data</p>
                )}
              </div>

              <div className="pg-card">
                <div className="pg-card-head">
                  我上传的表<span className="pg-card-sub">parquet + TEMP VIEW</span>
                </div>
                {tables.length ? (
                  tables.map((t) => (
                    <div className="ws-row" key={t.name}>
                      <div className="ws-main">
                        <div className="ws-name">{t.display || t.name}</div>
                        <div className="ws-meta">
                          {t.name} · {Number(t.rows || 0).toLocaleString()} 行 · {t.column_count} 列
                          {(t.pii_columns || []).length ? ` · PII ${(t.pii_columns || []).join("、")}` : ""}
                        </div>
                      </div>
                      <div className="ws-side">
                        <button
                          className="ws-open"
                          disabled={specBusy === t.name}
                          title="AI 分析这张表并生成专属看板（看板页可切换查看）"
                          onClick={() => void genSpec(t.name)}
                        >
                          {specBusy === t.name ? "生成中…" : "Smart Dashboard"}
                        </button>
                        <button className="ws-open" onClick={() => onAsk(`用 ${t.name} 表看看数据`)}>
                          问这张表
                        </button>
                        <button className="ws-del" onClick={() => void remove(t.name)}>删除</button>
                      </div>
                    </div>
                  ))
                ) : (
                  <p className="ev-empty">还没有上传过表。点上方上传区传 CSV / Excel，确认入库后 Agent 立刻能查。</p>
                )}
              </div>
            </div>

            <div className="dt-col">
              <div className="pg-card">
                <div className="pg-card-head">我的文档 · 知识库</div>
                {docs.length ? (
                  docs.map((d) => (
                    <div className="ws-row" key={d.name}>
                      <div className="ws-main">
                        <div className="ws-name">{d.name}</div>
                        <div className="ws-meta">{d.size_kb} KB · 已进检索索引</div>
                      </div>
                      <div className="ws-side">
                        <button className="ws-del" onClick={() => void remove(d.name)}>删除</button>
                      </div>
                    </div>
                  ))
                ) : (
                  <p className="ev-empty">还没有上传文档。Word / PPT / PDF / MD / TXT 上传后自动进知识库。</p>
                )}
              </div>

              <div className="pg-card">
                <div className="pg-card-head">
                  历史报告<span className="pg-card-sub">区块内滚动</span>
                </div>
                <div className="dt-scroll">
                  {reports.length ? (
                    reports.map((r) => (
                      <div className="ws-row" key={r.id}>
                        <div className="ws-main">
                          <div className="ws-name">{r.title}</div>
                          <div className="ws-meta">{r.id} · {r.size_kb} KB</div>
                        </div>
                        <div className="ws-side">
                          <a className="ws-open" href={r.url} target="_blank" rel="noopener">打开</a>
                          <button
                            className="ws-del"
                            title="删除报告（HTML + Excel 一并清理）"
                            onClick={() => void removeReport(r.id)}
                          >
                            删除
                          </button>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="ev-empty">还没有生成过报告。在对话里说「近 6 个月 GMV 分析报告」即可。</p>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
