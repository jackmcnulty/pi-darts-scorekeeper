/**
 * Mounting a screen the way the app mounts it, in front of a Pi that answers.
 *
 * Screens are rendered through `App` rather than imported directly, so a test
 * exercises the real route table and the real layout -- the same things a cold
 * reload would -- and a route wired to the wrong component fails here rather
 * than in somebody's hands.
 *
 * The query client is built per render, from the app's own `createQueryClient`,
 * so retry and staleness behave exactly as they will on the phone and no two
 * tests share a cache. Its connection monitor is a fresh one rather than the
 * module-level `connection`, which would otherwise carry a dropped connection
 * from one test into the next.
 *
 * This file is not `*.test.tsx`, so it is named explicitly in
 * `tsconfig.test.json`, in `tsconfig.app.json`'s excludes and in the coverage
 * excludes: it is test scaffolding, not app code, and counting it either way
 * would be wrong.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { ConnectionMonitor } from './api/connection'
import { createQueryClient } from './api/queryClient'
import App from './App'

/** The Pi, as far as any test is concerned. Handlers are added per test. */
export const server = setupServer()

/**
 * Wire the server's lifetime to the importing file's.
 *
 * Called explicitly rather than from `test-setup.ts`, because `pwa.test.ts`
 * runs a real Vite build in-process and has no business having its requests
 * intercepted.
 */
export function installServer(): void {
  // An unhandled request is a test that asked for something it did not mean to
  // -- a typo'd path, or a screen making a call nobody expected. Failing is the
  // only way to find out.
  beforeAll(() => {
    server.listen({ onUnhandledRequest: 'error' })
  })
  afterEach(() => {
    server.resetHandlers()
  })
  afterAll(() => {
    server.close()
  })
}

/** Open the app at `path`, exactly as the SPA fallback would on a reload. */
export function renderApp(path: string) {
  const queryClient = createQueryClient(new ConnectionMonitor())
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}
