import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the browser talks only to this server. Requests under /api are proxied to the
// backend (started on 127.0.0.1:8000 by scripts/dev.ps1), so cookies are same-origin and no CORS
// configuration is needed. The Origin header is forwarded unchanged for the backend CSRF checks.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: { target: "es2022", sourcemap: false },
});
