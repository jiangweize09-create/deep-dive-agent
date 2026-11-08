import Icon from "./Icon";
import Logo from "./Logo";
import type { PageKey } from "../App";
import type { Session } from "../types";

/** 工作台导航：主侧栏只放工作台四页，设置类收进左下角齿轮 */
const NAV: { group: string; items: { key: PageKey; label: string; icon: string }[] }[] = [
  {
    group: "工作台",
    items: [
      { key: "chat", label: "对话分析", icon: "chat" },
      { key: "dashboard", label: "看板", icon: "dashboard" },
      { key: "data", label: "数据", icon: "data" },
      { key: "reports", label: "报告中心", icon: "doc" },
    ],
  },
];

/** 设置模式导航：进入设置后侧栏整体切换，会话历史/新建会话不再出现 */
export type SettingsTab = "memory" | "tools" | "llm" | "usage";

const SETTINGS_NAV: { key: SettingsTab; label: string; icon: string; hint: string }[] = [
  { key: "memory", label: "个性化", icon: "trend", hint: "记忆：口径偏好 / 分析习惯 / 背景关注" },
  { key: "tools", label: "工具", icon: "tool", hint: "SQL 子 Agent 的工具开关" },
  { key: "llm", label: "LLM 配置", icon: "chip", hint: "模型接入与连通性" },
  { key: "usage", label: "使用统计", icon: "bar", hint: "token 消耗 / 问答量 / 工具排行" },
];


export default function Sidebar({
  page,
  settingsTab = "memory",
  onSettingsTab,
  onNavigate,
  onNewSession,
  sessions,
  currentId,
  onSelect,
  onDelete,
}: {
  page: PageKey;
  settingsTab?: SettingsTab;
  onSettingsTab?: (t: SettingsTab) => void;
  onNavigate: (p: PageKey) => void;
  onNewSession: () => void;
  sessions: Session[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const inSettings = page === "settings";

  if (inSettings) {
    return (
      <aside className="sidebar" id="sidebar">
        <div className="brand">
          <Logo size={38} radius={10} />
          <div className="brand-text">
            <div className="brand-name">Deep Dive</div>
            <div className="brand-sub">设置</div>
          </div>
        </div>

        <button className="btn-back" onClick={() => onNavigate("chat")}>
          <Icon name="caret" size={13} />
          返回工作台
        </button>

        <nav className="snav" id="snav">
          <div>
            <div className="snav-group">设置</div>
            {SETTINGS_NAV.map((it) => (
              <button
                key={it.key}
                className={"snav-item" + (settingsTab === it.key ? " active" : "")}
                onClick={() => onSettingsTab?.(it.key)}
                title={it.hint}
              >
                <span className="snav-icon">
                  <Icon name={it.icon} size={14} />
                </span>
                <span className="snav-label">{it.label}</span>
              </button>
            ))}
          </div>
        </nav>

        {/* 弹性占位：把用户卡压回侧栏最底部（与工作区行为一致） */}
        <div style={{ flex: 1 }} />

        <div className="side-foot">
          <div className="user-card" id="userCard" title="当前登录账号">
            <div className="uc-avatar">C</div>
            <div className="uc-info">
              <div className="uc-name">COLE</div>
              <div className="uc-role">数据分析师 · 演示账号</div>
            </div>
            <Icon name="caret" size={12} className="uc-caret" />
          </div>
          {/* 齿轮高亮：保持几何一致，同时示意「当前位于设置」 */}
          <span className="foot-gear active" title="当前位于设置" aria-hidden>
            <Icon name="gear" size={16} />
          </span>
        </div>
      </aside>
    );
  }

  return (
    <aside className="sidebar" id="sidebar">
      <div className="brand">
        <Logo size={38} radius={10} />
        <div className="brand-text">
          <div className="brand-name">Deep Dive</div>
          <div className="brand-sub">深潜数据 · 洞见自现</div>
        </div>
      </div>

      <button className="btn-new" id="btnNew" onClick={onNewSession}>
        <Icon name="plus" size={14} />
        新建会话
      </button>

      <nav className="snav" id="snav">
        {NAV.map((g) => (
          <div key={g.group}>
            <div className="snav-group">{g.group}</div>
            {g.items.map((it) => (
              <button
                key={it.key}
                className={"snav-item" + (page === it.key ? " active" : "")}
                onClick={() => onNavigate(it.key)}
              >
                <span className="snav-icon">
                  <Icon name={it.icon} size={14} />
                </span>
                <span className="snav-label">{it.label}</span>
              </button>
            ))}
          </div>
        ))}
      </nav>

      <div className="sessions-label">会话历史</div>
      <div className="sessions" id="sessions">
        {sessions.length === 0 ? (
          <div style={{ color: "var(--faint)", fontSize: 12, padding: "8px 10px" }}>暂无历史会话</div>
        ) : (
          sessions.map((s) => (
            <div
              key={s.id}
              className={"session" + (s.id === currentId ? " active" : "")}
              onClick={() => onSelect(s.id)}
            >
              <div className="s-title">{s.title || "新会话"}</div>
              <button
                className="s-del"
                title="删除会话"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(s.id);
                }}
              >
                ×
              </button>
            </div>
          ))
        )}
      </div>

      <div className="side-foot">
        <div className="user-card" id="userCard" title="当前登录账号">
          <div className="uc-avatar">C</div>
          <div className="uc-info">
            <div className="uc-name">COLE</div>
            <div className="uc-role">数据分析师 · 演示账号</div>
          </div>
          <Icon name="caret" size={12} className="uc-caret" />
        </div>
        <button
          className="foot-gear"
          title="设置：记忆与进化 / 工具 / LLM 配置"
          onClick={() => onNavigate("settings")}
        >
          <Icon name="gear" size={16} />
        </button>
      </div>
    </aside>
  );
}
