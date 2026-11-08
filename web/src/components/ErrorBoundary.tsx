import { Component, type ReactNode } from "react";

/**
 * 渲染错误边界。
 *
 * 为什么必须有：一条消息里的某个组件抛错（例如后端返回了没预料到的数据形状），
 * React 会把**整棵树**卸载 —— 表现就是「聊着聊着界面全空了、会话没了」。
 * 有了边界，坏的只是这一条消息，其余对话和导航都还在。
 */
export default class ErrorBoundary extends Component<
  { children: ReactNode; label?: string },
  { err: Error | null }
> {
  state: { err: Error | null } = { err: null };

  static getDerivedStateFromError(err: Error) {
    return { err };
  }

  componentDidCatch(err: Error, info: unknown) {
    // eslint-disable-next-line no-console
    console.error("[Deep Dive] 渲染失败", this.props.label || "", err, info);
  }

  render() {
    const e = this.state.err;
    if (e) {
      return (
        <div className="render-error">
          <span>这段内容渲染出错：{e.message || "未知错误"}</span>
          <button onClick={() => this.setState({ err: null })}>重试</button>
        </div>
      );
    }
    return this.props.children;
  }
}
