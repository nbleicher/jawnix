import { defineConfig, devices } from "@playwright/test";

// E2E runs against the *compiled* application served by `vite preview`, so the
// journeys exercise the same hashed bundle the deployment edge serves.
const PORT = Number(process.env.JAWNIX_E2E_PORT ?? 4173);
const BASE_URL = process.env.JAWNIX_E2E_BASE_URL ?? `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  // "github" alone writes annotations and no report directory, so the CI step
  // that uploads frontend/playwright-report had nothing to upload and produced
  // no artifact — which made every CI visual-regression failure undiagnosable:
  // the actual and diff images existed only on a runner that had been torn down.
  reporter: process.env.CI
    ? [["github"], ["html", { open: "never" }]]
    : "list",
  use: {
    baseURL: `${BASE_URL}/app/`,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "mobile",
      use: { ...devices["Pixel 7"] },
    },
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    // `--host 127.0.0.1` is required: vite preview otherwise binds only to
    // `localhost`, which resolves to ::1 here, and the readiness poll below
    // uses the IPv4 address.
    command: `npm run build && npx vite preview --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `${BASE_URL}/app/`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
