import { useCallback, useEffect, useRef, useState } from "react";
import Icon from "../components/Icon";

type Pref = {
  id: number;
  category: "caliber" | "workflow" | "profile" | "custom";
  key: string;
  value: string;
  source: "hitl" | "chat" | "manual";
  updated_at: string;
};

const SECTIONS: {
  key: Pref["category"];
  title: string;
  hint: string;
  keyPh: string;
  valuePh: string;
}[] = [
  {
    key: "caliber",
    title: "口径偏好",
    hint: "同名指标的不同算法（如「销售额=净销售额」）。记住了，口径反问永久跳过",
    keyPh: "术语，如：销售额",
    valuePh: "口径内容，如：净销售额（不含退款）",
  },
  {
    key: "workflow",
    title: "分析习惯",
    hint: "你偏好的分析流程与默认行为（如「出报告前先做数据质量体检」）",
    keyPh: "习惯名，如：报告流程",
    valuePh: "习惯内容，如：出报告前先做数据质量体检",
  },
  {
    key: "profile",
    title: "背景与关注",
    hint: "你的职责范围与近期关注（如「负责华南区运营」「关注退款率」），回答会更贴近语境",
    keyPh: "主题，如：职责范围",
    valuePh: "内容，如：负责华南区运营",
  },
  {
    key: "custom",
    title: "自定义指令",
    hint: "报告模板、输出格式等自由要求（如「每份报告末尾附风险提示章节」），原样注入每轮对话",
    keyPh: "指令名，如：报告模板",
    valuePh: "内容，如：每份报告末尾附 2-3 条风险提示",
  },
];

const SOURCE_LABEL: Record<Pref["source"], string> = {
  hitl: "自动 · 口径确认",
  chat: "自动 · 对话记录",
  manual: "手动",
};

