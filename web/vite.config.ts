import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발: vite dev 서버가 /api 를 FastAPI(8000) 로 프록시한다.
// 배포: npm run build → dist/ 를 FastAPI 가 / 에서 정적 서빙한다 (server.web_dist).
// 메뉴얼 화면(src/pages/Manual.tsx)은 레포 루트의 README.md 와 docs/img 를 빌드 때 읽는다. dev 서버는 프로젝트 루트(web/)
// 밖의 파일을 기본으로 막으므로 레포 루트까지 연다.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
    fs: { allow: [".."] },
  },
  build: { outDir: "dist", sourcemap: false },
});
