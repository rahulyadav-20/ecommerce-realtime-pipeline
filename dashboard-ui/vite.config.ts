import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": "/src" },
  },
  server: {
    port: 3000,
    proxy: {
      "/api":     { target: "http://localhost:8000", changeOrigin: true },
      "/ws":      { target: "ws://localhost:8000",   changeOrigin: true, ws: true },
      "/metrics": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor:   ["react", "react-dom", "react-router-dom"],
          query:    ["@tanstack/react-query", "@tanstack/react-query-devtools"],
          charts:   ["recharts"],
          http:     ["axios"],
        },
      },
    },
  },
});
