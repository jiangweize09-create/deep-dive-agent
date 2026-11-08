import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 产物直接输出到 FastAPI 托管的目录：/static 挂在 frontend/ 上，
// 所以 base 用 /static/dist/，构建后访问路径为 /static/dist/assets/*.js
// 构建时间戳：页面 console 里能直接看出当前跑的是哪一次构建，
// 排查「改了没生效」时不用再猜是缓存还是没重启
const BUILD_ID = new Date().toISOString().replace("T", " ").slice(0, 19);

export default defineConfig({
  plugins: [react()],
  base: "/static/dist/",
  define: {
    __BUILD_ID__: JSON.stringify(BUILD_ID),
  },
  build: {
    outDir: "../frontend/dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    // 开发时把 API 代理到 FastAPI，前端热更新、后端不用重启
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
