import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import { fileURLToPath, URL } from "url";

/**
 * Portable build config: inlines all JS/CSS into a single index.html.
 *
 * This produces a self-contained HTML file that works when:
 * - Opened directly as a file:// URL in any browser
 * - Deployed on Cloudflare Pages (at root or any subpath)
 * - Served by any static file server
 *
 * ES modules don't load from file:// due to CORS, so inlining is required.
 */
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  resolve: {
    alias: {
      "chad-client": fileURLToPath(new URL("../client/src/index.ts", import.meta.url)),
    },
  },
  build: {
    outDir: "dist-portable",
    emptyOutDir: true,
  },
});
