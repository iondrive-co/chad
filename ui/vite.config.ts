import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "url";

const apiPort = process.env.CHAD_API_PORT || "8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // Resolve chad-client directly from TypeScript source so changes are
      // picked up immediately without rebuilding, and stale node_modules
      // copies can never cause "method is not a function" crashes.
      "chad-client": fileURLToPath(new URL("../client/src/index.ts", import.meta.url)),
    },
  },
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
