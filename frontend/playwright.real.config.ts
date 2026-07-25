import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results/real-e2e-results.json" }],
  ],
  timeout: 180_000,
  expect: {
    timeout: 30_000,
  },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173",
    trace: "on",
    video: "on",
    screenshot: "on",
  },
  projects: [
    {
      name: "chromium-real",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
