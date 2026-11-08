import MemoryPage from "./MemoryPage";
import ToolsPage from "./ToolsPage";
import LlmPage from "./LlmPage";
import UsagePage from "./UsagePage";

import type { SettingsTab } from "../components/Sidebar";

/** 设置中心：记忆 / 工具 / LLM 配置三合一。
    子页切换由「设置模式侧栏导览」驱动（App 侧持有 tab 状态），本组件只做装配。 */
export default function SettingsPage({
  active,
  tab = "memory",
}: {
  active: boolean;
  tab?: SettingsTab;
}) {
  return (
    <>
      <MemoryPage active={active && tab === "memory"} />
      <ToolsPage active={active && tab === "tools"} />
      <LlmPage active={active && tab === "llm"} />
      <UsagePage active={active && tab === "usage"} />
    </>
  );
}