export default function MemoryPage({ active }: { active: boolean }) {
  const [prefs, setPrefs] = useState<Pref[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState<Pref["category"] | null>(null);
  const [draftKey, setDraftKey] = useState("");
  const [draftValue, setDraftValue] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(() => {
    fetch("/api/memory")
      .then((r) => r.json())
      .then((d) => setPrefs(d.prefs || []))
      .catch((e) => setErr(e.message));
  }, []);

  useEffect(() => {
    if (active) load();
  }, [active, load]);

  const call = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      setErr("");
      load();
    } catch (e: any) {
      setErr(e.message || "请求失败");
    } finally {
      setBusy(false);
    }
  };

  const add = (category: Pref["category"]) => {
    const key = draftKey.trim();
    const value = draftValue.trim();
    if (!key || !value) return;
    void call(async () => {
      await fetch("/api/memory", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category, key, value }),
      });
      setAdding(null);
      setDraftKey("");
      setDraftValue("");
    });
  };

  const saveEdit = (id: number) => {
    const value = editValue.trim();
    if (!value) return;
    void call(async () => {
      await fetch(`/api/memory/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      });
      setEditing(null);
    });
  };

  const remove = (id: number) => {
    void call(async () => {
      await fetch(`/api/memory/${id}`, { method: "DELETE" });
    });
  };

  const resetAll = () => {
    if (!window.confirm("确定清空全部偏好记忆？AI 将忘记所有已记住的口径与习惯。")) return;
    void call(async () => {
      await fetch("/api/memory/reset", { method: "POST" });
    });
  };

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(prefs, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "deep-dive-memory.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  const importJson = (file: File) => {
    file
      .text()
      .then((text) => {
        const arr = JSON.parse(text);
        if (!Array.isArray(arr)) throw new Error("格式应为偏好数组");
        void call(async () => {
          for (const p of arr) {
            if (!p?.category || !p?.key || !p?.value) continue;
            await fetch("/api/memory", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ category: p.category, key: p.key, value: p.value }),
            });
          }
        });
      })
      .catch((e) => setErr("导入失败：" + e.message));
  };

  const toggleEnabled = () => {
    setBusy(true);
    fetch("/api/memory/enabled", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !enabled }),
    })
      .then((r) => r.json())
      .then(() => setEnabled(!enabled))
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <section className="page page-scroll" hidden={!active}>
      <div className="pg-wrap pg-wide">
        <div className="pg-head-row">
          <div>
            <div className="pg-title">个性化</div>
            <div className="pg-sub">
              AI 跨会话记住你的口径与习惯——记得越多，反问越少、回答越贴合。全部内容此处可见、可编辑，
              与本轮表述冲突时以本轮为准。
            </div>
          </div>
          <div className="rep-head-actions">
            <input
              ref={fileRef}
              type="file"
              accept=".json"
              style={{ display: "none" }}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) importJson(f);
                e.target.value = "";
              }}
            />
            <button className="tool-refresh" disabled={busy} onClick={() => fileRef.current?.click()}>
              <Icon name="clip" size={13} />
              导入
            </button>
            <button className="tool-refresh" disabled={busy || !prefs.length} onClick={exportJson}>
              <Icon name="doc" size={13} />
              导出
            </button>
            <button className="tool-refresh mem-danger" disabled={busy || !prefs.length} onClick={resetAll}>
              <Icon name="refresh" size={13} />
              重置
            </button>
          </div>
        </div>

        {err ? <div className="error-banner">{err}</div> : null}

        {/* 总开关：启用/停用个性化记忆（含成本诚实披露） */}
        <div className="pg-shell mem-switch">
          <div className="mem-switch-info">
            <div className="mem-sec-title">启用个性化记忆</div>
            <div className="mem-sec-hint">
              {enabled
                ? "AI 会跨会话记住你的口径与习惯，并注入每轮对话——可能增加少量模型调用与 token 成本；与本轮表述冲突时以本轮为准。"
                : "记忆已关闭：AI 不会注入你的偏好，口径与数据范围每次都会重新反问（下方已保存的内容保留，随时可重新开启）。"}
            </div>
          </div>
          <button
            className={"mem-toggle" + (enabled ? " on" : "")}
            role="switch"
            aria-checked={enabled}
            disabled={busy}
            onClick={toggleEnabled}
          >
            <span className="mem-toggle-dot" />
            <span className="mem-toggle-text">{enabled ? "已开启" : "已关闭"}</span>
          </button>
        </div>

        <div className="mem-sections">
          {SECTIONS.map((s) => {
            const items = prefs.filter((p) => p.category === s.key);
            return (
              <div key={s.key} className="pg-shell mem-section">
                <div className="mem-sec-head">
                  <div>
                    <div className="mem-sec-title">{s.title}</div>
                    <div className="mem-sec-hint">{s.hint}</div>
                  </div>
                  <button
                    className="tool-refresh"
                    onClick={() => {
                      setAdding(adding === s.key ? null : s.key);
                      setDraftKey("");
                      setDraftValue("");
                    }}
                  >
                    <Icon name="plus" size={13} />
                    添加
                  </button>
                </div>

                {adding === s.key ? (
                  <div className="mem-add">
                    <input
                      className="plan-edit-input mem-key"
                      placeholder={s.keyPh}
                      value={draftKey}
                      onChange={(e) => setDraftKey(e.target.value)}
                    />
                    <textarea
                      className="plan-edit-input"
                      placeholder={s.valuePh}
                      value={draftValue}
                      onChange={(e) => setDraftValue(e.target.value)}
                      rows={2}
                    />
                    <div className="plan-actions">
                      <button
                        className="plan-btn primary"
                        disabled={!draftKey.trim() || !draftValue.trim() || busy}
                        onClick={() => add(s.key)}
                      >
                        保存
                      </button>
                      <button className="plan-btn" onClick={() => setAdding(null)}>
                        取消
                      </button>
                    </div>
                  </div>
                ) : null}

                {items.length === 0 && adding !== s.key ? (
                  <div className="mem-empty">
                    还没有这类记忆——在对话里说「记住…」「以后都…」，或点右上角「添加」。
                  </div>
                ) : null}

                <div className="mem-list">
                  {items.map((p) => (
                    <div key={p.id} className="rep-row mem-row">
                      <div className="art-info">
                        <div className="mem-key-label">{p.key}</div>
                        {editing === p.id ? (
                          <div className="mem-edit">
                            <textarea
                              className="plan-edit-input"
                              value={editValue}
                              onChange={(e) => setEditValue(e.target.value)}
                              rows={2}
                            />
                            <div className="plan-actions">
                              <button
                                className="plan-btn primary"
                                disabled={!editValue.trim() || busy}
                                onClick={() => saveEdit(p.id)}
                              >
                                保存
                              </button>
                              <button className="plan-btn" onClick={() => setEditing(null)}>
                                取消
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div className="mem-value">{p.value}</div>
                        )}
                        <div className="rep-meta">
                          {SOURCE_LABEL[p.source] || p.source} · {p.updated_at.replace("T", " ")}
                        </div>
                      </div>
                      <div className="art-actions">
                        {editing === p.id ? null : (
                          <>
                            <button
                              className="art-btn"
                              onClick={() => {
                                setEditing(p.id);
                                setEditValue(p.value);
                              }}
                            >
                              编辑
                            </button>
                            <button className="art-btn mem-del" onClick={() => remove(p.id)}>
                              删除
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
