import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/app.css";

declare const __BUILD_ID__: string;

// 排查「改了没生效」：F12 控制台第一行就是构建时间，对不上就是缓存或没重新 build
// eslint-disable-next-line no-console
console.info(`[Deep Dive] React 版构建于 ${__BUILD_ID__}`);

// 不包 StrictMode：开发态会双调用 effect，后续接入 SSE 时会产生重复请求，
// 排查起来容易误判成后端问题。需要严格模式时再单独打开。
ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
