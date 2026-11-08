import Icon from "./Icon";

export default function Topbar({
  dark,
  onToggleTheme,
  title = "Deep Dive · AI 数据助理",
}: {
  dark: boolean;
  onToggleTheme: () => void;
  title?: string;
}) {
  return (
    <header className="topbar">
      {/* 顶栏标题跟随模式：工作区各页显示页名，聊天会话显示会话标题（第一句问题） */}
      <div className="tb-title" id="tbTitle">
        {title}
      </div>
      {/* 主题切换放顶栏右侧：侧栏底部会被拉长的会话历史挤出视野 */}
      <div className="tb-actions">
        <button
          className="tb-btn"
          title={dark ? "切换浅色模式" : "切换深色模式"}
          onClick={onToggleTheme}
        >
          <Icon name={dark ? "sun" : "moon"} size={16} />
        </button>
      </div>
    </header>
  );
}
