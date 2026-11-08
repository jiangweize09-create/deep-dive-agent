import { useEffect, useState } from "react";
import Sidebar from "./components/Sidebar";
import Topbar from "./components/Topbar";
import ChatPage from "./pages/ChatPage";
import DashboardPage from "./pages/DashboardPage";
import DataPage from "./pages/DataPage";
import ArtifactsPage from "./pages/ArtifactsPage";
import SettingsPage from "./pages/SettingsPage";
import { useChat } from "./hooks/useChat";

export type PageKey = "chat" | "dashboard" | "data" | "reports" | "settings";

const PAGE_TITLES: Record<PageKey, string> = {
  chat: "Deep Dive · AI 数据助理",
  dashboard: "销售运营看板",
  data: "数据",
  reports: "报告中心",
  settings: "设置",
};

const LS_THEME = "xihuang_theme_v1";
const PAGES: PageKey[] = ["chat", "dashboard", "data", "reports", "settings"];

function initialPage(): PageKey {
  const h = location.hash.replace("#", "");
  return PAGES.includes(h as PageKey) ? (h as PageKey) : "chat";
}

export default function App() {
  const [page, setPage] = useState<PageKey>(initialPage);
  // 设置中心的子页（个性化/工具/LLM/使用统计）：由设置模式的侧栏导览切换
  const [settingsTab, setSettingsTab] = useState<import("./components/Sidebar").SettingsTab>("memory");
  const [dark, setDark] = useState(() => {
    try {
      return localStorage.getItem(LS_THEME) === "dark";
    } catch {
      return false;
    }
  });
  const chat = useChat();

  useEffect(() => {
    document.body.classList.toggle("dark", dark);
    try {
      localStorage.setItem(LS_THEME, dark ? "dark" : "light");
    } catch {
      /* 隐私模式 */
    }
  }, [dark]);

  useEffect(() => {
    document.title = `${PAGE_TITLES[page]} · Deep Dive Agent`;
    // hash 记页面：刷新保持当前页，也方便直接分享 / 排查某一页
    try {
      history.replaceState(null, "", "#" + page);
    } catch {
      /* file:// 下可能受限 */
    }
  }, [page]);

  // 监听浏览器前进/后退与手改 hash：否则地址栏变了、页面不跟着变
  useEffect(() => {
    const onHash = () => {
      const h = location.hash.replace("#", "") as PageKey;
      if (PAGES.includes(h)) setPage(h);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // 「问这张表」等跨页引导：切到对话页并直接发送
  const ask = (q: string) => {
    setPage("chat");
    void chat.send(q);
  };

  // 顶栏标题：设置模式带子页名；聊天会话显示会话标题（即第一句问题）
  const SETTINGS_TAB_LABEL = { memory: "个性化", tools: "工具", llm: "LLM 配置", usage: "使用统计" } as const;
  const topbarTitle =
    page === "settings"
      ? `设置 · ${SETTINGS_TAB_LABEL[settingsTab]}`
      : page === "chat" && chat.current?.title
        ? chat.current.title
        : PAGE_TITLES[page];

  return (
    <div className="app">
      <Sidebar
        page={page}
        settingsTab={settingsTab}
        onSettingsTab={(t) => {
          setSettingsTab(t);
          setPage("settings");
        }}
        onNavigate={setPage}
        onNewSession={() => {
          chat.newSession();
          setPage("chat");
        }}
        sessions={chat.sessions}
        currentId={chat.currentId}
        onSelect={(id) => {
          chat.selectSession(id);
          setPage("chat");
        }}
        onDelete={chat.deleteSession}
      />
      <main className="main">
        <Topbar
          dark={dark}
          onToggleTheme={() => setDark((d) => !d)}
          title={topbarTitle}
        />
        <ChatPage active={page === "chat"} chat={chat} />
        <DashboardPage active={page === "dashboard"} />
        <DataPage active={page === "data"} onAsk={ask} />
        <ArtifactsPage
          active={page === "reports"}
          sessions={chat.sessions}
          onOpenSession={(id) => {
            chat.selectSession(id);
            setPage("chat");
          }}
        />
        <SettingsPage active={page === "settings"} tab={settingsTab} />
      </main>
    </div>
  );
}
