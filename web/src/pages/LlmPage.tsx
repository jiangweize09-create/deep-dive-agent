import { useEffect, useMemo, useState } from "react";

const LS_PROFILES = "xihuang_llm_profiles_v1";

const PRESETS: Record<string, { label: string; baseUrl: string; models: string[]; hint: string }> = {
  deepseek: {
    label: "DeepSeek",
    baseUrl: "https://api.deepseek.com/v1",
    models: ["deepseek-v4-flash", "deepseek-v4-pro"],
    hint: "v4-flash 便宜稳定 · v4-pro 强推理（旧 chat/reasoner 已停用）",
  },
  claude: {
    label: "Claude · Anthropic",
    baseUrl: "https://api.anthropic.com/v1",
    models: ["claude-sonnet-4-5", "claude-opus-4-1"],
    hint: "需走 OpenAI 兼容端点",
  },
  glm: {
    label: "智谱 GLM",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    models: ["glm-4.7", "glm-4.7-flash", "glm-4.7-air"],
    hint: "glm-4.7-flash 免费",
  },
  custom: { label: "自定义（OpenAI 兼容）", baseUrl: "", models: [], hint: "OneAPI / vLLM / 中转…" },
};

type Profile = {
  id: string;
  name: string;
  vendor: string;
  baseUrl: string;
  apiKey: string;
  model: string;
  active?: boolean;
};

