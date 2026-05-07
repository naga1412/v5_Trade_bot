/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": new URL("src", import.meta.url).pathname },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    // Vite blocks unknown Host headers (DNS-rebinding protection). Allow any
    // *.nagayuaj.com subdomain so Cloudflare Tunnel traffic isn't rejected;
    // the leading dot is Vite's wildcard syntax for subdomains.
    allowedHosts: [".nagayuaj.com"],
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    exclude: ["node_modules", "dist", "tests/e2e/**"],
  },
});
