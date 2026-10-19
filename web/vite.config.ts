import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발: vite dev 서버가 /api 를 FastAPI(8000) 로 프록시한다.
// 배포: npm run build → dist/ 를 FastAPI 가 / 에서 정적 서빙한다 (server.web_dist).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