function load(): Profile[] {
  try {
    const v = JSON.parse(localStorage.getItem(LS_PROFILES) || "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

function maskKey(k: string) {
  return k ? k.slice(0, 3) + "••••" + k.slice(-4) : "未填（走 .env）";
}

export default function LlmPage({ active }: { active: boolean }) {
  const [profiles, setProfiles] = useState<Profile[]>(load);
  const [editing, setEditing] = useState<Profile | "new" | null>(null);
  const [status, setStatus] = useState<Record<string, { ok: boolean; text: string }>>({});

  useEffect(() => {
    localStorage.setItem(LS_PROFILES, JSON.stringify(profiles));
  }, [profiles]);

  const draft = useMemo<Profile>(() => {
    if (editing && editing !== "new") return editing;
    return {
      id: "p" + Date.now().toString(36),
      name: "",
      vendor: "deepseek",
      baseUrl: PRESETS.deepseek.baseUrl,
      apiKey: "",
      model: PRESETS.deepseek.models[0],
    };
  }, [editing]);

  const [form, setForm] = useState<Profile>(draft);
  useEffect(() => {
    if (editing) setForm(draft);
  }, [editing, draft]);

  const save = () => {
    if (!form.name.trim() && !form.model.trim()) return;
    setProfiles((prev) => {
      const exists = prev.some((p) => p.id === form.id);
      if (exists) return prev.map((p) => (p.id === form.id ? form : p));
      return [...prev, { ...form, active: prev.length === 0 }];
    });
    setEditing(null);
  };

  const activate = (id: string) =>
    setProfiles((prev) => prev.map((p) => ({ ...p, active: p.id === id })));

  const remove = (id: string) => {
    if (!confirm("删除这份配置？")) return;
    setProfiles((prev) => prev.filter((p) => p.id !== id));
  };

  const test = async (p: Profile) => {
    setStatus((s) => ({ ...s, [p.id]: { ok: true, text: "测试中…" } }));
    try {
      const r = await fetch("/api/llm/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_url: p.baseUrl, api_key: p.apiKey, model: p.model }),
      });
      const d = await r.json();
      setStatus((s) => ({
        ...s,
        [p.id]: {
          ok: !!d.ok,
          text: d.ok ? `连通 · ${d.latency_ms ?? "?"}ms` : d.error || "失败",
        },
      }));
    } catch (e: any) {
      setStatus((s) => ({ ...s, [p.id]: { ok: false, text: e.message } }));
    }
  };

  const set = (patch: Partial<Profile>) => setForm((f) => ({ ...f, ...patch }));

  return (
    <section className="page page-scroll" hidden={!active}>
      <div className="pg-wrap pg-wide">
        <div className="pg-head-row">
          <div>
            <div className="pg-title">LLM 配置</div>
            <div className="pg-sub">
              一套 Key 一张卡片，随时切换 · 激活卡片用于所有对话 · 全部留空则跟随后端 .env
            </div>
          </div>
          <button className="btn-primary" onClick={() => setEditing("new")}>＋ 新建配置</button>
        </div>

        {editing ? (
          <div className="pg-card" style={{ paddingBottom: 12 }}>
            <div className="pg-card-head">
              {editing === "new" ? "新建配置" : "编辑配置"}
              <span className="pg-card-sub">{PRESETS[form.vendor]?.hint || ""}</span>
            </div>
            <label className="pg-field">
              <span>厂商</span>
              <select
                value={form.vendor}
                onChange={(e) => {
                  const v = e.target.value;
                  set({
                    vendor: v,
                    baseUrl: PRESETS[v]?.baseUrl || "",
                    model: PRESETS[v]?.models?.[0] || "",
                  });
                }}
              >
                {Object.entries(PRESETS).map(([k, p]) => (
                  <option key={k} value={k}>{p.label}</option>
                ))}
              </select>
            </label>
            <label className="pg-field">
              <span>名称</span>
              <input value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="如：DeepSeek 主力" />
            </label>
            <label className="pg-field">
              <span>Base URL</span>
              <input value={form.baseUrl} onChange={(e) => set({ baseUrl: e.target.value })} placeholder="https://..." />
            </label>
            <label className="pg-field">
              <span>API Key</span>
              <input
                type="password"
                value={form.apiKey}
                onChange={(e) => set({ apiKey: e.target.value })}
                placeholder="sk-...（只存本浏览器）"
              />
            </label>
            <label className="pg-field">
              <span>模型</span>
              <input value={form.model} onChange={(e) => set({ model: e.target.value })} placeholder="deepseek-v4-flash" />
            </label>
            <div className="pg-actions">
              <button className="btn-primary" onClick={save}>保存</button>
              <button className="btn-db-ghost" onClick={() => setEditing(null)}>取消</button>
            </div>
          </div>
        ) : null}

        <div className="pg-shell">
          <div className="tool-shell-head">
            <span className="tool-shell-title">配置卡片</span>
            <span className="tool-shell-sub">Key 只存本浏览器 localStorage，不落服务器</span>
          </div>
          <div className="llm-grid">
            {profiles.length === 0 ? (
              <div className="llm-empty">还没有配置卡片 —— 点右上角「新建配置」，或什么都不填直接跟随后端 .env。</div>
            ) : (
              profiles.map((p) => {
                const st = status[p.id];
                return (
                  <div className={"llm-card" + (p.active ? " active" : "")} key={p.id}>
                    <div className="llm-card-top">
                      <span className={"llm-vendor v-" + p.vendor}>{PRESETS[p.vendor]?.label || p.vendor}</span>
                      <b className="llm-name">{p.name || "未命名"}</b>
                      {p.active ? <span className="llm-star">★ 激活中</span> : null}
                    </div>
                    <div className="llm-model">{p.model || "（模型未填）"}</div>
                    <div className="llm-url" title={p.baseUrl}>{p.baseUrl || "→ 跟随后端 .env"}</div>
                    <div className="llm-key">Key：{maskKey(p.apiKey)}</div>
                    <div className={"llm-status" + (st ? (st.ok ? " ok" : " err") : "")}>{st?.text || ""}</div>
                    <div className="llm-actions">
                      {p.active ? null : (
                        <button className="primary" onClick={() => activate(p.id)}>设为激活</button>
                      )}
                      <button onClick={() => void test(p)}>测试</button>
                      <button onClick={() => setEditing(p)}>编辑</button>
                      <button onClick={() => remove(p.id)}>删除</button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div className="pg-note">「测试」会向厂商端点发一次最小补全（max_tokens=8）验证连通与计费。</div>
      </div>
    </section>
  );
}
