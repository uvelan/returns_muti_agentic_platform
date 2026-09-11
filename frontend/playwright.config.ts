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
    /**
     * CFG-4's own real-stack specs -- one per typed screen, under `../e2e`
     * rather than `./tests`. A dedicated project with its own `testDir`
     * rather than folding them into `real-chromium` (which runs everything
     * under `./tests`, `canonical-routes.spec.ts` included): these publish a
     * real configuration release and revert it, which is a materially
     * different risk profile from a read-only route sweep, and keeping them
     * in their own project means running the route sweep can never
     * accidentally also mutate a live release. Real-stack only, by the same
     * `requireRealStack()` gate every real-stack spec in this suite uses --
     * see `../tests/realStack.ts`.
     *
     * **Run this project with `--workers=1`.** Each spec reads the
     * configuration head revision, edits, and publishes with that revision
     * as its optimistic lock (`POST /api/config/publish`'s
     * `expected_head_revision`) -- the same lock two operators editing the
     * same release concurrently trip. Two of these specs racing (the
     * default across CPU cores) publish against the same starting head, and
     * the loser gets a real 409 `CONFIGURATION_REVISION_CONFLICT`, mid-test,
     * with its own revert never sent -- observed running this project with
     * Playwright's default worker count. `fullyParallel`/`workers` are only
     * configurable per *run*, not per project, so this is enforced by
     * convention (and this comment) rather than by the config itself.
     */
    {
      name: "cfg4-e2e",
      testDir: "./e2e",
      use: {
        ...devices["Desktop Chrome"],
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
