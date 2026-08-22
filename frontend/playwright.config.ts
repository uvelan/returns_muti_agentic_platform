import { defineConfig, devices } from "@playwright/test";

/**
 * Two stacks, and the second is the point.
 *
 * The audit's finding was that a green browser suite proved less than it
 * appeared to: everything ran against the MSW mock, so "the route works" meant
 * "the route renders what we told it to". A mock cannot answer the question the
 * release gate actually asks -- did the thing persist -- because there is no
 * datastore behind it.
 *
 * So `mock` and `real` are separate projects rather than a flag. The mock
 * project is fast, hermetic and runs on every change; it is allowed to prove
 * *rendered* and *API-shape*, and nothing more. The real project runs against a
 * live backend with its real dependencies and is the only project whose result
 * may be cited for a persisted outcome.
 *
 * **The real project is skipped loudly, never silently.** If
 * `E2E_REAL_BASE_URL` is unset the project still exists and its tests report as
 * skipped with that reason in the run, so a release report shows "0 real-stack
 * tests ran" rather than showing nothing at all. An absent project reads as a
 * passing one.
 */

const REAL_BASE_URL = process.env.E2E_REAL_BASE_URL;
const MOCK_BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:5174";

/**
 * Firefox and WebKit are declared but off by default: only Chromium is
 * installed by `npx playwright install chromium`, and a project referencing a
 * missing browser fails the whole run rather than skipping. Turn them on with
 * `E2E_CROSS_BROWSER=1` once the browsers are installed -- the release gate
 * wants all three for the release-critical journeys.
 */
const CROSS_BROWSER = process.env.E2E_CROSS_BROWSER === "1";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: MOCK_BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "mock-chromium",
      use: { ...devices["Desktop Chrome"], baseURL: MOCK_BASE_URL },
    },
    ...(CROSS_BROWSER
      ? [
          { name: "mock-firefox", use: { ...devices["Desktop Firefox"], baseURL: MOCK_BASE_URL } },
          { name: "mock-webkit", use: { ...devices["Desktop Safari"], baseURL: MOCK_BASE_URL } },
        ]
      : []),
    {
      name: "real-chromium",
      use: {
        ...devices["Desktop Chrome"],
        // Falls back to the mock URL only so the project can construct; every
        // test in it skips when the real URL is absent.
        baseURL: REAL_BASE_URL ?? MOCK_BASE_URL,
      },
    },
  ],
  webServer: {
    command: "npm run dev:mock -- --port 5174 --force",
    url: MOCK_BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
