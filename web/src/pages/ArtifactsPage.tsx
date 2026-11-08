import { useEffect, useMemo, useState } from "react";
import Icon from "../components/Icon";
import type { Artifact, Session } from "../types";

/**
 * 报告/产物中心：全部已登记交付物的总览页。
 *
 * 数据来自 /api/artifacts（服务端 artifacts.db，持久）——聊天里的产物卡只覆盖
 * 单条消息的上下文，这里提供跨会话的翻阅入口：搜索、按会话跳转、打开/下载。
 * 文件本体在 data/reports/ 落盘，服务重启后仍可访问。
 */
export default function ArtifactsPage({
  active,
  sessions,
  onOpenSession,
}: {
  active: boolean;
  sessions: Session[];
  onOpenSession: (id: string) => void;
}) {
  const [list, setList] = useState<Artifact[]>([]);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const load = (showLoading = true) => {
    if (showLoading) setLoading(true);
    fetch("/api/artifacts")
      .then((r) => r.json())
      .then((d) => {
        setList(d.artifacts || []);
        setErr("");
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (active) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const sessionTitle = (sid: string) => {
    if (!sid || sid === "adhoc") return "系统生成";
    const hit = sessions.find((s) => s.id === sid);
    return hit ? hit.title || "未命名会话" : `会话 ${sid.slice(0, 8)}`;
  };

  const remove = (a: Artifact) => {
    if (!window.confirm(`删除报告「${a.title}」？\nHTML 与 Excel 文件将一并删除，不可恢复。`)) return;
    const rid = (a.url || "").split("/").pop() || "";
    fetch(`/api/report/${encodeURIComponent(rid)}`, { method: "DELETE" })
      .then(() => load(false))
      .catch((e) => setErr(e.message || "删除失败"));
  };

  const visible = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return list.filter(
      (a) =>
        !kw ||
        (a.title + " " + (a.meta?.task || "") + " " + sessionTitle(a.session_id || ""))
          .toLowerCase()
          .includes(kw)
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, q, sessions]);

  const reports = visible.filter((a) => a.kind === "report");
  const totalCharts = visible.reduce((n, a) => n + (a.meta?.charts || 0), 0);
  const totalInsights = visible.reduce((n, a) => n + (a.meta?.insights || 0), 0);

  return (
    <section className="page page-scroll" hidden={!active}>
      <div className="pg-wrap pg-wide">
        <div className="pg-head-row">
          <div>
            <div className="pg-title">报告中心</div>
            <div className="pg-sub">
              共 {visible.length} 份产物 · {totalCharts} 张图表 · {totalInsights} 条确定性洞察
              （服务端持久登记，重启不丢）
            </div>
          </div>
          <div className="rep-head-actions">
            <input
              className="tool-search"
              placeholder="搜索报告标题 / 主题 / 会话…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <button className="tool-refresh" title="重新加载" onClick={() => load()}>
              {loading ? (
                <span className="spinner" style={{ width: 11, height: 11 }} />
              ) : (
                <Icon name="refresh" size={13} />
              )}
              刷新
            </button>
          </div>
        </div>

        {err ? <div className="error-banner">{err}</div> : null}

        {!err && list.length === 0 ? (
          <div className="pg-shell rep-empty">
            <div className="rep-empty-title">还没有任何产物</div>
            <div className="rep-empty-sub">
              在对话里让 Agent 生成分析报告（如「近 6 个月 GMV 趋势报告」），
              生成后会自动登记到这里。
            </div>
          </div>
        ) : null}

        {list.length > 0 && reports.length === 0 && !err ? (
          <div className="pg-shell rep-empty">
            <div className="rep-empty-title">没有匹配「{q}」的报告</div>
            <div className="rep-empty-sub">换个关键词试试，或清空搜索框查看全部。</div>
          </div>
        ) : null}

        {reports.length > 0 ? (
          <div className="pg-shell">
            <div className="rep-list">
              {reports.map((a) => (
                <div key={a.id} className="rep-row">
                  <div className="art-glyph">▤</div>
                  <div className="art-info">
                    <div className="art-title">{a.title}</div>
                    <div className="rep-meta">
                      {(a.created_at || "").replace("T", " ")}
                      {a.meta?.charts ? ` · ${a.meta.charts} 张图表` : ""}
                      {a.meta?.insights ? ` · ${a.meta.insights} 条洞察` : ""}
                      {" · "}
                      <button
                        className="rep-session"
                        title="跳转到该会话"
                        onClick={() => onOpenSession(a.session_id || "")}
                      >
                        {sessionTitle(a.session_id || "")}
                      </button>
                    </div>
                  </div>
                  <div className="art-actions">
                    {a.url ? (
                      <a className="art-btn primary" href={a.url} target="_blank" rel="noreferrer">
                        打开报告
                      </a>
                    ) : null}
                    {a.excel_url ? (
                      <a className="art-btn" href={a.excel_url}>
                        下载 Excel
                      </a>
                    ) : null}
                    <button className="art-btn art-del" title="删除该报告及其文件" onClick={() => remove(a)}>
                      删除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
