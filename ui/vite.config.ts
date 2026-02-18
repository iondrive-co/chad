import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiPort = process.env.CHAD_API_PORT || "8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    open: true,
    proxy: {
      "/api": {
        target: `http://localhost:${apiPort}`,
        changeOrigin: true,
      },
      "/status": {
        target: `http://localhost:${apiPort}`,
        changeOrigin: true,
      },
      "/ws": {
        target: `ws://localhost:${apiPort}`,
        ws: true,
      },
    },
  },
});
