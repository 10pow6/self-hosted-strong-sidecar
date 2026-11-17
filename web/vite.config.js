import { defineConfig } from "vite";

// Dev: `yarn dev` on :5173 proxies API calls to the FastAPI server on :8765.
// Build: `yarn build` writes into strong_app/static, which FastAPI serves at /.
const API = process.env.STRONG_API || "http://127.0.0.1:8765";

export default defineConfig({
  server: {
    port: 5173,
    proxy: Object.fromEntries(["/api", "/docs", "/openapi.json"].map(p => [p, { target: API, changeOrigin: true }])),
  },
  build: { outDir: "../strong_app/static", emptyOutDir: true },
});
