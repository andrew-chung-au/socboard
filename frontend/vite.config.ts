import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// A plain single-page build: index.html plus assets in dist/, no server runtime.
// The router plugin must come before the React plugin.
export default defineConfig({
  plugins: [tanstackRouter({ target: "react", autoCodeSplitting: true }), react(), tailwindcss()],
  resolve: { tsconfigPaths: true },
  // Dev server only: IPv4 loopback so the dev container's forwarded port reaches it, and nothing else can.
  server: { host: "127.0.0.1" },
  build: { outDir: "dist", emptyOutDir: true },
});
