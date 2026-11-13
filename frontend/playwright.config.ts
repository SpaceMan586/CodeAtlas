import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const backend = resolve(root, "backend");
const python = resolve(backend, ".venv", "Scripts", "python.exe");
const frontend = resolve(root, "frontend");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  outputDir: "test-results",
  use: {
    ...devices["Desktop Chrome"],
    // Real installed browser channel. Microsoft Edge is present on the Windows
    // development machine; set CODEATLAS_E2E_CHANNEL="" to use Playwright's
    // downloaded Chromium build instead (requires `npx playwright install`).
    channel: process.env.CODEATLAS_E2E_CHANNEL === undefined ? "msedge" : process.env.CODEATLAS_E2E_CHANNEL || undefined,
    baseURL: "http://127.0.0.1:5173",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // Video needs Playwright's separately downloaded ffmpeg; traces and
    // screenshots provide failure evidence without that extra binary.
    video: "off",
  },
  webServer: [
    {
      // Playwright runs web-server commands through the platform shell
      // (cmd.exe on Windows), so quote the path instead of using PowerShell `&`.
      command: `"${python}" run.py`,
      cwd: backend,
      url: "http://127.0.0.1:5000/api/health",
      env: { CODEATLAS_ENV: "production", PYTHONDONTWRITEBYTECODE: "1" },
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort",
      cwd: frontend,
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
