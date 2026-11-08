import { useEffect, useMemo, useState } from "react";

const LS_TOOLS = "xihuang_tools_v2";

type Tool = { name: string; label: string; desc: string; locked?: boolean };
type Group = { key: string; name: string; desc: string; tools: Tool[] };

function loadAllow(): string[] | null {
  try {
    const v = JSON.parse(localStorage.getItem(LS_TOOLS) || "null");
    return Array.isArray(v) ? v : null;
  } catch {
    return null;
  }
}

export default function ToolsPage({ active }: { active: boolean }) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [calls, setCalls] = useState<Record<string, number>>({});
  const [allow, setAllow] = useState<string[] | null>(loadAllow);
  const [q, setQ] = useState("");
  const [tab, setTab] = useState("__all__");
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!active) return;
    fetch("/api/tools")
      .then((r) => r.json())
      .then((d) => {
        setGroups(d.groups || []);
        setCalls(d.calls || {});
      })
      .catch((e) => setErr(e.message));
  }, [active]);

  const isOn = (name: string) => allow === null || allow.includes(name);

  const switchable = useMemo(
    () => groups.filter((g) => g.key === "sql").flatMap((g) => g.tools).filter((t) => !t.locked),
    [groups]
  );

  const toggle = (name: string, on: boolean) => {
    // 以全部可开关工具为基准算新白名单；全勾时存 null（= 全开，新增工具自动生效）
    const next = switchable
      .filter((t) => (t.name === name ? on : isOn(t.name)))
      .map((t) => t.name);
    const val = next.length === switchable.length ? null : next;
    setAllow(val);
    localStorage.setItem(LS_TOOLS, JSON.stringify(val));
  };

  const visible = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return groups
      .filter((g) => tab === "__all__" || g.name === tab)
      .map((g) => ({
        group: g.name,
        tools: g.tools.filter(
          (t) => !kw || (t.label + " " + t.name + " " + t.desc + " " + g.name).toLowerCase().includes(kw)
        ),
      }))
      .filter((g) => g.tools.length);
  }, [groups, q, tab]);

  const total = groups.reduce((n, g) => n + g.tools.length, 0);
  const offCount = switchable.filter((t) => !isOn(t.name)).length;

  return (
    <section className="page page-scroll" hidden={!active}>
      <div className="pg-wrap pg-wide">
        <div className="pg-head-row">
          <div>
            <div className="pg-title">工具</div>
            <div className="pg-sub">
              系统工具的描述与开关 · 开关直接决定 SQL 子 Agent 装配哪些工具，关掉后模型连名字都看不到
            </div>
          </div>
          <input
            className="tool-search"
            placeholder="搜索工具名、描述、Agent…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>

        <div className="pill-tabs">
          <button
            className={"pill-tab" + (tab === "__all__" ? " active" : "")}
            onClick={() => setTab("__all__")}
          >
            全部工具
          </button>
          {groups.map((g) => (
            <button
              key={g.name}
              className={"pill-tab" + (tab === g.name ? " active" : "")}
              onClick={() => setTab(g.name)}
            >
              {g.name}
            </button>
          ))}
        </div>

        <div className="pg-shell">
          <div className="tool-shell-head">
            <span className="tool-shell-title">系统工具</span>
            <span className="tool-shell-sub">
              共 {total} 个 · 调用次数为服务启动以来的真实执行量 · 开关下一条消息生效
              {offCount ? ` · 当前已关闭 ${offCount} 个` : ""}
            </span>
          </div>

          {err ? <p className="ev-empty">读取失败：{err}</p> : null}

          <div className="tool-grid">
            {visible.map((grp) =>
              grp.tools.map((t) => {
                const locked = !!t.locked || !switchable.some((x) => x.name === t.name);
                const on = isOn(t.name);
                return (
                  <div className={"tool-card" + (on ? "" : " off")} key={t.name}>
                    <div className="tool-top">
                      <code className="tool-name">{t.name}</code>
                      <span className="tool-owner">{grp.group}</span>
                      <span className="tool-calls-top">
                        调用 {Number(calls[t.name] || 0).toLocaleString("zh-CN")}
                      </span>
                      {locked ? (
                        <span className="tool-tag">不可关闭</span>
                      ) : (
                        <input
                          type="checkbox"
                          className="tool-sw"
                          checked={on}
                          onChange={(e) => toggle(t.name, e.target.checked)}
                        />
                      )}
                    </div>
                    <div className="tool-desc">{t.desc}</div>
                  </div>
                );
              })
            )}
          </div>

          {!visible.length && !err ? <p className="ev-empty">没有匹配的工具</p> : null}
        </div>
      </div>
    </section>
  );
}
