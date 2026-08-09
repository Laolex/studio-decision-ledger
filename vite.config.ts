import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs separately in development; in production both are served from
// the same origin, so the client always calls a relative /api path.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
