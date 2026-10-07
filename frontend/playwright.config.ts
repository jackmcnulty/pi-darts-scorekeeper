/**
 * Playwright, for #32's two end-to-end specs.
 *
 * The specs drive the app the way a phone does, in WebKit, against the real
 * built image: `scripts/e2e-up.sh` starts it on a throwaway database and this
 * file only needs to know where. Nothing here starts a server -- the image
 * serves the frontend itself, so there is no dev server in an E2E run, which
 * is the point.
 *
 *     scripts/e2e-up.sh darts:latest           # from the repository root
 *     npm run e2e                              # from frontend/
 *
 * `E2E_BASE_URL` overrides the address, for a container published on another
 * port (`E2E_PORT=8100 scripts/e2e-up.sh` beside a stand-in already on 8000).
 */
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  // One database, shared by both specs, and spec 1 asserts on the leaderboard,
  // which is everybody. Serial is what makes the numbers exact.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  // Never retried, here or in CI. The server is authoritative and the client
  // never resubmits a dart, so a spec that fails and then passes on a retry is
  // a double-submit or an ordering bug being hidden, not a flake.
  retries: 0,
  // A full best-of-3 is a few hundred taps.
  timeout: 180_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['list'], ['github']] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8000',
    // The trace is the run recorded step by step: every action, a DOM snapshot
    // before and after it, the network, the console. Kept only when a spec
    // fails, and uploaded by CI's e2e job. Open one with
    // `npx playwright show-trace test-results/<spec>/trace.zip`.
    trace: 'retain-on-failure',
    // No service worker. On the phone it never registers anyway -- the Pi is
    // plain HTTP, not a secure context (#71) -- so this is the closer match to
    // the device. And while one controls the page, `page.route` cannot see the
    // page's requests, which `lanLatency` in e2e/helpers.ts depends on.
    serviceWorkers: 'block',
  },
  projects: [
    {
      name: 'iphone-17-pro',
      use: {
        // WebKit with the iPhone 17 Pro's user agent, touch, and 3x density.
        ...devices['iPhone 17 Pro'],
        // The preset's viewport is 402x681: a Safari *tab*, with its toolbars
        // taking the rest of the 874px screen. The scorekeeper is installed to
        // the home screen and runs standalone, and #24-#27 were built and
        // measured against the whole 402x874 screen, so that is the viewport.
        // This is WebKit, not iOS Safari: env(safe-area-inset-*) resolves to 0
        // here and there is no install or wake lock to speak of -- those are
        // docs/ops.md's device checklist, not this run.
        viewport: { width: 402, height: 874 },
      },
    },
  ],
})
