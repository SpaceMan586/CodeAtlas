import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const backendOrigin =
    env.VITE_API_BASE_URL?.trim() || "http://127.0.0.1:5000";

  return {
    plugins: [react(), tailwindcss()],
    test: {
      include: ["src/**/*.{test,spec}.{ts,tsx}"],
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      clearMocks: true,
      coverage: {
        provider: "v8",
        reportsDirectory: "./coverage",
        reporter: ["text", "html"],
        exclude: ["src/**/*.test.ts", "src/**/*.test.tsx", "src/test/**", "src/main.tsx", "src/vite-env.d.ts"],
      },
    },
    server: {
      proxy: {
        "/api": {
          target: backendOrigin,
          changeOrigin: true,
        },
      },
    },
  };
});
